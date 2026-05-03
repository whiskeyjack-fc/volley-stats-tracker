import sqlite3
conn = sqlite3.connect('stats.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

u = cur.execute("SELECT id, email, profile_id FROM users WHERE id=5").fetchone()
print(f"trainer3: id={u['id']} email={u['email']} profile_id={u['profile_id']}")

if u['profile_id']:
    p = cur.execute("SELECT id, first_name, last_name FROM player_profiles WHERE id=?", (u['profile_id'],)).fetchone()
    if p:
        print(f"Linked profile: [{p['id']}] {p['first_name']} {p['last_name']}")
        entries = cur.execute(
            "SELECT ctp.team_id, ctp.roles, ct.name FROM club_team_players ctp "
            "JOIN club_teams ct ON ct.id=ctp.team_id WHERE ctp.profile_id=?",
            (p['id'],)
        ).fetchall()
        for e in entries:
            print(f"  club_team_players: team=[{e['team_id']}] {e['name']} roles={e['roles']}")
        # Check club_team_trainers
        trainer_teams = cur.execute(
            "SELECT ct.id, ct.name FROM club_team_trainers ctt JOIN club_teams ct ON ct.id=ctt.team_id WHERE ctt.user_id=?",
            (u['id'],)
        ).fetchall()
        print(f"club_team_trainers rows: {[dict(t) for t in trainer_teams]}")

conn.close()

