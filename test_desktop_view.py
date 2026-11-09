import copy
import gc
import tkinter as tk
from datetime import datetime

import pytest

from desktop_view import Dashboard, account_actions, date_text, grid_columns, popup_position, quota_data, reset_countdown, state_of, wallet_text


def record(account="one", status="接口查询成功 / 登录会话", bound=True):
    return {
        "id": account, "email": account + "@example.test", "label": "",
        "has_credentials": bound, "has_api_key": False, "status": status, "last_success": "2026-11-06T08:15:00Z",
        "public": {"tier": "go", "balance_usd": "0.0000", "expires_at": "2026-11-22T01:09:33Z",
                   "windows": [{"kind": "5h", "percent": 28.3, "resets_at": "2026-11-06T11:09:33Z"},
                               {"kind": "7d", "percent": 47.4, "resets_at": "2026-11-12T01:09:33Z"}]},
    }


@pytest.mark.parametrize("value,expected", [(None, "未查询"), ("0.0000", "$0.00"),
                                           ("12.3499", "$12.35"), ("1234.50", "$1,234.50"),
                                           ("NaN", "未返回"), ("Infinity", "未返回"), ("invalid", "未返回")])
def test_wallet_display(value, expected):
    assert wallet_text(value) == expected


def test_card_quota_preserves_both_actual_reset_dates():
    data = record()["public"]
    assert quota_data(data, "5h") == {"value": "28.3%", "percent": 28.3, "reset": "2026-11-06 19:09"}
    assert quota_data(data, "7d") == {"value": "47.4%", "percent": 47.4, "reset": "2026-11-12 09:09"}


@pytest.mark.parametrize("tier,balance,expected", [
    ("go", "1.50", "余额 $1.50"), ("free", "0", "余额 $0.00"),
    ("pro", "1234.50", "余额 $1,234.50"), ("max", None, "余额 未查询"),
])
def test_wallet_next_to_plan_not_under_management_button(ui, tier, balance, expected):
    root, view, _, _ = ui
    root.geometry("1240x820")
    root.update()
    first = record("one")
    first["public"].update(tier=tier, balance_usd=balance)
    view.set_records([first, record("two"), record("three")], set(), "one")
    root.update()
    card = view.cards["one"]
    assert card.canvas.itemcget(card.wallet_item, "text") == expected
    assert card.canvas.itemcget(card.wallet_item, "anchor") == "w"
    assert card.canvas.coords(card.tier_item)[1] == card.canvas.coords(card.wallet_item)[1]
    assert card.canvas.bbox(card.wallet_item)[2] < card.buttons[1].winfo_x()
    view.verify_layout()


@pytest.mark.parametrize("percent", [None, "10", float("nan"), float("inf")])
def test_invalid_quota_not_displayed_as_zero(percent):
    data = {"windows": [{"kind": "5h", "percent": percent, "resets_at": None}]}
    assert quota_data(data, "5h") == {"value": "未返回", "percent": None, "reset": "未返回"}


def test_zero_quota_distinct_from_missing_window():
    assert quota_data({"windows": [{"kind": "5h", "percent": 0}]}, "5h")["value"] == "0%"
    assert quota_data({"windows": []}, "5h")["value"] == "未返回"


@pytest.mark.parametrize("status,expected", [("查询失败 / 登录会话失效", "需处理"),
                                           ("需重新登录", "需处理"), ("实际邮箱不一致", "需处理"),
                                           ("接口查询成功 / 登录会话", "已连接")])
def test_card_status_does_not_hide_failure(status, expected):
    assert state_of(record(status=status))[0] == expected


def test_loading_and_unbound_states():
    assert state_of(record(), True)[0] == "正在查询"
    assert state_of(record(bound=False))[0] == "待登录"


def test_card_date_crosses_year_in_beijing_time():
    assert date_text("2026-12-31T20:00:00Z") == "2027-01-01 04:00"
    assert date_text(None) == "未返回"


def test_no_placeholder_logo_and_text_aligned_to_left_edge(ui):
    _, view, _, _ = ui
    header_text = [view.header.itemcget(item, "text") for item in view.header.find_all() if view.header.type(item) == "text"]
    assert "OD" not in header_text
    for card in view.cards.values():
        text = [card.canvas.itemcget(item, "text") for item in card.canvas.find_all() if card.canvas.type(item) == "text"]
        assert "OD" not in text
        assert card.canvas.coords(card.email_item)[0] == 18
    view.verify_layout()


