# PlayerStats — Volleyball Club Management Suite

A web-based Flask/SQLite application for volleyball clubs. Beyond live match tracking and statistics, it covers player roster management, team & lineup planning, equipment (kit) tracking, and scheduling-conflict detection.

## Features

### Game Tracking & Reporting
- **Live tracking grid** — players as rows, stat categories as columns; click to increment, long-press to decrement
- **Stat categories** — Serve (error, 1–3, ace), Attack (kill, error), Receive (error, 1–3, overpass), Block (kill, error), Freeball (error, 1–3), Fault
- **Per-set tracking** — track sets independently, mark sets as Main or Reserve, finish and reopen sets
- **Derived stats** — total attempts, raw result, fault %, and quality score (Serve / Receive / Freeball)
- **Match reports** — filterable by set type (Main / Reserve) or individual set; interactive Chart.js charts
- **Game management** — create, edit, delete, and export (CSV) matches at `/games`
- **Player report** — cross-game career stats per player with trend charts and capability-based position recommendations at `/players`
- **Season reports** — aggregate stats across all matches in a season, browsable from a season list at `/seasons`

### Player Roster & Profiles
- **Player roster** (`/roster`) — searchable, filterable list of player profiles (name, DOB, number, status, positions, tags, notes)
- **Create / edit / delete** player profiles, with CSV import (`/roster/import`) and federation card-text import (`/roster/import-federation`)
- **Player remarks** — dated, optionally-private notes on a player's profile
- **Capability scoring** — rate players on technical and personality traits (20–80 scale); scores feed development-gap and position-fit recommendations

### Club Teams & Lineups
- **Team list** (`/teams`) — create, edit, and delete club teams; season-aware roster assignment
- **Trainer assignment** — assign or remove specific trainer accounts per team
- **Team positions** (`/teams/<id>/positions`) — heatmap of player-to-position fit based on capability scores vs. position weights, plus a captain/co-captain order editor
- **Capabilities & position-weight settings** (`/settings/capabilities`, `/settings/position-weights`) — coordinator/admin-only configuration of rateable traits and their importance per position

### Training Groups
- **Training groups** (`/training-groups`) — create, edit, and delete named cohorts of players for training purposes

### Kit / Equipment Tracking
- **Kit inventory** (`/kit`) — browse, filter, and sort equipment (model, type, size, number, status, condition)
- **Kit detail & log** — per-item assignment/removal/maintenance history, plus a global log view at `/kit/log`
- **CSV import/export**, and bulk soft-delete of equipment items

### Reports & Conflict Detection
- **Duplicate shirt-number report** (`/reports/duplicate-numbers`) — flags teams where the same jersey number is assigned to 2+ different players, with CSV export
- **Sporthal conflicts** (`/conflicts/sporthal`) — detects double-booked venues, downgrading warmup-only overlaps to warnings
- **Team overlap** (`/conflicts/teamoverlap`) — overlapping schedules across selected teams
- **Person conflicts** (`/conflicts/persons`) — players double-booked across teams/matches on the same day
- Match schedules are sourced from a federation (Volleyadmin2) XML feed, refreshable on demand or via manual XML upload

### Accounts & Administration
- **Multi-trainer support** — each trainer has their own account; data is fully isolated per user
- **Role-based access** — four roles: `trainer` (own data only), `coordinator` (read all data), `admin` (read all data + manage users), `kit_manager` (kit module only)
- **Admin panel** (`/admin/users`) — view all registered users, change roles, delete accounts, and link a user to a player profile
- **Database backup download** — admins can download a consistent SQLite snapshot from `/admin/backup/download`
- **CSRF protection** (Flask-WTF) on all forms, and rate limiting (Flask-Limiter) on `/login`

## Project Structure

