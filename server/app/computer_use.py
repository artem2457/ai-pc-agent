"""Screenshot → vision model → click/type on the real PC."""
from __future__ import annotations

import asyncio
import json
import re

import httpx
from sqlalchemy.orm import Session

from .config import settings
from .db import ChatMessage, Device, Task
from .hub import hub

MAX_GUI_TURNS = 8
GUI_ACTIONS = {"click", "type_text", "press_key", "scroll"}
PRESS_KEYS = {
    "return",
    "enter",
    "tab",
    "escape",
    "esc",
    "space",
    "backspace",
    "delete",
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "f5",
    "y",
    "n",
}

DESKTOP_RE = re.compile(
    r"блокнот|notepad|калькулятор|calc\.exe|mspaint|paint|"
    r"рабоч\w*\s+стол|экран|скрин|"
    r"кликн|нажм[иь]|кнопк|диалог|в окн|"
    r"введи в|напиш[иь] в|напечат|type in|click ",
    re.I,
)
TYPE_RE = re.compile(r"введ[иу]|напиш|напечат|текст|type |write |hello|впиши", re.I)


def needs_desktop(text: str) -> bool:
    return bool(DESKTOP_RE.search(text or ""))


def wants_type(text: str) -> bool:
    return bool(TYPE_RE.search(text or ""))


def desktop_goal_met(text: str, history: list[dict]) -> bool:
    if not history:
        return False
    if not needs_desktop(text):
        return True
    if wants_type(text):
        return any(h.get("action") == "type_text" and h.get("exit_code") == 0 for h in history)
    return any(h.get("action") in ("click", "type_text", "press_key") and h.get("exit_code") == 0 for h in history)


def window_hint(text: str) -> str:
    low = (text or "").lower()
    if any(w in low for w in ("блокнот", "notepad")):
        return "notepad"
    if any(w in low for w in ("калькулятор", "calc")):
        return "calculator"
    if "paint" in low:
        return "paint"
    if "chrome" in low:
        return "chrome"
    return ""


def launch_gui_step(text: str, os_name: str) -> dict | None:
    low = (text or "").lower()
    if os_name not in ("windows", "winpe"):
        return None
    if any(w in low for w in ("блокнот", "notepad")):
        return {
            "title": "Открываю Блокнот",
            "action": "run_powershell",
            "params": {"script": "Start-Process notepad; Start-Sleep -Milliseconds 800"},
        }
    if any(w in low for w in ("калькулятор", "calc")):
        return {
            "title": "Открываю калькулятор",
            "action": "run_powershell",
            "params": {"script": "Start-Process calc; Start-Sleep -Milliseconds 800"},
        }
    return None


def screen_step() -> dict:
    return {"title": "Смотрю экран", "action": "get_screen", "params": {}}


