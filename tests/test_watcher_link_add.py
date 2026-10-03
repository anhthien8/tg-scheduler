"""v3.7.10 — thêm nhóm bằng link (group Topic lớn không hiện trong dialogs)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TG = (ROOT / "telegram_client.py").read_text(encoding="utf-8")
ROUTES = (ROOT / "routes" / "watchers.py").read_text(encoding="utf-8")
JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def test_resolve_chat_link_exists():
    assert "async def resolve_chat_link(" in TG
    assert '"is_forum"' in TG


def test_resolve_link_endpoint():
    assert '@router.post("/resolve-link")' in ROUTES
    assert "resolve_chat_link" in ROUTES
    assert "join_channel" in ROUTES


def test_ui_link_box_present():
    assert 'id="w-group-link"' in HTML
    assert 'id="w-link-btn"' in HTML
    assert "resolveWatcherGroupLink" in JS


def test_ui_only_accepts_group_or_supergroup():
    # Client chặn channel/bot khi dán link (giống filter picker).
    assert "chỉ nhận group/supergroup" in JS
