# Steam Action Base-Building Genre Parser

Collects metadata for Action/Base Building/Physics/Destruction/Tower Defense games on Steam (2018–2026) and estimates sales using the Boxleiter formula.

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
The script saves progress every 25 games to `checkpoint.json`. Just re-run the same command — it picks up where it left off. `Ctrl+C` is safe: the partial dataset is still written to CSV.

Note: the checkpoint stores a fingerprint of the tag/filter config. If you change tags or filters, the old checkpoint is discarded automatically and the run starts fresh.

### Skip discovery phase (use cached appids)
```bash
python parse_steam_genre.py --skip-discovery
```

## How long does it take?

- **Discovery phase**: ~10-15 minutes (8 tag-pair combos)
- **Metadata phase**: 2-4 requests per game with 1.5s delays (store HTML is only fetched for games that pass the cheap filters)
  - Full v3 run (~7-8K apps) ≈ 9-12 hours; add ~1.5-2h with `--with-histograms`
  - With `--limit 20` ≈ 2 minutes

## Output files

| File | Description |
|------|-------------|
| `survival_rts_dataset.csv` | Main dataset, sorted by estimated sales descending |
| `survival_rts_dropped.csv` | Filtered-out games with drop reason |
| `unborn.csv` | Unreleased games (coming soon) — the future-competitor pipeline |
| `histograms/{appid}.json` | Monthly review histograms (only with `--with-histograms`) |
| `checkpoint.json` | Progress checkpoint for resume capability |

## CSV columns

| Column | Description |
|--------|-------------|
| `appid` | Steam app ID |
| `name` | Game title |
| `developer` / `publisher` | Studio names |
| `release_date` | ISO format (YYYY-MM-DD) |
| `release_year` | Integer |
| `price_usd` | Base US price (before discounts) |
| `is_free` | Boolean |
| `is_early_access` | Boolean (from Steam genres) |
| `tags` | Top 20 Steam user tags (semicolon-separated) |
| `genres` | Official Steam genres (semicolon-separated) |
| `total_reviews` | Total user reviews |
| `positive_reviews` | Positive review count |
| `positive_percentage` | 0-100 |
| `review_score_desc` | e.g. "Very Positive", "Mixed" |
| `platforms` | windows, mac, linux |
| `metacritic_score` | If available |
| `steam_url` | Store page link |
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
- Release year outside 2018-2026
- Free-to-play
- Price under $3
- NSFW/Sexual Content tags
- Relevant tag weight below 2.0 (most cluster tags weigh 1.0; Colony Sim and Top-Down weigh 0.5)

Games with fewer than 50 reviews are NOT dropped — they stay in the dataset flagged `is_low_data=True`, so the graveyard is analyzable (no survivorship bias). Unreleased games go to `unborn.csv` instead of dropped.
