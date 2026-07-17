# Steam Action Base-Building Genre Parser — Specification

## 0. Version History

- **v1** (2026-07): Survival RTS cluster. Shipped with two broken tag ids and a tag scraper that Steam's `InitAppTagData` → `InitAppTagModal` rename had silently killed.
- **v2** (2026-07-16): pivot to Action Base-Building — RTS/Strategy tags out, Action/Physics/Destruction in; ids fixed, scraper revived, age-gate cookies, base price, checkpoint config-hash. Full run completed 2026-07-16: 739 games / 3,989 dropped of 4,728 candidates.
- **v3** (2026-07-17): "hand" blind-spot combos (Villain Protagonist, Physics+Destruction, Mechs), survivorship bias removed (`is_low_data` flag instead of review-count drop), `unborn.csv`, 12 new columns, request funnel + per-endpoint delays, atomic checkpoint, Excel-injection sanitization. Not yet run.

## 1. Purpose

Automated collection of a structured dataset of Steam games in the Action / Base Building / Physics / Destruction / Tower Defense genre cluster, released 2018–2026, with metadata sufficient to estimate sales volume and build a market landscape analysis.

The cluster definition pivoted on 2026-07-16: the game is defined through action (physical hand, destruction, melee) rather than RTS/strategy. RTS, Strategy, Real-Time with Pause and Action RTS tags were removed (Strategy sits on half of Steam — more noise than signal); Action, Physics, Destruction, Hack and Slash, Action RPG, Dungeon Crawler, Isometric, Top-Down and Mechs were added. v3 (2026-07-17) extended discovery toward the "hand without a base" power fantasy (Villain Protagonist, Physics + Destruction, Mechs + Action).

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
| Villain Protagonist | 11333 | v3: Overlord-style power fantasy |

All ids are re-verified at startup against `tagdata/populartags/english` (hard exit on mismatch); ids missing from that top-430 list are probed via live search instead.

### Tag Pair Combinations (8 total)

```
Base Building + Action          (primary)
Base Building + Physics
Tower Defense + Action
Base Building + Destruction
Survival + Base Building        (catches the old v1 shelf)
Villain Protagonist + Action    (v3: the "hand" blind spot)
Physics + Destruction           (v3: hand without a base)
Mechs + Action                  (v3: piloted machine)
```

The first five are the stable v2 "shelf" — kept unchanged between runs for comparability. Hack and Slash + Isometric was evaluated (689 games) and rejected: it imports the whole Diablo ARPG shelf. Each appid records which combos found it (`discovered_via` column) so "shelf" and "hand" remain separable research questions.

### API Endpoint

```
GET https://store.steampowered.com/search/results/
    ?tags={tag_id_1},{tag_id_2}
    &category1=998          # Games only (not DLC/software)
    &count=50               # Per page
    &start={offset}         # Pagination
    &infinite=1             # JSON response
```

No `supportedlang` filter (removed in v3): English localization proxies for budget/ambition, and CJK/RU-only failures are part of the cluster graveyard being studied.

### Pagination

- 50 results per page
- Iterate until `loaded >= total_count` or no results returned
- 1.0s delay between pages
- Exponential backoff on HTTP 429/5xx (max 6 retries, up to 64s)
- A combo that collects < 95% of its `total_count` logs an explicit coverage warning

### Pre-filter

After discovery, appids < 400,000 are discarded (registered before ~2016). appid correlates with registration date, not release: the old 700,000 cutoff silently lost games registered 2016–2017 but released 2018+ (long development = ambitious projects). The year filter drops actual pre-2018 releases downstream.

### Output

A deduplicated map of `appid → [combos that found it]` stored in `checkpoint.json` (`appid_combos`), exported as the `discovered_via` CSV column.

## 4. Phase 2 — Metadata Fetch

### Per-Game API Calls (1-4 requests per game, cheapest first)

Request order is a funnel: each stage is only paid for games the previous
stage kept alive. Games dropped by the year/F2P/price filters cost exactly
one request; their review columns stay empty in dropped.csv.

#### 1. App Details — always
```
GET https://store.steampowered.com/api/appdetails
    ?appids={appid}&cc=us&l=en
```
Returns: name, developer, publisher, release date, price, genres, platforms, metacritic score, header image, is_free flag, app type. Everything the cheap filters (year, F2P, price) and the unborn check need.

