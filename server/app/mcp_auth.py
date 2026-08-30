"""Shared MCP auth helpers."""

from fastapi import HTTPException

from .config import settings


def mcp_public_url() -> str:
    return f"{settings.public_url.rstrip('/')}/mcp"


def mcp_connect_url(key: str) -> str:
    return f"{mcp_public_url()}?key={key}"


def mcp_bearer_challenge() -> HTTPException:
    meta = f"{settings.public_url.rstrip('/')}/.well-known/oauth-protected-resource"
    return HTTPException(
        401,
        "Неверный MCP-ключ. Используй Authorization: Bearer <key> или ?key= в URL.",
        headers={"WWW-Authenticate": f'Bearer realm="ai-pc-agent", resource_metadata="{meta}"'},
    )


def ensure_mcp_key(db, user_id: int) -> str:
    from .db import McpKey
    import secrets

    row = db.query(McpKey).filter(McpKey.owner_id == user_id).first()
    if row:
        return row.key
    key = secrets.token_urlsafe(24)
    db.add(McpKey(owner_id=user_id, key=key))
    db.commit()
    return key
