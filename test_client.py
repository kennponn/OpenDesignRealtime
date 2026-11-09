import copy
import json
import sqlite3
import urllib.error
import urllib.request

import pytest

from client import API, ApiError, Client, NoRedirect, cookie_header, normalize_windows
from secure_store import Store, decode, encode


SCOPE = {"userId": "user-1", "workspaceId": "workspace-1", "workspaceMemberId": "member-1"}
COOKIE = {"domain": "amr-api.open-design.ai", "path": "/", "name": "session_token", "value": "test-session-only"}
SNAPSHOT = {
    "schemaVersion": 1, "billingScopeVersion": 2, "billingMode": "shared",
    "workspaceId": "workspace-1", "workspaceMemberId": "member-1",
    "wallet": {"balanceUsd": "0.00"},
    "plan": {"tier": "pro", "interval": "month", "currentPeriodEnd": "2026-11-30T02:00:00Z", "cancelAtPeriodEnd": False, "status": "active"},
    "designPlanWindows": [{"kind": "7d", "remainingCredits": "72", "limitCredits": "90", "resetsAt": None}],
    "walletBreakdown": [{"kind": "plan_grant", "amountUsd": "0", "expiresAt": None}],
}


def transport(mapping):
    calls = []

    def run(path, method, body, headers):
        calls.append((path, method, body, headers))
        value = mapping[path]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)
    return run, calls


def test_identity_matching():
    fake, calls = transport({"/auth/get-session": {"user": {"id": "user-1", "email": "One@example.test"}}})
    client = Client(cookies=[COOKIE], transport=fake)
    assert client.identity("one@example.test")["userId"] == "user-1"
    assert calls[0][3]["Cookie"] == "session_token=test-session-only"
    with pytest.raises(ApiError, match="不一致"):
        client.identity("two@example.test")


@pytest.mark.parametrize("session", [None, {}, {"user": None}, []])
def test_not_logged_in(session):
    fake, _ = transport({"/auth/get-session": session})
    with pytest.raises(ApiError, match="尚未登录"):
        Client(transport=fake).identity("one@example.test")


def test_current_scope():
    fake, _ = transport({"/v1/workspaces/current": SCOPE})
    client = Client(transport=fake)
    assert client.current_scope("user-1") == SCOPE
    with pytest.raises(ApiError, match="用户标识不一致"):
        client.current_scope("other")
    with pytest.raises(ApiError, match="原绑定不同"):
        client.current_scope("user-1", "other")


def test_snapshot_real_mapping():
    fake, calls = transport({"/v1/workspaces/workspace-1/billing/snapshot": SNAPSHOT})
    data = Client(cookies=[COOKIE], transport=fake).snapshot(SCOPE)
    assert data["tier"] == "pro"
    assert data["balance_usd"] == "0.00"
    assert data["windows"][0]["percent"] == 80
    assert data["windows"][0]["remaining"] == "72"
    assert data["expires_at"] == "2026-11-30T02:00:00Z"
    assert data["cancel_at_period_end"] is False
    assert len(calls) == 1 and calls[0][1] == "GET"


@pytest.mark.parametrize("field,value", [("workspaceId", "other"), ("workspaceMemberId", "other"), ("schemaVersion", 99), ("billingScopeVersion", 1), ("wallet", None)])
def test_snapshot_rejects_wrong_scope_or_schema(field, value):
    snapshot = copy.deepcopy(SNAPSHOT)
    snapshot[field] = value
    fake, _ = transport({"/v1/workspaces/workspace-1/billing/snapshot": snapshot})
    with pytest.raises(ApiError):
        Client(transport=fake).snapshot(SCOPE)


def test_legacy_mapping():
    snapshot = {key: value for key, value in SNAPSHOT.items() if key not in ("billingMode", "plan", "designPlanWindows", "walletBreakdown")}
    fake, _ = transport({
        "/v1/workspaces/workspace-1/billing/snapshot": snapshot,
        "/v1/billing/summary?fresh=true&timeZone=Asia%2FShanghai": {
            "membershipTier": "plus", "billingInterval": "monthly", "subscriptionCurrentPeriodEnd": None,
            "subscriptionCancelAtPeriodEnd": True,
        },
        "/v1/workspaces/workspace-1/billing/coding-plan-usage": {
            "workspaceId": "workspace-1", "windows": [{"durationSeconds": 604800, "remainingCredits": "1", "limitCredits": "2"}],
        },
    })
    data = Client(transport=fake).snapshot(SCOPE)
    assert data["tier"] == "plus"
    assert data["windows"][0]["kind"] == "7d"
    assert data["windows"][0]["percent"] == 50


def test_key_only_does_not_leak_session():
    fake, calls = transport({"/v1/workspaces/workspace-1/billing/snapshot": SNAPSHOT})
    Client(cookies=[COOKIE], key="test-api-key", transport=fake).snapshot(SCOPE, key_only=True)
    headers = calls[0][3]
    assert headers["Authorization"] == "Bearer test-api-key"
    assert "Cookie" not in headers


