from app.llm import (
    extract_target,
    fallback_next,
    fallback_plan,
    intent_step,
    is_simple_package_request,
    sanitize_step,
)


def test_detect_profile_server():
    from app.llm import detect_profile

    assert detect_profile("поставь docker nginx") == "server"


def test_fallback_plan_opens_notepad_for_gui_task():
    plan = fallback_plan("напиши в блокнот hello", "windows")
    assert plan[0]["action"] == "run_powershell"
    assert "notepad" in plan[0]["params"]["script"].lower()


def test_fallback_next_screens_after_app_opened():
    nxt = fallback_next(
        "напиши в блокнот hello",
        "windows",
        [
            {
                "title": "Блокнот",
                "action": "run_powershell",
                "params": {"script": "Start-Process notepad"},
                "exit_code": 0,
                "console": "ok",
            }
        ],
    )
    assert nxt["status"] == "step"
    assert nxt["action"] == "get_screen"
    plan = fallback_plan("покажи процессы", "windows")
    assert len(plan) == 1
    assert plan[0]["action"] == "get_processes"


def test_fallback_plan_installs_simple_package():
    plan = fallback_plan("Установи git", "linux")
    assert plan[0]["action"] == "install_package"
    assert plan[0]["params"]["name"] == "git"


def test_fallback_plan_complex_install_uses_shell():
    plan = fallback_plan("Установи git и python", "linux")
    assert plan[0]["action"] == "run_shell"
    assert "git" in plan[0]["params"]["script"]


def test_extract_product_key():
    from app.llm import extract_product_key

    key = extract_product_key("ключ XXXXX-XXXXX-XXXXX-XXXXX-XXXXX для windows")
    assert key == "XXXXX-XXXXX-XXXXX-XXXXX-XXXXX"


def test_sanitize_drops_reboot_unless_asked():
    reboot = {"title": "reboot", "action": "reboot", "params": {}}
    assert sanitize_step(reboot, "установи chrome") is None
    assert sanitize_step(reboot, "перезагрузи компьютер") is not None


def test_sanitize_fills_empty_script_from_user_text():
    step = sanitize_step({"title": "cmd", "action": "run_powershell", "params": {}}, "Get-Date")
    assert step["params"]["script"] == "Get-Date"


def test_fallback_next_uses_console_after_failed_download():
    first = fallback_next("установи SuperApp", "windows", [])
    assert first["status"] == "step"
    assert first["action"] == "install_package"

    after_404 = fallback_next(
        "установи SuperApp",
        "windows",
        [
            {
                "title": "download",
                "action": "download_file",
                "exit_code": 1,
                "console": "HTTP Error 404: Not Found",
            }
        ],
    )
    assert after_404["status"] == "step"
    assert after_404["action"] == "install_package"


def test_fallback_next_retries_msstore_error_from_log():
    nxt = fallback_next(
        "установи SuperApp",
        "windows",
        [
            {
                "title": "install",
                "action": "install_package",
                "exit_code": 1,
                "console": "Сбой при поиске в источнике: msstore\n0x8a150044",
            }
        ],
    )
    assert nxt["status"] == "step"
    assert nxt["action"] == "run_powershell"
    assert "--source winget" in nxt["params"]["script"]


def test_extract_target_generic():
    assert "SuperApp" in extract_target("установи SuperApp")
    assert "Foo Bar" in extract_target("деинсталлируй Foo Bar")


def test_sanitize_fills_empty_package_name():
    step = sanitize_step(
        {"title": "Установка Notepad++", "action": "install_package", "params": {}},
        "установи notepad++",
    )
    assert step is not None
    assert "notepad" in step["params"]["name"].lower()


def test_intent_step_arbitrary_command():
    step = intent_step("dir C:\\Users", "windows")
    assert step["action"] == "run_powershell"
    assert step["params"]["script"] == "dir C:\\Users"


def test_is_simple_package_request():
    assert is_simple_package_request("установи notepad++")
    assert not is_simple_package_request("установи git и настрой репозиторий")
