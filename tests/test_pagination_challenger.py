import pytest
import database as db

@pytest.mark.asyncio
async def test_scraped_members_pagination_mismatch():
    """
    Verify that get_scraped_members uses consistent ordering between the
    initial chunk and keyset pagination chunks (ORDER BY id ASC),
    preventing duplicate and missed records.
    """
    scrape_job_id = "test_pagination_mismatch_job"
    
    # Clean up first
    async with db.get_db() as conn:
        await conn.execute("DELETE FROM scraped_members WHERE scrape_job_id = ?", (scrape_job_id,))
        await conn.commit()
        
    # Insert members sequentially to control generated autoincrement IDs
    # david -> id 1
    # charlie -> id 2
    # bob -> id 3
    # alice -> id 4
    members = [
        {"user_id": 104, "username": "david"},
        {"user_id": 103, "username": "charlie"},
        {"user_id": 102, "username": "bob"},
        {"user_id": 101, "username": "alice"},
    ]
    
    for m in members:
        await db.save_scraped_members(scrape_job_id, 1, 1, "Group", [m])
        
    # Step 1: Fetch first chunk (offset=0, limit=2). Sorts by id ASC.
    # Expected returned: david (id=1), charlie (id=2)
    first_chunk = await db.get_scraped_members(scrape_job_id, limit=2, offset=0)
    assert len(first_chunk) == 2
    assert first_chunk[0]["username"] == "david"
    assert first_chunk[1]["username"] == "charlie"
    
    last_id = first_chunk[-1]["id"]
    # charlie's id is 2
    assert last_id == 2
    
    # Step 2: Fetch next chunk using keyset pagination (last_id=2, limit=2).
    # Since it uses id > 2, it should return bob (id=3) and alice (id=4).
    second_chunk = await db.get_scraped_members(scrape_job_id, limit=2, last_id=last_id)
    assert len(second_chunk) == 2
    assert second_chunk[0]["username"] == "bob"
    assert second_chunk[1]["username"] == "alice"
    
    # Verify no duplicates and no missed items
    returned_usernames = [m["username"] for m in first_chunk + second_chunk]
    print("Returned usernames:", returned_usernames)
    
    # Keyset pagination should yield all 4 unique members correctly.
    assert len(returned_usernames) == 4
    assert set(returned_usernames) == {"david", "charlie", "bob", "alice"}
