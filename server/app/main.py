import asyncio
import io
import json
import secrets
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import current_user, hash_password, make_token, verify_password
from .config import ROOT, settings
from .db import (
    ChatMessage,
    CommandLog,
    Device,
    Enrollment,
    McpKey,
    Task,
    User,
    get_db,
    init_db,
)
from .hub import hub
from .llm import llm_plan, troubleshooting_hint
from .mcp import GROK_INSTRUCTIONS, handle_rpc, user_for_key
from .mcp_auth import ensure_mcp_key, mcp_connect_url, mcp_public_url

WEB = ROOT / "web"
WINPE = ROOT / "winpe"

app = FastAPI(title="AI PC Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id", "MCP-Protocol-Version"],
)


@app.on_event("startup")
def startup():
    init_db()


def mcp_headers(session: str | None = None) -> dict:
    return {
        "Mcp-Session-Id": session or str(uuid.uuid4()),
        "MCP-Protocol-Version": "2025-03-26",
    }


class AuthIn(BaseModel):
    email: str
    password: str


class ChatIn(BaseModel):
    message: str


class CommandIn(BaseModel):
    action: str
    params: dict = {}


class StickIn(BaseModel):
    label: str = "USB Agent"


def device_out(d: Device):
    try:
        hardware = json.loads(d.hardware or "{}")
    except json.JSONDecodeError:
        hardware = {}
    return {
        "id": d.id,
        "device_id": d.device_id,
        "hostname": d.hostname,
        "os": d.os,
        "status": "online" if hub.is_online(d.device_id) else "offline",
        "hardware": hardware,
        "last_seen": d.last_seen.isoformat() if d.last_seen else None,
    }


@app.get("/api/config")
def public_config():
    return {
        "brain": "grok_mcp",
        "tagline": "Мозг — Grok Bot. Руки — агент. JSON команд, не скриншот терминала.",
        "mcp_path": "/mcp",
        "public_url": settings.public_url,
        "grok_connectors": "https://grok.com/connectors",
        "instructions": GROK_INSTRUCTIONS,
    }


@app.get("/.well-known/oauth-protected-resource")
@app.get("/.well-known/oauth-protected-resource/mcp")
def oauth_protected_resource():
    resource = mcp_public_url()
    return {
        "resource": resource,
        "authorization_servers": [],
        "bearer_methods_supported": ["header", "query"],
        "resource_documentation": settings.public_url,
        "note": "Static Bearer MCP key from the web UI. No OAuth — use Authorization: Bearer <mcp_key> or ?key= on the MCP URL.",
    }


@app.post("/api/register")
def register(body: AuthIn, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if not body.password.strip():
        raise HTTPException(400, "Введи пароль")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "Такой email уже есть")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    mcp_key = ensure_mcp_key(db, user.id)
    return {
        "token": make_token(user.id),
        "email": user.email,
    }


