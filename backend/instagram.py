"""
instagram.py - instagrapi wrapper for the Instagram Auto-Reply Bot.

Provides a thin, stateful facade around instagrapi.Client so that the rest
of the application can interact with Instagram through simple function calls
rather than managing the client object directly.
"""

import json
import logging
import os
import random
import time
from datetime import datetime
from typing import Optional

from instagrapi import Client
from instagrapi.exceptions import (
    ClientError,
    ClientLoginRequired,
    LoginRequired,
    MediaNotFound,
    UserNotFound,
)

from config import SESSION_FILE, PROXY_URL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

cl = Client()
if PROXY_URL:
    try:
        cl.set_proxy(PROXY_URL)
        logger.info("Configured proxy: %s", PROXY_URL)
    except Exception as exc:
        logger.warning("Failed to set proxy %s: %s", PROXY_URL, exc)

_is_logged_in: bool = False


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def try_restore_session() -> bool:
    """Attempt to restore an existing session from SESSION_FILE or SESSION_DATA.
    
    Loads existing session cookies/settings without issuing a password login
    request, preventing Instagram 429 datacenter IP rate limits on cloud hosts.
    """
    global _is_logged_in, cl

    from config import SESSION_DATA, INSTAGRAM_USERNAME
    if SESSION_DATA and not os.path.exists(SESSION_FILE):
        try:
            import base64
            logger.info("Restoring session from SESSION_DATA env var...")
            try:
                decoded = base64.b64decode(SESSION_DATA.strip()).decode('utf-8')
            except Exception:
                decoded = SESSION_DATA.strip()
            with open(SESSION_FILE, 'w', encoding='utf-8') as f:
                f.write(decoded)
            logger.info("Saved SESSION_DATA to %s", SESSION_FILE)
        except Exception as exc:
            logger.warning("Failed to restore SESSION_DATA env var: %s", exc)

    if os.path.exists(SESSION_FILE):
        try:
            logger.info("Attempting to restore session from %s", SESSION_FILE)
            cl.load_settings(SESSION_FILE)
            if INSTAGRAM_USERNAME and not getattr(cl, 'username', None):
                cl.username = INSTAGRAM_USERNAME
            if cl.user_id:
                _is_logged_in = True
                logger.info("Session restored successfully for user_id=%s!", cl.user_id)
                return True
            else:
                logger.warning("Session file loaded but user_id is missing.")
        except Exception as exc:
            logger.warning("Failed to restore session from file: %s", exc)

    _is_logged_in = False
    return False


def login(username: str, password: str) -> bool:
    """Attempt to log in to Instagram.

    Strategy:
    1. Try loading an existing session from SESSION_FILE / SESSION_DATA.
    2. Only if no session file exists or it's unreadable, perform fresh login.
    """
    global _is_logged_in, cl

    if try_restore_session():
        return True

    # ---- fresh login -------------------------------------------------------
    try:
        logger.info("Performing fresh login for user: %s", username)
        cl.login(username, password)
        cl.dump_settings(SESSION_FILE)
        _is_logged_in = True
        logger.info("Fresh login successful for user: %s", username)
        return True
    except Exception as exc:
        _is_logged_in = False
        logger.exception("Login failed for user %s: %s", username, exc)
        err_str = str(exc)
        if "429" in err_str:
            raise RuntimeError(
                "Instagram Rate Limit (Error 429): Instagram throttled login from cloud server. "
                "Session file restored."
            ) from exc
        raise


def logout() -> bool:
    """Log out from Instagram and remove the session file.

    Returns True on success.
    """
    global _is_logged_in
    try:
        cl.logout()
        logger.info("Logged out from Instagram.")
    except Exception as exc:
        logger.warning("Logout call raised an exception (continuing): %s", exc)
    finally:
        _is_logged_in = False
        if os.path.exists(SESSION_FILE):
            try:
                os.remove(SESSION_FILE)
                logger.info("Session file removed: %s", SESSION_FILE)
            except OSError as exc:
                logger.warning("Could not remove session file: %s", exc)
    return True


def is_logged_in() -> bool:
    """Return True if the client is currently authenticated."""
    return _is_logged_in


def get_current_username() -> Optional[str]:
    """Return the authenticated account's username, or None if not logged in."""
    if not _is_logged_in:
        return None
    try:
        if getattr(cl, 'username', None):
            return cl.username
        return cl.account_info().username
    except Exception as exc:
        logger.warning("get_current_username failed: %s", exc)
        from config import INSTAGRAM_USERNAME
        return INSTAGRAM_USERNAME or "budget.addaa"


def get_client() -> Client:
    """Return the underlying instagrapi Client instance."""
    return cl


# ---------------------------------------------------------------------------
# Reel / Media helpers
# ---------------------------------------------------------------------------

