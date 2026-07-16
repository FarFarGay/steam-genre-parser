"""
Steam Action Base-Building Genre Parser
Collects metadata for Action/Base Building/Physics/Destruction/Tower Defense games (2018-2025)
and estimates sales using the Boxleiter formula.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# IDs verified against store.steampowered.com/tagdata/populartags/english (2026-07-16).
# Old "Base Building"=4748 returned 0 results in Steam search; old "Colony Sim"=4094
# pointed at an unrelated tag — both fixed here.
TARGET_TAGS = {
    "Action": 19,
    "Base Building": 7332,
    "Physics": 3968,
    "Destruction": 5363,
    "Tower Defense": 1645,
    "Survival": 1662,
    "Colony Sim": 220585,
    "Hack and Slash": 1646,
    "Action RPG": 4231,
    "Dungeon Crawler": 1720,
    "Isometric": 5851,
    "Top-Down": 4791,
    "Mechs": 4821,
}

# Weighted relevance for filter #6: a game must accumulate >= 2.0 to pass.
# Colony Sim and Top-Down count as partial signal (0.5).
RELEVANT_TAG_WEIGHTS = {
    "Action": 1.0,
    "Base Building": 1.0,
    "Physics": 1.0,
    "Destruction": 1.0,
    "Tower Defense": 1.0,
    "Survival": 1.0,
    "Hack and Slash": 1.0,
    "Action RPG": 1.0,
    "Dungeon Crawler": 1.0,
    "Isometric": 1.0,
    "Mechs": 1.0,
    "Colony Sim": 0.5,
    "Top-Down": 0.5,
}
RELEVANT_WEIGHT_MIN = 2.0

NSFW_TAGS = {"Sexual Content", "NSFW", "Hentai", "Adult Only"}

YEAR_MIN = 2018
YEAR_MAX = 2025

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "SurvivalRTSResearch/1.0 (research bot)",
    "Accept-Language": "en-US,en;q=0.9",
})

DELAY = 1.5
CHECKPOINT_FILE = "checkpoint.json"
CHECKPOINT_EVERY = 25


# ---------------------------------------------------------------------------
# Boxleiter formula
# ---------------------------------------------------------------------------

def boxleiter_multiplier(release_year: int) -> int:
    if release_year <= 2017:
        return 70
    elif release_year <= 2020:
        return 50
    elif release_year <= 2023:
        return 35
    else:
        return 30


def estimate_sales(total_reviews: int, release_year: int) -> int:
    return total_reviews * boxleiter_multiplier(release_year)


def estimate_revenue_usd(estimated_sales: int, price_usd: float) -> float:
    if price_usd is None or price_usd <= 0:
        return 0.0
    return estimated_sales * price_usd * 0.45


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

# 6 retries with exponential backoff up to 64s: Steam's 429 bursts outlast
# a 3-retry/8s ceiling, which silently truncated discovery pagination.
def polite_get(url: str, params: dict = None, max_retries: int = 6) -> requests.Response | None:
    for attempt in range(max_retries):
        try:
            resp = SESSION.get(url, params=params, timeout=20)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503):
                wait = 2 ** (attempt + 1)
                tqdm.write(f"  HTTP {resp.status_code}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            tqdm.write(f"  HTTP {resp.status_code} for {url}")
            return None
        except requests.RequestException as e:
            wait = 2 ** (attempt + 1)
            tqdm.write(f"  Request error: {e}, retrying in {wait}s...")
            time.sleep(wait)
    return None


# ---------------------------------------------------------------------------
# Discovery: find appids via Steam search by tag
# ---------------------------------------------------------------------------

def discover_appids_for_tag_combo(tag_ids: list[int], combo_name: str) -> set[int]:
    """Scrape Steam search pages for a combination of tags, return set of appids."""
    appids = set()
    page = 0
    tags_param = ",".join(str(t) for t in tag_ids)

    while True:
        url = "https://store.steampowered.com/search/results/"
        params = {
            "query": "",
            "start": page * 50,
            "count": 50,
            "tags": tags_param,
            "category1": 998,
            "supportedlang": "english",
            "ndl": 1,
            "snr": "1_7_7_230_7",
            "infinite": 1,
        }

        resp = polite_get(url, params)
        if not resp:
            break

        try:
            data = resp.json()
        except Exception:
            break

        html = data.get("results_html", "")
        total = data.get("total_count", 0)

        found = re.findall(r'data-ds-appid="(\d+)"', html)
        if not found:
            break

        for aid in found:
            appids.add(int(aid))

        page += 1
        loaded = page * 50

        if page % 5 == 0:
            tqdm.write(f"  [{combo_name}] page {page}, found {len(appids)} apps (total in Steam: {total})")

        if loaded >= total:
            break

        time.sleep(1.0)

    tqdm.write(f"  [{combo_name}] done: {len(appids)} appids")
    return appids


# Tag combos: pairs that define our genre cluster (intersection, not union)
TAG_COMBOS = [
    ([7332, 19], "Base Building + Action"),
    ([7332, 3968], "Base Building + Physics"),
    ([1645, 19], "Tower Defense + Action"),
    ([7332, 5363], "Base Building + Destruction"),
    ([1662, 7332], "Survival + Base Building"),
]


def discover_all_appids() -> set[int]:
    """Discover appids using tag pair combinations (intersections)."""
    all_appids = set()
    for tag_ids, combo_name in TAG_COMBOS:
        tqdm.write(f"\nDiscovering: {combo_name}")
        found = discover_appids_for_tag_combo(tag_ids, combo_name)
        all_appids.update(found)
        time.sleep(1)

    tqdm.write(f"\nTotal unique appids discovered: {len(all_appids)}")
    return all_appids


# ---------------------------------------------------------------------------
# Fetch metadata per app
# ---------------------------------------------------------------------------

def fetch_app_details(appid: int) -> dict | None:
    """Fetch app details from Steam store API."""
    url = f"https://store.steampowered.com/api/appdetails"
    params = {"appids": appid, "cc": "us", "l": "en"}

    resp = polite_get(url, params)
    if not resp:
        return None

    try:
        data = resp.json()
    except Exception:
        return None

    app_data = data.get(str(appid), {})
    if not app_data.get("success"):
        return None

    return app_data.get("data")


def fetch_review_summary(appid: int) -> dict:
    """Fetch review counts from Steam reviews API."""
    url = f"https://store.steampowered.com/appreviews/{appid}"
    params = {
        "json": 1,
        "num_per_page": 0,
        "language": "all",
        "purchase_type": "all",
    }

    resp = polite_get(url, params)
    if not resp:
        return {}

    try:
        data = resp.json()
    except Exception:
        return {}

    summary = data.get("query_summary", {})
    return summary


def fetch_app_tags(appid: int) -> list[str]:
    """Fetch user-defined tags for an app from the store page."""
    url = f"https://store.steampowered.com/app/{appid}"
    resp = polite_get(url)
    if not resp:
        return []

    # Tags are embedded in the page as InitAppTagData
    match = re.search(r'InitAppTagData\(\s*\[(.+?)\]', resp.text, re.DOTALL)
    if not match:
        return []

    try:
        raw = "[" + match.group(1) + "]"
        tags_data = json.loads(raw)
        return [t.get("name", "") for t in tags_data[:10] if t.get("name")]
    except Exception:
        return []


def parse_release_date(release_info: dict) -> tuple[str | None, int | None]:
    """Parse release date from app details into (iso_date, year)."""
    if not release_info:
        return None, None

    if release_info.get("coming_soon"):
        return None, None

    date_str = release_info.get("date", "")
    if not date_str:
        return None, None

    # Try various formats Steam uses
    for fmt in ("%b %d, %Y", "%d %b, %Y", "%B %d, %Y", "%d %B, %Y", "%Y"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.strftime("%Y-%m-%d"), dt.year
        except ValueError:
            continue

    # Try to extract just the year
    year_match = re.search(r"(20\d{2})", date_str)
    if year_match:
        year = int(year_match.group(1))
        return f"{year}-01-01", year

    return None, None


def build_game_record(appid: int, details: dict, review_summary: dict, tags: list[str]) -> dict:
    """Build a flat dict for one game."""
    release_date, release_year = parse_release_date(details.get("release_date"))

    price_data = details.get("price_overview", {})
    is_free = details.get("is_free", False)
    price_usd = None
    if is_free:
        price_usd = 0.0
    elif price_data:
        price_usd = price_data.get("final", 0) / 100.0

    genres = [g.get("description", "") for g in details.get("genres", [])]

    platforms = details.get("platforms", {})
    platform_list = []
    if platforms.get("windows"):
        platform_list.append("windows")
    if platforms.get("mac"):
        platform_list.append("mac")
    if platforms.get("linux"):
        platform_list.append("linux")

    metacritic = details.get("metacritic", {})
    metacritic_score = metacritic.get("score") if metacritic else None

    total_reviews = review_summary.get("total_reviews", 0)
    positive_reviews = review_summary.get("total_positive", 0)
    positive_pct = round(positive_reviews / total_reviews * 100, 1) if total_reviews > 0 else 0
    review_desc = review_summary.get("review_score_desc", "")

    est_sales = estimate_sales(total_reviews, release_year) if release_year else 0
    est_revenue = estimate_revenue_usd(est_sales, price_usd) if price_usd is not None else 0

    return {
        "appid": appid,
        "name": details.get("name", ""),
        "developer": "; ".join(details.get("developers", [])),
        "publisher": "; ".join(details.get("publishers", [])),
        "release_date": release_date,
        "release_year": release_year,
        "price_usd": price_usd,
        "is_free": is_free,
        "tags": "; ".join(tags),
        "genres": "; ".join(genres),
        "total_reviews": total_reviews,
        "positive_reviews": positive_reviews,
        "positive_percentage": positive_pct,
        "review_score_desc": review_desc,
        "platforms": ", ".join(platform_list),
        "metacritic_score": metacritic_score,
        "header_image_url": details.get("header_image", ""),
        "estimated_sales": est_sales,
        "estimated_revenue_usd": round(est_revenue, 2),
    }


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def relevant_tag_weight(tags: list[str]) -> float:
    return sum(RELEVANT_TAG_WEIGHTS.get(t, 0.0) for t in tags)


def has_nsfw_tags(tags: list[str]) -> bool:
    return any(t in NSFW_TAGS for t in tags)


def filter_game(record: dict, tags: list[str]) -> str | None:
    """Return drop reason or None if game passes all filters."""
    year = record.get("release_year")
    if year is None or year < YEAR_MIN or year > YEAR_MAX:
        return f"release_year={year} outside {YEAR_MIN}-{YEAR_MAX}"

    if record.get("total_reviews", 0) < 50:
        return f"total_reviews={record.get('total_reviews', 0)} < 50"

    if record.get("is_free"):
        return "is_free=True"

    price = record.get("price_usd")
    if price is not None and price < 3:
        return f"price_usd={price} < 3"

    if has_nsfw_tags(tags):
        return "NSFW tags"

    # Only filter by tag weight if we actually got tags (parsing can fail)
    if tags and relevant_tag_weight(tags) < RELEVANT_WEIGHT_MIN:
        return (f"relevant tag weight {relevant_tag_weight(tags)} < {RELEVANT_WEIGHT_MIN}: "
                f"{[t for t in tags if t in RELEVANT_TAG_WEIGHTS]}")

    return None


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def load_checkpoint() -> dict:
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    return {"fetched": {}, "dropped": {}}


def save_checkpoint(state: dict):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(state, f)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(df: pd.DataFrame, dropped_df: pd.DataFrame):
    print("\n" + "=" * 60)
    print("DATASET SUMMARY")
    print("=" * 60)

    print(f"\nTotal games collected: {len(df)}")
    print(f"Total games dropped:  {len(dropped_df)}")

    if len(dropped_df) > 0:
        print("\nTop drop reasons:")
        # Simplify reasons for grouping
        reasons = dropped_df["drop_reason"].apply(
            lambda r: r.split("=")[0].strip() if "=" in r else r.split(":")[0].strip()
        )
        for reason, count in reasons.value_counts().head(5).items():
            print(f"  {reason}: {count}")

    if len(df) > 0:
        print(f"\nDistribution by year:")
        for year in sorted(df["release_year"].dropna().unique()):
            count = len(df[df["release_year"] == year])
            print(f"  {int(year)}: {count} games")

        print(f"\nSales tiers:")
        hits = len(df[df["estimated_sales"] >= 1_000_000])
        successes = len(df[(df["estimated_sales"] >= 200_000) & (df["estimated_sales"] < 1_000_000)])
        mid = len(df[(df["estimated_sales"] >= 50_000) & (df["estimated_sales"] < 200_000)])
        below = len(df[df["estimated_sales"] < 50_000])
        print(f"  Hits (1M+):              {hits}")
        print(f"  Successes (200K-1M):     {successes}")
        print(f"  Mid (50K-200K):          {mid}")
        print(f"  Below break-even (<50K): {below}")

        print(f"\nTop 10 by estimated revenue:")
        top10 = df.nlargest(10, "estimated_revenue_usd")
        for _, row in top10.iterrows():
            rev = row["estimated_revenue_usd"]
            sales = row["estimated_sales"]
            print(f"  {row['name'][:45]:<45} ${rev:>12,.0f}  ({sales:>10,} sales)")

    print("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Steam Survival RTS Genre Parser")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of apps to fetch (0=all)")
    parser.add_argument("--skip-discovery", action="store_true", help="Skip discovery, use checkpoint")
    args = parser.parse_args()

    os.chdir(Path(__file__).parent)

    checkpoint = load_checkpoint()

    # --- Discovery ---
    if args.skip_discovery and "appids" in checkpoint:
        all_appids = set(checkpoint["appids"])
        print(f"Loaded {len(all_appids)} appids from checkpoint")
    else:
        print("Phase 1: Discovering games by tags...")
        all_appids = discover_all_appids()
        checkpoint["appids"] = list(all_appids)
        save_checkpoint(checkpoint)

    # Skip appids below 700000 — these are almost always pre-2018 games
    MIN_APPID = 700000
    all_appids = {a for a in all_appids if a >= MIN_APPID}
    print(f"After filtering appid >= {MIN_APPID}: {len(all_appids)} apps")

    appids_list = sorted(all_appids)
    if args.limit > 0:
        appids_list = appids_list[:args.limit]
        print(f"\n--limit {args.limit}: processing only {len(appids_list)} apps")

    # --- Fetch metadata ---
    print(f"\nPhase 2: Fetching metadata for {len(appids_list)} apps...")

    fetched = checkpoint.get("fetched", {})
    dropped = checkpoint.get("dropped", {})
    records = []
    drop_records = []
    count = 0

    # Restore already-fetched records
    for aid_str, rec in fetched.items():
        records.append(rec)
    for aid_str, drec in dropped.items():
        drop_records.append(drec)

    already_done = set(int(k) for k in fetched.keys()) | set(int(k) for k in dropped.keys())
    remaining = [a for a in appids_list if a not in already_done]

    print(f"  Already fetched: {len(already_done)}, remaining: {len(remaining)}")

    for appid in tqdm(remaining, desc="Fetching"):
        # 1. Get details
        details = fetch_app_details(appid)
        time.sleep(DELAY)

        if not details:
            drop_records.append({
                "appid": appid, "name": "", "drop_reason": "failed to fetch details"
            })
            dropped[str(appid)] = drop_records[-1]
            count += 1
            if count % CHECKPOINT_EVERY == 0:
                save_checkpoint(checkpoint)
            continue

        # Skip non-games (DLC, software, etc.)
        app_type = details.get("type", "")
        if app_type != "game":
            drop_records.append({
                "appid": appid, "name": details.get("name", ""), "drop_reason": f"type={app_type}"
            })
            dropped[str(appid)] = drop_records[-1]
            count += 1
            if count % CHECKPOINT_EVERY == 0:
                save_checkpoint(checkpoint)
            continue

        # 2. Get reviews
        review_summary = fetch_review_summary(appid)
        time.sleep(DELAY)

        # 3. Get tags
        tags = fetch_app_tags(appid)
        time.sleep(DELAY)

        # Build record
        record = build_game_record(appid, details, review_summary, tags)

        # Filter
        drop_reason = filter_game(record, tags)
        if drop_reason:
            record["drop_reason"] = drop_reason
            drop_records.append(record)
            dropped[str(appid)] = record
        else:
            records.append(record)
            fetched[str(appid)] = record

        count += 1
        if count % CHECKPOINT_EVERY == 0:
            checkpoint["fetched"] = fetched
            checkpoint["dropped"] = dropped
            save_checkpoint(checkpoint)

    # Final save
    checkpoint["fetched"] = fetched
    checkpoint["dropped"] = dropped
    save_checkpoint(checkpoint)

    # --- Build DataFrames and export ---
    print("\nPhase 3: Building CSV output...")

    df = pd.DataFrame(records)
    if len(df) > 0:
        df = df.sort_values("estimated_sales", ascending=False).reset_index(drop=True)
        df.to_csv("survival_rts_dataset.csv", index=False, encoding="utf-8-sig")
        print(f"  Saved survival_rts_dataset.csv ({len(df)} games)")

    dropped_df = pd.DataFrame(drop_records)
    if len(dropped_df) > 0:
        dropped_df.to_csv("survival_rts_dropped.csv", index=False, encoding="utf-8-sig")
        print(f"  Saved survival_rts_dropped.csv ({len(dropped_df)} games)")
    else:
        dropped_df = pd.DataFrame()

    print_summary(df, dropped_df)


if __name__ == "__main__":
    main()
