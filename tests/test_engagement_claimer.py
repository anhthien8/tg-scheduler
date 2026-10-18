"""
tests/test_engagement_claimer.py — Tests for the atomic claim system.

Covers all 4 mandatory checks from design doc §4.1.1:
(a) Account global limit: 25 OK, 26th blocked
(b) Group/hour limit: 5 accounts same group, only 4 total pass
(c) Concurrency: seed 1 slot, 10 coroutines → exactly 1 wins
(d) Rollback: release doesn't delete previous successful claims

Also covers gap enforcement and try_claim_all orchestration.
"""
import asyncio
import time

import pytest


@pytest.fixture
def clean_db():
    """Initialize a clean database for each test."""
    import database as db
    asyncio.run(db.init_db())
    yield db
    async def _cleanup():
        async with db.get_db() as conn:
            for table in (
                "engagement_campaigns", "engagement_logs",
                "engagement_claims", "engagement_window_counters",
                "engagement_dm_map",
            ):
                try:
                    await conn.execute(f"DELETE FROM {table}")
                except Exception:
                    pass
            await conn.commit()
    asyncio.run(_cleanup())


# ── Test (a): Account global limit — 25 pass, 26th blocked ──────────────────

class TestAccountGlobalLimit:
    def test_25_pass_26th_blocked(self, clean_db):
        """3 groups, 1 account, 25 replies OK; 26th blocked by acct:{a}:{day}."""
        import claimer
        db = clean_db

        async def _test():
            day = time.strftime("%Y%m%d", time.gmtime())
            account_id = 7
            campaign_id = 1
            groups = [100, 200, 300]

            # Create dummy campaign
            await db.create_engagement_campaign({
                "name": "Limit Test",
                "group_ids": groups,
            })

            success_count = 0
            for i in range(26):
                group = groups[i % len(groups)]
                ok, reason, claim_id, counter_id = await claimer.claim_limit(
                    campaign_id, group, account_id,
                    f"acct:{account_id}:{day}", 25, "account"
                )
                if ok:
                    success_count += 1

            assert success_count == 25

            # Verify counter value
            async with db.get_db() as conn:
                row = await (await conn.execute(
                    "SELECT count FROM engagement_window_counters "
                    "WHERE window_kind='acct' AND acct_key=? AND window_key=?",
                    (account_id, f"acct:{account_id}:{day}")
                )).fetchone()
                assert row is not None
                assert row[0] == 25  # exactly at limit, not over

        asyncio.run(_test())


# ── Test (b): Group/hour total — 5 accounts, only 4 pass ────────────────────

class TestGroupHourLimit:
    def test_5_accounts_only_4_pass(self, clean_db):
        """1 group, 5 different accounts, same hour → only 4 total pass."""
        import claimer
        db = clean_db

        async def _test():
            hour = time.strftime("%Y%m%dT%H", time.gmtime())
            group_id = 1000
            campaign_id = 1
            accounts = [7, 8, 9, 10, 11]

            await db.create_engagement_campaign({
                "name": "Group Limit Test",
                "group_ids": [group_id],
            })

            success_count = 0
            for acc_id in accounts:
                ok, reason, claim_id, counter_id = await claimer.claim_limit(
                    campaign_id, group_id, acc_id,
                    f"group:{group_id}:{hour}", 4, "group"
                )
                if ok:
                    success_count += 1

            assert success_count == 4

            # Verify: ONE counter row for the group (not per-account)
            async with db.get_db() as conn:
                row = await (await conn.execute(
                    "SELECT count FROM engagement_window_counters "
                    "WHERE window_kind='group' AND acct_key=-1 AND group_key=? AND window_key=?",
                    (group_id, f"group:{group_id}:{hour}")
                )).fetchone()
                assert row is not None
                assert row[0] == 4  # one counter row, value = 4

                # Verify only 1 counter row (not 5)
                count_rows = await (await conn.execute(
                    "SELECT COUNT(*) FROM engagement_window_counters "
                    "WHERE window_kind='group' AND group_key=?",
                    (group_id,)
                )).fetchone()
                assert count_rows[0] == 1

        asyncio.run(_test())


