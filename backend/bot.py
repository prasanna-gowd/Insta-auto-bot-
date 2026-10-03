"""
bot.py - Async polling bot for the Instagram Auto-Reply Bot.

The bot runs as a long-lived asyncio Task.  On each iteration it:
  1. Fetches all reels that have auto-reply enabled.
  2. Retrieves recent comments for each reel.
  3. Skips comments that have already been processed or belong to our own account.
  4. Posts a comment reply (if configured) and sends a DM (if configured).
  5. Logs every interaction to the database.
  6. Sleeps for POLL_INTERVAL seconds before the next cycle.

Public API:
  start_bot()  -> bool
  stop_bot()   -> None
  bot_status() -> bool
"""

import asyncio
import logging
import random
from typing import Optional

import instagram
import database
from config import POLL_INTERVAL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_bot_running: bool = False
_bot_task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# Core processing logic
# ---------------------------------------------------------------------------

async def process_reel_comments(reel_config: dict) -> None:
    """Fetch and process new comments for a single reel.

    For each unprocessed comment that does not belong to our own account:
      - Optionally send a public comment reply.
      - Optionally send a DM to the commenter.
      - Record the result in reply_logs.
      - Mark the comment as processed so it is never handled again.
    """
    reel_id = reel_config["reel_id"]
    comment_reply_template = reel_config.get("comment_reply", "").strip()
    dm_message_template = reel_config.get("dm_message", "").strip()

    logger.info("[Bot] Processing reel_id=%s (shortcode=%s)", reel_id, reel_config.get("shortcode"))

    # Determine our own username so we can skip self-comments
    own_username = instagram.get_current_username() or ""

    try:
        comments = instagram.get_comments(reel_id, limit=50)
    except Exception as exc:
        logger.error("[Bot] Failed to fetch comments for reel_id=%s: %s", reel_id, exc)
        return

    for comment in comments:
        if not _bot_running:
            logger.info("[Bot] Stop requested mid-reel; aborting.")
            return

        comment_id = comment["comment_id"]
        commenter_username = comment["username"]
        commenter_id = comment["user_id"]
        comment_text = comment["text"]

        # --- Deduplication check -------------------------------------------
        if database.is_comment_processed(comment_id):
            logger.debug("[Bot] Skipping already-processed comment_id=%s", comment_id)
            continue

        # --- Skip own account's comments ------------------------------------
        if own_username and commenter_username.lower() == own_username.lower():
            logger.debug("[Bot] Skipping own comment_id=%s", comment_id)
            database.mark_comment_processed(comment_id)
            continue

        logger.info(
            "[Bot] Handling comment_id=%s from @%s on reel_id=%s",
            comment_id, commenter_username, reel_id,
        )

        comment_reply_sent = False
        dm_sent = False
        error_message: Optional[str] = None

        # --- Comment reply --------------------------------------------------
        if comment_reply_template:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    instagram.reply_to_comment,
                    reel_id,
                    comment_id,
                    comment_reply_template,
                )
                comment_reply_sent = True
                logger.info("[Bot] Comment reply sent for comment_id=%s", comment_id)
            except Exception as exc:
                error_message = f"Comment reply error: {exc}"
                logger.error("[Bot] Comment reply failed for comment_id=%s: %s", comment_id, exc)

        # --- DM -------------------------------------------------------------
        if dm_message_template:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    instagram.send_dm,
                    commenter_id,
                    dm_message_template,
                )
                dm_sent = True
                logger.info("[Bot] DM sent to user_id=%s", commenter_id)
            except Exception as exc:
                dm_error = f"DM error: {exc}"
                error_message = (
                    f"{error_message}; {dm_error}" if error_message else dm_error
                )
                logger.error("[Bot] DM failed for user_id=%s: %s", commenter_id, exc)

        # --- Persist results ------------------------------------------------
        try:
            database.add_reply_log(
                reel_id=reel_id,
                commenter_username=commenter_username,
                commenter_id=commenter_id,
                comment_text=comment_text,
                comment_id=comment_id,
                comment_reply_sent=comment_reply_sent,
                dm_sent=dm_sent,
                error_message=error_message,
            )
            database.mark_comment_processed(comment_id)
        except Exception as exc:
            logger.exception("[Bot] Failed to persist log for comment_id=%s: %s", comment_id, exc)

        # --- Human-like inter-comment delay ---------------------------------
        if _bot_running:
            delay = random.uniform(5, 12)
            logger.debug("[Bot] Sleeping %.2fs between comments.", delay)
            await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Main bot loop
# ---------------------------------------------------------------------------

async def bot_loop() -> None:
    """The main async loop that drives the bot.

    Runs continuously while _bot_running is True.  On each cycle it processes
    every enabled reel then sleeps for POLL_INTERVAL seconds.
    """
    global _bot_running

    logger.info("[Bot] Loop started. Poll interval: %ds", POLL_INTERVAL)

    while _bot_running:
        if not instagram.is_logged_in():
            logger.warning("[Bot] Not logged in; pausing until next cycle.")
            await asyncio.sleep(POLL_INTERVAL)
            continue

        try:
            enabled_reels = database.get_enabled_reels()
            logger.info("[Bot] Processing %d enabled reel(s).", len(enabled_reels))

            for reel_config in enabled_reels:
                if not _bot_running:
                    break
                await process_reel_comments(reel_config)

        except Exception as exc:
            logger.exception("[Bot] Unexpected error in bot_loop: %s", exc)

        if _bot_running:
            logger.info("[Bot] Cycle complete. Sleeping %ds.", POLL_INTERVAL)
            await asyncio.sleep(POLL_INTERVAL)

    logger.info("[Bot] Loop exited cleanly.")


# ---------------------------------------------------------------------------
# Public control functions
# ---------------------------------------------------------------------------

def start_bot() -> bool:
    """Start the bot as an asyncio background Task.

    Returns True if the bot was successfully started, False if it was already
    running or if no event loop is available.
    """
    global _bot_running, _bot_task

    if _bot_running:
        logger.warning("[Bot] start_bot() called but bot is already running.")
        return False

    if not instagram.is_logged_in():
        logger.error("[Bot] Cannot start: not logged in to Instagram.")
        return False

    try:
        loop = asyncio.get_event_loop()
        _bot_running = True
        _bot_task = loop.create_task(bot_loop())
        logger.info("[Bot] Bot started successfully.")
        return True
    except RuntimeError as exc:
        _bot_running = False
        logger.exception("[Bot] Failed to start bot (no running event loop?): %s", exc)
        return False


def stop_bot() -> None:
    """Signal the bot loop to stop and cancel its asyncio Task."""
    global _bot_running, _bot_task

    logger.info("[Bot] Stop requested.")
    _bot_running = False

    if _bot_task and not _bot_task.done():
        _bot_task.cancel()
        logger.info("[Bot] Bot task cancelled.")

    _bot_task = None


def bot_status() -> bool:
    """Return True if the bot loop is currently active."""
    return _bot_running
