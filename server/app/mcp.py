"""Remote MCP for Grok / any MCP client. JSON-RPC 2.0 over POST /mcp."""
from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .db import CommandLog, Device, McpKey
from .hub import hub

STDOUT_TAIL = 4000

TOOLS = [
    {
        "name": "list_devices",
        "description": "List computers owned by this account (id, os, online, hardware).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_hardware",
        "description": "Get CPU/RAM/disk of a device.",
        "inputSchema": {
            "type": "object",
            "properties": {"device_id": {"type": "string"}},
            "required": ["device_id"],
        },
    },
    {
        "name": "execute_command",
        "description": "Run a shell/PowerShell command on the device. Returns exit_code and stdout_tail, not a screenshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "command": {"type": "string"},
            },
            "required": ["device_id", "command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file on the device.",
        "inputSchema": {
            "type": "object",
            "properties": {"device_id": {"type": "string"}, "path": {"type": "string"}},
            "required": ["device_id", "path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write a text file on the device.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["device_id", "path", "content"],
        },
    },
    {
        "name": "download_file",
        "description": "Download a URL onto the device (Grok finds the official Windows ISO link in its own browser, then passes it here). For Windows ISO use path like D:\\windows.iso.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "url": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["device_id", "url"],
        },
    },
    {
        "name": "upload_file",
        "description": "Write a file on the device from text or base64. Prefer download_file for large ISOs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string"},
                "content_base64": {"type": "string"},
            },
            "required": ["device_id", "path"],
        },
    },
    {
        "name": "install_package",
        "description": "Install a package (winget id on Windows, apt name on Linux).",
        "inputSchema": {
            "type": "object",
            "properties": {"device_id": {"type": "string"}, "name": {"type": "string"}},
            "required": ["device_id", "name"],
        },
    },
    {
        "name": "install_os",
        "description": "Install Windows from an ISO/WIM already on the device. First use download_file to fetch the ISO (Grok finds the official link in its browser). Do not ask the USB maker for an ISO.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "image": {"type": "string"},
                "product_key": {"type": "string"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_processes",
        "description": "List running processes.",
        "inputSchema": {
            "type": "object",
            "properties": {"device_id": {"type": "string"}},
            "required": ["device_id"],
        },
    },
    {
        "name": "get_services",
        "description": "List services.",
        "inputSchema": {
            "type": "object",
            "properties": {"device_id": {"type": "string"}},
            "required": ["device_id"],
        },
    },
    {
        "name": "get_logs",
        "description": "Read stored command logs (use this instead of huge stdout).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"},
                "search": {"type": "string"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "reboot",
        "description": "Reboot the device. Agent reconnects after boot.",
        "inputSchema": {
            "type": "object",
            "properties": {"device_id": {"type": "string"}},
            "required": ["device_id"],
        },
    },
    {
        "name": "shutdown",
        "description": "Shut down the device.",
        "inputSchema": {
            "type": "object",
            "properties": {"device_id": {"type": "string"}},
            "required": ["device_id"],
        },
    },
    {
        "name": "get_screen",
        "description": "Last-resort screenshot. Prefer execute_command. Often unavailable in WinPE.",
        "inputSchema": {
            "type": "object",
            "properties": {"device_id": {"type": "string"}},
            "required": ["device_id"],
        },
    },
]


def clip(text: str) -> str:
    text = text or ""
    if len(text) <= STDOUT_TAIL:
        return text
    return text[-STDOUT_TAIL:]


def pack_result(result: dict, log_id: str | None = None) -> dict:
    return {
        "exit_code": result.get("exit_code"),
        "status": "success" if result.get("exit_code") == 0 else "error",
        "stdout_tail": clip(result.get("stdout") or ""),
        "stderr_tail": clip(result.get("stderr") or ""),
        "log_id": log_id or result.get("command_id"),
        "data": result.get("data") or {},
    }


def user_for_key(db: Session, key: str) -> int:
    row = db.query(McpKey).filter(McpKey.key == key).first()
    if not row:
        raise HTTPException(401, "Неверный MCP-ключ")
    return row.owner_id


def owned(db: Session, owner_id: int, device_id: str) -> Device:
    d = db.query(Device).filter(Device.device_id == device_id, Device.owner_id == owner_id).first()
    if not d:
        raise HTTPException(404, "Нет такого устройства")
    return d