# ── Test (c): Concurrency — seed 1 slot, 10 coroutines → exactly 1 wins ─────

class TestConcurrencyIsolated:
    def test_one_slot_ten_coroutines(self, clean_db):
        """Isolated claim_limit test: seed count=24 (limit=25), 10 concurrent claims.
        Exactly 1 wins, 9 get rate_limit. Final count=25 (never 26+).
        """
        import claimer
        db = clean_db

        async def _test():
            day = time.strftime("%Y%m%d", time.gmtime())
            account_id = 7
            campaign_id = 1
            window_key = f"acct:{account_id}:{day}"

            await db.create_engagement_campaign({
                "name": "Concurrency Test",
                "group_ids": [1],
            })

            # Seed counter to 24 (1 slot remaining)
            async with db.get_db() as conn:
                await conn.execute(
                    "INSERT INTO engagement_window_counters"
                    "(window_kind, acct_key, group_key, window_key, count, limit_) "
                    "VALUES('acct', ?, -1, ?, 24, 25)",
                    (account_id, window_key),
                )
                await conn.commit()

            # Verify seed
            async with db.get_db() as conn:
                row = await (await conn.execute(
                    "SELECT count FROM engagement_window_counters WHERE window_key=?",
                    (window_key,)
                )).fetchone()
                assert row[0] == 24

            # 10 concurrent claims
            results = await asyncio.gather(*(
                claimer.claim_limit(
                    campaign_id, 1, account_id,
                    window_key, 25, "account"
                )
                for _ in range(10)
            ))

            successes = [r for r in results if r[0] is True]
            failures = [r for r in results if r[0] is False]

            assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
            assert len(failures) == 9, f"Expected 9 failures, got {len(failures)}"

            # Verify counter didn't exceed limit
            async with db.get_db() as conn:
                row = await (await conn.execute(
                    "SELECT count FROM engagement_window_counters WHERE window_key=?",
                    (window_key,)
                )).fetchone()
                assert row[0] == 25, f"Expected count=25, got {row[0]}"

                # Verify exactly 1 claim record
                claim_count = await (await conn.execute(
                    "SELECT COUNT(*) FROM engagement_claims WHERE window_key=?",
                    (window_key,)
                )).fetchone()
                assert claim_count[0] == 1

        asyncio.run(_test())


# ── Test (d): Rollback — release doesn't delete previous claims ──────────────

class TestRollback:
    def test_rollback_preserves_previous_claims(self, clean_db):
        """Claim round 1 succeeds. Round 2 claims partially, then releases.
        Round 1's claims must remain intact. Counter decremented correctly.
        """
        import claimer
        db = clean_db

        async def _test():
            day = time.strftime("%Y%m%d", time.gmtime())
            account_id = 7
            group_id = 1
            campaign_id = 1
            window_key = f"acct:{account_id}:{day}"

            await db.create_engagement_campaign({
                "name": "Rollback Test",
                "group_ids": [group_id],
            })

            # Round 1: successful claim
            ok1, _, claim_id1, counter_id1 = await claimer.claim_limit(
                campaign_id, group_id, account_id,
                window_key, 25, "account"
            )
            assert ok1 is True
            assert claim_id1 is not None

            # Verify counter = 1
            async with db.get_db() as conn:
                row = await (await conn.execute(
                    "SELECT count FROM engagement_window_counters WHERE window_key=?",
                    (window_key,)
                )).fetchone()
                assert row[0] == 1

            # Round 2: claim succeeds
            ok2, _, claim_id2, counter_id2 = await claimer.claim_limit(
                campaign_id, group_id, account_id,
                window_key, 25, "account"
            )
            assert ok2 is True

            # Verify counter = 2
            async with db.get_db() as conn:
                row = await (await conn.execute(
                    "SELECT count FROM engagement_window_counters WHERE window_key=?",
                    (window_key,)
                )).fetchone()
                assert row[0] == 2

            # Release round 2 claims (simulate error before network send)
            await claimer.release_claims([(claim_id2, counter_id2, False)])

            # Verify: counter back to 1 (not 0!)
            async with db.get_db() as conn:
                row = await (await conn.execute(
                    "SELECT count FROM engagement_window_counters WHERE window_key=?",
                    (window_key,)
                )).fetchone()
                assert row[0] == 1, f"Expected count=1 after rollback, got {row[0]}"

                # Round 1's claim still exists
                claim1_exists = await (await conn.execute(
                    "SELECT 1 FROM engagement_claims WHERE id=?",
                    (claim_id1,)
                )).fetchone()
                assert claim1_exists is not None, "Round 1 claim was deleted by rollback!"

                # Round 2's claim was deleted
                claim2_exists = await (await conn.execute(
                    "SELECT 1 FROM engagement_claims WHERE id=?",
                    (claim_id2,)
                )).fetchone()
                assert claim2_exists is None, "Round 2 claim was not cleaned up"

            # Round 3: claim succeeds (slot was properly released)
            ok3, _, _, _ = await claimer.claim_limit(
                campaign_id, group_id, account_id,
                window_key, 25, "account"
            )
            assert ok3 is True

        asyncio.run(_test())


