# Projeto: Análise de Canais Lo-fi / Ambiente no YouTube

## Objetivo
Coletar dados de canais de música lo-fi, ambiente, study e sleep via YouTube Data API v3 e identificar, com pandas, os padrões que mais se repetem entre os canais bem-sucedidos. Projeto de portfólio: código limpo, reprodutível e com conclusões claras no README.

## Stack (fixa, não trocar)
- Python 3, venv em `.venv`
- google-api-python-client, pandas, matplotlib, seaborn, python-dotenv, isodate, jupyter
- Chave da API em `.env` como `YT_API_KEY`. Nunca commitar `.env`.

## Estrutura
```
data/raw/            # saída bruta da API (CSV)
data/processed/      # dataset tratado com features
src/collect.py       # coleta via API
src/clean.py         # limpeza + engenharia de features
notebooks/analysis.ipynb
figures/             # PNGs usados no README
requirements.txt
README.md
```

## Coleta (src/collect.py)
1. `search.list` (type=channel, maxResults=50) com os termos: "lofi hip hop", "lofi beats", "chillhop", "jazz lofi", "ambient music", "study music", "sleep music", "relaxing music", "lofi radio", "chill beats", "deep focus music", "piano relaxing music", "meditation music", "rain sounds", "jazz cafe music", "chillout music". Cada termo com busca de canais (`order=relevance` e `order=viewCount`, até 2 páginas cada) e busca de vídeos (`type=video`, `order=viewCount`, 1 página), usando os canais dos vídeos encontrados. Deduplicar IDs.
   - Coleta incremental: buscas feitas ficam em `data/raw/search_log.csv` e não são repetidas; canais que já têm vídeos em `videos.csv` são pulados.
2. `channels.list` (part=snippet,statistics,contentDetails) em lotes de 50: id, título, inscritos, views totais, nº vídeos, data de criação, país, playlist de uploads.
3. Filtrar canais com >= 1.000 inscritos e >= 10 vídeos.
4. Para cada canal: `playlistItems.list` da playlist de uploads, até 50 vídeos mais recentes.
5. `videos.list` (part=snippet,contentDetails,statistics,liveStreamingDetails) em lotes de 50: id, canal, título, data/hora de publicação, duração (ISO 8601), views, likes, comentários, tags, se é/foi live.
6. Salvar `data/raw/channels.csv` e `data/raw/videos.csv`.
- Cota diária: 10.000 unidades. `search.list` = 100 unidades; o resto = 1. Imprimir estimativa de unidades gastas ao final.
- Tratar `HttpError` (quotaExceeded) salvando o que já foi coletado.

## Features (src/clean.py)
Por vídeo: duração em minutos, dia da semana, hora (UTC), tamanho do título, nº de emojis no título, presença de "|", "24/7", "live", "beats to", ano no título, engajamento = (likes+comentários)/views (NaN quando os likes estão ocultos; comentários desativados contam como 0).
Por canal: views médias por vídeo, mediana de views, frequência de upload (vídeos/semana nos últimos 50), % de lives, duração média, idade do canal em anos, engajamento médio.

## Filtro de nicho (src/clean.py)
A busca por vídeos traz canais de fora do nicho (infantil, culinária etc.). Fica o canal cujo nome contém termo do nicho ou que tem >= 50% dos vídeos com termo do nicho no título/tags (`NICHE_PATTERN`). Aplicado antes de calcular o limiar de sucesso. Limitação a citar no README: a busca por vídeos populares enviesa a amostra para canais de sucesso.

## Definição de sucesso
Canal bem-sucedido = top 25% em mediana de views por vídeo. Coluna `success` (bool).

## Análise (notebook)
Comparar `success=True` vs `False` em cada feature: boxplots, barras e heatmap de correlação. Top 20 palavras nos títulos de cada grupo. Fechar com 5 a 7 padrões objetivos, cada um com número que o sustente. Salvar gráficos em `figures/`.

## Regras
- Código em português nos comentários, nomes de variáveis em inglês.
- Funções pequenas, sem notebooks para coleta.
- Não adicionar bibliotecas fora da stack sem pedir.
