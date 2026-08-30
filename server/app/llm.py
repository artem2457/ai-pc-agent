import json
import re

import httpx

from .config import settings

ALLOWED_ACTIONS = {
    "run_powershell",
    "run_shell",
    "get_hardware",
    "get_system_info",
    "install_package",
    "download_file",
    "reboot",
    "shutdown",
    "manage_service",
    "install_windows",
    "partition_disk",
}

PRODUCT_KEY = re.compile(r"\b(?:[A-Z0-9]{5}-){4}[A-Z0-9]{5}\b", re.I)

PROFILES = {
    "programmer": {
        "windows": [
            {"title": "Проверка железа", "action": "get_hardware", "params": {}},
            {"title": "Git", "action": "install_package", "params": {"name": "Git.Git"}},
            {"title": "Python", "action": "install_package", "params": {"name": "Python.Python.3.13"}},
            {"title": "VS Code", "action": "install_package", "params": {"name": "Microsoft.VisualStudioCode"}},
            {"title": "Chrome", "action": "install_package", "params": {"name": "Google.Chrome"}},
        ],
        "linux": [
            {"title": "Проверка системы", "action": "get_system_info", "params": {}},
            {"title": "Git", "action": "install_package", "params": {"name": "git"}},
            {"title": "Python", "action": "install_package", "params": {"name": "python3"}},
            {"title": "Docker", "action": "install_package", "params": {"name": "docker.io"}},
            {"title": "nginx", "action": "install_package", "params": {"name": "nginx"}},
        ],
    },
    "office": {
        "windows": [
            {"title": "Проверка железа", "action": "get_hardware", "params": {}},
            {"title": "Chrome", "action": "install_package", "params": {"name": "Google.Chrome"}},
            {"title": "7-Zip", "action": "install_package", "params": {"name": "7zip.7zip"}},
        ],
        "linux": [
            {"title": "Проверка системы", "action": "get_system_info", "params": {}},
            {"title": "Firefox", "action": "install_package", "params": {"name": "firefox"}},
            {"title": "LibreOffice", "action": "install_package", "params": {"name": "libreoffice"}},
        ],
    },
    "electrician": {
        "windows": [
            {"title": "Проверка железа", "action": "get_hardware", "params": {}},
            {"title": "Firefox", "action": "install_package", "params": {"name": "Mozilla.Firefox"}},
            {"title": "PDF", "action": "install_package", "params": {"name": "Adobe.Acrobat.Reader.64-bit"}},
            {"title": "7-Zip", "action": "install_package", "params": {"name": "7zip.7zip"}},
        ],
        "linux": [
            {"title": "Проверка системы", "action": "get_system_info", "params": {}},
            {"title": "Firefox", "action": "install_package", "params": {"name": "firefox"}},
        ],
    },
    "server": {
        "linux": [
            {"title": "Проверка системы", "action": "get_system_info", "params": {}},
            {"title": "Docker", "action": "install_package", "params": {"name": "docker.io"}},
            {"title": "nginx", "action": "install_package", "params": {"name": "nginx"}},
            {"title": "certbot", "action": "install_package", "params": {"name": "certbot"}},
        ],
        "windows": [
            {"title": "Проверка железа", "action": "get_hardware", "params": {}},
        ],
    },
}


def detect_profile(text: str) -> str | None:
    t = text.lower()
    if any(w in t for w in ("программист", "developer", "vscode", "vs code", "python", "git", "docker", "wsl")):
        if any(w in t for w in ("nginx", "ssl", "vps", "сервер", "firewall")):
            return "server"
        return "programmer"
    if any(w in t for w in ("электрик", "pdf", "офис электрика")):
        return "electrician"
    if any(w in t for w in ("офис", "chrome", "office")):
        return "office"
    if any(w in t for w in ("nginx", "ssl", "certbot", "vps", "firewall")):
        return "server"
    return None


def family(os_name: str) -> str:
    if os_name in ("windows", "winpe"):
        return "windows"
    return "linux"


def extract_product_key(text: str) -> str | None:
    m = PRODUCT_KEY.search(text or "")
    return m.group(0).upper() if m else None


