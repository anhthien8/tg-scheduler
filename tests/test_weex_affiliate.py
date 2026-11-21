"""WEEX sub-affiliate monthly tier-up: ladder mapping, upgrade-only planning, payload, approval flow."""
from datetime import date

import pytest

import database as db
import kol_offer
import weex_affiliate as wx

LADDER = kol_offer.DEFAULT_LADDER


def _agent(uid, rate, name="kol"):
    return {"id": 1, "agent_user_id": int(uid), "agent_name": name, "account": name, "agent_level": 1,
            "channel_code": "CSS2026", "rebate_notice": True, "rebate_rate": str(rate),
            "spot_rebate_rate": "0.5", "trace_user_rebate_rate": "0"}


def test_tier_for_volume_thresholds():
    assert wx.tier_for_volume(0, LADDER)["tier"] == 1
    assert wx.tier_for_volume(9_999_999, LADDER)["tier"] == 1
    assert wx.tier_for_volume(10_000_000, LADDER)["tier"] == 2
    assert wx.tier_for_volume(15_275_724, LADDER)["tier"] == 2
    assert wx.tier_for_volume(250_000_000, LADDER)["tier"] == 5


def test_plan_upgrades_only_raises_never_lowers():
    agents = [_agent(1, 0.55), _agent(2, 0.8), _agent(3, 0), _agent(4, 0.6)]
    vols = {"1": 21e6, "2": 0, "4": 12e6}
    plans = {p["agent_uid"]: p for p in wx.plan_upgrades(agents, vols, LADDER)}
    assert plans["1"]["new_rate"] == 0.65 and plans["1"]["tier"] == 3
    assert "2" not in plans            # 80% with no volume: no downgrade
    assert plans["3"]["new_rate"] == 0.55  # tier 1 floor for a 0% sub-affiliate
    assert "4" not in plans            # already at tier 2 rate


def test_update_payload_replaces_only_futures_rate():
    body = wx.update_payload(_agent(8916360467, 0.6, "lexakrm"), 0.65)
    assert body["rebateRate"] == 0.65
    assert body["spotRebateRate"] == 0.5
    assert body["agentUserId"] == 8916360467 and body["agentName"] == "lexakrm"
    assert body["id"] == 1 and body["rebateNotice"] is True
    # exact key set the portal form posts (captured from a real edit)
    assert set(body) == {"spotApiTraceUserRebateRate", "spotApiRebateRate", "spotTraceUserRebateRate",
                         "spotRebateRate", "apiTraceUserRebateRate", "apiRebateRate", "traceUserRebateRate",
                         "rebateRate", "agentUserId", "account", "agentName", "channelCode", "relBdAccountId",
                         "agentType", "agentLevel", "rebateNotice", "id"}


def test_previous_month():
    assert wx.previous_month(date(2026, 12, 1)) == ("2026-11", "2026-11-01", "2026-11-30")
    assert wx.previous_month(date(2026, 1, 15)) == ("2025-12", "2025-12-01", "2025-12-31")


@pytest.fixture
async def fake_portal(monkeypatch):
    state = {"rate": 0.55, "posted": []}

    async def fake_request(method, path, *, params=None, json=None):
        if path == "/agent/list":
            return {"code": 200, "data": {"total": 1, "records": [_agent(42, state["rate"], "alice")]}}
        if path == "/rankings/list":
            return {"code": 0, "count": 1, "data": [{"userId": 42, "totalAmount": "21000000"}]}
        if path == "/agent/update":
            state["posted"].append(json)
            state["rate"] = json["rebateRate"]
            return {"success": True}
        raise AssertionError(path)

    monkeypatch.setattr(wx, "_request", fake_request)
    sent = []

    async def fake_send(text, month, ids):
        sent.append((month, ids))
        return True

    import command_bot
    monkeypatch.setattr(command_bot, "send_weex_tier_review", fake_send)
    state["sent"] = sent
    return state


async def test_review_disabled_by_default(fake_portal):
    await db.set_setting(wx.ENABLED_KEY, "0")
    assert await wx.run_monthly_review(date(2026, 12, 1)) == []
    assert fake_portal["posted"] == []


async def test_review_queues_then_approve_applies_once(fake_portal):
    await db.set_setting(wx.ENABLED_KEY, "1")
    rows = await wx.run_monthly_review(date(2026, 12, 1))
    assert len(rows) == 1 and rows[0]["new_rate"] == 0.65
    assert fake_portal["posted"] == []              # nothing written before approval
    assert fake_portal["sent"] == [("2026-11", [rows[0]["id"]])]
    assert await wx.run_monthly_review(date(2026, 12, 1)) == []  # rerun: no duplicate row

    msg = await wx.approve(rows[0]["id"])
    assert msg.startswith("✅") and fake_portal["rate"] == 0.65
    assert (await wx.get_upgrade(rows[0]["id"]))["status"] == "applied"
    assert "không còn" in await wx.approve(rows[0]["id"])  # second tap is a no-op
    assert len(fake_portal["posted"]) == 1


async def test_approve_refuses_when_rate_changed_meanwhile(fake_portal):
    await db.set_setting(wx.ENABLED_KEY, "1")
    rows = await wx.run_monthly_review(date(2026, 12, 1))
    fake_portal["rate"] = 0.7  # someone edited it on the portal
    msg = await wx.approve(rows[0]["id"])
    assert msg.startswith("❌") and fake_portal["posted"] == []
    assert (await wx.get_upgrade(rows[0]["id"]))["status"] == "failed"


async def test_reject(fake_portal):
    await db.set_setting(wx.ENABLED_KEY, "1")
    rows = await wx.run_monthly_review(date(2026, 12, 1))
    assert "Bỏ qua" in await wx.reject(rows[0]["id"])
    assert await wx.pending_ids("2026-11") == []


# ── Onboarding on UID ───────────────────────────────────────────────────────

@pytest.fixture
async def onboard_env(monkeypatch):
    st = {"agents": [], "added": [], "updated": [], "admin": [], "profile": {}, "exchange": "WEEX"}

    async def fake_request(method, path, *, params=None, json=None):
        if path == "/agent/list":
            recs = [a for a in st["agents"] if not params.get("agentUserId")
                    or str(a["agent_user_id"]) == params["agentUserId"]]
            return {"code": 200, "data": {"total": len(recs), "records": recs}}
        if path == "/agent/add":
            st["added"].append(json)
            return {"success": True, "data": {"account": json["account"], "pwd": "x1"}}
        if path == "/agent/update":
            st["updated"].append(json)
            return {"success": True}
        raise AssertionError(path)

    async def fake_profile(a, u):
        return st["profile"]

    async def fake_exchange(a, u=None):
        return st["exchange"]

    async def fake_admin(text):
        st["admin"].append(text)
        return True

    import command_bot
    monkeypatch.setattr(wx, "_request", fake_request)
    monkeypatch.setattr(db, "get_kol_profile", fake_profile)
    monkeypatch.setattr(db, "get_chat_exchange", fake_exchange)
    monkeypatch.setattr(command_bot, "send_admin_text", fake_admin)
    st["cards"], st["kol"] = [], []

    async def fake_approval(text, upgrade_id, ask_volume=False):
        st["cards"].append({"text": text, "id": upgrade_id, "ask_volume": ask_volume})
        return True

    async def fake_notify(account_id, user_id, text):
        st["kol"].append(text)
        return True

    async def fake_vi(account_id, user_id):
        return True

    monkeypatch.setattr(command_bot, "send_approval", fake_approval)
    monkeypatch.setattr(wx, "notify_kol", fake_notify)
    monkeypatch.setattr(wx, "_chat_is_vietnamese", fake_vi)
    await db.set_setting(wx.ENABLED_KEY, "1")
    return st


async def test_big_kol_added_to_css_line_at_tier1_and_kol_told(onboard_env):
    onboard_env["profile"] = {"monthly_volume": "50M", "username": "big_whale"}
    await wx.onboard_uid(1, 2, "123456789")
    body = onboard_env["added"][0]
    assert body["agentUserId"] == 123456789 and body["rebateRate"] == 0.55
    assert body["account"] == "bigwhale" and body["channelCode"] == "CSS2026"
    assert "KOL lớn" in onboard_env["admin"][0] and "Đã báo KOL" in onboard_env["admin"][0]
    assert "55%" in onboard_env["kol"][0] and "x1" in onboard_env["kol"][0]  # portal login handed to the KOL


async def test_big_kol_already_on_line_is_raised_not_duplicated(onboard_env):
    onboard_env["profile"] = {"monthly_volume": "30M"}
    onboard_env["agents"] = [_agent(123456789, 0.3)]
    await wx.onboard_uid(1, 2, "123456789")
    assert onboard_env["added"] == [] and onboard_env["updated"][0]["rebateRate"] == 0.55
    assert "Pass" not in onboard_env["kol"][0]  # existing account: no new login to send


async def test_small_kol_gets_approval_at_volume_tier(onboard_env):
    onboard_env["profile"] = {"monthly_volume": "12M"}
    await wx.onboard_uid(1, 2, "123456789")
    card = onboard_env["cards"][0]
    assert onboard_env["added"] == [] and "60%" in card["text"] and not card["ask_volume"]


async def test_unknown_volume_gets_tier1_approval_plus_ask_volume(onboard_env):
    await wx.onboard_uid(1, 2, "123456789")
    card = onboard_env["cards"][0]
    assert "55%" in card["text"] and card["ask_volume"] and onboard_env["added"] == []


async def test_ask_volume_then_volume_remakes_card(onboard_env):
    await wx.onboard_uid(1, 2, "123456789")
    first = onboard_env["cards"][0]["id"]
    assert "📊" in await wx.ask_volume(first)
    assert "volume" in onboard_env["kol"][0]
    assert (await wx.get_upgrade(first))["status"] == "awaiting_volume"
    onboard_env["profile"] = {"monthly_volume": "25M"}
    await wx.on_volume_stated(1, 2)  # big now → normal path: auto create + tell KOL
    assert onboard_env["added"][0]["rebateRate"] == 0.55 and await wx.get_upgrade(first) is None


async def test_onboard_skips_other_exchanges_and_when_disabled(onboard_env):
    onboard_env["profile"] = {"monthly_volume": "50M"}
    onboard_env["exchange"] = "BloFin"
    await wx.onboard_uid(1, 2, "123456789")
    onboard_env["exchange"] = "WEEX"
    await db.set_setting(wx.ENABLED_KEY, "0")
    await wx.onboard_uid(1, 2, "123456789")
    assert onboard_env["added"] == [] and onboard_env["admin"] == [] and onboard_env["cards"] == []


# ── Connect route ───────────────────────────────────────────────────────────

def _jwt(exp):
    import base64
    import json as _j
    seg = base64.urlsafe_b64encode(_j.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"h.{seg}.s"


async def test_connect_saves_valid_token_and_rolls_back_rejected(monkeypatch):
    import time
    from fastapi import HTTPException
    from routes import weex_connect as wc

    ok = {"v": True}

    async def fake_list(uid=""):
        if not ok["v"]:
            raise wx.WeexAuthError("bad")
        return []

    monkeypatch.setattr(wx, "list_agents", fake_list)
    good = _jwt(int(time.time()) + 86400)
    st = await wc.save_token(wc.TokenPayload(token=f'"{good}"'))
    assert st["connected"] and await db.get_setting(wx.TOKEN_KEY) == good

    ok["v"] = False
    with pytest.raises(HTTPException):
        await wc.save_token(wc.TokenPayload(token=_jwt(int(time.time()) + 999)))
    assert await db.get_setting(wx.TOKEN_KEY) == good  # rejected token not kept

    with pytest.raises(HTTPException):
        await wc.save_token(wc.TokenPayload(token=_jwt(int(time.time()) - 10)))  # expired


async def test_push_requires_connect_secret(monkeypatch):
    import time
    from fastapi import HTTPException
    from routes import weex_connect as wc

    async def fake_list(uid=""):
        return []

    monkeypatch.setattr(wx, "list_agents", fake_list)
    sec = (await wc.connect_secret())["secret"]
    assert (await wc.connect_secret())["secret"] == sec  # stable
    tok = _jwt(int(time.time()) + 3600)
    with pytest.raises(HTTPException):
        await wc.push_token(wc.PushPayload(token=tok, secret="wrong"))
    assert (await wc.push_token(wc.PushPayload(token=tok, secret=sec)))["connected"]


def test_push_preflight_allows_weex_origin_with_private_network():
    from fastapi.testclient import TestClient
    import main
    r = TestClient(main.app).options("/api/weex/push", headers={
        "Origin": "https://affiliates.weex.com", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type", "Access-Control-Request-Private-Network": "true"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://affiliates.weex.com"
    assert r.headers.get("access-control-allow-private-network") == "true"
    r2 = TestClient(main.app).options("/api/weex/push", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert r2.headers.get("access-control-allow-origin") is None


async def test_lookup_uid_found_and_missing(monkeypatch):
    row = {"user_id": 5808937626, "agent_name": "Michael", "register_time": "2026-11-18 10:00:00",
           "first_trade_time": "-", "first_chain_deposit_time": "2026-11-18 11:00:00", "kyc_status": "否",
           "superior_agent_tree": [{"agentName": "CSS", "agentUserId": 9811555540}, {"agentName": "Michael", "agentUserId": 1}]}
    seen = {}

    async def fake_request(method, path, *, params=None, json=None):
        seen.update(params)
        return {"code": 0, "data": [row] if params["userId"] == "5808937626" else []}

    monkeypatch.setattr(wx, "_request", fake_request)
    got = await wx.lookup_uid("5808937626")
    assert seen["userId"] == "5808937626" and "role" not in seen and seen["showType"] == 1
    text = wx.format_uid("5808937626", got)
    assert "Michael" in text and "Đã nạp: Có" in text and "Đã trade: Chưa" in text
    assert "CSS (9811555540) → Michael (1)" in text and "KYC: Chưa" in text
    assert "không nằm dưới line" in wx.format_uid("1", await wx.lookup_uid("1"))


async def test_agreed_deal_waits_for_admin_approval_then_creates_and_tells_kol(onboard_env):
    onboard_env["profile"] = {"current_deal": "80% revshare (base fee 0.02% Maker)", "username": "Facific9x"}
    await wx.onboard_uid(3, 1336533511, "2249716719")
    cards = onboard_env["cards"]
    assert onboard_env["added"] == [] and len(cards) == 1       # nothing written before the tap
    assert "80%" in cards[0]["text"] and "Tier 5" in cards[0]["text"]
    await wx.onboard_uid(3, 1336533511, "2249716719")
    assert len(cards) == 1                                       # re-trigger: no duplicate card
    msg = await wx.approve(cards[0]["id"])
    assert msg.startswith("✅") and "Đã báo KOL" in msg
    assert onboard_env["added"][0]["rebateRate"] == 0.8
    assert "80%" in onboard_env["kol"][0] and "User: Facific9x" in onboard_env["kol"][0]


async def test_off_ladder_deal_proposes_highest_tier_below(onboard_env):
    onboard_env["profile"] = {"current_deal": "85% revshare"}
    await wx.onboard_uid(3, 9, "123")
    card = onboard_env["cards"][0]["text"]
    assert "ngoài bảng bậc" in card and "80%" in card and onboard_env["added"] == []