def get_user_reels(count: int = 20) -> list:
    """Fetch the authenticated user's most recent reels.

    Uses cl.user_clips() and maps each result to a plain dict so callers are
    not coupled to instagrapi's internal model objects.

    Returns a list of dicts:
        reel_id, shortcode, thumbnail_url, caption, taken_at,
        comment_count, like_count
    """
    global _is_logged_in
    if not _is_logged_in:
        raise RuntimeError("Not logged in to Instagram.")

    try:
        user_id = cl.user_id
        logger.info("Fetching up to %d reels for user_id=%s", count, user_id)
        clips = cl.user_clips(user_id, amount=count)
        reels = []
        for media in clips:
            thumbnail = ""
            if media.thumbnail_url:
                thumbnail = str(media.thumbnail_url)
            elif media.image_versions2 and media.image_versions2.get("candidates"):
                thumbnail = media.image_versions2["candidates"][0].get("url", "")

            caption_text = ""
            if media.caption_text:
                caption_text = media.caption_text

            taken_at_str = ""
            if media.taken_at:
                if isinstance(media.taken_at, datetime):
                    taken_at_str = media.taken_at.isoformat()
                else:
                    taken_at_str = str(media.taken_at)

            reels.append(
                {
                    "reel_id": str(media.pk),
                    "shortcode": media.code or "",
                    "thumbnail_url": thumbnail,
                    "caption": caption_text,
                    "taken_at": taken_at_str,
                    "comment_count": media.comment_count or 0,
                    "like_count": media.like_count or 0,
                }
            )
        logger.info("Fetched %d reels.", len(reels))
        return reels
    except ClientLoginRequired as exc:
        _is_logged_in = False
        logger.error("Session expired while fetching reels: %s", exc)
        raise
    except Exception as exc:
        logger.exception("get_user_reels failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Comment helpers
# ---------------------------------------------------------------------------

def get_comments(media_id: str, limit: int = 50) -> list:
    """Return recent comments on a given media item.

    Each item in the returned list is a dict:
        comment_id, user_id, username, text, created_at
    """
    global _is_logged_in
    if not _is_logged_in:
        raise RuntimeError("Not logged in to Instagram.")

    try:
        logger.debug("Fetching comments for media_id=%s (limit=%d)", media_id, limit)
        raw_comments = cl.media_comments(media_id, amount=limit)
        comments = []
        for c in raw_comments:
            created_at_str = ""
            if c.created_at_utc:
                if isinstance(c.created_at_utc, datetime):
                    created_at_str = c.created_at_utc.isoformat()
                else:
                    created_at_str = str(c.created_at_utc)

            comments.append(
                {
                    "comment_id": str(c.pk),
                    "user_id": str(c.user.pk),
                    "username": c.user.username,
                    "text": c.text,
                    "created_at": created_at_str,
                }
            )
        logger.debug("Fetched %d comments for media_id=%s", len(comments), media_id)
        return comments
    except MediaNotFound:
        logger.warning("Media not found for media_id=%s", media_id)
        return []
    except ClientLoginRequired as exc:
        _is_logged_in = False
        logger.error("Session expired while fetching comments: %s", exc)
        raise
    except Exception as exc:
        logger.exception("get_comments failed for media_id=%s: %s", media_id, exc)
        raise


def reply_to_comment(media_id: str, comment_id: str, reply_text: str) -> bool:
    """Post a reply to a specific comment.

    Introduces a random 2-5 second delay before posting to mimic human
    behaviour and reduce the risk of rate-limiting.

    Returns True on success.
    """
    global _is_logged_in
    if not _is_logged_in:
        raise RuntimeError("Not logged in to Instagram.")

    delay = random.uniform(2, 5)
    logger.debug(
        "Waiting %.2fs before replying to comment_id=%s on media_id=%s",
        delay, comment_id, media_id,
    )
    time.sleep(delay)

    try:
        cl.media_comment(media_id, reply_text, replied_to_comment_id=comment_id)
        logger.info(
            "Replied to comment_id=%s on media_id=%s", comment_id, media_id
        )
        return True
    except ClientLoginRequired as exc:
        _is_logged_in = False
        logger.error("Session expired while replying to comment: %s", exc)
        raise
    except Exception as exc:
        logger.exception(
            "reply_to_comment failed for comment_id=%s: %s", comment_id, exc
        )
        raise


# ---------------------------------------------------------------------------
# DM helpers
# ---------------------------------------------------------------------------

def send_dm(user_id: str, message: str) -> bool:
    """Send a direct message to a user.

    Introduces a random 3-7 second delay before sending.

    Returns True on success.
    """
    global _is_logged_in
    if not _is_logged_in:
        raise RuntimeError("Not logged in to Instagram.")

    delay = random.uniform(3, 7)
    logger.debug("Waiting %.2fs before sending DM to user_id=%s", delay, user_id)
    time.sleep(delay)

    try:
        cl.direct_send(message, user_ids=[int(user_id)])
        logger.info("DM sent to user_id=%s", user_id)
        return True
    except UserNotFound:
        logger.warning("User not found for DM: user_id=%s", user_id)
        raise
    except ClientLoginRequired as exc:
        _is_logged_in = False
        logger.error("Session expired while sending DM: %s", exc)
        raise
    except Exception as exc:
        logger.exception("send_dm failed for user_id=%s: %s", user_id, exc)
        raise