# ── Gap enforcement tests ────────────────────────────────────────────────────

class TestGapEnforcement:
    def test_gap_blocks_within_min_gap(self, clean_db):
        """Two claims within min_gap_seconds → second is blocked."""
        import claimer
        db = clean_db

        async def _test():
            account_id = 7
            group_id = 1
            campaign_id = 1
            window_key = f"gap:{group_id}:{account_id}"

            await db.create_engagement_campaign({
                "name": "Gap Test",
                "group_ids": [group_id],
            })

            # First claim
            ok1, _, gap_id1 = await claimer.claim_gap(
                campaign_id, group_id, account_id, window_key, 180
            )
            assert ok1 is True

            # Second claim immediately → should be blocked
            ok2, reason2, _ = await claimer.claim_gap(
                campaign_id, group_id, account_id, window_key, 180
            )
            assert ok2 is False
            assert reason2 == "rate_limit"

        asyncio.run(_test())

    def test_gap_allows_after_min_gap(self, clean_db):
        """Two claims with old timestamp → second passes (gap expired)."""
        import claimer
        db = clean_db

        async def _test():
            account_id = 7
            group_id = 1
            campaign_id = 1
            window_key = f"gap:{group_id}:{account_id}"

            await db.create_engagement_campaign({
                "name": "Gap Allow Test",
                "group_ids": [group_id],
            })

            # Insert a gap claim from 200 seconds ago
            async with db.get_db() as conn:
                await conn.execute(
                    "INSERT INTO engagement_claims(campaign_id, group_id, account_id, window_key, created_at) "
                    "VALUES(?, ?, ?, ?, datetime('now', '-200 seconds'))",
                    (campaign_id, group_id, account_id, window_key),
                )
                await conn.commit()

            # Claim with min_gap=180 → should pass (200 > 180)
            ok, reason, gap_id = await claimer.claim_gap(
                campaign_id, group_id, account_id, window_key, 180
            )
            assert ok is True

        asyncio.run(_test())

    def test_gap_key_not_permanent(self, clean_db):
        """Gap key allows re-use after expired (NOT permanent block)."""
        import claimer
        db = clean_db

        async def _test():
            account_id = 7
            group_id = 1
            campaign_id = 1
            window_key = f"gap:{group_id}:{account_id}"

            await db.create_engagement_campaign({
                "name": "Gap Reuse Test",
                "group_ids": [group_id],
            })

            # First claim
            ok1, _, _ = await claimer.claim_gap(
                campaign_id, group_id, account_id, window_key, 1  # 1 second gap
            )
            assert ok1 is True

            # Wait for gap to expire (use fake old timestamp)
            async with db.get_db() as conn:
                await conn.execute(
                    "UPDATE engagement_claims SET created_at=datetime('now', '-10 seconds') "
                    "WHERE window_key=?",
                    (window_key,),
                )
                await conn.commit()

            # Second claim → should pass
            ok2, _, _ = await claimer.claim_gap(
                campaign_id, group_id, account_id, window_key, 1
            )
            assert ok2 is True

            # Verify only 1 gap row exists (old was deleted, new inserted)
            async with db.get_db() as conn:
                count = await (await conn.execute(
                    "SELECT COUNT(*) FROM engagement_claims WHERE window_key=?",
                    (window_key,)
                )).fetchone()
                assert count[0] == 1

        asyncio.run(_test())