def parse_json_object(text: str) -> dict | None:
    text = (text or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    raw = match.group(0) if match else text
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def sanitize_gui_step(raw: dict | None, image_width: int, image_height: int) -> dict | None:
    if not raw or not isinstance(raw, dict):
        return None
    action = str(raw.get("action") or "").strip()
    if action not in GUI_ACTIONS:
        return None
    params = dict(raw.get("params") if isinstance(raw.get("params"), dict) else {})
    if action == "click":
        try:
            x = int(float(params.get("x")))
            y = int(float(params.get("y")))
        except (TypeError, ValueError):
            return None
        if image_width > 0:
            x = max(0, min(image_width - 1, x))
        if image_height > 0:
            y = max(0, min(image_height - 1, y))
        button = str(params.get("button") or "left").lower()
        if button not in ("left", "right", "middle", "double"):
            button = "left"
        params = {"x": x, "y": y, "button": button, "image_width": image_width, "image_height": image_height}
    elif action == "type_text":
        text = str(params.get("text") or "")[:2000]
        if not text:
            return None
        cleaned: dict = {"text": text}
        if params.get("window"):
            cleaned["window"] = str(params.get("window"))[:80]
        if params.get("x") is not None and params.get("y") is not None:
            try:
                cleaned["x"] = int(float(params.get("x")))
                cleaned["y"] = int(float(params.get("y")))
                cleaned["image_width"] = image_width
                cleaned["image_height"] = image_height
            except (TypeError, ValueError):
                pass
        params = cleaned
    elif action == "press_key":
        key = str(params.get("key") or "").strip().lower()
        if key not in PRESS_KEYS and not (len(key) == 1 and key.isalnum()):
            return None
        params = {"key": key}
    elif action == "scroll":
        try:
            dy = int(params.get("dy") if params.get("dy") is not None else params.get("amount") or 0)
        except (TypeError, ValueError):
            return None
        if dy == 0:
            return None
        params = {"dy": max(-10, min(10, dy))}
    title = str(raw.get("title") or action)[:120]
    return {"title": title, "action": action, "params": params}


def gui_label(step: dict) -> str:
    action = step.get("action")
    p = step.get("params") or {}
    if action == "click":
        return f"🖱 клик ({p.get('x')},{p.get('y')}) {p.get('button') or 'left'}"
    if action == "type_text":
        preview = str(p.get("text") or "")[:80]
        return f"⌨ ввод: {preview}"
    if action == "press_key":
        return f"⌨ клавиша {p.get('key')}"
    if action == "scroll":
        return f"↕ скролл {p.get('dy')}"
    return str(step.get("title") or action)


def outcome_ok(decision: dict) -> bool:
    return str(decision.get("outcome") or "").lower() in ("success", "ok", "done")


async def vision_next(
    user_message: str,
    os_name: str,
    console_tail: str,
    gui_history: list[str],
    image_b64: str,
    image_width: int,
    image_height: int,
) -> dict:
    if not settings.openai_api_key:
        return {"status": "done", "outcome": "fail", "message": "Нет OPENAI_API_KEY для просмотра экрана."}
    history = "\n".join(gui_history[-12:]) if gui_history else "(ещё не кликали)"
    prompt = f"""Ты видишь текущий экран ПК пользователя. Продолжи задачу по картинке.

ОС: {os_name}
Задача: {user_message}

Что уже сделали (консоль и GUI):
{console_tail[-2500:] or "(пусто)"}
{history}

Скриншот {image_width}x{image_height} пикселей. Координаты — в ЭТИХ пикселях, от левого верхнего угла.

Правила:
- Если нужное окно УЖЕ открыто (Блокнот, диалог) — НЕ открывай вторую копию. Кликни в белое поле ввода и сразу type_text с текстом задачи.
- Для ввода текста предпочти один шаг type_text с params.x, params.y (центр поля) и params.window (например notepad).
- Диалог Да/Yes/OK/Next — click.
- Не кликай ярлык приложения, если окно уже на экране.
- Не reboot/shutdown.
- Текст ещё не в поле → type_text, не status=done.
- Текст уже в поле и задача выполнена → status=done, outcome=success.

JSON без markdown:
{{"status":"step","title":"...","action":"click|type_text|press_key|scroll","params":{{}}}}
или
{{"status":"done","outcome":"success|fail","message":"..."}}

Для click: params {{"x":123,"y":456,"button":"left"}}
Для type_text: params {{"text":"..."}}
Для press_key: params {{"key":"enter"}}
Для scroll: params {{"dy":3}}"""

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{settings.openai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={
                    "model": settings.openai_model,
                    "messages": [
                        {"role": "system", "content": "Ты оператор GUI. Отвечай только JSON одного шага."},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_b64}",
                                        "detail": "high",
                                    },
                                },
                            ],
                        },
                    ],
                    "temperature": 0.1,
                    "max_tokens": 400,
                },
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        raw = parse_json_object(content)
        if not raw:
            return {"status": "done", "outcome": "fail", "message": "Модель не вернула JSON по скриншоту."}
        if str(raw.get("status") or "").lower() == "done":
            return {
                "status": "done",
                "outcome": str(raw.get("outcome") or "fail"),
                "message": str(raw.get("message") or "Готово.")[:4000],
            }
        step = sanitize_gui_step(raw, image_width, image_height)
        if not step:
            return {"status": "done", "outcome": "fail", "message": "Некорректный GUI-шаг по скриншоту."}
        return {"status": "step", **step}
    except Exception as e:
        return {"status": "done", "outcome": "fail", "message": f"Vision API: {e}"}


