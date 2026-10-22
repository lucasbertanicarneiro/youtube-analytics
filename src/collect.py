"""Coleta de canais e vídeos de lo-fi / ambiente via YouTube Data API v3.

Uso: python src/collect.py
Saída: data/raw/channels.csv e data/raw/videos.csv
"""

import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SEARCH_TERMS = [
    "lofi hip hop",
    "lofi beats",
    "chillhop",
    "jazz lofi",
    "ambient music",
    "study music",
    "sleep music",
    "relaxing music",
    "lofi radio",
    "chill beats",
    "deep focus music",
    "piano relaxing music",
    "meditation music",
    "rain sounds",
    "jazz cafe music",
    "chillout music",
]
# Buscas feitas para cada termo: (tipo de resultado, ordenação, nº de páginas).
# Busca de canais traz muitos canais pequenos ou extintos; buscar os vídeos mais vistos
# e usar seus canais encontra canais mais estabelecidos (com viés para o sucesso).
SEARCHES = [
    ("channel", "relevance", 2),
    ("channel", "viewCount", 2),
    ("video", "viewCount", 1),
]
BATCH_SIZE = 50
MIN_SUBSCRIBERS = 1_000
MIN_VIDEOS = 10
VIDEOS_PER_CHANNEL = 50

# Custo em unidades de cota por chamada
QUOTA_COST = {"search": 100, "channels": 1, "playlistItems": 1, "videos": 1}

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
SEARCH_LOG = RAW_DIR / "search_log.csv"
CHANNELS_CSV = RAW_DIR / "channels.csv"
VIDEOS_CSV = RAW_DIR / "videos.csv"


class QuotaTracker:
    """Conta as unidades de cota gastas ao longo da execução."""

    def __init__(self):
        self.used = 0

    def add(self, endpoint):
        self.used += QUOTA_COST[endpoint]


class QuotaExceeded(Exception):
    """Sinaliza que a cota diária acabou durante a coleta."""


def is_quota_error(error):
    # O motivo vem no corpo do erro; checamos as variações conhecidas
    reasons = {d.get("reason") for d in getattr(error, "error_details", []) or []}
    return bool(reasons & {"quotaExceeded", "dailyLimitExceeded"}) or "quota" in str(error).lower()


def execute(request, endpoint, quota):
    """Executa uma requisição, contabiliza a cota e converte erro de cota."""
    try:
        response = request.execute()
    except HttpError as error:
        if error.resp.status == 403 and is_quota_error(error):
            raise QuotaExceeded() from error
        raise
    quota.add(endpoint)
    return response


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def build_client():
    load_dotenv()
    api_key = os.getenv("YT_API_KEY")
    if not api_key:
        sys.exit("YT_API_KEY não encontrada no .env")
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def search_term(youtube, term, kind, order, pages, quota):
    """Busca um termo e devolve os IDs dos canais encontrados (dos próprios canais ou dos vídeos)."""
    channel_ids, page_token = [], None
    for _ in range(pages):
        request = youtube.search().list(
            q=term, type=kind, part="snippet", order=order,
            maxResults=BATCH_SIZE, pageToken=page_token,
        )
        response = execute(request, "search", quota)
        channel_ids += [item["snippet"]["channelId"] for item in response.get("items", [])]
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return channel_ids


def search_channel_ids(youtube, quota, log_rows):
    """Executa as buscas ainda não registradas no log; acumula resultados em `log_rows`."""
    # Linhas sem "kind" vêm de coletas anteriores, que só buscavam canais
    done = {(row.get("kind", "channel"), row["term"], row["order"]) for row in log_rows}
    for term in SEARCH_TERMS:
        for kind, order, pages in SEARCHES:
            if (kind, term, order) in done:
                continue
            ids = search_term(youtube, term, kind, order, pages, quota)
            # Só registra a busca depois de concluída, para poder retomá-la se a cota acabar
            log_rows += [{"kind": kind, "term": term, "order": order, "channel_id": cid} for cid in ids]
            print(f"  '{term}' ({kind}, {order}): {len(set(ids))} canais")
    # Linhas sem channel_id marcam buscas da 1ª coleta, feita antes de existir o log
    ids = (row["channel_id"] for row in log_rows if isinstance(row["channel_id"], str))
    # dict.fromkeys mantém a ordem e remove duplicatas
    return list(dict.fromkeys(ids))


