import json

from app.db import Device, Escalation, SessionLocal, Task
from app.escalation import build_grok_prompt, chat_succeeded


def test_chat_succeeded():
    assert not chat_succeeded([])
    assert chat_succeeded([{"exit_code": 0}])
    assert not chat_succeeded([{"exit_code": 1}])


def test_build_grok_prompt():
    device = Device(device_id="LAPTOP-1", hostname="LAPTOP-1", os="windows")
    history = [{"title": "cmd", "exit_code": 1, "console": "error line"}]
    text = build_grok_prompt(device, "установи foo", history, "exit 1")
    assert "LAPTOP-1" in text
    assert "установи foo" in text
    assert "list_escalations" in text
    assert "error line" in text


def test_grok_handoff_api(client):
    client.post("/api/register", json={"email": "grok@example.com", "password": "test-password-123"})
    login = client.post("/api/login", json={"email": "grok@example.com", "password": "test-password-123"})
    auth = {"Authorization": "Bearer " + login.json()["token"]}
    me = client.get("/api/me", headers=auth).json()

    db = SessionLocal()
    try:
        device = Device(
            owner_id=me["id"],
            device_id="GROK-PC",
            hostname="GROK-PC",
            os="windows",
            hardware="{}",
            status="offline",
        )
        db.add(device)
        db.commit()
        db.refresh(device)
        task = Task(device_pk=device.id, user_message="fail task", status="escalated", plan_json="[]")
        db.add(task)
        db.commit()
        db.refresh(task)
        ctx = {"grok_prompt": "continue on PC", "mcp_url": "https://example/mcp"}
        db.add(
            Escalation(
                task_id=task.id,
                device_pk=device.id,
                owner_id=me["id"],
                user_message="fail task",
                reason="test",
                context_json=json.dumps(ctx),
                status="pending",
            )
        )
        db.commit()
    finally:
        db.close()

    handoff = client.get("/api/grok-handoff", headers=auth)
    assert handoff.status_code == 200
    body = handoff.json()
    assert body["mcp_url"]
    assert len(body["pending"]) == 1
    assert body["pending"][0]["user_message"] == "fail task"


def test_mcp_list_escalations(client):
    client.post("/api/register", json={"email": "mcp@example.com", "password": "test-password-123"})
    login = client.post("/api/login", json={"email": "mcp@example.com", "password": "test-password-123"})
    auth = {"Authorization": "Bearer " + login.json()["token"]}
    mcp = client.post("/api/mcp-key", headers=auth)
    key = mcp.json()["mcp_key"]

    rpc = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_escalations", "arguments": {}}},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert rpc.status_code == 200
    result = rpc.json()["result"]["content"][0]["text"]
    data = json.loads(result)
    assert "escalations" in data
