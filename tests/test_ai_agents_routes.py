import json
import pytest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
import database as db
import ai_remix as ai_rmx

pytestmark = pytest.mark.asyncio

# ── 1. AI Agents CRUD & Duplicate ─────────────────────────────────────────────

async def test_list_agents_empty(client):
    response = client.get("/api/ai-agents")
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data
    assert len(data["agents"]) == 1
    assert data["agents"][0]["name"] == "🤖 Crypto Exchange BD Pro"
    assert "campaign_count" in data["agents"][0]

async def test_list_agents_existing(client):
    agent_id = await db.create_ai_agent({
        "name": "Custom Agent",
        "description": "Test description",
        "provider": "gemini",
        "api_keys_json": ["key1", "key2"]
    })
    response = client.get("/api/ai-agents")
    assert response.status_code == 200
    data = response.json()
    assert len(data["agents"]) >= 1
    agent = next(a for a in data["agents"] if a["id"] == agent_id)
    assert agent["name"] == "Custom Agent"

async def test_get_agent_success(client):
    agent_id = await db.create_ai_agent({"name": "Agent A"})
    response = client.get(f"/api/ai-agents/{agent_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Agent A"

async def test_get_agent_not_found(client):
    response = client.get("/api/ai-agents/9999")
    assert response.status_code == 404

async def test_create_agent_success(client):
    payload = {
        "name": "New Agent",
        "description": "Desc",
        "provider": "openai",
        "api_keys_json": ["sk-test"],
        "system_prompt": "System Prompt",
        "knowledge_base": "KB Content",
        "handover_keywords": ["human"],
        "max_replies": 5,
        "tone": "professional"
    }
    response = client.post("/api/ai-agents", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert "id" in response.json()

async def test_update_agent_success(client):
    agent_id = await db.create_ai_agent({"name": "Old Name", "provider": "gemini"})
    payload = {"name": "New Name", "provider": "openai"}
    response = client.put(f"/api/ai-agents/{agent_id}", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "updated"
    
    agent = await db.get_ai_agent(agent_id)
    assert agent["name"] == "New Name"
    assert agent["provider"] == "openai"

async def test_delete_agent_success(client):
    agent_id = await db.create_ai_agent({"name": "To Delete"})
    response = client.delete(f"/api/ai-agents/{agent_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    agent = await db.get_ai_agent(agent_id)
    assert agent["is_active"] == 0

async def test_duplicate_agent_success(client):
    agent_id = await db.create_ai_agent({"name": "Agent Original", "system_prompt": "Prompt"})
    response = client.post(f"/api/ai-agents/{agent_id}/duplicate")
    assert response.status_code == 200
    new_id = response.json()["id"]
    assert new_id != agent_id
    
    dup = await db.get_ai_agent(new_id)
    assert dup["name"] == "Agent Original - Copy"
    assert dup["system_prompt"] == "Prompt"


# ── 2. Diagnostic & Proxy Testing ────────────────────────────────────────────

@patch("httpx.AsyncClient.get")
@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
async def test_test_router_models_success(mock_chat, mock_get, client):
    mock_get.return_value.status_code = 200
    mock_get.return_value.json = lambda: {"data": [{"id": "gpt-4"}, {"id": "gpt-3.5"}]}
    mock_chat.return_value = "Test response"
    
    response = client.get("/api/ai-agents/test-router?model=gpt-4")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "connected"
    assert data["available_models"] == ["gpt-4", "gpt-3.5"]
    assert data["test_result"]["status"] == "ok"
    assert data["test_result"]["reply"] == "Test response"


# ── 3. Agent Execution Test ──────────────────────────────────────────────────

@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
async def test_test_agent_success(mock_chat, client):
    agent_id = await db.create_ai_agent({
        "name": "Test Agent API",
        "provider": "gemini",
        "api_keys_json": ["api-key-123"],
        "system_prompt": "Prompt system",
        "knowledge_base": "Knowledge content"
    })
    mock_chat.return_value = "AI Reply"
    
    payload = {"text": "Hello agent"}
    response = client.post(f"/api/ai-agents/{agent_id}/test", json=payload)
    assert response.status_code == 200
    assert response.json()["reply"] == "AI Reply"
    
    mock_chat.assert_called_once_with(
        [{"role": "user", "content": "Hello agent"}],
        "Prompt system\n\n--- KNOWLEDGE BASE ---\nKnowledge content",
        "gemini",
        ["api-key-123"]
    )

async def test_test_agent_no_keys_fallback_success(client):
    agent_id = await db.create_ai_agent({
        "name": "No Key Agent",
        "provider": "openai_compatible",
        "api_keys_json": []
    })
    await db.set_setting("ai_provider", "openai")
    await db.set_setting("ai_keys_openai", '["global-key"]')
    
    with patch("ai_remix.generate_chat_response", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = "Global Reply"
        response = client.post(f"/api/ai-agents/{agent_id}/test", json={"text": "Test"})
        assert response.status_code == 200
        assert response.json()["reply"] == "Global Reply"

async def test_test_agent_no_keys_anywhere(client):
    agent_id = await db.create_ai_agent({
        "name": "No Key Agent",
        "provider": "gemini",
        "api_keys_json": []
    })
    await db.set_setting("ai_provider", "")
    response = client.post(f"/api/ai-agents/{agent_id}/test", json={"text": "Test"})
    assert response.status_code == 400
    assert "chưa có API Key" in response.json()["detail"]


# ── 4. Chat Reset ────────────────────────────────────────────────────────────

async def test_reset_agent_chats_success(client):
    agent_id = await db.create_ai_agent({"name": "Agent"})
    await db.get_or_create_followup_chat(
        account_id=1, user_id=101, username="user", name="User"
    )
    await db.update_followup_chat_status(1, 101, "needs_human")
    
    response = client.post(f"/api/ai-agents/{agent_id}/reset-chats")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    
    chat = await db.get_followup_chat(1, 101)
    assert chat["status"] == "active"


# ── 5. Learned Rules & Profiles ──────────────────────────────────────────────

async def test_learned_rules_endpoints(client):
    agent_id = await db.create_ai_agent({"name": "Agent"})
    rule_id = await db.add_learned_knowledge(
        ai_agent_id=agent_id,
        source_user_id=101,
        question="price",
        answer="50USD",
        status="approved"
    )
    
    resp_get = client.get(f"/api/ai-agents/{agent_id}/learned-rules")
    assert resp_get.status_code == 200
    assert len(resp_get.json()["rules"]) == 1
    assert resp_get.json()["rules"][0]["learned_answer"] == "50USD"
    
    resp_del = client.delete(f"/api/ai-agents/{agent_id}/learned-rules/{rule_id}")
    assert resp_del.status_code == 200
    assert resp_del.json()["status"] == "deleted"

async def test_get_kol_profile(client):
    await db.upsert_kol_profile(1, 101, {"followers": "50k", "cex": "Binance"})
    response = client.get("/api/ai-agents/kol-profile/1/101")
    assert response.status_code == 200
    assert response.json()["profile"]["followers"] == "50k"


# ── 6. Follow-up Configuration settings ────────────────────────────────────────

async def test_followup_settings_success(client):
    payload = {
        "enabled": False,
        "system_prompt": "Custom prompt",
        "knowledge_base": "Custom kb",
        "max_replies_per_user": 12,
        "handover_keywords": ["human", "support"]
    }
    resp_post = client.post("/api/ai-followup/settings", json=payload)
    assert resp_post.status_code == 200
    
    resp_get = client.get("/api/ai-followup/settings")
    assert resp_get.status_code == 200
    data = resp_get.json()
    assert data["enabled"] is False
    assert data["system_prompt"] == "Custom prompt"
    assert data["max_replies_per_user"] == 12
    assert data["handover_keywords"] == ["human", "support"]

async def test_update_chat_status_success(client):
    await db.get_or_create_followup_chat(
        account_id=1, user_id=101, username="user", name="User"
    )
    response = client.post("/api/ai-followup/chats/1/101/status", json={"status": "paused_admin"})
    assert response.status_code == 200
    
    chat = await db.get_followup_chat(1, 101)
    assert chat["status"] == "paused_admin"

async def test_get_chat_summary_success(client):
    await db.get_or_create_followup_chat(
        account_id=1, user_id=101, username="user", name="User"
    )
    await db.update_followup_lead_metrics(1, 101, 80, "Tier A", "Wants to test CEX")
    
    response = client.get("/api/ai-followup/chats/1/101/summary")
    assert response.status_code == 200
    assert response.json()["lead_tier"] == "Tier A"
    assert response.json()["intent_score"] == 80
    assert response.json()["summary"] == "Wants to test CEX"
