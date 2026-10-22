"""Limpeza e engenharia de features dos dados coletados.

Uso: python src/clean.py
Entrada: data/raw/channels.csv, data/raw/videos.csv
Saída: data/processed/videos.csv, data/processed/channels.csv
"""

import re
from pathlib import Path

import isodate
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"

SUCCESS_QUANTILE = 0.75

# Filtro de nicho: a busca por vídeos traz canais de fora do nicho (infantil, culinária...).
# Fica o canal cujo nome tem termo do nicho ou com >= NICHE_MIN_SHARE dos vídeos com termo
# do nicho no título ou nas tags. Palavras genéricas como "music" ficam de fora de propósito.
NICHE_PATTERN = (
    r"lo-?fi|chill|relax|sleep|study|ambient|jazz|meditat|piano|rain|focus|beats|calm|lullab"
    r"|healing|spa\b|asmr|nature sounds|white noise|cafe|coffee|peaceful|soothing|zen\b|yoga|new age"
)
NICHE_MIN_SHARE = 0.5

# Faixas Unicode mais comuns de emojis e símbolos pictográficos
EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002B00-\U00002BFF]"
)
YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")

# Padrões textuais buscados no título (case-insensitive)
TITLE_FLAGS = {
    "has_pipe": r"\|",
    "has_24_7": r"24\s*/\s*7",
    "has_live": r"\blive\b",
    "has_beats_to": r"beats to",
}


def parse_duration_minutes(value):
    """Converte duração ISO 8601 (ex.: PT1H2M3S) em minutos; None se inválida."""
    if not isinstance(value, str):
        return None
    try:
        return isodate.parse_duration(value).total_seconds() / 60
    except (isodate.ISO8601Error, ValueError):
        return None


def add_video_features(videos):
    videos = videos.copy()
    videos["published_at"] = pd.to_datetime(videos["published_at"], utc=True)
    videos["duration_min"] = videos["duration"].map(parse_duration_minutes)
    videos["weekday"] = videos["published_at"].dt.day_name()
    videos["hour_utc"] = videos["published_at"].dt.hour

    titles = videos["title"].fillna("")
    videos["title_length"] = titles.str.len()
    videos["emoji_count"] = titles.str.count(EMOJI_PATTERN)
    for column, pattern in TITLE_FLAGS.items():
        videos[column] = titles.str.contains(pattern, case=False, regex=True)
    videos["has_year"] = titles.str.contains(YEAR_PATTERN, regex=True)
    niche_text = titles + " " + videos["tags"].fillna("")
    videos["is_niche"] = niche_text.str.contains(NICHE_PATTERN, case=False, regex=True)

    # Engajamento só é definido quando há views > 0 e likes visíveis.
    # Likes ocultos são desconhecidos (NaN); comentários desativados contam como 0.
    interactions = videos["likes"] + videos["comments"].fillna(0)
    views = videos["views"].where(videos["views"] > 0)
    videos["engagement"] = interactions / views
    return videos


def upload_frequency_per_week(published_at):
    """Vídeos por semana no intervalo coberto pelos vídeos coletados."""
    if len(published_at) < 2:
        return None
    span_weeks = (published_at.max() - published_at.min()).total_seconds() / (7 * 24 * 3600)
    return len(published_at) / span_weeks if span_weeks > 0 else None


def aggregate_channel_features(videos):
    grouped = videos.groupby("channel_id")
    return pd.DataFrame({
        "mean_views": grouped["views"].mean(),
        "median_views": grouped["views"].median(),
        "uploads_per_week": grouped["published_at"].apply(upload_frequency_per_week),
        "live_pct": grouped["is_live"].mean() * 100,
        "mean_duration_min": grouped["duration_min"].mean(),
        "mean_engagement": grouped["engagement"].mean(),
        "videos_collected": grouped.size(),
        "niche_share": grouped["is_niche"].mean(),
    }).reset_index()


def filter_niche(channels):
    name_match = channels["title"].str.contains(NICHE_PATTERN, case=False, regex=True, na=False)
    keep = name_match | (channels["niche_share"] >= NICHE_MIN_SHARE)
    print(f"Filtro de nicho: {keep.sum()} de {len(channels)} canais mantidos")
    return channels[keep].reset_index(drop=True)


def add_channel_features(channels, videos):
    channels = channels.copy()
    created = pd.to_datetime(channels["published_at"], utc=True, format="ISO8601")
    now = pd.Timestamp.now(tz="UTC")
    channels["age_years"] = (now - created).dt.days / 365.25

    channels = channels.merge(aggregate_channel_features(videos), on="channel_id", how="inner")
    channels = filter_niche(channels)
    # O limiar de sucesso é calculado só depois do filtro, sobre canais do nicho
    threshold = channels["median_views"].quantile(SUCCESS_QUANTILE)
    channels["success"] = channels["median_views"] >= threshold
    return channels


def main():
    channels = pd.read_csv(RAW_DIR / "channels.csv")
    videos = pd.read_csv(RAW_DIR / "videos.csv")
    videos["published_at"] = pd.to_datetime(videos["published_at"], utc=True, format="ISO8601")

    # Remove duplicatas e vídeos sem contagem de views
    videos = videos.drop_duplicates("video_id").dropna(subset=["views"])
    videos = add_video_features(videos)
    channels = add_channel_features(channels.drop_duplicates("channel_id"), videos)

    # Propaga o rótulo de sucesso para os vídeos, facilitando comparações no notebook
    videos = videos.merge(channels[["channel_id", "success"]], on="channel_id", how="inner")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    videos.to_csv(PROCESSED_DIR / "videos.csv", index=False)
    channels.to_csv(PROCESSED_DIR / "channels.csv", index=False)
    print(f"{len(channels)} canais ({channels['success'].sum()} bem-sucedidos), {len(videos)} vídeos")


if __name__ == "__main__":
    main()
