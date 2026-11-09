import copy
import gc
import json
import time

import pytest

from app import App
from client import ApiError, Client
from key_actions import create_existing_account_key, revoke_old_key
from secure_store import Store
from test_client import SCOPE, COOKIE
from test_key_inventory import PATH, key, response, secret
from test_session_service import mapping


FULL_KEY = "sk-test-created-full-value-not-real"


def creation_response():
    return {"schemaVersion": 1, "scope": SCOPE, "key": key("new-key"), "rawKey": FULL_KEY}


def factory(create_error=None, revoke_error=None, identity_error=False):
    calls = []

    def transport(path, method, body, headers):
        calls.append((path, method, body, headers))
        if path == "/auth/get-session":
            return {"user": {"id": SCOPE["userId"], "email": "other@example.test" if identity_error else "actual@example.test"}}
        if path == "/v1/workspaces/current":
            return copy.deepcopy(SCOPE)
        if path == PATH and method == "GET":
            return response([key("old-key"), key("new-key")])
        if path == PATH and method == "POST":
            if create_error:
                raise create_error
            return creation_response()
        if path == PATH + "/old-key/revoke":
            if revoke_error:
                raise revoke_error
            return {"schemaVersion": 1, "scope": SCOPE, "key": key("old-key", status="revoked")}
        raise AssertionError("Unexpected path or method")

    return lambda **kw: Client(transport=transport, **kw), calls


def test_create_only_posts_once_never_revokes_and_returns_complete_value():
    make, calls = factory()
    result = create_existing_account_key("actual@example.test", secret(), "manager", "old-key", make)
    assert result["created"]["api_key"] == FULL_KEY
    posts = [call for call in calls if call[1] == "POST"]
    assert len(posts) == 1 and posts[0][0] == PATH
    assert posts[0][2] == {"name": "manager", "expiresAt": None}
    assert all("Authorization" not in headers for _, _, _, headers in calls)


def test_create_mismatch_blocks_mutations():
    make, calls = factory(identity_error=True)
    with pytest.raises(ApiError):
        create_existing_account_key("actual@example.test", secret(), "manager", client_factory=make)
    assert all(method == "GET" for _, method, _, _ in calls)


def test_unknown_selected_old_key_cannot_create_then_revoke_arbitrary_key():
    make, calls = factory()
    with pytest.raises(ApiError, match="旧 Key"):
        create_existing_account_key("actual@example.test", secret(), "manager", "other-key", make)
    assert all(method == "GET" for _, method, _, _ in calls)


def test_create_timeout_never_retries_or_revokes_or_leaks_arbitrary_exception():
    make, calls = factory(create_error=RuntimeError(FULL_KEY))
    with pytest.raises(ApiError) as error:
        create_existing_account_key("actual@example.test", secret(), "manager", client_factory=make)
    assert "结果不确定" in str(error.value)
    assert FULL_KEY not in str(error.value)
    assert len([c for c in calls if c[1] == "POST"]) == 1


def test_revoke_requires_saved_verified_new_key_and_targets_only_selected_old():
    make, calls = factory()
    saved = {**secret(), **creation_response()["key"], "api_key": FULL_KEY, "key_id": "new-key", "key_status": "active"}
    revoke_old_key("actual@example.test", saved, "old-key", "new-key", make)
    posts = [call for call in calls if call[1] == "POST"]
    assert len(posts) == 1 and posts[0][0] == PATH + "/old-key/revoke"
    assert posts[0][2] is None


@pytest.mark.parametrize("old,new,saved", [("new-key", "new-key", secret()), ("old-key", "new-key", secret()),
                                          ("other-key", "new-key", {**secret(), "api_key": FULL_KEY, "key_id": "new-key", "key_status": "active"})])
def test_unsafe_revocation_is_blocked(old, new, saved):
    make, calls = factory()
    with pytest.raises(ApiError):
        revoke_old_key("actual@example.test", saved, old, new, make)
    assert all(method == "GET" for _, method, _, _ in calls)


def test_revoke_timeout_never_retries_or_creates_another_key():
    make, calls = factory(revoke_error=ApiError("timeout"))
    saved = {**secret(), "api_key": FULL_KEY, "key_id": "new-key", "key_status": "active"}
    with pytest.raises(ApiError, match="结果不确定"):
        revoke_old_key("actual@example.test", saved, "old-key", "new-key", make)
    assert len([c for c in calls if c[1] == "POST"]) == 1


def test_created_key_atomic_encrypted_save_and_previous_full_key_backup(tmp_path):
    store = Store(tmp_path)
    try:
        account = store.add("actual@example.test")
        store.bind_session(account, "actual@example.test", secret(), {"tier": "go"})
        store.save_api_key(account, "sk-prior-key-test-not-real")
        before = store.get(account)
        store.save_created_key(account, {"email": before["email"], "scope": SCOPE,
                                          "created": {"api_key": FULL_KEY, "key_id": "new-key", "key_status": "active"}})
        saved = store.credentials(account)
        assert saved["api_key"] == FULL_KEY
        assert saved["previous_keys"][0]["api_key"] == "sk-prior-key-test-not-real"
        assert saved["cookies"] == [COOKIE]
        assert FULL_KEY not in json.dumps(store.all())
        assert store.get(account)["last_success"] == before["last_success"]
        store.update(account, "query succeeded", {"tier": "go"}, success=True)
        assert store.get(account)["public"]["key_operation"]["state"] == "saved"
    finally:
        store.close()


def pump(app):
    deadline = time.monotonic() + 4
    while app.jobs and time.monotonic() < deadline:
        app.update()
        time.sleep(.025)
    assert not app.jobs


@pytest.mark.parametrize("save_fails", [False, True])
def test_controller_saves_new_key_before_revoke_and_never_revokes_if_save_fails(tmp_path, monkeypatch, save_fails):
    gc.collect()
    app = App(tmp_path, smoke=True)
    try:
        account = app.store.add("actual@example.test")
        app.store.bind_session(account, "actual@example.test", secret(), {"tier": "go"})
        events = []

        def create(*args):
            events.append("create")
            return {"email": "actual@example.test", "scope": SCOPE,
                    "created": {"api_key": FULL_KEY, "key_id": "new-key", "key_status": "active"}}

        def revoke(email, saved, old_id, new_id):
            assert saved["api_key"] == FULL_KEY
            assert old_id == "old-key" and new_id == "new-key"
            events.append("revoke")
            return {"total": 2, "active": 1, "entries": []}

        monkeypatch.setattr("app.create_existing_account_key", create)
        monkeypatch.setattr("app.revoke_old_key", revoke)
        monkeypatch.setattr("app.messagebox.showwarning", lambda *a, **k: None)
        if save_fails:
            monkeypatch.setattr(app.store, "save_created_key", lambda *a: (_ for _ in ()).throw(RuntimeError("disk error")))
        app.run_key_creation(account, "manager", "old-key")
        pump(app)
        assert events == (["create"] if save_fails else ["create", "revoke"])
        if save_fails:
            assert app.store.get(account)["public"]["key_operation"]["state"] == "needs_review"
            app.run_key_creation(account, "manager", "old-key")
            assert events == ["create"]
        else:
            assert app.store.has_api_key(account)
            assert app.store.get(account)["public"]["key_operation"]["state"] == "completed"
    finally:
        app.close()
        gc.collect()
