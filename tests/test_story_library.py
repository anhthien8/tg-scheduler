"""Story photo library: AI risk verdicts, photo picking and the daily autopilot planner."""
import asyncio
import json
import random
import time
from datetime import datetime

import pytest

import database as db
import story_library as sl


def test_parse_verdict():
    ok = sl.parse_verdict('Sure: {"category": "am_thuc", "safe": true, "reason": "", "desc": "Ly cà phê"}')
    assert ok == {"status": "ok", "category": "am_thuc", "reason": "", "description": "Ly cà phê"}
    bad = sl.parse_verdict('{"category": "khac", "safe": false, "reason": "Có súng", "desc": "Khẩu súng"}')
    assert bad["status"] == "rejected" and bad["reason"] == "Có súng"
    assert sl.parse_verdict('{"category": "weird", "safe": true}')["category"] == "khac"
    assert sl.parse_verdict('{"category": "du_lich"}') is None         # no verdict → never posted
    assert sl.parse_verdict("I cannot see the image") is None


def test_pick_media_skips_taken_and_already_posted_least_used_first():
    pool = [{"file": f, "used_count": u} for f, u in (("a", 3), ("b", 0), ("c", 1), ("d", 0))]
    got = sl.pick_media(pool, nick_used={"d"}, taken={"b"}, n=2, rng=random.Random(1))
    assert [m["file"] for m in got] == ["c", "a"]


def test_plan_slots_inside_window():
    noon = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
    slots = sl.plan_slots(noon, 8, 22, 3, rng=random.Random(2))
    assert len(slots) == 3 and slots == sorted(slots)
    assert all(noon + 300 <= s < noon + 10 * 3600 for s in slots)
    assert sl.plan_slots(noon, 8, 12, 1) == []                        # window already over


def test_vision_refuses_providers_that_cannot_see(monkeypatch):
    import ai_remix

    async def setting(key, default=None):
        return "gemini" if key == "ai_provider" else default

    async def order():
        return ["groq"]

    async def cfg(p):
        return ["k"], {}

    monkeypatch.setattr(db, "get_setting", setting)
    monkeypatch.setattr(ai_remix, "get_fallback_order", order)
    monkeypatch.setattr(ai_remix, "get_provider_config", cfg)
    with pytest.raises(RuntimeError, match="đọc được ảnh"):
        asyncio.run(sl._vision("x", "data:"))


async def _add(file, status="ok", category="am_thuc", used=0, msg_id=None):
    await sl.ensure_schema()
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO story_media (source_account_id, msg_id, file, status, category, description, used_count) "
            "VALUES (1, ?, ?, ?, ?, 'mô tả', ?)", (msg_id or abs(hash(file)) % 10**9, file, status, category, used))
        await conn.commit()


def test_classify_marks_risky_and_unreadable(monkeypatch, tmp_path):
    monkeypatch.setattr(sl.se, "MEDIA_DIR", str(tmp_path))
    (tmp_path / "saved_1_1.jpg").write_bytes(b"\xff\xd8 fake")
    (tmp_path / "saved_1_2.jpg").write_bytes(b"\xff\xd8 fake")
    answers = iter(['{"category": "khac", "safe": false, "reason": "Tiền mặt", "desc": "Cọc tiền"}', "no idea"])

    async def vision(prompt, url):
        assert url.startswith("data:image/jpeg;base64,")
        return next(answers)

    monkeypatch.setattr(sl, "_vision", vision)

    async def run():
        await _add("saved_1_1.jpg", status="pending", msg_id=1)
        await _add("saved_1_2.jpg", status="pending", msg_id=2)
        await sl.classify_pending()
        lib = await sl.library()
        by = {i["file"]: i for i in lib["items"]}
        assert by["saved_1_1.jpg"]["status"] == "rejected" and by["saved_1_1.jpg"]["reason"] == "Tiền mặt"
        assert by["saved_1_2.jpg"]["status"] == "error"
        assert sum(lib["counts"].values()) == 0
    asyncio.run(run())


def test_autopilot_plans_once_per_day_distinct_photos(monkeypatch):
    async def caption(desc, lang):
        return "Sáng nay"

    monkeypatch.setattr(sl, "_caption", caption)
    noon = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()

    async def run():
        for i in range(5):
            await _add(f"saved_1_{i}.jpg", used=i)
        await _add("saved_1_gun.jpg", status="rejected")
        await _add("saved_1_trip.jpg", category="du_lich")
        await sl.save_autopilot(enabled=True, categories=["am_thuc"], account_ids=[7, 8], per_day=2,
                                start_hour=8, end_hour=22)
        assert await sl.plan_today(noon) == 4
        assert await sl.plan_today(noon) == 0                          # already planned today
        async with db.get_db() as conn:
            rows = await (await conn.execute(
                "SELECT j.media_file, j.caption, j.name, p.account_id, p.post_at FROM story_jobs j "
                "JOIN story_posts p ON p.job_id = j.id")).fetchall()
        files = [r[0] for r in rows]
        assert len(set(files)) == 4                                     # no photo twice today
        assert "saved_1_gun.jpg" not in files and "saved_1_trip.jpg" not in files
        assert "saved_1_4.jpg" not in files                             # most used left out
        assert {r[3] for r in rows} == {7, 8} and all(r[1] == "Sáng nay" for r in rows)
        assert all(r[2].startswith(sl.AUTO_PREFIX) and r[4] > noon for r in rows)

        # switching off cancels what is not posted yet and lets a re-enable plan again
        assert await sl.cancel_pending_auto() == 4
        await sl.save_autopilot(enabled=True, categories=["am_thuc"], account_ids=[7], per_day=1,
                                start_hour=8, end_hour=22)
        assert await sl.plan_today(noon) == 1
    asyncio.run(run())


def test_save_autopilot_validation():
    async def run():
        with pytest.raises(ValueError):
            await sl.save_autopilot(enabled=True, categories=[], account_ids=[1], per_day=1, start_hour=8, end_hour=22)
        with pytest.raises(ValueError):
            await sl.save_autopilot(enabled=True, categories=["khac"], account_ids=[1], per_day=1, start_hour=20, end_hour=8)
        await sl.save_autopilot(enabled=False, categories=[], account_ids=[], per_day=9, start_hour=8, end_hour=22)
        assert (await sl.get_autopilot())["per_day"] == sl.MAX_PER_DAY
    asyncio.run(run())