#### 2. Review Summary — only for games that pass the cheap filters
```
GET https://store.steampowered.com/appreviews/{appid}
    ?json=1&num_per_page=0&language=all&purchase_type=all
```
Returns: total_reviews, total_positive, review_score_desc. (Also fetched for the rare no-date-no-coming_soon case, where the review count decides between unborn and dropped.)

#### 3. User Tags (HTML scrape) — fetched LAST, and only for survivors

The store page is the heaviest request, so cheap filters (year, F2P, price) run first and the HTML is fetched only for games still alive — saves roughly a third of fetch-phase time. Tag-dependent filters (NSFW, tag weight) run after.

```
GET https://store.steampowered.com/app/{appid}
```
Tags are extracted from the `InitAppTagModal(appid, [...])` JavaScript call embedded in the page HTML (Steam renamed it from `InitAppTagData` at some point in 2026, which silently broke tag parsing until 2026-07-16). Top 20 tags are captured **with vote counts** (`tag_weights` column, `name:count; ...`) — signal intensity, not just presence.

#### 4. Review histograms (optional, `--with-histograms`)
```
GET https://store.steampowered.com/appreviewhistogram/{appid}?l=english
```
For games with ≥1 review, raw monthly-review JSON is stored to `histograms/{appid}.json` (idempotent — existing files are skipped, so it can run as a second pass). Enables death diagnosis (dead-on-arrival vs died-after-launch) and correcting the 2026 right-censoring. Adds ~1.5–2h.

The session carries age-gate cookies (`birthtime`, `lastagecheckage`, `wants_mature_content`) — without them, mature-rated games redirect to `/agecheck/` and return no tags.

**Note:** Tag parsing may fail due to Steam rate limiting or page structure changes. When tags are empty, the tag-weight filter is skipped (the game already passed tag pair discovery).

### Rate Limiting

- Per-endpoint delays: 1.5s appdetails (strictest, ~200 req/5min), 0.75s reviews/histograms, 1.0s store pages
- Exponential backoff on HTTP 429, 500, 502, 503 (up to 64s)
- Max 6 retries per request
- Custom User-Agent: `SurvivalRTSResearch/1.0 (research bot)`

### Early Rejection

Games with `type != "game"` (DLC, software, video, etc.) are dropped immediately without making review/tag requests, saving 2 API calls per rejected item.

### Checkpoint System

Every 25 processed games, the full state is written to `checkpoint.json`:

```json
{
  "config_hash": "md5 of tags/combos/weights/years/schema",
  "appid_combos": {
    "700100": ["Base Building + Action", "Survival + Base Building"],
    ...
  },
  "fetched": {
    "700100": { full game record },
    ...
  },
  "dropped": {
    "700200": { record + "drop_reason" },
    ...
  },
  "unborn": {
    "700300": { record with coming_soon=true },
    ...
  }
}
```

On restart with `--skip-discovery`, already-processed appids are skipped.

`config_hash` fingerprints everything that affects dataset membership (tags, combos, weights, year range, MIN_APPID) plus `SCHEMA_VERSION` (bumped when the column set changes) — a checkpoint built under a different config or schema is discarded on load, otherwise old and new rules/columns would silently mix.

Robustness guarantees:
- **Atomic writes**: the checkpoint is written to a temp file and `os.replace`d — a kill mid-write can never corrupt it (readers also never see a half-written file).
- **Corruption recovery**: an unreadable checkpoint logs a warning and starts fresh instead of crashing.
- **Transient failures are NOT checkpointed** — a failed appdetails *or* reviews request skips the game for this run and retries it next run. A failed reviews request is never recorded as "0 reviews" (that would poison sales estimates permanently).
- `KeyboardInterrupt` saves the checkpoint and still writes the partial CSVs.

## 5. Data Schema

### Per-Game Record

v3 additions on top of the v2 schema (all from responses already being fetched — zero new requests): `coming_soon`, `price_final_usd`, `discount_percent`, `has_demo`, `discovered_via`, `tag_weights`, `categories`, `n_languages`, `n_achievements`, `n_dlc`, `recommendations`, `is_low_data`.

### Core fields (v2)

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
| 0 | Unreleased | coming_soon, or no date + 0 reviews | Routed to `unborn.csv` (future-competitor pipeline), not dropped |
| 1 | Release year | < 2018 or > 2026 | Scope limitation |
| 2 | Free-to-play | is_free = True | Premium segment only |
| 3 | Price | < $3.00 | Removes asset flips and shovelware |
| 4 | NSFW tags | "Sexual Content", "NSFW", "Hentai", "Adult Only" | Out of scope |
| 5 | Relevant tag weight | < 2.0 (only when tags parsed successfully) | Must belong to genre cluster |

