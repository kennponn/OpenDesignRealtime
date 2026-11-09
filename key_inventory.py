"""Read existing key metadata, never create keys or treat prefixes as secrets."""

import re
from datetime import datetime

from client import ApiError, identifier
from secure_store import now, TZ


def read_key_inventory(client, scope):
    try:
        data, _ = client.key_list(scope)
        legacy = False
    except ApiError as error:
        if error.status not in (404, 405):
            raise
        data, _ = client.key_list(scope, legacy=True)
        legacy = True
    if "rawKey" in data or "secret" in data:
        raise ApiError("已有 Key 接口结构改变，未保存任何完整密钥。")
    entries, ids = [], set()
    current = datetime.now(TZ)
    for key in data["keys"]:
        if not isinstance(key, dict) or "rawKey" in key or "secret" in key:
            raise ApiError("已有 Key 接口结构改变，未保存任何完整密钥。")
        for field in ("userId", "workspaceId", "workspaceMemberId"):
            if (not legacy or key.get(field) is not None) and key.get(field) != scope[field]:
                raise ApiError("已有 Key 的归属与绑定账号不一致，未更新 Key 数据。")
        key_id = identifier(key.get("id"))
        if key_id in ids:
            raise ApiError("已有 Key 接口返回重复标识，未更新 Key 数据。")
        ids.add(key_id)
        name, prefix, status = key.get("name"), key.get("keyPrefix"), key.get("status")
        if (not isinstance(name, str) or not 1 <= len(name) <= 120
                or not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", prefix)
                or status not in ("active", "paused", "revoked")):
            raise ApiError("已有 Key 接口返回格式不匹配，未更新 Key 数据。")
        expires_at = key.get("expiresAt")
        expired = False
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    raise ValueError
                expired = expiry <= current
            except (TypeError, AttributeError, ValueError):
                raise ApiError("Key 到期时间格式不匹配，未更新 Key 数据。") from None
        entries.append({"id": key_id, "name": name, "prefix": prefix, "status": status,
                        "expires_at": expires_at, "expired": expired,
                        "available": status == "active" and not key.get("revokedAt") and not expired})
    return {"total": len(entries), "active": sum(entry["available"] for entry in entries),
            "entries": entries, "synced_at": now(), "full_key_returned": False}


def append_key_inventory(live, client, scope):
    try:
        live["key_inventory"] = read_key_inventory(client, scope)
        live["key_sync_error"] = None
    except ApiError as error:
        # A failed metadata read must not discard a successful quota snapshot.
        live["key_sync_error"] = str(error)
