import gc
import json
import pytest

from app import App, export_public
from key_clipboard import KeyClipboard
from secure_store import Store


KEY_ONE = "sk-test-only-not-a-real-key-one"
KEY_TWO = "sk-test-only-not-a-real-key-two"
SESSION = {"auth_mode": "session", "cookies": [{"name": "session", "value": "test-only-session"}],
           "scope": {"userId": "u", "workspaceId": "w", "workspaceMemberId": "m"}}


def test_key_import_is_encrypted_preserves_session_and_public_cache(tmp_path):
    store = Store(tmp_path)
    try:
        account = store.add("one@example.test")
        store.bind_session(account, "one@example.test", SESSION, {"tier": "go", "balance_usd": "1.50"})
        before = store.get(account)
        store.save_api_key(account, KEY_ONE)
        assert store.has_api_key(account)
        assert store.get(account) == before
        saved = store.credentials(account)
        assert saved["api_key"] == KEY_ONE
        for name in SESSION:
            assert saved[name] == SESSION[name]
        assert KEY_ONE not in json.dumps(store.all())
        blob = store.db.execute("SELECT secrets FROM accounts WHERE id=?", (account,)).fetchone()[0]
        assert KEY_ONE.encode() not in blob
        assert b"test-only-session" not in blob
        path = tmp_path / "public.csv"
        export_public(store.all(), path)
        assert KEY_ONE not in path.read_text(encoding="utf-8-sig")
        store.forget(account)
        assert not store.has_api_key(account)
    finally:
        store.close()


@pytest.mark.parametrize("bad", ["sk-******", "", "short", "x" * 513, "sk-value-has spaces", "sk-value\nline-break", "sk-value-has-中文"])
def test_invalid_or_masked_key_not_saved(tmp_path, bad):
    store = Store(tmp_path)
    try:
        account = store.add("one@example.test")
        store.bind_session(account, "one@example.test", SESSION, {})
        with pytest.raises(ValueError):
            store.save_api_key(account, bad)
        assert store.credentials(account) == SESSION
        assert not store.has_api_key(account)
    finally:
        store.close()


def test_key_only_import_cannot_make_unbound_account_look_connected(tmp_path):
    store = Store(tmp_path)
    try:
        account = store.add("one@example.test")
        with pytest.raises(ValueError, match="登录会话"):
            store.save_api_key(account, KEY_ONE)
        assert not store.get(account)["has_credentials"]
    finally:
        store.close()


def test_encryption_failure_keeps_previous_key_and_does_not_report_raw_value(tmp_path, monkeypatch):
    store = Store(tmp_path)
    try:
        account = store.add("one@example.test")
        store.bind_session(account, "one@example.test", SESSION, {})
        store.save_api_key(account, KEY_ONE)

        def fail(_):
            raise RuntimeError("encryption failed")

        monkeypatch.setattr("secure_store.encode", fail)
        with pytest.raises(RuntimeError):
            store.save_api_key(account, KEY_TWO)
        assert store.credentials(account)["api_key"] == KEY_ONE
    finally:
        store.close()


class FakeClipboardRoot:
    def __init__(self):
        self.clipboard = ""
        self.callbacks = {}
        self.counter = 0

    def clipboard_clear(self):
        self.clipboard = ""

    def clipboard_append(self, value):
        self.clipboard += value

    def clipboard_get(self):
        return self.clipboard

    def after(self, delay, callback):
        self.counter += 1
        self.callbacks[self.counter] = (delay, callback)
        return self.counter

    def after_cancel(self, timer):
        self.callbacks.pop(timer, None)


def test_clipboard_copies_full_key_then_clears_after_30_seconds():
    root = FakeClipboardRoot()
    clipboard = KeyClipboard(root)
    clipboard.copy(KEY_ONE)
    assert root.clipboard == KEY_ONE
    delay, callback = root.callbacks[clipboard.timer]
    assert delay == 30_000
    callback()
    assert root.clipboard == ""
    assert clipboard.value is None


def test_clipboard_cleanup_never_deletes_later_user_copy():
    root = FakeClipboardRoot()
    clipboard = KeyClipboard(root)
    clipboard.copy(KEY_ONE)
    root.clipboard = "user copied something else"
    clipboard.clear()
    assert root.clipboard == "user copied something else"
    assert not root.callbacks


def test_copy_another_key_replaces_old_timer():
    root = FakeClipboardRoot()
    clipboard = KeyClipboard(root)
    clipboard.copy(KEY_ONE)
    first_timer = clipboard.timer
    clipboard.copy(KEY_TWO)
    assert first_timer not in root.callbacks
    assert root.clipboard == KEY_TWO
    clipboard.clear()
    assert root.clipboard == ""


def test_real_controller_masks_keys_and_copies_only_clicked_account(tmp_path, monkeypatch):
    gc.collect()
    app = App(tmp_path, smoke=True)
    try:
        one = app.store.add("one@example.test")
        two = app.store.add("two@example.test")
        for account, email, key in ((one, "one@example.test", KEY_ONE), (two, "two@example.test", KEY_TWO)):
            app.store.bind_session(account, email, SESSION, {"tier": "go", "expires_at": "2026-11-22T01:09:33Z"})
            app.store.save_api_key(account, key)
        copied = []
        monkeypatch.setattr(app.key_clipboard, "copy", copied.append)
        app.refresh_view(one)
        app.update()
        assert KEY_ONE not in json.dumps(app.view.records)
        assert KEY_TWO not in json.dumps(app.view.records)
        app.view.cards[two].buttons[2].invoke()
        assert copied == [KEY_TWO]
        assert app.selected_id == two
        assert KEY_TWO not in app.status.get()
        assert "订阅到期" in app.view.cards[two].canvas.itemcget(app.view.cards[two].expiry_item, "text")
    finally:
        app.close()
        gc.collect()
