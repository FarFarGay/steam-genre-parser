# Steam Action Base-Building Genre Parser — Specification

## 1. Purpose

Automated collection of a structured dataset of Steam games in the Action / Base Building / Physics / Destruction / Tower Defense genre cluster, released 2018–2026, with metadata sufficient to estimate sales volume and build a market landscape analysis.

The cluster definition pivoted on 2026-07-16: the game is defined through action (physical hand, destruction, melee) rather than RTS/strategy. RTS, Strategy, Real-Time with Pause and Action RTS tags were removed (Strategy sits on half of Steam — more noise than signal); Action, Physics, Destruction, Hack and Slash, Action RPG, Dungeon Crawler, Isometric, Top-Down and Mechs were added.

The output is a clean CSV ready for Excel analysis, pitch deck slides, and genre research.

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    parse_steam_genre.py              │
│                                                     │
│  Phase 1: Discovery ──► Phase 2: Fetch ──► Phase 3  │
│  (tag pair search)      (metadata)         (export) │
│         │                   │                  │    │
│         ▼                   ▼                  ▼    │
│   Steam Search API    Steam Store API      CSV/JSON │
│                       Steam Reviews API             │
│                       Steam Store Pages             │
│                                                     │
│              checkpoint.json (resume)                │
└─────────────────────────────────────────────────────┘
```

## 3. Phase 1 — Discovery

### Strategy

Games are discovered via Steam Search API using **tag pair combinations** (intersections, not unions). This ensures every discovered game has at least 2 relevant genre tags, dramatically reducing noise.

### Target Tags

IDs verified against `store.steampowered.com/tagdata/populartags/english` (2026-07-16). Two IDs from v1 were broken: "Base Building" 4748 returned 0 search results (correct: 7332), "Colony Sim" 4094 pointed at an unrelated tag (correct: 220585).

| Tag | Steam ID | Role |
|-----|----------|------|
| Action | 19 | core — new game definition |
| Base Building | 7332 | core |
| Physics | 3968 | the hand |
| Destruction | 5363 | destructibles |
| Tower Defense | 1645 | kept from v1 |
| Survival | 1662 | kept from v1 |
| Colony Sim | 220585 | kept, half weight (seven dwarves ≠ colony) |
| Hack and Slash | 1646 | melee |
| Action RPG | 4231 | if progression survives playtests |
| Dungeon Crawler | 1720 | dwarves go underground |
| Isometric | 5851 | camera — catches Riftbreaker, Cult of the Lamb |
| Top-Down | 4791 | adjacent camera, half weight |
| Mechs | 4821 | piloted machine (Riftbreaker) |

### Tag Pair Combinations (5 total)

```
Base Building + Action          (primary)
Base Building + Physics
Tower Defense + Action
Base Building + Destruction
Survival + Base Building        (catches the old v1 shelf)
```

### API Endpoint

```
GET https://store.steampowered.com/search/results/
    ?tags={tag_id_1},{tag_id_2}
    &category1=998          # Games only (not DLC/software)
    &supportedlang=english
    &count=50               # Per page
    &start={offset}         # Pagination
    &infinite=1             # JSON response
```

### Pagination

- 50 results per page
- Iterate until `loaded >= total_count` or no results returned
- 1.0s delay between pages
- Exponential backoff on HTTP 429/5xx (max 3 retries)

### Pre-filter

After discovery, appids < 700,000 are discarded. These correspond to games released before ~2017 and would all be filtered out later anyway. This saves significant API calls.

### Output

A deduplicated set of appids stored in `checkpoint.json`.

## 4. Phase 2 — Metadata Fetch

### Per-Game API Calls (3 requests per game)

#### 1. App Details
```
GET https://store.steampowered.com/api/appdetails
    ?appids={appid}&cc=us&l=en
```
Returns: name, developer, publisher, release date, price, genres, platforms, metacritic score, header image, is_free flag, app type.

#### 2. Review Summary
```
GET https://store.steampowered.com/appreviews/{appid}
    ?json=1&num_per_page=0&language=all&purchase_type=all
