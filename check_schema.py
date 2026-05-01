import sqlite3
conn = sqlite3.connect('stats.db')
cur = conn.cursor()
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
print('Tables:', tables)
new_tables = ['player_profiles','player_remarks','training_groups','training_group_players','club_team_trainers']
for t in new_tables:
    if t in tables:
        cols = [r[1] for r in cur.execute(f'PRAGMA table_info({t})').fetchall()]
        print(f'{t}: {cols}')
    else:
        print(f'MISSING: {t}')
# Check added columns
for t, col in [('players','profile_id'), ('club_team_players','profile_id'), ('users','profile_id'), ('club_teams','name')]:
    cols = [r[1] for r in cur.execute(f'PRAGMA table_info({t})').fetchall()]
    if col in cols:
        print(f'{t}.{col}: OK')
    else:
        print(f'{t}.{col}: MISSING')
# Check users
users = cur.execute("SELECT id, name, role FROM users").fetchall()
print('Users:', users)
conn.close()