def test_session_does_not_send_key():
    fake, calls = transport({"/v1/workspaces/workspace-1/billing/snapshot": SNAPSHOT})
    Client(cookies=[COOKIE], key="test-api-key", transport=fake).snapshot(SCOPE)
    assert "Authorization" not in calls[0][3]


def test_redirects_blocked():
    request = urllib.request.Request(API + "/v1/billing/summary", headers={"Cookie": "secret"})
    assert NoRedirect().redirect_request(request, None, 302, "redirect", {}, "https://example.test") is None


def test_cookies_restricted_to_api_scope():
    assert cookie_header([{**COOKIE, "domain": "google.com"}], "/api/auth/get-session") == ""
    assert cookie_header([{**COOKIE, "path": "/api/auth"}], "/api/v1/billing/summary") == ""
    assert cookie_header([{**COOKIE, "path": "/api/auth"}], "/api/authenticate") == ""
    assert cookie_header([COOKIE], "/api/auth/get-session") == "session_token=test-session-only"
    with pytest.raises(ApiError):
        cookie_header([{**COOKIE, "value": "secret\r\nmalicious"}], "/")


@pytest.mark.parametrize("remaining,limit", [("nan", "10"), ("-1", "10"), ("1", "inf"), (None, "10")])
def test_window_invalid(remaining, limit):
    with pytest.raises(ApiError):
        normalize_windows([{"kind": "7d", "remainingCredits": remaining, "limitCredits": limit}])


def test_window_zero_and_missing():
    assert normalize_windows(None) is None
    assert normalize_windows([]) == []
    assert normalize_windows([{"kind": "7d", "remainingCredits": "0", "limitCredits": "0"}])[0]["percent"] is None


def test_key_creation_checks_scope():
    key = {"id": "key-1", "keyPrefix": "prefix", "status": "active", **SCOPE}
    fake, calls = transport({"/v1/workspaces/workspace-1/runtime-api-keys": {"schemaVersion": 1, "scope": SCOPE, "key": key, "rawKey": "sk-secret-test-key"}})
    result = Client(cookies=[COOKIE], transport=fake).create_key(SCOPE, "/v1/workspaces/workspace-1/runtime-api-keys", "test-name")
    assert result["api_key"] == "sk-secret-test-key"
    assert len(calls) == 1 and calls[0][1] == "POST"
    assert calls[0][2] == {"name": "test-name", "expiresAt": None}
    key["workspaceId"] = "other"
    with pytest.raises(ApiError, match="归属不匹配"):
        Client(transport=fake).create_key(SCOPE, "/v1/workspaces/workspace-1/runtime-api-keys", "test-name")


def test_key_list():
    fake, _ = transport({"/v1/workspaces/workspace-1/runtime-api-keys": {"schemaVersion": 1, "scope": SCOPE, "keys": [], "capabilities": {"canCreate": True}}})
    data, path = Client(transport=fake).key_list(SCOPE)
    assert data["capabilities"]["canCreate"] is True
    assert path.endswith("runtime-api-keys")


def test_legacy_key_contract():
    fake, calls = transport({"/v1/runtime-api-keys": {"keys": []}})
    _, path = Client(transport=fake).key_list(SCOPE, legacy=True)
    assert path == "/v1/runtime-api-keys"
    fake, calls = transport({path: {"key": {"id": "key-1", "status": "active"}, "rawKey": "sk-test-only-key-not-real"}})
    result = Client(transport=fake).create_key(SCOPE, path, "test")
    assert result["api_key"] == "sk-test-only-key-not-real"
    assert calls[0][1] == "POST"


def test_dpapi_roundtrip():
    raw = {"api_key": "not-a-real-key", "cookies": [COOKIE]}
    blob = encode(raw)
    assert b"not-a-real-key" not in blob
    assert b"test-session-only" not in blob
    assert decode(blob) == raw


def test_store_does_not_export_secrets_and_keeps_last_good(tmp_path):
    store = Store(tmp_path)
    account = store.add("one@example.test")
    store.save_credentials(account, {"api_key": "not-a-real-key", "cookies": [COOKIE]})
    store.update(account, "success", {"balance_usd": "12"}, success=True)
    old_time = store.get(account)["last_success"]
    store.update(account, "network failed")
    public = store.get(account)
    assert public["public"]["balance_usd"] == "12"
    assert public["last_success"] == old_time
    assert "not-a-real-key" not in json.dumps(public)
    assert "test-session-only" not in json.dumps(store.all())
    assert store.credentials(account)["api_key"] == "not-a-real-key"
    store.forget(account)
    assert store.credentials(account) == {}
    store.close()


@pytest.mark.parametrize("account_id", ["../escape", "..\\escape", "", "f" * 31])
def test_profile_path_restriction(tmp_path, account_id):
    store = Store(tmp_path)
    with pytest.raises(ValueError):
        store.profile(account_id)
    store.close()