```
PlayerStats/
├── app.py               # Flask application — routes, DB logic, stat computation
├── stats.db             # SQLite database (auto-created on first run)
├── backup.py            # Daily local backup rotation script (7 copies)
├── import_data.py       # One-off script used to bulk-import historical match data
├── deploy.sh            # Server-side deploy script (git pull + conditional pip install)
├── .env.example         # Template for PythonAnywhere API credentials
├── Procfile              # Gunicorn entry point for PythonAnywhere / Render
├── backups/              # Local rotating DB backups (created by backup.py)
├── tests/                # Pytest test suite
├── .github/
│   ├── copilot-instructions.md
│   ├── workflows/
│   │   └── weekly-backup.yml  # Off-site DB backup to the `backups` branch
│   └── prompts/
│       └── deploy.prompt.md  # Copilot agent prompt — automated deploy to PythonAnywhere
├── templates/
│   ├── base.html                       # Layout wrapper (nav, sidebar, shared scripts)
│   ├── _macros.html                    # Shared Jinja2 macros (filters, player picker section, etc.)
│   ├── index.html                      # Home / dashboard
│   ├── login.html / register.html      # Auth pages
│   ├── game_setup.html / edit_game.html  # Create / edit a game
│   ├── track.html                      # Live tracking grid
│   ├── report.html                     # Per-match report with charts
│   ├── season_report.html / season_list.html
│   ├── player_report.html              # Cross-game player stats & position recommendations
│   ├── roster_list.html / roster_detail.html / roster_form.html
│   ├── roster_import.html / roster_import_federation.html
│   ├── team_list.html / team_form.html / team_positions.html
│   ├── position_weights_form.html      # Position-to-capability weight grid
│   ├── training_groups.html / training_group_form.html / training_group_detail.html
│   ├── kit_list.html / kit_detail.html / kit_form.html / kit_import.html / kit_log.html
│   ├── report_duplicate_numbers.html   # Duplicate shirt-number report
│   ├── conflicts.html                  # Sporthal / team-overlap / person conflict tabs
│   └── admin_users.html                # Admin panel — user list and role management
├── static/
│   ├── css/style.css
│   └── js/
│       ├── tracker.js             # Tracking grid interactions (click, long-press, set bar, offline queue)
│       ├── charts-common.js       # Shared chart constants: CAT_COLORS, RESULT_LABELS, axis defaults
│       ├── charts-report.js       # Shared chart infrastructure (registry, plugins, modal, mkChart)
│       ├── player-picker.js       # Reusable player-profile selector with duplicate guard
│       └── filter-multiselect.js  # Excel-style include/exclude multi-checkbox filters
└── README.md
```

## Requirements

- Python 3.9+
- Flask 3.x
- Flask-Login 0.6+
- Flask-Limiter 3.5+ (login rate limiting)
- Flask-WTF 1.2+ (CSRF protection)
- Requests 2.31+ (federation schedule XML fetching)
- Gunicorn 21+ (production only)

Install dependencies:

```bash
pip install -r requirements.txt
```

For local development and running tests:

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
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

## Updating the App on PythonAnywhere

Deployments are fully automated via a Copilot agent prompt — including the git push.

1. Copy `.env.example` to `.env` in the project root and fill in your PythonAnywhere credentials (one-time setup):
   ```
   PA_API_TOKEN=your_api_token_here
   PA_USERNAME=your_username_here
   PA_DOMAIN=your_username.pythonanywhere.com
   ```
   Generate your API token at **pythonanywhere.com → Account → API token**.

2. In VS Code Copilot Chat, run the deploy prompt:
   - Open the prompt picker and select **deploy** (`.github/prompts/deploy.prompt.md`)

   The agent will:
   - Commit and push any uncommitted local changes to `main`
   - Create a temporary console on PythonAnywhere
   - Run `deploy.sh` on the server (`git pull`, and `pip install` only if `requirements.txt` changed)
   - Poll until the script completes
   - Reload the web app automatically
   - Report the outcome

> **CPU seconds:** A routine deploy (no dependency changes) costs ~1–2 CPU seconds against PythonAnywhere's free tier limit of 100/day.

> **Database migrations:** If `app.py` adds new tables or columns, they are applied automatically the next time the app starts (`CREATE TABLE IF NOT EXISTS` pattern). No manual migration step is needed unless a column is renamed or dropped.

### First-time server setup
Before using the deploy prompt for the first time, run these commands once in a PythonAnywhere Bash console to make the deploy script executable:
```bash
cd ~/PlayerStats
chmod +x deploy.sh
```

## Usage