def test_header_redraw_keeps_controls_mapped_and_search_text(ui):
    root, view, _, search = ui
    search.set("one")
    controls = view.utility_buttons + view.header_buttons + [view.search_entry, view.toggle]
    for _ in range(30):
        view.paint_header()
        root.update()
        assert all(control.winfo_ismapped() for control in controls)
    assert view.search_entry.get() == "one"
    assert all(view.header.type(item) != "window" for item in view.header.find_all())


@pytest.fixture(scope="module")
def ui_root():
    gc.collect()
    root = tk.Tk()
    yield root
    root.destroy()
    gc.collect()


@pytest.fixture
def ui(ui_root):
    root = ui_root
    root.geometry("1100x768+15+15")
    events = []
    selected = [None]

    def select(account):
        selected[0] = account
        events.append(("select", account))

    callbacks = {name: (lambda n=name: events.append((n, selected[0])))
                 for name in ("add", "refresh_all", "refresh", "login", "bind", "forget", "export", "help", "save_key", "copy_key", "sync_keys", "create_key", "rebuild_key", "review_key_operation")}
    callbacks["select"] = select
    search, auto, status = tk.StringVar(root), tk.BooleanVar(root), tk.StringVar(root, "就绪")
    dashboard = Dashboard(root, search, auto, status, callbacks)
    dashboard.pack(fill="both", expand=True)
    dashboard.set_records([record("one"), record("two")], set(), "one")
    root.update()
    dashboard.repaint_cards()
    root.update()
    yield root, dashboard, events, search
    dashboard.destroy()
    root.unbind_all("<MouseWheel>")
    root.update_idletasks()
    gc.collect()


def open_actions(card):
    card.open_menu()
    root = card.winfo_toplevel()
    root.update()
    assert root.account_popup is not None
    return root.account_popup


@pytest.mark.parametrize("action", ["refresh", "login", "bind"])
def test_card_buttons_target_own_account_not_previous_selection(ui, action):
    _, view, events, _ = ui
    view.activate("two", action)
    assert events == [("select", "two"), (action, "two")]
    assert view.selected_id == "two"
    assert view.cards["two"].selected and not view.cards["one"].selected


def test_busy_card_disables_refresh_and_mutating_menu_actions(ui):
    root, view, _, _ = ui
    view.set_records([record("one"), record("two")], {"two"}, "two")
    root.update()
    card = view.cards["two"]
    assert not card.buttons[0].enabled
    popup = open_actions(card)
    for action in ("login", "bind", "forget", "save_key", "sync_keys", "create_key", "rebuild_key"):
        assert not popup.tiles[action].enabled
    assert popup.tiles["details"].enabled
    assert all(button.enabled for button in view.cards["one"].buttons[:2])


def test_search_and_empty_state_remove_hidden_account_actions(ui):
    root, view, _, search = ui
    search.set("two")
    view.repaint_cards()
    root.update()
    assert set(view.cards) == {"two"}
    assert "one" not in view.cards
    search.set("not-found")
    view.repaint_cards()
    root.update()
    assert not view.cards
    search.set("")
    view.repaint_cards()
    root.update()
    assert len(view.cards) == 2


def test_card_layout_contains_full_email_and_two_dates_at_minimum_size(ui):
    root, view, _, _ = ui
    root.geometry("960x680")
    root.update()
    view.repaint_cards()
    root.update()
    view.verify_layout()
    assert view.canvas.xview() == (0.0, 1.0)
    for card in view.cards.values():
        assert card.canvas.itemcget(card.email_item, "text") == card.record["email"]
        assert len(card.reset_items) == 2


def test_error_preserves_cached_quotas_and_shows_inline_warning(ui):
    root, view, _, _ = ui
    failed = record("one", status="查询失败 / 登录会话失效")
    before = copy.deepcopy(failed)
    view.set_records([failed], set(), "one")
    root.update()
    assert failed == before
    assert view.cards["one"].record["public"]["windows"][0]["percent"] == 28.3
    card = view.cards["one"]
    assert "上次成功数据保留" in card.canvas.itemcget(card.error_item, "text")
    assert card.height > card.HEIGHT


def test_empty_account_collection_is_valid(ui):
    root, view, _, _ = ui
    view.set_records([], set(), None)
    root.update()
    assert not view.cards
    view.verify_layout()


@pytest.mark.parametrize("width,expected", [(350, 1), (740, 1), (900, 2), (1180, 3), (1560, 4), (2100, 4)])
def test_grid_responsive_column_count(width, expected):
    assert grid_columns(width) == expected


