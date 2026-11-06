"""Pure helpers for the TG Scheduler MCP server (no I/O, unit-tested).

- account_health(): same rules as Engagement._accStatus (static/js/engagement.js)
  plus the SpamBot "limited" downgrade used by the DM-campaign account picker.
- filter_members(): split a scrape job into kept / excluded targets with a reason.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

# Fields of /api/auth/accounts that must never reach an AI client.
_SECRET_ACCOUNT_FIELDS = {"api_hash", "api_id", "proxy_url", "session_name", "phone"}


def account_health(acc: dict, spam: dict | None = None, now: float | None = None) -> dict:
    """{ok, label, detail} — first failing rule wins, like the dashboard."""
    now = time.time() if now is None else now
    try:
        pf_ts = float(acc.get("peerflood_until") or 0)
    except (TypeError, ValueError):
        pf_ts = 0.0
    if acc.get("session_error"):
        return {"ok": False, "label": "Lỗi session", "detail": str(acc["session_error"])[:200]}
    if not acc.get("is_logged_in"):
        return {"ok": False, "label": "Offline", "detail": ""}
    if acc.get("is_paused"):
        return {"ok": False, "label": "Tạm dừng", "detail": acc.get("pause_reason") or ""}
    if acc.get("is_flagged"):
        return {"ok": False, "label": "Bị flag", "detail": acc.get("flag_reason") or ""}
    if pf_ts > now:
        until = datetime.fromtimestamp(pf_ts, tz=timezone.utc).isoformat(timespec="minutes")
        return {"ok": False, "label": "PeerFlood", "detail": f"Hết hạn {until} UTC"}
    if spam and spam.get("status") == "limited":
        return {"ok": False, "label": "SpamBot giới hạn",
                "detail": f"kiểm tra lúc {spam.get('checked_at') or '?'}"}
    return {"ok": True, "label": "Hoạt động", "detail": ""}


def public_account(acc: dict, spam: dict | None = None, now: float | None = None) -> dict:
    """Compact, secret-free view of one account."""
    info = acc.get("user_info") or {}
    out = {
        "id": acc.get("id"),
        "name": acc.get("name") or info.get("first_name") or f"#{acc.get('id')}",
        "username": info.get("username"),
        "telegram_user_id": acc.get("telegram_user_id"),
        "is_premium": bool(acc.get("is_premium")),
        "health": account_health(acc, spam, now),
        "spambot": (spam or {}).get("status") or "chưa kiểm tra",
        "spambot_checked_at": (spam or {}).get("checked_at"),
    }
    assert not (_SECRET_ACCOUNT_FIELDS & out.keys())
    return out


def internal_identity(accounts: list[dict]) -> tuple[set[int], set[str]]:
    """Telegram user_ids and usernames (lowercase) of our own accounts."""
    ids: set[int] = set()
    names: set[str] = set()
    for a in accounts:
        for v in (a.get("telegram_user_id"), (a.get("user_info") or {}).get("id")):
            try:
                if v:
                    ids.add(int(v))
            except (TypeError, ValueError):
                pass
        u = (a.get("user_info") or {}).get("username")
        if u:
            names.add(u.lower().lstrip("@"))
    return ids, names


def _display(m: dict) -> str:
    return " ".join(x for x in (m.get("first_name"), m.get("last_name")) if x).strip()


def filter_members(
    members: list[dict],
    *,
    internal_ids: set[int] = frozenset(),
    internal_usernames: set[str] = frozenset(),
    exclude_keywords: list[str] | None = None,
    exclude_user_ids: list[int] | None = None,
    exclude_usernames: list[str] | None = None,
    require_username: bool = False,
    only_premium: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Return (kept, excluded); every excluded row gets a `reason`.

    Admins/owner are already dropped by the scrape itself (routes/members.py
    fetches ChannelParticipantsAdmins first); bots are dropped there too, this
    re-checks `is_bot` / "...bot" usernames as a second layer.
    """
    kws = [k.strip().lower() for k in (exclude_keywords or []) if k and k.strip()]
    ex_ids = {int(x) for x in (exclude_user_ids or [])}
    ex_names = {u.strip().lower().lstrip("@") for u in (exclude_usernames or []) if u and u.strip()}
    kept: list[dict] = []
    excluded: list[dict] = []
    for m in members:
        uid = m.get("user_id")
        uname = (m.get("username") or "").lower()
        name = _display(m).lower()
        reason = None
        if m.get("is_bot") or uname.endswith("bot"):
            reason = "bot"
        elif uid in internal_ids or (uname and uname in internal_usernames):
            reason = "account nội bộ"
        elif uid in ex_ids or (uname and uname in ex_names):
            reason = "loại thủ công"
        else:
            hit = next((k for k in kws if k in uname or k in name), None)
            if hit:
                reason = f"chứa từ khóa '{hit}'"
            elif require_username and not uname:
                reason = "không có username"
            elif only_premium and not m.get("is_premium"):
                reason = "không Premium"
        (excluded if reason else kept).append(m if not reason else {**m, "reason": reason})
    return kept, excluded


def compact_member(m: dict) -> dict:
    out = {
        "user_id": m.get("user_id"),
        "username": ("@" + m["username"]) if m.get("username") else None,
        "name": _display(m) or None,
        "premium": bool(m.get("is_premium")),
        "last_seen": m.get("last_seen"),
    }
    if m.get("reason"):
        out["reason"] = m["reason"]
    return out
