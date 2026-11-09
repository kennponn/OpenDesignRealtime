"""Native desktop presentation. No web runtime, credentials or network access."""

import math
import tkinter as tk
from datetime import datetime
from decimal import Decimal, InvalidOperation
from tkinter import font as tkfont

from secure_store import TZ


BG = "#F5F7F8"
SURFACE = "#FFFFFF"
INK = "#1C2C30"
MUTED = "#66777C"
LINE = "#E4EAEC"
GREEN = "#0CB883"
GREEN_SOFT = "#E8F4EF"
RED = "#AD4B3D"
RED_SOFT = "#FCF0EB"
FONT = "Microsoft YaHei UI"
NUM = "Bahnschrift"
ICON = "Segoe MDL2 Assets"
TIERS = {"free": "Free", "go": "Go", "plus": "Go Plus", "pro": "Go Pro", "max": "Go Max"}


def date_text(value):
    if not value:
        return "未返回"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "格式未知"


def wallet_text(value):
    if value is None:
        return "未查询"
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return "未返回"
        return f"${number:,.2f}"
    except InvalidOperation:
        return "未返回"


def state_of(record, busy=False):
    if busy:
        return "正在查询", GREEN, GREEN_SOFT
    if any(word in record["status"] for word in ("失败", "失效", "需重新", "不一致", "未登录", "未完成")):
        return "需处理", RED, RED_SOFT
    if not record["has_credentials"]:
        return "待登录", MUTED, BG
    return "已连接", GREEN, GREEN_SOFT


def quota_data(data, kind):
    window = next((item for item in (data.get("windows") or []) if item.get("kind") == kind), None)
    if not window:
        return {"value": "未返回", "percent": None, "reset": "未返回"}
    percent = window.get("percent")
    if not isinstance(percent, (int, float)) or not math.isfinite(percent):
        percent = None
    return {"value": f"{percent:g}%" if percent is not None else "未返回",
            "percent": percent, "reset": date_text(window.get("resets_at"))}


