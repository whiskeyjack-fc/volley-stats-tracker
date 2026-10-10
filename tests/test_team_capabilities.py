import sqlite3
from types import SimpleNamespace

import pytest

from app import _can_view_team_capabilities, _overall_capability_scores


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE club_team_trainers (team_id INTEGER, user_id INTEGER);
        CREATE TABLE club_team_players (team_id INTEGER, season_id INTEGER, profile_id INTEGER, roles TEXT);
        CREATE TABLE capabilities (id INTEGER PRIMARY KEY, key TEXT, label_nl TEXT, category TEXT,
                                   sort_order INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1);
        CREATE TABLE player_capability_scores (id INTEGER PRIMARY KEY AUTOINCREMENT, player_id INTEGER,
                                               capability TEXT, score INTEGER, created_by INTEGER,
                                               created_at TEXT, source TEXT);
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, profile_id INTEGER);
        CREATE TABLE player_profiles (id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT);
        INSERT INTO capabilities (key, label_nl, category) VALUES
            ('serve', 'Serve', 'technical'), ('attack', 'Aanval', 'technical'), ('drive', 'Drive', 'personality');
        INSERT INTO club_team_trainers VALUES (1, 10);
        INSERT INTO club_team_players VALUES (1, 5, 100, 'head_coach'), (1, 5, 101, 'assistant_coach,player'),
                                             (1, 5, 102, 'team_manager'), (1, 5, 103, 'player');
    """)
    return conn


def _user(role="trainer", uid=99, profile_id=None):
    return SimpleNamespace(role=role, id=uid, profile_id=profile_id)


@pytest.mark.parametrize("role", ["coordinator", "admin"])
def test_coordinator_and_admin_see_every_team(db, role):
    assert _can_view_team_capabilities(db, _user(role), 2, 5)


def test_assigned_trainer_sees_own_team_only(db):
    assert _can_view_team_capabilities(db, _user(uid=10), 1, 5)
    assert not _can_view_team_capabilities(db, _user(uid=10), 2, 5)


@pytest.mark.parametrize("profile_id,expected", [(100, True), (101, True), (102, False), (103, False)])
def test_profile_roles(db, profile_id, expected):
    assert _can_view_team_capabilities(db, _user(profile_id=profile_id), 1, 5) is expected


def test_coach_role_in_other_season_not_counted(db):
    assert not _can_view_team_capabilities(db, _user(profile_id=100), 1, 6)


def test_overall_scores_technical_only_and_separate_sources(db):
    db.executescript("""
        INSERT INTO player_capability_scores (player_id, capability, score, created_by, created_at, source) VALUES
            (1, 'serve', 60, 10, '2026-01-01', 'coach'),
            (1, 'attack', 50, 10, '2026-01-01', 'coach'),
            (1, 'drive', 80, 10, '2026-01-01', 'coach'),
            (1, 'serve', 40, NULL, '2026-01-01', 'self'),
            (1, 'serve', 30, NULL, '2026-02-01', 'self');
    """)
    scores = _overall_capability_scores(db, [1, 2])
    assert scores[1]["coach"] == {
        "raw": 55.0, "rounded": 55,
        "breakdown": [{"label": "Aanval", "score": 50}, {"label": "Serve", "score": 60}],
    }
    assert scores[1]["self"] == {
        "raw": 30.0, "rounded": 30,
        "breakdown": [{"label": "Aanval", "score": None}, {"label": "Serve", "score": 30}],
    }
    assert scores[2] == {"coach": None, "self": None}
