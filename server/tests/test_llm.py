from app.llm import extract_target, fallback_next, fallback_plan, sanitize_step


def test_detect_profile_server():
    from app.llm import detect_profile

    assert detect_profile("поставь docker nginx") == "server"


def test_fallback_plan_installs_git_and_python():
    plan = fallback_plan("Установи git и python", "linux")
    names = [s["params"].get("name") for s in plan if s["action"] == "install_package"]
    assert "git" in names
    assert "python3" in names


def test_extract_product_key():
    from app.llm import extract_product_key

    key = extract_product_key("ключ XXXXX-XXXXX-XXXXX-XXXXX-XXXXX для windows")
    assert key == "XXXXX-XXXXX-XXXXX-XXXXX-XXXXX"


def test_sanitize_drops_reboot_unless_asked():
    reboot = {"title": "reboot", "action": "reboot", "params": {}}
    assert sanitize_step(reboot, "установи chrome") is None
    assert sanitize_step(reboot, "перезагрузи компьютер") is not None


def test_fallback_next_uses_console_after_failed_download():
    first = fallback_next("установи SuperApp", "windows", [])
    assert first["status"] == "step"
    assert first["action"] == "install_package"
    assert "SuperApp" in first["params"]["name"]

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
