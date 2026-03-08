# PlayerStats — Volleyball Stat Tracker

A web-based volleyball statistics tracking application built with Flask and SQLite. Track player performance during matches, review per-set and per-match reports, and analyse season-level trends with interactive charts.

## Features

- **Live tracking grid** — players as rows, stat categories as columns; click to increment, long-press to decrement
- **Stat categories** — Serve (error, 1–3, ace), Attack (kill, error), Receive (error, 1–3, overpass), Block (kill, error), Freeball (error, 1–3), Fault
- **Per-set tracking** — track sets independently, mark sets as Main or Reserve, finish and reopen sets
- **Derived stats** — total attempts, raw result, fault %, and quality score (Serve / Receive / Freeball)
- **Match reports** — filterable by set type (Main / Reserve) or individual set; interactive Chart.js charts
- **Season reports** — aggregate stats across all matches in a season
- **Game management** — create, edit, and delete matches
- **Multi-trainer support** — each trainer has their own account; data is fully isolated per user
- **Role-based access** — three roles: `trainer` (own data only), `coordinator` (read all data), `admin` (read all data + manage user roles)
- **Admin panel** — admins can view all registered users and assign or change roles at `/admin/users`

## Project Structure

```
PlayerStats/
├── app.py               # Flask application — routes, DB logic, stat computation
├── stats.db             # SQLite database (auto-created on first run)
├── import_data.py       # One-off script used to bulk-import historical match data
├── Procfile             # Gunicorn entry point for PythonAnywhere / Render
├── templates/
│   ├── base.html
│   ├── index.html       # Game list / home page
│   ├── game_setup.html  # Create / edit a game
│   ├── track.html       # Live tracking grid
│   ├── report.html      # Per-match report with charts
│   ├── season_report.html
│   ├── login.html       # Login page
│   ├── register.html    # Registration page
│   └── admin_users.html # Admin panel — user list and role management
├── static/
│   ├── css/style.css
│   └── js/tracker.js    # Tracking grid interactions (click, long-press, set bar)
└── README.md
```

## Requirements

- Python 3.9+
- Flask 3.x
- Flask-Login 0.6+
- Gunicorn 21+ (production only)

Install dependencies:

```bash
pip install -r requirements.txt
```

## Starting the Server (development)

```bash
cd c:\git\PlayerStats
python app.py
```

The development server starts at **http://127.0.0.1:5000**.

Flask's auto-reloader is enabled by default, so the server restarts automatically whenever `app.py` or a template is saved.

## Deploying to PythonAnywhere (free hosting)

1. Create a free account at [pythonanywhere.com](https://www.pythonanywhere.com).
2. Open a **Bash console** and clone/upload the repo.
3. Create a virtualenv and install dependencies:
   ```bash
   mkvirtualenv --python=python3.12 volleystats
   pip install -r requirements.txt
   ```
4. In the **Web** tab, create a new web app (Manual configuration, Python 3.12).
5. Edit the **WSGI configuration file** — replace its contents with:
   ```python
   import sys
   sys.path.insert(0, '/home/<your-username>/PlayerStats')
   from app import app as application
   ```
6. In the **Web** tab → **Environment variables**, add:
   ```
   SECRET_KEY=<a long random string>
   ```
7. Click **Reload** — the app is live at `<your-username>.pythonanywhere.com`.

> **Note:** PythonAnywhere's free tier stores your SQLite database on persistent disk, so no database migration is needed.

## Usage

1. Open **http://127.0.0.1:5000** in your browser.
2. Click **New Game** to create a match (enter opponent name, date, season, team name).
3. Click **Track** to open the live tracking grid for that match.
4. Use the set bar at the top to manage sets — add new sets, assign a type (Main / Reserve), and finish them when done.
5. Click a cell to record a stat; long-press (≥ 600 ms) to remove one.
6. Click **Report** to view the match report with stat tables and charts.
7. Use the filter bar at the top of the report to switch between All, Main, Reserve, or individual sets.
8. Navigate to **Seasons** from the home page for an aggregated season view.

## Database

The SQLite database (`stats.db`) is created automatically in the project root on first run. It contains the following tables:

| Table               | Description                                      |
|---------------------|--------------------------------------------------|
| `users`             | Trainer accounts (email, hashed password, role)  |
| `games`             | One row per match (scoped to a user)             |
| `players`           | Players registered per match                     |
| `sets`              | Sets within a match (type, finished flag)        |
| `events`            | Individual stat events (stat + result)           |
| `seasons`           | Named seasons (scoped to a user)                 |
| `club_teams`        | Club roster definitions (scoped to a user)       |
| `club_team_players` | Players belonging to a club team                 |

## Dependencies

| Library | Version | Source |
|---|---|---|
| Chart.js | 4.4.2 | https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js |
| chartjs-plugin-datalabels | 2.2.0 | https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js |
