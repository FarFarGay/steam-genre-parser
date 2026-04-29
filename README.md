# Steam Survival RTS Genre Parser

Collects metadata for Survival/RTS/Base Building/Colony Sim/Tower Defense games on Steam (2018–2025) and estimates sales using the Boxleiter formula.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

### Test run (first 20 games)
```bash
python parse_steam_genre.py --limit 20
```

### Full run
```bash
python parse_steam_genre.py
```

### Resume after interruption
The script saves progress every 25 games to `checkpoint.json`. Just re-run the same command — it picks up where it left off.

### Skip discovery phase (use cached appids)
```bash
python parse_steam_genre.py --skip-discovery
```

## How long does it take?

- **Discovery phase**: ~5-10 minutes (scraping tag pages)
- **Metadata phase**: ~1.5s delay × 3 requests per game × number of games
  - 500 games ≈ 2-4 hours
  - With `--limit 20` ≈ 2 minutes

## Output files

| File | Description |
|------|-------------|
| `survival_rts_dataset.csv` | Main dataset, sorted by estimated sales descending |
| `survival_rts_dropped.csv` | Filtered-out games with drop reason |
| `checkpoint.json` | Progress checkpoint for resume capability |

## CSV columns

| Column | Description |
|--------|-------------|
| `appid` | Steam app ID |
| `name` | Game title |
| `developer` / `publisher` | Studio names |
| `release_date` | ISO format (YYYY-MM-DD) |
| `release_year` | Integer |
| `price_usd` | Current US price |
| `is_free` | Boolean |
| `tags` | Top 10 Steam user tags (semicolon-separated) |
| `genres` | Official Steam genres (semicolon-separated) |
| `total_reviews` | Total user reviews |
| `positive_reviews` | Positive review count |
| `positive_percentage` | 0-100 |
| `review_score_desc` | e.g. "Very Positive", "Mixed" |
| `platforms` | windows, mac, linux |
| `metacritic_score` | If available |
| `estimated_sales` | Boxleiter formula: reviews × year multiplier |
| `estimated_revenue_usd` | sales × price × 0.45 (after cuts/discounts/refunds) |

## Boxleiter multipliers

| Release year | Multiplier |
|-------------|-----------|
| ≤2017 | 70× |
| 2018-2020 | 50× |
| 2021-2023 | 35× |
| 2024+ | 30× |

## Filters applied

Games are dropped if:
- Release year outside 2018-2025
- Fewer than 50 reviews
- Free-to-play
- Price under $3
- NSFW/Sexual Content tags
- Fewer than 2 relevant genre tags