@app.post("/api/login")
def login(body: AuthIn, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Неверный логин или пароль")
    return {"token": make_token(user.id), "email": user.email}


@app.get("/api/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ensure_mcp_key(db, user.id)
    return {"email": user.email, "id": user.id}


@app.post("/api/mcp-key")
def create_mcp_key(user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.query(McpKey).filter(McpKey.owner_id == user.id).delete()
    key = secrets.token_urlsafe(24)
    db.add(McpKey(owner_id=user.id, key=key))
    db.commit()
    return {
        "mcp_url": mcp_public_url(),
        "mcp_key": key,
        "mcp_connect_url": mcp_connect_url(key),
    }


def mcp_owner(request: Request, authorization: str | None, db: Session) -> int:
    key = ""
    if authorization:
        key = authorization.replace("Bearer ", "").replace("bearer ", "").strip()
    if not key:
        key = request.query_params.get("key") or ""
    return user_for_key(db, key)


@app.post("/mcp")
async def mcp_rpc(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(None),
):
    owner_id = mcp_owner(request, authorization, db)
    body = await request.json()
    session = request.headers.get("mcp-session-id") or str(uuid.uuid4())
    headers = mcp_headers(session)
    accept = (request.headers.get("accept") or "").lower()

    async def one(msg: dict) -> dict:
        return await handle_rpc(db, owner_id, msg)

    if isinstance(body, list):
        results = []
        for item in body:
            r = await one(item)
            if r:
                results.append(r)
        payload = results
    else:
        payload = await one(body)

    if not payload:
        return Response(status_code=202, headers=headers)

    if "text/event-stream" in accept and "application/json" not in accept:
        data = json.dumps(payload, ensure_ascii=False)
        return Response(
            f"event: message\ndata: {data}\n\n",
            media_type="text/event-stream",
            headers=headers,
        )
    return JSONResponse(payload, headers=headers)


@app.get("/mcp")
async def mcp_get(request: Request):
    accept = (request.headers.get("accept") or "").lower()
    if "text/event-stream" in accept:
        async def gen():
            yield ": connected\n\n"
            while True:
                await asyncio.sleep(20)
                yield ": ping\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                **mcp_headers(),
            },
        )
    return {
        "name": "ai-pc-agent",
        "brain": "grok_mcp",
        "protocol": "MCP Streamable HTTP + JSON-RPC 2.0",
        "post": "/mcp",
        "auth": "Authorization: Bearer <mcp_key> or ?key=<mcp_key>",
        "auth_note": "No OAuth. Get the key at the web UI after login.",
        "grok": "https://grok.com/connectors → Custom → mcp_connect_url from /api/me",
        "instructions": GROK_INSTRUCTIONS,
    }


@app.delete("/mcp")
def mcp_delete():
    return Response(status_code=204)


@app.get("/api/devices")
def list_devices(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Device).filter(Device.owner_id == user.id).order_by(Device.id.desc()).all()
    return [device_out(d) for d in rows]


def owned_device(db: Session, user: User, device_id: str) -> Device:
    d = db.query(Device).filter(Device.device_id == device_id, Device.owner_id == user.id).first()
    if not d:
        raise HTTPException(404, "Устройство не найдено")
    return d


@app.get("/api/devices/{device_id}")
def get_device(device_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return device_out(owned_device(db, user, device_id))


@app.get("/api/devices/{device_id}/messages")
def get_messages(device_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = owned_device(db, user, device_id)
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.device_pk == d.id)
        .order_by(ChatMessage.id.asc())
        .all()
    )
    return [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()} for m in rows]