@pytest.mark.parametrize("reset,expected", [
    ("2026-11-06T11:09:33Z", "3 小时 55 分后重置"),
    ("2026-11-12T01:09:33Z", "5 天 17 小时后重置"),
    ("2026-11-06T07:15:30Z", "1 分钟后重置"),
    ("2026-11-06T07:15:00Z", "已到重置时间，请刷新"),
    ("2026-11-05T07:15:00Z", "已到重置时间，请刷新"),
    (None, "重置时间未返回"),
    ("invalid", "重置时间格式未知"),
    ("2026-11-06T11:09:33", "重置时区未知"),
])
def test_countdown_from_real_reset_timestamp(reset, expected):
    now = datetime.fromisoformat("2026-11-06T15:15:00+08:00")
    assert reset_countdown(reset, now) == expected


def test_countdown_tick_never_changes_cached_balance_or_quota(ui):
    root, view, _, _ = ui
    before = copy.deepcopy(view.records)
    now = datetime.fromisoformat("2026-11-06T19:10:00+08:00")
    card = view.cards["one"]
    card.update_countdowns(now)
    root.update()
    assert "已到重置时间" in card.canvas.itemcget(card.countdown_items[0], "text")
    assert view.records == before
    assert card.record["public"]["windows"][0]["percent"] == 28.3


@pytest.mark.parametrize("action", ["login", "bind", "forget"])
def test_per_card_menu_actions_use_own_account(ui, action):
    _, view, events, _ = ui
    open_actions(view.cards["two"]).invoke(action)
    assert events == [("select", "two"), (action, "two")]


@pytest.mark.parametrize("geometry,columns", [("960x680", 2), ("1240x820", 3), ("1680x820", 4)])
def test_grid_layout_with_many_accounts_and_resize(ui, geometry, columns):
    root, view, _, _ = ui
    root.geometry(geometry)
    root.update()
    view.set_records([record(f"account{i}") for i in range(7)], set(), "account0")
    root.update()
    assert view.columns == columns
    view.verify_layout()
    for index, card in enumerate(view.cards.values()):
        assert int(card.grid_info()["row"]) == index // columns
        assert int(card.grid_info()["column"]) == index % columns
        assert card.winfo_x() + card.winfo_width() <= view.canvas.winfo_width()


def test_long_email_wraps_without_hiding_full_address(ui):
    root, view, _, _ = ui
    root.geometry("960x680")
    root.update()
    long = record("a-very-long-user-name-for-layout-qa-with-many-characters")
    view.set_records([long, record("two")], set(), long["id"])
    root.update()
    card = view.cards[long["id"]]
    assert card.canvas.itemcget(card.email_item, "text") == long["email"]
    assert card.height > card.HEIGHT
    view.verify_layout()


@pytest.mark.parametrize("expiry,expected", [
    ("2026-11-22T01:09:33Z", "订阅到期  2026-11-22 09:09"),
    ("2026-12-31T20:00:00Z", "订阅到期  2027-01-01 04:00"),
    (None, "订阅到期  未返回"),
])
def test_subscription_expiry_visible_on_card_without_menu(ui, expiry, expected):
    root, view, _, _ = ui
    first = record("one")
    first["public"]["expires_at"] = expiry
    root.geometry("960x680")
    root.update()
    view.set_records([first, record("two")], set(), "one")
    root.update()
    card = view.cards["one"]
    assert card.canvas.itemcget(card.expiry_item, "text") == expected
    assert len(card.reset_items) == 2
    view.verify_layout()


def test_key_button_masked_and_targets_own_account(ui):
    root, view, events, _ = ui
    second = record("two")
    second["has_api_key"] = True
    view.set_records([record("one"), second], set(), "one")
    root.update()
    card = view.cards["two"]
    assert card.buttons[2].enabled
    assert card.buttons[2].text == "Key  sk-******  复制"
    card.buttons[2].invoke()
    assert events == [("select", "two"), ("copy_key", "two")]
    assert not view.cards["one"].buttons[2].enabled
    root.update()
    view.verify_layout()


@pytest.mark.parametrize("action", ["save_key", "copy_key"])
def test_key_menu_targets_own_account_even_when_other_selected(ui, action):
    root, view, events, _ = ui
    second = record("two")
    second["has_api_key"] = True
    view.set_records([record("one"), second], set(), "one")
    root.update()
    open_actions(view.cards["two"]).invoke(action)
    assert events == [("select", "two"), (action, "two")]


def test_missing_key_not_shown_as_fake_copyable_key(ui):
    _, view, events, _ = ui
    card = view.cards["one"]
    assert card.buttons[2].text == "Key  尚未同步"
    assert not card.buttons[2].enabled
    popup = open_actions(card)
    assert not popup.tiles["copy_key"].enabled
    card.buttons[2].invoke()
    assert events == []


