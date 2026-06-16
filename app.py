import os
import sys
import sqlite3
import csv
import io
import re
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, UTC
from flask import Flask, render_template, request, redirect, url_for, jsonify, make_response, g, flash, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-insecure-key-change-in-production")
app.config["WTF_CSRF_SECRET_KEY"] = os.environ.get("WTF_CSRF_SECRET_KEY", app.secret_key)
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.db")

csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access this page."

@app.template_filter("from_json")
def from_json_filter(value):
    if not value:
        return []
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return []

@app.template_filter("fmt_date")
def fmt_date_filter(value):
    if not value:
        return ''
    parts = str(value)[:10].split('-')
    if len(parts) == 3:
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return str(value)[:10]

@app.template_filter("fmt_datetime")
def fmt_datetime_filter(value):
    if not value:
        return ''
    s = str(value)
    date_part = s[:10]
    time_part = s[11:16] if len(s) > 10 else ''
    parts = date_part.split('-')
    if len(parts) == 3:
        result = f"{parts[2]}/{parts[1]}/{parts[0]}"
        return f"{result} {time_part}" if time_part else result
    return s[:16] if time_part else date_part

@app.context_processor
def inject_helpers():
    return dict(can_view_all=can_view_all, is_admin=is_admin,
                is_kit_manager=is_kit_manager, can_manage_kit=can_manage_kit)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


class User(UserMixin):
    def __init__(self, id, email, role='trainer', profile_id=None):
        self.id = id
        self.email = email
        self.role = role
        self.profile_id = profile_id


@login_manager.user_loader
def load_user(user_id):
    db_conn = sqlite3.connect(DATABASE)
    db_conn.row_factory = sqlite3.Row
    row = db_conn.execute("SELECT id, email, role, profile_id FROM users WHERE id=?", (int(user_id),)).fetchone()
    db_conn.close()
    return User(row["id"], row["email"], row["role"], row["profile_id"]) if row else None


def can_view_all():
    """True for coordinator and admin — they see every user's data."""
    return current_user.is_authenticated and current_user.role in ('coordinator', 'admin')


def is_admin():
    return current_user.is_authenticated and current_user.role == 'admin'


def is_kit_manager():
    return current_user.is_authenticated and current_user.role == 'kit_manager'


def can_manage_kit():
    return current_user.is_authenticated and current_user.role in ('kit_manager', 'coordinator', 'admin')


def _uid_cond():
    """Return (sql AND fragment, params) scoping queries to the current user.
    Returns ('', []) for coordinator/admin roles who see all data."""
    if can_view_all():
        return "", []
    return " AND user_id=?", [current_user.id]

def _team_cond():
    """Return (sql AND fragment, params) scoping club_teams queries to trainer's assigned teams.
    A trainer has access to a team if they appear in club_team_trainers OR their linked
    player_profile has a non-player role in club_team_players for that team.
    Returns ('', []) for coordinator/admin roles who see all teams."""
    if can_view_all():
        return "", []
    uid = current_user.id
    profile_id = getattr(current_user, "profile_id", None)
    if profile_id:
        return (
            " AND id IN ("
            "SELECT team_id FROM club_team_trainers WHERE user_id=?"
            " UNION "
            "SELECT team_id FROM club_team_players "
            "WHERE profile_id=? AND roles IS NOT NULL AND roles!='' AND roles NOT LIKE '%player%'"
            ")",
            [uid, profile_id],
        )
    return " AND id IN (SELECT team_id FROM club_team_trainers WHERE user_id=?)", [uid]

def _collect_profile_ids(form_values):
    """Deduplicate a list of player_profile_id form values.
    Returns a list of unique integer IDs, preserving first-occurrence order."""
    seen = set()
    result = []
    for pid_str in form_values:
        pid_str = pid_str.strip()
        if not pid_str:
            continue
        pid = int(pid_str)
        if pid in seen:
            continue
        seen.add(pid)
        result.append(pid)
    return result

def _resolve_profile_id(first, last):
    """Return profile_id matching normalized full name (first+" "+last), or None."""
    norm = (first + " " + last).strip().lower()
    if not norm:
        return None
    db = get_db()
    row = db.execute(
        "SELECT id FROM player_profiles "
        "WHERE lower(first_name) || ' ' || lower(last_name) = ?",
        (norm,)
    ).fetchone()
    return row["id"] if row else None

def _save_profile(form, profile_id=None):
    """Parse roster form data and INSERT (profile_id=None) or UPDATE player_profiles.
    Returns the saved profile's id, or None if the row was not found on UPDATE."""
    first         = form.get("first_name", "").strip()
    last          = form.get("last_name", "").strip()
    dob           = form.get("date_of_birth", "").strip() or None
    number        = form.get("number", "").strip() or None
    status        = form.get("status", "active").strip()
    if status not in ("active", "prospect", "trial", "unknown", "inactive"):
        status = "active"
    positions     = form.get("positions", "").strip() or None
    tags_raw      = form.get("tags", "").strip()
    tags          = json.dumps([t.strip() for t in tags_raw.split(",") if t.strip()]) if tags_raw else None
    notes         = form.get("notes", "").strip() or None
    federation_id = form.get("federation_id", "").strip() or None
    is_staff      = 1 if form.get("is_staff") else 0
    now = datetime.now(UTC).isoformat()
    db  = get_db()
    if profile_id is None:
        cur = db.execute(
            "INSERT INTO player_profiles "
            "(first_name, last_name, date_of_birth, number, status, positions, tags, notes, "
            " federation_id, is_staff, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (first, last, dob, number, status, positions, tags, notes,
             federation_id, is_staff, current_user.id, now, now)
        )
        db.commit()
        return cur.lastrowid
    cur = db.execute(
        "UPDATE player_profiles "
        "SET first_name=?, last_name=?, date_of_birth=?, number=?, "
        "    status=?, positions=?, tags=?, notes=?, federation_id=?, is_staff=?, updated_at=? "
        "WHERE id=?",
        (first, last, dob, number, status, positions, tags, notes,
         federation_id, is_staff, now, profile_id)
    )
    db.commit()
    return profile_id if cur.rowcount else None

# ── DB helpers ───────────────────────────────────────────────────────────────

def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'trainer',
            profile_id    INTEGER REFERENCES player_profiles(id)
        );

        CREATE TABLE IF NOT EXISTS games (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER REFERENCES users(id),
            season      TEXT NOT NULL DEFAULT '',
            team_name   TEXT NOT NULL,
            opponent    TEXT NOT NULL,
            played_at   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS players (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id    INTEGER NOT NULL REFERENCES games(id),
            name       TEXT NOT NULL,
            number     TEXT,
            profile_id INTEGER REFERENCES player_profiles(id)
        );

        CREATE TABLE IF NOT EXISTS sets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id    INTEGER NOT NULL REFERENCES games(id),
            set_number INTEGER NOT NULL,
            set_type   TEXT NOT NULL DEFAULT 'main',
            finished   INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id    INTEGER NOT NULL REFERENCES games(id),
            set_id     INTEGER REFERENCES sets(id),
            player_id  INTEGER REFERENCES players(id),
            stat       TEXT NOT NULL,
            result     TEXT NOT NULL,
            ts         TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS seasons (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            name    TEXT NOT NULL,
            UNIQUE(user_id, name)
        );

        CREATE TABLE IF NOT EXISTS club_teams (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            name    TEXT NOT NULL,
            UNIQUE(name)
        );

        CREATE TABLE IF NOT EXISTS club_team_players (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id    INTEGER NOT NULL REFERENCES club_teams(id),
            name       TEXT NOT NULL,
            number     TEXT,
            profile_id INTEGER REFERENCES player_profiles(id)
        );

        CREATE TABLE IF NOT EXISTS player_profiles (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name    TEXT NOT NULL,
            last_name     TEXT NOT NULL,
            date_of_birth TEXT,
            number        TEXT,
            status        TEXT NOT NULL DEFAULT 'active',
            positions     TEXT,
            tags          TEXT,
            notes         TEXT,
            created_by    INTEGER REFERENCES users(id),
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS player_remarks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id   INTEGER NOT NULL REFERENCES player_profiles(id),
            remark_type TEXT NOT NULL,
            content     TEXT NOT NULL,
            due_date    TEXT,
            is_private  INTEGER NOT NULL DEFAULT 0,
            created_by  INTEGER REFERENCES users(id),
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS training_groups (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            description TEXT,
            created_by  INTEGER REFERENCES users(id),
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS training_group_players (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id  INTEGER NOT NULL REFERENCES training_groups(id),
            player_id INTEGER NOT NULL REFERENCES player_profiles(id),
            UNIQUE(group_id, player_id)
        );

        CREATE TABLE IF NOT EXISTS club_team_trainers (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES club_teams(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            UNIQUE(team_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS kit_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            model         TEXT NOT NULL,
            type          TEXT NOT NULL,
            size          TEXT NOT NULL,
            number        TEXT,
            name_printed  TEXT,
            status        TEXT NOT NULL DEFAULT 'in stock',
            state         TEXT NOT NULL DEFAULT 'new',
            store         TEXT,
            profile_id    INTEGER REFERENCES player_profiles(id),
            team_id       INTEGER REFERENCES club_teams(id),
            date_added    TEXT,
            date_removed  TEXT,
            is_deleted    INTEGER NOT NULL DEFAULT 0,
            created_by    INTEGER REFERENCES users(id),
            created_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS kit_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id    INTEGER NOT NULL REFERENCES kit_items(id),
            action     TEXT NOT NULL,
            profile_id INTEGER REFERENCES player_profiles(id),
            team_id    INTEGER REFERENCES club_teams(id),
            note       TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL
        );
    """)
    db.commit()
    db.close()

def migrate_db():
    """Non-destructive migrations for existing databases."""
    db = sqlite3.connect(DATABASE)
    for sql in [
        "ALTER TABLE games ADD COLUMN season TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE events ADD COLUMN set_id INTEGER REFERENCES sets(id)",
        """
        CREATE TABLE IF NOT EXISTS sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL REFERENCES games(id),
            set_number INTEGER NOT NULL,
            set_type TEXT NOT NULL DEFAULT 'main',
            finished INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )""",
        "CREATE TABLE IF NOT EXISTS seasons (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE)",
        "CREATE TABLE IF NOT EXISTS club_teams (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE)",
        """CREATE TABLE IF NOT EXISTS club_team_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES club_teams(id),
            name TEXT NOT NULL,
            number TEXT
        )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_seasons_null_user ON seasons(name) WHERE user_id IS NULL",
        "INSERT OR IGNORE INTO seasons (name) SELECT DISTINCT season FROM games WHERE season != '' AND season NOT IN (SELECT name FROM seasons)",
        # user accounts
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'trainer')",
        "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'trainer'",
        "ALTER TABLE games ADD COLUMN user_id INTEGER REFERENCES users(id)",
        # player profiles and scouting tables
        """CREATE TABLE IF NOT EXISTS player_profiles (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name    TEXT NOT NULL,
            last_name     TEXT NOT NULL,
            date_of_birth TEXT,
            number        TEXT,
            status        TEXT NOT NULL DEFAULT 'active',
            positions     TEXT,
            tags          TEXT,
            notes         TEXT,
            created_by    INTEGER REFERENCES users(id),
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS player_remarks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id   INTEGER NOT NULL REFERENCES player_profiles(id),
            remark_type TEXT NOT NULL,
            content     TEXT NOT NULL,
            due_date    TEXT,
            is_private  INTEGER NOT NULL DEFAULT 0,
            created_by  INTEGER REFERENCES users(id),
            created_at  TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS training_groups (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            description TEXT,
            created_by  INTEGER REFERENCES users(id),
            created_at  TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS training_group_players (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id  INTEGER NOT NULL REFERENCES training_groups(id),
            player_id INTEGER NOT NULL REFERENCES player_profiles(id),
            UNIQUE(group_id, player_id)
        )""",
        """CREATE TABLE IF NOT EXISTS club_team_trainers (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES club_teams(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            UNIQUE(team_id, user_id)
        )""",
        "ALTER TABLE players ADD COLUMN profile_id INTEGER REFERENCES player_profiles(id)",
        "ALTER TABLE club_team_players ADD COLUMN profile_id INTEGER REFERENCES player_profiles(id)",
        "ALTER TABLE users ADD COLUMN profile_id INTEGER REFERENCES player_profiles(id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_club_team_players_profile ON club_team_players(team_id, profile_id) WHERE profile_id IS NOT NULL",
        "ALTER TABLE player_profiles ADD COLUMN federation_id TEXT",
        "ALTER TABLE club_teams ADD COLUMN division TEXT",
        "ALTER TABLE club_teams ADD COLUMN short_name TEXT",
        "ALTER TABLE club_team_players ADD COLUMN season_id INTEGER REFERENCES seasons(id)",
        "DROP INDEX IF EXISTS uq_club_team_players_profile",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_ctp_season ON club_team_players(team_id, season_id, profile_id) WHERE profile_id IS NOT NULL AND season_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_ctp_legacy ON club_team_players(team_id, profile_id) WHERE profile_id IS NOT NULL AND season_id IS NULL",
        "ALTER TABLE club_team_players ADD COLUMN roles TEXT",
        "ALTER TABLE player_profiles ADD COLUMN is_staff INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE games ADD COLUMN club_team_id INTEGER REFERENCES club_teams(id)",
        """CREATE TABLE IF NOT EXISTS club_team_season_info (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id   INTEGER NOT NULL REFERENCES club_teams(id),
            season_id INTEGER NOT NULL REFERENCES seasons(id),
            short_name TEXT,
            division   TEXT,
            UNIQUE(team_id, season_id)
        )""",
        """CREATE TABLE IF NOT EXISTS kit_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            model         TEXT NOT NULL DEFAULT '',
            type          TEXT NOT NULL DEFAULT '',
            size          TEXT NOT NULL DEFAULT '',
            number        TEXT,
            name_printed  TEXT,
            status        TEXT NOT NULL DEFAULT 'in stock',
            state         TEXT NOT NULL DEFAULT 'new',
            store         TEXT,
            profile_id    INTEGER REFERENCES player_profiles(id),
            team_id       INTEGER REFERENCES club_teams(id),
            date_added    TEXT,
            date_removed  TEXT,
            is_deleted    INTEGER NOT NULL DEFAULT 0,
            created_by    INTEGER REFERENCES users(id),
            created_at    TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS kit_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id    INTEGER NOT NULL REFERENCES kit_items(id),
            action     TEXT NOT NULL,
            profile_id INTEGER REFERENCES player_profiles(id),
            team_id    INTEGER REFERENCES club_teams(id),
            note       TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL
        )""",
        "ALTER TABLE club_teams ADD COLUMN federation_reeks TEXT",
        """CREATE TABLE IF NOT EXISTS federation_match_cache (
            id           INTEGER PRIMARY KEY CHECK(id=1),
            fetched_at   TEXT NOT NULL,
            matches_json TEXT NOT NULL
        )""",
    ]:
        try:
            db.execute(sql)
            db.commit()
        except Exception as exc:
            msg = str(exc).lower()
            if "duplicate column" not in msg and "already exists" not in msg:
                print(f"migrate_db warning: {exc}", file=sys.stderr)

    # Ensure whiskeyjack.fc@gmail.com is admin
    try:
        db.execute("UPDATE users SET role='admin' WHERE email='whiskeyjack.fc@gmail.com'")
        db.commit()
    except Exception:
        pass

    # Rename legacy 'alumni' status to 'unknown'
    try:
        db.execute("UPDATE player_profiles SET status='unknown' WHERE status='alumni'")
        db.commit()
    except Exception:
        pass

    # Recreate club_teams with UNIQUE(user_id, name) if not already migrated
    cols = {row[1] for row in db.execute("PRAGMA table_info(club_teams)").fetchall()}
    if "user_id" not in cols:
        try:
            db.execute("ALTER TABLE club_teams RENAME TO _club_teams_bak")
            db.execute("""CREATE TABLE club_teams (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id),
                name    TEXT NOT NULL,
                UNIQUE(user_id, name)
            )""")
            db.execute("INSERT INTO club_teams (id, name) SELECT id, name FROM _club_teams_bak")
            db.execute("DROP TABLE _club_teams_bak")
            db.commit()
        except Exception:
            pass

    # Recreate seasons with UNIQUE(user_id, name) if not already migrated
    cols = {row[1] for row in db.execute("PRAGMA table_info(seasons)").fetchall()}
    if "user_id" not in cols:
        try:
            db.execute("ALTER TABLE seasons RENAME TO _seasons_bak")
            db.execute("""CREATE TABLE seasons (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id),
                name    TEXT NOT NULL,
                UNIQUE(user_id, name)
            )""")
            db.execute("INSERT INTO seasons (id, name) SELECT id, name FROM _seasons_bak")
            db.execute("DROP TABLE _seasons_bak")
            db.commit()
        except Exception:
            pass

    # Migrate club_teams: UNIQUE(user_id, name) → UNIQUE(name) for global team ownership
    ct_sql_row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='club_teams'"
    ).fetchone()
    if ct_sql_row and "UNIQUE(user_id, name)" in (ct_sql_row[0] or ""):
        try:
            db.execute("""
                INSERT OR IGNORE INTO club_team_trainers (team_id, user_id)
                SELECT id, user_id FROM club_teams WHERE user_id IS NOT NULL
            """)
            db.commit()
            db.execute("ALTER TABLE club_teams RENAME TO _club_teams_v2_bak")
            db.execute("""CREATE TABLE club_teams (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id),
                name    TEXT NOT NULL,
                UNIQUE(name)
            )""")
            db.execute(
                "INSERT OR IGNORE INTO club_teams (id, user_id, name) "
                "SELECT id, user_id, name FROM _club_teams_v2_bak"
            )
            db.execute("DROP TABLE _club_teams_v2_bak")
            db.commit()
        except Exception as exc:
            print(f"migrate_db club_teams UNIQUE migration: {exc}", file=sys.stderr)

    # Migrate club_teams: remove UNIQUE(name), enforce uniqueness on short_name instead
    ct_sql_row3 = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='club_teams'"
    ).fetchone()
    if ct_sql_row3 and "UNIQUE(name)" in (ct_sql_row3[0] or ""):
        try:
            db.execute("ALTER TABLE club_teams RENAME TO _club_teams_v3_bak")
            db.execute("""CREATE TABLE club_teams (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER REFERENCES users(id),
                name       TEXT NOT NULL,
                division   TEXT,
                short_name TEXT
            )""")
            db.execute(
                "INSERT OR IGNORE INTO club_teams (id, user_id, name, division, short_name) "
                "SELECT id, user_id, name, division, short_name FROM _club_teams_v3_bak"
            )
            db.execute("DROP TABLE _club_teams_v3_bak")
            db.commit()
        except Exception as exc:
            print(f"migrate_db club_teams short_name unique migration: {exc}", file=sys.stderr)
    try:
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_club_teams_short_name "
            "ON club_teams(short_name) WHERE short_name IS NOT NULL"
        )
        db.commit()
    except Exception as exc:
        print(f"migrate_db uq_club_teams_short_name: {exc}", file=sys.stderr)

    # Backfill club_team_id on games from the matching club_teams.name
    try:
        db.execute("""
            UPDATE games SET club_team_id = (
                SELECT id FROM club_teams WHERE name = games.team_name LIMIT 1
            ) WHERE club_team_id IS NULL AND team_name != ''
        """)
        db.commit()
    except Exception as exc:
        print(f"migrate_db club_team_id backfill: {exc}", file=sys.stderr)


