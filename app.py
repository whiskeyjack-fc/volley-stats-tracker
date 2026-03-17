import os
import sqlite3
import csv
import io
import re
from datetime import datetime, UTC
from flask import Flask, render_template, request, redirect, url_for, jsonify, make_response, g, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-insecure-key-change-in-production")
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.db")

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access this page."


class User(UserMixin):
    def __init__(self, id, email, role='trainer'):
        self.id = id
        self.email = email
        self.role = role


@login_manager.user_loader
def load_user(user_id):
    db_conn = sqlite3.connect(DATABASE)
    db_conn.row_factory = sqlite3.Row
    row = db_conn.execute("SELECT id, email, role FROM users WHERE id=?", (int(user_id),)).fetchone()
    db_conn.close()
    return User(row["id"], row["email"], row["role"]) if row else None


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
            role          TEXT NOT NULL DEFAULT 'trainer'
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
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id  INTEGER NOT NULL REFERENCES games(id),
            name     TEXT NOT NULL,
            number   TEXT
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
            UNIQUE(user_id, name)
        );

        CREATE TABLE IF NOT EXISTS club_team_players (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES club_teams(id),
            name    TEXT NOT NULL,
            number  TEXT
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
        "INSERT OR IGNORE INTO seasons (name) SELECT DISTINCT season FROM games WHERE season != ''",
        # user accounts
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'trainer')",
        "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'trainer'",
        "ALTER TABLE games ADD COLUMN user_id INTEGER REFERENCES users(id)",
    ]:
        try:
            db.execute(sql)
            db.commit()
        except Exception:
            pass

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

    db.close()

# ── constants ────────────────────────────────────────────────────────────────

STAT_RESULTS = {
    "serve":    ["error", "1-serve", "2-serve", "3-serve", "ace"],
    "receive":  ["error", "1-receive", "2-receive", "3-receive", "overpass"],
    "attack":   ["kill", "error"],
    "block":    ["kill", "error"],
    "freeball": ["error", "1-freeball", "2-freeball", "3-freeball"],
    "fault":    ["fault"],
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
    "freeball": {"3-freeball", "2-freeball"},
}
STAT_NEGATIVE = {
    "serve":    {"error"},
    "attack":   {"error"},
    "receive":  {"error"},
    "block":    set(),
    "freeball": {"error", "1-freeball"},
    "fault":    {"fault"},
}