def fallback_plan(text: str, os_name: str) -> list[dict]:
    fam = family(os_name)
    profile = detect_profile(text)
    if profile and profile in PROFILES:
        return list(PROFILES[profile].get(fam, PROFILES[profile].get("linux", [])))

    steps = [{"title": "Проверка системы", "action": "get_system_info" if fam == "linux" else "get_hardware", "params": {}}]
    key = extract_product_key(text)
    wants_windows = any(
        w in text.lower()
        for w in ("установи windows", "install windows", "windows 11", "windows 10", "поставить windows")
    )
    if fam == "windows" and (wants_windows or key):
        params = {}
        if key:
            params["product_key"] = key
        steps.insert(0, {"title": "Установка Windows", "action": "install_windows", "params": params})

    packages = []
    mapping = {
        "git": ("Git.Git", "git"),
        "python": ("Python.Python.3.13", "python3"),
        "chrome": ("Google.Chrome", "chromium-browser"),
        "vscode": ("Microsoft.VisualStudioCode", "code"),
        "vs code": ("Microsoft.VisualStudioCode", "code"),
        "docker": ("Docker.DockerDesktop", "docker.io"),
        "nginx": ("", "nginx"),
        "certbot": ("", "certbot"),
        "firefox": ("Mozilla.Firefox", "firefox"),
        "7-zip": ("7zip.7zip", "p7zip-full"),
        "7zip": ("7zip.7zip", "p7zip-full"),
    }
    low = text.lower()
    for word, (win, lin) in mapping.items():
        if word in low:
            pkg = win if fam == "windows" else lin
            if pkg:
                packages.append(pkg)
    for pkg in packages:
        steps.append({"title": f"Установка {pkg}", "action": "install_package", "params": {"name": pkg}})
    if len(steps) == 1:
        # generic shell/powershell from remaining text
        if fam == "windows":
            steps.append({"title": "Выполнить задачу", "action": "run_powershell", "params": {"script": "Write-Output 'Нет точного плана; проверьте формулировку'"}})
        else:
            steps.append({"title": "Выполнить задачу", "action": "run_shell", "params": {"script": "uname -a"}})
    return steps


async def llm_plan(text: str, os_name: str, hardware: dict) -> list[dict]:
    if not settings.openai_api_key:
        return fallback_plan(text, os_name)

    fam = family(os_name)
    run_action = "run_powershell" if fam == "windows" else "run_shell"
    prompt = f"""Ты оркестратор установки компьютера.
ОС агента: {os_name} (семейство {fam}).
Железо: {json.dumps(hardware, ensure_ascii=False)[:1500]}
Задача пользователя: {text}

Верни ТОЛЬКО JSON-массив шагов:
[{{"title":"...","action":"...","params":{{}}}}]

Разрешённые action: {sorted(ALLOWED_ACTIONS)}
Для Windows ставь пакеты через install_package с id winget.
Для Linux — имя apt-пакета.
Для произвольных команд используй {run_action} с params.script.
Не больше 12 шагов. Без markdown."""

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                f"{settings.openai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={
                    "model": settings.openai_model,
                    "messages": [
                        {"role": "system", "content": "Отвечай только JSON-массивом шагов."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                },
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        match = re.search(r"\[.*\]", content, re.S)
        raw = json.loads(match.group(0) if match else content)
        steps = []
        for item in raw[:12]:
            action = item.get("action")
            if action not in ALLOWED_ACTIONS:
                continue
            steps.append(
                {
                    "title": str(item.get("title") or action)[:120],
                    "action": action,
                    "params": item.get("params") or {},
                }
            )
        return steps or fallback_plan(text, os_name)
    except Exception:
        return fallback_plan(text, os_name)


def troubleshooting_hint(step: dict, result: dict) -> dict | None:
    stderr = (result.get("stderr") or "") + " " + (result.get("stdout") or "")
    low = stderr.lower()
    if result.get("exit_code") == 0:
        return None
    if "wsl" in low and "kernel" in low:
        return {
            "title": "Исправление WSL",
            "action": step.get("action"),
            "params": {"script": "wsl --update"} if step.get("action") in ("run_powershell", "run_shell") else step.get("params"),
        }
    if "lock" in low or "dpkg" in low:
        return {
            "title": "Снять apt lock и повторить",
            "action": "run_shell",
            "params": {"script": "dpkg --configure -a || true"},
        }
    return None