```
Returns: total_reviews, total_positive, review_score_desc.

#### 3. User Tags (HTML scrape)
```
GET https://store.steampowered.com/app/{appid}
```
Tags are extracted from the `InitAppTagModal(appid, [...])` JavaScript call embedded in the page HTML (Steam renamed it from `InitAppTagData` at some point in 2026, which silently broke tag parsing until 2026-07-16). Top 20 tags by weight are captured.

The session carries age-gate cookies (`birthtime`, `lastagecheckage`, `wants_mature_content`) — without them, mature-rated games redirect to `/agecheck/` and return no tags.

**Note:** Tag parsing may fail due to Steam rate limiting or page structure changes. When tags are empty, the tag-weight filter is skipped (the game already passed tag pair discovery).

### Rate Limiting

- 1.5s delay between each API request
- Exponential backoff on HTTP 429, 500, 502, 503
- Max 3 retries per request
- Custom User-Agent: `SurvivalRTSResearch/1.0 (research bot)`

### Early Rejection

Games with `type != "game"` (DLC, software, video, etc.) are dropped immediately without making review/tag requests, saving 2 API calls per rejected item.

### Checkpoint System

Every 25 processed games, the full state is written to `checkpoint.json`:

```json
{
  "config_hash": "md5 of tags/combos/weights/years",
  "appids": [700100, 700200, ...],
  "fetched": {
    "700100": { full game record },
    ...
  },
  "dropped": {
    "700200": { record + "drop_reason" },
    ...
  }
}
```

On restart with `--skip-discovery`, already-processed appids are skipped.

`config_hash` fingerprints everything that affects dataset membership (tags, combos, weights, year range). A checkpoint built under a different config is discarded on load — otherwise old and new filter rules would silently mix. Transient fetch failures are NOT checkpointed, so the next run retries them. `KeyboardInterrupt` saves the checkpoint and still writes the partial CSVs.

## 5. Data Schema

### Per-Game Record (19 fields)

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `appid` | int | Discovery | Steam application ID |
| `name` | string | App Details | Game title |
| `developer` | string | App Details | Developer name(s), semicolon-separated |
| `publisher` | string | App Details | Publisher name(s), semicolon-separated |
| `release_date` | string | App Details | ISO format YYYY-MM-DD |
| `release_year` | int | Derived | Extracted from release_date |
| `price_usd` | float | App Details | Base US price before discounts (`price_overview.initial`; 0 for free, null if unavailable) |
| `is_free` | bool | App Details | Free-to-play flag |
| `is_early_access` | bool | Derived | "Early Access" present in Steam genres |
| `tags` | string | Store Page | Top 20 user tags, semicolon-separated |
| `genres` | string | App Details | Official Steam genres, semicolon-separated |
| `total_reviews` | int | Reviews API | Total user review count |
| `positive_reviews` | int | Reviews API | Positive review count |
| `positive_percentage` | float | Calculated | 0–100, rounded to 1 decimal |
| `review_score_desc` | string | Reviews API | e.g. "Very Positive", "Mixed" |
| `platforms` | string | App Details | Comma-separated: windows, mac, linux |
| `metacritic_score` | int/null | App Details | Metacritic score if available |
| `header_image_url` | string | App Details | URL to store header image |
| `steam_url` | string | Derived | Store page link for click-through |
| `estimated_sales` | int | Calculated | Boxleiter formula |
| `estimated_revenue_usd` | float | Calculated | Revenue estimate |

### Release Date Parsing

Steam uses inconsistent date formats. The parser attempts these in order:
1. `"Dec 11, 2018"` → `%b %d, %Y`
2. `"11 Dec, 2018"` → `%d %b, %Y`
3. `"December 11, 2018"` → `%B %d, %Y`
4. `"11 December, 2018"` → `%d %B, %Y`
5. `"2018"` → `%Y`
6. Regex fallback: extract 4-digit year matching `20\d{2}`

## 6. Sales Estimation — Boxleiter Formula

### Review-to-Sales Multiplier

Based on Jake Birkett / GameDiscoverCo published estimates. The ratio decreases over time as fewer players leave reviews.

| Release Year | Multiplier |
|-------------|-----------|
| ≤ 2017 | 70x |
| 2018–2020 | 50x |
| 2021–2023 | 35x |
| 2024+ | 30x |

```
estimated_sales = total_reviews × multiplier(release_year)
```

### Revenue Estimation

```
estimated_revenue = estimated_sales × price_usd × 0.45
```

The 0.45 coefficient accounts for:
- Steam platform cut: ~30%
- Regional pricing discounts: ~15%
- Lifetime sale discounts: ~25%
- Refunds: ~5%

Effective revenue ≈ 45% of gross (sales × full price).

## 7. Filtering Rules

Games are dropped (with reason logged) if any condition is true:

| # | Filter | Threshold | Rationale |
|---|--------|-----------|-----------|
| 1 | Release year | < 2018 or > 2026 | Scope limitation |
| 2 | Review count | < 50 | Insufficient data for estimation |
| 3 | Free-to-play | is_free = True | Premium segment only |
| 4 | Price | < $3.00 | Removes asset flips and shovelware |
| 5 | NSFW tags | "Sexual Content", "NSFW", "Hentai", "Adult Only" | Out of scope |
| 6 | Relevant tag weight | < 2.0 (only when tags parsed successfully) | Must belong to genre cluster |

Filters are applied in this order. First matching rule determines the drop reason.

### Relevant Tag Weights (for filter #6)

All target tags weigh 1.0, except partial signals at 0.5: **Colony Sim** (seven dwarves are not a colony) and **Top-Down** (adjacent camera, partial credit). A game must accumulate total weight ≥ 2.0 across its top-10 tags.

## 8. Output Files

### survival_rts_dataset.csv

Main dataset. Sorted by `estimated_sales` descending. UTF-8 with BOM for Excel compatibility.

### survival_rts_dropped.csv

All filtered-out games with an additional `drop_reason` column. Same schema plus the reason field.

### checkpoint.json

Internal state for resume capability. Not intended for analysis.

## 9. CLI Interface

```
python parse_steam_genre.py [OPTIONS]