def build_player_stats(events, players):
    """Return per-player stat summary list given event dicts and player rows."""
    result = []
    for p in players:
        pid   = p["id"]
        pevts = [e for e in events if e["player_id"] == pid]
        by_stat = {}
        for stat, results in STAT_RESULTS.items():
            cnt = {r: sum(1 for e in pevts if e["stat"] == stat and e["result"] == r)
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
            by_stat[stat] = cnt
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
            "Fault":       [-ps["stats"]["fault"]["fault"]   for ps in player_stats],
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
            "name":         p["name"],
            "number":       p["number"],
            "points_pos":   [
                ((_v(g, "serve", "ace") or 0) + (_v(g, "attack", "kill") or 0) + (_v(g, "block", "kill") or 0))
                if gsm.get(g) else None for g in game_ids
            ],
            "points_neg":   [
                ((_v(g, "serve", "error") or 0) + (_v(g, "attack", "error") or 0) +
                 (_v(g, "receive", "error") or 0) + (_v(g, "fault", "fault") or 0))
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
    stats = {}
    for stat, results in STAT_RESULTS.items():
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
        stats[stat] = cnt
    return stats


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
        login_user(User(row["id"], row["email"], row["role"]))
        return redirect(url_for("index"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        row = db.execute(
            "SELECT id, email, password_hash, role FROM users WHERE email=?", (email,)
        ).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return render_template("login.html", error="Invalid email or password.")
        login_user(User(row["id"], row["email"], row["role"]))
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
    active_team = request.args.get("team", "")
    ucond, uparams = _uid_cond()
    if active_team:
        games = db.execute(
            f"SELECT * FROM games WHERE team_name=?{ucond} ORDER BY played_at DESC, id DESC",
            [active_team] + uparams
        ).fetchall()
    else:
        games = db.execute(
            f"SELECT * FROM games WHERE 1=1{ucond} ORDER BY played_at DESC, id DESC",
            uparams
        ).fetchall()
    seasons = db.execute(
        f"SELECT DISTINCT season FROM games WHERE season != ''{ucond} ORDER BY season DESC",
        uparams
    ).fetchall()
    seasons = [s["season"] for s in seasons]
    teams = [r["team_name"] for r in db.execute(
        f"SELECT DISTINCT team_name FROM games WHERE team_name != ''{ucond} ORDER BY team_name COLLATE NOCASE",
        uparams
    ).fetchall()]
    return render_template("index.html", games=games, seasons=seasons, teams=teams, active_team=active_team)


@app.route("/games/new", methods=["GET", "POST"])
@login_required
def new_game():
    if request.method == "POST":
        team   = request.form["team_name"].strip()
        opp    = request.form["opponent"].strip()
        played = request.form.get("played_at") or datetime.now().strftime("%Y-%m-%d")
        season = request.form.get("season", "").strip()
        db = get_db()
        cur = db.execute(
            "INSERT INTO games (user_id, season, team_name, opponent, played_at) VALUES (?,?,?,?,?)",
            (current_user.id, season, team, opp, played)
        )
        game_id = cur.lastrowid

        numbers = request.form.getlist("player_number")
        names   = request.form.getlist("player_name")
        for num, name in zip(numbers, names):
            name = name.strip()
            if name:
                db.execute(
                    "INSERT INTO players (game_id, name, number) VALUES (?,?,?)",
                    (game_id, name, num.strip())
                )
        db.commit()
        return redirect(url_for("track", game_id=game_id))
    db = get_db()
    ucond, uparams = _uid_cond()
    seasons = [s["name"] for s in db.execute(
        f"SELECT name FROM seasons WHERE 1=1{ucond} ORDER BY name DESC", uparams
    ).fetchall()]
    club_teams = [dict(t) for t in db.execute(
        f"SELECT id, name FROM club_teams WHERE 1=1{ucond} ORDER BY name COLLATE NOCASE", uparams
    ).fetchall()]
    return render_template("game_setup.html", seasons=seasons, club_teams=club_teams)


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

@app.route("/api/games/<int:game_id>/events", methods=["POST"])
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
    db.execute(
        "INSERT INTO events (game_id, set_id, player_id, stat, result, ts) VALUES (?,?,?,?,?,?)",
        (game_id, set_id, player_id, stat, result, datetime.now(UTC).isoformat())
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/games/<int:game_id>/events", methods=["DELETE"])
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
    return jsonify(dict(new_set)), 201


@app.route("/api/games/<int:game_id>/sets/<int:set_id>", methods=["DELETE"])
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
@login_required
def finish_set(game_id, set_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    db.execute("UPDATE sets SET finished=1 WHERE id=? AND game_id=?", (set_id, game_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/games/<int:game_id>/sets/<int:set_id>/reopen", methods=["POST"])
@login_required
def reopen_set(game_id, set_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    db.execute("UPDATE sets SET finished=0 WHERE id=? AND game_id=?", (set_id, game_id))
    db.commit()
    return jsonify({"ok": True})



@app.route("/games/<int:game_id>/report")
@login_required
def report(game_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    game    = db.execute(f"SELECT * FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone()
    if not game:
        return "Game not found", 404
    players = db.execute("SELECT * FROM players WHERE game_id=? ORDER BY name COLLATE NOCASE", (game_id,)).fetchall()
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
    player_names = [ps["name"] for ps in player_stats]
    chart_data = {
        "players": player_names,
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
                "players": [ps["name"] for ps in s_player_stats],
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
    else:
        team_events = [e for e in all_events if e["player_id"] is not None]

    team_kwarg = {"team": active_team} if active_team else {}
    filter_urls = {
        "all":     url_for("season_report", season=season, **team_kwarg),
        "main":    url_for("season_report", season=season, type="main", **team_kwarg),
        "reserve": url_for("season_report", season=season, type="reserve", **team_kwarg),
    }

    rows = []
    for g in games:
        g_events = [e for e in team_events if e["game_id"] == g["id"]]
        rows.append({
            "name":      f"vs {g['opponent']}",
            "game_id":   g["id"],
            "played_at": g["played_at"],
            "opponent":  g["opponent"],
            "stats":     agg_team_stats(g_events),
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
    active_team      = request.args.get("team", "")       # game team_name filter
    active_club_team = request.args.get("club_team", "")  # club roster filter (by name)
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
    all_club_teams = [dict(t) for t in db.execute(
        f"SELECT id, name FROM club_teams WHERE 1=1{ucond} ORDER BY name COLLATE NOCASE",
        uparams
    ).fetchall()]

    # Build base params dict (excludes game selection; used for chip URLs)
    def _base_params(**overrides):
        p = {}
        if active_season:    p["season"]    = active_season
        if active_team:      p["team"]      = active_team
        if active_club_team: p["club_team"] = active_club_team
        if active_set_type:  p["type"]      = active_set_type
        if active_players:   p["player"]    = list(active_players)
        p.update({k: v for k, v in overrides.items() if v is not None})
        # drop keys explicitly set to "" or empty list
        return {k: v for k, v in p.items() if v != "" and v != []}

    def _base_params_no_games(**overrides):
        """Like _base_params but always drops `game` selections."""
        return _base_params(**overrides)

    season_urls = {
        "all": url_for("player_report", **_base_params(season="")),
    }
    season_urls.update({s: url_for("player_report", **_base_params(season=s)) for s in all_seasons})

    team_urls = {
        "all": url_for("player_report", **_base_params(team="")),
    }
    team_urls.update({t: url_for("player_report", **_base_params(team=t)) for t in all_game_teams})

    club_team_urls = {
        "all": url_for("player_report", **_base_params(club_team="")),
    }
    club_team_urls.update({ct["name"]: url_for("player_report", **_base_params(club_team=ct["name"]))
                           for ct in all_club_teams})

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

    # Build roster filter from club team (normalized names)
    roster_names = None  # None = no filter
    if active_club_team:
        club_row = db.execute(
            f"SELECT id FROM club_teams WHERE name=?{ucond}", [active_club_team] + uparams
        ).fetchone()
        if club_row:
            roster_rows = db.execute(
                "SELECT name FROM club_team_players WHERE team_id=?", (club_row["id"],)
            ).fetchall()
            roster_names = {r["name"].strip().lower() for r in roster_rows}

    # Game toggle URLs — toggling a game_id in/out of selected_game_ids
    def _game_toggle_url(gid):
        new_sel = selected_game_ids.symmetric_difference({gid})
        p = _base_params_no_games()
        for g in new_sel:
            p.setdefault("game", [])
            if isinstance(p["game"], list):
                p["game"].append(str(g))
            else:
                p["game"] = [p["game"], str(g)]
        return url_for("player_report", **p)

    game_chips = []
    for g in all_candidate_games:
        gid = g["id"]
        new_sel = selected_game_ids.symmetric_difference({gid})
        base_p = _base_params_no_games()
        base_p["game"] = [str(x) for x in new_sel]
        game_chips.append({
            "id":        gid,
            "label":     f"{g['played_at']} vs {g['opponent']}",
            "active":    gid in selected_game_ids,
            "toggle_url": url_for("player_report", **base_p),
        })

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
            all_seasons=all_seasons, all_game_teams=all_game_teams, all_club_teams=all_club_teams,
            active_season=active_season, active_team=active_team,
            active_club_team=active_club_team, active_set_type=active_set_type,
            has_main=False, has_reserve=False,
            filter_urls=filter_urls, season_urls=season_urls,
            team_urls=team_urls, club_team_urls=club_team_urls,
            game_chips=game_chips, selected_game_ids=list(selected_game_ids),
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
        f"SELECT * FROM players WHERE game_id IN ({placeholders}) ORDER BY name COLLATE NOCASE", game_ids
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
        if roster_names is not None and key not in roster_names:
            continue
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

        display_name   = records[0]["name"]
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
        all_club_teams=all_club_teams,
        active_season=active_season,
        active_team=active_team,
        active_club_team=active_club_team,
        active_set_type=active_set_type,
        has_main=has_main,
        has_reserve=has_reserve,
        filter_urls=filter_urls,
        season_urls=season_urls,
        team_urls=team_urls,
        club_team_urls=club_team_urls,
        game_chips=game_chips,
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

    if request.method == "POST":
        team   = request.form["team_name"].strip()
        opp    = request.form["opponent"].strip()
        played = request.form.get("played_at") or game["played_at"]
        season = request.form.get("season", "").strip()
        db.execute(
            "UPDATE games SET team_name=?, opponent=?, played_at=?, season=? WHERE id=?",
            (team, opp, played, season, game_id)
        )

        # Rebuild players: delete all then re-insert from form
        db.execute("DELETE FROM players WHERE game_id=?", (game_id,))
        numbers = request.form.getlist("player_number")
        names   = request.form.getlist("player_name")
        for num, name in zip(numbers, names):
            name = name.strip()
            if name:
                db.execute(
                    "INSERT INTO players (game_id, name, number) VALUES (?,?,?)",
                    (game_id, name, num.strip())
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
                           existing_seasons=existing_seasons)


@app.route("/games/<int:game_id>/delete", methods=["POST"])
@login_required
def delete_game(game_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM games WHERE id=?{ucond}", [game_id] + uparams).fetchone():
        return "Game not found", 404
    db.execute("DELETE FROM events  WHERE game_id=?", (game_id,))
    db.execute("DELETE FROM players WHERE game_id=?", (game_id,))
    db.execute("DELETE FROM games   WHERE id=?",      (game_id,))
    db.commit()
    return redirect(url_for("index"))


# ── Club Teams ───────────────────────────────────────────────────────────────

@app.route("/teams")
@login_required
def team_list():
    db = get_db()
    ucond, uparams = _uid_cond()
    teams = db.execute(
        f"SELECT * FROM club_teams WHERE 1=1{ucond} ORDER BY name COLLATE NOCASE", uparams
    ).fetchall()
    team_player_counts = {}
    for t in teams:
        cnt = db.execute(
            "SELECT COUNT(*) AS c FROM club_team_players WHERE team_id=?", (t["id"],)
        ).fetchone()["c"]
        team_player_counts[t["id"]] = cnt
    return render_template("team_list.html", teams=teams, team_player_counts=team_player_counts)


@app.route("/teams/new", methods=["GET", "POST"])
@login_required
def new_team():
    if request.method == "POST":
        name = request.form.get("team_name", "").strip()
        if not name:
            return render_template("team_form.html", team=None, players=[], error="Team name is required.")
        db = get_db()
        try:
            cur = db.execute("INSERT INTO club_teams (user_id, name) VALUES (?,?)", (current_user.id, name))
            team_id = cur.lastrowid
        except sqlite3.IntegrityError:
            return render_template("team_form.html", team=None, players=[], error="A team with this name already exists.")
        numbers = request.form.getlist("player_number")
        names   = request.form.getlist("player_name")
        for num, pname in zip(numbers, names):
            pname = pname.strip()
            if pname:
                db.execute(
                    "INSERT INTO club_team_players (team_id, name, number) VALUES (?,?,?)",
                    (team_id, pname, num.strip())
                )
        db.commit()
        return redirect(url_for("team_list"))
    return render_template("team_form.html", team=None, players=[])


@app.route("/teams/<int:team_id>/edit", methods=["GET", "POST"])
@login_required
def edit_team(team_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    team = db.execute(f"SELECT * FROM club_teams WHERE id=?{ucond}", [team_id] + uparams).fetchone()
    if not team:
        return "Team not found", 404
    if request.method == "POST":
        name = request.form.get("team_name", "").strip()
        players = db.execute(
            "SELECT * FROM club_team_players WHERE team_id=? ORDER BY name COLLATE NOCASE", (team_id,)
        ).fetchall()
        if not name:
            return render_template("team_form.html", team=team, players=players, error="Team name is required.")
        try:
            db.execute("UPDATE club_teams SET name=? WHERE id=?", (name, team_id))
        except sqlite3.IntegrityError:
            return render_template("team_form.html", team=team, players=players, error="A team with this name already exists.")
        db.execute("DELETE FROM club_team_players WHERE team_id=?", (team_id,))
        numbers = request.form.getlist("player_number")
        names   = request.form.getlist("player_name")
        for num, pname in zip(numbers, names):
            pname = pname.strip()
            if pname:
                db.execute(
                    "INSERT INTO club_team_players (team_id, name, number) VALUES (?,?,?)",
                    (team_id, pname, num.strip())
                )
        db.commit()
        return redirect(url_for("team_list"))
    players = db.execute(
        "SELECT * FROM club_team_players WHERE team_id=? ORDER BY name COLLATE NOCASE", (team_id,)
    ).fetchall()
    return render_template("team_form.html", team=team, players=players)


@app.route("/teams/<int:team_id>/delete", methods=["POST"])
@login_required
def delete_team(team_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM club_teams WHERE id=?{ucond}", [team_id] + uparams).fetchone():
        return "Team not found", 404
    db.execute("DELETE FROM club_team_players WHERE team_id=?", (team_id,))
    db.execute("DELETE FROM club_teams WHERE id=?", (team_id,))
    db.commit()
    return redirect(url_for("team_list"))


@app.route("/api/teams/<int:team_id>/players")
@login_required
def api_team_players(team_id):
    db = get_db()
    ucond, uparams = _uid_cond()
    if not db.execute(f"SELECT id FROM club_teams WHERE id=?{ucond}", [team_id] + uparams).fetchone():
        return jsonify({"error": "forbidden"}), 403
    players = db.execute(
        "SELECT name, number FROM club_team_players WHERE team_id=? ORDER BY name COLLATE NOCASE",
        (team_id,)
    ).fetchall()
    return jsonify([dict(p) for p in players])


@app.route("/api/seasons", methods=["POST"])
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
    if not is_admin():
        return "Forbidden", 403
    db = get_db()
    users = db.execute(
        "SELECT id, email, role, created_at FROM users ORDER BY email"
    ).fetchall()
    return render_template("admin_users.html", users=users)


@app.route("/admin/users/<int:user_id>/role", methods=["POST"])
@login_required
def admin_set_role(user_id):
    if not is_admin():
        return "Forbidden", 403
    new_role = request.form.get("role")
    if new_role not in ("trainer", "coordinator", "admin"):
        return "Invalid role", 400
    db = get_db()
    db.execute("UPDATE users SET role=? WHERE id=?", (new_role, user_id))
    db.commit()
    return redirect(url_for("admin_users"))


# ── initialise db on startup (runs under both `python app.py` and WSGI) ───────
init_db()
migrate_db()

# ── run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, port=5000)