@app.get("/api/devices/{device_id}/logs")
def get_logs(device_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = owned_device(db, user, device_id)
    rows = db.query(CommandLog).filter(CommandLog.device_pk == d.id).order_by(CommandLog.id.desc()).limit(50).all()
    return [
        {
            "command_id": r.command_id,
            "action": r.action,
            "status": r.status,
            "exit_code": r.exit_code,
            "stdout": (r.stdout or "")[-4000:],
            "stderr": (r.stderr or "")[-4000:],
        }
        for r in rows
    ]


@app.post("/api/devices/{device_id}/command")
async def send_command(
    device_id: str,
    body: CommandIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    d = owned_device(db, user, device_id)
    try:
        result = await hub.send_command(db, d, body.action, body.params)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return result


@app.post("/api/devices/{device_id}/chat")
async def chat(
    device_id: str,
    body: ChatIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    d = owned_device(db, user, device_id)
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "Пустое сообщение")
    db.add(ChatMessage(device_pk=d.id, role="user", content=text))
    db.commit()
    hardware = json.loads(d.hardware or "{}")
    steps = await llm_plan(text, d.os or "linux", hardware)
    task = Task(device_pk=d.id, user_message=text, status="running", plan_json=json.dumps(steps, ensure_ascii=False))
    db.add(task)
    db.commit()
    db.refresh(task)

    lines = [f"План ({len(steps)} шагов):"]
    for i, step in enumerate(steps, 1):
        lines.append(f"{i}. {step['title']}")
    db.add(ChatMessage(device_pk=d.id, role="assistant", content="\n".join(lines)))
    db.commit()

    if not hub.is_online(d.device_id):
        task.status = "waiting_device"
        db.add(ChatMessage(device_pk=d.id, role="assistant", content="Устройство офлайн. Подключи агент — задача продолжится после появления."))
        db.commit()
        return {"task_id": task.id, "status": task.status, "plan": steps}

    for step in steps:
        db.add(ChatMessage(device_pk=d.id, role="assistant", content=f"→ {step['title']}"))
        db.commit()
        try:
            result = await hub.send_command(db, d, step["action"], step.get("params") or {}, task_id=task.id)
        except RuntimeError:
            task.status = "waiting_device"
            db.add(ChatMessage(device_pk=d.id, role="assistant", content="Связь с агентом пропала."))
            db.commit()
            return {"task_id": task.id, "status": task.status, "plan": steps}

        ok = result.get("exit_code") == 0
        mark = "✓" if ok else "⚠"
        snippet = (result.get("stdout") or result.get("stderr") or "")[-500:]
        db.add(ChatMessage(device_pk=d.id, role="assistant", content=f"{mark} {step['title']}\n{snippet}"))
        db.commit()
        if not ok:
            fix = troubleshooting_hint(step, result)
            if fix:
                db.add(ChatMessage(device_pk=d.id, role="assistant", content=f"Пробую исправить: {fix['title']}"))
                db.commit()
                try:
                    await hub.send_command(db, d, fix["action"], fix.get("params") or {}, task_id=task.id)
                    result = await hub.send_command(db, d, step["action"], step.get("params") or {}, task_id=task.id)
                    ok2 = result.get("exit_code") == 0
                    db.add(ChatMessage(device_pk=d.id, role="assistant", content=("✓ Исправлено" if ok2 else "⚠ Не получилось автоматически")))
                    db.commit()
                except RuntimeError:
                    pass

    task.status = "done"
    db.add(ChatMessage(device_pk=d.id, role="assistant", content="Готово."))
    db.commit()
    return {"task_id": task.id, "status": "done", "plan": steps}


@app.post("/api/sticks")
def create_stick(body: StickIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    token = secrets.token_hex(8).upper()
    code = secrets.token_hex(3).upper()
    enroll = Enrollment(owner_id=user.id, token=token, label=body.label or f"USB-{code}")
    db.add(enroll)
    db.commit()
    return {
        "id": enroll.id,
        "token": token,
        "label": enroll.label,
        "agent_windows": f"{settings.public_url.rstrip('/')}/install-agent.bat?token={token}",
        "agent_linux": f"{settings.public_url.rstrip('/')}/install-agent.sh?token={token}",
        "usb_maker": f"{settings.public_url.rstrip('/')}/usb-maker.bat?token={token}",
        "install_linux": f"curl -fsSL {settings.public_url}/install.sh | bash -s -- --token {token} --url {settings.public_url}",
        "install_windows": f"powershell -ExecutionPolicy Bypass -File install.ps1 -Token {token} -Url {settings.public_url}",
        "pair_code": token[-6:],
    }


@app.get("/api/sticks")
def list_sticks(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Enrollment).filter(Enrollment.owner_id == user.id).order_by(Enrollment.id.desc()).all()
    return [
        {
            "id": e.id,
            "token": e.token,
            "label": e.label,
            "used": e.used,
            "created_at": e.created_at.isoformat(),
        }
        for e in rows
    ]


@app.get("/api/sticks/{token}/usb.zip")
def download_usb(token: str, db: Session = Depends(get_db)):
    e = db.query(Enrollment).filter(Enrollment.token == token).first()
    if not e:
        raise HTTPException(404, "Флешка не найдена")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        config = {
            "server_url": settings.public_url,
            "token": token,
            "label": e.label,
        }
        z.writestr("AIAgent/config.json", json.dumps(config, indent=2))
        agent_src = ROOT / "agent" / "agent.py"
        if agent_src.exists():
            z.write(agent_src, "AIAgent/agent.py")
        win_install = ROOT / "agent" / "windows" / "install.ps1"
        if win_install.exists():
            z.write(win_install, "AIAgent/install.ps1")
        for name in ("startnet.cmd", "Autounattend.xml", "README.txt"):
            p = WINPE / name
            if p.exists():
                z.write(p, name)
        z.writestr("start.bat", _usb_maker_bat_body(token).decode("ascii"))
        z.writestr(
            "HOW-TO.txt",
            "Run start.bat as Administrator. Token is already inside the file.\n"
            "Alpine Linux USB (~400 MB, open source). Internet required on boot.\n",
        )
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="usb-agent-{token}.zip"'},
    )


def _linux_usb_path(name: str) -> Path:
    for folder in (
        ROOT / "linux_usb",
        Path(__file__).resolve().parent.parent / "linux_usb",
    ):
        path = folder / name
        if path.is_file():
            return path
    raise HTTPException(404, f"missing {name}")


def _usb_maker_path(name: str) -> Path:
    for folder in (
        ROOT / "usb_maker",
        Path(__file__).resolve().parent.parent / "usb_maker",
    ):
        path = folder / name
        if path.is_file():
            return path
    raise HTTPException(404, f"missing {name}")


def _usb_maker_file(name: str) -> FileResponse:
    return FileResponse(_usb_maker_path(name), media_type="text/plain")


def _usb_maker_bat_body(token: str = "") -> bytes:
    url = settings.public_url.rstrip("/")
    token = "".join(c for c in (token or "") if c.isalnum())
    extra = " -Token \"%TOKEN%\"" if token else ""
    body = (
        "@echo off\r\n"
        "chcp 65001 >nul 2>&1\r\n"
        "title AI PC Agent\r\n"
        "cd /d \"%~dp0\"\r\n"
        "\r\n"
        "net session >nul 2>&1\r\n"
        "if not %errorLevel%==0 (\r\n"
        "  echo Run as Administrator - confirm UAC.\r\n"
        "  powershell -NoProfile -Command \"Start-Process -FilePath '%~f0' -Verb RunAs\"\r\n"
        "  exit /b\r\n"
        ")\r\n"
        f"set \"SERVER={url}\"\r\n"
        f"set \"TOKEN={token}\"\r\n"
        "set \"PS=%TEMP%\\ai-pc-usb-maker.ps1\"\r\n"
        "echo Downloading from %SERVER% ...\r\n"
        "powershell -NoProfile -Command \"Invoke-WebRequest -UseBasicParsing '%SERVER%/usb-maker.ps1' -OutFile '%TEMP%\\ai-pc-usb-maker.ps1'; Invoke-WebRequest -UseBasicParsing '%SERVER%/usb-maker/write_linux_usb.ps1' -OutFile '%TEMP%\\write_linux_usb.ps1'\"\r\n"
        "if not exist \"%PS%\" (\r\n"
        "  echo Download failed. Check server: %SERVER%\r\n"
        "  pause\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        f"powershell -STA -NoProfile -ExecutionPolicy Bypass -File \"%PS%\" -ServerUrl \"%SERVER%\"{extra}\r\n"
        "if errorlevel 1 pause\r\n"
    )
    return body.encode("ascii")


def _clean_enroll_token(token: str) -> str:
    return "".join(c for c in (token or "") if c.isalnum())


def _install_agent_bat_body(token: str = "") -> bytes:
    url = settings.public_url.rstrip("/")
    token = _clean_enroll_token(token)
    body = (
        "@echo off\r\n"
        "chcp 65001 >nul 2>&1\r\n"
        "title AI PC Agent\r\n"
        "cd /d \"%~dp0\"\r\n"
        "\r\n"
        "net session >nul 2>&1\r\n"
        "if not %errorLevel%==0 (\r\n"
        "  echo Run as Administrator - confirm UAC.\r\n"
        "  powershell -NoProfile -Command \"Start-Process -FilePath '%~f0' -Verb RunAs\"\r\n"
        "  exit /b\r\n"
        ")\r\n"
        f"set \"SERVER={url}\"\r\n"
        f"set \"TOKEN={token}\"\r\n"
        "set \"PS=%TEMP%\\ai-pc-install.ps1\"\r\n"
        "echo Installing agent from %SERVER% ...\r\n"
        "powershell -NoProfile -Command \"Invoke-WebRequest -UseBasicParsing '%SERVER%/install.ps1' -OutFile '%TEMP%\\ai-pc-install.ps1'\"\r\n"
        "if not exist \"%PS%\" (\r\n"
        "  echo Download failed. Check server: %SERVER%\r\n"
        "  pause\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        "powershell -NoProfile -ExecutionPolicy Bypass -File \"%PS%\" -Token \"%TOKEN%\" -Url \"%SERVER%\"\r\n"
        "pause\r\n"
    )
    return body.encode("ascii")


def _install_agent_sh_body(token: str = "") -> bytes:
    url = settings.public_url.rstrip("/")
    token = _clean_enroll_token(token)
    body = (
        "#!/usr/bin/env bash\r\n"
        "set -euo pipefail\r\n"
        f'SERVER="{url}"\r\n'
        f'TOKEN="{token}"\r\n'
        'curl -fsSL "$SERVER/install.sh" | bash -s -- --token "$TOKEN" --url "$SERVER"\r\n'
    )
    return body.encode("ascii")


@app.get("/install-agent.bat")
def install_agent_bat(token: str = Query("")):
    return Response(
        _install_agent_bat_body(token),
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=install-agent.bat"},
    )


@app.get("/install-agent.sh")
def install_agent_sh(token: str = Query("")):
    return Response(
        _install_agent_sh_body(token),
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=install-agent.sh"},
    )


@app.get("/usb-maker.ps1")
def usb_maker_ps_gui():
    return _usb_maker_file("usb_maker.ps1")


@app.get("/usb-maker.bat")
def usb_maker_bat(token: str = Query("")):
    return Response(
        _usb_maker_bat_body(token),
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=usb-maker.bat"},
    )


@app.get("/usb-maker/write_linux_usb.ps1")
def usb_maker_linux_ps1():
    return _usb_maker_file("write_linux_usb.ps1")


@app.get("/usb-maker/write_usb.ps1")
def usb_maker_ps1_legacy():
    return _usb_maker_file("write_linux_usb.ps1")


@app.get("/linux_usb/agent-boot.sh")
def linux_agent_boot():
    return FileResponse(_linux_usb_path("agent-boot.sh"), media_type="text/plain")


@app.get("/usb-maker.zip")
def usb_maker_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("usb_maker.ps1", "write_linux_usb.ps1", "start.bat"):
            z.write(_usb_maker_path(name), name)
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=usb-maker.zip"},
    )


@app.get("/winpe/Autounattend.xml")
def autounattend():
    path = WINPE / "Autounattend.xml"
    if not path.exists():
        raise HTTPException(404, "нет Autounattend.xml")
    return FileResponse(path, media_type="application/xml")


@app.get("/agent.py")
def agent_py():
    return FileResponse(ROOT / "agent" / "agent.py", media_type="text/x-python")


@app.get("/install.sh")
def install_sh():
    path = ROOT / "agent" / "linux" / "install.sh"
    return FileResponse(path, media_type="text/plain")


@app.get("/install.ps1")
def install_ps1():
    path = ROOT / "agent" / "windows" / "install.ps1"
    return FileResponse(path, media_type="text/plain")


@app.websocket("/ws/agent")
async def agent_ws(ws: WebSocket, db: Session = Depends(get_db)):
    await ws.accept()
    device_id = None
    try:
        first = await ws.receive_json()
        if first.get("type") != "register":
            await ws.close(code=4000)
            return
        token = first.get("token")
        enroll = db.query(Enrollment).filter(Enrollment.token == token).first()
        if not enroll:
            await ws.close(code=4001)
            return
        device_id = first.get("device_id") or f"DEV-{secrets.token_hex(3).upper()}"
        hardware = first.get("hardware") or {}
        os_name = first.get("os") or "unknown"
        hostname = first.get("hostname") or device_id
        device = db.query(Device).filter(Device.device_id == device_id).first()
        if not device:
            device = Device(
                owner_id=enroll.owner_id,
                device_id=device_id,
                hostname=hostname,
                os=os_name,
                hardware=json.dumps(hardware, ensure_ascii=False),
                status="online",
                last_seen=datetime.now(timezone.utc),
            )
            db.add(device)
        else:
            if device.owner_id != enroll.owner_id:
                await ws.close(code=4003)
                return
            device.hostname = hostname
            device.os = os_name
            device.hardware = json.dumps(hardware, ensure_ascii=False)
            device.status = "online"
            device.last_seen = datetime.now(timezone.utc)
        enroll.used = True
        db.commit()
        await hub.connect(device_id, ws)
        await ws.send_json({"type": "registered", "device_id": device_id})
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "result":
                log = db.query(CommandLog).filter(CommandLog.command_id == msg.get("command_id")).first()
                if log:
                    log.status = "ok" if msg.get("exit_code") == 0 else "error"
                    log.stdout = msg.get("stdout") or ""
                    log.stderr = msg.get("stderr") or ""
                    log.exit_code = msg.get("exit_code")
                    db.commit()
                hub.resolve(msg)
            elif kind == "heartbeat":
                device = db.query(Device).filter(Device.device_id == device_id).first()
                if device:
                    device.last_seen = datetime.now(timezone.utc)
                    device.status = "online"
                    db.commit()
            elif kind == "log":
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass
    finally:
        if device_id:
            hub.disconnect(device_id, ws)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


if WEB.exists():
    app.mount("/static", StaticFiles(directory=WEB), name="static")
