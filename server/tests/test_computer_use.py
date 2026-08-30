from app.computer_use import desktop_goal_met, needs_desktop, outcome_ok, parse_json_object, sanitize_gui_step, window_hint


def test_needs_desktop_and_goal():
    assert needs_desktop("напиши в блокнот hello")
    assert window_hint("напиши в блокнот hello") == "notepad"
    assert not desktop_goal_met("напиши в блокнот hello", [{"action": "run_powershell", "exit_code": 0}])
    assert desktop_goal_met("напиши в блокнот hello", [{"action": "type_text", "exit_code": 0}])


def test_parse_json_object_from_markdown():
    raw = '```json\n{"status":"done","outcome":"fail","message":"нет окна"}\n```'
    data = parse_json_object(raw)
    assert data["status"] == "done"
    assert data["outcome"] == "fail"


def test_sanitize_click_clamps_to_image():
    step = sanitize_gui_step(
        {"action": "click", "title": "OK", "params": {"x": 9000, "y": -4, "button": "left"}},
        1280,
        720,
    )
    assert step["action"] == "click"
    assert step["params"]["x"] == 1279
    assert step["params"]["y"] == 0
    assert step["params"]["image_width"] == 1280
    assert step["params"]["image_height"] == 720


def test_sanitize_type_and_key():
    typed = sanitize_gui_step({"action": "type_text", "params": {"text": "hello"}}, 100, 100)
    assert typed["params"]["text"] == "hello"
    key = sanitize_gui_step({"action": "press_key", "params": {"key": "Enter"}}, 100, 100)
    assert key["params"]["key"] == "enter"
    bad = sanitize_gui_step({"action": "press_key", "params": {"key": "ctrl+alt+del"}}, 100, 100)
    assert bad is None
    assert sanitize_gui_step({"action": "reboot", "params": {}}, 100, 100) is None


def test_outcome_ok():
    assert outcome_ok({"outcome": "success"})
    assert not outcome_ok({"outcome": "fail"})
    assert not outcome_ok({})


def test_mcp_lists_gui_tools(client):
    client.post("/api/register", json={"email": "gui@example.com", "password": "test-password-123"})
    login = client.post("/api/login", json={"email": "gui@example.com", "password": "test-password-123"})
    auth = {"Authorization": "Bearer " + login.json()["token"]}
    key = client.post("/api/mcp-key", headers=auth).json()["mcp_key"]
    rpc = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={"Authorization": f"Bearer {key}"},
    )
    names = {t["name"] for t in rpc.json()["result"]["tools"]}
    assert {"get_screen", "click", "type_text", "press_key", "scroll"} <= names
