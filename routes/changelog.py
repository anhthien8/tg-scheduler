"""
Routes for Changelog & Release Notes feature.
Provides structured system update history for SaaS users.

Data lives in routes/changelog.json (newest first). It is re-read when the file's
mtime changes, so a new release note shows up without restarting the server.
"""
import json
import logging
import os

from fastapi import APIRouter

router = APIRouter(prefix="/api/changelog", tags=["Changelog"])
logger = logging.getLogger("tg-scheduler.changelog")

CHANGELOG_PATH = os.path.join(os.path.dirname(__file__), "changelog.json")
_cache: dict = {"mtime": None, "data": []}


def load_changelog() -> list[dict]:
    """Changelog entries, newest first. Cached until the JSON file changes on disk."""
    try:
        mtime = os.path.getmtime(CHANGELOG_PATH)
        if mtime != _cache["mtime"]:
            with open(CHANGELOG_PATH, encoding="utf-8") as f:
                _cache["data"] = json.load(f)
            _cache["mtime"] = mtime
    except Exception as e:  # keep serving the last good copy on a bad edit
        logger.error(f"[Changelog] Không đọc được {CHANGELOG_PATH}: {e}")
    return _cache["data"]


@router.get("")
async def get_changelog():
    data = load_changelog()
    return {
        "changelog": data,
        "latest_version": data[0]["version"] if data else "v1.0.0"
    }


@router.get("/latest")
async def get_latest_release():
    data = load_changelog()
    if not data:
        return {"version": "v1.0.0", "title": "Initial Release"}
    return data[0]