async def run_tool(db: Session, owner_id: int, name: str, args: dict) -> dict:
    args = args or {}
    if name == "list_devices":
        rows = db.query(Device).filter(Device.owner_id == owner_id).all()
        return {
            "devices": [
                {
                    "device_id": d.device_id,
                    "hostname": d.hostname,
                    "os": d.os,
                    "online": hub.is_online(d.device_id),
                    "hardware": json.loads(d.hardware or "{}"),
                }
                for d in rows
            ]
        }
    if name == "get_logs":
        d = owned(db, owner_id, args.get("device_id") or "")
        q = db.query(CommandLog).filter(CommandLog.device_pk == d.id).order_by(CommandLog.id.desc())
        search = (args.get("search") or "").strip()
        if search:
            q = q.filter(CommandLog.stdout.contains(search) | CommandLog.stderr.contains(search))
        offset = int(args.get("offset") or 0)
        limit = min(int(args.get("limit") or 10), 50)
        rows = q.offset(offset).limit(limit).all()
        return {
            "logs": [
                {
                    "id": r.id,
                    "command_id": r.command_id,
                    "action": r.action,
                    "status": r.status,
                    "exit_code": r.exit_code,
                    "stdout_tail": clip(r.stdout or ""),
                    "stderr_tail": clip(r.stderr or ""),
                }
                for r in rows
            ]
        }

    d = owned(db, owner_id, args.get("device_id") or "")
    os_name = d.os or "linux"

    async def go(action, params):
        result = await hub.send_command(db, d, action, params)
        return pack_result(result)

    if name == "get_hardware":
        return await go("get_hardware", {})
    if name == "execute_command":
        cmd = args.get("command") or ""
        if os_name in ("windows", "winpe"):
            return await go("run_powershell", {"script": cmd})
        return await go("run_shell", {"script": cmd})
    if name == "read_file":
        return await go("read_file", {"path": args.get("path")})
    if name == "write_file":
        return await go("write_file", {"path": args.get("path"), "content": args.get("content") or ""})
    if name == "download_file":
        return await go("download_file", {"url": args.get("url"), "path": args.get("path")})
    if name == "upload_file":
        return await go(
            "upload_file",
            {
                "path": args.get("path"),
                "content": args.get("content") or "",
                "content_base64": args.get("content_base64") or "",
            },
        )
    if name == "install_package":
        return await go("install_package", {"name": args.get("name")})
    if name == "install_os":
        return await go(
            "install_windows",
            {"image": args.get("image"), "product_key": args.get("product_key")},
        )
    if name == "get_processes":
        return await go("get_processes", {})
    if name == "get_services":
        return await go("get_services", {})
    if name == "reboot":
        return await go("reboot", {})
    if name == "shutdown":
        return await go("shutdown", {})
    if name == "get_screen":
        return await go("get_screen", {})
    raise HTTPException(400, f"Unknown tool {name}")


async def handle_rpc(db: Session, owner_id: int, body: dict) -> dict:
    method = body.get("method")
    rpc_id = body.get("id")
    params = body.get("params") or {}
    if method == "initialize":
        ver = params.get("protocolVersion") or "2025-03-26"
        if ver not in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"):
            ver = "2025-03-26"
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": ver,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "ai-pc-agent",
                    "version": "0.1.0",
                    "instructions": (
                        "These tools control a physical PC via a USB/Windows agent. "
                        "Always start with list_devices. Prefer execute_command over get_screen. "
                        "Command results are JSON with exit_code and stdout_tail, not screenshots. "
                        "Use get_logs if stdout was truncated. Windows ISO is the owner's file on USB."
                    ),
                },
            },
        }
    if method in ("notifications/initialized", "notifications/cancelled", "logging/setLevel"):
        return {}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {"resources": []}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {"prompts": []}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result = await run_tool(db, owner_id, name, args)
            text = json.dumps(result, ensure_ascii=False)
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {"content": [{"type": "text", "text": text}]},
            }
        except RuntimeError as e:
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [{"type": "text", "text": str(e)}],
                    "isError": True,
                },
            }
        except HTTPException as e:
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": e.status_code, "message": str(e.detail)},
            }
    if rpc_id is None:
        return {}
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": method}}
