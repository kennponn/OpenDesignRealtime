import copy
import gc
import json
import time

import pytest

from app import App
from client import ApiError, Client
from key_inventory import read_key_inventory
from secure_store import Store
from session_service import capture_session, query_keys, query_session
from test_client import COOKIE, SCOPE, transport
from test_session_service import mapping, factory, BrowserStub


PATH = "/v1/workspaces/workspace-1/runtime-api-keys"


def key(key_id="key-1", **extra):
    return {"id": key_id, "name": "test-key", "keyPrefix": "sk-testprefix", "status": "active",
            "expiresAt": None, "revokedAt": None, **SCOPE, **extra}


def response(keys=None):
    return {"schemaVersion": 1, "scope": copy.deepcopy(SCOPE), "keys": keys if keys is not None else [key()],
            "capabilities": {"canCreate": True, "allowedModels": None}}


def secret():
    return {"auth_mode": "session", "cookies": [COOKIE], "scope": copy.deepcopy(SCOPE)}


def test_key_inventory_read_only_returns_metadata_not_complete_key():
    fake, calls = transport({PATH: response([key(), key("key-2", status="paused"), key("key-3", status="revoked")])})
    result = read_key_inventory(Client(cookies=[COOKIE], transport=fake), SCOPE)
    assert result["total"] == 3 and result["active"] == 1
    assert result["full_key_returned"] is False
    assert result["synced_at"]
    assert result["entries"][0]["prefix"] == "sk-testprefix"
    assert "api_key" not in json.dumps(result)
    assert len(calls) == 1
    assert calls[0][1:3] == ("GET", None)
    assert "Authorization" not in calls[0][3]


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_key_failure_not_retried_through_legacy_route(status):
    fake, calls = transport({PATH: ApiError("denied", status)})
    with pytest.raises(ApiError):
        read_key_inventory(Client(transport=fake), SCOPE)
    assert len(calls) == 1


def test_legacy_fallback_only_if_workspace_route_not_available():
    fake, calls = transport({PATH: ApiError("not found", 404), "/v1/runtime-api-keys": {"keys": [key()]}})
    result = read_key_inventory(Client(transport=fake), SCOPE)
    assert result["total"] == 1
    assert [call[0] for call in calls] == [PATH, "/v1/runtime-api-keys"]
    assert all(method == "GET" and body is None for _, method, body, _ in calls)


@pytest.mark.parametrize("field", ["userId", "workspaceId", "workspaceMemberId"])
def test_per_key_wrong_scope_rejected_even_if_envelope_matches(field):
    fake, _ = transport({PATH: response([key(**{field: "other"})])})
    with pytest.raises(ApiError, match="归属"):
        read_key_inventory(Client(transport=fake), SCOPE)


@pytest.mark.parametrize("bad", [key(rawKey="sk-test-secret-never-exported"), key(secret="test-private"),
                                 key(status="unknown"), key(expiresAt="invalid"), key(expiresAt="2026-11-06T18:00:00"),
                                 key(keyPrefix="prefix with spaces"), key(name="x" * 121)])
def test_invalid_inventory_cannot_create_fake_key_or_leak_secret(bad):
    fake, _ = transport({PATH: response([bad])})
    with pytest.raises(ApiError) as error:
        read_key_inventory(Client(transport=fake), SCOPE)
    assert "sk-test-secret-never-exported" not in str(error.value)
    assert "test-private" not in str(error.value)


def test_duplicate_key_ids_rejected():
    fake, _ = transport({PATH: response([key(), key()])})
    with pytest.raises(ApiError, match="重复"):
        read_key_inventory(Client(transport=fake), SCOPE)


def test_expired_revoked_and_paused_keys_not_counted_available():
    fake, _ = transport({PATH: response([key(), key("key-2", expiresAt="2020-01-01T00:00:00Z"),
                                       key("key-3", revokedAt="2026-01-01T00:00:00Z"), key("key-4", status="paused")])})
    result = read_key_inventory(Client(transport=fake), SCOPE)
    assert result["total"] == 4 and result["active"] == 1


@pytest.mark.parametrize("mismatch", ["email", "member"])
def test_identity_and_member_checked_before_key_list(mismatch):
    values = mapping()
    values[PATH] = response()
    make, calls = factory(values)
    saved = secret()
    if mismatch == "member":
        saved["scope"]["workspaceMemberId"] = "other"
    with pytest.raises(ApiError):
        query_keys("wrong@example.test" if mismatch == "email" else "actual@example.test", saved, make)
    assert PATH not in [call[0] for call in calls]


def test_capture_opt_in_reads_existing_keys_without_creating_or_using_them():
    values = mapping()
    values[PATH] = response()
    make, calls = factory(values)
    _, saved, live = capture_session(BrowserStub(), client_factory=make, include_keys=True)
    assert live["key_inventory"]["total"] == 1
    assert "api_key" not in saved
    assert len(calls) == 4
    assert all(method == "GET" and body is None and "Authorization" not in headers for _, method, body, headers in calls)


def test_key_list_failure_does_not_discard_successful_quota_query():
    values = mapping()
    values[PATH] = ApiError("Key 接口拒绝访问", 403)
    make, calls = factory(values)
    saved = secret()
    saved["api_key"] = "sk-existing-full-value-not-a-real-key"
    before = copy.deepcopy(saved)
    live = query_session("actual@example.test", saved, make, include_keys=True)
    assert live["balance_usd"] == "0.00"
    assert "key_inventory" not in live
    assert live["key_sync_error"] == "Key 接口拒绝访问"
    assert saved == before
    assert all("Authorization" not in headers for _, _, _, headers in calls)


def test_key_inventory_store_write_does_not_forge_new_quota_timestamp_or_change_credentials(tmp_path):
    store = Store(tmp_path)
    try:
        account = store.add("actual@example.test")
        store.bind_session(account, "actual@example.test", secret(), {"tier": "go"})
        before = store.get(account)
        public = dict(before["public"])
        public["key_inventory"] = {"total": 1, "active": 1}
        store.save_key_inventory(account, public)
        after = store.get(account)
        for field in ("status", "last_success", "last_attempt"):
            assert before[field] == after[field]
        assert store.credentials(account) == secret()
    finally:
        store.close()


def test_controller_key_sync_failure_preserves_successful_quota_status_and_previous_inventory(tmp_path, monkeypatch):
    gc.collect()
    app = App(tmp_path, smoke=True)
    try:
        account = app.store.add("actual@example.test")
        app.store.bind_session(account, "actual@example.test", secret(),
                               {"tier": "go", "key_inventory": {"total": 1, "active": 1}, "balance_usd": "1.50"})
        before = app.store.get(account)
        app.refresh_view(account)

        def fail(*args, **kwargs):
            raise ApiError("Key 接口暂时不可用", 500)

        monkeypatch.setattr("app.query_keys", fail)
        app.sync_keys()
        deadline = time.monotonic() + 3
        while app.jobs and time.monotonic() < deadline:
            app.update()
            time.sleep(.03)
        assert not app.jobs
        after = app.store.get(account)
        assert after["status"] == before["status"]
        assert after["last_success"] == before["last_success"]
        assert after["public"]["key_inventory"] == before["public"]["key_inventory"]
        assert after["public"]["key_sync_error"] == "Key 接口暂时不可用"
    finally:
        app.close()
        gc.collect()
