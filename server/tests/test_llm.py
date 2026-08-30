from app.llm import detect_profile, extract_product_key, fallback_plan


def test_detect_profile_server():
    assert detect_profile("поставь docker nginx") == "server"


def test_fallback_plan_installs_git_and_python():
    plan = fallback_plan("Установи git и python", "linux")
    names = [s["params"].get("name") for s in plan if s["action"] == "install_package"]
    assert "git" in names
    assert "python3" in names


def test_extract_product_key():
    key = extract_product_key("ключ XXXXX-XXXXX-XXXXX-XXXXX-XXXXX для windows")
    assert key == "XXXXX-XXXXX-XXXXX-XXXXX-XXXXX"
