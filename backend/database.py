"""
database.py - SQLite database module for the Instagram Auto-Reply Bot.

Manages three tables:
  - reel_configs      : Stores per-reel reply/DM configuration
  - reply_logs        : Audit log of every interaction the bot performed
  - processed_comments: Deduplication ledger so each comment is handled once
"""

import sqlite3
import logging
from datetime import datetime
from typing import Optional

from config import DATABASE_PATH

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Open a new SQLite connection with row_factory set to sqlite3.Row."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for better concurrent read performance
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all required tables if they do not already exist."""
    logger.info("Initialising database at: %s", DATABASE_PATH)
    conn = _get_conn()
    try:
        cursor = conn.cursor()

        # --- reel_configs ---------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reel_configs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                reel_id         TEXT    UNIQUE NOT NULL,
                shortcode       TEXT,
                thumbnail_url   TEXT,
                caption         TEXT,
                comment_reply   TEXT    DEFAULT '',
                dm_message      TEXT    DEFAULT '',
                is_enabled      INTEGER DEFAULT 0,
                taken_at        TEXT,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # --- reply_logs -----------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reply_logs (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                reel_id              TEXT    NOT NULL,
                commenter_username   TEXT,
                commenter_id         TEXT,
                comment_text         TEXT,
                comment_id           TEXT    UNIQUE,
                comment_reply_sent   INTEGER DEFAULT 0,
                dm_sent              INTEGER DEFAULT 0,
                error_message        TEXT,
                replied_at           TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # --- processed_comments ---------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_comments (
                comment_id   TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        conn.commit()
        logger.info("Database initialised successfully.")
    except sqlite3.Error as exc:
        logger.exception("Failed to initialise database: %s", exc)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# reel_configs helpers
# ---------------------------------------------------------------------------

def upsert_reel_config(
    reel_id: str,
    shortcode: str,
    thumbnail_url: str,
    caption: str,
    taken_at: str,
) -> None:
    """Insert a new reel or update its metadata columns if it already exists.

    Note: comment_reply, dm_message, and is_enabled are intentionally NOT
    overwritten on update so that user configuration is preserved.
    """
    now = datetime.utcnow().isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO reel_configs (reel_id, shortcode, thumbnail_url, caption, taken_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(reel_id) DO UPDATE SET
                shortcode     = excluded.shortcode,
                thumbnail_url = excluded.thumbnail_url,
                caption       = excluded.caption,
                taken_at      = excluded.taken_at,
                updated_at    = ?
            """,
            (reel_id, shortcode, thumbnail_url, caption, taken_at, now, now, now),
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.exception("upsert_reel_config failed for reel_id=%s: %s", reel_id, exc)
        raise
    finally:
        conn.close()


def get_reel_config(reel_id: str) -> Optional[sqlite3.Row]:
    """Return a single reel config row, or None if not found."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM reel_configs WHERE reel_id = ?", (reel_id,)
        ).fetchone()
        return row
    except sqlite3.Error as exc:
        logger.exception("get_reel_config failed for reel_id=%s: %s", reel_id, exc)
        raise
    finally:
        conn.close()


def update_reel_config(
    reel_id: str,
    comment_reply: str,
    dm_message: str,
    is_enabled: bool,
) -> bool:
    """Update the user-configurable fields for a reel.

    Returns True if a row was updated, False if reel_id was not found.
    """
    now = datetime.utcnow().isoformat()
    conn = _get_conn()
    try:
        cursor = conn.execute(
            """
            UPDATE reel_configs
               SET comment_reply = ?,
                   dm_message    = ?,
                   is_enabled    = ?,
                   updated_at    = ?
             WHERE reel_id = ?
            """,
            (comment_reply, dm_message, int(is_enabled), now, reel_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.Error as exc:
        logger.exception("update_reel_config failed for reel_id=%s: %s", reel_id, exc)
        raise
    finally:
        conn.close()


def get_all_reel_configs() -> list:
    """Return every row from reel_configs ordered by taken_at descending."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM reel_configs ORDER BY taken_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        logger.exception("get_all_reel_configs failed: %s", exc)
        raise
    finally:
        conn.close()


