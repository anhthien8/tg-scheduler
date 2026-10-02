"""v3.7.6 — luật học chọn theo độ liên quan + dashboard Agent Evolution.

Ba nhóm kiểm tra:
1. DB: get_relevant_learned_knowledge chọn luật ĐÚNG chủ đề, không chỉ 30 mới nhất.
2. DB: get_agent_evolution trả đủ 5 chỉ số.
3. Source: dm_reply_tracker dùng relevance picker (không quay lại LIMIT 30).
"""
import asyncio
import inspect
from pathlib import Path

import database as db
import dm_reply_tracker as drt

SRC = (Path(__file__).resolve().parent.parent / "dm_reply_tracker.py").read_text(encoding="utf-8")


def test_tracker_uses_relevance_picker():
    assert "get_relevant_learned_knowledge" in SRC
    # Ngưỡng cũ LIMIT 30 đã bị bỏ khỏi luồng lắp prompt.
    assert 'get_learned_knowledge_for_agent(agent_config["id"], status="approved")' not in SRC


def test_get_learned_knowledge_allows_unlimited():
    sig = inspect.signature(db.get_learned_knowledge_for_agent)
    assert "limit" in sig.parameters
    # mặc định không giới hạn (None) -> trả hết luật
    assert sig.parameters["limit"].default is None


def _seed(conn, rows):
    conn.executemany(
        "INSERT INTO agent_learned_knowledge (ai_agent_id, source_user_id, question_pattern, learned_answer, status) "
        "VALUES (?,?,?,?,'approved')", rows)
    conn.commit()


def test_relevant_picker_prefers_matching_topic(tmp_path, monkeypatch):
    """Luật khớp chủ đề (dù CŨ) phải lọt vào, không bị chôn dưới trần 30 mới nhất."""
    import sqlite3
    dbfile = tmp_path / "t.db"
    conn = sqlite3.connect(dbfile)
    conn.executescript("""
        CREATE TABLE agent_learned_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ai_agent_id INTEGER,
            source_user_id INTEGER, question_pattern TEXT NOT NULL,
            learned_answer TEXT NOT NULL, status TEXT, created_at TEXT DEFAULT (datetime('now')));
    """)
    # 60 luật rác mới nhất (chủ đề 'random filler topic n')
    filler = [(1, 1, f"random filler topic {i}", f"answer {i}") for i in range(60)]
    _seed(conn, filler)
    # 1 luật cũ nhưng ĐÚNG chủ đề cần tìm
    conn.execute("INSERT INTO agent_learned_knowledge (ai_agent_id,source_user_id,question_pattern,learned_answer,status) "
                 "VALUES (1,1,'marketing fund cho channel 35k subs','ho tro 5000 usd/thang','approved')")
    conn.commit()
    conn.close()

    from contextlib import asynccontextmanager
    import aiosqlite

    @asynccontextmanager
    async def fake_get_db():
        c = await aiosqlite.connect(dbfile)
        c.row_factory = aiosqlite.Row
        try:
            yield c
        finally:
            await c.close()

    monkeypatch.setattr(db, "get_db", fake_get_db)

    async def run():
        rules = await db.get_relevant_learned_knowledge(
            1, "toi muon biet marketing fund cho channel cua toi")
        qs = [r["question_pattern"] for r in rules]
        return qs

    qs = asyncio.get_event_loop().run_until_complete(run()) if False else asyncio.run(run())
    assert any("marketing fund" in q.lower() for q in qs), f"luật đúng chủ đề bị bỏ sót: {qs[:5]}"


def test_agent_evolution_shape():
    async def run():
        return await db.get_agent_evolution(30)
    r = asyncio.run(run())
    for key in ("learning_per_week", "repetition", "handover_per_week",
                "funnel", "stages", "discipline", "range_days"):
        assert key in r, f"thiếu chỉ số {key}"
    assert r["range_days"] == 30
    assert "redundant_pct" in r["repetition"]
    assert "avg_replies" in r["discipline"]
