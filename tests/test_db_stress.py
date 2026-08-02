import pytest
import asyncio
import random
import time
import database as db

# Mark all test functions in this module as async
pytestmark = pytest.mark.asyncio

async def populate_mock_data():
    # Add accounts
    for i in range(1, 11):
        await db.create_account({
            "name": f"Account {i}",
            "phone": f"+12345678{i:02d}",
            "api_id": f"api_id_{i}",
            "api_hash": f"api_hash_{i}",
            "session_name": f"session_name_{i}",
            "proxy_url": None
        })
    
    # Add schedules, messages, and targets
    for i in range(1, 21):
        sch_id = await db.create_schedule({
            "account_id": random.randint(1, 10),
            "name": f"Schedule {i}",
            "schedule_type": "daily",
            "time_of_day": "12:00",
            "is_active": 1,
            "messages": [
                {"msg_order": 1, "msg_type": "text", "content": f"Message for schedule {i}"}
            ],
            "targets": [
                {"chat_id": 1000 + i, "chat_title": f"Target Group {i}", "chat_type": "group"}
            ]
        })
        
        # Add send logs
        for j in range(10):
            await db.add_send_log(
                schedule_id=sch_id,
                account_id=random.randint(1, 10),
                message_id=random.randint(100, 999),
                chat_id=1000 + i,
                chat_title=f"Target Group {i}",
                status=random.choice(["success", "failed", "skipped"]),
                error_message="Some error" if random.random() < 0.2 else None
            )

    # Add watcher logs
    for i in range(50):
        await db.add_watcher_dm_log(
            watcher_id=random.randint(1, 5),
            account_id=random.randint(1, 10),
            target_user_id=random.randint(5000, 9999),
            target_username=f"user_{i}",
            group_id=random.randint(1000, 2000),
            group_title="Watcher Group",
            matched_keyword="crypto",
            status=random.choice(["success", "failed", "skipped"]),
            error_message="Flood" if random.random() < 0.1 else None
        )

    # Add scraped members
    members = [
        {"user_id": 2000 + i, "username": f"scraped_{i}", "first_name": "Scraped", "is_bot": False, "is_premium": False}
        for i in range(200)
    ]
    await db.save_scraped_members("job_stress", 1, 101, "Stress Group", members)

    # Add DM replies
    for i in range(10):
        await db.add_dm_reply({
            "account_id": random.randint(1, 10),
            "sender_user_id": 2000 + i,
            "sender_username": f"scraped_{i}",
            "sender_name": "Scraped",
            "message_text": f"Reply message {i}",
            "watcher_id": random.randint(1, 5),
            "platform": "telegram"
        })

async def worker(worker_id: int, num_iterations: int, stats: dict):
    for i in range(num_iterations):
        action = random.choice([
            "get_analytics_overview",
            "get_all_schedules",
            "get_analytics_account_health",
            "write_log",
            "write_account"
        ])
        
        try:
            if action == "get_analytics_overview":
                res = await db.get_analytics_overview()
                assert isinstance(res, dict)
                stats["reads"] += 1
            elif action == "get_all_schedules":
                res = await db.get_all_schedules()
                assert isinstance(res, list)
                stats["reads"] += 1
            elif action == "get_analytics_account_health":
                res = await db.get_analytics_account_health()
                assert isinstance(res, list)
                stats["reads"] += 1
            elif action == "write_log":
                await db.add_send_log(
                    schedule_id=random.randint(1, 20),
                    account_id=random.randint(1, 10),
                    message_id=random.randint(100, 999),
                    chat_id=random.randint(1000, 1020),
                    chat_title="Stress Group",
                    status="success"
                )
                stats["writes"] += 1
            elif action == "write_account":
                await db.create_account({
                    "name": f"Stress Account {worker_id}_{i}",
                    "phone": f"+1234567{worker_id:02d}{i:02d}",
                    "api_id": f"api_{worker_id}_{i}",
                    "api_hash": f"hash_{worker_id}_{i}",
                    "session_name": f"sess_{worker_id}_{i}_{random.randint(0, 1000000)}",
                    "proxy_url": None
                })
                stats["writes"] += 1
                
        except Exception as e:
            error_msg = str(e)
            stats["errors"] += 1
            if "lock" in error_msg.lower() or "busy" in error_msg.lower():
                stats["lock_errors"] += 1
        
        await asyncio.sleep(0.005)

async def test_db_concurrency_stress():
    # Initialize DB (conftest already overrides DB_PATH to a temporary file)
    await db.init_db()
    await populate_mock_data()
    
    num_workers = 10
    num_iterations = 50
    
    stats = {
        "reads": 0,
        "writes": 0,
        "errors": 0,
        "lock_errors": 0
    }
    
    workers = [worker(w_id, num_iterations, stats) for w_id in range(num_workers)]
    await asyncio.gather(*workers)
    
    # Verify pool state
    pool = db._pool
    assert pool is not None, "Connection pool should not be None"
    
    active_connections = len(pool._connections)
    available_in_queue = pool._queue.qsize()
    
    leak_detected = active_connections != available_in_queue
    
    # Clean up pool
    await db.close_db()
    
    assert not leak_detected, f"Connection leak detected: {active_connections} total connections, but only {available_in_queue} in queue"
    assert stats["errors"] == 0, f"Expected 0 errors, got {stats['errors']}"
    assert stats["lock_errors"] == 0, f"Expected 0 database lock errors, got {stats['lock_errors']}"