def get_enabled_reels() -> list:
    """Return only the reels that have auto-reply enabled."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM reel_configs WHERE is_enabled = 1 ORDER BY taken_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        logger.exception("get_enabled_reels failed: %s", exc)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# processed_comments helpers
# ---------------------------------------------------------------------------

def is_comment_processed(comment_id: str) -> bool:
    """Return True if comment_id has already been processed."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM processed_comments WHERE comment_id = ?", (comment_id,)
        ).fetchone()
        return row is not None
    except sqlite3.Error as exc:
        logger.exception("is_comment_processed failed for comment_id=%s: %s", comment_id, exc)
        raise
    finally:
        conn.close()


def mark_comment_processed(comment_id: str) -> None:
    """Insert comment_id into processed_comments; ignore if already present."""
    now = datetime.utcnow().isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO processed_comments (comment_id, processed_at) VALUES (?, ?)",
            (comment_id, now),
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.exception("mark_comment_processed failed for comment_id=%s: %s", comment_id, exc)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# reply_logs helpers
# ---------------------------------------------------------------------------

def add_reply_log(
    reel_id: str,
    commenter_username: str,
    commenter_id: str,
    comment_text: str,
    comment_id: str,
    comment_reply_sent: bool,
    dm_sent: bool,
    error_message: Optional[str] = None,
) -> None:
    """Append a new interaction record to reply_logs."""
    now = datetime.utcnow().isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO reply_logs
                (reel_id, commenter_username, commenter_id, comment_text,
                 comment_id, comment_reply_sent, dm_sent, error_message, replied_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reel_id,
                commenter_username,
                commenter_id,
                comment_text,
                comment_id,
                int(comment_reply_sent),
                int(dm_sent),
                error_message,
                now,
            ),
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.exception("add_reply_log failed for comment_id=%s: %s", comment_id, exc)
        raise
    finally:
        conn.close()


def get_logs(limit: int = 50, offset: int = 0) -> list:
    """Return paginated reply_logs joined with reel shortcode for display."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                rl.*,
                rc.shortcode,
                rc.thumbnail_url
            FROM reply_logs rl
            LEFT JOIN reel_configs rc ON rl.reel_id = rc.reel_id
            ORDER BY rl.replied_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        logger.exception("get_logs failed: %s", exc)
        raise
    finally:
        conn.close()


def get_logs_count() -> int:
    """Return the total number of rows in reply_logs."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM reply_logs").fetchone()
        return row["cnt"] if row else 0
    except sqlite3.Error as exc:
        logger.exception("get_logs_count failed: %s", exc)
        raise
    finally:
        conn.close()


def clear_logs() -> int:
    """Delete all rows from reply_logs. Returns the number of deleted rows."""
    conn = _get_conn()
    try:
        cursor = conn.execute("DELETE FROM reply_logs")
        conn.commit()
        logger.info("Cleared %d log entries.", cursor.rowcount)
        return cursor.rowcount
    except sqlite3.Error as exc:
        logger.exception("clear_logs failed: %s", exc)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stats helper
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    """Return aggregated statistics for the dashboard.

    Returns a dict with:
        total_interactions   - count of all log rows
        comment_replies_sent - count where comment_reply_sent = 1
        dms_sent             - count where dm_sent = 1
        enabled_reels        - count of reels with is_enabled = 1
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*)                                    AS total_interactions,
                SUM(CASE WHEN comment_reply_sent=1 THEN 1 ELSE 0 END) AS comment_replies_sent,
                SUM(CASE WHEN dm_sent=1 THEN 1 ELSE 0 END)            AS dms_sent
            FROM reply_logs
            """
        ).fetchone()

        enabled_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM reel_configs WHERE is_enabled = 1"
        ).fetchone()

        return {
            "total_interactions": row["total_interactions"] or 0,
            "comment_replies_sent": row["comment_replies_sent"] or 0,
            "dms_sent": row["dms_sent"] or 0,
            "enabled_reels": enabled_row["cnt"] if enabled_row else 0,
        }
    except sqlite3.Error as exc:
        logger.exception("get_stats failed: %s", exc)
        raise
    finally:
        conn.close()
