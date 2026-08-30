import asyncio
import json
from datetime import datetime, timezone

from fastapi import WebSocket
from sqlalchemy.orm import Session

from .db import CommandLog, Device, SessionLocal

FAST_ACTIONS = {
    "get_screen",
    "click",
    "type_text",
    "press_key",
    "scroll",
    "open_remote_assistance",
    "get_hardware",
    "get_system_info",
    "get_processes",
    "get_services",
    "read_file",
}


def _command_timeout(action: str) -> int:
    if action in ("download_file", "install_windows"):
        return 7200
    if action in FAST_ACTIONS:
        return 90
    return 600


class Hub:
    def __init__(self):
        self.sockets: dict[str, WebSocket] = {}
        self.pending: dict[str, asyncio.Future] = {}

    async def connect(self, device_id: str, ws: WebSocket):
        old = self.sockets.get(device_id)
        if old:
            try:
                await old.close()
            except Exception:
                pass
        self.sockets[device_id] = ws
        self._set_status(device_id, "online")

    def disconnect(self, device_id: str, ws: WebSocket):
        if self.sockets.get(device_id) is ws:
            self.sockets.pop(device_id, None)
            self._set_status(device_id, "offline")

    def is_online(self, device_id: str) -> bool:
        return device_id in self.sockets

    def _set_status(self, device_id: str, status: str):
        db = SessionLocal()
        try:
            device = db.query(Device).filter(Device.device_id == device_id).first()
            if device:
                device.status = status
                device.last_seen = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()

    async def send_command(self, db: Session, device: Device, action: str, params: dict, task_id: int | None = None) -> dict:
        if device.device_id not in self.sockets:
            raise RuntimeError("Устройство офлайн")
        command_id = f"cmd-{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
        log = CommandLog(
            device_pk=device.id,
            task_id=task_id,
            command_id=command_id,
            action=action,
            params=json.dumps(params, ensure_ascii=False),
            status="sent",
        )
        db.add(log)
        db.commit()
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self.pending[command_id] = fut
        await self.sockets[device.device_id].send_json(
            {"type": "command", "id": command_id, "action": action, "params": params or {}}
        )
        try:
            result = await asyncio.wait_for(fut, timeout=_command_timeout(action))
        except TimeoutError:
            log.status = "timeout"
            db.commit()
            return {"command_id": command_id, "exit_code": -1, "stdout": "", "stderr": "timeout", "data": {}}
        log.status = "ok" if result.get("exit_code") == 0 else "error"
        log.stdout = result.get("stdout") or ""
        log.stderr = result.get("stderr") or ""
        log.exit_code = result.get("exit_code")
        db.commit()
        return result

    def resolve(self, payload: dict):
        command_id = payload.get("command_id")
        fut = self.pending.pop(command_id, None)
        if fut and not fut.done():
            fut.set_result(payload)


hub = Hub()
