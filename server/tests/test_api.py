def test_root_serves_ui(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert b"chat-in" in response.content
    assert b"btn-send" in response.content


def test_pc_chat_page(client):
    page = client.get("/pc-chat")
    assert page.status_code == 200
    assert b"pc-chat.js" in page.content
    js = client.get("/static/pc-chat.js")
    assert js.status_code == 200
    assert b"/api/pc/session" in js.content


def test_register_and_login(client):
    email = "ci@example.com"
    password = "test-password-123"

    register = client.post("/api/register", json={"email": email, "password": password})
    assert register.status_code == 200

    login = client.post("/api/login", json={"email": email, "password": password})
    assert login.status_code == 200
    token = login.json()["token"]
    assert token

    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == email


def test_usb_maker_scripts(client):
    ps1 = client.get("/usb-maker.ps1")
    assert ps1.status_code == 200
    assert b"AI PC Agent" in ps1.content

    writer = client.get("/usb-maker/write_linux_usb.ps1")
    assert writer.status_code == 200
    assert b"Alpine" in writer.content

    boot = client.get("/linux_usb/agent-boot.sh")
    assert boot.status_code == 200
    assert b"AIAgent" in boot.content

    bat = client.get("/usb-maker.bat")
    assert bat.status_code == 200
    assert b"@echo off" in bat.content


def test_install_agent_downloads(client):
    client.post("/api/register", json={"email": "agent@example.com", "password": "test-password-123"})
    login = client.post("/api/login", json={"email": "agent@example.com", "password": "test-password-123"})
    auth = {"Authorization": "Bearer " + login.json()["token"]}
    stick = client.post("/api/sticks", json={"label": "PC"}, headers=auth)
    enroll = stick.json()["token"]
    assert stick.json()["agent_windows"].endswith(f"token={enroll}")

    bat = client.get("/install-agent.bat", params={"token": enroll})
    assert bat.status_code == 200
    text = bat.content.decode("ascii")
    assert f"TOKEN={enroll}" in text
    assert "/install.ps1" in text

    sh = client.get("/install-agent.sh", params={"token": enroll})
    assert sh.status_code == 200
    assert enroll.encode() in sh.content


def test_usb_maker_bat_embeds_token(client):
    client.post("/api/register", json={"email": "usb@example.com", "password": "test-password-123"})
    login = client.post("/api/login", json={"email": "usb@example.com", "password": "test-password-123"})
    auth = {"Authorization": "Bearer " + login.json()["token"]}
    stick = client.post("/api/sticks", json={"label": "PC"}, headers=auth)
    enroll = stick.json()["token"]
    bat = client.get("/usb-maker.bat", params={"token": enroll})
    assert bat.status_code == 200
    text = bat.content.decode("ascii")
    assert f"TOKEN={enroll}" in text
    assert "-Token" in text


def test_mcp_rejects_invalid_key(client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={"Authorization": "Bearer invalid"},
    )
    assert response.status_code == 401


def test_pc_session_needs_valid_token(client):
    bad = client.post("/api/pc/session", json={"token": "NOPE", "device_id": "PC"})
    assert bad.status_code == 401


def test_pc_session_and_chat(client):
    from app.db import Device, SessionLocal

    client.post("/api/register", json={"email": "pcchat@example.com", "password": "test-password-123"})
    login = client.post("/api/login", json={"email": "pcchat@example.com", "password": "test-password-123"})
    auth = {"Authorization": "Bearer " + login.json()["token"]}
    stick = client.post("/api/sticks", json={"label": "PC"}, headers=auth)
    enroll = stick.json()["token"]

    missing = client.post("/api/pc/session", json={"token": enroll, "device_id": "LAPTOP-TEST"})
    assert missing.status_code == 404

    me = client.get("/api/me", headers=auth).json()
    db = SessionLocal()
    try:
        db.add(
            Device(
                owner_id=me["id"],
                device_id="LAPTOP-TEST",
                hostname="LAPTOP-TEST",
                os="windows",
                hardware="{}",
                status="offline",
            )
        )
        db.commit()
    finally:
        db.close()

    session = client.post("/api/pc/session", json={"token": enroll, "device_id": "LAPTOP-TEST"})
    assert session.status_code == 200
    body = session.json()
    assert body["device_id"] == "LAPTOP-TEST"
    assert body["token"]

    chat = client.post(
        "/api/devices/LAPTOP-TEST/chat",
        json={"message": "покажи железо"},
        headers={"Authorization": "Bearer " + body["token"]},
    )
    assert chat.status_code == 200
    assert chat.json()["status"] in ("done", "waiting_device")
    messages = client.get("/api/devices/LAPTOP-TEST/messages", headers=auth)
    assert messages.status_code == 200
    roles = [m["role"] for m in messages.json()]
    assert "user" in roles
    assert "assistant" in roles