async def see_and_act(
    db: Session,
    *,
    device: Device,
    task: Task,
    user_message: str,
    history: list[dict],
) -> dict:
    """Screenshot now, then one vision click/type. Used mid-task, not only after failures."""
    executed: list[dict] = []
    history_items: list[dict] = []
    db.add(ChatMessage(device_pk=device.id, role="assistant", content="→ Смотрю экран"))
    db.commit()
    try:
        shot = await hub.send_command(db, device, "get_screen", {}, task_id=task.id)
    except RuntimeError as e:
        return {"finished": True, "ok": False, "executed": [], "history_items": [], "message": str(e)}

    data = shot.get("data") or {}
    b64 = data.get("image_base64") or ""
    summary = (shot.get("stdout") or shot.get("stderr") or "screenshot")[:500]
    shot_step = {"title": "Смотрю экран", "action": "get_screen", "params": {}}
    executed.append(shot_step)
    history_items.append(
        {"title": shot_step["title"], "action": "get_screen", "params": {}, "exit_code": shot.get("exit_code"), "console": summary}
    )
    db.add(ChatMessage(device_pk=device.id, role="assistant", content=f"✓ экран\n{summary}"))
    db.commit()
    if shot.get("exit_code") != 0 or not b64:
        msg = shot.get("stderr") or "нет скриншота"
        db.add(ChatMessage(device_pk=device.id, role="assistant", content=f"Экран недоступен: {msg}"))
        db.commit()
        return {"finished": True, "ok": False, "executed": executed, "history_items": history_items, "message": msg}

    iw = int(data.get("image_width") or 0)
    ih = int(data.get("image_height") or 0)
    gui_history = [
        f"{h.get('title')} {h.get('action')} exit={h.get('exit_code')}"
        for h in history
        if h.get("action") in GUI_ACTIONS or h.get("action") == "get_screen"
    ]
    decision = await vision_next(
        user_message,
        device.os or "windows",
        _console_tail(history),
        gui_history,
        b64,
        iw,
        ih,
    )
    if decision.get("status") != "step":
        msg = decision.get("message") or "Готово по экрану."
        db.add(ChatMessage(device_pk=device.id, role="assistant", content=msg))
        db.commit()
        return {
            "finished": True,
            "ok": outcome_ok(decision),
            "executed": executed,
            "history_items": history_items,
            "message": msg,
        }

    step = {"title": decision["title"], "action": decision["action"], "params": dict(decision.get("params") or {})}
    hint = window_hint(user_message)
    if step["action"] == "type_text" and hint and not step["params"].get("window"):
        step["params"]["window"] = hint
    label = gui_label(step)
    db.add(ChatMessage(device_pk=device.id, role="assistant", content=f"→ {label}"))
    db.commit()
    try:
        result = await hub.send_command(db, device, step["action"], step["params"], task_id=task.id)
    except RuntimeError as e:
        return {"finished": True, "ok": False, "executed": executed, "history_items": history_items, "message": str(e)}
    mark = "✓" if result.get("exit_code") == 0 else "⚠"
    body = f"{mark} {label}\n{(result.get('stdout') or result.get('stderr') or '')[:500]}"
    db.add(ChatMessage(device_pk=device.id, role="assistant", content=body))
    db.commit()
    item = {
        "title": step["title"],
        "action": step["action"],
        "params": step["params"],
        "exit_code": result.get("exit_code"),
        "console": (result.get("stdout") or result.get("stderr") or "")[:800],
    }
    executed.append(step)
    history_items.append(item)
    await asyncio.sleep(0.9)
    return {"finished": False, "ok": result.get("exit_code") == 0, "executed": executed, "history_items": history_items, "message": ""}


