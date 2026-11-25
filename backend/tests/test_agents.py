import pytest

from app.agents.base import AgentContext
from app.agents.registry import (
    DataAnalysisAgent,
    EmailCalendarAgent,
    TranslationAgent,
    build_registry,
    master_plan,
)
from conftest import FakeProvider, auth_headers, make_router, register_user

pytestmark = pytest.mark.asyncio


def ctx_for(provider):
    return AgentContext(user_id="u1", router=make_router(provider))


async def test_registry_has_all_specialized_agents():
    registry = build_registry()
    expected = {
        "research", "coding", "android_dev", "web_dev", "backend_dev", "file",
        "image", "audio", "translation", "browser", "data_analysis",
        "task_management", "email_calendar",
    }
    assert expected <= set(registry)


async def test_translation_agent_uses_llm():
    provider = FakeProvider(reply="مرحبا بالعالم")
    result = await TranslationAgent().run(ctx_for(provider), "hello world to arabic")
    assert result.ok
    assert result.output == "مرحبا بالعالم"


async def test_data_analysis_agent_computes_real_stats():
    result = await DataAnalysisAgent().run(ctx_for(FakeProvider()), "القيم: 10 20 30")
    assert result.ok
    assert result.data["mean"] == 20.0
    assert result.data["count"] == 3


async def test_stub_agent_is_honest():
    result = await EmailCalendarAgent().run(ctx_for(FakeProvider()), "ابعت إيميل")
    assert not result.ok
    assert "لم يُنفَّذ شيء" in result.output
    assert result.data["missing"]


async def test_master_plan_parses_and_filters():
    plan_json = (
        'مقدمة [{"agent": "research", "instruction": "ابحث"}, '
        '{"agent": "nonexistent", "instruction": "x"}, '
        '{"agent": "translation", "instruction": "ترجم"}] تعليق'
    )
    provider = FakeProvider(reply=plan_json)
    registry = build_registry()
    plan = await master_plan(ctx_for(provider), "مهمة", registry)
    assert [s["agent"] for s in plan] == ["research", "translation"]


async def test_agent_run_endpoint_executes_plan(client, monkeypatch):
    plan_json = '[{"agent": "data_analysis", "instruction": "حلل: 5 10 15"}]'
    provider = FakeProvider(reply=plan_json)
    client.app.state.provider_router = make_router(provider)
    token = await register_user(client, "agent@example.com")
    resp = await client.post(
        "/v1/agent/run",
        json={"task": "حلل الأرقام دي"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "done"
    assert data["steps"][0]["agent"] == "data_analysis"
    assert data["steps"][0]["status"] == "done"
    assert "30" in data["steps"][0]["output"]  # sum


async def test_sensitive_agent_blocked_without_approval(client):
    plan_json = '[{"agent": "email_calendar", "instruction": "ابعت إيميل"}]'
    provider = FakeProvider(reply=plan_json)
    client.app.state.provider_router = make_router(provider)
    token = await register_user(client, "sens@example.com")

    resp = await client.post(
        "/v1/agent/run",
        json={"task": "ابعت إيميل", "approved": False},
        headers=auth_headers(token),
    )
    data = resp.json()
    assert data["status"] == "done_with_blocked"
    assert data["blocked"] == ["email_calendar"]
    assert data["steps"][0]["status"] == "blocked"


async def test_sensitive_agent_with_approval_runs_and_reports_stub(client):
    plan_json = '[{"agent": "email_calendar", "instruction": "ابعت إيميل"}]'
    provider = FakeProvider(reply=plan_json)
    client.app.state.provider_router = make_router(provider)
    token = await register_user(client, "sens2@example.com")
    resp = await client.post(
        "/v1/agent/run",
        json={"task": "ابعت إيميل", "approved": True},
        headers=auth_headers(token),
    )
    data = resp.json()
    assert data["blocked"] == []
    # With approval it runs — and honestly reports the missing connection.
    assert data["steps"][0]["status"] == "failed"
    assert "لم يُنفَّذ شيء" in data["steps"][0]["output"]


async def test_agent_catalog_lists_agents(client):
    token = await register_user(client, "cat@example.com")
    resp = await client.get("/v1/agent/catalog", headers=auth_headers(token))
    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()}
    assert "research" in names and "email_calendar" in names
    sensitive = {a["name"] for a in resp.json() if a["sensitive"]}
    assert sensitive == {"email_calendar"}
