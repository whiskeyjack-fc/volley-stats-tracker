"""One-off analysis: HP3 last-season player capability scores (20-80 scale).

Throwaway script — not part of the app, not wired into any route. Reads
stats.db read-only and prints a table to stdout. See
/memories/session/plan.md for the full method writeup.
"""
import sqlite3
import statistics
import sys

DB_PATH = "stats.db"
TEAM_SHORT_NAME = "HP3"

# --- copied from app.py (STAT_RESULTS / STAT_QUALITY_WEIGHTS / STAT_POSITIVE / STAT_NEGATIVE) ---
STAT_RESULTS = {
    "serve":    ["error", "1-serve", "2-serve", "3-serve", "ace"],
    "receive":  ["error", "1-receive", "2-receive", "3-receive", "overpass"],
    "attack":   ["kill", "error"],
    "block":    ["kill", "error"],
    "freeball": ["error", "1-freeball", "2-freeball", "3-freeball"],
    "fault":      ["fault"],
    "ball_error": ["ball_error"],
}
STAT_QUALITY_WEIGHTS = {
    "serve":    {"error": 0, "1-serve": 1, "2-serve": 2, "3-serve": 3, "ace": 4},
    "receive":  {"error": 0, "1-receive": 1, "2-receive": 2, "3-receive": 3, "overpass": 0},
    "freeball": {"error": 0, "1-freeball": 1, "2-freeball": 2, "3-freeball": 3},
}
STAT_POSITIVE = {
    "serve":    {"ace"},
    "attack":   {"kill"},
    "receive":  set(),
    "block":    {"kill"},
    "freeball": set(),
}
STAT_NEGATIVE = {
    "serve":    {"error"},
    "attack":   {"error"},
    "receive":  {"error"},
    "block":    set(),
    "freeball": {"error"},
    "fault":      {"fault"},
    "ball_error": {"ball_error"},
}

# capability -> (stat, metric) where metric is "quality" or "efficiency" (raw/total)
CAPABILITY_MAP = {
    "serve":             ("serve", "quality", 10),
    "reception":         ("receive", "quality", 10),
    "attack":            ("attack", "efficiency", 10),
    "block":              ("block", "efficiency", 5),
    "freeball_receive":  ("freeball", "quality", 10),
}
EXCLUDED_CAPABILITIES = ["pass", "defense", "freeball_serve"]


def calc_stat_counts(events, stat, results):
    """Mirrors app.py's _calc_stat_counts for a single player's events."""
    cnt = {r: sum(1 for e in events if e["stat"] == stat and e["result"] == r) for r in results}
    cnt["total"] = sum(cnt[r] for r in results)
    pos = sum(cnt[r] for r in results if r in STAT_POSITIVE.get(stat, set()))
    neg = sum(cnt[r] for r in results if r in STAT_NEGATIVE.get(stat, set()))
    cnt["raw"] = pos - neg
    if stat in STAT_QUALITY_WEIGHTS:
        w = STAT_QUALITY_WEIGHTS[stat]
        weighted = sum(cnt[r] * w.get(r, 0) for r in results)
        cnt["quality"] = round(weighted / cnt["total"], 3) if cnt["total"] else 0.0
    return cnt


def round_to_5(value):
    return int(round(value / 5.0) * 5)


def clamp(value, lo=20, hi=80):
    return max(lo, min(hi, value))


def percentile_of(value, all_values):
    less = sum(1 for v in all_values if v < value)
    equal = sum(1 for v in all_values if v == value)
    return (less + 0.5 * equal) / len(all_values) * 100


