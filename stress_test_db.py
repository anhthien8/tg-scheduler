import asyncio
import os
import shutil
import tempfile
import sys
import random
import time

# Setup path so it runs in project root
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import database

async def populate_mock_data():
    print("Populating initial mock data...")
    # Add accounts
    for i in range(1, 11):
        await database.create_account({
            "name": f"Account {i}",
            "phone": f"+12345678{i:02d}",
            "api_id": f"api_id_{i}",
            "api_hash": f"api_hash_{i}",
            "session_name": f"session_name_{i}",
            "proxy_url": None
        })
    
    # Add schedules, messages, and targets
    for i in range(1, 21):
        sch_id = await database.create_schedule({
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
            await database.add_send_log(
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
        await database.add_watcher_dm_log(
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
    await database.save_scraped_members("job_stress", 1, 101, "Stress Group", members)

    # Add DM replies
    for i in range(10):
        await database.add_dm_reply({
            "account_id": random.randint(1, 10),
            "sender_user_id": 2000 + i,
            "sender_username": f"scraped_{i}",
            "sender_name": "Scraped",
            "message_text": f"Reply message {i}",
            "watcher_id": random.randint(1, 5),
            "platform": "telegram"
        })

    print("Initial mock data population done.")

async def worker(worker_id: int, num_iterations: int, stats: dict):
    print(f"Worker {worker_id} started.")
    
    # We will simulate concurrent requests
    for i in range(num_iterations):
        # We randomly pick an action
        action = random.choice([
            "get_analytics_overview",
            "get_all_schedules",
            "get_analytics_account_health",
            "write_log",
            "write_account"
        ])
        
        try:
            start_time = time.perf_counter()
            if action == "get_analytics_overview":
                res = await database.get_analytics_overview()
                assert isinstance(res, dict)
                stats["reads"] += 1
            elif action == "get_all_schedules":
                res = await database.get_all_schedules()
                assert isinstance(res, list)
                stats["reads"] += 1
            elif action == "get_analytics_account_health":
                res = await database.get_analytics_account_health()
                assert isinstance(res, list)
                stats["reads"] += 1
            elif action == "write_log":
                await database.add_send_log(
                    schedule_id=random.randint(1, 20),
                    account_id=random.randint(1, 10),
                    message_id=random.randint(100, 999),
                    chat_id=random.randint(1000, 1020),
                    chat_title="Stress Group",
                    status="success"
                )
                stats["writes"] += 1
            elif action == "write_account":
                await database.create_account({
                    "name": f"Stress Account {worker_id}_{i}",
                    "phone": f"+1234567{worker_id:02d}{i:02d}",
                    "api_id": f"api_{worker_id}_{i}",
                    "api_hash": f"hash_{worker_id}_{i}",
                    "session_name": f"sess_{worker_id}_{i}_{random.randint(0, 1000000)}",
                    "proxy_url": None
                })
                stats["writes"] += 1
                
            latency = time.perf_counter() - start_time
            stats["latencies"].append(latency)
            
        except Exception as e:
            error_msg = str(e)
            print(f"Worker {worker_id} failed on {action} with error: {type(e).__name__}: {error_msg}")
            stats["errors"] += 1
            if "lock" in error_msg.lower() or "busy" in error_msg.lower():
                stats["lock_errors"] += 1
        
        # yield control briefly
        await asyncio.sleep(0.01)
        
    print(f"Worker {worker_id} finished.")

async def run_stress_test():
    print("Setting up temporary database for stress test...")
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "stress_scheduler.db")
    
    # Override global DB_PATH in imported module
    database.DB_PATH = test_db
    
    try:
        # Initialize DB
        await database.init_db()
        print("Database initialized.")
        
        # Populate initial data
        await populate_mock_data()
        
        # Run workers
        num_workers = 10
        num_iterations = 50
        
        stats = {
            "reads": 0,
            "writes": 0,
            "errors": 0,
            "lock_errors": 0,
            "latencies": []
        }
        
        print(f"Launching {num_workers} concurrent workers executing {num_iterations} operations each...")
        start_time = time.perf_counter()
        
        workers = [worker(w_id, num_iterations, stats) for w_id in range(num_workers)]
        await asyncio.gather(*workers)
        
        total_time = time.perf_counter() - start_time
        print("\n--- Stress Test Results ---")
        print(f"Total duration: {total_time:.2f} seconds")
        print(f"Total read operations: {stats['reads']}")
        print(f"Total write operations: {stats['writes']}")
        print(f"Total errors: {stats['errors']}")
        print(f"Total DB lock/busy errors: {stats['lock_errors']}")
        if stats["latencies"]:
            avg_latency = sum(stats["latencies"]) / len(stats["latencies"])
            print(f"Average operation latency: {avg_latency*1000:.2f} ms")
        else:
            print("Average operation latency: N/A")
            
        # Verify connection pool safety and check for leaks
        pool = database._pool
        if pool:
            active_connections = len(pool._connections)
            available_in_queue = pool._queue.qsize()
            print(f"Pool status after run: Total created connections = {active_connections}, Available in queue = {available_in_queue}")
            
            leak_detected = active_connections != available_in_queue
            if leak_detected:
                print("[FAIL] Connection leak detected! Not all connections were released back to the queue.")
            else:
                print("[OK] Connection pool is completely clean. No connection leaks detected!")
            
            assert not leak_detected, "Connection leak detected"
        else:
            print("[WARN] Connection pool was not initialized?")
            
        assert stats["errors"] == 0, f"Expected 0 errors, got {stats['errors']}"
        assert stats["lock_errors"] == 0, f"Expected 0 database lock errors, got {stats['lock_errors']}"
        print("[SUCCESS] All concurrent operations completed without errors or database locks!")

    finally:
        # Cleanup
        print("Closing database and cleaning up temp files...")
        await database.close_db()
        shutil.rmtree(temp_dir)
        print("Cleanup completed.")

if __name__ == "__main__":
    asyncio.run(run_stress_test())