Options:
  --limit N          Process only first N appids (for testing)
  --skip-discovery   Skip Phase 1, load appids from checkpoint
```

### Typical Usage

```bash
# Test run
python parse_steam_genre.py --limit 20

# Full run
python parse_steam_genre.py

# Resume after interruption
python parse_steam_genre.py --skip-discovery
```

## 10. Summary Report

After export, the script prints:

- Total collected vs. dropped
- Top 5 drop reasons
- Distribution by release year
- Sales tier breakdown:
  - **Hits**: 1M+ estimated sales
  - **Successes**: 200K–1M
  - **Mid**: 50K–200K
  - **Below break-even**: < 50K
- Top 10 games by estimated revenue

## 11. Performance Characteristics

| Metric | Value |
|--------|-------|
| Discovery phase | ~5–10 minutes |
| Fetch speed | ~5.5–6.0 seconds per game |
| Requests per game | 3 (details + reviews + tags) |
| Delay between requests | 1.5 seconds |
| Checkpoint interval | Every 25 games |
| Typical total appids | ~4,000–5,000 |
| Typical games after filter | ~700–900 |
| Full run time | ~6–8 hours |

## 12. Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| requests | >= 2.31.0 | HTTP client |
| pandas | >= 2.0.0 | DataFrame operations, CSV export |
| tqdm | >= 4.65.0 | Progress bar |

Python 3.10+ required (uses `X | Y` union type syntax).

## 13. Known Limitations

- **Tag parsing unreliable**: Steam may block or change the page structure for `InitAppTagModal` (it already renamed the call once). When tags fail to parse, the tag-weight filter is bypassed (safe because discovery already ensures the game matched a tag pair).
- **Price is current base price, not launch price**: discounts are excluded (`initial`), but permanent price changes since launch are not.
- **Boxleiter is an approximation**: Actual sales can vary 2–3x from the estimate. The formula is best used for relative comparison within the dataset, not absolute numbers.
- **No wishlist data**: Wishlists are not publicly available via Steam API.
- **No concurrent player history**: Would require a separate Steam Charts scrape.
- **Rate limiting variability**: Steam's rate limits are not formally documented and can vary. The script may slow down during high-traffic periods.

## 14. Out of Scope (v1)

- Wishlist counts
- Concurrent player history
- Localization analysis
- Update / DLC frequency
- Trailer / video metadata
- SteamSpy cross-validation
