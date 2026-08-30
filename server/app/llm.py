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
    "uninstall_package",
    "download_file",
    "reboot",
    "shutdown",
    "manage_service",
    "install_windows",
    "partition_disk",
}

MAX_TURNS = 8
CONSOLE_KEEP = 6000

PRODUCT_KEY = re.compile(r"\b(?:[A-Z0-9]{5}-){4}[A-Z0-9]{5}\b", re.I)
INSTALL_RE = re.compile(
    r"(?:установи(?:ть)?|поставь|поставить|install|скачай|скачать)\s+(.+)",
    re.I,
)
UNINSTALL_RE = re.compile(
    r"(?:удали(?:ть)?|деинсталл\w*|uninstall|remove)\s+(.+)",
    re.I,
)
POWER_RE = re.compile(
    r"перезагруз|reboot|restart|выключи|shutdown|выключить\s+(комп|пк|компьютер)",
    re.I,
)

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


def wants_power_cycle(text: str) -> bool:
    return bool(POWER_RE.search(text or ""))


def extract_target(text: str) -> str:
    raw = (text or "").strip()
    for rx in (UNINSTALL_RE, INSTALL_RE):
        m = rx.search(raw)
        if m:
            return m.group(1).strip(" .!«»\"'")
    return raw


def is_uninstall(text: str) -> bool:
    return bool(UNINSTALL_RE.search(text or ""))


def console_of(result: dict) -> str:
    parts = [result.get("stdout") or "", result.get("stderr") or ""]
    text = "\n".join(p for p in parts if p).strip()
    return text[-CONSOLE_KEEP:]


def format_history(history: list[dict]) -> str:
    if not history:
        return "(команд ещё не было)"
    blocks = []
    for i, item in enumerate(history, 1):
        blocks.append(
            f"#{i} {item.get('title')} | action={item.get('action')} | exit={item.get('exit_code')}\n"
            f"{item.get('console') or '(пусто)'}"
        )
    return "\n\n".join(blocks)


def package_name_from_step(step: dict, user_text: str) -> str:
    params = step.get("params") if isinstance(step.get("params"), dict) else {}
    for key in ("name", "id", "package", "pkg"):
        value = str(params.get(key) or "").strip()
        if value:
            return value
    title = str(step.get("title") or "")
    for prefix in ("Установка ", "Удаление ", "Install ", "Uninstall "):
        if title.startswith(prefix) and title[len(prefix) :].strip():
            return title[len(prefix) :].strip()
    return extract_target(user_text)


def step_fingerprint(step: dict) -> str:
    params = step.get("params") if isinstance(step.get("params"), dict) else {}
    return json.dumps({"action": step.get("action"), "params": params}, ensure_ascii=False, sort_keys=True)


def already_tried(step: dict, history: list[dict]) -> bool:
    key = step_fingerprint(step)
    return any(step_fingerprint(h) == key for h in history)


def sanitize_step(step: dict | None, user_text: str) -> dict | None:
    if not step or not isinstance(step, dict):
        return None
    action = step.get("action")
    if action not in ALLOWED_ACTIONS:
        return None
    if action in ("reboot", "shutdown") and not wants_power_cycle(user_text):
        return None
    params = dict(step.get("params") if isinstance(step.get("params"), dict) else {})
    if action == "download_file":
        url = str(params.get("url") or "")
        if not url.startswith("http://") and not url.startswith("https://"):
            return None
    if action in ("install_package", "uninstall_package"):
        name = package_name_from_step({"params": params, "title": step.get("title")}, user_text)
        if not name:
            return None
        params["name"] = name
    return {
        "title": str(step.get("title") or action)[:120],
        "action": action,
        "params": params,
    }


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
        target = extract_target(text)
        if is_uninstall(text):
            steps.append({"title": f"Удаление {target}", "action": "uninstall_package", "params": {"name": target}})
        elif INSTALL_RE.search(text or ""):
            steps.append({"title": f"Установка {target}", "action": "install_package", "params": {"name": target}})
        elif fam == "windows":
            steps.append({"title": "Выполнить задачу", "action": "run_powershell", "params": {"script": text}})
        else:
            steps.append({"title": "Выполнить задачу", "action": "run_shell", "params": {"script": text}})
    return [s for s in steps if sanitize_step(s, text)]