KIT_MODELS   = ['dames', 'heren', 'kinder']
KIT_TYPES    = ['wedstrijd', 'opwarm', 'training', 'short', 'libero', 'polo', 'vest', 'hoodie', 'overig']
KIT_SIZES    = ['XS', 'S', 'M', 'L', 'XL', 'XXL', '2XL', '3XL', '4XL',
                '4', '5', '6', '7', '8', '10/12', '12/14', '16',
                '34', '36', '38', '40', '42', '44', '46', '48']
KIT_STATUSES = ['in stock', 'assigned', 'lost', 'retired']
KIT_STATES   = ['nieuw', 'misdruk', 'gebruikt', 'beschadigd']

TEAM_MEMBER_ROLES = [
    ('player',          'Speler'),
    ('head_coach',      'Hoofdcoach'),
    ('assistant_coach', 'Assistent-coach'),
    ('team_manager',    'Teammanager'),
    ('medical',         'Medisch'),
    ('marker',          'Markeerder'),
    ('video_analyst',   'Video-analist'),
]

STAT_RESULTS = {
    "serve":    ["error", "1-serve", "2-serve", "3-serve", "ace"],
    "receive":  ["error", "1-receive", "2-receive", "3-receive", "overpass"],
    "attack":   ["kill", "error"],
    "block":    ["kill", "error"],
    "freeball": ["error", "1-freeball", "2-freeball", "3-freeball"],
    "fault":      ["fault"],
    "ball_error": ["ball_error"],
}

# Quality weighting: result → score (only for stats with graded levels)
STAT_QUALITY_WEIGHTS = {
    "serve":    {"error": 0, "1-serve": 1, "2-serve": 2, "3-serve": 3, "ace": 4},
    "receive":  {"error": 0, "1-receive": 1, "2-receive": 2, "3-receive": 3, "overpass": 0},
    "freeball": {"error": 0, "1-freeball": 1, "2-freeball": 2, "3-freeball": 3},
}

# Per-stat-group positive/negative subsets
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

def _calc_stat_counts(events, stat, results):
    """Return a counts dict for one stat category over a list of event dicts."""
    cnt = {r: sum(1 for e in events if e["stat"] == stat and e["result"] == r)
           for r in results}
    cnt["total"] = sum(cnt[r] for r in results)
    pos = sum(cnt[r] for r in results if r in STAT_POSITIVE.get(stat, set()))
    neg = sum(cnt[r] for r in results if r in STAT_NEGATIVE.get(stat, set()))
    cnt["raw"]       = pos - neg
    cnt["fault_pct"] = round(neg / cnt["total"] * 100, 1) if cnt["total"] else 0.0
    if stat in STAT_QUALITY_WEIGHTS:
        w = STAT_QUALITY_WEIGHTS[stat]
        weighted = sum(cnt[r] * w.get(r, 0) for r in results)
        cnt["quality"] = round(weighted / cnt["total"], 2) if cnt["total"] else 0.0
    return cnt


_NAME_PARTICLES = {"van", "de", "den", "der", "ter", "ten", "op"}

def _make_display_names(player_stats):
    """Return display labels for a player_stats list.

    Each label is the first name only when that first name is unique among all
    players in the list.  If two or more players share a first name the label
    becomes  FirstName + abbreviated last name, where every particle word is
    shortened to its initial letter and the core surname word is shortened to
    its capitalised initial (e.g. "Jan v. d. B.").
    Names in player_stats are stored lowercase; this function returns
    properly-cased labels.
    """
    def _parse(name_lower):
        parts = name_lower.strip().split()
        if not parts:
            return "", [], None
        first = parts[0]
        rest = parts[1:]
        i = 0
        while i < len(rest) - 1 and rest[i] in _NAME_PARTICLES:
            i += 1
        return first, rest[:i], rest[i] if i < len(rest) else None

    parsed = [_parse(ps["name"].strip().lower()) for ps in player_stats]
    first_counts = {}
    for first, _, _ in parsed:
        first_counts[first] = first_counts.get(first, 0) + 1

    result = []
    for first, particles, core in parsed:
        if first_counts[first] > 1 and (particles or core):
            abbr = [p[0] + "." for p in particles]
            if core:
                abbr.append(core[0].upper() + ".")
            result.append(first.title() + " " + " ".join(abbr))
        else:
            result.append(first.title())
    return result


def build_player_stats(events, players):
    """Return per-player stat summary list given event dicts and player rows."""
    result = []
    for p in players:
        pid   = p["id"]
        pevts = [e for e in events if e["player_id"] == pid]
        by_stat = {
            stat: _calc_stat_counts(pevts, stat, results)
            for stat, results in STAT_RESULTS.items()
        }
        result.append({
            "id": pid, "name": p["name"], "number": p["number"],
            "stats": by_stat,
            "total_events": len(pevts),
        })
    return result

def build_chart_data(player_stats):
    """Build Chart.js data dict from a build_player_stats()-compatible list."""
    return {
        "players": [ps["name"] for ps in player_stats],
        "stat_totals": {
            stat: {r: [ps["stats"][stat][r] for ps in player_stats] for r in results}
            for stat, results in STAT_RESULTS.items()
        },
        "fault_pct": {
            stat: [ps["stats"][stat]["fault_pct"] for ps in player_stats]
            for stat in STAT_RESULTS
        },
        "quality": {
            stat: [ps["stats"][stat].get("quality", 0) for ps in player_stats]
            for stat in STAT_QUALITY_WEIGHTS
        },
        "points_pos": {
            "Ace":         [ps["stats"]["serve"]["ace"]   for ps in player_stats],
            "Attack Kill": [ps["stats"]["attack"]["kill"] for ps in player_stats],
            "Block Kill":  [ps["stats"]["block"]["kill"]  for ps in player_stats],
        },
        "points_neg": {
            "Serve Err":   [-ps["stats"]["serve"]["error"]   for ps in player_stats],
            "Attack Err":  [-ps["stats"]["attack"]["error"]  for ps in player_stats],
            "Receive Err": [-ps["stats"]["receive"]["error"] for ps in player_stats],
            "Fault":       [-ps["stats"]["fault"]["fault"]       for ps in player_stats],
            "Ball Error":  [-ps["stats"]["ball_error"]["ball_error"] for ps in player_stats],
        },
    }


def build_comparison_data(players_data, games):
    """Build per-player aligned arrays for comparison charts (X-axis = games)."""
    labels   = [f"vs {g['opponent']}" for g in games]
    game_ids = [g["id"] for g in games]
    players_out = []
    for p in players_data:
        gsm = {gr["game_id"]: gr["stats"] for gr in p["game_rows"]}
        def _v(gid, stat, result):
            s = gsm.get(gid)
            return s[stat][result] if s else None
        players_out.append({
            "slug":         p["slug"],
            "name":         p["name"].title(),
            "number":       p["number"],
            "points_pos":   [
                ((_v(g, "serve", "ace") or 0) + (_v(g, "attack", "kill") or 0) + (_v(g, "block", "kill") or 0))
                if gsm.get(g) else None for g in game_ids
            ],
            "points_neg":   [
                ((_v(g, "serve", "error") or 0) + (_v(g, "attack", "error") or 0) +
                 (_v(g, "receive", "error") or 0) + (_v(g, "fault", "fault") or 0) +
                 (_v(g, "ball_error", "ball_error") or 0))
                if gsm.get(g) else None for g in game_ids
            ],
            "serve_results":   {r: [_v(g, "serve", r) for g in game_ids] for r in ["error", "1-serve", "2-serve", "3-serve", "ace"]},
            "serve_qual":      [gsm[g]["serve"].get("quality", 0) if gsm.get(g) else None for g in game_ids],
            "serve_fault_pct": [gsm[g]["serve"]["fault_pct"]      if gsm.get(g) else None for g in game_ids],
            "attack_kill":  [_v(g, "attack",   "kill")                              for g in game_ids],
            "attack_err":   [_v(g, "attack",   "error")                             for g in game_ids],
            "receive_results":   {r: [_v(g, "receive", r) for g in game_ids] for r in ["error", "1-receive", "2-receive", "3-receive", "overpass"]},
            "receive_qual":      [gsm[g]["receive"].get("quality", 0) if gsm.get(g) else None for g in game_ids],
            "receive_fault_pct": [gsm[g]["receive"]["fault_pct"]      if gsm.get(g) else None for g in game_ids],
            "block_kill":   [_v(g, "block", "kill")  for g in game_ids],
            "block_err":    [_v(g, "block", "error") for g in game_ids],
        })
    return {"labels": labels, "players": players_out}


def agg_team_stats(events):
    """Aggregate player events into per-stat totals with all derived fields."""
    return {
        stat: _calc_stat_counts(events, stat, results)
        for stat, results in STAT_RESULTS.items()
    }


