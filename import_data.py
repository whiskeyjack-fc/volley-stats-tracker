"""
One-off bulk import of 3 historical matches into stats.db.
Run with:  python import_data.py
"""
import sqlite3
from datetime import datetime, date

DB_PATH = "stats.db"
TEAM_NAME = "Belvoc HP3"
SEASON = "S25-S26"

# Column index → (stat, result)  (0-based, after the 5 fixed columns)
COL_MAP = [
    ("serve",    "error"),
    ("serve",    "1-serve"),
    ("serve",    "2-serve"),
    ("serve",    "3-serve"),
    ("serve",    "ace"),
    ("attack",   "kill"),
    ("attack",   "error"),
    ("receive",  "error"),
    ("receive",  "1-receive"),
    ("receive",  "2-receive"),
    ("receive",  "3-receive"),
    ("receive",  "overpass"),
    ("block",    "kill"),
    ("block",    "error"),
    ("freeball", "error"),
    ("freeball", "3-freeball"),
    ("fault",    "fault"),
]

TYPE_MAP = {"Reserves": "reserve", "Hoofd": "main"}

RAW = """Vamos\t10-1-2026\tReserves\tSet 1\tAiden\t2\t0\t2\t1\t0\t0\t1\t1\t0\t0\t1\t0\t0\t0\t0\t1\t0
Vamos\t10-1-2026\tReserves\tSet 1\tArno\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 1\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 1\tGust\t1\t1\t2\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 1\tJasper\t0\t0\t0\t0\t0\t0\t0\t1\t3\t3\t3\t1\t0\t0\t1\t3\t0
Vamos\t10-1-2026\tReserves\tSet 1\tMathias\t0\t1\t0\t2\t0\t3\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 1\tMax\t0\t0\t1\t3\t0\t2\t0\t0\t4\t3\t1\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 1\tQuinten\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 1\tSenne\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 1\tThomas\t1\t0\t2\t0\t0\t2\t0\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 1\tTuur\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 1\tVictor\t1\t1\t1\t2\t0\t2\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 2\tAiden\t0\t1\t0\t0\t0\t2\t0\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 2\tArno\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 2\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t1\t4\t1\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 2\tGust\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 2\tJasper\t0\t0\t0\t0\t0\t0\t0\t1\t1\t2\t2\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 2\tMathias\t0\t2\t0\t0\t0\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 2\tMax\t2\t0\t0\t0\t1\t2\t1\t1\t3\t3\t2\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 2\tQuinten\t0\t1\t0\t0\t0\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 2\tSenne\t0\t0\t2\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 2\tThomas\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 2\tTuur\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 2\tVictor\t1\t1\t0\t0\t0\t0\t5\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 3\tAiden\t0\t0\t0\t0\t0\t0\t1\t2\t1\t1\t1\t1\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 3\tArno\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 3\tCasper\t0\t0\t0\t0\t0\t0\t0\t1\t0\t2\t2\t1\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 3\tGust\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 3\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 3\tMathias\t0\t3\t1\t0\t0\t2\t0\t0\t0\t0\t1\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 3\tMax\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 3\tQuinten\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 3\tSenne\t0\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 3\tThomas\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 3\tTuur\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tReserves\tSet 3\tVictor\t1\t1\t0\t1\t0\t0\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 1\tAiden\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 1\tArno\t0\t1\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 1\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t1\t1\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 1\tGust\t1\t2\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 1\tJasper\t0\t0\t0\t0\t0\t0\t0\t3\t1\t1\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 1\tMathias\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 1\tMax\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 1\tQuinten\t1\t0\t1\t1\t0\t0\t1\t0\t0\t0\t0\t0\t1\t0\t0\t1\t0
Vamos\t10-1-2026\tHoofd\tSet 1\tSenne\t1\t1\t1\t3\t0\t0\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 1\tThomas\t1\t0\t0\t1\t0\t2\t0\t1\t0\t1\t1\t0\t0\t1\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 1\tTuur\t1\t0\t1\t0\t0\t2\t1\t0\t1\t1\t3\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 1\tVictor\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 2\tAiden\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 2\tArno\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 2\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t1\t1\t1\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 2\tGust\t0\t0\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t2\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 2\tJasper\t0\t0\t0\t0\t0\t0\t0\t2\t1\t1\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 2\tMathias\t1\t2\t1\t0\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 2\tMax\t0\t1\t0\t0\t0\t1\t1\t0\t1\t0\t1\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 2\tQuinten\t0\t1\t1\t0\t0\t1\t1\t0\t0\t0\t0\t0\t1\t2\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 2\tSenne\t0\t0\t2\t1\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 2\tThomas\t0\t0\t1\t0\t0\t0\t2\t0\t1\t2\t1\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 2\tTuur\t0\t3\t0\t1\t0\t2\t1\t0\t2\t3\t1\t2\t1\t0\t0\t1\t0
Vamos\t10-1-2026\tHoofd\tSet 2\tVictor\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 3\tAiden\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 3\tArno\t1\t0\t1\t1\t1\t2\t1\t0\t0\t0\t0\t0\t2\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 3\tCasper\t0\t0\t0\t0\t0\t0\t0\t3\t1\t2\t4\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 3\tGust\t0\t1\t2\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 3\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 3\tMathias\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 3\tMax\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 3\tQuinten\t1\t1\t0\t0\t0\t3\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 3\tSenne\t2\t0\t1\t4\t0\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 3\tThomas\t1\t1\t1\t2\t0\t3\t2\t0\t1\t3\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 3\tTuur\t0\t1\t0\t1\t0\t7\t0\t0\t2\t1\t1\t0\t0\t1\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 3\tVictor\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 4\tAiden\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 4\tArno\t1\t0\t1\t0\t0\t1\t1\t0\t0\t0\t0\t0\t1\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 4\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t2\t4\t3\t0\t0\t0\t0\t2\t0
Vamos\t10-1-2026\tHoofd\tSet 4\tGust\t0\t1\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 4\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 4\tMathias\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 4\tMax\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 4\tQuinten\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 4\tSenne\t1\t1\t1\t2\t1\t2\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 4\tThomas\t0\t1\t2\t2\t0\t0\t1\t0\t1\t0\t1\t1\t0\t1\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 4\tTuur\t1\t0\t1\t0\t1\t2\t1\t3\t2\t1\t1\t0\t0\t0\t0\t0\t0
Vamos\t10-1-2026\tHoofd\tSet 4\tVictor\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 1\tAiden\t0\t2\t0\t0\t0\t0\t1\t4\t1\t2\t0\t1\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 1\tArno\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 1\tCasper\t0\t0\t0\t0\t0\t0\t0\t1\t1\t1\t1\t1\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 1\tGust\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 1\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 1\tMathias\t0\t1\t0\t0\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 1\tMax\t1\t0\t0\t0\t0\t0\t1\t0\t2\t1\t4\t0\t0\t2\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 1\tQuinten\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 1\tSenne\t0\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 1\tThomas\t1\t1\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 1\tTuur\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 1\tVictor\t0\t1\t1\t0\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 2\tAiden\t1\t1\t0\t0\t0\t1\t0\t0\t0\t2\t3\t1\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 2\tArno\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 2\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 2\tGust\t1\t1\t0\t2\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 2\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t1\t1\t5\t1\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 2\tMathias\t0\t1\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 2\tMax\t0\t0\t1\t1\t0\t4\t1\t1\t1\t1\t2\t1\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 2\tQuinten\t1\t0\t1\t0\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 2\tSenne\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 2\tThomas\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 2\tTuur\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 2\tVictor\t0\t1\t1\t0\t0\t1\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 3\tAiden\t0\t2\t0\t0\t0\t1\t2\t1\t0\t2\t1\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 3\tArno\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 3\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t1\t3\t1\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 3\tGust\t0\t0\t2\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 3\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 3\tMathias\t1\t0\t0\t0\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 3\tMax\t0\t1\t1\t0\t2\t1\t1\t1\t0\t1\t2\t1\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 3\tQuinten\t0\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 3\tSenne\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 3\tThomas\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 3\tTuur\t1\t0\t2\t0\t0\t1\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tReserves\tSet 3\tVictor\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 1\tAiden\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 1\tArno\t1\t0\t1\t0\t0\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 1\tCasper\t0\t0\t0\t0\t0\t1\t0\t0\t1\t0\t1\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 1\tGust\t0\t5\t3\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t1
Argos\t21-2-2026\tHoofd\tSet 1\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 1\tMathias\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 1\tMax\t0\t1\t0\t0\t0\t1\t2\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 1\tQuinten\t0\t0\t1\t0\t0\t1\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 1\tSenne\t0\t0\t0\t0\t0\t0\t3\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 1\tThomas\t0\t1\t1\t0\t0\t4\t0\t0\t0\t2\t4\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 1\tTuur\t0\t1\t0\t1\t0\t1\t2\t1\t5\t3\t2\t0\t0\t1\t0\t0\t1
Argos\t21-2-2026\tHoofd\tSet 1\tVictor\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 2\tAiden\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 2\tArno\t0\t2\t0\t0\t0\t4\t1\t0\t0\t0\t1\t0\t1\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 2\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t2\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 2\tGust\t1\t1\t1\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t1
Argos\t21-2-2026\tHoofd\tSet 2\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 2\tMathias\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 2\tMax\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 2\tQuinten\t1\t0\t1\t1\t1\t0\t1\t0\t0\t0\t0\t0\t1\t0\t0\t0\t1
Argos\t21-2-2026\tHoofd\tSet 2\tSenne\t1\t1\t0\t1\t0\t1\t1\t0\t0\t0\t2\t0\t1\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 2\tThomas\t0\t2\t1\t0\t0\t3\t2\t1\t0\t3\t1\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 2\tTuur\t1\t2\t0\t0\t0\t2\t1\t1\t0\t4\t6\t0\t1\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 2\tVictor\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 3\tAiden\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 3\tArno\t0\t4\t0\t0\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 3\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t2\t3\t6\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 3\tGust\t0\t1\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 3\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 3\tMathias\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 3\tMax\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 3\tQuinten\t0\t0\t2\t0\t0\t0\t1\t0\t0\t0\t0\t0\t2\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 3\tSenne\t0\t2\t0\t0\t0\t0\t0\t0\t1\t1\t1\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 3\tThomas\t1\t1\t1\t0\t0\t2\t1\t0\t0\t0\t5\t0\t1\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 3\tTuur\t1\t1\t2\t0\t0\t3\t3\t0\t0\t1\t0\t0\t0\t0\t0\t0\t0
Argos\t21-2-2026\tHoofd\tSet 3\tVictor\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 1\tAiden\t1\t0\t0\t2\t1\t1\t1\t1\t3\t4\t1\t0\t1\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 1\tArno\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 1\tCasper\t0\t0\t0\t0\t0\t0\t0\t2\t1\t1\t7\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 1\tGust\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 1\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 1\tMathias\t0\t5\t2\t0\t1\t4\t1\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 1\tMax\t0\t0\t2\t3\t1\t2\t1\t2\t2\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 1\tQuinten\t1\t1\t1\t1\t0\t2\t0\t0\t0\t0\t0\t0\t3\t3\t1\t0\t0
BMV C\t7-3-2026\tReserves\tSet 1\tSenne\t0\t2\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t1\t0\t1\t0
BMV C\t7-3-2026\tReserves\tSet 1\tThomas\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 1\tTuur\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 1\tVictor\t1\t0\t1\t1\t0\t2\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 2\tAiden\t1\t1\t1\t0\t0\t2\t1\t0\t0\t2\t1\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 2\tArno\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 2\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t2\t2\t8\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 2\tGust\t0\t1\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 2\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 2\tMathias\t0\t2\t0\t1\t0\t1\t0\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 2\tMax\t0\t0\t0\t1\t0\t3\t1\t0\t0\t0\t1\t0\t0\t1\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 2\tQuinten\t1\t1\t0\t0\t0\t2\t2\t0\t0\t0\t0\t0\t1\t2\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 2\tSenne\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 2\tThomas\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 2\tTuur\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 2\tVictor\t0\t4\t3\t0\t0\t0\t4\t0\t0\t0\t1\t0\t1\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 3\tAiden\t0\t1\t0\t1\t0\t2\t1\t0\t0\t0\t2\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 3\tArno\t0\t1\t1\t0\t0\t1\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 3\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t3\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 3\tGust\t0\t0\t1\t0\t0\t0\t0\t0\t0\t0\t1\t0\t1\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 3\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 3\tMathias\t0\t3\t0\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 3\tMax\t1\t1\t0\t0\t0\t1\t0\t1\t1\t0\t0\t0\t0\t0\t0\t1\t0
BMV C\t7-3-2026\tReserves\tSet 3\tQuinten\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 3\tSenne\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 3\tThomas\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 3\tTuur\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tReserves\tSet 3\tVictor\t0\t1\t1\t0\t1\t3\t0\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 1\tAiden\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 1\tArno\t0\t1\t1\t0\t0\t1\t1\t0\t0\t0\t0\t0\t2\t4\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 1\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 1\tGust\t0\t3\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t1\t5\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 1\tJasper\t0\t0\t0\t0\t0\t0\t0\t1\t1\t2\t0\t0\t0\t0\t1\t1\t0
BMV C\t7-3-2026\tHoofd\tSet 1\tMathias\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 1\tMax\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 1\tQuinten\t2\t0\t0\t0\t0\t0\t1\t0\t0\t1\t0\t0\t2\t3\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 1\tSenne\t0\t2\t0\t0\t0\t3\t1\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 1\tThomas\t0\t5\t1\t1\t0\t2\t3\t0\t0\t1\t4\t0\t1\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 1\tTuur\t0\t1\t0\t0\t0\t3\t0\t1\t3\t2\t4\t0\t1\t1\t0\t1\t0
BMV C\t7-3-2026\tHoofd\tSet 1\tVictor\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 2\tAiden\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 2\tArno\t0\t0\t3\t1\t0\t2\t0\t0\t0\t0\t1\t0\t0\t1\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 2\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 2\tGust\t0\t3\t0\t1\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 2\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t3\t4\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 2\tMathias\t0\t2\t0\t0\t0\t2\t0\t0\t0\t0\t0\t0\t1\t1\t0\t0\t1
BMV C\t7-3-2026\tHoofd\tSet 2\tMax\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 2\tQuinten\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 2\tSenne\t0\t3\t1\t1\t0\t2\t0\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 2\tThomas\t0\t3\t0\t1\t0\t6\t2\t0\t0\t2\t4\t1\t2\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 2\tTuur\t2\t1\t1\t0\t0\t5\t1\t0\t0\t3\t0\t1\t0\t0\t0\t5\t0
BMV C\t7-3-2026\tHoofd\tSet 2\tVictor\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 3\tAiden\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 3\tArno\t2\t0\t1\t0\t0\t5\t2\t0\t0\t1\t0\t0\t1\t1\t0\t1\t0
BMV C\t7-3-2026\tHoofd\tSet 3\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 3\tGust\t0\t3\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 3\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t1\t0\t2\t0\t0\t0\t0\t1\t0
BMV C\t7-3-2026\tHoofd\tSet 3\tMathias\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 3\tMax\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 3\tQuinten\t1\t1\t0\t0\t0\t2\t1\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 3\tSenne\t0\t1\t1\t2\t1\t0\t0\t0\t0\t0\t1\t0\t0\t1\t0\t1\t0
BMV C\t7-3-2026\tHoofd\tSet 3\tThomas\t2\t0\t0\t0\t0\t4\t1\t1\t0\t0\t3\t0\t0\t0\t0\t1\t0
BMV C\t7-3-2026\tHoofd\tSet 3\tTuur\t1\t3\t1\t2\t0\t2\t0\t0\t2\t3\t5\t0\t0\t1\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 3\tVictor\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 4\tAiden\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 4\tArno\t1\t1\t0\t0\t0\t0\t2\t0\t0\t1\t1\t0\t2\t2\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 4\tCasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 4\tGust\t0\t1\t1\t0\t0\t0\t0\t0\t0\t0\t1\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 4\tJasper\t0\t0\t0\t0\t0\t0\t0\t0\t0\t1\t3\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 4\tMathias\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 4\tMax\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 4\tQuinten\t0\t2\t3\t1\t1\t0\t0\t0\t0\t0\t0\t0\t1\t4\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 4\tSenne\t0\t1\t0\t1\t0\t1\t0\t0\t0\t0\t0\t0\t0\t2\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 4\tThomas\t0\t4\t1\t0\t0\t3\t0\t1\t0\t0\t3\t0\t0\t2\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 4\tTuur\t0\t1\t1\t0\t0\t6\t2\t0\t1\t2\t3\t0\t2\t1\t0\t0\t0
BMV C\t7-3-2026\tHoofd\tSet 4\tVictor\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0"""