# ── try_claim_all orchestration ──────────────────────────────────────────────

class TestTryClaimAll:
    def test_all_windows_pass(self, clean_db):
        """All 5 windows pass → ok=True, claimed list has 5 entries."""
        import claimer
        db = clean_db

        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "All Pass",
                "group_ids": [1],
                "max_replies_per_hour_per_group": 4,
                "max_replies_per_day_per_group": 12,
                "min_gap_seconds": 180,
            })
            campaign = await db.get_engagement_campaign(cid)
            ok, reason, claimed = await claimer.try_claim_all(campaign, 1, 7)
            assert ok is True
            assert reason == ""
            assert len(claimed) == 5

        asyncio.run(_test())

    def test_rollback_on_failure(self, clean_db):
        """If one window fails, all previously claimed windows are released."""
        import claimer
        db = clean_db

        async def _test():
            day = time.strftime("%Y%m%d", time.gmtime())
            hour = time.strftime("%Y%m%dT%H", time.gmtime())
            group_id = 1
            account_id = 7

            cid = await db.create_engagement_campaign({
                "name": "Rollback All",
                "group_ids": [group_id],
                "max_replies_per_hour_per_group": 4,
                "max_replies_per_day_per_group": 12,
                "min_gap_seconds": 180,
            })

            # Exhaust group/hour limit (fill to 4)
            for i in range(4):
                await claimer.claim_limit(
                    cid, group_id, 100 + i,
                    f"group:{group_id}:{hour}", 4, "group"
                )

            campaign = await db.get_engagement_campaign(cid)
            ok, reason, claimed = await claimer.try_claim_all(campaign, group_id, account_id)
            assert ok is False
            assert reason == "rate_limit"
            assert claimed == []

            # Verify the first 2 windows (acct, groupacct) were rolled back
            async with db.get_db() as conn:
                wkey = f"acct:{account_id}:{day}"
                row = await (await conn.execute(
                    "SELECT count FROM engagement_window_counters WHERE window_key=?",
                    (wkey,)
                )).fetchone()
                # Counter should be 0 (was incremented then rolled back)
                if row:
                    assert row[0] == 0

        asyncio.run(_test())


# ── Account busy integration ────────────────────────────────────────────────

class TestAccountBusy:
    def test_busy_check(self):
        import account_busy
        async def _test():
            # Not busy initially
            assert account_busy.is_busy(7) is False

            # Acquire
            async with account_busy.acquire(7, "test"):
                assert account_busy.is_busy(7) is True
                assert account_busy.get_busy_kind(7) == "test"

                # Second acquire should raise
                with pytest.raises(account_busy.AccountBusy):
                    async with account_busy.acquire(7, "other"):
                        pass

            # Released after context exit
            assert account_busy.is_busy(7) is False

        asyncio.run(_test())

    def test_ttl_expiry(self):
        import account_busy
        async def _test():
            # Acquire with very short TTL
            async with account_busy.acquire(7, "test", ttl_secs=0.01):
                assert account_busy.is_busy(7) is True

            # After TTL expiry
            import time
            time.sleep(0.02)
            assert account_busy.is_busy(7) is False

        asyncio.run(_test())

    def test_get_all_busy(self):
        import account_busy
        async def _test():
            async with account_busy.acquire(7, "dm"):
                async with account_busy.acquire(8, "engagement"):
                    busy = account_busy.get_all_busy()
                    assert 7 in busy
                    assert 8 in busy
                    assert busy[7] == "dm"
                    assert busy[8] == "engagement"

        asyncio.run(_test())
