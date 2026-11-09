import copy
import csv
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import App, account_values, export_public, window_reset_text
from client import ApiError, Client
from secure_store import Store
from session_service import capture_session, query_session
from test_client import COOKIE, SCOPE, SNAPSHOT, transport


def factory(mapping):
    fake, calls = transport(mapping)

    def make(**kwargs):
        assert set(kwargs) == {"cookies"}
        return Client(transport=fake, **kwargs)

    return make, calls


def mapping():
    snapshot = copy.deepcopy(SNAPSHOT)
    snapshot["designPlanWindows"] = [
        {"kind": "5h", "remainingCredits": "27396", "limitCredits": "50000", "resetsAt": "2026-11-06T11:09:33Z"},
        {"kind": "7d", "remainingCredits": "84401", "limitCredits": "150000", "resetsAt": "2026-11-12T01:09:33Z"},
    ]
    return {
        "/auth/get-session": {"user": {"id": SCOPE["userId"], "email": "Actual@example.test"}},
        "/v1/workspaces/current": SCOPE,
        "/v1/workspaces/workspace-1/billing/snapshot": snapshot,
    }


class BrowserStub:
    def __init__(self, error=None):
        self.started = False
        self.closed = False
        self.error = error

    def start_capture(self):
        self.started = True
        if self.error:
            raise self.error

    def cookies(self):
        return [COOKIE]

    def close(self):
        self.closed = True


def test_capture_read_only_without_key_and_detects_actual_email():
    make, calls = factory(mapping())
    browser = BrowserStub()
    identity, secret, live = capture_session(browser, client_factory=make)
    assert identity == {"userId": SCOPE["userId"], "email": "actual@example.test"}
    assert secret == {"cookies": [COOKIE], "scope": SCOPE, "auth_mode": "session"}
    assert [w["percent"] for w in live["windows"]] == [54.8, 56.3]
    assert live["balance_usd"] == "0.00"
    assert browser.started and browser.closed
    assert len(calls) == 3
    assert all(method == "GET" and body is None for _, method, body, _ in calls)
    assert all("runtime-api-keys" not in path and "Authorization" not in headers for path, _, _, headers in calls)


@pytest.mark.parametrize("where", ["browser", "identity", "scope", "snapshot"])
def test_capture_always_closes_connection_on_failure(where):
    values = mapping()
    browser = BrowserStub(ApiError("ordinary browser still open") if where == "browser" else None)
    path = {"identity": "/auth/get-session", "scope": "/v1/workspaces/current",
            "snapshot": "/v1/workspaces/workspace-1/billing/snapshot"}.get(where)
    if path:
        values[path] = ApiError("read failed")
    make, _ = factory(values)
    with pytest.raises(ApiError):
        capture_session(browser, client_factory=make)
    assert browser.closed


def test_existing_keys_preserved_but_never_used_or_queried():
    make, calls = factory(mapping())
    previous = {"scope": SCOPE, "api_key": "existing-test-key", "auth_mode": "key"}
    _, secret, _ = capture_session(BrowserStub(), previous, make)
    assert previous["auth_mode"] == "key"
    assert secret["api_key"] == previous["api_key"]
    assert secret["auth_mode"] == "session"
    live = query_session("actual@example.test", secret, make)
    assert live["auth_mode"] == "session"
    assert all("Authorization" not in headers for _, _, _, headers in calls)


def test_previous_other_scope_not_merged():
    make, _ = factory(mapping())
    previous = {"scope": {**SCOPE, "userId": "other"}, "api_key": "other-user-key"}
    _, secret, _ = capture_session(BrowserStub(), previous, make)
    assert "api_key" not in secret


def test_refresh_rejects_account_mismatch_before_billing():
    make, calls = factory(mapping())
    with pytest.raises(ApiError, match="不一致"):
        query_session("other@example.test", {"cookies": [COOKIE], "scope": SCOPE}, make)
    assert len(calls) == 1


def test_refresh_rejects_member_mismatch_before_billing():
    make, calls = factory(mapping())
    with pytest.raises(ApiError, match="绑定范围"):
        query_session("actual@example.test", {"cookies": [COOKIE], "scope": {**SCOPE, "workspaceMemberId": "other"}}, make)
    assert len(calls) == 2


@pytest.mark.parametrize("secret", [{}, {"api_key": "test-key", "scope": SCOPE}, {"cookies": [COOKIE]}])
def test_refresh_requires_saved_session(secret):
    with pytest.raises(ApiError, match="未保存有效"):
        query_session("actual@example.test", secret)


def capture_values():
    make, _ = factory(mapping())
    return capture_session(BrowserStub(), client_factory=make)


def test_identity_correction_and_binding_commit_atomically(tmp_path):
    store = Store(tmp_path)
    try:
        account = store.add("typed@example.test")
        identity, secret, live = capture_values()
        profile = store.profile(account)
        store.bind_session(account, identity["email"], secret, live)
        row = store.get(account)
        assert row["email"] == identity["email"] and row["has_credentials"]
        assert row["last_success"] and row["public"]["windows"] == live["windows"]
        assert store.profile(account) == profile
        assert store.credentials(account) == secret
        raw = bytes(store.db.execute("SELECT secrets FROM accounts WHERE id=?", (account,)).fetchone()[0])
        assert COOKIE["value"].encode() not in raw
        assert COOKIE["value"] not in json.dumps(row)
    finally:
        store.close()


