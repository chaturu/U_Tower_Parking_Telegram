import sqlite3
import os
import datetime

from time_utils import now_kst, today_kst

DB_PATH = "data/parking_stats.db"


def _fmt(t):
    if isinstance(t, datetime.datetime):
        return t.strftime('%Y-%m-%d %H:%M')
    return t


def _get_conn():
    os.makedirs("data", exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS registrations (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                vehicle   TEXT NOT NULL,
                code      TEXT,
                user_id   TEXT,
                status    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_date TEXT NOT NULL,
                vehicle    TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                entry_time TEXT,
                exit_time  TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.commit()


def log_registration(vehicle, code, user_id, status):
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO registrations (timestamp, vehicle, code, user_id, status) VALUES (?, ?, ?, ?, ?)",
            (now_kst().isoformat(), vehicle, code, user_id, status)
        )
        conn.commit()


def is_alert_sent_today(vehicle, alert_type, entry_time=None, exit_time=None):
    today = today_kst().isoformat()
    params = [today, vehicle, alert_type]
    query = "SELECT id FROM alert_log WHERE alert_date=? AND vehicle=? AND alert_type=?"
    if entry_time is not None:
        query += " AND entry_time=?"
        params.append(_fmt(entry_time))
    if exit_time is not None:
        query += " AND exit_time=?"
        params.append(_fmt(exit_time))

    with _get_conn() as conn:
        row = conn.execute(query, params).fetchone()
    return row is not None


def is_alert_sent(vehicle, alert_type, entry_time=None, exit_time=None):
    params = [vehicle, alert_type]
    query = "SELECT id FROM alert_log WHERE vehicle=? AND alert_type=?"
    if entry_time is not None:
        query += " AND entry_time=?"
        params.append(_fmt(entry_time))
    if exit_time is not None:
        query += " AND exit_time=?"
        params.append(_fmt(exit_time))

    with _get_conn() as conn:
        row = conn.execute(query, params).fetchone()
    return row is not None


def mark_alert_sent(vehicle, alert_type, entry_time=None, exit_time=None):
    today = today_kst().isoformat()

    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO alert_log (alert_date, vehicle, alert_type, entry_time, exit_time) "
            "VALUES (?, ?, ?, ?, ?)",
            (today, vehicle, alert_type, _fmt(entry_time), _fmt(exit_time))
        )
        conn.commit()


def get_alert_state(key, default=None):
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM alert_state WHERE key=?",
            (key,)
        ).fetchone()
    return row[0] if row else default


def set_alert_state(key, value):
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO alert_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )
        conn.commit()
