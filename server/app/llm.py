import json
import re

import httpx

from .config import settings

ALLOWED_ACTIONS = {
    "run_powershell",
    "run_shell",
    "get_hardware",
    "get_system_info",
    "get_processes",
    "get_services",
    "read_file",
    "write_file",
    "upload_file",
    "install_package",
    "uninstall_package",
    "download_file",
    "reboot",
    "shutdown",
    "manage_service",
    "install_windows",
    "partition_disk",
}

MAX_TURNS = 10
CONSOLE_KEEP = 6000

PRODUCT_KEY = re.compile(r"\b(?:[A-Z0-9]{5}-){4}[A-Z0-9]{5}\b", re.I)
INSTALL_RE = re.compile(
    r"(?:^|\s)(?:установи(?:ть)?|поставь|поставить|install)\s+(.+)",
    re.I,
)
UNINSTALL_RE = re.compile(
    r"(?:^|\s)(?:удали(?:ть)?|деинсталл\w*|uninstall|remove)\s+(.+)",
    re.I,
)
POWER_RE = re.compile(
    r"перезагруз|reboot|restart|выключи|shutdown|выключить\s+(комп|пк|компьютер)",
    re.I,
)
PROFILE_SETUP_RE = re.compile(
    r"настрой(?:\s+\w+){0,3}\s+(?:комп(?:ьютер)?|пк|рабоч(?:ее|ую)\s+место)|"
    r"setup(?:\s+\w+){0,3}\s+(?:pc|computer|workstation)|"
    r"профиль\s+(?:программист|офис|сервер|электрик)",
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
    if any(w in t for w in ("программист", "developer", "vscode", "vs code")):
        if any(w in t for w in ("nginx", "ssl", "vps", "сервер", "firewall")):
            return "server"
        return "programmer"
    if any(w in t for w in ("электрик", "pdf", "офис электрика")):
        return "electrician"
    if any(w in t for w in ("офис", "office")) and "office" in t or "офис" in t:
        return "office"
    if any(w in t for w in ("nginx", "ssl", "certbot", "vps", "firewall", "сервер")):
        return "server"
    return None


def family(os_name: str) -> str:
    if os_name in ("windows", "winpe"):
        return "windows"
    return "linux"


def run_action_for(os_name: str) -> str:
    return "run_powershell" if family(os_name) == "windows" else "run_shell"


def extract_product_key(text: str) -> str | None:
    m = PRODUCT_KEY.search(text or "")
    return m.group(0).upper() if m else None


def wants_power_cycle(text: str) -> bool:
    return bool(POWER_RE.search(text or ""))


def wants_profile_setup(text: str) -> bool:
    return bool(PROFILE_SETUP_RE.search(text or ""))


def extract_target(text: str) -> str:
    raw = (text or "").strip()
    for rx in (UNINSTALL_RE, INSTALL_RE):
        m = rx.search(raw)
        if m:
            return m.group(1).strip(" .!«»\"'")
    return raw


def is_uninstall(text: str) -> bool:
    return bool(UNINSTALL_RE.search(text or ""))


def is_simple_package_request(text: str) -> bool:
    if is_uninstall(text):
        m = UNINSTALL_RE.search(text or "")
    else:
        m = INSTALL_RE.search(text or "")
    if not m:
        return False
    target = m.group(1).strip()
    low = target.lower()
    if any(w in low for w in (" и ", " then ", " потом ", " настрой", " configure", " config")):
        return False
    return len(target.split()) <= 4


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


def intent_step(text: str, os_name: str) -> dict:
    low = (text or "").lower()
    fam = family(os_name)
    run = run_action_for(os_name)

    if any(w in low for w in ("желез", "hardware", "cpu", "оператив", "ram")) and not any(
        w in low for w in ("установ", "install", "удали", "uninstall")
    ):
        action = "get_hardware" if fam == "windows" else "get_system_info"
        return {"title": "Информация о системе", "action": action, "params": {}}
    if any(w in low for w in ("процесс", "process", "tasklist", "ps aux")):
        return {"title": "Список процессов", "action": "get_processes", "params": {}}
    if any(w in low for w in ("служб", "service", "systemctl")):
        return {"title": "Список служб", "action": "get_services", "params": {}}
    if wants_power_cycle(text):
        action = "reboot" if any(w in low for w in ("перезагруз", "reboot", "restart")) else "shutdown"
        return {"title": "Перезагрузка" if action == "reboot" else "Выключение", "action": action, "params": {}}

    return {
        "title": "Выполнение команды",
        "action": run,
        "params": {"script": text.strip()},
    }


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
    if action in ("run_powershell", "run_shell"):
        script = str(params.get("script") or "").strip()
        if not script:
            script = user_text.strip()
        if not script:
            return None
        params["script"] = script
    return {
        "title": str(step.get("title") or action)[:120],
        "action": action,
        "params": params,
    }


def fallback_plan(text: str, os_name: str) -> list[dict]:
    if wants_profile_setup(text):
        profile = detect_profile(text)
        fam = family(os_name)
        if profile and profile in PROFILES:
            return list(PROFILES[profile].get(fam, PROFILES[profile].get("linux", [])))

    key = extract_product_key(text)
    wants_windows = any(
        w in text.lower()
        for w in ("установи windows", "install windows", "windows 11", "windows 10", "поставить windows")
    )
    if family(os_name) == "windows" and (wants_windows or key):
        params = {}
        if key:
            params["product_key"] = key
        step = {"title": "Установка Windows", "action": "install_windows", "params": params}
        clean = sanitize_step(step, text)
        return [clean] if clean else []

    if is_uninstall(text) and is_simple_package_request(text):
        step = sanitize_step(
            {"title": f"Удаление {extract_target(text)}", "action": "uninstall_package", "params": {"name": extract_target(text)}},
            text,
        )
        return [step] if step else []

    if INSTALL_RE.search(text or "") and is_simple_package_request(text):
        step = sanitize_step(
            {"title": f"Установка {extract_target(text)}", "action": "install_package", "params": {"name": extract_target(text)}},
            text,
        )
        return [step] if step else []

    step = sanitize_step(intent_step(text, os_name), text)
    return [step] if step else []


def fallback_next(text: str, os_name: str, history: list[dict]) -> dict:
    fam = family(os_name)
    target = extract_target(text)
    run_action = run_action_for(os_name)

    if not history:
        plan = fallback_plan(text, os_name)
        step = plan[0] if plan else intent_step(text, os_name)
        clean = sanitize_step(step, text)
        if clean:
            return {"status": "step", **clean}
        return {"status": "done", "message": "Не понял задачу."}

    last = history[-1]
    console = (last.get("console") or "").lower()
    code = last.get("exit_code")

    if code == 0:
        return {"status": "done", "message": "Готово.\n" + (last.get("console") or "")[-2000:]}

    if last.get("action") in ("run_powershell", "run_shell") and code != 0:
        return {
            "status": "done",
            "message": "Команда завершилась с ошибкой. Смотри консоль выше.\n" + (last.get("console") or "")[-2000:],
        }

    if is_simple_package_request(text) and (
        last.get("action") == "download_file" or "404" in console or "empty package name" in console
    ):
        step = sanitize_step(
            {"title": f"Установка через пакетный менеджер: {target}", "action": "install_package", "params": {"name": target}},
            text,
        )
        if step and not already_tried(step, history):
            return {"status": "step", **step}

    if is_simple_package_request(text) and fam == "windows" and any(
        x in console for x in ("msstore", "0x8a150044", "rest api", "источнике")
    ):
        op = "uninstall" if is_uninstall(text) else "install"
        extra = "--accept-package-agreements --accept-source-agreements" if op == "install" else ""
        script = f"winget {op} --name {target} --source winget --disable-interactivity {extra}".strip()
        step = sanitize_step(
            {"title": "Повтор через winget (источник winget)", "action": "run_powershell", "params": {"script": script}},
            text,
        )
        if step and not already_tried(step, history):
            return {"status": "step", **step}

    if is_simple_package_request(text) and last.get("action") == "install_package" and fam == "windows":
        script = f"winget search {target} --source winget --disable-interactivity"
        step = sanitize_step(
            {"title": "Поиск пакета в winget", "action": "run_powershell", "params": {"script": script}},
            text,
        )
        if step and not already_tried(step, history):
            return {"status": "step", **step}

    retry = sanitize_step(intent_step(text, os_name), text)
    if retry and not already_tried(retry, history) and last.get("action") in ("install_package", "uninstall_package"):
        retry["title"] = "Пробую через консоль"
        return {"status": "step", **retry}

    return {
        "status": "done",
        "message": "Остановился: по логу команда не удалась.\n" + (last.get("console") or "")[-2000:],
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
    run_action = run_action_for(os_name)
    prompt = f"""Ты универсальный оператор ПК. Пользователь даёт ЛЮБУЮ задачу — файлы, команды, настройка, диагностика, установка софта.

ОС: {os_name} ({fam})
Железо: {json.dumps(hardware, ensure_ascii=False)[:1200]}
Задача пользователя: {text}

Журнал уже выполненных команд и ИХ КОНСОЛЬНЫЙ ВЫВОД:
{format_history(history)}

Правила:
- Главный инструмент — {run_action} (params.script): выполняй то, что просит пользователь, обычным языком переводи в команды ОС.
- install_package / uninstall_package — ТОЛЬКО если пользователь явно просит установить/удалить программу одним названием.
- read_file / write_file / upload_file — для работы с файлами.
- get_processes / get_services / get_hardware / get_system_info — для информации о системе.
- Смотри вывод консоли. Ошибка → другая команда, не повторяй то же самое.
- reboot/shutdown только если пользователь сам просил.
- Не выдумывай URL для download_file.
- Один ход = одно действие или status=done с ответом по логу.

Разрешённые action: {sorted(ALLOWED_ACTIONS)}

JSON без markdown:
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
                        {"role": "system", "content": "Ты оператор ПК. Отвечай только JSON одного шага."},
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
