"""Fix: /api/auth/accounts không được chặn HTTP vào Telegram RPC.

Trước đây khi cache nguội (ngay sau restart), mỗi account gọi
is_authorized()+get_me() với timeout 2s → 19 account treo trang Tài khoản
30-40s. Test source-level chống tái phát.
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "routes" / "auth.py").read_text(encoding="utf-8")


def test_list_accounts_has_no_blocking_telegram_calls():
    # Không còn gọi is_authorized/get_me trong đường trả response của list_accounts.
    block = SRC.split("async def list_accounts")[1].split("@router.post")[0]
    assert "await tg.is_authorized(" not in block
    assert "await tg.get_me(" not in block


def test_list_accounts_uses_cache_and_connection_state():
    block = SRC.split("async def list_accounts")[1].split("@router.post")[0]
    assert "tg._me_cache.get(" in block
    assert "is_connected()" in block


def test_no_asyncio_gather_over_telegram():
    block = SRC.split("async def list_accounts")[1].split("@router.post")[0]
    assert "asyncio.gather(" not in block