# ── auth ──────────────────────────────────────────────────────────────────────

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm", "")
        if not email or not password:
            return render_template("register.html", error="Email and password are required.")
        if password != confirm:
            return render_template("register.html", error="Passwords do not match.", email=email)
        if len(password) < 8:
            return render_template("register.html", error="Password must be at least 8 characters.", email=email)
        db = get_db()
        if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            return render_template("register.html", error="An account with that email already exists.")
        db.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?,?,?)",
            (email, generate_password_hash(password), datetime.now(UTC).isoformat())
        )
        db.commit()
        row = db.execute("SELECT id, email, role FROM users WHERE email=?", (email,)).fetchone()
        if not row:
            return render_template("register.html", error="Registration failed. Please try again.", email=email)
        login_user(User(row["id"], row["email"], row["role"]))
        return redirect(url_for("index"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", error_message="Too many login attempts. Please wait a minute and try again.")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        row = db.execute(
            "SELECT id, email, password_hash, role, profile_id FROM users WHERE email=?", (email,)
        ).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return render_template("login.html", error="Invalid email or password.")
        login_user(User(row["id"], row["email"], row["role"], row["profile_id"]))
        next_url = request.args.get("next") or ""
        if not next_url.startswith("/"):
            next_url = url_for("index")
        if row["role"] == 'kit_manager':
            next_url = url_for("kit_list")
        return redirect(next_url)
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    logout_user()
    return redirect(url_for("login"))


# ── access control ────────────────────────────────────────────────────────────

_KIT_MANAGER_ALLOWED = frozenset({
    'kit_list', 'kit_new', 'kit_edit', 'kit_detail', 'kit_add_log', 'kit_log_page',
    'kit_bulk_delete', 'kit_export', 'kit_import_form', 'kit_import_confirm',
    'roster_list', 'roster_detail',
    'login', 'logout', 'static',
})


@app.before_request
def restrict_kit_manager():
    if current_user.is_authenticated and current_user.role == 'kit_manager':
        endpoint = request.endpoint or ''
        if endpoint not in _KIT_MANAGER_ALLOWED:
            from flask import abort
            abort(403)


def require_kit_access():
    """Return a 403 abort if the current user has no access to kit routes."""
    from flask import abort
    if not (can_manage_kit() or is_kit_manager()):
        abort(403)


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return redirect(url_for("roster_list"))


@app.route("/games")
@login_required
def games_list():
    db = get_db()
    ucond, uparams = _uid_cond()
    gcond = " AND g.user_id=?" if ucond else ""
    gcond_sub = " AND user_id=?" if ucond else ""
    teams_rows = db.execute(
        f"SELECT ct.id, ct.name, ct.short_name FROM club_teams ct "
        f"WHERE ct.id IN (SELECT DISTINCT club_team_id FROM games "
        f"WHERE club_team_id IS NOT NULL{gcond_sub}) "
        f"ORDER BY ct.name COLLATE NOCASE",
        uparams
    ).fetchall()
    teams = [dict(r) for r in teams_rows]
    seasons = [s["season"] for s in db.execute(
        f"SELECT DISTINCT season FROM games WHERE season != ''{ucond} ORDER BY season DESC",
        uparams
    ).fetchall()]
    active_team   = request.args.get("team", type=int)
    active_season = request.args.get("season", "")
    if active_team and active_team not in [t["id"] for t in teams]:
        active_team = None
    if active_season and active_season not in seasons:
        active_season = ""
    where = "WHERE 1=1"
    params = list(uparams)
    if active_team:
        where += " AND g.club_team_id=?"
        params.append(active_team)
    if active_season:
        where += " AND g.season=?"
        params.append(active_season)
    games = db.execute(
        f"SELECT g.*, ct.short_name AS team_short_name FROM games g "
        f"LEFT JOIN club_teams ct ON ct.id = g.club_team_id "
        f"{where}{gcond} ORDER BY g.played_at DESC, g.id DESC",
        params
    ).fetchall()
    return render_template("index.html", games=games, seasons=seasons, teams=teams,
                           active_team=active_team, active_season=active_season)


@app.route("/games/new", methods=["GET", "POST"])
@login_required
def new_game():
    db = get_db()
    ucond, uparams = _uid_cond()
    tcond, tparams = _team_cond()
    season_objs = [dict(s) for s in db.execute(
        f"SELECT id, name FROM seasons WHERE 1=1{ucond} ORDER BY name DESC", uparams
    ).fetchall()]
    seasons = [s["name"] for s in season_objs]
    season_id_map = {s["name"]: s["id"] for s in season_objs}
    club_teams = [dict(t) for t in db.execute(
        f"SELECT id, name, short_name FROM club_teams WHERE 1=1{tcond} ORDER BY name COLLATE NOCASE", tparams
    ).fetchall()]
    all_profiles = [dict(r) for r in db.execute(
        "SELECT id, first_name, last_name, number FROM player_profiles "
        "WHERE status='active' ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE"
    ).fetchall()]
    if request.method == "POST":
        team         = request.form["team_name"].strip()
        club_team_id = request.form.get("club_team_id", type=int)
        opp    = request.form["opponent"].strip()
        played = request.form.get("played_at") or datetime.now().strftime("%Y-%m-%d")
        season = request.form.get("season", "").strip()
        cur = db.execute(
            "INSERT INTO games (user_id, season, team_name, club_team_id, opponent, played_at) VALUES (?,?,?,?,?,?)",
            (current_user.id, season, team, club_team_id, opp, played)
        )
        game_id = cur.lastrowid
        try:
            for pid in _collect_profile_ids(request.form.getlist("player_profile_id")):
                profile = db.execute(
                    "SELECT first_name, last_name, number FROM player_profiles WHERE id=?",
                    (pid,)
                ).fetchone()
                if profile:
                    pname = (profile["first_name"] + " " + profile["last_name"]).strip().lower()
                    db.execute(
                        "INSERT INTO players (game_id, name, number, profile_id) VALUES (?,?,?,?)",
                        (game_id, pname, profile["number"] or "", pid)
                    )
            db.commit()
        except Exception:
            db.rollback()
            return render_template("game_setup.html", error="Failed to save players. Please try again.",
                                   seasons=seasons, club_teams=club_teams, all_profiles=all_profiles,
                                   season_id_map=season_id_map)
        return redirect(url_for("track", game_id=game_id))
    return render_template("game_setup.html", seasons=seasons, club_teams=club_teams, all_profiles=all_profiles,
                           season_id_map=season_id_map)


@app.route("/games/<int:game_id>/track")
@login_required
def track(game_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    game = db.execute(f"SELECT * FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone()
    if not game:
        return "Game not found", 404
    players = db.execute("SELECT * FROM players WHERE game_id=? ORDER BY name COLLATE NOCASE", (game_id,)).fetchall()
    players_json = [{"id": p["id"], "name": p["name"], "number": p["number"]} for p in players]
    return render_template("track.html", game=game, players=players, players_json=players_json, stat_results=STAT_RESULTS)


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True})


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/games/<int:game_id>/events", methods=["GET"])
@login_required
def get_events_list(game_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    set_id_filter = request.args.get("set_id", type=int)
    if set_id_filter:
        rows = db.execute(
            "SELECT player_id, stat, result FROM events WHERE game_id=? AND set_id=? ORDER BY id",
            (game_id, set_id_filter)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT player_id, stat, result FROM events WHERE game_id=? ORDER BY id",
            (game_id,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/games/<int:game_id>/events", methods=["POST"])
@csrf.exempt
@login_required
def record_event(game_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    data      = request.get_json()
    player_id = data.get("player_id")   # None / null = opponent
    set_id    = data.get("set_id")      # None = no set assigned
    stat      = data["stat"]
    result    = data["result"]
    if stat not in STAT_RESULTS or result not in STAT_RESULTS[stat]:
        return jsonify({"error": "invalid stat/result"}), 400
    if set_id is not None:
        if not db.execute("SELECT id FROM sets WHERE id=? AND game_id=?", (set_id, game_id)).fetchone():
            return jsonify({"error": "set_id does not belong to this game"}), 400
    db.execute(
        "INSERT INTO events (game_id, set_id, player_id, stat, result, ts) VALUES (?,?,?,?,?,?)",
        (game_id, set_id, player_id, stat, result, datetime.now(UTC).isoformat())
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/games/<int:game_id>/events", methods=["DELETE"])
@csrf.exempt
@login_required
def undo_event(game_id):
    """Undo the single most recent event for this game."""
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    last = db.execute(
        "SELECT id FROM events WHERE game_id=? ORDER BY id DESC LIMIT 1", (game_id,)
    ).fetchone()
    if last:
        db.execute("DELETE FROM events WHERE id=?", (last["id"],))
        db.commit()
    return jsonify({"ok": True})


@app.route("/api/games/<int:game_id>/events/decrement", methods=["POST"])
@csrf.exempt
@login_required
def decrement_event(game_id):
    """Remove the most recent event matching a specific player+stat+result."""
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    data      = request.get_json()
    player_id = data.get("player_id")   # None / null = opponent
    stat      = data["stat"]
    result    = data["result"]
    db = get_db()
    if player_id is None:
        row = db.execute(
            "SELECT id FROM events WHERE game_id=? AND player_id IS NULL AND stat=? AND result=? ORDER BY id DESC LIMIT 1",
            (game_id, stat, result)
        ).fetchone()
    else:
        row = db.execute(
            "SELECT id FROM events WHERE game_id=? AND player_id=? AND stat=? AND result=? ORDER BY id DESC LIMIT 1",
            (game_id, player_id, stat, result)
        ).fetchone()
    if row:
        db.execute("DELETE FROM events WHERE id=?", (row["id"],))
        db.commit()
        return jsonify({"ok": True, "removed": True})
    return jsonify({"ok": True, "removed": False})


@app.route("/api/games/<int:game_id>/stats")
@login_required
def live_stats(game_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    set_id_filter = request.args.get("set_id", type=int)

    if set_id_filter:
        events = db.execute(
            "SELECT * FROM events WHERE game_id=? AND set_id=?", (game_id, set_id_filter)
        ).fetchall()
    else:
        events = db.execute("SELECT * FROM events WHERE game_id=?", (game_id,)).fetchall()

    players = db.execute("SELECT * FROM players WHERE game_id=?", (game_id,)).fetchall()
    events  = [dict(e) for e in events]

    result = {}
    for p in players:
        pid   = p["id"]
        pevts = [e for e in events if e["player_id"] == pid]
        result[str(pid)] = {
            "name": p["name"], "number": p["number"],
            "stats": agg_team_stats(pevts),
            "total_events": len(pevts)
        }

    opp_evts = [e for e in events if e["player_id"] is None]
    result["opponent"] = {
        "stats": agg_team_stats(opp_evts),
        "total_events": len(opp_evts)
    }
    return jsonify(result)


# ── Sets API ──────────────────────────────────────────────────────────────────

@app.route("/api/games/<int:game_id>/sets", methods=["GET"])
@login_required
def get_sets(game_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    sets = db.execute(
        "SELECT * FROM sets WHERE game_id=? ORDER BY created_at", (game_id,)
    ).fetchall()
    return jsonify([dict(s) for s in sets])


@app.route("/api/games/<int:game_id>/sets", methods=["POST"])
@csrf.exempt
@login_required
def create_set(game_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    data       = request.get_json()
    set_number = int(data["set_number"])
    set_type   = data.get("set_type", "main")
    # Check if a set with same number+type already exists for this game
    existing = db.execute(
        "SELECT * FROM sets WHERE game_id=? AND set_number=? AND set_type=?",
        (game_id, set_number, set_type)
    ).fetchone()
    if existing:
        return jsonify({"error": "Set already exists"}), 409
    cur = db.execute(
        "INSERT INTO sets (game_id, set_number, set_type, finished, created_at) VALUES (?,?,?,0,?)",
        (game_id, set_number, set_type, datetime.now(UTC).isoformat())
    )
    db.commit()
    new_set = db.execute("SELECT * FROM sets WHERE id=?", (cur.lastrowid,)).fetchone()
    if not new_set:
        return jsonify({"error": "failed to retrieve set"}), 500
    return jsonify(dict(new_set)), 201


@app.route("/api/games/<int:game_id>/sets/<int:set_id>", methods=["DELETE"])
@csrf.exempt
@login_required
def delete_set(game_id, set_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    existing = db.execute(
        "SELECT id FROM sets WHERE id=? AND game_id=?", (set_id, game_id)
    ).fetchone()
    if not existing:
        return jsonify({"error": "Set not found"}), 404
    db.execute("DELETE FROM events WHERE set_id=? AND game_id=?", (set_id, game_id))
    db.execute("DELETE FROM sets WHERE id=? AND game_id=?", (set_id, game_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/games/<int:game_id>/sets/<int:set_id>/finish", methods=["POST"])
@csrf.exempt
@login_required
def finish_set(game_id, set_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    cur = db.execute("UPDATE sets SET finished=1 WHERE id=? AND game_id=?", (set_id, game_id))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "set not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/games/<int:game_id>/sets/<int:set_id>/reopen", methods=["POST"])
@csrf.exempt
@login_required
def reopen_set(game_id, set_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    cur = db.execute("UPDATE sets SET finished=0 WHERE id=? AND game_id=?", (set_id, game_id))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "set not found"}), 404
    return jsonify({"ok": True})



@app.route("/games/<int:game_id>/report")
@login_required
def report(game_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    game    = db.execute(f"SELECT * FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone()
    if not game:
        return "Game not found", 404
    players = db.execute(
        "SELECT p.* FROM players p"
        " LEFT JOIN player_profiles pp ON pp.id = p.profile_id"
        " WHERE p.game_id=? AND (p.profile_id IS NULL OR pp.is_staff=0)"
        " ORDER BY p.name COLLATE NOCASE",
        (game_id,)
    ).fetchall()
    all_events = [dict(e) for e in db.execute("SELECT * FROM events WHERE game_id=?", (game_id,)).fetchall()]
    sets    = db.execute("SELECT * FROM sets WHERE game_id=? ORDER BY created_at", (game_id,)).fetchall()

    set_id_filter   = request.args.get("set_id",  type=int)
    set_type_filter = request.args.get("type")        # "main" | "reserve" | None

    # Build set-id lookup for type filter
    if set_type_filter in ("main", "reserve"):
        allowed_set_ids = {s["id"] for s in sets if s["set_type"] == set_type_filter}
        events = [e for e in all_events if e["set_id"] in allowed_set_ids]
    elif set_id_filter:
        events = [e for e in all_events if e["set_id"] == set_id_filter]
    else:
        events = all_events

    player_stats = build_player_stats(events, players)
    _display_names = _make_display_names(player_stats)
    for ps, dn in zip(player_stats, _display_names):
        ps["display_name"] = dn

    opp_evts  = [e for e in events if e["player_id"] is None]
    opp_stats = agg_team_stats(opp_evts)

    # Chart.js data
    chart_data = {
        "players": [ps["display_name"] for ps in player_stats],
        "stat_totals": {
            stat: {
                r: [ps["stats"][stat][r] for ps in player_stats]
                for r in results
            }
            for stat, results in STAT_RESULTS.items()
        },
        "fault_pct": {
            stat: [ps["stats"][stat]["fault_pct"] for ps in player_stats]
            for stat in STAT_RESULTS
        },
        "quality": {
            stat: [ps["stats"][stat].get("quality", 0) for ps in player_stats]
            for stat in STAT_QUALITY_WEIGHTS
        },
        "points_pos": {
            "Ace":         [ps["stats"]["serve"]["ace"]   for ps in player_stats],
            "Attack Kill": [ps["stats"]["attack"]["kill"] for ps in player_stats],
            "Block Kill":  [ps["stats"]["block"]["kill"]  for ps in player_stats],
        },
        "points_neg": {
            "Serve Err":   [-ps["stats"]["serve"]["error"]    for ps in player_stats],
            "Attack Err":  [-ps["stats"]["attack"]["error"]   for ps in player_stats],
            "Receive Err": [-ps["stats"]["receive"]["error"]  for ps in player_stats],
            "Fault":       [-ps["stats"]["fault"]["fault"]    for ps in player_stats],
        },
    }

    # Team totals row
    team_totals = {}
    for stat, results in STAT_RESULTS.items():
        team_totals[stat] = {
            r: sum(ps["stats"][stat][r] for ps in player_stats)
            for r in results
        }
        team_totals[stat]["total"] = sum(team_totals[stat][r] for r in results)
        pos = sum(team_totals[stat][r] for r in results if r in STAT_POSITIVE.get(stat, set()))
        neg = sum(team_totals[stat][r] for r in results if r in STAT_NEGATIVE.get(stat, set()))
        team_totals[stat]["raw"]       = pos - neg
        team_totals[stat]["fault_pct"] = round(neg / team_totals[stat]["total"] * 100, 1) if team_totals[stat]["total"] else 0.0
        if stat in STAT_QUALITY_WEIGHTS:
            w = STAT_QUALITY_WEIGHTS[stat]
            weighted = sum(team_totals[stat][r] * w.get(r, 0) for r in results)
            team_totals[stat]["quality"] = round(weighted / team_totals[stat]["total"], 2) if team_totals[stat]["total"] else 0.0

    # Per-set chart data for split-by-set toggle (Main / Reserve filters only)
    per_set_data = []
    if set_type_filter in ("main", "reserve"):
        type_sets = sorted(
            [s for s in sets if s["set_type"] == set_type_filter],
            key=lambda s: s["set_number"]
        )
        for s in type_sets:
            s_events = [e for e in all_events if e["set_id"] == s["id"]]
            s_player_stats = build_player_stats(s_events, players)
            s_chart_data = {
                "players": _display_names,
                "stat_totals": {
                    stat: {
                        r: [ps["stats"][stat][r] for ps in s_player_stats]
                        for r in results
                    }
                    for stat, results in STAT_RESULTS.items()
                },
                "fault_pct": {
                    stat: [ps["stats"][stat]["fault_pct"] for ps in s_player_stats]
                    for stat in STAT_RESULTS
                },
                "quality": {
                    stat: [ps["stats"][stat].get("quality", 0) for ps in s_player_stats]
                    for stat in STAT_QUALITY_WEIGHTS
                },
                "points_pos": {
                    "Ace":         [ps["stats"]["serve"]["ace"]   for ps in s_player_stats],
                    "Attack Kill": [ps["stats"]["attack"]["kill"] for ps in s_player_stats],
                    "Block Kill":  [ps["stats"]["block"]["kill"]  for ps in s_player_stats],
                },
                "points_neg": {
                    "Serve Err":   [-ps["stats"]["serve"]["error"]   for ps in s_player_stats],
                    "Attack Err":  [-ps["stats"]["attack"]["error"]  for ps in s_player_stats],
                    "Receive Err": [-ps["stats"]["receive"]["error"] for ps in s_player_stats],
                    "Fault":       [-ps["stats"]["fault"]["fault"]   for ps in s_player_stats],
                },
            }
            per_set_data.append({
                "id": s["id"],
                "label": f"S{s['set_number']}",
                "chart_data": s_chart_data,
            })

    return render_template(
        "report.html",
        game=game,
        player_stats=player_stats,
        opp_stats=opp_stats,
        stat_results=STAT_RESULTS,
        chart_data=chart_data,
        team_totals=team_totals,
        sets=[dict(s) for s in sets],
        active_set_id=set_id_filter,
        active_set_type=set_type_filter,
        quality_stats=set(STAT_QUALITY_WEIGHTS),
        per_set_data=per_set_data,
    )


@app.route("/seasons")
@login_required
def season_list():
    db = get_db()
    ucond, uparams = _uid_cond()
    seasons = db.execute(
        f"SELECT DISTINCT season FROM games WHERE season != ''{ucond} ORDER BY season DESC",
        uparams
    ).fetchall()
    if seasons:
        return redirect(url_for("season_report", season=seasons[0]["season"]))
    return redirect(url_for("index"))


@app.route("/seasons/<path:season>")
@login_required
def season_report(season):
    db = get_db()
    ucond, uparams = _uid_cond()
    all_seasons = [s["season"] for s in db.execute(
        f"SELECT DISTINCT season FROM games WHERE season != ''{ucond} ORDER BY season DESC",
        uparams
    ).fetchall()]

    active_team = request.args.get("team", type=int)

    # All games in the season (for team list and set-type detection)
    all_season_games = db.execute(
        f"SELECT * FROM games WHERE season=?{ucond} ORDER BY played_at ASC, id ASC",
        [season] + uparams
    ).fetchall()
    if not all_season_games:
        return "Season not found", 404

    team_ids = sorted({g["club_team_id"] for g in all_season_games if g["club_team_id"]})
    teams_rows = db.execute(
        "SELECT id, name, short_name FROM club_teams WHERE id IN (%s)" % ",".join("?" * len(team_ids)),
        team_ids,
    ).fetchall() if team_ids else []
    teams = [dict(r) for r in teams_rows]

    if active_team and active_team in {t["id"] for t in teams}:
        games = [g for g in all_season_games if g["club_team_id"] == active_team]
    else:
        active_team = None
        games = list(all_season_games)

    game_ids = [g["id"] for g in games]
    placeholders = ",".join("?" * len(game_ids))

    all_sets = [dict(s) for s in db.execute(
        f"SELECT * FROM sets WHERE game_id IN ({placeholders}) ORDER BY game_id, created_at",
        game_ids,
    ).fetchall()]

    all_events = [
        dict(e) for e in db.execute(
            f"SELECT * FROM events WHERE game_id IN ({placeholders})", game_ids
        ).fetchall()
    ]

    has_main    = any(s["set_type"] == "main"    for s in all_sets)
    has_reserve = any(s["set_type"] == "reserve" for s in all_sets)

    active_set_type = request.args.get("type")  # "main" | "reserve" | None

    if active_set_type in ("main", "reserve"):
        allowed_set_ids = {s["id"] for s in all_sets if s["set_type"] == active_set_type}
        team_events = [e for e in all_events
                       if e["player_id"] is not None and e["set_id"] in allowed_set_ids]
        # Limit games list to those that actually have sets of the requested type
        games_with_type = {s["game_id"] for s in all_sets if s["set_type"] == active_set_type}
        games = [g for g in games if g["id"] in games_with_type]
    else:
        team_events = [e for e in all_events if e["player_id"] is not None]

    team_kwarg = {"team": active_team} if active_team else {}
    filter_urls = {
        "all":     url_for("season_report", season=season, **team_kwarg),
        "main":    url_for("season_report", season=season, type="main", **team_kwarg),
        "reserve": url_for("season_report", season=season, type="reserve", **team_kwarg),
    }

    if active_set_type:
        # Main / Reserve: one bar per game, skip games with no recorded events
        rows = []
        for g in games:
            g_events = [e for e in team_events if e["game_id"] == g["id"]]
            if not g_events:
                continue
            rows.append({
                "name":      f"vs {g['opponent']}",
                "game_id":   g["id"],
                "played_at": g["played_at"],
                "opponent":  g["opponent"],
                "stats":     agg_team_stats(g_events),
            })
    else:
        # All: aggregate games on the same date into one bar, skip days with no events
        games_by_date = {}
        for g in games:
            games_by_date.setdefault(g["played_at"], []).append(g)
        game_id_to_date = {g["id"]: g["played_at"] for g in games}
        events_by_date = {}
        for e in team_events:
            date = game_id_to_date.get(e["game_id"])
            if date:
                events_by_date.setdefault(date, []).append(e)
        rows = []
        for date in sorted(games_by_date):
            day_games = games_by_date[date]
            day_events = events_by_date.get(date, [])
            if not day_events:
                continue
            opponent = day_games[0]["opponent"]
            rows.append({
                "name":      f"vs {opponent}",
                "game_id":   day_games[0]["id"],
                "played_at": date,
                "opponent":  opponent,
                "stats":     agg_team_stats(day_events),
            })

    season_totals  = agg_team_stats(team_events)
    chart_data_all = build_chart_data(rows)

    # Per-game, per-set chart data for client-side split toggle
    game_set_data = []
    for g in games:
        g_sets = [s for s in all_sets if s["game_id"] == g["id"]]
        if active_set_type in ("main", "reserve"):
            g_sets = [s for s in g_sets if s["set_type"] == active_set_type]
        g_sets.sort(key=lambda s: s["set_number"])
        sets_data = []
        for s in g_sets:
            s_events = [e for e in team_events if e["set_id"] == s["id"]]
            set_label = f"S{s['set_number']}"
            sets_data.append({
                "label":      set_label,
                "chart_data": build_chart_data([{"name": set_label, "stats": agg_team_stats(s_events)}]),
            })
        game_set_data.append({
            "label":     f"vs {g['opponent']}",
            "game_id":   g["id"],
            "played_at": g["played_at"],
            "sets":      sets_data,
        })

    return render_template(
        "season_report.html",
        season=season,
        all_seasons=all_seasons,
        games=[dict(g) for g in games],
        rows=rows,
        season_totals=season_totals,
        stat_results=STAT_RESULTS,
        chart_data_all=chart_data_all,
        quality_stats=set(STAT_QUALITY_WEIGHTS),
        active_set_type=active_set_type,
        has_main=has_main,
        has_reserve=has_reserve,
        filter_urls=filter_urls,
        game_set_data=game_set_data,
        stat_positive={k: list(v) for k, v in STAT_POSITIVE.items()},
        stat_negative={k: list(v) for k, v in STAT_NEGATIVE.items()},
        teams=teams,
        active_team=active_team,
    )


@app.route("/players")
@login_required
def player_report():
    db = get_db()
    active_season    = request.args.get("season", "")
    active_team      = request.args.get("team", type=int)
    active_set_type  = request.args.get("type")
    active_players   = request.args.getlist("player")
    selected_game_ids = set(int(x) for x in request.args.getlist("game") if x.isdigit())

    ucond, uparams = _uid_cond()
    gcond_sub = " AND user_id=?" if ucond else ""
    all_seasons = [s["season"] for s in db.execute(
        f"SELECT DISTINCT season FROM games WHERE season != ''{ucond} ORDER BY season DESC",
        uparams
    ).fetchall()]
    all_game_teams = [dict(r) for r in db.execute(
        f"SELECT ct.id, ct.name, ct.short_name FROM club_teams ct "
        f"WHERE ct.id IN (SELECT DISTINCT club_team_id FROM games "
        f"WHERE club_team_id IS NOT NULL{gcond_sub}) "
        f"ORDER BY ct.name COLLATE NOCASE",
        uparams
    ).fetchall()]

    # Build base params dict (excludes game selection; used for chip URLs)
    def _base_params(**overrides):
        p = {}
        if active_season:    p["season"]    = active_season
        if active_team:      p["team"]      = active_team
        if active_set_type:  p["type"]      = active_set_type
        if active_players:   p["player"]    = list(active_players)
        p.update({k: v for k, v in overrides.items() if v is not None})
        # drop keys explicitly set to "" or empty list
        return {k: v for k, v in p.items() if v != "" and v != []}

    filter_urls = {
        "all":     url_for("player_report", **_base_params(type="")),
        "main":    url_for("player_report", **_base_params(type="main")),
        "reserve": url_for("player_report", **_base_params(type="reserve")),
    }

    # Fetch candidate games (season + game-team filters)
    conditions = list(uparams)
    cond_parts = []
    if ucond:
        cond_parts.append(ucond.lstrip(" AND "))
    if active_season:
        cond_parts.append("season=?")
        conditions.append(active_season)
    if active_team:
        cond_parts.append("club_team_id=?")
        conditions.append(active_team)
    where = ("WHERE " + " AND ".join(cond_parts)) if cond_parts else ""
    params = conditions
    all_candidate_games = db.execute(
        f"SELECT * FROM games {where} ORDER BY played_at ASC, id ASC", params
    ).fetchall()

    candidate_games = [
        {"id": g["id"], "played_at": g["played_at"], "opponent": g["opponent"]}
        for g in all_candidate_games
    ]

    # Determine which games are actually used
    if selected_game_ids:
        games = [g for g in all_candidate_games if g["id"] in selected_game_ids]
    else:
        games = list(all_candidate_games)

    def _render_empty(**extra):
        return render_template(
            "player_report.html",
            players_data=[], players_data_js=[],
            player_chips=[], player_urls={},
            active_players=active_players, is_comparison=False,
            comparison_data_js=None, clear_comparison_url=None,
            all_seasons=all_seasons, all_game_teams=all_game_teams,
            active_season=active_season, active_team=active_team,
            active_set_type=active_set_type,
            has_main=False, has_reserve=False,
            filter_urls=filter_urls,
            candidate_games=candidate_games, selected_game_ids=list(selected_game_ids),
            stat_results=STAT_RESULTS, quality_stats=set(STAT_QUALITY_WEIGHTS),
            stat_positive={k: list(v) for k, v in STAT_POSITIVE.items()},
            stat_negative={k: list(v) for k, v in STAT_NEGATIVE.items()},
            games_count=len(games),
            **extra,
        )

    if not games:
        return _render_empty()

    game_ids = [g["id"] for g in games]
    placeholders = ",".join("?" * len(game_ids))
    all_sets = [dict(s) for s in db.execute(
        f"SELECT * FROM sets WHERE game_id IN ({placeholders})", game_ids
    ).fetchall()]
    all_events = [dict(e) for e in db.execute(
        f"SELECT * FROM events WHERE game_id IN ({placeholders})", game_ids
    ).fetchall()]
    all_players = db.execute(
        "SELECT p.* FROM players p"
        " LEFT JOIN player_profiles pp ON pp.id = p.profile_id"
        f" WHERE p.game_id IN ({placeholders}) AND (p.profile_id IS NULL OR pp.is_staff=0)"
        " ORDER BY p.name COLLATE NOCASE",
        game_ids
    ).fetchall()

    has_main    = any(s["set_type"] == "main"    for s in all_sets)
    has_reserve = any(s["set_type"] == "reserve" for s in all_sets)

    if active_set_type in ("main", "reserve"):
        allowed_set_ids = {s["id"] for s in all_sets if s["set_type"] == active_set_type}
        team_events = [e for e in all_events if e["player_id"] is not None and e["set_id"] in allowed_set_ids]
    else:
        team_events = [e for e in all_events if e["player_id"] is not None]

    # Group player rows by normalized name, optionally filtered to club roster
    name_to_records = {}
    for p in all_players:
        key = p["name"].strip().lower()
        if key not in name_to_records:
            name_to_records[key] = []
        name_to_records[key].append({
            "game_id":   p["game_id"],
            "player_id": p["id"],
            "name":      p["name"],
            "number":    p["number"],
        })

    players_data = []
    for norm_name, records in name_to_records.items():
        pid_set = {r["player_id"] for r in records}
        player_events = [e for e in team_events if e["player_id"] in pid_set]
        if not player_events:
            continue

        display_name   = records[0]["name"].title()
        display_number = records[0]["number"]
        slug = re.sub(r'\W+', '_', norm_name).strip('_') or "player"

        # Build per-game rows in chronological order
        game_rows = []
        for g in games:
            g_pid_set = {r["player_id"] for r in records if r["game_id"] == g["id"]}
            if not g_pid_set:
                continue
            g_events = [e for e in player_events if e["game_id"] == g["id"] and e["player_id"] in g_pid_set]
            if not g_events:
                continue
            game_rows.append({
                "name":      f"vs {g['opponent']}",
                "game_id":   g["id"],
                "played_at": g["played_at"],
                "opponent":  g["opponent"],
                "stats":     agg_team_stats(g_events),
            })

        if not game_rows:
            continue

        # Per-game per-set data for client-side split toggle (main/reserve only)
        player_game_set_data = []
        if active_set_type in ("main", "reserve"):
            for g in games:
                g_pid_set = {r["player_id"] for r in records if r["game_id"] == g["id"]}
                if not g_pid_set:
                    continue
                g_sets = sorted(
                    [s for s in all_sets if s["game_id"] == g["id"] and s["set_type"] == active_set_type],
                    key=lambda s: s["set_number"]
                )
                sets_data = []
                for s in g_sets:
                    s_events = [e for e in player_events
                                if e["game_id"] == g["id"] and e["player_id"] in g_pid_set
                                and e["set_id"] == s["id"]]
                    set_label = f"S{s['set_number']}"
                    sets_data.append({
                        "label":      set_label,
                        "chart_data": build_chart_data([{"name": set_label, "stats": agg_team_stats(s_events)}]),
                    })
                if sets_data:
                    player_game_set_data.append({
                        "label":     f"vs {g['opponent']}",
                        "game_id":   g["id"],
                        "played_at": g["played_at"],
                        "sets":      sets_data,
                    })

        players_data.append({
            "name":               display_name,
            "number":             display_number,
            "slug":               slug,
            "game_rows":          game_rows,
            "totals":             agg_team_stats(player_events),
            "chart_data":         build_chart_data(game_rows),
            "game_set_data":      player_game_set_data,
        })

    players_data.sort(key=lambda p: p["name"].lower())

    # Build player filter chips and toggle URLs
    player_chips = [{"name": p["name"], "number": p["number"], "slug": p["slug"]} for p in players_data]
    _chip_display_names = _make_display_names(player_chips)
    for chip, dn in zip(player_chips, _chip_display_names):
        chip["display_name"] = dn
    valid_slugs = {c["slug"] for c in player_chips}
    active_players = [s for s in active_players if s in valid_slugs]
    if not active_players:
        active_players = [player_chips[0]["slug"]] if player_chips else []

    # Toggle-URL per chip: add slug if absent, remove if already selected
    def _player_toggle_url(slug):
        new_list = sorted(set(active_players).symmetric_difference({slug}))
        return url_for("player_report", **_base_params(player=new_list or []))

    player_urls = {p["slug"]: _player_toggle_url(p["slug"]) for p in player_chips}

    is_comparison = len(active_players) > 1
    active_slugs  = set(active_players)
    players_data_cmp = [p for p in players_data if p["slug"] in active_slugs]

    if is_comparison:
        players_data = players_data_cmp
        comparison_data_js = build_comparison_data(players_data, games)
    else:
        players_data = [p for p in players_data_cmp if p["slug"] == active_players[0]] if active_players else []
        comparison_data_js = None

    clear_comparison_url = (
        url_for("player_report", **_base_params(player=[active_players[0]]))
        if is_comparison and active_players else None
    )

    # Split-by-set only available in single-player mode
    split_available = active_set_type in ("main", "reserve")
    players_data_js = [
        {
            "slug":          p["slug"],
            "name":          p["name"],
            "number":        p["number"],
            "chart_data":    p["chart_data"],
            "game_set_data": p["game_set_data"],
        }
        for p in players_data
    ]

    return render_template(
        "player_report.html",
        players_data=players_data,
        players_data_js=players_data_js,
        player_chips=player_chips,
        player_urls=player_urls,
        active_players=active_players,
        is_comparison=is_comparison,
        comparison_data_js=comparison_data_js,
        clear_comparison_url=clear_comparison_url,
        all_seasons=all_seasons,
        all_game_teams=all_game_teams,
        active_season=active_season,
        active_team=active_team,
        active_set_type=active_set_type,
        has_main=has_main,
        has_reserve=has_reserve,
        filter_urls=filter_urls,
        candidate_games=candidate_games,
        selected_game_ids=list(selected_game_ids),
        stat_results=STAT_RESULTS,
        quality_stats=set(STAT_QUALITY_WEIGHTS),
        stat_positive={k: list(v) for k, v in STAT_POSITIVE.items()},
        stat_negative={k: list(v) for k, v in STAT_NEGATIVE.items()},
        games_count=len(games),
        split_available=split_available,
    )


@app.route("/games/<int:game_id>/export")
@login_required
def export_csv(game_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    game    = db.execute(f"SELECT * FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone()
    if not game:
        return "Game not found", 404
    players = {p["id"]: p for p in db.execute("SELECT * FROM players WHERE game_id=?", (game_id,)).fetchall()}
    events  = db.execute("SELECT * FROM events WHERE game_id=? ORDER BY id", (game_id,)).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["game_id", "team", "opponent", "played_at", "player", "number", "stat", "result", "ts"])
    for e in events:
        if e["player_id"]:
            p = players.get(e["player_id"])
            pname  = p["name"]   if p else "?"
            pnum   = p["number"] if p else "?"
        else:
            pname, pnum = game["opponent"] + " (team)", ""
        writer.writerow([game_id, game["team_name"], game["opponent"], game["played_at"],
                         pname, pnum, e["stat"], e["result"], e["ts"]])

    resp = make_response(output.getvalue())
    resp.headers["Content-Disposition"] = f'attachment; filename="game_{game_id}_stats.csv"'
    resp.headers["Content-Type"] = "text/csv"
    return resp


@app.route("/games/<int:game_id>/edit", methods=["GET", "POST"])
@login_required
def edit_game(game_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    game = db.execute(f"SELECT * FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone()
    if not game:
        return "Game not found", 404

    all_profiles = [dict(r) for r in db.execute(
        "SELECT id, first_name, last_name, number FROM player_profiles "
        "WHERE status='active' ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE"
    ).fetchall()]

    if request.method == "POST":
        team         = request.form["team_name"].strip()
        club_team_id = request.form.get("club_team_id", type=int)
        opp    = request.form["opponent"].strip()
        played = request.form.get("played_at") or game["played_at"]
        season = request.form.get("season", "").strip()
        cur = db.execute(
            "UPDATE games SET team_name=?, club_team_id=?, opponent=?, played_at=?, season=? WHERE id=?",
            (team, club_team_id, opp, played, season, game_id)
        )
        if cur.rowcount == 0:
            return "Game not found", 404

        db.execute("DELETE FROM players WHERE game_id=?", (game_id,))
        for pid in _collect_profile_ids(request.form.getlist("player_profile_id")):
            profile = db.execute(
                "SELECT first_name, last_name, number FROM player_profiles WHERE id=?",
                (pid,)
            ).fetchone()
            if profile:
                pname = (profile["first_name"] + " " + profile["last_name"]).strip().lower()
                db.execute(
                    "INSERT INTO players (game_id, name, number, profile_id) VALUES (?,?,?,?)",
                    (game_id, pname, profile["number"] or "", pid)
                )
        db.commit()
        return redirect(url_for("index"))

    players = db.execute(
        "SELECT * FROM players WHERE game_id=? ORDER BY name COLLATE NOCASE", (game_id,)
    ).fetchall()
    existing_seasons = [s["season"] for s in db.execute(
        f"SELECT DISTINCT season FROM games WHERE season != ''{ucond} ORDER BY season DESC",
        uparams
    ).fetchall()]
    tcond, tparams = _team_cond()
    club_teams = [dict(r) for r in db.execute(
        f"SELECT id, name, short_name FROM club_teams WHERE 1=1{tcond} ORDER BY name COLLATE NOCASE",
        tparams
    ).fetchall()]
    return render_template("edit_game.html", game=game, players=players,
                           existing_seasons=existing_seasons, all_profiles=all_profiles,
                           club_teams=club_teams)


@app.route("/games/<int:game_id>/delete", methods=["POST"])
@login_required
def delete_game(game_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return "Game not found", 404
    try:
        db.execute("DELETE FROM events  WHERE game_id=?", (game_id,))
        db.execute("DELETE FROM players WHERE game_id=?", (game_id,))
        db.execute("DELETE FROM games   WHERE id=?",      (game_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return redirect(url_for("index"))


# ── Club Teams ───────────────────────────────────────────────────────────────

@app.route("/teams")
@login_required
def team_list():
    db = get_db()
    tcond, tparams = _team_cond()
    ucond, uparams = _uid_cond()
    q                = request.args.get('q', '').strip()
    active_season_id = request.args.get('season', type=int)
    seasons = db.execute(
        f"SELECT id, name FROM seasons WHERE 1=1{ucond} ORDER BY name DESC", uparams
    ).fetchall()
    valid_season_ids = {s['id'] for s in seasons}
    if active_season_id and active_season_id not in valid_season_ids:
        active_season_id = None
    if active_season_id:
        season_join        = "?"
        season_join_params = [active_season_id]
    else:
        season_join        = "(SELECT season_id FROM club_team_season_info WHERE team_id = ct.id ORDER BY season_id DESC LIMIT 1)"
        season_join_params = []
    extra_conds  = ""
    extra_params = []
    if q:
        extra_conds += " AND (ct.name LIKE ? OR ct.short_name LIKE ? OR ctsi.short_name LIKE ?)"
        like = '%' + q + '%'
        extra_params += [like, like, like]
    if active_season_id:
        extra_conds += " AND ctsi.season_id IS NOT NULL"
    teams = db.execute(
        f"""SELECT ct.*,
               ctsi.short_name AS season_short_name,
               ctsi.division   AS season_division,
               s.name          AS season_name
            FROM club_teams ct
            LEFT JOIN club_team_season_info ctsi
                   ON ctsi.team_id = ct.id
                  AND ctsi.season_id = {season_join}
            LEFT JOIN seasons s ON s.id = ctsi.season_id
            WHERE 1=1{tcond}{extra_conds}
            ORDER BY COALESCE(NULLIF(ctsi.short_name,''), NULLIF(ct.short_name,''), ct.name) COLLATE NOCASE""",
        season_join_params + tparams + extra_params
    ).fetchall()
    team_player_counts = {}
    team_trainers = {}
    team_non_players = {}
    for t in teams:
        if active_season_id:
            cnt = db.execute(
                "SELECT COUNT(*) AS c FROM club_team_players WHERE team_id=? AND season_id=?",
                (t["id"], active_season_id)
            ).fetchone()["c"]
        else:
            cnt = db.execute(
                """SELECT COUNT(*) AS c FROM club_team_players
                   WHERE team_id=? AND season_id = (
                       SELECT season_id FROM club_team_season_info
                       WHERE team_id=? ORDER BY season_id DESC LIMIT 1
                   )""", (t["id"], t["id"])
            ).fetchone()["c"]
        team_player_counts[t["id"]] = cnt
        trainers = db.execute(
            "SELECT u.id, u.email FROM users u "
            "JOIN club_team_trainers ctt ON ctt.user_id = u.id "
            "WHERE ctt.team_id=?",
            (t["id"],)
        ).fetchall()
        team_trainers[t["id"]] = [dict(tr) for tr in trainers]
        non_players = db.execute(
            "SELECT ctp.name, ctp.roles FROM club_team_players ctp "
            "WHERE ctp.team_id=? AND ctp.roles IS NOT NULL AND ctp.roles != '' "
            "AND ctp.roles NOT LIKE '%player%' "
            "ORDER BY ctp.name COLLATE NOCASE",
            (t["id"],)
        ).fetchall()
        team_non_players[t["id"]] = [dict(r) for r in non_players]
    all_trainers = db.execute(
        "SELECT id, email FROM users WHERE role='trainer' ORDER BY email COLLATE NOCASE"
    ).fetchall()
    return render_template("team_list.html", teams=teams,
                           team_player_counts=team_player_counts,
                           team_trainers=team_trainers,
                           team_non_players=team_non_players,
                           all_trainers=all_trainers,
                           seasons=seasons,
                           active_season_id=active_season_id,
                           q=q)


@app.route("/teams/new", methods=["GET", "POST"])
@login_required
def new_team():
    if not can_view_all():
        return "Forbidden", 403
    db = get_db()
    ucond, uparams = _uid_cond()
    seasons = [dict(s) for s in db.execute(
        f"SELECT id, name FROM seasons WHERE 1=1{ucond} ORDER BY name DESC", uparams
    ).fetchall()]
    if request.method == "POST":
        name             = request.form.get("team_name", "").strip()
        division         = request.form.get("division", "").strip() or None
        short_name       = request.form.get("short_name", "").strip() or None
        federation_reeks = request.form.get("federation_reeks", "").strip() or None
        if not name:
            return render_template("team_form.html", team=None, players=[], all_profiles=[],
                                   seasons=seasons, selected_season_id=None, error="Team name is required.",
                                   team_member_roles=TEAM_MEMBER_ROLES)
        if short_name:
            existing = db.execute(
                "SELECT id FROM club_teams WHERE short_name=?", (short_name,)
            ).fetchone()
            if existing:
                return render_template("team_form.html", team=None, players=[], all_profiles=[],
                                       seasons=seasons, selected_season_id=None,
                                       error="A team with this short name already exists.",
                                       team_member_roles=TEAM_MEMBER_ROLES)
        try:
            cur = db.execute("INSERT INTO club_teams (user_id, name, division, short_name, federation_reeks) VALUES (?,?,?,?,?)", (current_user.id, name, division, short_name, federation_reeks))
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            return render_template("team_form.html", team=None, players=[], all_profiles=[],
                                   seasons=seasons, selected_season_id=None, error="A team with this short name already exists.",
                                   team_member_roles=TEAM_MEMBER_ROLES)
        except Exception:
            db.rollback()
            raise
        return redirect(url_for("edit_team", team_id=cur.lastrowid))
    return render_template("team_form.html", team=None, players=[], all_profiles=[],
                           seasons=seasons, selected_season_id=None, team_member_roles=TEAM_MEMBER_ROLES)


@app.route("/teams/<int:team_id>/edit", methods=["GET", "POST"])
@login_required
def edit_team(team_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    tcond, tparams = _team_cond()
    team = db.execute(f"SELECT * FROM club_teams WHERE id=?{tcond}", [team_id] + tparams).fetchone()
    if not team:
        return "Team not found", 404
    all_profiles = [dict(r) for r in db.execute(
        "SELECT id, first_name, last_name, number FROM player_profiles "
        "WHERE status='active' ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE"
    ).fetchall()]
    seasons = [dict(s) for s in db.execute(
        f"SELECT id, name FROM seasons WHERE 1=1{ucond} ORDER BY name DESC", uparams
    ).fetchall()]
    if request.method == "POST":
        name             = request.form.get("team_name", "").strip()
        division         = request.form.get("division", "").strip() or None
        short_name       = request.form.get("short_name", "").strip() or None
        federation_reeks = request.form.get("federation_reeks", "").strip() or None
        season_id_str    = request.form.get("season_id", "").strip()
        season_id     = int(season_id_str) if season_id_str.isdigit() else None
        if season_id:
            players = db.execute(
                "SELECT * FROM club_team_players WHERE team_id=? AND season_id=? ORDER BY name COLLATE NOCASE",
                (team_id, season_id)
            ).fetchall()
        else:
            players = db.execute(
                "SELECT * FROM club_team_players WHERE team_id=? AND season_id IS NULL ORDER BY name COLLATE NOCASE",
                (team_id,)
            ).fetchall()
        selected_season_id = season_id
        season_info = {"short_name": short_name, "division": division} if season_id else None
        if not name:
            return render_template("team_form.html", team=team, players=players, all_profiles=all_profiles,
                                   seasons=seasons, selected_season_id=selected_season_id, error="Team name is required.",
                                   season_info=season_info, team_member_roles=TEAM_MEMBER_ROLES)
        if short_name and not season_id:
            existing = db.execute(
                "SELECT id FROM club_teams WHERE short_name=? AND id!=?", (short_name, team_id)
            ).fetchone()
            if existing:
                return render_template("team_form.html", team=team, players=players, all_profiles=all_profiles,
                                       seasons=seasons, selected_season_id=selected_season_id,
                                       error="A team with this short name already exists.",
                                       season_info=season_info, team_member_roles=TEAM_MEMBER_ROLES)
        try:
            if season_id:
                # Only update global team fields; short_name/division are season-specific
                cur = db.execute("UPDATE club_teams SET name=?, federation_reeks=? WHERE id=?", (name, federation_reeks, team_id))
                if cur.rowcount == 0:
                    return "Team not found", 404
                db.execute(
                    "INSERT INTO club_team_season_info (team_id, season_id, short_name, division) VALUES (?,?,?,?) "
                    "ON CONFLICT(team_id, season_id) DO UPDATE SET short_name=excluded.short_name, division=excluded.division",
                    (team_id, season_id, short_name, division)
                )
            else:
                cur = db.execute("UPDATE club_teams SET name=?, division=?, short_name=?, federation_reeks=? WHERE id=?", (name, division, short_name, federation_reeks, team_id))
                if cur.rowcount == 0:
                    return "Team not found", 404
            if season_id:
                db.execute("DELETE FROM club_team_players WHERE team_id=? AND season_id=?", (team_id, season_id))
            else:
                db.execute("DELETE FROM club_team_players WHERE team_id=? AND season_id IS NULL", (team_id,))
            pid_list   = request.form.getlist("player_profile_id")
            roles_list = request.form.getlist("player_roles")
            pid_roles  = {}
            for i, pid_str in enumerate(pid_list):
                pid_str = pid_str.strip()
                if pid_str and int(pid_str) not in pid_roles:
                    r = roles_list[i].strip() if i < len(roles_list) else ""
                    pid_roles[int(pid_str)] = r or "player"
            for pid in _collect_profile_ids(pid_list):
                roles   = pid_roles[pid]
                profile = db.execute(
                    "SELECT first_name, last_name, number FROM player_profiles WHERE id=?",
                    (pid,)
                ).fetchone()
                if profile:
                    pname = (profile["first_name"] + " " + profile["last_name"]).strip().lower()
                    db.execute(
                        "INSERT OR IGNORE INTO club_team_players (team_id, name, number, profile_id, season_id, roles) VALUES (?,?,?,?,?,?)",
                        (team_id, pname, profile["number"] or "", pid, season_id, roles)
                    )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            return render_template("team_form.html", team=team, players=players, all_profiles=all_profiles,
                                   seasons=seasons, selected_season_id=selected_season_id, error="A team with this short name already exists.",
                                   season_info=season_info, team_member_roles=TEAM_MEMBER_ROLES)
        except Exception:
            db.rollback()
            raise
        return redirect(url_for("team_list"))
    selected_season_id = request.args.get("season_id", type=int)
    season_info = None
    if selected_season_id:
        season_info = db.execute(
            "SELECT short_name, division FROM club_team_season_info WHERE team_id=? AND season_id=?",
            (team_id, selected_season_id)
        ).fetchone()
    if selected_season_id:
        players = db.execute(
            "SELECT * FROM club_team_players WHERE team_id=? AND season_id=? ORDER BY name COLLATE NOCASE",
            (team_id, selected_season_id)
        ).fetchall()
    else:
        players = db.execute(
            "SELECT * FROM club_team_players WHERE team_id=? AND season_id IS NULL ORDER BY name COLLATE NOCASE",
            (team_id,)
        ).fetchall()
    return render_template("team_form.html", team=team, players=players, all_profiles=all_profiles,
                           seasons=seasons, selected_season_id=selected_season_id,
                           season_info=season_info, team_member_roles=TEAM_MEMBER_ROLES)


@app.route("/teams/<int:team_id>/delete", methods=["POST"])
@login_required
def delete_team(team_id):
    if not can_view_all():
        return "Forbidden", 403
    db = get_db()
    if not db.execute("SELECT id FROM club_teams WHERE id=?", (team_id,)).fetchone():
        return "Team not found", 404
    try:
        db.execute("DELETE FROM club_team_players WHERE team_id=?", (team_id,))
        db.execute("DELETE FROM club_team_trainers WHERE team_id=?", (team_id,))
        db.execute("DELETE FROM club_teams WHERE id=?", (team_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return redirect(url_for("team_list"))


@app.route("/api/teams/<int:team_id>/players")
@login_required
def api_team_players(team_id):
    db = get_db()
    tcond, tparams = _team_cond()
    if not db.execute(f"SELECT id FROM club_teams WHERE id=?{tcond}", [team_id] + tparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    season_id = request.args.get("season_id", type=int)
    if season_id is not None:
        players = db.execute(
            "SELECT name, number, profile_id FROM club_team_players WHERE team_id=? AND season_id=? ORDER BY name COLLATE NOCASE",
            (team_id, season_id)
        ).fetchall()
    else:
        players = db.execute(
            "SELECT name, number, profile_id FROM club_team_players WHERE team_id=? AND season_id IS NULL ORDER BY name COLLATE NOCASE",
            (team_id,)
        ).fetchall()
    return jsonify([dict(p) for p in players])


@app.route("/api/seasons", methods=["POST"])
@csrf.exempt
@login_required
def api_create_season():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    if not re.match(r'^S\d{2}-\d{2}$', name):
        return jsonify({"error": "Season name must match format Sxx-yy (e.g. S25-26)"}), 400
    db = get_db()
    try:
        cur = db.execute("INSERT INTO seasons (user_id, name) VALUES (?,?)", (current_user.id, name))
        db.commit()
        return jsonify({"id": cur.lastrowid, "name": name}), 201
    except sqlite3.IntegrityError:
        existing = db.execute(
            "SELECT id FROM seasons WHERE user_id=? AND name=?", (current_user.id, name)
        ).fetchone()
        return jsonify({"id": existing["id"] if existing else None, "name": name}), 409


@app.route("/api/profiles/<int:profile_id>", methods=["PATCH"])
@csrf.exempt
@login_required
def api_patch_profile(profile_id):
    if not can_view_all():
        return jsonify({"error": "Forbidden"}), 403
    db = get_db()
    if not db.execute("SELECT id FROM player_profiles WHERE id=?", (profile_id,)).fetchone():
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    field = data.get("field", "")
    value = data.get("value")
    ALLOWED = {"number", "status", "date_of_birth"}
    if field not in ALLOWED:
        return jsonify({"error": "Field not editable"}), 400
    if field == "status":
        VALID_STATUSES = ("active", "inactive", "injured", "prospect", "trial", "unknown")
        if value not in VALID_STATUSES:
            return jsonify({"error": "Invalid status"}), 400
    elif field == "date_of_birth":
        if value:
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', str(value)):
                return jsonify({"error": "Invalid date format"}), 400
        else:
            value = None
    elif field == "number":
        value = str(value).strip() if value is not None else None
        if value == "":
            value = None
    now = datetime.now(UTC).isoformat()
    cur = db.execute(
        f"UPDATE player_profiles SET {field}=?, updated_at=? WHERE id=?",
        (value, now, profile_id)
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True, "field": field, "value": value})


# ── admin ─────────────────────────────────────────────────────────────────────

@app.route("/admin/users")
@login_required
def admin_users():
    if not can_view_all():
        return "Forbidden", 403
    db = get_db()
    users = db.execute(
        "SELECT id, email, role, created_at, profile_id FROM users ORDER BY email"
    ).fetchall()
    profiles = db.execute(
        "SELECT id, first_name, last_name FROM player_profiles ORDER BY last_name, first_name"
    ).fetchall()
    return render_template("admin_users.html", users=users, profiles=profiles)


@app.route("/admin/users/<int:user_id>/role", methods=["POST"])
@login_required
def admin_set_role(user_id):
    if not is_admin():
        return "Forbidden", 403
    new_role = request.form.get("role")
    if new_role not in ("trainer", "coordinator", "admin"):
        return "Invalid role", 400
    db = get_db()
    cur = db.execute("UPDATE users SET role=? WHERE id=?", (new_role, user_id))
    db.commit()
    if cur.rowcount == 0:
        return "User not found", 404
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
def admin_delete_user(user_id):
    if not is_admin():
        return "Forbidden", 403
    if user_id == current_user.id:
        flash("U kunt uw eigen account niet verwijderen.", "error")
        return redirect(url_for("admin_users"))
    db = get_db()
    if not db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
        return "User not found", 404
    db.execute("DELETE FROM club_team_trainers WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    flash("Gebruiker verwijderd.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/profile", methods=["POST"])
@login_required
def admin_link_profile(user_id):
    if not can_view_all():
        return "Forbidden", 403
    db = get_db()
    if not db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
        return "User not found", 404
    profile_id_raw = request.form.get("profile_id", "").strip()
    profile_id = int(profile_id_raw) if profile_id_raw else None
    if profile_id is not None:
        if not db.execute("SELECT id FROM player_profiles WHERE id=?", (profile_id,)).fetchone():
            return "Profile not found", 404
    db.execute("UPDATE users SET profile_id=? WHERE id=?", (profile_id, user_id))
    db.commit()
    return redirect(url_for("admin_users"))


@app.route("/admin/backup/download")
@login_required
def admin_backup_download():
    if not is_admin():
        return "Forbidden", 403
    import tempfile
    today = datetime.now().strftime("%Y-%m-%d")
    tmp_path = tempfile.mktemp(suffix=".db")
    try:
        src = sqlite3.connect(DATABASE)
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(tmp_path, "rb") as f:
            data = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    resp = make_response(data)
    resp.headers["Content-Disposition"] = f'attachment; filename="stats_{today}.db"'
    resp.headers["Content-Type"] = "application/octet-stream"
    return resp


# ── Roster / Player Profiles ──────────────────────────────────────────────────

@app.route("/roster")
@login_required
def roster_list():
    db = get_db()
    q         = request.args.get("q", "").strip()
    status    = request.args.get("status", "")
    position  = request.args.get("position", "")
    tag       = request.args.get("tag", "").strip()
    team      = request.args.get("team", type=int)
    group     = request.args.get("group", type=int)
    season_id = request.args.get("season_id", type=int)
    role      = request.args.get("role", "").strip()

    sql    = "SELECT * FROM player_profiles WHERE 1=1"
    params = []
    if q:
        sql    += " AND (first_name || ' ' || last_name LIKE ?)"
        params += [f"%{q}%"]
    if status:
        sql    += " AND status=?"
        params += [status]
    if position:
        sql    += " AND (',' || positions || ',') LIKE ?"
        params += [f"%,{position},%"]
    if tag:
        sql    += ' AND tags LIKE ?'
        params += [f'%"{tag}"%']
    if team and season_id:
        sql    += " AND id IN (SELECT profile_id FROM club_team_players WHERE team_id=? AND season_id=? AND profile_id IS NOT NULL)"
        params += [team, season_id]
    elif team:
        sql    += " AND id IN (SELECT profile_id FROM club_team_players WHERE team_id=? AND profile_id IS NOT NULL)"
        params += [team]
    elif season_id:
        sql    += " AND id IN (SELECT profile_id FROM club_team_players WHERE season_id=? AND profile_id IS NOT NULL)"
        params += [season_id]
    if group:
        sql    += " AND id IN (SELECT player_id FROM training_group_players WHERE group_id=?)"
        params += [group]
    if role:
        sql    += " AND id IN (SELECT profile_id FROM club_team_players WHERE profile_id IS NOT NULL AND (',' || COALESCE(roles,'') || ',') LIKE ?)"
        params += [f"%,{role},%"]
    sql += " ORDER BY last_name, first_name"
    profiles = db.execute(sql, params).fetchall()

    # Attach teams to each profile (season-filtered when a season is active)
    teams_by_pid = {}
    if season_id:
        teams_rows = db.execute(
            "SELECT ctp.profile_id, ct.id, ct.name, "
            "COALESCE(ctsi.short_name, ct.short_name) AS short_name, ctp.roles "
            "FROM club_team_players ctp "
            "JOIN club_teams ct ON ct.id = ctp.team_id "
            "LEFT JOIN club_team_season_info ctsi ON ctsi.team_id = ctp.team_id AND ctsi.season_id = ctp.season_id "
            "WHERE ctp.profile_id IS NOT NULL AND ctp.season_id=?",
            (season_id,)
        ).fetchall()
    else:
        # Show each team once — latest season per (team, player)
        teams_rows = db.execute(
            "SELECT ctp.profile_id, ct.id, ct.name, "
            "COALESCE(ctsi.short_name, ct.short_name) AS short_name, ctp.roles "
            "FROM club_team_players ctp "
            "JOIN club_teams ct ON ct.id = ctp.team_id "
            "LEFT JOIN club_team_season_info ctsi ON ctsi.team_id = ctp.team_id AND ctsi.season_id = ctp.season_id "
            "WHERE ctp.profile_id IS NOT NULL "
            "AND (ctp.season_id IS NULL OR ctp.season_id = ("
            "  SELECT MAX(ctp2.season_id) FROM club_team_players ctp2 "
            "  WHERE ctp2.team_id = ctp.team_id AND ctp2.profile_id = ctp.profile_id"
            "))"
        ).fetchall()
    for row in teams_rows:
        teams_by_pid.setdefault(row["profile_id"], []).append({"id": row["id"], "name": row["name"], "short_name": row["short_name"], "roles": row["roles"]})

    ucond, uparams = _uid_cond()
    all_seasons = [dict(s) for s in db.execute(
        f"SELECT id, name FROM seasons WHERE 1=1{ucond} ORDER BY name DESC", uparams
    ).fetchall()]
    all_teams  = db.execute("SELECT id, name, short_name FROM club_teams ORDER BY COALESCE(NULLIF(short_name,''), name) COLLATE NOCASE").fetchall()
    all_groups = db.execute("SELECT id, name FROM training_groups ORDER BY name").fetchall()

    # Collect all distinct tags for the filter dropdown
    all_tags_set = set()
    for row in db.execute("SELECT tags FROM player_profiles WHERE tags IS NOT NULL").fetchall():
        try:
            for t in (json.loads(row["tags"]) or []):
                if t:
                    all_tags_set.add(t.strip())
        except Exception:
            pass
    all_tags = sorted(all_tags_set)

    return render_template("roster_list.html",
        profiles=profiles,
        teams_by_pid=teams_by_pid,
        teams=all_teams,
        groups=all_groups,
        seasons=all_seasons,
        all_tags=all_tags,
        q_filter=q,
        status_filter=status,
        position_filter=position,
        tag_filter=tag,
        team_filter=team,
        group_filter=group,
        season_filter=season_id,
        role_filter=role,
    )


@app.route("/roster/new", methods=["GET", "POST"])
@login_required
def roster_new():
    if not can_view_all():
        return "Forbidden", 403
    if request.method == "POST":
        first = request.form.get("first_name", "").strip()
        last  = request.form.get("last_name", "").strip()
        if not first or not last:
            return render_template("roster_form.html", profile=None, error="First and last name are required.")
        new_id = _save_profile(request.form)
        return redirect(url_for("roster_detail", profile_id=new_id))
    return render_template("roster_form.html", profile=None, error=None)


@app.route("/roster/import", methods=["GET", "POST"])
@login_required
def roster_import():
    if not can_view_all():
        return "Forbidden", 403
    if request.method == "POST":
        if "csv_file" in request.files and request.files["csv_file"].filename:
            # Step 1: parse CSV and store preview in session
            f = request.files["csv_file"]
            try:
                content = f.read().decode("utf-8-sig")
                reader  = csv.DictReader(io.StringIO(content))
                rows = []
                for row_dict in reader:
                    first = row_dict.get("first_name", "").strip()
                    last  = row_dict.get("last_name", "").strip()
                    if not first and not last:
                        continue
                    status = (row_dict.get("status", "") or "active").strip()
                    if status not in ("active", "prospect", "trial", "unknown", "inactive"):
                        status = "active"
                    tags_raw = (row_dict.get("tags", "") or "").strip()
                    tags = json.dumps([t.strip() for t in tags_raw.split(",") if t.strip()]) if tags_raw else None
                    rows.append({
                        "first_name":    first,
                        "last_name":     last,
                        "date_of_birth": (row_dict.get("date_of_birth", "") or "").strip() or None,
                        "number":        (row_dict.get("number", "") or "").strip() or None,
                        "status":        status,
                        "positions":     (row_dict.get("positions", "") or "").strip() or None,
                        "tags":          tags,
                        "federation_id": (row_dict.get("federation_id", "") or "").strip() or None,
                    })
            except Exception:
                return "CSV parse error — ensure file is UTF-8 encoded.", 400
            # Flag duplicates: existing DB names + within-file duplicates
            db = get_db()
            existing_norms = {
                r[0] for r in db.execute(
                    "SELECT lower(first_name) || ' ' || lower(last_name) FROM player_profiles"
                ).fetchall()
            }
            seen_in_file = set()
            for row in rows:
                norm = (row["first_name"] + " " + row["last_name"]).strip().lower()
                row["_norm"]      = norm
                row["_duplicate"] = norm in existing_norms or norm in seen_in_file
                seen_in_file.add(norm)
            session["import_preview"] = rows
            return redirect(url_for("roster_import"))
        if request.form.get("confirm_import") == "1":
            # Step 2: insert non-duplicate rows
            rows     = session.pop("import_preview", [])
            inserted = 0
            skipped  = 0
            if not rows:
                return redirect(url_for("roster_list"))
            db  = get_db()
            now = datetime.now(UTC).isoformat()
            try:
                for row in rows:
                    if row.get("_duplicate"):
                        skipped += 1
                        continue
                    db.execute(
                        "INSERT INTO player_profiles "
                        "(first_name, last_name, date_of_birth, number, status, positions, tags, "
                        " federation_id, created_by, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (row["first_name"], row["last_name"],
                         row.get("date_of_birth"), row.get("number"),
                         row.get("status", "active"),
                         row.get("positions"), row.get("tags"),
                         row.get("federation_id"),
                         current_user.id, now, now)
                    )
                    inserted += 1
                db.commit()
            except Exception:
                db.rollback()
                raise
            flash(f"Import voltooid: {inserted} ingevoegd, {skipped} overgeslagen (duplicaat).", "success")
            return redirect(url_for("roster_list"))
        return "Bad request", 400
    # GET: show preview if available, else upload form
    preview = session.get("import_preview")
    return render_template("roster_import.html", preview=preview)


@app.route("/roster/import-federation", methods=["GET", "POST"])
@login_required
def roster_import_federation():
    if not can_view_all():
        return "Forbidden", 403

    # Regex: optional K-prefix, jersey number, federation card #, last_name, first_name,
    # nationality (discarded), DOB DD/MM/YYYY
    # Last name may consist of multiple words (e.g. "Van Damme"); greedy .+ captures
    # everything up to the first name, then nationality ([A-Z]{2,3}) anchors the boundary.
    _LINE_RE = re.compile(
        r"^(K\s+)?(\d+)\s+(\d+)\s+(.+)\s+(\S+)\s+[A-Z]{2,3}\s+(\d{2}/\d{2}/\d{4})\s*$"
    )

    if request.method == "POST":
        if request.form.get("paste_text") is not None and not request.form.get("confirm_import"):
            # Step 1: parse pasted text → build preview
            raw = request.form.get("paste_text", "")
            rows = []
            for line in raw.splitlines():
                line = line.strip()
                m = _LINE_RE.match(line)
                if not m:
                    continue
                kern_flag, number, fed_id, last_name, first_name, dob_raw = m.groups()
                try:
                    dob = datetime.strptime(dob_raw, "%d/%m/%Y").strftime("%Y-%m-%d")
                except ValueError:
                    dob = None
                tag_list = ["kern"] if kern_flag else []
                tags = json.dumps(tag_list) if tag_list else None
                rows.append({
                    "first_name":    first_name,
                    "last_name":     last_name,
                    "date_of_birth": dob,
                    "number":        number,
                    "status":        "active",
                    "federation_id": fed_id,
                    "tags":          tags,
                })
            if not rows:
                return render_template("roster_import_federation.html", preview=None,
                                       error="No valid player lines found. Check the format.")
            db = get_db()
            existing_norms = {
                r[0] for r in db.execute(
                    "SELECT lower(first_name) || ' ' || lower(last_name) FROM player_profiles"
                ).fetchall()
            }
            seen_in_file = set()
            for row in rows:
                norm = (row["first_name"] + " " + row["last_name"]).strip().lower()
                row["_norm"]      = norm
                row["_duplicate"] = norm in existing_norms or norm in seen_in_file
                seen_in_file.add(norm)
            session["federation_preview"] = rows
            return redirect(url_for("roster_import_federation"))

        if request.form.get("confirm_import") == "1":
            # Step 2: insert non-duplicate rows
            rows     = session.pop("federation_preview", [])
            inserted = 0
            skipped  = 0
            if not rows:
                return redirect(url_for("roster_list"))
            db  = get_db()
            now = datetime.now(UTC).isoformat()
            try:
                for row in rows:
                    if row.get("_duplicate"):
                        skipped += 1
                        continue
                    db.execute(
                        "INSERT INTO player_profiles "
                        "(first_name, last_name, date_of_birth, number, status, tags, "
                        " federation_id, created_by, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (row["first_name"], row["last_name"],
                         row.get("date_of_birth"), row.get("number"),
                         row.get("status", "active"),
                         row.get("tags"), row.get("federation_id"),
                         current_user.id, now, now)
                    )
                    inserted += 1
                db.commit()
            except Exception:
                db.rollback()
                raise
            flash(f"Import voltooid: {inserted} ingevoegd, {skipped} overgeslagen (duplicaat).", "success")
            return redirect(url_for("roster_list"))
        return "Bad request", 400

    if request.args.get("reset"):
        session.pop("federation_preview", None)
    preview = session.get("federation_preview")
    return render_template("roster_import_federation.html", preview=preview, error=None)


@app.route("/roster/<int:profile_id>")
@login_required
def roster_detail(profile_id):
    db = get_db()
    profile = db.execute("SELECT * FROM player_profiles WHERE id=?", (profile_id,)).fetchone()
    if not profile:
        return "Profile not found", 404

    # Private remarks: trainers cannot see them
    if can_view_all():
        remarks_rows = db.execute(
            "SELECT pr.*, u.email AS author_email FROM player_remarks pr "
            "LEFT JOIN users u ON u.id = pr.created_by "
            "WHERE pr.player_id=? ORDER BY pr.created_at DESC",
            (profile_id,)
        ).fetchall()
    else:
        remarks_rows = db.execute(
            "SELECT pr.*, u.email AS author_email FROM player_remarks pr "
            "LEFT JOIN users u ON u.id = pr.created_by "
            "WHERE pr.player_id=? AND pr.is_private=0 "
            "  AND pr.remark_type IN ('general','training') "
            "ORDER BY pr.created_at DESC",
            (profile_id,)
        ).fetchall()

    # Can this user add a remark?
    if can_view_all():
        can_add = True
    else:
        assigned = db.execute(
            "SELECT 1 FROM club_team_trainers ctt "
            "JOIN club_team_players ctp ON ctp.team_id = ctt.team_id "
            "WHERE ctt.user_id=? AND ctp.profile_id=? LIMIT 1",
            (current_user.id, profile_id)
        ).fetchone()
        can_add = bool(assigned)

    # Current teams for this player — one row per team (latest season)
    current_teams = db.execute(
        "SELECT ct.id, ct.name, COALESCE(ctsi.short_name, ct.short_name) AS short_name, "
        "s.name AS season_name, ctp.roles "
        "FROM club_team_players ctp "
        "JOIN club_teams ct ON ct.id = ctp.team_id "
        "LEFT JOIN seasons s ON s.id = ctp.season_id "
        "LEFT JOIN club_team_season_info ctsi ON ctsi.team_id = ctp.team_id AND ctsi.season_id = ctp.season_id "
        "WHERE ctp.profile_id=? "
        "AND (ctp.season_id IS NULL OR ctp.season_id = ("
        "  SELECT MAX(ctp2.season_id) FROM club_team_players ctp2 "
        "  WHERE ctp2.team_id = ctp.team_id AND ctp2.profile_id = ctp.profile_id"
        ")) "
        "ORDER BY ct.name COLLATE NOCASE",
        (profile_id,)
    ).fetchall()

    # Coaching badge: users whose profile_id = this player, with their teams
    coaching_users = db.execute(
        "SELECT id FROM users WHERE profile_id=?", (profile_id,)
    ).fetchall()
    coaching_teams = []
    for cu in coaching_users:
        for team_row in db.execute(
            "SELECT ct.name FROM club_team_trainers ctt "
            "JOIN club_teams ct ON ct.id = ctt.team_id "
            "WHERE ctt.user_id=?", (cu["id"],)
        ).fetchall():
            coaching_teams.append(team_row)

    # Kit items and log for this profile (only loaded when kit access is available)
    kit_items_profile = []
    kit_log_profile = []
    if can_manage_kit() or is_kit_manager():
        kit_items_profile = db.execute(
            "SELECT ki.*, ct.name AS team_name FROM kit_items ki "
            "LEFT JOIN club_teams ct ON ct.id = ki.team_id "
            "WHERE ki.profile_id=? AND ki.is_deleted=0 ORDER BY ki.created_at DESC",
            (profile_id,)
        ).fetchall()
        kit_log_profile = db.execute(
            "SELECT kl.*, "
            "ki.model || ' ' || ki.type || ' #' || COALESCE(ki.number, CAST(ki.id AS TEXT)) AS item_label, "
            "u.email AS created_by_email "
            "FROM kit_log kl "
            "JOIN kit_items ki ON ki.id = kl.item_id "
            "LEFT JOIN users u ON u.id = kl.created_by "
            "WHERE kl.profile_id=? ORDER BY kl.created_at DESC",
            (profile_id,)
        ).fetchall()

    return render_template("roster_detail.html",
        profile=profile,
        remarks=remarks_rows,
        can_add_remark=can_add,
        current_teams=current_teams,
        coaching_teams=coaching_teams,
        kit_items_profile=kit_items_profile,
        kit_log_profile=kit_log_profile,
    )


@app.route("/roster/<int:profile_id>/edit", methods=["GET", "POST"])
@login_required
def roster_edit(profile_id):
    if not can_view_all():
        return "Forbidden", 403
    db = get_db()
    profile = db.execute("SELECT * FROM player_profiles WHERE id=?", (profile_id,)).fetchone()
    if not profile:
        return "Profile not found", 404
    if request.method == "POST":
        first = request.form.get("first_name", "").strip()
        last  = request.form.get("last_name", "").strip()
        if not first or not last:
            return render_template("roster_form.html", profile=profile, error="First and last name are required.")
        saved_id = _save_profile(request.form, profile_id=profile_id)
        if saved_id is None:
            return "Profile not found", 404
        return redirect(url_for("roster_detail", profile_id=profile_id))
    return render_template("roster_form.html", profile=profile, error=None)


@app.route("/roster/<int:profile_id>/delete", methods=["POST"])
@login_required
def roster_delete(profile_id):
    if not can_view_all():
        return "Forbidden", 403
    db = get_db()
    if not db.execute("SELECT id FROM player_profiles WHERE id=?", (profile_id,)).fetchone():
        return "Profile not found", 404
    try:
        db.execute("DELETE FROM player_remarks WHERE player_id=?", (profile_id,))
        db.execute("DELETE FROM training_group_players WHERE player_id=?", (profile_id,))
        db.execute("UPDATE players SET profile_id=NULL WHERE profile_id=?", (profile_id,))
        db.execute("UPDATE club_team_players SET profile_id=NULL WHERE profile_id=?", (profile_id,))
        db.execute("UPDATE users SET profile_id=NULL WHERE profile_id=?", (profile_id,))
        db.execute("DELETE FROM player_profiles WHERE id=?", (profile_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return redirect(url_for("roster_list"))


@app.route("/roster/<int:profile_id>/remarks", methods=["POST"])
@login_required
def roster_add_remark(profile_id):
    db = get_db()
    if not db.execute("SELECT id FROM player_profiles WHERE id=?", (profile_id,)).fetchone():
        return "Profile not found", 404
    remark_type = request.form.get("remark_type", "")
    content     = request.form.get("content", "").strip()
    due_date    = request.form.get("due_date", "").strip() or None
    is_private  = 1 if request.form.get("is_private") else 0
    ALLOWED_TYPES = ("general", "scouting", "injury", "training", "followup")
    if remark_type not in ALLOWED_TYPES:
        return "Invalid remark type", 400
    if not content:
        return redirect(url_for("roster_detail", profile_id=profile_id))
    if not can_view_all():
        # Trainers: general/training only, and only for players on their assigned team(s)
        if remark_type not in ("general", "training"):
            return "Forbidden", 403
        assigned = db.execute(
            "SELECT 1 FROM club_team_trainers ctt "
            "JOIN club_team_players ctp ON ctp.team_id = ctt.team_id "
            "WHERE ctt.user_id=? AND ctp.profile_id=? LIMIT 1",
            (current_user.id, profile_id)
        ).fetchone()
        if not assigned:
            return "Forbidden", 403
        is_private = 0
    db.execute(
        "INSERT INTO player_remarks "
        "(player_id, remark_type, content, due_date, is_private, created_by, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (profile_id, remark_type, content, due_date, is_private,
         current_user.id, datetime.now(UTC).isoformat())
    )
    db.commit()
    return redirect(url_for("roster_detail", profile_id=profile_id))


@app.route("/roster/<int:profile_id>/remarks/<int:remark_id>/delete", methods=["POST"])
@login_required
def roster_delete_remark(profile_id, remark_id):
    if not can_view_all():
        return "Forbidden", 403
    db = get_db()
    cur = db.execute(
        "DELETE FROM player_remarks WHERE id=? AND player_id=?", (remark_id, profile_id)
    )
    db.commit()
    if cur.rowcount == 0:
        return "Remark not found", 404
    return redirect(url_for("roster_detail", profile_id=profile_id))


@app.route("/api/roster/search")
@login_required
def api_roster_search():
    q  = request.args.get("q", "").strip()
    db = get_db()
    if q:
        norm_q = f"%{q.lower()}%"
        rows = db.execute(
            "SELECT id, first_name, last_name, number FROM player_profiles "
            "WHERE lower(first_name || ' ' || last_name) LIKE ? "
            "   OR lower(last_name  || ' ' || first_name) LIKE ? "
            "ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE LIMIT 20",
            (norm_q, norm_q)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, first_name, last_name, number FROM player_profiles "
            "ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE LIMIT 20"
        ).fetchall()
    result = []
    for r in rows:
        teams = db.execute(
            "SELECT ct.name FROM club_teams ct "
            "JOIN club_team_players ctp ON ctp.team_id = ct.id "
            "WHERE ctp.profile_id=?",
            (r["id"],)
        ).fetchall()
        result.append({
            "id":           r["id"],
            "display_name": r["first_name"] + " " + r["last_name"],
            "number":       r["number"],
            "teams":        [t["name"] for t in teams],
        })
    return jsonify(result)


# ── Training Groups ───────────────────────────────────────────────────────────

@app.route("/training-groups")
@login_required
def training_group_list():
    if not can_view_all():
        return "Forbidden", 403
    db = get_db()
    groups = db.execute(
        "SELECT tg.*, COUNT(tgp.player_id) AS player_count "
        "FROM training_groups tg "
        "LEFT JOIN training_group_players tgp ON tgp.group_id = tg.id "
        "GROUP BY tg.id ORDER BY tg.name"
    ).fetchall()
    return render_template("training_groups.html", groups=groups)


@app.route("/training-groups/new", methods=["GET", "POST"])
@login_required
def training_group_new():
    if not can_view_all():
        return "Forbidden", 403
    if request.method == "POST":
        name        = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip() or None
        if not name:
            return render_template("training_group_form.html", error="Name is required.")
        db = get_db()
        try:
            db.execute(
                "INSERT INTO training_groups (name, description, created_by, created_at) "
                "VALUES (?,?,?,?)",
                (name, description, current_user.id, datetime.now(UTC).isoformat())
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            return render_template("training_group_form.html", error="A group with that name already exists.")
        return redirect(url_for("training_group_list"))
    return render_template("training_group_form.html", error=None)


@app.route("/training-groups/<int:group_id>", methods=["GET", "POST"])
@login_required
def training_group_detail(group_id):
    if not can_view_all():
        return "Forbidden", 403
    db = get_db()
    group = db.execute("SELECT * FROM training_groups WHERE id=?", (group_id,)).fetchone()
    if not group:
        return "Group not found", 404
    all_profiles = [dict(r) for r in db.execute(
        "SELECT id, first_name, last_name, number FROM player_profiles "
        "WHERE status='active' ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE"
    ).fetchall()]
    if request.method == "POST":
        group_name = request.form.get("group_name", "").strip()
        description = request.form.get("description", "").strip()
        if not group_name:
            players = db.execute(
                "SELECT pp.* FROM training_group_players tgp "
                "JOIN player_profiles pp ON pp.id = tgp.player_id "
                "WHERE tgp.group_id=? ORDER BY pp.last_name, pp.first_name",
                (group_id,)
            ).fetchall()
            player_rows = [dict(p) | {"profile_id": p["id"]} for p in players]
            return render_template("training_group_detail.html", group=group, players=player_rows,
                                   all_profiles=all_profiles, error="Group name is required.")
        seen = set()
        new_ids = []
        for pid_str in request.form.getlist("player_profile_id"):
            pid_str = pid_str.strip()
            if not pid_str:
                continue
            pid = int(pid_str)
            if pid not in seen:
                seen.add(pid)
                new_ids.append(pid)
        try:
            db.execute(
                "UPDATE training_groups SET name=?, description=? WHERE id=?",
                (group_name, description or None, group_id)
            )
            db.execute("DELETE FROM training_group_players WHERE group_id=?", (group_id,))
            for pid in new_ids:
                db.execute(
                    "INSERT OR IGNORE INTO training_group_players (group_id, player_id) VALUES (?,?)",
                    (group_id, pid)
                )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            players = db.execute(
                "SELECT pp.* FROM training_group_players tgp "
                "JOIN player_profiles pp ON pp.id = tgp.player_id "
                "WHERE tgp.group_id=? ORDER BY pp.last_name, pp.first_name",
                (group_id,)
            ).fetchall()
            player_rows = [dict(p) | {"profile_id": p["id"]} for p in players]
            return render_template("training_group_detail.html", group=group, players=player_rows,
                                   all_profiles=all_profiles, error="A group with this name already exists.")
        except Exception:
            db.rollback()
            raise
        return redirect(url_for("training_group_detail", group_id=group_id))
    players = db.execute(
        "SELECT pp.* FROM training_group_players tgp "
        "JOIN player_profiles pp ON pp.id = tgp.player_id "
        "WHERE tgp.group_id=? ORDER BY pp.last_name, pp.first_name",
        (group_id,)
    ).fetchall()
    # give each row a profile_id field so the player_roster_section macro can pre-select it
    player_rows = [dict(p) | {"profile_id": p["id"]} for p in players]
    return render_template("training_group_detail.html", group=group, players=player_rows,
                           all_profiles=all_profiles)


@app.route("/training-groups/<int:group_id>/delete", methods=["POST"])
@login_required
def training_group_delete(group_id):
    if not can_view_all():
        return "Forbidden", 403
    db = get_db()
    if not db.execute("SELECT id FROM training_groups WHERE id=?", (group_id,)).fetchone():
        return "Group not found", 404
    try:
        db.execute("DELETE FROM training_group_players WHERE group_id=?", (group_id,))
        db.execute("DELETE FROM training_groups WHERE id=?", (group_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return redirect(url_for("training_group_list"))


# ── Team Trainer Management ───────────────────────────────────────────────────

@app.route("/teams/<int:team_id>/trainers/add", methods=["POST"])
@login_required
def team_add_trainer(team_id):
    if not can_view_all():
        return "Forbidden", 403
    db = get_db()
    if not db.execute("SELECT id FROM club_teams WHERE id=?", (team_id,)).fetchone():
        return "Team not found", 404
    trainer_email = request.form.get("trainer_email", "").strip().lower()
    if not trainer_email:
        return "trainer_email required", 400
    row = db.execute("SELECT id FROM users WHERE LOWER(email)=?", (trainer_email,)).fetchone()
    if not row:
        return "User not found", 404
    user_id = row["id"]
    try:
        db.execute(
            "INSERT INTO club_team_trainers (team_id, user_id) VALUES (?,?)",
            (team_id, user_id)
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()  # already assigned — idempotent
    return redirect(url_for("team_list"))


@app.route("/teams/<int:team_id>/trainers/<int:user_id>/remove", methods=["POST"])
@login_required
def team_remove_trainer(team_id, user_id):
    if not can_view_all():
        return "Forbidden", 403
    db = get_db()
    cur = db.execute(
        "DELETE FROM club_team_trainers WHERE team_id=? AND user_id=?",
        (team_id, user_id)
    )
    db.commit()
    if cur.rowcount == 0:
        return "Not found", 404
    return redirect(url_for("team_list"))


# ── Kit / Uniform Inventory ───────────────────────────────────────────────────

@app.route("/kit")
@login_required
def kit_list():
    require_kit_access()
    db  = get_db()
    q          = request.args.get("q", "").strip()
    f_status   = request.args.get("status", "")
    f_model    = request.args.get("model", "")
    f_type     = request.args.get("type", "")
    f_team_id  = request.args.get("team_id", "")
    f_profile  = request.args.get("profile_id", "")
    f_store    = request.args.get("store", "")
    f_remark   = request.args.get("remark", "").strip()

    where  = "WHERE ki.is_deleted = 0"
    params = []
    if q:
        where += (
            " AND (ki.name_printed LIKE ? OR ki.model LIKE ? OR ki.type LIKE ? OR ki.number LIKE ?"
            " OR (pp.first_name || ' ' || pp.last_name) LIKE ?)"
        )
        like = "%" + q + "%"
        params += [like, like, like, like, like]
    if f_status:
        where += " AND ki.status = ?"
        params.append(f_status)
    if f_model:
        where += " AND ki.model = ?"
        params.append(f_model)
    if f_type:
        where += " AND ki.type = ?"
        params.append(f_type)
    if f_team_id:
        where += (
            " AND (ki.team_id = ?"
            " OR (ki.team_id IS NULL AND ki.profile_id IN"
            "  (SELECT profile_id FROM club_team_players"
            "   WHERE team_id = ? AND profile_id IS NOT NULL)))"
        )
        params += [f_team_id, f_team_id]
    if f_profile:
        where += " AND ki.profile_id = ?"
        params.append(f_profile)
    if f_store:
        where += " AND ki.store = ?"
        params.append(f_store)
    if f_remark:
        where += (
            " AND EXISTS (SELECT 1 FROM kit_log kl WHERE kl.item_id = ki.id"
            " AND kl.action = 'remark' AND kl.note LIKE ?)"
        )
        params.append("%" + f_remark + "%")

    items = db.execute(
        "SELECT ki.*, "
        "pp.first_name || ' ' || pp.last_name AS member_name, "
        "COALESCE(ct.short_name, ct.name, dct.short_name, dct.name) AS team_name, "
        "lr.note AS last_remark "
        "FROM kit_items ki "
        "LEFT JOIN player_profiles pp ON pp.id = ki.profile_id "
        "LEFT JOIN club_teams ct ON ct.id = ki.team_id "
        "LEFT JOIN ("
        "  SELECT ctp.profile_id, c2.name, c2.short_name "
        "  FROM club_team_players ctp "
        "  JOIN club_teams c2 ON c2.id = ctp.team_id "
        "  WHERE ctp.profile_id IS NOT NULL "
        "  AND ctp.id = (SELECT MAX(ctp2.id) FROM club_team_players ctp2 "
        "               WHERE ctp2.profile_id = ctp.profile_id) "
        ") dct ON dct.profile_id = ki.profile_id "
        "LEFT JOIN ("
        "  SELECT item_id, note FROM kit_log"
        "  WHERE action = 'remark' AND note IS NOT NULL"
        "  AND id = (SELECT MAX(kl2.id) FROM kit_log kl2"
        "            WHERE kl2.item_id = kit_log.item_id"
        "            AND kl2.action = 'remark' AND kl2.note IS NOT NULL)"
        ") lr ON lr.item_id = ki.id "
        f"{where} ORDER BY ki.created_at DESC",
        params
    ).fetchall()

    all_teams    = db.execute("SELECT id, name, short_name FROM club_teams ORDER BY COALESCE(NULLIF(short_name,''), name) COLLATE NOCASE").fetchall()
    all_profiles = db.execute(
        "SELECT id, first_name, last_name FROM player_profiles "
        "WHERE status != 'inactive' ORDER BY last_name, first_name"
    ).fetchall()
    all_stores = [r[0] for r in db.execute(
        "SELECT DISTINCT store FROM kit_items WHERE is_deleted=0 AND store IS NOT NULL ORDER BY store"
    ).fetchall()]

    return render_template(
        "kit_list.html",
        items=items,
        all_teams=all_teams,
        all_profiles=all_profiles,
        all_stores=all_stores,
        q=q,
        f_status=f_status,
        f_model=f_model,
        f_type=f_type,
        f_team_id=f_team_id,
        f_profile=f_profile,
        f_store=f_store,
        f_remark=f_remark,
        KIT_MODELS=KIT_MODELS,
        KIT_TYPES=KIT_TYPES,
        KIT_STATUSES=KIT_STATUSES,
    )


@app.route("/kit/new", methods=["GET", "POST"])
@login_required
def kit_new():
    require_kit_access()
    db = get_db()
    if request.method == "POST":
        model        = request.form.get("model", "").strip()
        kit_type     = request.form.get("type", "").strip()
        size         = request.form.get("size", "").strip()
        number       = request.form.get("number", "").strip() or None
        name_printed = request.form.get("name_printed", "").strip() or None
        status       = request.form.get("status", "in stock").strip()
        state        = request.form.get("state", "new").strip()
        store        = request.form.get("store", "").strip() or None
        profile_id   = request.form.get("profile_id", "").strip() or None
        date_added_raw = request.form.get("date_added", "").strip() or None
        date_added = None
        if date_added_raw:
            try:
                date_added = datetime.strptime(date_added_raw, "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                date_added = None
        now          = datetime.now(UTC).isoformat()

        if model not in KIT_MODELS:
            model = KIT_MODELS[0]
        if kit_type not in KIT_TYPES:
            kit_type = KIT_TYPES[0]
        if size not in KIT_SIZES:
            size = KIT_SIZES[0]
        if status not in KIT_STATUSES:
            status = KIT_STATUSES[0]
        if state not in KIT_STATES:
            state = KIT_STATES[0]

        team_id = None
        if profile_id:
            t_row = db.execute(
                "SELECT team_id FROM club_team_players WHERE profile_id=? LIMIT 1",
                (profile_id,)
            ).fetchone()
            if t_row:
                team_id = t_row["team_id"]

        cur = db.execute(
            "INSERT INTO kit_items "
            "(model, type, size, number, name_printed, status, state, store, "
            " profile_id, team_id, date_added, is_deleted, created_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
            (model, kit_type, size, number, name_printed, status, state, store,
             profile_id, team_id, date_added, current_user.id, now)
        )
        item_id = cur.lastrowid
        db.execute(
            "INSERT INTO kit_log (item_id, action, profile_id, team_id, note, created_by, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (item_id, 'created', profile_id, team_id, None, current_user.id, now)
        )
        db.commit()
        flash("Materiaalitem aangemaakt.", "success")
        return redirect(url_for("kit_list"))

    all_profiles = db.execute(
        "SELECT id, first_name, last_name FROM player_profiles "
        "WHERE status != 'inactive' ORDER BY last_name, first_name"
    ).fetchall()
    return render_template(
        "kit_form.html",
        item=None,
        all_profiles=all_profiles,
        KIT_MODELS=KIT_MODELS,
        KIT_TYPES=KIT_TYPES,
        KIT_SIZES=KIT_SIZES,
        KIT_STATUSES=KIT_STATUSES,
        KIT_STATES=KIT_STATES,
    )


@app.route("/kit/<int:item_id>")
@login_required
def kit_detail(item_id):
    require_kit_access()
    db = get_db()
    item = db.execute(
        "SELECT ki.*, "
        "pp.first_name || ' ' || pp.last_name AS member_name, "
        "ct.name AS team_name "
        "FROM kit_items ki "
        "LEFT JOIN player_profiles pp ON pp.id = ki.profile_id "
        "LEFT JOIN club_teams ct ON ct.id = ki.team_id "
        "WHERE ki.id = ?",
        (item_id,)
    ).fetchone()
    if not item:
        return "Item not found", 404
    log_entries = db.execute(
        "SELECT kl.*, "
        "pp.first_name || ' ' || pp.last_name AS member_name, "
        "u.email AS created_by_email "
        "FROM kit_log kl "
        "LEFT JOIN player_profiles pp ON pp.id = kl.profile_id "
        "LEFT JOIN users u ON u.id = kl.created_by "
        "WHERE kl.item_id = ? ORDER BY kl.created_at DESC",
        (item_id,)
    ).fetchall()
    return render_template("kit_detail.html", item=item, log_entries=log_entries)


@app.route("/kit/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def kit_edit(item_id):
    require_kit_access()
    db = get_db()
    item = db.execute("SELECT * FROM kit_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        return "Item not found", 404

    if request.method == "POST":
        model        = request.form.get("model", "").strip()
        kit_type     = request.form.get("type", "").strip()
        size         = request.form.get("size", "").strip()
        number       = request.form.get("number", "").strip() or None
        name_printed = request.form.get("name_printed", "").strip() or None
        status       = request.form.get("status", "in stock").strip()
        state        = request.form.get("state", "new").strip()
        store        = request.form.get("store", "").strip() or None
        profile_id   = request.form.get("profile_id", "").strip() or None
        date_added_raw = request.form.get("date_added", "").strip() or None
        date_added = None
        if date_added_raw:
            try:
                date_added = datetime.strptime(date_added_raw, "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                date_added = None
        now          = datetime.now(UTC).isoformat()

        if model not in KIT_MODELS:
            model = item["model"]
        if kit_type not in KIT_TYPES:
            kit_type = item["type"]
        if size not in KIT_SIZES:
            size = item["size"]
        if status not in KIT_STATUSES:
            status = item["status"]
        if state not in KIT_STATES:
            state = item["state"]

        team_id = None
        if profile_id:
            t_row = db.execute(
                "SELECT team_id FROM club_team_players WHERE profile_id=? LIMIT 1",
                (profile_id,)
            ).fetchone()
            if t_row:
                team_id = t_row["team_id"]

        old_profile = str(item["profile_id"]) if item["profile_id"] else None
        old_status  = item["status"]
        new_profile = str(profile_id) if profile_id else None

        db.execute(
            "UPDATE kit_items SET model=?, type=?, size=?, number=?, name_printed=?, "
            "status=?, state=?, store=?, profile_id=?, team_id=?, date_added=? "
            "WHERE id=?",
            (model, kit_type, size, number, name_printed, status, state, store,
             profile_id, team_id, date_added, item_id)
        )

        # Log assignment change
        if new_profile != old_profile:
            if new_profile:
                db.execute(
                    "INSERT INTO kit_log (item_id, action, profile_id, team_id, note, created_by, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (item_id, 'assigned', profile_id, team_id, None, current_user.id, now)
                )
            else:
                db.execute(
                    "INSERT INTO kit_log (item_id, action, profile_id, team_id, note, created_by, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (item_id, 'unassigned', old_profile, team_id, None, current_user.id, now)
                )
        # Log status change
        if status != old_status:
            db.execute(
                "INSERT INTO kit_log (item_id, action, profile_id, team_id, note, created_by, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (item_id, 'remark', profile_id, team_id,
                 f"Status changed from '{old_status}' to '{status}'", current_user.id, now)
            )

        db.commit()
        flash("Materiaalitem bijgewerkt.", "success")
        return redirect(url_for("kit_detail", item_id=item_id))

    all_profiles = db.execute(
        "SELECT id, first_name, last_name FROM player_profiles "
        "WHERE status != 'inactive' ORDER BY last_name, first_name"
    ).fetchall()
    return render_template(
        "kit_form.html",
        item=item,
        all_profiles=all_profiles,
        KIT_MODELS=KIT_MODELS,
        KIT_TYPES=KIT_TYPES,
        KIT_SIZES=KIT_SIZES,
        KIT_STATUSES=KIT_STATUSES,
        KIT_STATES=KIT_STATES,
    )


@app.route("/kit/<int:item_id>/log", methods=["POST"])
@login_required
def kit_add_log(item_id):
    require_kit_access()
    db = get_db()
    if not db.execute("SELECT id FROM kit_items WHERE id=? AND is_deleted=0", (item_id,)).fetchone():
        return "Item not found", 404
    note = request.form.get("note", "").strip()
    if not note:
        flash("Opmerking mag niet leeg zijn.", "error")
        return redirect(url_for("kit_detail", item_id=item_id))
    now = datetime.now(UTC).isoformat()
    db.execute(
        "INSERT INTO kit_log (item_id, action, profile_id, team_id, note, created_by, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (item_id, 'remark', None, None, note, current_user.id, now)
    )
    db.commit()
    return redirect(url_for("kit_detail", item_id=item_id))


@app.route("/kit/log")
@login_required
def kit_log_page():
    require_kit_access()
    db = get_db()
    f_kit_type  = request.args.get("kit_type", "").strip()
    f_team      = request.args.get("team_id", "").strip()
    f_profile   = request.args.get("profile_id", "").strip()
    f_action    = request.args.get("action", "").strip()
    f_date_from = request.args.get("date_from", "").strip()
    f_date_to   = request.args.get("date_to", "").strip()

    where  = "WHERE 1=1"
    params = []
    if f_kit_type:
        where += " AND ki.type = ?"
        params.append(f_kit_type)
    if f_team:
        where += " AND kl.team_id = ?"
        params.append(f_team)
    if f_profile:
        where += " AND kl.profile_id = ?"
        params.append(f_profile)
    if f_action:
        where += " AND kl.action = ?"
        params.append(f_action)
    if f_date_from:
        where += " AND kl.created_at >= ?"
        params.append(f_date_from)
    if f_date_to:
        where += " AND kl.created_at < ?"
        params.append(f_date_to + "T23:59:59")

    entries = db.execute(
        "SELECT kl.*, "
        "ki.model || ' ' || ki.type || COALESCE(' #' || ki.number, '') AS item_label, "
        "pp.first_name || ' ' || pp.last_name AS member_name, "
        "ct.name AS team_name, "
        "u.email AS created_by_email "
        "FROM kit_log kl "
        "JOIN kit_items ki ON ki.id = kl.item_id "
        "LEFT JOIN player_profiles pp ON pp.id = kl.profile_id "
        "LEFT JOIN club_teams ct ON ct.id = kl.team_id "
        "LEFT JOIN users u ON u.id = kl.created_by "
        f"{where} ORDER BY kl.created_at DESC",
        params
    ).fetchall()

    all_profiles = db.execute(
        "SELECT id, first_name, last_name FROM player_profiles ORDER BY last_name, first_name"
    ).fetchall()
    all_teams = db.execute(
        "SELECT id, name FROM club_teams ORDER BY name COLLATE NOCASE"
    ).fetchall()
    KIT_ACTIONS = ['created', 'assigned', 'unassigned', 'remark', 'deleted']

    return render_template(
        "kit_log.html",
        entries=entries,
        all_profiles=all_profiles,
        all_teams=all_teams,
        KIT_ACTIONS=KIT_ACTIONS,
        KIT_TYPES=KIT_TYPES,
        f_kit_type=f_kit_type,
        f_team=f_team,
        f_profile=f_profile,
        f_action=f_action,
        f_date_from=f_date_from,
        f_date_to=f_date_to,
    )


@app.route("/kit/bulk-delete", methods=["POST"])
@login_required
def kit_bulk_delete():
    require_kit_access()
    db = get_db()
    item_ids = request.form.getlist("item_ids")
    if not item_ids:
        flash("Geen items geselecteerd.", "error")
        return redirect(url_for("kit_list"))
    now = datetime.now(UTC).isoformat()
    count = 0
    try:
        for raw_id in item_ids:
            try:
                iid = int(raw_id)
            except ValueError:
                continue
            cur = db.execute(
                "UPDATE kit_items SET is_deleted=1, date_removed=? WHERE id=? AND is_deleted=0",
                (now[:10], iid)
            )
            if cur.rowcount:
                db.execute(
                    "INSERT INTO kit_log (item_id, action, profile_id, team_id, note, created_by, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (iid, 'deleted', None, None, None, current_user.id, now)
                )
                count += 1
        db.commit()
    except Exception as exc:
        db.rollback()
        flash(f"Bulkverwijdering mislukt: {exc}", "error")
        return redirect(url_for("kit_list"))
    flash(f"{count} item(s) verwijderd.", "success")
    return redirect(url_for("kit_list"))


@app.route("/kit/export")
@login_required
def kit_export():
    require_kit_access()
    db = get_db()
    rows = db.execute(
        "SELECT ki.id, ki.model, ki.type, ki.size, ki.number, ki.name_printed, "
        "ki.status, ki.state, ki.store, "
        "pp.first_name AS member_first_name, pp.last_name AS member_last_name, "
        "ct.name AS team_name, ki.date_added, ki.date_removed "
        "FROM kit_items ki "
        "LEFT JOIN player_profiles pp ON pp.id = ki.profile_id "
        "LEFT JOIN club_teams ct ON ct.id = ki.team_id "
        "WHERE ki.is_deleted = 0 ORDER BY ki.id"
    ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['id', 'model', 'type', 'size', 'number', 'name_printed',
                     'status', 'state', 'store', 'member_first_name', 'member_last_name',
                     'team_name', 'date_added', 'date_removed', 'remark'])
    for r in rows:
        writer.writerow([
            r['id'], r['model'], r['type'], r['size'],
            r['number'] or '', r['name_printed'] or '',
            r['status'], r['state'], r['store'] or '',
            r['member_first_name'] or '', r['member_last_name'] or '',
            r['team_name'] or '', r['date_added'] or '', r['date_removed'] or '', ''
        ])
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename="kit_export.csv"'
    return response


@app.route("/kit/import", methods=["GET", "POST"])
@login_required
def kit_import_form():
    require_kit_access()
    if request.method == "GET":
        return render_template("kit_import.html", preview=None)

    f = request.files.get("csv_file")
    if not f or not f.filename:
        flash("Selecteer een CSV-bestand om te uploaden.", "error")
        return render_template("kit_import.html", preview=None)

    try:
        content = f.read().decode('utf-8-sig')
    except Exception:
        flash("Bestand kon niet worden gelezen. Zorg dat het UTF-8 gecodeerd is.", "error")
        return render_template("kit_import.html", preview=None)

    reader = csv.DictReader(io.StringIO(content))
    required_cols = {'model', 'type', 'size', 'status', 'state'}
    fieldnames = set(reader.fieldnames or [])
    if not required_cols.issubset(fieldnames):
        missing = required_cols - fieldnames
        flash(f"CSV mist vereiste kolommen: {', '.join(sorted(missing))}.", "error")
        return render_template("kit_import.html", preview=None)

    db = get_db()
    all_teams = {
        r['name'].lower(): r['id']
        for r in db.execute("SELECT id, name FROM club_teams").fetchall()
    }
    all_profiles = {
        (r['first_name'].strip().lower(), r['last_name'].strip().lower()): r['id']
        for r in db.execute("SELECT id, first_name, last_name FROM player_profiles").fetchall()
    }

    preview = []
    for line_num, row in enumerate(reader, start=2):
        raw_id       = (row.get('id') or '').strip()
        model        = (row.get('model') or '').strip()
        kit_type     = (row.get('type') or '').strip()
        size         = (row.get('size') or '').strip()
        number       = (row.get('number') or '').strip() or None
        name_printed = (row.get('name_printed') or '').strip() or None
        status       = (row.get('status') or '').strip()
        state        = (row.get('state') or '').strip()
        store        = (row.get('store') or '').strip() or None
        mem_first    = (row.get('member_first_name') or '').strip()
        mem_last     = (row.get('member_last_name') or '').strip()
        team_name    = (row.get('team_name') or '').strip()
        date_added   = (row.get('date_added') or '').strip() or None
        remark       = (row.get('remark') or '').strip() or None

        row_errors = []

        if model not in KIT_MODELS:
            row_errors.append(f"unknown model '{model}'")
            model = KIT_MODELS[0]
        if kit_type not in KIT_TYPES:
            row_errors.append(f"unknown type '{kit_type}'")
            kit_type = KIT_TYPES[0]
        if size not in KIT_SIZES:
            row_errors.append(f"unknown size '{size}'")
            size = KIT_SIZES[0]
        if status not in KIT_STATUSES:
            row_errors.append(f"unknown status '{status}'")
            status = KIT_STATUSES[0]
        if state not in KIT_STATES:
            row_errors.append(f"unknown state '{state}'")
            state = KIT_STATES[0]

        profile_id = None
        if mem_first and mem_last:
            profile_id = all_profiles.get((mem_first.lower(), mem_last.lower()))
            if profile_id is None:
                row_errors.append(f"member '{mem_first} {mem_last}' not found")

        team_id = None
        if team_name:
            team_id = all_teams.get(team_name.lower())
            if team_id is None:
                row_errors.append(f"team '{team_name}' not found")

        op = 'insert'
        existing_id = None
        if raw_id:
            try:
                existing_id = int(raw_id)
            except ValueError:
                row_errors.append(f"invalid id '{raw_id}'")
            else:
                exists = db.execute(
                    "SELECT id FROM kit_items WHERE id=? AND is_deleted=0", (existing_id,)
                ).fetchone()
                if not exists:
                    row_errors.append(f"id {existing_id} not found or deleted")
                    existing_id = None
                else:
                    op = 'update'

        preview.append({
            'line': line_num, 'op': op, 'id': existing_id,
            'model': model, 'type': kit_type, 'size': size,
            'number': number, 'name_printed': name_printed,
            'status': status, 'state': state, 'store': store,
            'profile_id': profile_id, 'team_id': team_id,
            'date_added': date_added, 'remark': remark,
            'errors': row_errors,
            'mem_display': ((mem_first + ' ' + mem_last).strip() or None),
            'team_display': team_name or None,
        })

    if not preview:
        flash("CSV-bestand heeft geen datarijen.", "error")
        return render_template("kit_import.html", preview=None)

    return render_template("kit_import.html", preview=preview)


@app.route("/kit/import/confirm", methods=["POST"])
@login_required
def kit_import_confirm():
    require_kit_access()
    raw = request.form.get('preview_data', '')
    if not raw:
        flash("Importsessie verlopen. Upload het bestand opnieuw.", "error")
        return redirect(url_for("kit_import_form"))
    try:
        staged = json.loads(raw)
    except (ValueError, TypeError):
        flash("Importsessie ongeldig. Upload het bestand opnieuw.", "error")
        return redirect(url_for("kit_import_form"))
    if not staged:
        flash("Importsessie verlopen. Upload het bestand opnieuw.", "error")
        return redirect(url_for("kit_import_form"))
    db = get_db()
    now = datetime.now(UTC).isoformat()
    inserted = updated = 0
    try:
        for row in staged:
            if row['op'] == 'insert':
                cur = db.execute(
                    "INSERT INTO kit_items "
                    "(model, type, size, number, name_printed, status, state, store, "
                    " profile_id, team_id, date_added, is_deleted, created_by, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                    (row['model'], row['type'], row['size'],
                     row['number'], row['name_printed'],
                     row['status'], row['state'], row['store'],
                     row['profile_id'], row['team_id'], row['date_added'],
                     current_user.id, now)
                )
                iid = cur.lastrowid
                db.execute(
                    "INSERT INTO kit_log (item_id, action, profile_id, team_id, note, created_by, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (iid, 'created', row['profile_id'], row['team_id'], None, current_user.id, now)
                )
                if row['remark']:
                    db.execute(
                        "INSERT INTO kit_log (item_id, action, profile_id, team_id, note, created_by, created_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (iid, 'remark', row['profile_id'], row['team_id'], row['remark'], current_user.id, now)
                    )
                inserted += 1
            else:
                existing = db.execute("SELECT * FROM kit_items WHERE id=?", (row['id'],)).fetchone()
                if not existing:
                    continue
                old_profile = str(existing['profile_id']) if existing['profile_id'] else None
                new_profile = str(row['profile_id']) if row['profile_id'] else None
                db.execute(
                    "UPDATE kit_items SET model=?, type=?, size=?, number=?, name_printed=?, "
                    "status=?, state=?, store=?, profile_id=?, team_id=?, date_added=? WHERE id=?",
                    (row['model'], row['type'], row['size'],
                     row['number'], row['name_printed'],
                     row['status'], row['state'], row['store'],
                     row['profile_id'], row['team_id'], row['date_added'], row['id'])
                )
                db.execute(
                    "INSERT INTO kit_log (item_id, action, profile_id, team_id, note, created_by, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (row['id'], 'remark', row['profile_id'], row['team_id'],
                     'bulk import update', current_user.id, now)
                )
                if new_profile != old_profile:
                    log_action = 'assigned' if new_profile else 'unassigned'
                    log_profile = row['profile_id'] if new_profile else existing['profile_id']
                    db.execute(
                        "INSERT INTO kit_log (item_id, action, profile_id, team_id, note, created_by, created_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (row['id'], log_action, log_profile, row['team_id'], None, current_user.id, now)
                    )
                if row['remark']:
                    db.execute(
                        "INSERT INTO kit_log (item_id, action, profile_id, team_id, note, created_by, created_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (row['id'], 'remark', row['profile_id'], row['team_id'], row['remark'], current_user.id, now)
                    )
                updated += 1
        db.commit()
    except Exception as exc:
        db.rollback()
        flash(f"Import mislukt: {exc}", "error")
        return redirect(url_for("kit_list"))
    flash(f"Import voltooid: {inserted} ingevoegd, {updated} bijgewerkt.", "success")
    return redirect(url_for("kit_list"))


# ── /conflicts routes ─────────────────────────────────────────────────────────

@app.route("/conflicts")
@login_required
def conflicts_page():
    db = get_db()
    ucond, uparams = _uid_cond()
    seasons = [dict(s) for s in db.execute(
        f"SELECT id, name FROM seasons WHERE 1=1{ucond} ORDER BY name DESC", uparams
    ).fetchall()]

    valid_season_ids = {s['id'] for s in seasons}
    selected_season_id = request.args.get("season_id", type=int)
    if selected_season_id not in valid_season_ids:
        selected_season_id = seasons[0]['id'] if seasons else None

    fetched_at, all_matches = _get_match_cache(db)
    no_cache = all_matches is None

    sporthal_conflicts = []
    person_conflicts   = {}
    if not no_cache and selected_season_id:
        future = _filter_future_matches(all_matches)
        sporthal_conflicts = _detect_sporthal_conflicts(future)
        merged = _merge_matches_with_people(future, db, selected_season_id)
        person_conflicts = _detect_person_conflicts(merged)

    return render_template(
        "conflicts.html",
        sporthal_conflicts=sporthal_conflicts,
        person_conflicts=person_conflicts,
        fetched_at=fetched_at,
        no_cache=no_cache,
        seasons=seasons,
        selected_season_id=selected_season_id,
    )


@app.route("/conflicts/refresh", methods=["POST"])
@login_required
def conflicts_refresh():
    season_id_str = request.form.get("season_id", "").strip()
    season_id = int(season_id_str) if season_id_str.isdigit() else None
    xml_bytes, fetch_err = _fetch_federation_xml()
    if xml_bytes is None:
        flash(f"Fout bij ophalen wedstrijddata: {fetch_err}", "error")
        return redirect(url_for("conflicts_page"))
    matches = _parse_federation_xml(xml_bytes)
    db = get_db()
    _store_match_cache(db, matches)
    db.commit()
    return redirect(url_for("conflicts_page", season_id=season_id))


@app.route("/conflicts/upload-xml", methods=["POST"])
@login_required
def conflicts_upload_xml():
    """Accept a manually uploaded Volleyadmin2 XML file and store it as the match cache."""
    season_id_str = request.form.get("season_id", "").strip()
    season_id = int(season_id_str) if season_id_str.isdigit() else None
    f = request.files.get("xml_file")
    if not f or not f.filename:
        flash("Selecteer een XML-bestand om te uploaden.", "error")
        return redirect(url_for("conflicts_page", season_id=season_id))
    try:
        xml_bytes = f.read()
    except Exception as exc:
        flash(f"Bestand kon niet worden gelezen: {exc}", "error")
        return redirect(url_for("conflicts_page", season_id=season_id))
    matches = _parse_federation_xml(xml_bytes)
    if not matches:
        flash("Het XML-bestand bevat geen wedstrijdgegevens of is ongeldig.", "error")
        return redirect(url_for("conflicts_page", season_id=season_id))
    db = get_db()
    _store_match_cache(db, matches)
    db.commit()
    flash(f"Wedstrijddata geladen: {len(matches)} wedstrijden.", "success")
    return redirect(url_for("conflicts_page", season_id=season_id))


# ── Federation conflict-checker helpers ───────────────────────────────────────

BELVOC_STAMNUMMER = "O-2186"
BELVOC_SPORTHAL   = "Belsele, Sporthal De Klavers"


def _normalize_ploeg(name):
    """Strip (+) suffix, collapse whitespace, lowercase — used for team-name matching."""
    return ' '.join(name.replace('(+)', '').split()).lower()


def _overlap_duration(s1, e1, s2, e2):
    """Return 'N min (P%)' overlap string, or None when the intervals do not overlap."""
    overlap_start = max(s1, s2)
    overlap_end   = min(e1, e2)
    if overlap_start < overlap_end:
        duration = (overlap_end - overlap_start).total_seconds() / 60
        pct      = duration / ((e2 - s2).total_seconds() / 60) * 100
        return f"{duration:.0f} min ({pct:.0f}%)"
    return None


def _fetch_federation_xml():
    """Fetch the Volleyadmin2 XML for BELVOC_STAMNUMMER; returns (bytes, None) or (None, str_error)."""
    url = f"http://www.volleyadmin2.be/services/wedstrijden_xml.php?stamnummer={BELVOC_STAMNUMMER}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "http://www.volleyadmin2.be/",
        "Accept": "text/xml,application/xml,application/xhtml+xml,*/*;q=0.9",
        "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        return resp.content, None
    except Exception as exc:
        print(f"_fetch_federation_xml error: {exc}", file=sys.stderr)
        return None, str(exc)


def _parse_federation_xml(xml_bytes):
    """Parse Volleyadmin2 XML bytes; returns list of match dicts."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        print(f"_parse_federation_xml parse error: {exc}", file=sys.stderr)
        return []
    matches = []
    for w in root.findall('.//wedstrijd'):
        datum       = w.findtext('datum', default='')
        aanvangsuur = w.findtext('aanvangsuur', default='')
        reeks       = w.findtext('reeks', default='')
        promo       = reeks.startswith(('OHP', 'ODP', 'OBP'))
        try:
            base_dt  = datetime.strptime(f"{datum} {aanvangsuur}", "%d/%m/%Y %H:%M")
            offset   = timedelta(minutes=150) if promo else timedelta(minutes=60)
            start_dt = base_dt - offset
            einde_dt = base_dt + timedelta(hours=2)
            start_str = start_dt.strftime("%d/%m/%Y %H:%M")
            einde_str = einde_dt.strftime("%d/%m/%Y %H:%M")
        except ValueError:
            start_str = datum + ' ' + aanvangsuur
            einde_str = ''
        thuisploeg     = w.findtext('thuisploeg', default='')
        bezoekersploeg = w.findtext('bezoekersploeg', default='')
        ploeg = thuisploeg if 'vc belvoc belsele' in thuisploeg.lower() else bezoekersploeg
        matches.append({
            'datum':          datum,
            'aanvangsuur':    aanvangsuur,
            'reeks':          reeks,
            'thuisploeg':     thuisploeg,
            'bezoekersploeg': bezoekersploeg,
            'ploeg':          ploeg,
            'sporthal':       w.findtext('sporthal', default=''),
            'start':          start_str,
            'einde':          einde_str,
        })
    return matches


def _filter_future_matches(matches):
    """Return only matches with start >= now()."""
    now = datetime.now()
    result = []
    for m in matches:
        try:
            start_dt = datetime.strptime(m['start'], "%d/%m/%Y %H:%M")
        except Exception:
            continue
        if start_dt >= now:
            result.append(m)
    return result


def _get_match_cache(db):
    """Return (fetched_at_str, matches_list) or (None, None) when cache is empty."""
    row = db.execute(
        "SELECT fetched_at, matches_json FROM federation_match_cache WHERE id=1"
    ).fetchone()
    if not row:
        return None, None
    try:
        return row['fetched_at'], json.loads(row['matches_json'])
    except Exception:
        return None, None


def _store_match_cache(db, matches):
    """Upsert the full match list into federation_match_cache."""
    fetched_at = datetime.now(UTC).isoformat()
    db.execute(
        "INSERT OR REPLACE INTO federation_match_cache (id, fetched_at, matches_json) VALUES (1,?,?)",
        (fetched_at, json.dumps(matches))
    )


def _detect_sporthal_conflicts(matches):
    """Return list of conflict groups (each group = list of match dicts with overlap_duration).
    A group contains 3+ overlapping matches at BELVOC_SPORTHAL on the same day."""
    sporthal_matches = [m for m in matches if m.get('sporthal') == BELVOC_SPORTHAL]

    # Attach datetime objects temporarily
    for m in sporthal_matches:
        try:
            m['_s'] = datetime.strptime(m['start'], "%d/%m/%Y %H:%M")
            m['_e'] = datetime.strptime(m['einde'], "%d/%m/%Y %H:%M")
        except Exception:
            m['_s'] = None
            m['_e'] = None

    sporthal_matches = [m for m in sporthal_matches if m['_s'] and m['_e']]

    per_day = {}
    for m in sporthal_matches:
        per_day.setdefault(m['_s'].date(), []).append(m)

    groups = []
    for _, day_ms in per_day.items():
        day_ms = sorted(day_ms, key=lambda x: (x['_s'], x.get('reeks', ''), x.get('bezoekersploeg', '')))
        n = len(day_ms)
        if n < 3:
            continue
        adj = [set() for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                a, b = day_ms[i], day_ms[j]
                if a['_s'] <= b['_e'] and a['_e'] >= b['_s']:
                    adj[i].add(j)
                    adj[j].add(i)
        visited = [False] * n
        for i in range(n):
            if visited[i]:
                continue
            stack, comp = [i], []
            visited[i] = True
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for nb in adj[cur]:
                    if not visited[nb]:
                        visited[nb] = True
                        stack.append(nb)
            if len(comp) <= 2:
                continue
            group = sorted([day_ms[idx] for idx in comp],
                           key=lambda x: (x['_s'], x.get('reeks', ''), x.get('bezoekersploeg', '')))
            for m in group:
                best, best_min = None, -1
                for other in group:
                    if other is m:
                        continue
                    od = _overlap_duration(other['_s'], other['_e'], m['_s'], m['_e'])
                    if od:
                        mins = int(od.split(' ', 1)[0])
                        if mins > best_min:
                            best_min, best = mins, od
                m['overlap_duration'] = best
            groups.append(group)

    groups.sort(key=lambda g: g[0]['_s'])

    # Clean up temporary fields
    for m in sporthal_matches:
        m.pop('_s', None)
        m.pop('_e', None)

    return groups


def _merge_matches_with_people(matches, db, season_id):
    """Enrich match list with coach/player assignments from club_team_players.

    Returns a flat list of dicts combining match fields with:
        profile_id, person_name, involvement ('coach' | 'speler' | None)

    For each match the team is resolved via
        (_normalize_ploeg(ct.name), ct.federation_reeks) → club_team.id
    Then club_team_players is queried for coaches (roles LIKE '%coach%').
    Additionally, each found coach is checked for player roles on other teams
    in the same season; those matches are added as involvement='speler' rows.
    Matches with no resolved team produce a single row with None person fields.
    """
    # Build lookup: (norm_name, reeks) → team_id
    team_rows = db.execute(
        "SELECT id, name, federation_reeks FROM club_teams WHERE federation_reeks IS NOT NULL"
    ).fetchall()
    team_lookup = {}
    for t in team_rows:
        key = (_normalize_ploeg(t['name']), t['federation_reeks'])
        team_lookup[key] = t['id']

    # For each match resolve team_id and fetch coaches
    merged = []
    all_coach_teams = {}   # profile_id → set of team_ids where they are coach

    for m in matches:
        key = (_normalize_ploeg(m['ploeg']), m['reeks'])
        team_id = team_lookup.get(key)
        if team_id is None:
            merged.append({**m, 'profile_id': None, 'person_name': None, 'involvement': None, '_team_id': None})
            continue
        coaches = db.execute(
            "SELECT ctp.profile_id, pp.first_name, pp.last_name "
            "FROM club_team_players ctp "
            "JOIN player_profiles pp ON pp.id = ctp.profile_id "
            "WHERE ctp.team_id=? AND ctp.season_id=? AND ctp.roles LIKE '%coach%'",
            (team_id, season_id)
        ).fetchall()
        if not coaches:
            merged.append({**m, 'profile_id': None, 'person_name': None, 'involvement': None, '_team_id': team_id})
            continue
        for c in coaches:
            pid  = c['profile_id']
            name = (c['first_name'] + ' ' + c['last_name']).strip()
            merged.append({**m, 'profile_id': pid, 'person_name': name, 'involvement': 'coach', '_team_id': team_id})
            all_coach_teams.setdefault(pid, set()).add(team_id)

    # For each coach, find player roles on other teams in this season
    for pid, coach_team_ids in all_coach_teams.items():
        placeholders = ','.join('?' * len(coach_team_ids))
        player_teams = db.execute(
            f"SELECT ct.id AS team_id, ct.name, ct.federation_reeks "
            f"FROM club_team_players ctp "
            f"JOIN club_teams ct ON ct.id = ctp.team_id "
            f"WHERE ctp.profile_id=? AND ctp.season_id=? AND ctp.roles LIKE '%player%' "
            f"AND ctp.team_id NOT IN ({placeholders})",
            [pid, season_id] + list(coach_team_ids)
        ).fetchall()

        # Fetch person_name once (already known but look it up for safety)
        pp = db.execute(
            "SELECT first_name, last_name FROM player_profiles WHERE id=?", (pid,)
        ).fetchone()
        person_name = (pp['first_name'] + ' ' + pp['last_name']).strip() if pp else ''

        for pt in player_teams:
            if not pt['federation_reeks']:
                continue
            pt_norm = _normalize_ploeg(pt['name'])
            pt_reeks = pt['federation_reeks']
            for m in matches:
                if _normalize_ploeg(m['ploeg']) == pt_norm and m['reeks'] == pt_reeks:
                    merged.append({
                        **m,
                        'profile_id':  pid,
                        'person_name': person_name,
                        'involvement': 'speler',
                        '_team_id':    pt['team_id'],
                    })

    return merged


def _detect_person_conflicts(merged_rows):
    """Detect overlapping matches per person across coach and speler rows.

    Returns dict: {person_name: [[group_row, ...], ...]}
    where each inner list is a group of ≥2 time-overlapping matches.
    """
    person_games = {}
    for row in merged_rows:
        if not row.get('profile_id'):
            continue
        pid = row['profile_id']
        try:
            s = datetime.strptime(row['start'], "%d/%m/%Y %H:%M")
            e = datetime.strptime(row['einde'], "%d/%m/%Y %H:%M")
        except Exception:
            continue
        person_games.setdefault(pid, []).append({**row, '_s': s, '_e': e})

    result = {}
    for pid, games in person_games.items():
        games.sort(key=lambda x: (x['_s'], x.get('reeks', ''), x.get('bezoekersploeg', '')))
        n = len(games)
        conflict_groups = []
        for i in range(n):
            group = [games[i]]
            for j in range(n):
                if i == j:
                    continue
                gi, gj = games[i], games[j]
                if gi['_s'].date() != gj['_s'].date():
                    continue
                if gj['_s'] < group[-1]['_e'] and gj['_e'] > group[-1]['_s']:
                    od = _overlap_duration(gi['_s'], gi['_e'], gj['_s'], gj['_e'])
                    games[j]['overlap_duration'] = od
                    group.append(games[j])
            if len(group) > 1:
                group = sorted(group, key=lambda x: (x['_s'], x.get('reeks', ''), x.get('bezoekersploeg', '')))
                if group not in conflict_groups:
                    conflict_groups.append(group)
        if conflict_groups:
            person_name = games[0].get('person_name') or str(pid)
            result[person_name] = conflict_groups

    # Strip internal datetime fields before returning
    for groups in result.values():
        for group in groups:
            for row in group:
                row.pop('_s', None)
                row.pop('_e', None)
                row.pop('_team_id', None)

    return result


# ── initialise db on startup (runs under both `python app.py` and WSGI) ───────
init_db()
migrate_db()

# ── run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, port=5000)
