import os
import sys
import sqlite3
import csv
import io
import re
import json
from datetime import datetime, UTC
from flask import Flask, render_template, request, redirect, url_for, jsonify, make_response, g, flash, session, abort
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

@app.context_processor
def inject_helpers():
    return dict(can_view_all=can_view_all, is_admin=is_admin)

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

    db.close()

# ── constants ────────────────────────────────────────────────────────────────

TEAM_MEMBER_ROLES = [
    ('player',          'Player'),
    ('head_coach',      'Head Coach'),
    ('assistant_coach', 'Assistant Coach'),
    ('team_manager',    'Team Manager'),
    ('medical',         'Medical'),
    ('scorer',          'Scorer'),
    ('marker',          'Marker'),
    ('video_analyst',   'Video Analyst'),
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
        return redirect(next_url)
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    logout_user()
    return redirect(url_for("login"))


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    db = get_db()
    ucond, uparams = _uid_cond()
    teams_rows = db.execute(
        f"SELECT DISTINCT g.team_name, ct.short_name FROM games g "
        f"LEFT JOIN club_teams ct ON ct.name = g.team_name "
        f"WHERE g.team_name != ''{ucond.replace('user_id', 'g.user_id')} ORDER BY g.team_name COLLATE NOCASE",
        uparams
    ).fetchall()
    teams = [dict(r) for r in teams_rows]
    seasons = [s["season"] for s in db.execute(
        f"SELECT DISTINCT season FROM games WHERE season != ''{ucond} ORDER BY season DESC",
        uparams
    ).fetchall()]
    active_team   = request.args.get("team", "")
    active_season = request.args.get("season", "")
    if active_team and active_team not in [t["team_name"] for t in teams]:
        active_team = ""
    if active_season and active_season not in seasons:
        active_season = ""
    where = "WHERE 1=1"
    params = list(uparams)
    if active_team:
        where += " AND g.team_name=?"
        params.append(active_team)
    if active_season:
        where += " AND g.season=?"
        params.append(active_season)
    games = db.execute(
        f"SELECT g.*, ct.short_name AS team_short_name FROM games g "
        f"LEFT JOIN club_teams ct ON ct.name = g.team_name "
        f"{where}{ucond.replace('user_id', 'g.user_id')} ORDER BY g.played_at DESC, g.id DESC",
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
        team   = request.form["team_name"].strip()
        opp    = request.form["opponent"].strip()
        played = request.form.get("played_at") or datetime.now().strftime("%Y-%m-%d")
        season = request.form.get("season", "").strip()
        cur = db.execute(
            "INSERT INTO games (user_id, season, team_name, opponent, played_at) VALUES (?,?,?,?,?)",
            (current_user.id, season, team, opp, played)
        )
        game_id = cur.lastrowid
        try:
            for pid_str in request.form.getlist("player_profile_id"):
                pid_str = pid_str.strip()
                if not pid_str:
                    continue
                profile = db.execute(
                    "SELECT first_name, last_name, number FROM player_profiles WHERE id=?",
                    (int(pid_str),)
                ).fetchone()
                if profile:
                    pname = (profile["first_name"] + " " + profile["last_name"]).strip().lower()
                    pcur = db.execute(
                        "INSERT INTO players (game_id, name, number, profile_id) VALUES (?,?,?,?)",
                        (game_id, pname, profile["number"] or "", int(pid_str))
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
        pid    = p["id"]
        pevts  = [e for e in events if e["player_id"] == pid]
        by_stat = {}
        for stat, results in STAT_RESULTS.items():
            cnt = {r: sum(1 for e in pevts if e["stat"] == stat and e["result"] == r)
                   for r in results}
            cnt["total"] = sum(cnt.values())
            by_stat[stat] = cnt
        result[str(pid)] = {
            "name": p["name"], "number": p["number"],
            "stats": by_stat,
            "total_events": len(pevts)
        }

    opp_evts = [e for e in events if e["player_id"] is None]
    opp_by_stat = {}
    for stat, results in STAT_RESULTS.items():
        cnt = {r: sum(1 for e in opp_evts if e["stat"] == stat and e["result"] == r)
               for r in results}
        cnt["total"] = sum(cnt.values())
        opp_by_stat[stat] = cnt
    result["opponent"] = {
        "stats": opp_by_stat,
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

    opp_evts = [e for e in events if e["player_id"] is None]
    opp_stats = {}
    for stat, results in STAT_RESULTS.items():
        cnt = {r: sum(1 for e in opp_evts if e["stat"] == stat and e["result"] == r)
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
        opp_stats[stat] = cnt

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

    active_team = request.args.get("team", "")

    # All games in the season (for team list and set-type detection)
    all_season_games = db.execute(
        f"SELECT * FROM games WHERE season=?{ucond} ORDER BY played_at ASC, id ASC",
        [season] + uparams
    ).fetchall()
    if not all_season_games:
        return "Season not found", 404

    teams = sorted(
        {g["team_name"] for g in all_season_games if g["team_name"]},
        key=str.lower,
    )
    team_short_names_rows = db.execute(
        "SELECT name, short_name FROM club_teams WHERE name IN (%s)" % ",".join("?" * len(teams)),
        teams,
    ).fetchall() if teams else []
    team_short_names = {r["name"]: r["short_name"] for r in team_short_names_rows if r["short_name"]}

    if active_team and active_team in teams:
        games = [g for g in all_season_games if g["team_name"] == active_team]
    else:
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
        team_short_names=team_short_names,
    )


@app.route("/players")
@login_required
def player_report():
    db = get_db()
    active_season    = request.args.get("season", "")
    active_team      = request.args.get("team", "")       # game team_name filter
    active_set_type  = request.args.get("type")
    active_players   = request.args.getlist("player")
    selected_game_ids = set(int(x) for x in request.args.getlist("game") if x.isdigit())

    ucond, uparams = _uid_cond()
    all_seasons = [s["season"] for s in db.execute(
        f"SELECT DISTINCT season FROM games WHERE season != ''{ucond} ORDER BY season DESC",
        uparams
    ).fetchall()]
    all_game_teams = [r["team_name"] for r in db.execute(
        f"SELECT DISTINCT team_name FROM games WHERE team_name != ''{ucond} ORDER BY team_name COLLATE NOCASE",
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
        cond_parts.append("team_name=?")
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
        team   = request.form["team_name"].strip()
        opp    = request.form["opponent"].strip()
        played = request.form.get("played_at") or game["played_at"]
        season = request.form.get("season", "").strip()
        cur = db.execute(
            "UPDATE games SET team_name=?, opponent=?, played_at=?, season=? WHERE id=?",
            (team, opp, played, season, game_id)
        )
        if cur.rowcount == 0:
            return "Game not found", 404

        db.execute("DELETE FROM players WHERE game_id=?", (game_id,))
        seen_profiles = set()
        for pid_str in request.form.getlist("player_profile_id"):
            pid_str = pid_str.strip()
            if not pid_str:
                continue
            pid = int(pid_str)
            if pid in seen_profiles:
                continue
            seen_profiles.add(pid)
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
    return render_template("edit_game.html", game=game, players=players,
                           existing_seasons=existing_seasons, all_profiles=all_profiles)


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
    teams = db.execute(
        f"SELECT * FROM club_teams WHERE 1=1{tcond} ORDER BY name COLLATE NOCASE", tparams
    ).fetchall()
    team_player_counts = {}
    team_trainers = {}
    team_non_players = {}
    for t in teams:
        cnt = db.execute(
            "SELECT COUNT(*) AS c FROM club_team_players WHERE team_id=?", (t["id"],)
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
                           all_trainers=all_trainers)


@app.route("/teams/new", methods=["GET", "POST"])
@login_required
def new_team():
    if not can_view_all():
        return "Forbidden", 403
    db = get_db()
    ucond, uparams = _uid_cond()
    all_profiles = [dict(r) for r in db.execute(
        "SELECT id, first_name, last_name, number FROM player_profiles "
        "WHERE status='active' ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE"
    ).fetchall()]
    seasons = [dict(s) for s in db.execute(
        f"SELECT id, name FROM seasons WHERE 1=1{ucond} ORDER BY name DESC", uparams
    ).fetchall()]
    if request.method == "POST":
        name          = request.form.get("team_name", "").strip()
        division      = request.form.get("division", "").strip() or None
        short_name    = request.form.get("short_name", "").strip() or None
        season_id_str = request.form.get("season_id", "").strip()
        season_id     = int(season_id_str) if season_id_str.isdigit() else None
        if not name:
            return render_template("team_form.html", team=None, players=[], all_profiles=all_profiles,
                                   seasons=seasons, selected_season_id=None, error="Team name is required.",
                                   team_member_roles=TEAM_MEMBER_ROLES)
        try:
            cur = db.execute("INSERT INTO club_teams (user_id, name, division, short_name) VALUES (?,?,?,?)", (current_user.id, name, division, short_name))
            team_id = cur.lastrowid
            seen_profiles = set()
            pid_list   = request.form.getlist("player_profile_id")
            roles_list = request.form.getlist("player_roles")
            for i, pid_str in enumerate(pid_list):
                pid_str = pid_str.strip()
                if not pid_str:
                    continue
                pid = int(pid_str)
                if pid in seen_profiles:
                    continue
                seen_profiles.add(pid)
                roles = roles_list[i].strip() if i < len(roles_list) else "player"
                if not roles:
                    roles = "player"
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
            return render_template("team_form.html", team=None, players=[], all_profiles=all_profiles,
                                   seasons=seasons, selected_season_id=None, error="A team with this name already exists.",
                                   team_member_roles=TEAM_MEMBER_ROLES)
        except Exception:
            db.rollback()
            raise
        return redirect(url_for("team_list"))
    return render_template("team_form.html", team=None, players=[], all_profiles=all_profiles,
                           seasons=seasons, selected_season_id=None, team_member_roles=TEAM_MEMBER_ROLES)


@app.route("/teams/<int:team_id>/edit", methods=["GET", "POST"])
@login_required
def edit_team(team_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    team = db.execute(f"SELECT * FROM club_teams WHERE id=?{ucond}", [team_id] + uparams).fetchone()
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
        name          = request.form.get("team_name", "").strip()
        division      = request.form.get("division", "").strip() or None
        short_name    = request.form.get("short_name", "").strip() or None
        season_id_str = request.form.get("season_id", "").strip()
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
        if not name:
            return render_template("team_form.html", team=team, players=players, all_profiles=all_profiles,
                                   seasons=seasons, selected_season_id=selected_season_id, error="Team name is required.",
                                   team_member_roles=TEAM_MEMBER_ROLES)
        try:
            cur = db.execute("UPDATE club_teams SET name=?, division=?, short_name=? WHERE id=?", (name, division, short_name, team_id))
            if cur.rowcount == 0:
                return "Team not found", 404
            if season_id:
                db.execute("DELETE FROM club_team_players WHERE team_id=? AND season_id=?", (team_id, season_id))
            else:
                db.execute("DELETE FROM club_team_players WHERE team_id=? AND season_id IS NULL", (team_id,))
            seen_profiles = set()
            pid_list   = request.form.getlist("player_profile_id")
            roles_list = request.form.getlist("player_roles")
            for i, pid_str in enumerate(pid_list):
                pid_str = pid_str.strip()
                if not pid_str:
                    continue
                pid = int(pid_str)
                if pid in seen_profiles:
                    continue
                seen_profiles.add(pid)
                roles = roles_list[i].strip() if i < len(roles_list) else "player"
                if not roles:
                    roles = "player"
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
                                   seasons=seasons, selected_season_id=selected_season_id, error="A team with this name already exists.",
                                   team_member_roles=TEAM_MEMBER_ROLES)
        except Exception:
            db.rollback()
            raise
        return redirect(url_for("team_list"))
    selected_season_id = request.args.get("season_id", type=int)
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
                           team_member_roles=TEAM_MEMBER_ROLES)


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
    if not re.match(r'^S\d{2}-S\d{2}$', name):
        return jsonify({"error": "Season name must match format Sxx-Syy (e.g. S25-S26)"}), 400
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
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("admin_users"))
    db = get_db()
    if not db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
        return "User not found", 404
    db.execute("DELETE FROM club_team_trainers WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    flash("User deleted.", "success")
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
    sql += " ORDER BY last_name, first_name"
    profiles = db.execute(sql, params).fetchall()

    # Attach teams to each profile (season-filtered when a season is active)
    teams_by_pid = {}
    if season_id:
        teams_rows = db.execute(
            "SELECT ctp.profile_id, ct.id, ct.name, ct.short_name, ctp.roles FROM club_team_players ctp "
            "JOIN club_teams ct ON ct.id = ctp.team_id "
            "WHERE ctp.profile_id IS NOT NULL AND ctp.season_id=?",
            (season_id,)
        ).fetchall()
    else:
        teams_rows = db.execute(
            "SELECT ctp.profile_id, ct.id, ct.name, ct.short_name, ctp.roles FROM club_team_players ctp "
            "JOIN club_teams ct ON ct.id = ctp.team_id WHERE ctp.profile_id IS NOT NULL"
        ).fetchall()
    for row in teams_rows:
        teams_by_pid.setdefault(row["profile_id"], []).append({"id": row["id"], "name": row["name"], "short_name": row["short_name"], "roles": row["roles"]})

    ucond, uparams = _uid_cond()
    all_seasons = [dict(s) for s in db.execute(
        f"SELECT id, name FROM seasons WHERE 1=1{ucond} ORDER BY name DESC", uparams
    ).fetchall()]
    all_teams  = db.execute("SELECT id, name, short_name FROM club_teams ORDER BY name").fetchall()
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
        dob           = request.form.get("date_of_birth", "").strip() or None
        number        = request.form.get("number", "").strip() or None
        status        = request.form.get("status", "active").strip()
        if status not in ("active", "prospect", "trial", "alumni", "inactive"):
            status = "active"
        positions     = request.form.get("positions", "").strip() or None
        tags_raw      = request.form.get("tags", "").strip()
        tags          = json.dumps([t.strip() for t in tags_raw.split(",") if t.strip()]) if tags_raw else None
        notes         = request.form.get("notes", "").strip() or None
        federation_id = request.form.get("federation_id", "").strip() or None
        is_staff      = 1 if request.form.get("is_staff") else 0
        now = datetime.now(UTC).isoformat()
        db = get_db()
        cur = db.execute(
            "INSERT INTO player_profiles "
            "(first_name, last_name, date_of_birth, number, status, positions, tags, notes, "
            " federation_id, is_staff, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (first, last, dob, number, status, positions, tags, notes,
             federation_id, is_staff, current_user.id, now, now)
        )
        db.commit()
        return redirect(url_for("roster_detail", profile_id=cur.lastrowid))
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
                    if status not in ("active", "prospect", "trial", "alumni", "inactive"):
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
            flash(f"Import complete: {inserted} inserted, {skipped} skipped (duplicate).")
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

    import re as _re

    # Regex: optional K-prefix, jersey number, federation card #, last_name, first_name,
    # nationality (discarded), DOB DD/MM/YYYY
    # Last name may consist of multiple words (e.g. "Van Damme"); greedy .+ captures
    # everything up to the first name, then nationality ([A-Z]{2,3}) anchors the boundary.
    _LINE_RE = _re.compile(
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
            flash(f"Import complete: {inserted} inserted, {skipped} skipped (duplicate).")
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

    # Current teams for this player (with season info and roles)
    current_teams = db.execute(
        "SELECT ct.id, ct.name, ct.short_name, s.name AS season_name, ctp.roles "
        "FROM club_team_players ctp "
        "JOIN club_teams ct ON ct.id = ctp.team_id "
        "LEFT JOIN seasons s ON s.id = ctp.season_id "
        "WHERE ctp.profile_id=? "
        "ORDER BY COALESCE(s.name, '') DESC, ct.name COLLATE NOCASE",
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

    return render_template("roster_detail.html",
        profile=profile,
        remarks=remarks_rows,
        can_add_remark=can_add,
        current_teams=current_teams,
        coaching_teams=coaching_teams,
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
        dob           = request.form.get("date_of_birth", "").strip() or None
        number        = request.form.get("number", "").strip() or None
        status        = request.form.get("status", "active").strip()
        if status not in ("active", "prospect", "trial", "alumni", "inactive"):
            status = "active"
        positions     = request.form.get("positions", "").strip() or None
        tags_raw      = request.form.get("tags", "").strip()
        tags          = json.dumps([t.strip() for t in tags_raw.split(",") if t.strip()]) if tags_raw else None
        notes         = request.form.get("notes", "").strip() or None
        federation_id = request.form.get("federation_id", "").strip() or None
        is_staff      = 1 if request.form.get("is_staff") else 0
        now = datetime.now(UTC).isoformat()
        cur = db.execute(
            "UPDATE player_profiles "
            "SET first_name=?, last_name=?, date_of_birth=?, number=?, "
            "    status=?, positions=?, tags=?, notes=?, federation_id=?, is_staff=?, updated_at=? "
            "WHERE id=?",
            (first, last, dob, number, status, positions, tags, notes, federation_id, is_staff, now, profile_id)
        )
        db.commit()
        if cur.rowcount == 0:
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


# ── initialise db on startup (runs under both `python app.py` and WSGI) ───────
init_db()
migrate_db()

# ── run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, port=5000)
