"""
Scoring module tests for VolleyStats.

The live scoring logic lives inside a non-modular IIFE in tracker.js
(SCORE_OWN, SCORE_ERR, computeScoreFromStats, buildEventLogFromHistory,
scoringAction).  We mirror the scoring constants here in Python and verify:

  1. Internal consistency between the three JS scoring paths
  2. Cross-language parity: mirrored JS constants vs Python STAT_POSITIVE /
     STAT_NEGATIVE in app.py
  3. Score-reconstruction regression tests against real match data (read-only)
  4. Unit tests for the Python stat-helper functions build_player_stats() and
     agg_team_stats()
"""

import os
import sqlite3
import pytest

# ---------------------------------------------------------------------------
# Mirrored JS scoring constants
# Keep in sync with tracker.js SCORE_OWN / SCORE_ERR
# ---------------------------------------------------------------------------

SCORE_OWN = {
    ("serve",  "ace"),
    ("attack", "kill"),
    ("block",  "kill"),
}

SCORE_ERR = {
    ("serve",       "error"),
    ("attack",      "error"),
    ("block",       "error"),   # scoring-only — NOT a personal error in reports
    ("receive",     "error"),
    ("freeball",    "error"),
    ("fault",       "fault"),
    ("ball_error",  "ball_error"),
}