def parse_channel(item):
    stats = item.get("statistics", {})
    snippet = item.get("snippet", {})
    return {
        "channel_id": item["id"],
        "title": snippet.get("title"),
        "subscribers": int(stats["subscriberCount"]) if "subscriberCount" in stats else None,
        "total_views": int(stats.get("viewCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
        "published_at": snippet.get("publishedAt"),
        "country": snippet.get("country"),
        "uploads_playlist": item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads"),
    }


def fetch_channels(youtube, channel_ids, quota, rows):
    """Busca detalhes dos canais em lotes; acumula em `rows` para não perder dados."""
    for batch in chunks(channel_ids, BATCH_SIZE):
        request = youtube.channels().list(
            id=",".join(batch), part="snippet,statistics,contentDetails", maxResults=BATCH_SIZE,
        )
        response = execute(request, "channels", quota)
        rows += [parse_channel(item) for item in response.get("items", [])]


def filter_channels(channels):
    mask = (channels["subscribers"].fillna(0) >= MIN_SUBSCRIBERS) & (channels["video_count"] >= MIN_VIDEOS)
    return channels[mask].reset_index(drop=True)


def fetch_recent_video_ids(youtube, playlist_id, quota):
    """Pega os IDs dos vídeos mais recentes da playlist de uploads."""
    request = youtube.playlistItems().list(
        playlistId=playlist_id, part="contentDetails", maxResults=VIDEOS_PER_CHANNEL,
    )
    try:
        response = execute(request, "playlistItems", quota)
    except HttpError as error:
        # Playlist de uploads pode estar indisponível (404); seguimos para o próximo canal
        if error.resp.status == 404:
            return []
        raise
    return [item["contentDetails"]["videoId"] for item in response.get("items", [])]


def parse_video(item):
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    return {
        "video_id": item["id"],
        "channel_id": snippet.get("channelId"),
        "title": snippet.get("title"),
        "published_at": snippet.get("publishedAt"),
        "duration": item.get("contentDetails", {}).get("duration"),
        "views": int(stats["viewCount"]) if "viewCount" in stats else None,
        "likes": int(stats["likeCount"]) if "likeCount" in stats else None,
        "comments": int(stats["commentCount"]) if "commentCount" in stats else None,
        "tags": "|".join(snippet.get("tags", [])),
        # liveStreamingDetails só existe para vídeos que são ou foram live
        "is_live": "liveStreamingDetails" in item,
    }


def fetch_videos(youtube, video_ids, quota, rows):
    for batch in chunks(video_ids, BATCH_SIZE):
        request = youtube.videos().list(
            id=",".join(batch),
            part="snippet,contentDetails,statistics,liveStreamingDetails",
            maxResults=BATCH_SIZE,
        )
        response = execute(request, "videos", quota)
        rows += [parse_video(item) for item in response.get("items", [])]


def collect_videos(youtube, channels, quota, rows):
    """Para cada canal ainda sem vídeos coletados, busca os vídeos recentes e seus detalhes."""
    collected = {row["channel_id"] for row in rows}
    pending = channels[~channels["channel_id"].isin(collected)]["uploads_playlist"].dropna()
    print(f"  {len(pending)} canais novos para coletar")
    for i, playlist_id in enumerate(pending, start=1):
        video_ids = fetch_recent_video_ids(youtube, playlist_id, quota)
        fetch_videos(youtube, video_ids, quota, rows)
        if i % 25 == 0:
            print(f"  {i}/{len(pending)} canais processados, {len(rows)} vídeos")


def load_rows(path):
    """Carrega um CSV já existente como lista de dicts (vazia se não existir)."""
    if not path.exists():
        return []
    return pd.read_csv(path).to_dict("records")


def save(log_rows, channel_rows, video_rows):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(log_rows).to_csv(SEARCH_LOG, index=False)
    channels = pd.DataFrame(channel_rows)
    if not channels.empty:
        # keep="last" mantém as estatísticas mais recentes de cada canal
        channels = filter_channels(channels.drop_duplicates("channel_id", keep="last"))
    channels.to_csv(CHANNELS_CSV, index=False)
    videos = pd.DataFrame(video_rows).drop_duplicates("video_id", keep="last")
    videos.to_csv(VIDEOS_CSV, index=False)
    print(f"Salvo: {len(channels)} canais e {len(videos)} vídeos em {RAW_DIR}")


def main():
    youtube = build_client()
    quota = QuotaTracker()
    # Reaproveita coletas anteriores para não gastar cota de novo
    log_rows = load_rows(SEARCH_LOG)
    channel_rows = load_rows(CHANNELS_CSV)
    video_rows = load_rows(VIDEOS_CSV)
    try:
        print("1/3 Buscando canais...")
        channel_ids = search_channel_ids(youtube, quota, log_rows)
        print(f"2/3 Detalhando {len(channel_ids)} canais...")
        fetch_channels(youtube, channel_ids, quota, channel_rows)
        channels = filter_channels(pd.DataFrame(channel_rows).drop_duplicates("channel_id", keep="last"))
        print(f"  {len(channels)} canais passaram no filtro")
        print("3/3 Coletando vídeos...")
        collect_videos(youtube, channels, quota, video_rows)
    except QuotaExceeded:
        print("Cota diária esgotada: salvando o que foi coletado até aqui.")
    finally:
        save(log_rows, channel_rows, video_rows)
        print(f"Unidades de cota gastas (estimativa): {quota.used}")


if __name__ == "__main__":
    main()