def test_duplicate_email_correction_preserves_both_rows(tmp_path):
    store = Store(tmp_path)
    try:
        identity, secret, live = capture_values()
        existing = store.add(identity["email"])
        store.bind_session(existing, identity["email"], secret, live)
        unbound = store.add("typed@example.test")
        before = store.all()
        with pytest.raises(ValueError, match="已在列表中"):
            store.bind_session(unbound, identity["email"], secret, live)
        assert store.all() == before
        assert store.credentials(existing) == secret
    finally:
        store.close()


def test_bound_row_cannot_switch_identity(tmp_path):
    store = Store(tmp_path)
    try:
        identity, secret, live = capture_values()
        account = store.add(identity["email"])
        store.bind_session(account, identity["email"], secret, live)
        before = store.get(account)
        with pytest.raises(ValueError, match="不能改绑"):
            store.bind_session(account, "other@example.test", secret, live)
        assert store.get(account) == before
    finally:
        store.close()


def test_export_both_quota_windows_without_secrets(tmp_path):
    _, _, live = capture_values()
    path = tmp_path / "quota.csv"
    export_public([{"email": "actual@example.test", "label": "=not-a-formula", "public": live,
                    "last_success": "2026-11-06T15:39:00+08:00", "status": "ok"}], path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        row = list(csv.DictReader(stream))[0]
    assert row["5小时剩余百分比"] == "54.8%"
    assert row["7天剩余百分比"] == "56.3%"
    assert row["5小时重置时间"] == "2026-11-06T11:09:33Z"
    assert row["备注"] == "'=not-a-formula"
    assert COOKIE["value"] not in path.read_text(encoding="utf-8-sig")


def test_table_has_two_distinct_reset_dates_in_beijing_time():
    _, _, live = capture_values()
    record = {"email": "actual@example.test", "public": live,
              "last_success": "2026-11-06T15:39:00+08:00", "status": "ok"}
    values = account_values(record)
    assert values[2:6] == ["54.8%", "2026-11-06 19:09", "56.3%", "2026-11-12 09:09"]
    assert values[8] == "2026-11-06 15:39"
    assert account_values(record, busy=True)[-1] == "处理中"


@pytest.mark.parametrize("windows", [None, [], [{"kind": "7d", "resets_at": None}]])
def test_missing_reset_dates_not_invented_or_replaced_with_other_window(windows):
    assert window_reset_text({"windows": windows}, "5h") == "未返回"
    assert window_reset_text({"windows": windows}, "7d") == "未返回"


def test_reset_date_keeps_full_year_and_day_rollover():
    data = {"windows": [{"kind": "5h", "resets_at": "2026-12-31T20:00:00Z"}]}
    assert window_reset_text(data, "5h") == "2027-01-01 04:00"


def test_multiple_accounts_survive_restart_and_use_own_session(tmp_path):
    store = Store(tmp_path)
    accounts = []
    try:
        for index in range(2):
            email = f"account{index}@example.test"
            scope = {"userId": f"user-{index}", "workspaceId": f"workspace-{index}", "workspaceMemberId": f"member-{index}"}
            cookie = {**COOKIE, "value": f"session-for-account-{index}"}
            snapshot = {**copy.deepcopy(SNAPSHOT), "workspaceId": scope["workspaceId"], "workspaceMemberId": scope["workspaceMemberId"]}
            snapshot["wallet"]["balanceUsd"] = str(index)
            fake, calls = transport({
                "/auth/get-session": {"user": {"id": scope["userId"], "email": email}},
                "/v1/workspaces/current": scope,
                f"/v1/workspaces/{scope['workspaceId']}/billing/snapshot": snapshot,
            })
            client = Client(cookies=[cookie], transport=fake)
            live = client.snapshot(scope)
            account = store.add(email)
            store.bind_session(account, email, {"cookies": [cookie], "scope": scope, "auth_mode": "session"}, live)
            accounts.append((account, email, cookie, fake, calls, index))
    finally:
        store.close()
    reopened = Store(tmp_path)
    try:
        for account, email, cookie, fake, calls, index in accounts:
            calls.clear()
            make = lambda **kwargs: Client(transport=fake, **kwargs)
            result = query_session(email, reopened.credentials(account), make)
            assert result["balance_usd"] == str(index)
            assert len(calls) == 3
            assert all(headers["Cookie"] == "session_token=" + cookie["value"] for _, _, _, headers in calls)
            assert all("Authorization" not in headers for _, _, _, headers in calls)
    finally:
        reopened.close()


def test_new_account_login_pins_id_despite_changed_selection():
    first = {"id": "first", "email": "first@example.test"}
    second = {"id": "second", "email": "second@example.test"}
    refreshed = []
    ui = SimpleNamespace(store=SimpleNamespace(get=lambda account: second if account == "second" else first),
                         selected=lambda: first, jobs=set(), refresh_view=refreshed.append)
    with patch("app.messagebox.askokcancel", return_value=False) as confirm:
        App.login(ui, "second")
    assert refreshed == ["second"]
    text = confirm.call_args.args[1]
    assert "second@example.test" in text and "first@example.test" not in text
    assert "添加账号" in text


def test_login_does_not_start_while_same_account_is_busy():
    record = {"id": "second", "email": "second@example.test"}
    ui = SimpleNamespace(store=SimpleNamespace(get=lambda _: record), jobs={"second"})
    with patch("app.messagebox.askokcancel") as confirm:
        App.login(ui, "second")
    confirm.assert_not_called()


@pytest.mark.parametrize("email", [None, "", "invalid", "bad\r\n@example.test", 42])
def test_discovery_rejects_invalid_identity_email(email):
    make, _ = factory({"/auth/get-session": {"user": {"id": "user-1", "email": email}}})
    with pytest.raises(ApiError, match="邮箱无效"):
        make(cookies=[]).identity()