# scoringAction in tracker.js classifies by result string alone
SCORING_RESULTS_OWN = {"ace", "kill"}
SCORING_RESULTS_ERR = {"error", "fault", "ball_error"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reconstruct_score(set_id, conn):
    """Return (our_score, opp_score) derived from events for one set."""
    events = conn.execute(
        "SELECT player_id, stat, result FROM events WHERE set_id=? ORDER BY id",
        (set_id,),
    ).fetchall()
    home = opp = 0
    for e in events:
        is_opp = e["player_id"] is None
        key = (e["stat"], e["result"])
        if not is_opp:
            if key in SCORE_OWN:    home += 1
            elif key in SCORE_ERR:  opp  += 1
        else:
            if key in SCORE_ERR:    home += 1
            elif key in SCORE_OWN:  opp  += 1
    return home, opp

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db():
    path = os.path.join(os.path.dirname(__file__), "..", "stats.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()

# ---------------------------------------------------------------------------
# 1. Internal consistency of SCORE_OWN / SCORE_ERR
# ---------------------------------------------------------------------------

def test_score_sets_no_overlap():
    """A (stat, result) pair cannot be both own-point and opponent-point."""
    assert SCORE_OWN.isdisjoint(SCORE_ERR)


def test_scoringaction_covers_score_own():
    """Every SCORE_OWN result must be caught by scoringAction's result check."""
    for stat, result in SCORE_OWN:
        assert result in SCORING_RESULTS_OWN, (
            f"({stat}, {result}) is in SCORE_OWN but scoringAction "
            f"result='{result}' is not in {SCORING_RESULTS_OWN}"
        )


def test_scoringaction_covers_score_err():
    """Every SCORE_ERR result must be caught by scoringAction's result check."""
    for stat, result in SCORE_ERR:
        assert result in SCORING_RESULTS_ERR, (
            f"({stat}, {result}) is in SCORE_ERR but scoringAction "
            f"result='{result}' is not in {SCORING_RESULTS_ERR}"
        )

# ---------------------------------------------------------------------------
# 2. Cross-language parity: JS constants <-> Python STAT_POSITIVE / STAT_NEGATIVE
# ---------------------------------------------------------------------------

def test_stat_positive_matches_score_own():
    """
    SCORE_OWN must be a subset of STAT_POSITIVE.

    STAT_POSITIVE may contain additional quality-only positives (e.g.
    freeball 2/3 are good plays but don't directly end the rally).  Every
    direct point-ending action in SCORE_OWN must still be positive in reports.
    """
    from app import STAT_POSITIVE
    py_positive = {
        (stat, result)
        for stat, results in STAT_POSITIVE.items()
        for result in results
    }
    not_in_positive = SCORE_OWN - py_positive
    assert not not_in_positive, (
        f"SCORE_OWN entries missing from STAT_POSITIVE: {not_in_positive}"
    )


def test_score_err_subset_of_stat_negative_or_exceptions():
    """
    Every SCORE_ERR entry must either be in STAT_NEGATIVE or be a documented
    exception.  This ensures that anything that directly costs a point is
    accounted for in the Python constants (or the exception list is explicit).

    Known exceptions:
      block.error  — scores a point for the opponent but is NOT a personal
                     error in reports (block attempts are not penalised).

    Note: STAT_NEGATIVE may contain quality-only negatives that don't end a
    rally (e.g. freeball.1-freeball), so the reverse subset is not required.
    """
    from app import STAT_NEGATIVE
    py_negative = {
        (stat, result)
        for stat, results in STAT_NEGATIVE.items()
        for result in results
    }
    KNOWN_SCORING_ONLY_EXCEPTIONS = {("block", "error")}
    unexpected = (SCORE_ERR - py_negative) - KNOWN_SCORING_ONLY_EXCEPTIONS
    assert not unexpected, (
        f"Undocumented SCORE_ERR entries absent from STAT_NEGATIVE: {unexpected}\n"
        f"Either add them to STAT_NEGATIVE or to KNOWN_SCORING_ONLY_EXCEPTIONS."
    )


def test_score_err_extras_are_intentional():
    """
    Document every SCORE_ERR entry that is absent from STAT_NEGATIVE.
    This test is a changelog guard — if this set changes, someone deliberately
    added or removed a scoring-only rule and this test will surface it.
    """
    from app import STAT_NEGATIVE
    py_negative = {
        (stat, result)
        for stat, results in STAT_NEGATIVE.items()
        for result in results
    }
    scoring_only = SCORE_ERR - py_negative
    assert scoring_only == {("block", "error")}, (
        f"Expected only block.error as the scoring-only exception, "
        f"got: {scoring_only}"
    )

# ---------------------------------------------------------------------------
# 3. Regression: score reconstruction from real match events
# ---------------------------------------------------------------------------

TOLERANCE = 3  # max acceptable per-side gap per set

REGRESSION_FIXTURES = [
    # (game_id, opponent, [(set_id, actual_opp_score, actual_our_score)])
    # Games 7-9 have dense event coverage; earlier games have sparse tracking.
    (7, "Caruur E", [(30, 25, 21), (31, 25, 14), (32, 25, 23)]),
    (8, "Kalken C", [(36, 16, 25), (37, 15, 25), (39, 23, 25)]),
    (9, "Gimm-e C", [(43, 25, 15), (44, 25, 20), (45, 25, 14)]),
]


@pytest.mark.parametrize("game_id,opponent,sets", REGRESSION_FIXTURES)
def test_score_reconstruction(game_id, opponent, sets, db):
    for snum, (set_id, actual_opp, actual_us) in enumerate(sets, 1):
        our_score, opp_score = _reconstruct_score(set_id, db)
        label = f"Game {game_id} vs {opponent} set {snum} (set_id={set_id})"
        assert abs(opp_score - actual_opp) <= TOLERANCE, (
            f"{label}: opponent reconstructed {opp_score}, actual {actual_opp} "
            f"(gap {actual_opp - opp_score})"
        )
        assert abs(our_score - actual_us) <= TOLERANCE, (
            f"{label}: our score reconstructed {our_score}, actual {actual_us} "
            f"(gap {actual_us - our_score})"
        )

# ---------------------------------------------------------------------------
# 4. Unit tests for Python stat helpers
# ---------------------------------------------------------------------------

def test_build_player_stats_kill_and_error():
    from app import build_player_stats
    events = [
        {"player_id": 1, "stat": "attack", "result": "kill"},
        {"player_id": 1, "stat": "attack", "result": "kill"},
        {"player_id": 1, "stat": "attack", "result": "error"},
        {"player_id": 2, "stat": "serve",  "result": "ace"},
    ]
    players = [
        {"id": 1, "name": "Alice", "number": "1"},
        {"id": 2, "name": "Bob",   "number": "2"},
    ]
    result = build_player_stats(events, players)
    alice = next(p for p in result if p["name"] == "Alice")
    bob   = next(p for p in result if p["name"] == "Bob")

    assert alice["stats"]["attack"]["kill"]  == 2
    assert alice["stats"]["attack"]["error"] == 1
    assert alice["stats"]["attack"]["raw"]   == 1   # 2 kills - 1 error
    assert bob["stats"]["serve"]["ace"]      == 1
    assert bob["stats"]["serve"]["raw"]      == 1


def test_build_player_stats_empty_player():
    from app import build_player_stats
    result = build_player_stats([], [{"id": 1, "name": "Alice", "number": "1"}])
    assert result[0]["stats"]["attack"]["total"] == 0
    assert result[0]["stats"]["serve"]["quality"] == 0.0
    assert result[0]["total_events"] == 0


def test_agg_team_stats_sums_across_players():
    from app import agg_team_stats
    events = [
        {"player_id": 1, "stat": "attack", "result": "kill"},
        {"player_id": 2, "stat": "attack", "result": "kill"},
        {"player_id": 1, "stat": "attack", "result": "error"},
    ]
    totals = agg_team_stats(events)
    assert totals["attack"]["kill"]  == 2
    assert totals["attack"]["error"] == 1
    assert totals["attack"]["raw"]   == 1   # 2 kills - 1 error


def test_agg_team_stats_serve_quality():
    from app import agg_team_stats
    events = [
        {"player_id": 1, "stat": "serve", "result": "3-serve"},
        {"player_id": 1, "stat": "serve", "result": "3-serve"},
        {"player_id": 1, "stat": "serve", "result": "1-serve"},
    ]
    totals = agg_team_stats(events)
    # quality = (3 + 3 + 1) / 3 = 2.33
    assert totals["serve"]["quality"] == round((3 + 3 + 1) / 3, 2)


def test_agg_team_stats_block_error_not_negative():
    """block.error must NOT appear in STAT_NEGATIVE — it affects score only."""
    from app import agg_team_stats, STAT_NEGATIVE
    events = [{"player_id": 1, "stat": "block", "result": "error"}]
    totals = agg_team_stats(events)
    # raw = positives - negatives; block has no negatives in STAT_NEGATIVE
    assert totals["block"]["raw"] == 0
    assert "error" not in STAT_NEGATIVE.get("block", set())
