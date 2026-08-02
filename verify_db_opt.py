import asyncio
import os
import shutil
import tempfile
import sys

# Setup path so it runs in project root
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

async def test_optimization():
    print("Testing optimizations...")
    # Point DB_PATH to a temp test file
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_scheduler.db")
    
    # Override global DB_PATH in imported module
    import database
    database.DB_PATH = test_db
    
    try:
        # 1. Initialize DB and verify indexes
        await database.init_db()
        print("[OK] init_db completed successfully")
        
        async with database.get_db() as db:
            cursor = await db.execute("PRAGMA index_list('send_logs')")
            indexes = [row[1] for row in await cursor.fetchall()]
            assert "idx_send_logs_schedule_id" in indexes, "Missing schedule_id index on send_logs"
            assert "idx_send_logs_account_id" in indexes, "Missing account_id index on send_logs"
            
            cursor = await db.execute("PRAGMA index_list('reaction_logs')")
            indexes = [row[1] for row in await cursor.fetchall()]
            assert "idx_reaction_logs_target_acc" in indexes, "Missing target_acc index on reaction_logs"
            assert "idx_reaction_logs_account_id" in indexes, "Missing account_id index on reaction_logs"
            
            cursor = await db.execute("PRAGMA index_list('schedule_messages')")
            indexes = [row[1] for row in await cursor.fetchall()]
            assert "idx_schedule_messages_schedule_id" in indexes, "Missing schedule_id index on schedule_messages"
            
            cursor = await db.execute("PRAGMA index_list('schedule_targets')")
            indexes = [row[1] for row in await cursor.fetchall()]
            assert "idx_schedule_targets_schedule_id" in indexes, "Missing schedule_id index on schedule_targets"

            # Assert daily stats range indexes
            cursor = await db.execute("PRAGMA index_list('dm_campaign_logs')")
            indexes = [row[1] for row in await cursor.fetchall()]
            assert "idx_dm_campaign_logs_sent_at" in indexes, "Missing sent_at index on dm_campaign_logs"

            cursor = await db.execute("PRAGMA index_list('watcher_dm_logs')")
            indexes = [row[1] for row in await cursor.fetchall()]
            assert "idx_watcher_dm_logs_sent_at" in indexes, "Missing sent_at index on watcher_dm_logs"

            cursor = await db.execute("PRAGMA index_list('dm_replies')")
            indexes = [row[1] for row in await cursor.fetchall()]
            assert "idx_dm_replies_received_at" in indexes, "Missing received_at index on dm_replies"
            
        print("[OK] Foreign key and daily stats indexes successfully verified")

        # 2. Test Bulk Insertion
        members = [
            {"user_id": 1000 + i, "username": f"user_{i}", "first_name": "Test", "is_bot": False, "is_premium": True}
            for i in range(100)
        ]
        await database.save_scraped_members("job_1", 1, 101, "Test Group", members)
        count = await database.count_scraped_members("job_1")
        assert count == 100, f"Expected 100 members, got {count}"
        print("[OK] save_scraped_members bulk insertion verified")
        
        # 3. Test Analytics Overview
        overview = await database.get_analytics_overview()
        assert isinstance(overview, dict)
        assert overview.get("total_contacts") == 100
        print("[OK] get_analytics_overview query consolidated verified")

        # 4. Test Account Health
        # Insert mock account
        async with database.get_db() as db:
            await db.execute("INSERT INTO accounts (id, name, phone, api_id, api_hash, session_name) VALUES (1, 'Acc 1', '123', 'api', 'hash', 'sess')")
            await db.commit()
            
        health = await database.get_analytics_account_health()
        assert len(health) == 1
        assert health[0]["account_id"] == 1
        assert health[0]["health_score"] == 100
        print("[OK] get_analytics_account_health optimized query verified")

        # 5. Test Campaign Performance Consolidated Query
        async with database.get_db() as db:
            await db.execute("""
                INSERT INTO dm_campaigns (id, name, scrape_job_id, sent_count, failed_count)
                VALUES (1, 'Test Campaign', 'job_1', 10, 2)
            """)
            await db.execute("""
                INSERT INTO dm_campaign_logs (campaign_id, account_id, target_user_id, target_username, status)
                VALUES (1, 1, 12345, 'target_user', 'success')
            """)
            await db.execute("""
                INSERT INTO dm_replies (account_id, sender_user_id, message_text)
                VALUES (1, 12345, 'Hello back!')
            """)
            await db.commit()

        campaign_perf = await database.get_analytics_campaign_performance()
        assert len(campaign_perf) == 1
        assert campaign_perf[0]["id"] == 1
        assert campaign_perf[0]["reply_count"] == 1
        assert campaign_perf[0]["success_rate"] == 83.3 # 10 / 12 * 100
        print("[OK] get_analytics_campaign_performance consolidated query verified")

        # 6. Test Active Connection Validation (ConnectionPool liveness discard)
        conn1 = await database._pool.acquire()
        assert conn1._conn is not None
        await conn1.close()
        await database._pool.release(conn1)
        
        conn2 = await database._pool.acquire()
        assert conn2._conn is not None
        assert conn2 is not conn1
        await database._pool.release(conn2)
        print("[OK] Active connection validation and closed discard verified")

        print("All verification checks passed successfully!")

    finally:
        # Cleanup pool and temporary DB
        await database.close_db()
        shutil.rmtree(temp_dir)
        print("Cleanup completed.")

if __name__ == "__main__":
    asyncio.run(test_optimization())