Filters are applied in this order (1–3 before the HTML fetch, 4–5 after). First matching rule determines the drop reason.

**Review count is a flag, not a filter (v3):** games with < 50 reviews stay in the dataset with `is_low_data=True`. Dropping them baked survivorship bias into the architecture; now it's a post-processing choice.

### Relevant Tag Weights (for filter #5)

All target tags weigh 1.0, except partial signals at 0.5: **Colony Sim** (seven dwarves are not a colony) and **Top-Down** (adjacent camera, partial credit). A game must accumulate total weight ≥ 2.0 across its top-20 tags.

## 8. Output Files

### survival_rts_dataset.csv

Main dataset. Sorted by `estimated_sales` descending. UTF-8 with BOM for Excel compatibility.

All CSV exports are sanitized against Excel formula injection: Steam-controlled strings (names, developers, tags) starting with `=` `+` `-` `@` get a leading apostrophe so Excel treats them as text, not formulas.

### survival_rts_dropped.csv

All filtered-out games with an additional `drop_reason` column. Same schema plus the reason field.

### unborn.csv

Unreleased (coming soon) games with the same schema — the pipeline of future competitors.

### histograms/{appid}.json

Raw monthly review histograms, only with `--with-histograms`. Gitignored.

### checkpoint.json

Internal state for resume capability. Not intended for analysis.

## 9. CLI Interface

```
python parse_steam_genre.py [OPTIONS]

Options:
  --limit N           Process only first N appids (for testing)
  --skip-discovery    Skip Phase 1, load appids from checkpoint
  --with-histograms   Also fetch monthly review histograms (adds ~1.5-2h)
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
- Unreleased pipeline size (unborn.csv)
- Count of low-data games in the dataset (is_low_data=True)

## 11. Performance Characteristics

| Metric | Value |
|--------|-------|
| Discovery phase | ~10–15 minutes (8 combos) |
| Fetch speed | ~2s per cheap-dropped game, ~4.5s per surviving game |
| Requests per game | 1–4 (details always; reviews + tags only for survivors; + histogram with flag) |
| Delay between requests | 1.5s details / 0.75s reviews / 1.0s store pages |
| Checkpoint interval | Every 25 games |
| Typical total appids | ~7,000–8,000 (v3: new combos, no lang filter, MIN_APPID 400k) |
| Typical dataset size | ~3,000–4,000 rows (low-data games kept, flagged) |
| Full run time | ~6–8 hours (+1.5–2h with histograms) |

## 12. Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| requests | >= 2.31.0 | HTTP client |
| pandas | >= 2.0.0 | DataFrame operations, CSV export |
| tqdm | >= 4.65.0 | Progress bar |

Python 3.9+ (the `X | Y` annotation syntax is behind `from __future__ import annotations`).

## 13. Known Limitations

- **Tag parsing unreliable**: Steam may block or change the page structure for `InitAppTagModal` (it already renamed the call once). When tags fail to parse, the tag-weight filter is bypassed (safe because discovery already ensures the game matched a tag pair).
- **Price is current base price, not launch price**: discounts are excluded (`initial`), but permanent price changes since launch are not.
- **Boxleiter is an approximation**: Actual sales can vary 2–3x from the estimate. The formula is best used for relative comparison within the dataset, not absolute numbers.
- **Bundle-only rows missed**: search rows with multi-id `data-ds-appid="111,222"` (package listings) don't match the discovery regex — games sold only in bundles are skipped.
- **n_languages is approximate**: parsed from Steam's HTML-ish language string, ±1 possible.
- **No wishlist data**: Wishlists are not publicly available via Steam API.
- **No concurrent player history**: Would require a separate Steam Charts scrape.
- **Rate limiting variability**: Steam's rate limits are not formally documented and can vary. The script may slow down during high-traffic periods.

## 14. Out of Scope

- Wishlist counts
- Concurrent player history
- Deep localization analysis (only the `n_languages` count is collected)
- Update / DLC frequency over time (only the `n_dlc` count is collected)
- Trailer / video metadata
- SteamSpy cross-validation (candidate for v4: tags from SteamSpy would offload the heaviest request to another host and add an independent `owners` estimate)
- Parallel fetch lanes per endpoint (candidate for v4: ~2-2.5x speedup)