def test_existing_keys_not_confused_with_missing_local_full_value(ui, monkeypatch):
    root, view, events, _ = ui
    second = record("two")
    second["public"]["key_inventory"] = {"total": 3, "active": 2, "synced_at": "2026-11-06T10:00:00Z", "entries": []}
    view.set_records([record("one"), second], set(), "one")
    root.update()
    card = view.cards["two"]
    assert card.buttons[2].text == "Key  sk-******  仅前缀"
    assert card.buttons[2].enabled
    assert card.canvas.itemcget(card.key_summary_item, "text") == "官网 3 把 / 可用 2"
    assert not next(item for item in account_actions(second, False) if item and item[0] == "copy_key")[3]
    details = []
    monkeypatch.setattr(card, "show_key_details", lambda: details.append("shown"))
    card.buttons[2].invoke()
    assert events == [("select", "two")]
    assert details == ["shown"]
    root.update()
    view.verify_layout()


def test_key_sync_menu_uses_correct_account(ui):
    _, view, events, _ = ui
    open_actions(view.cards["two"]).invoke("sync_keys")
    assert events == [("select", "two"), ("sync_keys", "two")]


@pytest.mark.parametrize("action", ["create_key", "rebuild_key", "review_key_operation"])
def test_key_mutation_menu_targets_own_account(ui, action):
    root, view, events, _ = ui
    if action == "review_key_operation":
        second = record("two")
        second["public"]["key_operation"] = {"state": "needs_review"}
        view.set_records([record("one"), second], set(), "one")
        root.update()
    open_actions(view.cards["two"]).invoke(action)
    assert events == [("select", "two"), (action, "two")]


def test_key_sync_failure_keeps_count_but_shows_stale_warning(ui):
    root, view, _, _ = ui
    failed = record("one")
    failed["public"].update(key_inventory={"total": 1, "active": 1}, key_sync_error="Key 接口网络超时")
    view.set_records([failed], set(), "one")
    root.update()
    card = view.cards["one"]
    assert card.canvas.itemcget(card.key_summary_item, "text") == "同步失败 · 旧数据"
    assert card.record["public"]["key_inventory"]["total"] == 1
    view.verify_layout()


def test_action_popup_escape_closes_without_dispatch_or_leaving_grab(ui):
    root, view, events, _ = ui
    popup = open_actions(view.cards["two"])
    popup.event_generate("<Escape>")
    root.update()
    assert root.account_popup is None
    assert root.grab_current() is None
    assert events == []


def test_action_popup_outside_click_closes_without_dispatch(ui):
    from types import SimpleNamespace
    root, view, events, _ = ui
    popup = open_actions(view.cards["two"])
    popup.outside_click(SimpleNamespace(x_root=popup.winfo_rootx() - 5, y_root=popup.winfo_rooty()))
    assert root.account_popup is None and root.grab_current() is None
    assert events == []


def test_only_one_popup_open_and_refresh_closes_obsolete_actions(ui):
    root, view, events, _ = ui
    first = open_actions(view.cards["one"])
    second = open_actions(view.cards["two"])
    assert first.closed and not second.closed
    view.set_records([record("one")], set(), "one")
    root.update()
    assert second.closed and root.account_popup is None
    second.invoke("create_key")
    assert events == []


def test_action_popup_has_short_grouped_labels_and_full_account_identity(ui):
    _, view, _, _ = ui
    popup = open_actions(view.cards["two"])
    assert popup.email_label.cget("text") == "two@example.test"
    assert all(len(tile.text) <= 8 for tile in popup.tiles.values())
    assert popup.tiles["create_key"].variant == "soft"
    assert popup.tiles["rebuild_key"].variant == "danger"
    assert "review_key_operation" not in popup.tiles
    assert popup.winfo_height() < 520
    assert popup.winfo_width() == popup.WIDTH
    for tile in popup.tiles.values():
        assert tile.winfo_ismapped()
        for item in tile.find_all():
            if tile.type(item) == "text":
                box = tile.bbox(item)
                assert 0 <= box[0] and box[2] < tile.winfo_width()


def test_popup_keyboard_navigation_skips_disabled_items(ui):
    root, view, _, _ = ui
    view.set_records([record("two")], {"two"}, "two")
    root.update()
    popup = open_actions(view.cards["two"])
    assert popup.focus_get() == popup.tiles["details"]
    popup.move_focus(1)
    root.update()
    assert popup.focus_get() == popup.tiles["copy_email"]
    popup.move_focus(-1)
    root.update()
    assert popup.focus_get() == popup.tiles["details"]


