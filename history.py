"""Local, bounded audit history for Probe's decisions (no API keys or file contents)."""
import json
import sqlite3
import time

MAX_EVENTS = 2000


def initialize(database):
    with sqlite3.connect(database, timeout=5) as db:
        db.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, kind TEXT NOT NULL, details TEXT NOT NULL)')


def record(database, kind, details):
    with sqlite3.connect(database, timeout=5) as db:
        db.execute('INSERT INTO history(timestamp, kind, details) VALUES (?, ?, ?)',
                   (time.time(), kind, json.dumps(details, ensure_ascii=False)))
        db.execute('DELETE FROM history WHERE id <= (SELECT id FROM history ORDER BY id DESC LIMIT 1 OFFSET ?)', (MAX_EVENTS,))


def read_history(database, limit=200):
    if not database.exists():
        return [], []
    with sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True, timeout=3) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        skills = db.execute('SELECT scenario, app FROM skills ORDER BY app, scenario').fetchall() if 'skills' in tables else []
        events = db.execute('SELECT timestamp, kind, details FROM history ORDER BY id DESC LIMIT ?', (limit,)).fetchall() if 'history' in tables else []
    return skills, events


def read_labels(database):
    if not database.exists():
        return {}
    try:
        with sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True, timeout=3) as db:
            return dict(db.execute('SELECT id, name FROM app_labels'))
    except sqlite3.Error:
        return {}
