"""Read-only session queries with optional existing-key metadata sync."""

from client import ApiError, Client
from key_inventory import append_key_inventory, read_key_inventory


def capture_session(browser, previous=None, client_factory=Client, include_keys=False):
    try:
        browser.start_capture()
        cookies = browser.cookies()
        client = client_factory(cookies=cookies)
        identity = client.identity()
        scope = client.current_scope(identity["userId"])
        live = client.snapshot(scope)
        if include_keys:
            append_key_inventory(live, client, scope)
        # Preserve previously saved key material only for the same verified scope.
        secret = dict(previous or {}) if (previous or {}).get("scope") == scope else {}
        secret.update(cookies=cookies, scope=scope, auth_mode="session")
        live["auth_mode"] = "session"
        return identity, secret, live
    finally:
        browser.close()


def query_session(email, secret, client_factory=Client, include_keys=False):
    if not secret.get("cookies") or not isinstance(secret.get("scope"), dict):
        raise ApiError("未保存有效登录会话，请重新登录并保存。")
    client = client_factory(cookies=secret["cookies"])
    identity = client.identity(email)
    scope = client.current_scope(identity["userId"], secret["scope"].get("workspaceId"))
    if scope != secret["scope"]:
        raise ApiError("绑定范围已发生变化，请重新登录并保存。")
    live = client.snapshot(scope)
    if include_keys:
        append_key_inventory(live, client, scope)
    live["auth_mode"] = "session"
    return live


def query_keys(email, secret, client_factory=Client):
    if not secret.get("cookies") or not isinstance(secret.get("scope"), dict):
        raise ApiError("未保存有效登录会话，请重新登录并保存。")
    client = client_factory(cookies=secret["cookies"])
    identity = client.identity(email)
    scope = client.current_scope(identity["userId"], secret["scope"].get("workspaceId"))
    if scope != secret["scope"]:
        raise ApiError("绑定范围已发生变化，未更新已有 Key。")
    return read_key_inventory(client, scope)
