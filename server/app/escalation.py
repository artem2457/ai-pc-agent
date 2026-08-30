"""Hand off failed local chat tasks to Grok Bot via MCP."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .db import ChatMessage, Device, Escalation, Task
from .hub import hub
from .mcp_auth import ensure_mcp_key, mcp_connect_url


def build_grok_prompt(device: Device, user_message: str, history: list[dict], reason: str) -> str:
    log_lines = []
    for i, item in enumerate(history[-8:], 1):
        log_lines.append(
            f"{i}. {item.get('title')} (exit {item.get('exit_code')})\n{(item.get('console') or '')[-1200:]}"
        )
    log = "\n\n".join(log_lines) if log_lines else "(команд не было)"
    return (
        f"Продолжи задачу на ПК, локальный бот не справился.\n"
        f"device_id: {device.device_id}\n"
        f"hostname: {device.hostname or device.device_id}\n"
        f"os: {device.os}\n"
        f"задача пользователя: {user_message}\n"
        f"причина эскалации: {reason}\n\n"
        f"Журнал консоли:\n{log}\n\n"
        f"Сначала list_escalations или list_devices, затем execute_command / install_package. "
        f"Если нужен GUI: get_screen, затем click / type_text / press_key. "
        f"Смотри stdout_tail после каждого шага."
    )


def chat_succeeded(history: list[dict]) -> bool:
    return bool(history) and history[-1].get("exit_code") == 0


async def escalate_to_grok(
    db: Session,
    *,
    task: Task,
    device: Device,
    owner_id: int,
    user_message: str,
    history: list[dict],
    reason: str,
) -> dict:
    mcp_key = ensure_mcp_key(db, owner_id)
    grok_prompt = build_grok_prompt(device, user_message, history, reason)
    handoff = {
        "device_id": device.device_id,
        "hostname": device.hostname or device.device_id,
        "os": device.os,
        "task": user_message,
        "reason": reason,
        "grok_prompt": grok_prompt,
        "grok_connectors": "https://grok.com/connectors",
        "grok_chat": "https://grok.com",
        "mcp_url": mcp_connect_url(mcp_key),
        "history": history[-8:],
    }

    esc = Escalation(
        task_id=task.id,
        device_pk=device.id,
        owner_id=owner_id,
        user_message=user_message,
        reason=reason,
        context_json=json.dumps(handoff, ensure_ascii=False),
        status="pending",
    )
    db.add(esc)
    task.status = "escalated"
    db.commit()
    db.refresh(esc)

    remote_note = ""
    if device.os in ("windows", "winpe") and hub.is_online(device.device_id):
        try:
            await hub.send_command(
                db,
                device,
                "open_remote_assistance",
                {},
                task_id=task.id,
            )
            remote_note = "\nНа ПК открыта «Быстрая помощь» Windows — можно подключиться вручную, если нужен экран."
        except Exception:
            remote_note = ""

    msg = (
        f"Локальный бот не справился — задача передана Grok Bot.\n\n"
        f"Причина: {reason}\n\n"
        f"1. Открой https://grok.com/connectors\n"
        f"2. Custom MCP → URL:\n{mcp_connect_url(mcp_key)}\n"
        f"3. В Grok напиши (можно скопировать):\n\n{grok_prompt}"
        f"{remote_note}"
    )
    db.add(ChatMessage(device_pk=device.id, role="assistant", content=msg))
    db.commit()

    handoff["escalation_id"] = esc.id
    return handoff