def main():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    team = conn.execute(
        "SELECT id, name, short_name FROM club_teams WHERE short_name = ? COLLATE NOCASE",
        (TEAM_SHORT_NAME,),
    ).fetchone()
    if not team:
        sys.exit(f"No club_teams row found with short_name='{TEAM_SHORT_NAME}'")

    season_row = conn.execute(
        "SELECT season FROM games WHERE club_team_id = ? AND season != '' ORDER BY played_at DESC LIMIT 1",
        (team["id"],),
    ).fetchone()
    if not season_row:
        sys.exit(f"No games found for club_team_id={team['id']} ({TEAM_SHORT_NAME})")
    season = season_row["season"]

    print(f"Team: {team['name']} (short_name={team['short_name']}, id={team['id']})")
    print(f"Resolved last season: {season!r}")

    games = conn.execute(
        "SELECT id FROM games WHERE club_team_id = ? AND season = ?",
        (team["id"], season),
    ).fetchall()
    game_ids = [g["id"] for g in games]
    print(f"Games found: {len(game_ids)}")
    if not game_ids:
        sys.exit("No games to analyze.")

    placeholders = ",".join("?" * len(game_ids))
    player_rows = conn.execute(
        f"SELECT id, game_id, name, number, profile_id FROM players WHERE game_id IN ({placeholders})",
        game_ids,
    ).fetchall()
    event_rows = conn.execute(
        f"SELECT game_id, player_id, stat, result FROM events WHERE game_id IN ({placeholders})",
        game_ids,
    ).fetchall()

    profile_names = {}
    for row in conn.execute("SELECT id, first_name, last_name FROM player_profiles").fetchall():
        profile_names[row["id"]] = f"{row['first_name']} {row['last_name']}".strip()

    # group per-game player rows into one identity: profile_id, else normalized name
    def identity_key(p):
        return ("profile", p["profile_id"]) if p["profile_id"] else ("name", p["name"].strip().lower())

    groups = {}  # identity_key -> {"display": str, "player_ids": set}
    for p in player_rows:
        key = identity_key(p)
        bucket = groups.setdefault(key, {"display": None, "player_ids": set()})
        bucket["player_ids"].add(p["id"])
        if bucket["display"] is None:
            bucket["display"] = profile_names.get(p["profile_id"]) if p["profile_id"] else p["name"].strip().title()

    events_by_player_id = {}
    for e in event_rows:
        events_by_player_id.setdefault(e["player_id"], []).append(e)

    # per-identity: {stat: counts_dict}
    per_player_stats = {}
    for key, bucket in groups.items():
        pevents = []
        for pid in bucket["player_ids"]:
            pevents.extend(events_by_player_id.get(pid, []))
        per_player_stats[key] = {
            stat: calc_stat_counts(pevents, stat, results)
            for stat, results in STAT_RESULTS.items()
        }

    print(f"Players with recorded events: {len(groups)}\n")

    # raw metric per capability per player (only for players meeting the threshold)
    raw_metrics = {cap: {} for cap in CAPABILITY_MAP}
    sample_counts = {cap: {} for cap in CAPABILITY_MAP}
    for key, stats in per_player_stats.items():
        for cap, (stat, metric, threshold) in CAPABILITY_MAP.items():
            total = stats[stat]["total"]
            sample_counts[cap][key] = total
            if total < threshold:
                continue
            if metric == "quality":
                raw_metrics[cap][key] = stats[stat]["quality"]
            else:  # efficiency
                raw_metrics[cap][key] = stats[stat]["raw"] / total

    # squad mean/stddev + percentile scoring per capability
    scores = {cap: {} for cap in CAPABILITY_MAP}
    for cap, values_by_key in raw_metrics.items():
        if not values_by_key:
            continue
        values = list(values_by_key.values())
        mean = statistics.mean(values)
        stdev = statistics.stdev(values) if len(values) >= 2 else 0.0
        for key, value in values_by_key.items():
            if stdev > 0:
                z = (value - mean) / stdev
                score_z = clamp(round_to_5(50 + 10 * z))
            else:
                score_z = 50
            pct = percentile_of(value, values)
            score_pct = clamp(round_to_5(20 + pct / 100 * 60))
            scores[cap][key] = (score_z, score_pct)

    # ---- print table ----
    cap_order = ["serve", "reception", "attack", "block", "freeball_receive"]
    header = ["Player"] + [c.replace("_", " ").title() for c in cap_order]
    rows = []
    for key, bucket in groups.items():
        row = [bucket["display"]]
        for cap in cap_order:
            if key in scores[cap]:
                z, pct = scores[cap][key]
                row.append(f"{z}/{pct} (n={sample_counts[cap][key]})")
            else:
                row.append(f"N/A (n={sample_counts[cap][key]})")
        rows.append(row)

    # sort by mean of available z-scores, descending
    def sort_key(row):
        zs = []
        for cell in row[1:]:
            if cell.startswith("N/A"):
                continue
            zs.append(int(cell.split("/")[0]))
        return -statistics.mean(zs) if zs else 0

    rows.sort(key=sort_key)

    col_widths = [max(len(str(row[i])) for row in ([header] + rows)) for i in range(len(header))]
    def fmt_row(row):
        return " | ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row))

    print(fmt_row(header))
    print("-+-".join("-" * w for w in col_widths))
    for row in rows:
        print(fmt_row(row))

    print(f"\nScore format: Z-score/Percentile-score (both 20-80 scale), n = sample size.")
    print(f"Excluded (no raw stat available): {', '.join(EXCLUDED_CAPABILITIES)}")

    conn.close()


if __name__ == "__main__":
    main()
