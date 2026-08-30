def test_root_serves_ui(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


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

    writer = client.get("/usb-maker/write_usb.ps1")
    assert writer.status_code == 200
    assert b"WinRE" in writer.content

    bat = client.get("/usb-maker.bat")
    assert bat.status_code == 200
    assert b"@echo off" in bat.content


def test_mcp_rejects_invalid_key(client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={"Authorization": "Bearer invalid"},
    )
    assert response.status_code == 401