1. Open **http://127.0.0.1:5000** in your browser.
2. Click **New Game** to create a match (enter opponent name, date, season, team name).
3. Click **Track** to open the live tracking grid for that match.
4. Use the set bar at the top to manage sets — add new sets, assign a type (Main / Reserve), and finish them when done.
5. Click a cell to record a stat; long-press (≥ 600 ms) to remove one.
6. Click **Report** to view the match report with stat tables and charts.
7. Use the filter bar at the top of the report to switch between All, Main, Reserve, or individual sets.
8. Navigate to **Seasons** from the home page for an aggregated season view.
9. Use **Roster** to manage player profiles, remarks, and capability scores; **Teams** to manage club teams, trainers, and lineup positions; **Training Groups** to organise training cohorts.
10. Use **Kit** to track equipment assignment and condition, and **Conflicts** to check for venue/schedule/roster double-bookings against the federation's match schedule.

## Database

The SQLite database (`stats.db`) is created automatically in the project root on first run. It contains the following tables:

## Database backups

Three layers of backup are implemented:

**1. Admin on-demand download**
Any `admin` user can download a live hot-backup from `GET /admin/backup/download`. The route uses SQLite's built-in `Connection.backup()` API so the snapshot is always consistent, even under concurrent writes.

**2. Daily local rotation on PythonAnywhere (7 copies)**
`backup.py` (project root) creates `backups/stats_YYYY-MM-DD.db` and prunes the oldest files to keep exactly 7. Register it once as a PythonAnywhere scheduled task (free Beginner tier: 1 task allowed):

- Go to **Dashboard → Tasks** on PythonAnywhere
- Add a daily task at e.g. `03:00 UTC`:
  ```
  python ~/PlayerStats/backup.py
  ```

`deploy.sh` also calls `python backup.py` before `git pull`, ensuring a snapshot is captured before any schema migration runs.

**3. Weekly off-site backup to GitHub (`backups` branch)**
`.github/workflows/weekly-backup.yml` runs every Monday at 06:00 UTC. It downloads `stats.db` via the PythonAnywhere Files API and commits it to the `backups` branch of this repository.

Required GitHub repository secrets (Settings → Secrets → Actions):

| Secret | Value |
|--------|-------|
| `PA_API_TOKEN` | Your PythonAnywhere API token (same as `.env`) |
| `PA_USERNAME` | Your PythonAnywhere username (same as `.env`) |

Create the `backups` branch once before the first run:
```bash
git checkout --orphan backups
git rm -rf .
git commit --allow-empty -m "init: backups branch"
git push origin backups
git checkout main
```

You can also trigger the Action manually from the **Actions** tab using **Run workflow**.

| Table | Description |
|---|---|
| `users` | Trainer/coordinator/admin/kit_manager accounts (email, hashed password, role, optional link to a player profile) |
| `games` | One row per match (scoped to a user) |
| `players` | Players registered per match, optionally linked to a `player_profiles` row |
| `sets` | Sets within a match (type, finished flag) |
| `events` | Individual stat events (stat + result) |
| `seasons` | Named seasons |
| `club_teams` | Club team definitions (name, division, short name, federation reeks code) |
| `club_team_players` | Players belonging to a club team, season-aware |
| `club_team_season_info` | Season-specific team metadata (short name, division) |
| `club_team_trainers` | Trainer-to-team assignments |
| `club_team_captain_order` | Captain/co-captain lineup order per team |
| `club_team_captain_configured` | Flags whether a team's captain order has been manually set |
| `player_profiles` | Central player database (name, DOB, number, status, positions, tags, notes, federation ID) |
| `player_remarks` | Dated notes/observations on a player profile |
| `player_capability_scores` | Player ratings on technical/personality traits (20–80 scale) |
| `position_capability_weights` | Importance of each capability per position, used for position-fit and development-gap calculations |
| `capabilities` | Master list of rateable traits (label, category, sort order, active flag) |
| `training_groups` | Named training cohorts |
| `training_group_players` | Player membership within a training group |
| `kit_items` | Equipment inventory (model, type, size, number, status, condition, assignment) |
| `kit_log` | Equipment action history (assigned/removed/maintenance) |
| `federation_match_cache` | Cached federation (Volleyadmin2) match-schedule XML, used by conflict detection |

## Dependencies

| Library | Version | Source |
|---|---|---|
| Chart.js | 4.4.2 | https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js |
| chartjs-plugin-datalabels | 2.2.0 | https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js |
| Flask-Limiter | 3.5+ | pip (`flask-limiter`) — in-memory rate limiter for `/login` |
| Flask-WTF | 1.2+ | pip (`flask-wtf`) — CSRF protection on all forms |
| Requests | 2.31+ | pip (`requests`) — fetches the federation match-schedule XML for conflict detection |
