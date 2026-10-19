"""Tests for C4: Self-Learning FTS5 and C5: Referral Loop."""
import pytest
import aiosqlite

from database import (
    _sanitize_fts_query,
    get_relevant_learned_knowledge_v2,
    add_learned_knowledge,
    get_relevant_learned_knowledge,
    generate_referral_code,
    create_referral_code,
    get_referral_code,
    get_referral_code_by_user,
    get_all_referral_codes,
    deactivate_referral_code,
    increment_referral_uses,
    create_referral_attribution,
    get_attribution_by_referred,
    get_attributions_by_referrer,
    get_referral_stats,
    init_db,
    get_db,
)


# ── C4: FTS5 Tests ─────────────────────────────────────────────────────────

class TestSanitizeFtsQuery:

    def test_simple_query(self):
        result = _sanitize_fts_query("phí giao dịch")
        assert '"phí"' in result or '"giao"' in result

    def test_special_chars_escaped(self):
        result = _sanitize_fts_query('fee "quoted" AND trading*')
        assert "AND" not in result or '"AND"' in result

    def test_empty_query(self):
        assert _sanitize_fts_query("") == ""
        assert _sanitize_fts_query("   ") == ""

    def test_single_char_tokens_filtered(self):
        result = _sanitize_fts_query("a b c hello")
        assert "hello" in result

    def test_max_20_terms(self):
        long_query = " ".join(f"word{i}" for i in range(30))
        result = _sanitize_fts_query(long_query)
        assert result.count('"') <= 40  # 20 terms * 2 quotes each


class TestFTS5Retrieval:

    @pytest.fixture(autouse=True)
    async def setup_db(self, tmp_path):
        """Create a temp DB with FTS5 tables."""
        import database
        db_path = str(tmp_path / "test.db")
        original_path = database.DB_PATH
        database.DB_PATH = db_path
        await init_db()
        yield
        database.DB_PATH = original_path

    @pytest.mark.asyncio
    async def test_fts5_basic_retrieval(self):
        """Insert a rule and retrieve it via FTS5."""
        await add_learned_knowledge(1, 0, "phí giao dịch WEEX", "WEEX phí 0.02% maker")
        results = await get_relevant_learned_knowledge_v2(1, "phí giao dịch bao nhiêu")
        assert len(results) >= 1
        assert any("WEEX" in r.get("learned_answer", "") for r in results)

    @pytest.mark.asyncio
    async def test_fts5_empty_query_returns_recent(self):
        """Empty query should still return recent rules."""
        await add_learned_knowledge(1, 0, "test question", "test answer")
        results = await get_relevant_learned_knowledge_v2(1, "")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_fts5_no_cross_agent(self):
        """Rules from agent 1 should not appear for agent 2."""
        await add_learned_knowledge(1, 0, "secret", "agent1 only")
        results = await get_relevant_learned_knowledge_v2(2, "secret")
        # Only recent rules (which are for agent 2) should appear, no FTS match
        fts_match = [r for r in results if "agent1" in r.get("learned_answer", "")]
        assert len(fts_match) == 0

    @pytest.mark.asyncio
    async def test_fts5_ranking(self):
        """More relevant rules should rank higher."""
        await add_learned_knowledge(1, 0, "copy trade là gì", "Copy trade là tính năng sao chép")
        await add_learned_knowledge(1, 0, "thời tiết hôm nay", "Không liên quan")
        results = await get_relevant_learned_knowledge_v2(1, "copy trade")
        if len(results) >= 2:
            assert "copy" in results[0].get("question_pattern", "").lower() or \
                   "copy" in results[0].get("learned_answer", "").lower()


# ── C5: Referral Tests ─────────────────────────────────────────────────────

class TestReferralCode:

    def test_code_format(self):
        code = generate_referral_code()
        assert code.startswith("REF-")
        assert len(code) == 12  # "REF-" + 8 chars

    def test_unique_codes(self):
        codes = {generate_referral_code() for _ in range(100)}
        assert len(codes) == 100


class TestReferralCRUD:

    @pytest.fixture(autouse=True)
    async def setup_db(self, tmp_path):
        import database
        db_path = str(tmp_path / "test_ref.db")
        original_path = database.DB_PATH
        database.DB_PATH = db_path
        await init_db()
        yield
        database.DB_PATH = original_path

    @pytest.mark.asyncio
    async def test_create_and_get_code(self):
        result = await create_referral_code(1, 12345, "REF-TEST0001")
        assert result["code"] == "REF-TEST0001"
        fetched = await get_referral_code("REF-TEST0001")
        assert fetched is not None
        assert fetched["referrer_user_id"] == 12345

    @pytest.mark.asyncio
    async def test_get_code_by_user(self):
        await create_referral_code(1, 99999, "REF-USER9999")
        found = await get_referral_code_by_user(99999)
        assert found is not None
        assert found["code"] == "REF-USER9999"

    @pytest.mark.asyncio
    async def test_deactivate_code(self):
        result = await create_referral_code(1, 11111, "REF-DEACT001")
        await deactivate_referral_code(result["id"])
        fetched = await get_referral_code("REF-DEACT001")
        assert fetched["is_active"] == 0

    @pytest.mark.asyncio
    async def test_increment_uses(self):
        result = await create_referral_code(1, 22222, "REF-INCR0001")
        await increment_referral_uses(result["id"])
        await increment_referral_uses(result["id"])
        fetched = await get_referral_code("REF-INCR0001")
        assert fetched["uses_count"] == 2

    @pytest.mark.asyncio
    async def test_attribution_create(self):
        result = await create_referral_code(1, 33333, "REF-ATTR0001")
        attr_id = await create_referral_attribution(
            result["id"], 33333, 44444, "kol_b"
        )
        assert attr_id > 0
        found = await get_attribution_by_referred(44444)
        assert found is not None
        assert found["referrer_user_id"] == 33333

    @pytest.mark.asyncio
    async def test_self_referral_detection(self):
        """Self-referral should be prevented at the route level, not DB level."""
        result = await create_referral_code(1, 55555, "REF-SELF0001")
        # DB allows it (logic is in route), but we test the route function
        from routes.referrals import _process_referral_code
        success, msg = await _process_referral_code("REF-SELF0001", 55555)
        assert success is False
        assert "self-refer" in msg.lower()

    @pytest.mark.asyncio
    async def test_referral_stats(self):
        await create_referral_code(1, 66666, "REF-STAT0001")
        stats = await get_referral_stats()
        assert stats["active_codes"] >= 1
        assert "conversion_rate" in stats

    @pytest.mark.asyncio
    async def test_list_all_codes(self):
        await create_referral_code(1, 77777, "REF-LIST0001")
        await create_referral_code(1, 88888, "REF-LIST0002")
        codes = await get_all_referral_codes()
        assert len(codes) >= 2


class TestReferralDetection:

    def test_pattern_match(self):
        from routes.referrals import REFERRAL_PATTERN
        text = "Hello! My referral code is REF-ABCD1234, please use it"
        match = REFERRAL_PATTERN.search(text)
        assert match is not None
        assert match.group(0) == "REF-ABCD1234"

    def test_pattern_no_match(self):
        from routes.referrals import REFERRAL_PATTERN
        assert REFERRAL_PATTERN.search("no code here") is None
        assert REFERRAL_PATTERN.search("REF-short") is None
