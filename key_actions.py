"""Explicit key mutations, never used by automatic quota refresh."""

import urllib.parse

from client import ApiError, Client, identifier
from key_inventory import read_key_inventory


def verified_client(email, secret, client_factory=Client):
    if not secret.get("cookies") or not isinstance(secret.get("scope"), dict):
        raise ApiError("请先保存此账号的登录会话。")
    client = client_factory(cookies=secret["cookies"])
    identity = client.identity(email)
    scope = client.current_scope(identity["userId"], secret["scope"].get("workspaceId"))
    if scope != secret["scope"]:
        raise ApiError("账号绑定范围变化，未操作 Key。")
    return client, scope


def create_existing_account_key(email, secret, name, old_id=None, client_factory=Client):
    client, scope = verified_client(email, secret, client_factory)
    inventory = read_key_inventory(client, scope)
    if old_id and not any(entry["id"] == old_id and entry["status"] != "revoked" for entry in inventory["entries"]):
        raise ApiError("所选旧 Key 已不存在或已撤销，未新建 Key；请先同步列表。")
    path = f"/v1/workspaces/{urllib.parse.quote(identifier(scope['workspaceId']), safe='')}/runtime-api-keys"
    try:
        created = client.create_key(scope, path, name)
    except Exception:
        # Never retry POST: even a timeout may have created a key successfully.
        raise ApiError("新建请求已发出但结果不确定；旧 Key 未撤销。请检查官网，勿重复新建。") from None
    return {"email": email, "scope": scope, "created": created}


def revoke_old_key(email, secret, old_id, new_id, client_factory=Client):
    if old_id == new_id:
        raise ApiError("不能撤销刚创建并保存的新 Key。")
    client, scope = verified_client(email, secret, client_factory)
    if secret.get("key_id") != new_id or not secret.get("api_key") or secret.get("key_status") != "active":
        raise ApiError("新 Key 尚未成功保存或不再可用，未撤销旧 Key。")
    inventory = read_key_inventory(client, scope)
    if not any(entry["id"] == new_id and entry["available"] for entry in inventory["entries"]):
        raise ApiError("官网未确认新 Key 可用，未撤销旧 Key。")
    old = next((entry for entry in inventory["entries"] if entry["id"] == old_id), None)
    if not old:
        raise ApiError("所选旧 Key 不在已核验的账号列表，未撤销任何 Key。")
    if old["status"] == "revoked":
        return inventory
    try:
        client.revoke_key(scope, old_id)
    except Exception:
        raise ApiError("新 Key 已保存；旧 Key 撤销结果不确定，请在官网检查。不会自动重试或再新建。") from None
    try:
        return read_key_inventory(client, scope)
    except ApiError:
        raise ApiError("新 Key 已保存、旧 Key 已确认撤销；列表刷新失败，请手动同步。") from None
