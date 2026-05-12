import re
import sqlite3
from pathlib import Path

DB_PATH = Path("data/develly.db")


def _norm_jid(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            config           TEXT,
            liste_id         INTEGER,
            contact_email    TEXT,
            contact_company  TEXT,
            angle            TEXT,
            canal            TEXT,
            whatsapp_check   TEXT,
            pagespeed_score  INTEGER,
            concurrent_1     TEXT,
            concurrent_2     TEXT,
            status           TEXT,
            erreur_detail    TEXT,
            created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wa_messages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            jid           TEXT NOT NULL,
            contact_email TEXT NOT NULL,
            wa_step       TEXT,
            sent_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wa_messages_jid ON wa_messages(jid)"
    )
    conn.commit()
    conn.close()


def save_wa_message(phone: str, contact_email: str, wa_step: str) -> None:
    jid = _norm_jid(phone)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO wa_messages (jid, contact_email, wa_step) VALUES (?, ?, ?)",
        (jid, contact_email, wa_step),
    )
    conn.commit()
    conn.close()


def find_contact_by_jid(phone: str) -> dict | None:
    jid = _norm_jid(phone)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM wa_messages WHERE jid = ? ORDER BY sent_at DESC LIMIT 1",
        (jid,),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def log_entry(record: dict) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO logs
            (config, liste_id, contact_email, contact_company, angle, canal,
             whatsapp_check, pagespeed_score, concurrent_1, concurrent_2,
             status, erreur_detail)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.get("config"),
            record.get("liste_id"),
            record.get("contact_email"),
            record.get("contact_company"),
            record.get("angle"),
            record.get("canal"),
            record.get("whatsapp_check"),
            record.get("pagespeed_score"),
            record.get("concurrent_1"),
            record.get("concurrent_2"),
            record.get("status"),
            record.get("erreur_detail"),
        ),
    )
    conn.commit()
    conn.close()


def get_logs(
    limit: int = 50,
    status_filter: str | None = None,
    config_filter: str | None = None,
) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conditions: list[str] = []
    params: list = []

    if status_filter:
        conditions.append("status = ?")
        params.append(status_filter)
    if config_filter:
        conditions.append("config = ?")
        params.append(config_filter)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    cursor = conn.execute(
        f"SELECT * FROM logs {where} ORDER BY created_at DESC LIMIT ?",
        params,
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows
