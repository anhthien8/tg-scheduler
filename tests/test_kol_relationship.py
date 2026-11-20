"""WEEX BD training 2026-11-20 (relationship-driven deal sourcing): KOL archetypes,
show-the-money comparison, account care list and BD scorecard."""
from datetime import date

import pytest

import database as db
import kol_archetype as ka
import kol_care as kc
import kol_offer as k


@pytest.fixture(autouse=True)
def _fresh():
    kc._tables_ready = False  # tests drop tables between runs


# ── A. Archetypes ────────────────────────────────────────────────────────────

def test_normalize_aliases():
    assert ka.normalize("Signal Provider") == "signal"
    assert ka.normalize("content-kol") == "content"
    assert ka.normalize("Key trader") == "trader"
    assert ka.normalize("intermediary") == "connector"
    assert ka.normalize("astronaut") is None and ka.normalize("") is None


def test_opening_rule_per_type():
    assert "ASK, DON'T TELL" in ka.opening_rule("trader")
    assert "SHOW THE MONEY" in ka.opening_rule("content")
    assert "[HANDOVER_REQUIRED]" in ka.opening_rule("connector")
    assert "community" in ka.opening_rule("signal").lower()
    assert ka.opening_rule(None) == "" and ka.opening_rule("nope") == ""


def test_manual_pick_is_never_overwritten_by_ai():
    assert ka.merge_extracted({"archetype": "Trader", "platform": "X"}, {}) == {
        "archetype": "trader", "archetype_source": "ai", "platform": "X"}
    assert ka.merge_extracted({"archetype": "content"}, {"archetype": "signal", "archetype_source": "manual"}) == {}
    assert ka.merge_extracted({"archetype": "??"}, {}) == {}


def test_extractor_prompt_asks_for_type_and_rate():
    import inspect

    import ai_remix
    src = inspect.getsource(ai_remix.extract_kol_profile)
    assert "current_rebate_pct" in src and "EXTRACT_HINT" in src


async def test_admin_pick_marks_manual(client):
    acc = await db.create_account({"name": "Nick", "phone": "+840000000009", "api_id": 1, "api_hash": "h", "session_name": "s9"})
    await db.get_or_create_followup_chat(account_id=acc, user_id=5, username="k", name="K")
    r = client.post(f"/api/ai-followup/chats/{acc}/5/profile", json={"archetype": "Signal provider"})
    assert r.status_code == 200
    p = await db.get_kol_profile(acc, 5)
    assert p["archetype"] == "signal" and p["archetype_source"] == "manual"


# ── B. Show the money ────────────────────────────────────────────────────────

def test_money_comparison_numbers():
    c = k.money_comparison(None, "50M", "40%")
    # 50M at 0.08% = 40,000 fees; 40% = 16,000 vs Tier 4 (70%) = 28,000.
    r0 = c["rows"][0]
    assert (r0["volume_m"], r0["theirs"], r0["ours"], r0["tier"]) == (50, 16000, 28000, 4)
    assert c["multiple"] == 1.8
    assert [r["volume_m"] for r in c["rows"]] == [50, 75, 100]
    assert c["rows"][2]["tier"] == 5  # 100M reaches the top tier


def test_money_comparison_skips_when_unknown_or_not_better():
    assert k.money_comparison(None, None, 40) is None
    assert k.money_comparison(None, "50M", None) is None
    assert k.money_comparison(None, "5M", "90%") is None  # their deal pays more: no flattering table


def test_prompt_and_guard_allow_comparison_figures():
    prompt = k.render_offer_ladder_prompt(None, "50M", None, "40")
    assert "SHOW THE MONEY" in prompt and "$16,000" in prompt and "$28,000" in prompt
    assert "VALUE ESTIMATE and SHOW THE MONEY blocks" in prompt
    assert "SHOW THE MONEY" not in k.render_offer_ladder_prompt(None, "50M")


async def test_offer_block_guard_passes_their_rate_and_dollars(monkeypatch):
    async def ladder():
        return k.DEFAULT_LADDER
    monkeypatch.setattr(k, "get_ladder", ladder)
    block, L = await k.offer_block_for("WEEX", "50M", current_pct="40")
    assert "SHOW THE MONEY" in block
    assert k.find_off_ladder_figures("You get 40% now, about $16,000; with us about $28,000/month", L) == []
    assert k.find_off_ladder_figures("about $31,337/month", L) == ["$31,337"]


# ── C. Account care ──────────────────────────────────────────────────────────

def test_country_code_from_profile_and_language():
    assert kc.country_code({"kol_country": "Based in Jakarta, Indonesia"}) == "ID"
    assert kc.country_code({"community_country": "Türkiye"}) == "TR"
    assert kc.country_code({}, [{"role": "user", "content": "Chào anh, dạo này thị trường khó quá"}]) == "VN"
    assert kc.country_code({}, [{"role": "user", "content": "hello"}]) is None


def test_festival_window():
    assert kc.festival_for("PH", date(2026, 12, 23))["name"] == "Christmas"
    assert kc.festival_for("VN", date(2026, 12, 23)) is None          # no Christmas greeting for VN
    assert kc.festival_for("VN", date(2027, 2, 4))["name"] == "Lunar New Year"
    assert kc.festival_for("ID", date(2027, 3, 11))["name"] == "Eid al-Fitr"
    assert kc.festival_for("TH", date(2027, 1, 1))["name"] == "New Year"   # everyone
    assert kc.festival_for(None, date(2026, 12, 31))["name"] == "New Year"
    assert kc.festival_for("TH", date(2027, 4, 20)) is None


def test_review_numbers():
    ladder = k.DEFAULT_LADDER
    r = kc.review_numbers([("2026-09", 8_000_000), ("2026-10", 10_000_000)], ladder)
    assert r["change_pct"] == 25 and r["tier"] == 2 and r["next_tier"] == 3 and r["to_next_tier"] == 10_000_000
    assert "10.0M" in kc.review_summary(r) and "+25%" in kc.review_summary(r) and "Tier 3" in kc.review_summary(r)
    assert kc.last_months(3, date(2026, 1, 15)) == ["2025-10", "2025-11", "2025-12"]
    assert kc.month_range("2026-02") == ("2026-02-01", "2026-02-28")


async def _lead(uid, step, *, quiet_days=0, country=None, weex_uid=None):
    await db.get_or_create_followup_chat(account_id=1, user_id=uid, username=f"u{uid}", name=f"KOL {uid}")
    async with db.get_db() as conn:
        await conn.execute("UPDATE ai_followup_chats SET kol_step = ?, updated_at = datetime('now', ?) "
                           "WHERE account_id = 1 AND user_id = ?", (step, f"-{quiet_days} days", uid))
        await conn.commit()
    prof = {}
    if country:
        prof["kol_country"] = country
    if weex_uid:
        prof["weex_uid"] = weex_uid
    if prof:
        await db.upsert_kol_profile(1, uid, prof)


async def test_due_items_and_skip(monkeypatch):
    await _lead(1, "active", quiet_days=30)                       # check-in due
    await _lead(2, "active", quiet_days=3)                        # talked recently: nothing
    await _lead(3, "prospect", quiet_days=60)                     # not onboarded: never
    await _lead(4, "deal_agreed", country="Philippines")          # Christmas greeting
    await _lead(5, "first_post", weex_uid="777")                  # monthly review
    await kc.save_volumes("2026-11", {"777": 12_000_000})
    await kc.save_volumes("2026-10", {"777": 9_000_000})
    items = await kc.due_items(date(2026, 12, 24))
    got = {(i["user_id"], i["kind"]) for i in items}
    assert (1, "checkin") in got and (4, "greeting") in got and (5, "review") in got
    assert not any(u in (2, 3) for u, _ in got)
    assert items[0]["kind"] == "greeting"

    await kc.log_touchpoint(1, 1, "skip_checkin")
    await kc.log_touchpoint(1, 4, "greeting")
    async with db.get_db() as conn:  # sent during the festival window
        await conn.execute("UPDATE kol_touchpoints SET created_at = '2026-12-23 10:00:00' WHERE kind = 'greeting'")
        await conn.commit()
    got = {(i["user_id"], i["kind"]) for i in await kc.due_items(date(2026, 12, 24))}
    assert (1, "checkin") not in got and (4, "greeting") not in got and (5, "review") in got


async def test_auto_care_counts_as_touch():
    await _lead(7, "active", quiet_days=40)
    async with db.get_db() as conn:
        await conn.execute("UPDATE ai_followup_chats SET last_care_at = datetime('now', '-2 days') WHERE user_id = 7")
        await conn.commit()
    assert not [i for i in await kc.due_items() if i["user_id"] == 7 and i["kind"] == "checkin"]


async def test_bad_touch_kind_rejected():
    with pytest.raises(ValueError):
        await kc.log_touchpoint(1, 1, "spam")


def test_draft_prompt_has_no_pitch():
    p = kc.draft_prompt("greeting", "An", [{"role": "user", "content": "xin chào"}],
                        {"festival": {"name": "Lunar New Year"}})
    assert "Lunar New Year" in p and "NO business" in p and "SAME language" in p


# ── D. Scorecard ─────────────────────────────────────────────────────────────

async def test_scorecard_counts(client):
    await _lead(1, "active")
    await db.upsert_kol_profile(1, 1, {"archetype": "trader"})
    await kc.log_touchpoint(1, 1, "meet")
    await kc.log_touchpoint(1, 1, "call")
    await kc.log_touchpoint(1, 1, "greeting")
    s = await kc.scorecard()
    assert s["active_kols"] == 1 and s["meetings_30d"] == 1 and s["calls_30d"] == 1
    assert s["care_30d"]["greeting"] == 1 and s["by_archetype"].get("trader") == 1
    r = client.get("/api/kol-care/scorecard")
    assert r.status_code == 200 and r.json()["active_kols"] == 1


# ── Birthday greeting ────────────────────────────────────────────────────────

def test_parse_birthday_formats():
    assert kc.parse_birthday("25/12") == (25, 12)
    assert kc.parse_birthday("born 3-7") == (3, 7)
    assert kc.parse_birthday("1990-12-25") == (25, 12)
    assert kc.parse_birthday("29/02") == (29, 2)
    assert kc.parse_birthday("31/02") is None and kc.parse_birthday("soon") is None


def test_birthday_window():
    assert kc.birthday_for("25/12", date(2026, 12, 23))["date"] == "2026-12-25"
    assert kc.birthday_for("25/12", date(2026, 12, 27)) is None
    assert kc.birthday_for("01/01", date(2026, 12, 30))["date"] == "2027-01-01"   # across the year end
    assert kc.birthday_for("29/02", date(2027, 2, 27))["date"] == "2027-02-28"   # non-leap year


async def test_birthday_due_and_done():
    await _lead(9, "uid_received")
    await db.upsert_kol_profile(1, 9, {"birthday": "24/12"})
    items = [i for i in await kc.due_items(date(2026, 12, 23)) if i["user_id"] == 9]
    assert [i["kind"] for i in items] == ["birthday"] and items[0]["reason"] == "Sinh nhật 24/12"
    assert "happy-birthday" in kc.draft_prompt("birthday", "An", [], items[0])
    await kc.log_touchpoint(1, 9, "birthday")
    async with db.get_db() as conn:
        await conn.execute("UPDATE kol_touchpoints SET created_at = '2026-12-23 09:00:00' WHERE kind = 'birthday'")
        await conn.commit()
    assert not [i for i in await kc.due_items(date(2026, 12, 23)) if i["user_id"] == 9]
    assert "birthday" in (await kc.scorecard())["care_30d"]


async def test_profile_form_normalizes_birthday(client):
    acc = await db.create_account({"name": "Nick", "phone": "+840000000010", "api_id": 1, "api_hash": "h", "session_name": "s10"})
    await db.get_or_create_followup_chat(account_id=acc, user_id=6, username="b", name="B")
    assert client.post(f"/api/ai-followup/chats/{acc}/6/profile", json={"birthday": "5.7"}).status_code == 200
    assert (await db.get_kol_profile(acc, 6))["birthday"] == "05/07"
    assert client.post(f"/api/ai-followup/chats/{acc}/6/profile", json={"birthday": "32/13"}).status_code == 400