def parse_date(s):
    """Convert DD-M-YYYY to YYYY-MM-DD."""
    parts = s.split("-")
    return date(int(parts[2]), int(parts[1]), int(parts[0])).isoformat()


def run():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    ts = datetime.now().isoformat()

    # Parse all rows
    rows = [line.split("\t") for line in RAW.strip().splitlines()]

    # --- Build unique (opponent, date_str) → game_id ---
    game_keys = {}
    for r in rows:
        key = (r[0], r[1])
        if key not in game_keys:
            played_at = parse_date(r[1])
            cur.execute(
                "INSERT INTO games (season, team_name, opponent, played_at) VALUES (?,?,?,?)",
                (SEASON, TEAM_NAME, r[0], played_at),
            )
            game_keys[key] = cur.lastrowid
            print(f"  Game created: id={cur.lastrowid} vs {r[0]} on {played_at}")

    # --- Build unique (game_id, name) → player_id ---
    player_keys = {}
    seen_players = set()
    for r in rows:
        game_id = game_keys[(r[0], r[1])]
        name = r[4]
        pk = (game_id, name)
        if pk not in seen_players:
            seen_players.add(pk)
            cur.execute(
                "INSERT INTO players (game_id, name, number) VALUES (?,?,?)",
                (game_id, name, ""),
            )
            player_keys[pk] = cur.lastrowid

    print(f"  Players inserted: {len(player_keys)}")

    # --- Build unique (game_id, set_number, set_type) → set_id ---
    set_keys = {}
    seen_sets = set()
    for r in rows:
        game_id = game_keys[(r[0], r[1])]
        set_type = TYPE_MAP[r[2]]
        set_num = int(r[3].split()[1])  # "Set 1" → 1
        sk = (game_id, set_num, set_type)
        if sk not in seen_sets:
            seen_sets.add(sk)
            cur.execute(
                "INSERT INTO sets (game_id, set_number, set_type, finished, created_at) VALUES (?,?,?,1,?)",
                (game_id, set_num, set_type, ts),
            )
            set_keys[sk] = cur.lastrowid

    print(f"  Sets inserted: {len(set_keys)}")

    # --- Insert events ---
    event_count = 0
    for r in rows:
        game_id = game_keys[(r[0], r[1])]
        set_type = TYPE_MAP[r[2]]
        set_num = int(r[3].split()[1])
        name = r[4]
        player_id = player_keys[(game_id, name)]
        set_id = set_keys[(game_id, set_num, set_type)]

        for col_idx, (stat, result) in enumerate(COL_MAP):
            count = int(r[5 + col_idx])
            for _ in range(count):
                cur.execute(
                    "INSERT INTO events (game_id, set_id, player_id, stat, result, ts) VALUES (?,?,?,?,?,?)",
                    (game_id, set_id, player_id, stat, result, ts),
                )
                event_count += 1

    print(f"  Events inserted: {event_count}")
    con.commit()
    con.close()
    print("Done.")


if __name__ == "__main__":
    run()