def _console_tail(history: list[dict]) -> str:
    blocks = []
    for item in history[-4:]:
        blocks.append(
            f"{item.get('title')} exit={item.get('exit_code')}\n{(item.get('console') or '')[-800:]}"
        )
    return "\n\n".join(blocks)


async def run_computer_use(
    db: Session,
    *,
    device: Device,
    task: Task,
    user_message: str,
    console_history: list[dict],
) -> dict:
    executed: list[dict] = []
    gui_history: list[str] = []
    db.add(
        ChatMessage(
            device_pk=device.id,
            role="assistant",
            content="Смотрю экран и кликаю / ввожу текст.",
        )
    )
    db.commit()

    same = 0
    last_fp = ""
    for _ in range(MAX_GUI_TURNS):
        try:
            shot = await hub.send_command(db, device, "get_screen", {}, task_id=task.id)
        except RuntimeError as e:
            return {"ok": False, "executed": executed, "reason": str(e)}
        data = shot.get("data") or {}
        b64 = data.get("image_base64") or ""
        if shot.get("exit_code") != 0 or not b64:
            reason = shot.get("stderr") or "нет скриншота (агент должен быть в сессии пользователя)"
            db.add(ChatMessage(device_pk=device.id, role="assistant", content=f"Экран недоступен: {reason}"))
            db.commit()
            return {"ok": False, "executed": executed, "reason": reason}

        iw = int(data.get("image_width") or 0)
        ih = int(data.get("image_height") or 0)
        decision = await vision_next(
            user_message,
            device.os or "windows",
            _console_tail(console_history),
            gui_history,
            b64,
            iw,
            ih,
        )
        if decision.get("status") != "step":
            msg = decision.get("message") or "Готово по экрану."
            db.add(ChatMessage(device_pk=device.id, role="assistant", content=msg))
            db.commit()
            return {
                "ok": outcome_ok(decision),
                "executed": executed,
                "reason": "" if outcome_ok(decision) else msg,
            }

        step = {"title": decision["title"], "action": decision["action"], "params": decision.get("params") or {}}
        fp = json.dumps(step, sort_keys=True, ensure_ascii=False)
        same = same + 1 if fp == last_fp else 1
        last_fp = fp
        if same >= 3:
            msg = "Остановился: один и тот же клик повторяется."
            db.add(ChatMessage(device_pk=device.id, role="assistant", content=msg))
            db.commit()
            return {"ok": False, "executed": executed, "reason": msg}

        label = gui_label(step)
        db.add(ChatMessage(device_pk=device.id, role="assistant", content=f"→ {label}"))
        db.commit()
        try:
            result = await hub.send_command(db, device, step["action"], step["params"], task_id=task.id)
        except RuntimeError as e:
            return {"ok": False, "executed": executed, "reason": str(e)}

        mark = "✓" if result.get("exit_code") == 0 else "⚠"
        body = f"{mark} {label}\n{(result.get('stdout') or result.get('stderr') or '')[:500]}"
        db.add(ChatMessage(device_pk=device.id, role="assistant", content=body))
        db.commit()
        executed.append(step)
        gui_history.append(f"{label} exit={result.get('exit_code')} {(result.get('stdout') or '')[:200]}")
        await asyncio.sleep(0.7)

    msg = "Остановился: слишком много шагов по экрану."
    db.add(ChatMessage(device_pk=device.id, role="assistant", content=msg))
    db.commit()
    return {"ok": False, "executed": executed, "reason": msg}