def test_review_control_only_appears_when_needed_and_prevents_duplicate_mutations(ui):
    root, view, _, _ = ui
    second = record("two")
    second["public"]["key_operation"] = {"state": "needs_review"}
    view.set_records([second], set(), "two")
    root.update()
    popup = open_actions(view.cards["two"])
    assert popup.tiles["review_key_operation"].enabled
    assert not popup.tiles["create_key"].enabled
    assert not popup.tiles["rebuild_key"].enabled


def test_popup_long_email_wraps_and_root_resize_dismisses_it(ui):
    root, view, events, _ = ui
    second = record("a-very-long-account-identity-for-the-popup-layout")
    view.set_records([second], set(), second["id"])
    root.update()
    popup = open_actions(view.cards[second["id"]])
    assert popup.email_label.cget("text") == second["email"]
    assert popup.email_label.winfo_reqheight() > 20
    root.geometry("1020x720")
    root.update()
    assert popup.closed
    assert root.grab_current() is None
    assert events == []


def test_popup_focus_loss_dismisses_without_action(ui):
    root, view, events, _ = ui
    popup = open_actions(view.cards["two"])
    root.focus_force()
    root.update()
    assert popup.closed and root.account_popup is None
    assert events == []


def test_management_entry_real_press_release_opens_and_toggle_closes(ui):
    root, view, events, _ = ui
    card = view.cards["two"]
    button = card.buttons[1]
    assert button.text == "管理"
    button.event_generate("<ButtonPress-1>", x=24, y=16)
    button.event_generate("<ButtonRelease-1>", x=24, y=16)
    root.update()
    assert root.account_popup is not None
    assert root.account_popup.card is card
    assert root.account_popup.winfo_ismapped()
    button.invoke()
    root.update()
    assert root.account_popup is None and root.grab_current() is None
    assert events == []


def test_management_release_without_press_or_drag_out_does_not_open(ui):
    root, view, events, _ = ui
    button = view.cards["two"].buttons[1]
    button.event_generate("<ButtonRelease-1>", x=24, y=16)
    root.update()
    assert getattr(root, "account_popup", None) is None
    button.event_generate("<ButtonPress-1>", x=24, y=16)
    button.event_generate("<ButtonRelease-1>", x=button.winfo_width() + 10, y=16)
    root.update()
    assert getattr(root, "account_popup", None) is None
    assert events == []


def test_card_redraw_does_not_unmap_management_or_other_buttons(ui):
    root, view, _, _ = ui
    card = view.cards["two"]
    for _ in range(10):
        card.paint()
        assert all(button.winfo_ismapped() for button in card.buttons)
    root.update()
    view.verify_layout()


def test_regular_refresh_reuses_card_and_open_panel_while_updating_actions(ui):
    root, view, events, _ = ui
    card = view.cards["two"]
    popup = open_actions(card)
    updated = record("two")
    updated["public"]["balance_usd"] = "2.50"
    updated["has_api_key"] = True
    view.set_records([record("one"), updated], set(), "two")
    root.update()
    assert view.cards["two"] is card
    assert not popup.closed and root.account_popup is popup
    assert popup.tiles["copy_key"].enabled
    assert card.record["public"]["balance_usd"] == "2.50"
    view.set_records([record("one"), updated], {"two"}, "two")
    root.update()
    assert not popup.closed and not popup.tiles["rebuild_key"].enabled
    popup.invoke("rebuild_key")
    assert events == []


@pytest.mark.parametrize("geometry", ["960x680", "1240x820", "1680x820"])
def test_management_entry_visible_with_long_email_on_every_column(ui, geometry):
    root, view, _, _ = ui
    root.geometry(geometry)
    root.update()
    records = [record("long-alias-example"), record("a-long-user-email-address-for-qa"), record("third"), record("fourth")]
    view.set_records(records, set(), records[0]["id"])
    root.update()
    view.verify_layout()
    for card in view.cards.values():
        button = card.buttons[1]
        assert button.winfo_ismapped()
        assert button.winfo_x() + button.winfo_width() <= card.canvas.winfo_width() - 16


@pytest.mark.parametrize("anchor,bounds", [
    ((1900, 1020, 1920, 1048), (0, 0, 1920, 1080)),
    ((5, 5, 33, 33), (0, 0, 1920, 1080)),
    ((-1870, 50, -1840, 78), (-1920, 0, 0, 1080)),
])
def test_popup_position_stays_inside_workarea_even_at_edges(anchor, bounds):
    x, y = popup_position(anchor, (320, 470), bounds)
    assert bounds[0] <= x and x + 320 <= bounds[2]
    assert bounds[1] <= y and y + 470 <= bounds[3]