def reset_countdown(value, now=None):
    if not value:
        return "重置时间未返回"
    try:
        reset = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if reset.tzinfo is None:
            return "重置时区未知"
        minutes = math.ceil((reset - (now or datetime.now(TZ))).total_seconds() / 60)
    except (TypeError, ValueError):
        return "重置时间格式未知"
    if minutes <= 0:
        return "已到重置时间，请刷新"
    days, hours = divmod(minutes // 60, 24)
    if days:
        return f"{days} 天 {hours} 小时后重置"
    if hours:
        return f"{hours} 小时 {minutes % 60} 分后重置"
    return f"{minutes} 分钟后重置"


def grid_columns(width):
    return max(1, min(4, (max(1, int(width)) + 14) // 380))


def key_button_text(record):
    if record.get("has_api_key"):
        return "Key  sk-******  复制"
    inventory = record["public"].get("key_inventory")
    if inventory and inventory.get("total", 0) > 0:
        return "Key  sk-******  仅前缀"
    if inventory:
        return "Key  官网暂无"
    return "Key  尚未同步"


def key_summary(record):
    data = record["public"]
    if data.get("key_operation", {}).get("state") in ("create_pending", "revoke_pending", "needs_review"):
        return "Key 操作需核对"
    inventory = data.get("key_inventory")
    if data.get("key_sync_error"):
        return "同步失败 · 旧数据" if inventory else "同步失败"
    if inventory:
        return f"官网 {inventory['total']} 把 / 可用 {inventory['active']}"
    return "本机已保存" if record.get("has_api_key") else ""


def rounded(canvas, x1, y1, x2, y2, radius=12, **kwargs):
    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
              x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
              x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=28, **kwargs)


class Button(tk.Canvas):
    def __init__(self, parent, text, command, variant="plain", icon=None, width=120, height=38, bg=BG, pill=False):
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0,
                         bd=0, cursor="hand2", takefocus=True)
        self.text, self.command, self.variant, self.icon = text, command, variant, icon
        self.pill = pill
        self.hover = False
        self.enabled = True
        self.pressed = False
        self.bind("<Enter>", lambda _: self.set_hover(True))
        self.bind("<Leave>", lambda _: self.set_hover(False))
        self.bind("<Configure>", lambda _: self.paint())
        self.bind("<FocusIn>", lambda _: self.paint())
        self.bind("<FocusOut>", lambda _: self.paint())
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<Return>", self.invoke)
        self.bind("<space>", self.invoke)

    def set_hover(self, value):
        self.hover = value
        self.paint()

    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.pressed = False
        self.configure(cursor="hand2" if enabled else "arrow", takefocus=enabled)
        self.paint()

    def press(self, event):
        self.pressed = self.enabled
        return "break"

    def release(self, event):
        pressed, self.pressed = self.pressed, False
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            return self.invoke()
        return "break"

    def invoke(self, event=None):
        if self.enabled:
            self.command()
        return "break"

    def paint(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if self.variant == "primary":
            fill, foreground, border = ("#089A6D" if self.hover else "#109F75"), "white", "#109F75"
        elif self.variant == "soft":
            fill, foreground, border = ("#DDEEE7" if self.hover else GREEN_SOFT), GREEN, GREEN_SOFT
        else:
            fill, foreground, border = (BG if self.hover else SURFACE), INK, LINE
        if not self.enabled:
            fill, foreground, border = BG, "#A4AFB2", LINE
        if self.focus_get() == self:
            border = GREEN
        rounded(self, 1, 1, w - 1, h - 1, h / 2 if self.pill else 9, fill=fill, outline=border, width=1)
        text_width = tkfont.Font(family=FONT, size=9).measure(self.text)
        start = (w - text_width - (23 if self.icon else 0)) / 2
        if self.icon:
            self.create_text(start + 6, h / 2, text=self.icon, font=(ICON, 11), fill=foreground)
            start += 23
        self.create_text(start, h / 2, text=self.text, font=(FONT, 9), fill=foreground, anchor="w")


def account_actions(record, busy):
    bound = record["has_credentials"]
    ready = bound and not busy
    needs_review = record["public"].get("key_operation", {}).get("state") in (
        "create_pending", "revoke_pending", "needs_review")
    return [
        ("login", "登录账号", "\uE77B", not busy, "plain", "account"),
        ("bind", "保存会话", "\uE74E", not busy, "plain", "account"),
        ("details", "账号详情", "\uE946", True, "plain", "account"),
        ("copy_email", "复制邮箱", "\uE8C8", True, "plain", "account"),
        ("copy_key", "复制 Key", "\uE8C8", bool(record.get("has_api_key")), "plain", "key"),
        ("save_key", "导入 Key", "\uE898", ready, "plain", "key"),
        ("sync_keys", "同步 Key", "\uE72C", ready, "plain", "key"),
        ("key_details", "Key 状态", "\uE9D9", True, "plain", "key"),
        ("create_key", "新建 Key", "\uE710", ready and not needs_review, "soft", "key"),
        ("rebuild_key", "重建 Key", "\uE777", ready and not needs_review, "danger", "key"),
        ("review_key_operation", "核对上次操作", "\uE7BA", ready, "warning", "more") if needs_review else None,
        ("forget", "解除本地绑定", "\uE785", not busy, "danger", "more"),
    ]


def popup_position(anchor, size, bounds):
    left, top, right, bottom = bounds
    x1, y1, x2, y2 = anchor
    width, height = size
    x = max(left + 8, min(x2 - width, right - width - 8))
    y = y2 + 6 if y2 + 6 + height <= bottom - 8 else y1 - height - 6
    return x, max(top + 8, min(y, bottom - height - 8))


def screen_workarea(root, x, y):
    if root.tk.call("tk", "windowingsystem") == "win32":
        import ctypes
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

        user = ctypes.WinDLL("user32")
        user.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user.MonitorFromPoint.restype = wintypes.HANDLE
        user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        monitor = user.MonitorFromPoint(wintypes.POINT(x, y), 2)
        if user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return info.work.left, info.work.top, info.work.right, info.work.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


class ActionTile(Button):
    def paint(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        color = RED if self.variant in ("danger", "warning") else INK
        fill = RED_SOFT if self.variant in ("danger", "warning") else SURFACE
        border = LINE if self.variant == "plain" else fill
        if self.variant == "soft":
            color, fill, border = "#087B5C", GREEN_SOFT, GREEN_SOFT
        if self.hover:
            fill = "#F7E5DE" if self.variant in ("danger", "warning") else "#E1F1E9"
        if not self.enabled:
            color, fill, border = "#A7B1B4", "#F8FAFA", "#EFF2F3"
        if self.focus_get() == self:
            border = RED if self.variant in ("danger", "warning") else GREEN
        rounded(self, 1, 1, w - 1, h - 1, 8, fill=fill, outline=border)
        self.create_text(17, h / 2, text=self.icon, font=(ICON, 10), fill=color)
        self.create_text(33, h / 2, text=self.text, font=(FONT, 9), fill=color, anchor="w")


class AccountActions(tk.Toplevel):
    WIDTH = 320

    def __init__(self, card, anchor):
        self.root = card.winfo_toplevel()
        previous = getattr(self.root, "account_popup", None)
        if previous:
            previous.close(restore_focus=False)
        super().__init__(self.root, bg=BG)
        self.card, self.anchor = card, anchor
        self.actions, self.tiles = {}, {}
        self.closed = False
        self.root_configure_binding = None
        self.withdraw()
        self.overrideredirect(True)
        self.transient(self.root)
        self.root.account_popup = self
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.panel = tk.Frame(self.canvas, bg=SURFACE)
        self.canvas.create_window(12, 10, window=self.panel, anchor="nw", width=self.WIDTH - 24)
        heading = tk.Frame(self.panel, bg=SURFACE)
        heading.pack(fill="x", padx=7, pady=(6, 9))
        tk.Label(heading, text="账号操作", font=(FONT, 12, "bold"), bg=SURFACE, fg=INK).pack(anchor="w")
        self.email_label = tk.Label(heading, text=card.record["email"], font=(FONT, 8), bg=SURFACE,
                                    fg=MUTED, wraplength=self.WIDTH - 40, justify="left", anchor="w")
        self.email_label.pack(fill="x", pady=(3, 0))
        definitions = [item for item in account_actions(card.record, card.busy) if item]
        for group, label in (("account", "账号"), ("key", "API KEY"), ("more", "更多")):
            section = tk.Frame(self.panel, bg=SURFACE)
            section.pack(fill="x", padx=6, pady=(0, 7))
            title = tk.Frame(section, bg=SURFACE)
            title.pack(fill="x", pady=(0, 5))
            tk.Label(title, text=label, bg=SURFACE, fg=MUTED, font=(FONT, 8, "bold")).pack(side="left")
            if group == "key":
                inventory = card.record["public"].get("key_inventory")
                meta = "已保存完整值" if card.record.get("has_api_key") else "仅有前缀"
                if not inventory and not card.record.get("has_api_key"):
                    meta = "尚未同步"
                tk.Label(title, text=meta, bg=SURFACE, fg="#8A969B", font=(FONT, 8)).pack(side="right")
                self.key_meta = title.winfo_children()[-1]
            grid = tk.Frame(section, bg=SURFACE)
            grid.pack(fill="x")
            grid.columnconfigure((0, 1), weight=1, uniform="actions")
            for index, (action, text, icon, enabled, variant, _) in enumerate(item for item in definitions if item[5] == group):
                tile = ActionTile(grid, text, lambda a=action: self.invoke(a), variant=variant,
                                  icon=icon, width=138, height=34, bg=SURFACE)
                tile.set_enabled(enabled)
                column = index % 2
                tile.grid(row=index // 2, column=column, sticky="ew", padx=(0, 6) if column == 0 else (0, 0), pady=(0, 5))
                if group == "more":
                    tile.grid_configure(row=index, column=0, columnspan=2, padx=0)
                self.tiles[action] = tile
                self.actions[action] = enabled
        tk.Frame(self.panel, bg=LINE, height=1).pack(fill="x", padx=7)
        tk.Label(self.panel, text="重建将撤销所选旧 Key，操作前会确认", font=(FONT, 8),
                 bg=SURFACE, fg=MUTED).pack(anchor="w", padx=7, pady=(9, 8))
        self.update_idletasks()
        self.height = self.panel.winfo_reqheight() + 22
        self.canvas.configure(width=self.WIDTH, height=self.height)
        rounded(self.canvas, 5, 7, self.WIDTH - 3, self.height - 1, 14, fill="#DFE7E6", outline="")
        rounded(self.canvas, 1, 1, self.WIDTH - 7, self.height - 6, 13, fill=SURFACE, outline="#DAE4E1")
        self.canvas.tag_lower("all")
        work = screen_workarea(self.root, anchor[0], anchor[1])
        x, y = popup_position(anchor, (self.WIDTH, self.height), work)
        self.geometry(f"{self.WIDTH}x{self.height}{x:+d}{y:+d}")
        self.bind("<Escape>", lambda _: self.close())
        # Keep the grab until release so dismissing cannot click a background action.
        self.bind("<ButtonRelease-1>", self.outside_click)
        self.bind("<FocusOut>", lambda _: self.root.after_idle(self.check_focus))
        self.bind("<Down>", lambda _: self.move_focus(1))
        self.bind("<Up>", lambda _: self.move_focus(-1))
        self.root_configure_binding = self.root.bind(
            "<Configure>", lambda event: self.close(restore_focus=False) if event.widget is self.root else None, add="+")
        self.deiconify()
        self.lift()
        self.grab_set()
        enabled = [tile for tile in self.tiles.values() if tile.enabled]
        if enabled:
            enabled[0].focus_force()

    def refresh_record(self):
        definitions = [item for item in account_actions(self.card.record, self.card.busy) if item]
        if {item[0] for item in definitions} != set(self.actions):
            self.close(restore_focus=False)
            return
        for action, _, _, enabled, _, _ in definitions:
            self.actions[action] = enabled
            self.tiles[action].set_enabled(enabled)
        record = self.card.record
        meta = "已保存完整值" if record.get("has_api_key") else "仅有前缀"
        if not record["public"].get("key_inventory") and not record.get("has_api_key"):
            meta = "尚未同步"
        self.key_meta.configure(text=meta)

    def invoke(self, action):
        if not self.actions.get(action) or self.closed:
            return
        card = self.card
        self.close(restore_focus=False)
        if action == "details":
            card.show_details()
        elif action == "copy_email":
            card.copy_email()
        elif action == "key_details":
            card.show_key_details()
        else:
            card.activate(card.record["id"], action)

    def move_focus(self, direction):
        tiles = [tile for tile in self.tiles.values() if tile.enabled]
        if tiles:
            focused = self.focus_get()
            index = tiles.index(focused) if focused in tiles else -1
            tiles[(index + direction) % len(tiles)].focus_set()
        return "break"

    def outside_click(self, event):
        if not (self.winfo_rootx() <= event.x_root < self.winfo_rootx() + self.WIDTH
                and self.winfo_rooty() <= event.y_root < self.winfo_rooty() + self.height):
            self.close()
            return "break"

    def check_focus(self):
        if not self.closed:
            focused = self.focus_get()
            if not focused or focused.winfo_toplevel() != self:
                self.close(restore_focus=False)

    def close(self, restore_focus=True):
        if self.closed:
            return
        self.closed = True
        if self.root_configure_binding:
            self.root.unbind("<Configure>", self.root_configure_binding)
            self.root_configure_binding = None
        if self.root.account_popup is self:
            self.root.account_popup = None
        if self.grab_current() == self:
            self.grab_release()
        self.destroy()
        if restore_focus and self.card.winfo_exists():
            self.card.buttons[1].focus_set()


class AccountCard(tk.Frame):
    HEIGHT = 278
    KEY_ROW = 24
    MANAGEMENT_WIDTH = 64
    EMAIL_RIGHT = 98

    def __init__(self, parent, record, busy, selected, activate):
        super().__init__(parent, bg=BG, height=self.HEIGHT, takefocus=True)
        self.record, self.busy, self.selected, self.activate = record, busy, selected, activate
        self.canvas = tk.Canvas(self, width=1, bg=BG, highlightthickness=0, bd=0, height=self.HEIGHT)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _: self.paint())
        self.canvas.bind("<Button-1>", lambda _: self.activate(self.record["id"], None))
        self.canvas.bind("<Button-3>", self.open_menu)
        self.bind("<Return>", lambda _: self.activate(self.record["id"], None))
        self.bind("<Shift-F10>", self.open_menu)
        self.buttons = [
            Button(self.canvas, "查询中" if busy else "刷新额度", lambda: activate(self.record["id"], "refresh"),
                   icon="\uE72C", width=110, height=30, bg=SURFACE, pill=True),
            Button(self.canvas, "管理", self.open_menu, width=self.MANAGEMENT_WIDTH, height=32,
                   bg=SURFACE, variant="soft"),
            Button(self.canvas, key_button_text(record), self.key_action, variant="soft", icon="\uE8C8",
                   width=178, height=24, bg=SURFACE, pill=True),
        ]
        self.buttons[0].set_enabled(not busy and record["has_credentials"])
        self.buttons[2].set_enabled(bool(record.get("has_api_key") or record["public"].get("key_inventory")))
        self.email_item = None
        self.expiry_item = None
        self.key_summary_item = None
        self.tier_item = None
        self.wallet_item = None
        self.reset_items, self.countdown_items = [], []
        self.error_item = None
        self.email_size = 9
        self.height = self.HEIGHT

    def set_record(self, record, busy, selected):
        self.record, self.busy, self.selected = record, busy, selected
        self.buttons[0].text = "查询中" if busy else "刷新额度"
        self.buttons[0].set_enabled(not busy and record["has_credentials"])
        self.buttons[2].text = key_button_text(record)
        self.buttons[2].set_enabled(bool(record.get("has_api_key") or record["public"].get("key_inventory")))
        popup = getattr(self.winfo_toplevel(), "account_popup", None)
        if popup and popup.card is self:
            popup.refresh_record()
        self.paint()

    def text(self, x, y, text, size=9, color=INK, family=FONT, weight="normal", **kwargs):
        kwargs.setdefault("anchor", "w")
        return self.canvas.create_text(x, y, text=text, font=(family, size, weight), fill=color, **kwargs)

    def copy_email(self):
        self.clipboard_clear()
        self.clipboard_append(self.record["email"])

    def key_action(self):
        if self.record.get("has_api_key"):
            self.activate(self.record["id"], "copy_key")
        else:
            self.activate(self.record["id"], None)
            self.show_key_details()

    def show_key_details(self):
        from tkinter import messagebox
        inventory = self.record["public"].get("key_inventory")
        lines = [self.record["email"], ""]
        if inventory:
            lines.extend([f"官网已有 {inventory['total']} 把 Key，其中 {inventory['active']} 把可用。",
                          "列表同步：" + date_text(inventory.get("synced_at"))])
            for index, entry in enumerate(inventory.get("entries", []), 1):
                state = {"active": "启用", "paused": "暂停", "revoked": "撤销"}.get(entry["status"], "未知")
                if entry.get("expired"):
                    state += " / 已过期"
                lines.append(f"{index}. {entry['name']} · {state} · sk-******")
        else:
            lines.append("尚未成功同步官网 Key 列表，请使用操作面板「同步 Key」。")
        error = self.record["public"].get("key_sync_error")
        if error:
            lines.extend(["", "本次同步失败：" + error, "上次同步数据保留，不代表当前状态。"])
        lines.extend(["", "本机完整 Key：" + ("已加密保存，可点击复制。" if self.record.get("has_api_key") else "尚未保存。"),
                      "官网列表只返回前缀，不能用于调用接口；完整 Key 仅创建时显示一次。",
                      "可导入原完整值，或从操作面板确认「新建 Key」。",
                      "重建只在确认后执行：新 Key 保存成功后撤销所选旧 Key。"])
        messagebox.showinfo("已有 Key 状态", "\n".join(lines), parent=self.winfo_toplevel())

    def open_menu(self, event=None):
        root = self.winfo_toplevel()
        previous = getattr(root, "account_popup", None)
        if previous and previous.card is self:
            previous.close()
            return "break"
        if event and getattr(event, "num", None) == 3:
            anchor = (event.x_root, event.y_root, event.x_root, event.y_root)
        else:
            button = self.buttons[1]
            x, y = button.winfo_rootx(), button.winfo_rooty()
            anchor = (x, y, x + button.winfo_width(), y + button.winfo_height())
        AccountActions(self, anchor)
        return "break"

    def destroy(self):
        popup = getattr(self.winfo_toplevel(), "account_popup", None)
        if popup and popup.card is self:
            popup.close(restore_focus=False)
        super().destroy()

    def show_details(self):
        from tkinter import messagebox
        data = self.record["public"]
        lines = [self.record["email"], "",
                 "备注：" + (self.record.get("label") or "无"),
                 "套餐：" + TIERS.get(data.get("tier"), data.get("tier") or "未查询"),
                 "订阅结束：" + date_text(data.get("expires_at")),
                 "按量余额：" + wallet_text(data.get("balance_usd"))]
        for kind, label in (("5h", "5 小时"), ("7d", "7 天")):
            value = quota_data(data, kind)
            lines.append(f"{label}剩余：{value['value']} · 重置：{value['reset']}")
        lines.extend(["最后成功：" + date_text(self.record.get("last_success")),
                      "查询结果：" + self.record["status"], "", "日期和时间均为北京时间。"])
        messagebox.showinfo("账号详情", "\n".join(lines), parent=self.winfo_toplevel())

    def paint(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width()
        if w < 310:
            return
        # Fit typical email addresses on one line; very long ones wrap at full width.
        email_font = tkfont.Font(family=FONT, size=9, weight="bold")
        self.email_size = 9
        email_width = w - 18 - self.EMAIL_RIGHT
        if email_font.measure(self.record["email"]) > email_width:
            self.email_size = 8
            email_font.configure(size=8)
        wrapped = email_font.measure(self.record["email"]) > email_width
        email_lines = max(1, math.ceil(email_font.measure(self.record["email"]) / email_width))
        offset = (email_lines - 1) * 18 if wrapped else 0
        self.offset = offset
        state, color, soft = state_of(self.record, self.busy)
        failed = state == "需处理"
        self.height = self.HEIGHT + offset + (38 if failed else 0)
        self.canvas.configure(height=self.height)
        rounded(c, 1, 2, w - 1, self.height - 2, 12, fill=SURFACE,
                outline="#A6DCCD" if self.selected else "#DFE5E6", width=1)
        self.email_item = self.text(18, 29, self.record["email"], self.email_size,
                                    weight="bold", width=email_width, anchor="nw")
        c.move(self.email_item, 0, -8)
        # Place controls independently of canvas redraws; delete("all") must not unmap them.
        self.buttons[1].place(relx=1, x=-18, y=16, anchor="ne", width=self.MANAGEMENT_WIDTH, height=32)
        data = self.record["public"]
        tier = TIERS.get(data.get("tier"), data.get("tier") or "未查询")
        self.text(18, 61 + offset, "套餐", 9, MUTED)
        self.tier_item = self.text(54, 61 + offset, tier, 9, INK, weight="bold")
        wallet_x = c.bbox(self.tier_item)[2] + 26
        c.create_line(wallet_x - 13, 55 + offset, wallet_x - 13, 67 + offset, fill=LINE)
        self.wallet_item = self.text(wallet_x, 61 + offset,
                                     "余额 " + wallet_text(data.get("balance_usd")), 9, MUTED)
        self.expiry_item = self.text(18, 82 + offset,
                                     "订阅到期  " + date_text(data.get("expires_at")), 9, MUTED)
        self.buttons[2].place(x=18, y=94 + offset, width=178, height=24)
        self.key_summary_item = self.text(w - 18, 106 + offset, key_summary(self.record), 8,
                                          RED if data.get("key_sync_error") or data.get("key_operation", {}).get("state") == "needs_review" else MUTED, anchor="e")
        self.reset_items, self.countdown_items = [], []
        for index, (kind, title) in enumerate((("5h", "5 小时额度"), ("7d", "7 天额度"))):
            y = 106 + self.KEY_ROW + offset + index * 57
            value = quota_data(data, kind)
            self.text(18, y, title, 9, MUTED, weight="bold")
            value_color = RED if value["percent"] is not None and value["percent"] <= 15 else INK
            self.text(w - 18, y, "剩余 " + value["value"], 9, value_color, weight="bold", anchor="e")
            rounded(c, 18, y + 12, w - 18, y + 18, 3, fill="#EDF1F2", outline="")
            if value["percent"] is not None and value["percent"] > 0:
                length = max(4, (w - 36) * min(100, value["percent"]) / 100)
                rounded(c, 18, y + 12, 18 + length, y + 18, 3,
                        fill=GREEN if value["percent"] > 15 else RED, outline="")
            self.reset_items.append(self.text(18, y + 32, "重置 " + value["reset"], 8, "#8A969B"))
            self.countdown_items.append(self.text(w - 18, y + 32, "", 8, "#8A969B", anchor="e"))
        bottom = 218 + self.KEY_ROW + offset
        if failed:
            self.error_item = self.text(18, bottom + 7, "上次成功数据保留 · " + self.record["status"],
                                        8, RED, width=w - 36, anchor="w")
            bottom += 38
        else:
            self.error_item = None
        c.create_oval(19, bottom + 3, 24, bottom + 8, fill=color, outline="")
        self.text(31, bottom + 6, state, 8, color)
        updated = date_text(self.record.get("last_success"))
        if self.record.get("last_success"):
            is_today = updated[:10] == datetime.now(TZ).strftime("%Y-%m-%d")
            success_text = "更新 " + (updated[-5:] if is_today else updated[5:])
        else:
            success_text = "尚未查询"
        self.text(96, bottom + 6, success_text, 8, "#8A969B")
        self.buttons[0].place(relx=1, x=-18, y=bottom - 8, anchor="ne", width=110, height=30)
        self.update_countdowns()

    def update_countdowns(self, now=None):
        data = self.record["public"]
        for index, kind in enumerate(("5h", "7d")):
            window = next((item for item in (data.get("windows") or []) if item.get("kind") == kind), {})
            if index >= len(self.countdown_items):
                continue
            item = self.countdown_items[index]
            self.canvas.coords(item, self.canvas.winfo_width() - 18,
                               138 + self.KEY_ROW + self.offset + index * 57)
            self.canvas.itemconfigure(item, anchor="e", font=(FONT, 8))
            self.canvas.itemconfigure(item, text=reset_countdown(window.get("resets_at"), now))
            left = self.canvas.bbox(self.reset_items[index])
            right = self.canvas.bbox(item)
            if left and right and right[0] < left[2] + 8:
                self.canvas.itemconfigure(item, font=(FONT, 7))



class Dashboard(tk.Frame):
    def __init__(self, parent, search, auto, status, callbacks):
        super().__init__(parent, bg=BG)
        self.search, self.auto, self.status, self.callbacks = search, auto, status, callbacks
        self.records, self.jobs, self.selected_id = [], set(), None
        self.cards = {}
        self.redraw_pending = None
        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True)
        self.header = tk.Canvas(main, bg=BG, height=141, highlightthickness=0)
        self.header.pack(fill="x", padx=24)
        self.header.bind("<Configure>", lambda _: self.paint_header())
        self.header_buttons = [
            Button(self.header, "刷新全部", callbacks["refresh_all"], icon="\uE72C", width=106),
            Button(self.header, "添加账号", callbacks["add"], variant="primary", icon="\uE710", width=106),
        ]
        self.utility_buttons = [
            Button(self.header, "导出", callbacks["export"], icon="\uE896", width=76),
            Button(self.header, "帮助", callbacks["help"], icon="\uE946", width=76),
        ]
        self.search_entry = tk.Entry(self.header, textvariable=search, relief="flat", bd=0, bg=SURFACE,
                                     fg=INK, insertbackground=GREEN, font=(FONT, 9))
        self.search_entry.bind("<Escape>", lambda _: search.set(""))
        self.search_hint = tk.Label(self.search_entry, text="搜索邮箱或备注", bg=SURFACE, fg="#93A0A4", font=(FONT, 9))
        self.search_hint.bind("<Button-1>", lambda _: self.search_entry.focus_set())
        self.search_entry.bind("<FocusIn>", lambda _: self.search_hint.place_forget())
        self.search_entry.bind("<FocusOut>", lambda _: self.paint_header())
        self.toggle = tk.Checkbutton(self.header, text="每 60 秒刷新", variable=auto, bg=BG, fg=MUTED,
                                     activebackground=BG, selectcolor=SURFACE, font=(FONT, 9), bd=0,
                                     highlightthickness=0, cursor="hand2")
        self.content = tk.Frame(main, bg=BG)
        self.content.pack(fill="both", expand=True, padx=(24, 16))
        self.canvas = tk.Canvas(self.content, bg=BG, bd=0, highlightthickness=0, yscrollincrement=20)
        self.canvas.pack(side="left", fill="both", expand=True)
        from tkinter import ttk
        self.scrollbar = ttk.Scrollbar(self.content, orient="vertical", command=self.canvas.yview)
        self.scrollbar.pack(side="right", fill="y", padx=(6, 0))
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.cards_frame = tk.Frame(self.canvas, bg=BG)
        self.body_window = self.canvas.create_window(0, 0, window=self.cards_frame, anchor="nw")
        self.canvas.bind("<Configure>", self.resize_body)
        self.cards_frame.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        # Route wheel input only while the pointer is inside the account list.
        parent.bind_all("<MouseWheel>", self.mousewheel, add="+")
        self.footer = tk.Canvas(main, bg=BG, height=34, highlightthickness=0)
        self.footer.pack(fill="x", padx=24)
        self.footer.bind("<Configure>", lambda _: self.paint_footer())
        status.trace_add("write", lambda *_: self.paint_footer())
        self.countdown_timer = self.after(30_000, self.tick_countdowns)

    def paint_header(self):
        c = self.header
        c.delete("all")
        w = c.winfo_width()
        if w < 500:
            return
        c.create_text(1, 37, text="OpenDesign 账号", font=(FONT, 16, "bold"), fill=INK, anchor="w")
        right = w
        for button, width in reversed(list(zip(self.utility_buttons + self.header_buttons, (76, 76, 106, 106)))):
            right -= width
            button.place(x=right, y=23, width=width, height=34)
            right -= 8
        states = [state_of(record, record["id"] in self.jobs)[0] for record in self.records]
        connected = sum(state == "已连接" for state in states)
        attention = sum(state in ("需处理", "待登录") for state in states)
        meta = f"全部 {len(self.records):02d}    ·    已连接 {connected:02d}    ·    需处理 {attention:02d}"
        c.create_text(1, 76, text=meta, font=(FONT, 9), fill=MUTED, anchor="w")
        c.create_text(w, 76, text="登录一次，独立保存会话", font=(FONT, 9), fill="#8A969B", anchor="e")
        search_width = min(340, w - 200)
        rounded(c, 1, 98, search_width, 133, 8, fill=SURFACE, outline=LINE)
        c.create_text(18, 115, text="\uE721", font=(ICON, 11), fill=MUTED)
        self.search_entry.place(x=34, y=105, width=search_width - 48, height=20)
        if not self.search.get() and self.search_entry.focus_get() != self.search_entry:
            self.search_hint.place(x=0, y=-2)
        else:
            self.search_hint.place_forget()
        self.toggle.place(x=w, y=105, anchor="ne")

    def paint_footer(self):
        c = self.footer
        c.delete("all")
        w = c.winfo_width()
        c.create_line(0, 3, w, 3, fill=LINE)
        c.create_text(w, 23, text="所有时间均为北京时间", font=(FONT, 8), fill=MUTED, anchor="e")
        text = self.status.get()
        f = tkfont.Font(family=FONT, size=8)
        limit = max(140, w - 160)
        while f.measure(text) > limit and len(text) > 2:
            text = text[:-2] + "…"
        c.create_text(0, 23, text=text, font=(FONT, 8), fill=MUTED, anchor="w")

    def resize_body(self, event):
        self.canvas.itemconfigure(self.body_window, width=event.width)
        if self.redraw_pending:
            self.after_cancel(self.redraw_pending)
            self.redraw_pending = None
        if grid_columns(event.width) == getattr(self, "columns", None):
            return
        self.redraw_pending = self.after(80, self.repaint_cards)

    def mousewheel(self, event):
        if getattr(self.winfo_toplevel(), "account_popup", None):
            return "break"
        x, y = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
        if x <= event.x_root <= x + self.canvas.winfo_width() and y <= event.y_root <= y + self.canvas.winfo_height():
            if self.cards_frame.winfo_height() > self.canvas.winfo_height():
                self.canvas.yview_scroll(-int(event.delta / 120), "units")
                return "break"

    def show_all(self):
        self.search.set("")
        self.repaint_cards()

    def set_records(self, records, jobs, selected_id=None):
        self.records, self.jobs, self.selected_id = records, set(jobs), selected_id
        self.paint_header()
        self.repaint_cards()

    def activate(self, account_id, action):
        # Bind every per-card action to its own ID, never to a stale previous selection.
        self.selected_id = account_id
        self.callbacks["select"](account_id)
        for card in self.cards.values():
            card.selected = card.record["id"] == account_id
            card.paint()
        if action:
            self.callbacks[action]()

    def repaint_cards(self):
        if self.redraw_pending:
            self.after_cancel(self.redraw_pending)
        self.redraw_pending = None
        old_offset = self.canvas.yview()[0]
        needle = self.search.get().strip().casefold()
        visible = [r for r in self.records if not needle or needle in f"{r['email']} {r['label']}".casefold()]
        identifiers = {record["id"] for record in visible}
        for account_id in list(self.cards):
            if account_id not in identifiers:
                self.cards.pop(account_id).destroy()
        for child in self.cards_frame.winfo_children():
            if not isinstance(child, AccountCard):
                child.destroy()
        self.columns = grid_columns(self.canvas.winfo_width())
        for index in range(4):
            self.cards_frame.columnconfigure(index, weight=1 if index < self.columns else 0,
                                             minsize=0, uniform="accounts" if index < self.columns else "")
        for index, record in enumerate(visible):
            card = self.cards.get(record["id"])
            if card:
                card.set_record(record, record["id"] in self.jobs, record["id"] == self.selected_id)
            else:
                card = AccountCard(self.cards_frame, record, record["id"] in self.jobs,
                                   record["id"] == self.selected_id, self.activate)
            column = index % self.columns
            card.grid(row=index // self.columns, column=column, sticky="new",
                      padx=(0, 14 if column < self.columns - 1 else 0), pady=(0, 14))
            self.cards[record["id"]] = card
        if not visible:
            empty = tk.Frame(self.cards_frame, bg=BG, height=280)
            empty.grid(row=0, column=0, columnspan=self.columns, sticky="ew")
            empty.grid_propagate(False)
            tk.Label(empty, text="没有匹配的账号" if self.records else "添加你的第一个账号", bg=BG,
                     fg=INK, font=(FONT, 17, "bold")).pack(pady=(65, 12))
            tk.Label(empty, text="换个邮箱或备注试试。" if self.records else "本人登录一次，保存会话，之后直接查询额度。",
                     bg=BG, fg=MUTED, font=(FONT, 10)).pack()
            if not self.records:
                Button(empty, "添加第一个账号", self.callbacks["add"], variant="primary", width=154).pack(pady=24)
        self.cards_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if self.cards_frame.winfo_height() <= self.canvas.winfo_height():
            self.canvas.yview_moveto(0)
        else:
            self.canvas.yview_moveto(old_offset)

    def tick_countdowns(self):
        for card in self.cards.values():
            card.update_countdowns()
        self.countdown_timer = self.after(30_000, self.tick_countdowns)

    def destroy(self):
        for name in ("redraw_pending", "countdown_timer"):
            timer = getattr(self, name, None)
            if timer:
                self.after_cancel(timer)
        super().destroy()

    def verify_layout(self):
        width = self.canvas.winfo_width()
        assert width > 550
        assert self.header_buttons[-1].winfo_ismapped()
        for card in self.cards.values():
            card_width = card.canvas.winfo_width()
            assert card_width >= 360
            email = card.canvas.bbox(card.email_item)
            assert email and 0 <= email[0] and email[2] <= card_width
            manage = card.buttons[1]
            assert manage.winfo_width() >= 60 and manage.winfo_height() >= 32
            assert email[2] + 8 <= manage.winfo_x()
            expiry = card.canvas.bbox(card.expiry_item)
            assert expiry and 0 <= expiry[0] and expiry[2] <= card_width
            wallet = card.canvas.bbox(card.wallet_item)
            assert wallet and wallet[0] >= card.canvas.bbox(card.tier_item)[2] + 16
            assert wallet[2] <= card_width - 16
            summary = card.canvas.bbox(card.key_summary_item)
            if summary:
                assert summary[0] >= 204 and summary[2] <= card_width
            for item in card.reset_items + card.countdown_items:
                bounds = card.canvas.bbox(item)
                assert bounds and 0 <= bounds[0] and bounds[2] <= card_width
            for date, countdown in zip(card.reset_items, card.countdown_items):
                assert card.canvas.bbox(date)[2] + 4 <= card.canvas.bbox(countdown)[0]
            for button in card.buttons:
                assert button.winfo_ismapped()
                assert button.winfo_x() >= 0 and button.winfo_x() + button.winfo_width() <= card_width