def fallback_next(text: str, os_name: str, history: list[dict]) -> dict:
    fam = family(os_name)
    target = extract_target(text)
    run_action = "run_powershell" if fam == "windows" else "run_shell"

    if not history:
        step = None
        if is_uninstall(text):
            step = {"title": f"Удаление {target}", "action": "uninstall_package", "params": {"name": target}}
        elif INSTALL_RE.search(text or ""):
            step = {"title": f"Установка {target}", "action": "install_package", "params": {"name": target}}
        else:
            plan = fallback_plan(text, os_name)
            step = plan[0] if plan else None
        clean = sanitize_step(step, text)
        if clean:
            return {"status": "step", **clean}
        return {"status": "done", "message": "Не понял задачу."}

    last = history[-1]
    console = (last.get("console") or "").lower()
    code = last.get("exit_code")

    if code == 0:
        if last.get("action") in ("install_package", "uninstall_package"):
            already_checked = any(str(h.get("title") or "").startswith("Проверка") for h in history)
            if not already_checked:
                if fam == "windows":
                    script = f"winget list --name {target} --source winget --disable-interactivity"
                else:
                    script = f"command -v {target} || dpkg -l | grep -i {target} | head"
                step = sanitize_step(
                    {"title": "Проверка по выводу системы", "action": run_action, "params": {"script": script}},
                    text,
                )
                if step:
                    return {"status": "step", **step}
        return {"status": "done", "message": "Готово. Последняя команда завершилась успешно."}

    if last.get("action") == "download_file" or "404" in console or "empty package name" in console:
        step = sanitize_step(
            {"title": f"Установка через пакетный менеджер: {target}", "action": "install_package", "params": {"name": target}},
            text,
        )
        if step and not already_tried(step, history):
            return {"status": "step", **step}

    if fam == "windows" and any(x in console for x in ("msstore", "0x8a150044", "rest api", "источнике")):
        op = "uninstall" if is_uninstall(text) else "install"
        extra = "--accept-package-agreements --accept-source-agreements" if op == "install" else ""
        script = (
            f"winget {op} --name {target} --source winget --disable-interactivity {extra}".strip()
        )
        step = sanitize_step({"title": "Повтор через winget (источник winget)", "action": "run_powershell", "params": {"script": script}}, text)
        if step:
            return {"status": "step", **step}

    if last.get("action") == "install_package" and fam == "windows":
        script = f"winget search {target} --source winget --disable-interactivity"
        step = sanitize_step({"title": "Поиск пакета в winget по выводу ошибки", "action": "run_powershell", "params": {"script": script}}, text)
        if step and not already_tried(step, history):
            return {"status": "step", **step}

    return {
        "status": "done",
        "message": "Остановился: по логу команда не удалась, повтор того же шага смысла нет.\n" + (last.get("console") or "")[-1500:],
    }


async def llm_next(text: str, os_name: str, hardware: dict, history: list[dict]) -> dict:
    if settings.openai_api_key:
        decided = await _llm_next_api(text, os_name, hardware, history)
        if decided:
            if decided.get("status") == "done":
                return decided
            step = sanitize_step(decided, text)
            if step and not already_tried(step, history):
                return {"status": "step", **step}
    return fallback_next(text, os_name, history)


async def _llm_next_api(text: str, os_name: str, hardware: dict, history: list[dict]) -> dict | None:
    fam = family(os_name)
    run_action = "run_powershell" if fam == "windows" else "run_shell"
    prompt = f"""Ты оператор ПК. Не строй длинный план заранее.

ОС: {os_name} ({fam})
Железо: {json.dumps(hardware, ensure_ascii=False)[:1200]}
Задача пользователя: {text}

Журнал уже выполненных команд и ИХ КОНСОЛЬНЫЙ ВЫВОД:
{format_history(history)}

Правила:
- Смотри именно на вывод консоли. Если ошибка — смени подход, не повторяй ту же команду.
- Один ход = одно действие, либо завершение.
- reboot/shutdown ЗАПРЕЩЕНЫ, если пользователь сам не просил перезагрузку или выключение.
- Не выдумывай URL. Для программ используй install_package / uninstall_package (Windows = winget, Linux = apt).
- Имя пакета бери из запроса пользователя. Для install_package и uninstall_package params ОБЯЗАНЫ содержать "name": "<имя из запроса>". Без name команда падает.
- Не повторяй шаг, который уже есть в журнале с тем же action и теми же params. Если он упал — смени команду.
- download_file только с реальным http(s) URL из задачи или из лога, не с выдуманного.
- Если задача уже сделана по логу — status=done и кратко опиши результат по логу.

Разрешённые action: {sorted(ALLOWED_ACTIONS)}
Для произвольной команды: {run_action} с params.script.

Верни ТОЛЬКО JSON, без markdown:
{{"status":"step","title":"...","action":"...","params":{{}}}}
или
{{"status":"done","message":"..."}}"""

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                f"{settings.openai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={
                    "model": settings.openai_model,
                    "messages": [
                        {"role": "system", "content": "Отвечай только JSON-объектом одного хода."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                },
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, re.S)
        raw = json.loads(match.group(0) if match else content)
        if not isinstance(raw, dict):
            return None
        if str(raw.get("status") or "").lower() == "done":
            return {"status": "done", "message": str(raw.get("message") or "Готово.")[:4000]}
        action = raw.get("action")
        if action not in ALLOWED_ACTIONS:
            return None
        return {
            "status": "step",
            "title": str(raw.get("title") or action)[:120],
            "action": action,
            "params": raw.get("params") if isinstance(raw.get("params"), dict) else {},
        }
    except Exception:
        return None


async def llm_plan(text: str, os_name: str, hardware: dict) -> list[dict]:
    first = await llm_next(text, os_name, hardware, [])
    if first.get("status") == "step":
        return [{"title": first["title"], "action": first["action"], "params": first.get("params") or {}}]
    return fallback_plan(text, os_name)


def troubleshooting_hint(step: dict, result: dict) -> dict | None:
    return None
