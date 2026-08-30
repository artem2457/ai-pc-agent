from llm import fallback_plan, detect_profile


def test_profiles():
    assert detect_profile("поставь docker nginx") == "server"
    plan = fallback_plan("Установи git и python", "linux")
    names = [s["params"].get("name") for s in plan if s["action"] == "install_package"]
    assert "git" in names
    print("ok", plan)


if __name__ == "__main__":
    test_profiles()
