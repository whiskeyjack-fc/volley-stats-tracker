"""
backup.py — create a timestamped hot-backup of stats.db and rotate to keep
the KEEP most recent copies under backups/.

Usage:
    python backup.py

PythonAnywhere scheduled task (daily, e.g. 03:00 UTC):
    python ~/PlayerStats/backup.py
"""
import glob
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "stats.db")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
KEEP = 7


def main():
    if not os.path.exists(DATABASE):
        print(f"Database not found: {DATABASE}")
        return

    os.makedirs(BACKUP_DIR, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    dest = os.path.join(BACKUP_DIR, f"stats_{today}.db")

    if os.path.exists(dest):
        print(f"Backup for today already exists: {dest}")
        return

    src = sqlite3.connect(DATABASE)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    print(f"Backup created: {dest}")

    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "stats_*.db")))
    while len(backups) > KEEP:
        old = backups.pop(0)
        os.remove(old)
        print(f"Removed old backup: {old}")


if __name__ == "__main__":
    main()
