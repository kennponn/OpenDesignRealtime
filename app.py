import argparse
import csv
import json
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from browser_auth import Browser
from client import ApiError
from secure_store import Store, TZ, now
from session_service import capture_session, query_session, query_keys
from desktop_view import BG, INK, GREEN, MUTED, Button, Dashboard
from key_clipboard import KeyClipboard
from key_actions import create_existing_account_key, revoke_old_key


FONT = "Microsoft YaHei UI"
TIERS = {"free": "Free", "go": "Go", "plus": "Go Plus", "pro": "Go Pro", "max": "Go Max"}


def local_date(value):
    if not value:
        return "未返回"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return "格式未知"


def window_text(data, kind):
    window = next((item for item in (data.get("windows") or []) if item["kind"] == kind), None)
    if not window:
        return "未返回"
    return f"{window['percent']:g}%" if window["percent"] is not None else "未返回"


def window_reset_text(data, kind):
    window = next((item for item in (data.get("windows") or []) if item.get("kind") == kind), None)
    return local_date(window.get("resets_at")) if window else "未返回"


def account_values(record, busy=False):
    data = record["public"]
    return [record["email"], TIERS.get(data.get("tier"), data.get("tier") or "未查询"),
            window_text(data, "5h"), window_reset_text(data, "5h"),
            window_text(data, "7d"), window_reset_text(data, "7d"),
            data.get("balance_usd") if data.get("balance_usd") is not None else "未查询",
            local_date(data.get("expires_at")), local_date(record.get("last_success")) if record.get("last_success") else "未查询",
            "处理中" if busy else record["status"]]


def export_public(records, path):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["邮箱", "备注", "套餐", "订阅结束时间", "余额USD", "5小时剩余百分比", "7天剩余百分比", "5小时重置时间", "7天重置时间", "最后成功查询", "查询状态"])
        for record in records:
            data = record["public"]
            resets = {item["kind"]: item.get("resets_at") for item in (data.get("windows") or [])}
            values = [record["email"], record["label"], data.get("tier"), data.get("expires_at"), data.get("balance_usd"), window_text(data, "5h"), window_text(data, "7d"), resets.get("5h"), resets.get("7d"), record.get("last_success"), record["status"]]
            values = ["" if value is None else str(value) for value in values]
            values = ["'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value for value in values]
            writer.writerow(values)


class App(tk.Tk):
    def __init__(self, data_dir, smoke=False):
        super().__init__()
        self.title("OpenDesign · 多账号额度")
        icon = Path(__file__).with_name("icon.png")
        if icon.is_file():
            self.app_icon = tk.PhotoImage(file=str(icon))
            self.iconphoto(True, self.app_icon)
        self.configure(bg=BG)
        width, height = min(1240, self.winfo_screenwidth() - 60), min(820, self.winfo_screenheight() - 90)
        self.geometry(f"{width}x{height}+20+20")
        self.minsize(min(960, width), min(680, height))
        self.store = Store(data_dir)
        self.key_clipboard = KeyClipboard(self)
        self.browsers = {}
        self.jobs = set()
        self.events = queue.Queue()
        self.closed = False
        self.selected_id = None
        self.search = tk.StringVar()
        self.auto = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="就绪 · 登录会话加密保存在本机")
        self.style()
        self.build()
        self.search.trace_add("write", lambda *_args: self.refresh_view())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Control-n>", lambda _event: self.add())
        self.refresh_view()
        self.after(100, self.process_events)
        if not smoke:
            self.after(500, self.refresh_all)
            self.after(60_000, self.poll)

    def style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=(FONT, 10), background=BG, foreground=INK)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG)
        style.configure("Title.TLabel", font=(FONT, 25, "bold"))
        style.configure("Muted.TLabel", font=(FONT, 9), foreground=MUTED)
        style.configure("TButton", padding=(12, 8), relief="flat", background="white")
        style.map("TButton", background=[("active", "#dcece3")])
        style.configure("Accent.TButton", background=GREEN, foreground="white")
        style.map("Accent.TButton", background=[("active", "#124e37"), ("disabled", "#829b8c")], foreground=[("disabled", "white")])
        style.configure("TEntry", fieldbackground="white", padding=6)
        style.configure("Treeview", background="white", fieldbackground="white", rowheight=43, borderwidth=0)
        style.configure("Treeview.Heading", background="#e4ebe6", font=(FONT, 10, "bold"), padding=(8, 12))
        style.map("Treeview", background=[("selected", "#dcece3")], foreground=[("selected", INK)])

    def build(self):
        self.view = Dashboard(self, self.search, self.auto, self.status, {
            "add": self.add, "refresh_all": self.refresh_all, "refresh": self.refresh_selected,
            "login": self.login, "bind": self.bind_account, "forget": self.forget,
            "export": self.export, "help": self.help, "select": self.select_account,
            "save_key": self.save_key, "copy_key": self.copy_key,
            "sync_keys": self.sync_keys,
            "create_key": lambda: self.key_dialog(False), "rebuild_key": lambda: self.key_dialog(True),
            "review_key_operation": self.review_key_operation,
        })
        self.view.pack(fill="both", expand=True)

    def selected(self):
        return self.store.get(self.selected_id) if self.selected_id else None

    def select_account(self, account_id):
        self.selected_id = account_id

    def refresh_view(self, select=None):
        records = self.store.all()
        for record in records:
            record["has_api_key"] = self.store.has_api_key(record["id"])
        identifiers = {record["id"] for record in records}
        if select in identifiers:
            self.selected_id = select
        elif self.selected_id not in identifiers:
            self.selected_id = records[0]["id"] if records else None
        self.view.set_records(records, self.jobs, self.selected_id)

    def copy_key(self):
        record = self.selected()
        if not record:
            return
        try:
            value = self.store.credentials(record["id"]).get("api_key")
            if not isinstance(value, str) or not value.strip():
                messagebox.showinfo("没有完整 Key 可复制", "官网 Key 列表只返回前缀，不能用作完整 API Key。\n"
                                    "请通过卡片菜单保存创建时保留的完整 Key；不会自动新建。", parent=self)
                return
            self.key_clipboard.copy(value)
        except Exception:
            messagebox.showerror("复制未完成", "无法解密凭证或写入剪贴板；未显示完整 Key。", parent=self)
            return
        self.status.set(f"{record['email']} 的完整 Key 已复制；30 秒后清理当前剪贴板。")

    def save_key(self):
        record = self.selected()
        if not record or record["id"] in self.jobs:
            return
        dialog = tk.Toplevel(self)
        dialog.title("保存已有 API Key")
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=28)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="保存已有 Key", font=(FONT, 18, "bold")).pack(anchor="w")
        ttk.Label(panel, text=record["email"], font=(FONT, 10, "bold")).pack(anchor="w", pady=(8, 16))
        ttk.Label(panel, text="请确认 Key 属于此账号；这里只加密保存，不联网核验归属。\n"
                  "不会创建 Key，不改变额度查询方式。", style="Muted.TLabel").pack(anchor="w")
        entry = ttk.Entry(panel, width=48, show="*")
        entry.pack(fill="x", pady=(18, 10))
        ttk.Label(panel, text="卡片只显示 sk-******，点击复制完整值。\n"
                  "复制后 30 秒清理当前剪贴板，但不能清除系统历史或云同步。",
                  style="Muted.TLabel").pack(anchor="w")

        def dismiss():
            entry.delete(0, "end")
            dialog.destroy()

        def save():
            if record["id"] in self.jobs:
                messagebox.showinfo("账号正在查询", "请等此账号操作结束后再保存 Key。", parent=dialog)
                return
            if self.store.has_api_key(record["id"]) and not messagebox.askyesno(
                "替换本地 Key", f"账号：{record['email']}\n\n替换本地保存的 Key？不会撤销官网原 Key。", parent=dialog,
            ):
                return
            try:
                self.store.save_api_key(record["id"], entry.get())
            except ValueError as error:
                messagebox.showerror("未保存", str(error), parent=dialog)
                return
            except Exception:
                messagebox.showerror("未保存", "无法加密保存 Key；未保存明文，请检查 Windows 用户和目录权限。", parent=dialog)
                return
            dismiss()
            self.refresh_view(record["id"])
            self.status.set(f"{record['email']} 的已有 Key 已加密保存；未联网验证，不用于额度查询。")

        Button(panel, "加密保存", save, variant="primary", width=128).pack(anchor="e", pady=(20, 0))
        dialog.protocol("WM_DELETE_WINDOW", dismiss)
        dialog.bind("<Escape>", lambda _: dismiss())
        dialog.bind("<Return>", lambda _: save())
        entry.focus_set()

    def sync_keys(self):
        record = self.selected()
        if not record or not record["has_credentials"] or record["id"] in self.jobs:
            return
        try:
            secret = self.store.credentials(record["id"])
        except Exception:
            messagebox.showerror("同步未完成", "无法解密本地会话，请重新保存登录会话。", parent=self)
            return

        def synced(inventory):
            current = self.store.get(record["id"])
            public = dict(current["public"])
            public.update(key_inventory=inventory, key_sync_error=None)
            self.store.save_key_inventory(record["id"], public)
            self.status.set(f"{record['email']} 已同步 {inventory['total']} 把已有 Key（{inventory['active']} 把可用）；官网仅返回前缀。")

        def failed(error):
            current = self.store.get(record["id"])
            public = dict(current["public"])
            public["key_sync_error"] = error
            self.store.save_key_inventory(record["id"], public)
            self.status.set("已有 Key 同步失败；未覆盖原 Key 或上次同步数据。")

        self.job(record, lambda: query_keys(record["email"], secret), synced, failed, affect_status=False)

    def review_key_operation(self):
        record = self.selected()
        if not record or record["id"] in self.jobs:
            return
        if messagebox.askyesno("确认已核对官网结果", f"账号：{record['email']}\n\n"
                               "仅在你已经检查官网 Key 列表、确认上次操作结果后继续。\n"
                               "这会解除本地重复操作保护，不会新建、撤销或恢复任何 Key。\n"
                               "如果上次新建成功但未保存完整值，无法从列表恢复它。\n\n确认已核对？", parent=self):
            self.set_key_operation(record["id"], "reviewed")
            self.refresh_view(record["id"])

    def set_key_operation(self, account_id, state, **extra):
        public = dict(self.store.get(account_id)["public"])
        public["key_operation"] = {"state": state, "updated_at": now(), **extra}
        self.store.save_key_inventory(account_id, public)

    def key_dialog(self, rebuild):
        record = self.selected()
        if not record or not record["has_credentials"] or record["id"] in self.jobs:
            return
        operation = record["public"].get("key_operation", {})
        if operation.get("state") in ("create_pending", "revoke_pending", "needs_review"):
            messagebox.showwarning("上次 Key 操作需核对", "为避免重复创建，已暂停再次操作。\n"
                                   "请先同步并核对官网，再使用操作面板「核对上次操作」。", parent=self)
            return
        entries = [entry for entry in record["public"].get("key_inventory", {}).get("entries", [])
                   if entry["status"] != "revoked"]
        if rebuild and (not entries or record["public"].get("key_sync_error")):
            messagebox.showinfo("请先同步 Key", "请先同步已有 Key 列表，再选择需要撤销的旧 Key。", parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title("重建 Key" if rebuild else "新建并保存 Key")
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=28)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="重建并保存 Key" if rebuild else "新建并保存 Key", font=(FONT, 18, "bold")).pack(anchor="w")
        ttk.Label(panel, text=record["email"], font=(FONT, 10, "bold")).pack(anchor="w", pady=(8, 16))
        ttk.Label(panel, text="新建成功后自动加密保存，卡片显示 sk-******，点击复制。\n"
                  + ("先新建并保存，再撤销选中的旧 Key；其他 Key 保留。\n旧 Key 会永久失效，使用它的工具必须更新配置。" if rebuild
                     else "只新增一把 Key；不会撤销官网现有 Key。"), style="Muted.TLabel").pack(anchor="w")
        ttk.Label(panel, text="新 Key 名称").pack(anchor="w", pady=(16, 6))
        name = ttk.Entry(panel, width=48)
        name.insert(0, "OpenDesignManager-" + datetime.now(TZ).strftime("%m%d-%H%M%S"))
        name.pack(fill="x")
        selector = None
        if rebuild:
            ttk.Label(panel, text="只撤销这把旧 Key").pack(anchor="w", pady=(16, 6))
            choices = [f"{entry['name']} | {entry['prefix']}*** | ID {entry['id']}" for entry in entries]
            selector = ttk.Combobox(panel, state="readonly", values=choices, width=58)
            selector.current(0)
            selector.pack(fill="x")

        def submit():
            label = name.get().strip()
            if not 1 <= len(label) <= 50:
                messagebox.showerror("名称无效", "名称需为 1 至 50 个字符。", parent=dialog)
                return
            old = entries[selector.current()] if rebuild else None
            target = f"\n将撤销：{old['name']}\nKey ID：{old['id']}\n旧 Key 永久失效，外部工具需要换 Key。" if old else "\n保留所有现有 Key。"
            if not messagebox.askyesno("确认 Key 操作", f"账号：{record['email']}\n新 Key 名称：{label}" + target
                                      + "\n\n是否执行？不会调用模型或修改订阅。", parent=dialog):
                return
            dialog.destroy()
            self.run_key_creation(record["id"], label, old["id"] if old else None)

        Button(panel, "确认重建" if rebuild else "新建并保存", submit, variant="primary", width=128).pack(anchor="e", pady=(22, 0))

    def run_key_creation(self, account_id, name, old_id=None):
        record = self.store.get(account_id)
        if account_id in self.jobs:
            return
        if record["public"].get("key_operation", {}).get("state") in ("create_pending", "revoke_pending", "needs_review"):
            return
        try:
            secret = self.store.credentials(account_id)
            # Persist intent before POST so a crash cannot trigger an automatic duplicate.
            self.set_key_operation(account_id, "create_pending", old_id=old_id, name=name)
        except Exception:
            messagebox.showerror("未开始操作", "无法读取或记录本地凭证；没有发送新建请求。", parent=self)
            return

        def failed(error):
            self.set_key_operation(account_id, "needs_review", old_id=old_id, name=name)
            self.status.set(error)
            messagebox.showwarning("Key 操作需核对", error + "\n没有自动重试。请核对官网结果后再操作。", parent=self)

        def complete(inventory):
            public = dict(self.store.get(account_id)["public"])
            public.update(key_inventory=inventory, key_sync_error=None,
                          key_operation={"state": "completed", "updated_at": now()})
            self.store.save_key_inventory(account_id, public)
            self.status.set(f"{record['email']} 新 Key 已加密保存，可点击复制。" + ("所选旧 Key 已撤销。" if old_id else "现有 Key 保留。"))

        def saved(result):
            try:
                self.store.save_created_key(account_id, result)
                persisted = self.store.credentials(account_id)
            except Exception:
                failed("新 Key 可能已创建，但本地保存失败；旧 Key 未撤销。不要重复新建，请检查官网。")
                return
            if old_id:
                self.set_key_operation(account_id, "revoke_pending", old_id=old_id, new_id=result["created"]["key_id"])
                self.job(self.store.get(account_id),
                         lambda: revoke_old_key(record["email"], persisted, old_id, result["created"]["key_id"]),
                         complete, failed, affect_status=False)
            else:
                self.set_key_operation(account_id, "completed", new_id=result["created"]["key_id"])
                self.status.set(f"{record['email']} 新 Key 已加密保存，可点击复制；现有 Key 保留。")
                self.job(self.store.get(account_id), lambda: query_keys(record["email"], persisted),
                         complete, lambda error: self.status.set("新 Key 已保存可复制；列表同步失败，请稍后手动同步。"), affect_status=False)

        self.job(record, lambda: create_existing_account_key(record["email"], secret, name, old_id),
                 saved, failed, affect_status=False)

    def add(self):
        dialog = tk.Toplevel(self)
        dialog.title("连接新账号")
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=28)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="连接一个新账号", font=(FONT, 18, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(panel, text="1 本人登录    /    2 保存会话    /    3 查看额度", style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 20))
        ttk.Label(panel, text="预计登录邮箱").grid(row=2, column=0, sticky="w", pady=8)
        email = ttk.Entry(panel, width=38)
        email.grid(row=2, column=1, padx=(16, 0))
        ttk.Label(panel, text="备注（可选）").grid(row=3, column=0, sticky="w", pady=8)
        label = ttk.Entry(panel, width=38)
        label.grid(row=3, column=1, padx=(16, 0))
        ttk.Label(panel, text="独立登录环境，以官网实际邮箱为准。\n不用提供密码、验证码或 Cookie。", style="Muted.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=(16, 20))

        def save():
            try:
                account_id = self.store.add(email.get(), label.get())
            except Exception as error:
                messagebox.showerror("添加失败", str(error), parent=dialog)
                return
            self.refresh_view(account_id)
            dialog.destroy()
            self.after(150, lambda: self.login(account_id))

        Button(panel, "添加并登录", save, variant="primary", width=128).grid(row=5, column=1, sticky="e")
        email.focus_set()
        dialog.bind("<Return>", lambda _event: save())

    def job(self, record, operation, callback, failure=None, affect_status=True):
        account_id = record["id"]
        if account_id in self.jobs:
            return
        self.jobs.add(account_id)
        self.refresh_view(account_id)

        def run():
            try:
                result = operation()
            except Exception as error:
                # Do not put arbitrary exception text into UI or logs; it can contain credentials.
                safe_error = str(error) if isinstance(error, ApiError) else "连接或解析错误，请关闭独立窗口后重试。"
                self.events.put((account_id, failure, safe_error, True, affect_status))
            else:
                self.events.put((account_id, callback, result, False, affect_status))

        threading.Thread(target=run, daemon=True).start()

    def process_events(self):
        try:
            while True:
                account_id, callback, result, failed, affect_status = self.events.get_nowait()
                self.jobs.discard(account_id)
                if failed:
                    if affect_status:
                        self.store.update(account_id, "查询失败 / " + result)
                    self.status.set(result)
                    if callback:
                        callback(result)
                else:
                    try:
                        callback(result)
                    except Exception:
                        if affect_status:
                            self.store.update(account_id, "本地凭证保存失败，请重新保存会话")
                        self.status.set("本地保存失败；未写入明文凭证，请检查目录权限。")
                self.refresh_view(account_id)
        except queue.Empty:
            pass
        if not self.closed:
            self.after(100, self.process_events)

    def login(self, account_id=None):
        record = self.store.get(account_id) if account_id else self.selected()
        if not record:
            return
        if record["id"] in self.jobs:
            return
        self.refresh_view(record["id"])
        if not messagebox.askokcancel(
            "Google 本人授权",
            f"当前选中的记录：{record['email']}\n"
            "要登录另一个账号，请先点击添加账号，不要在已有账号的窗口里退出并换号。\n\n"
            "将打开只属于此账号的普通 Chrome / Edge 窗口，登录阶段不启用调试或自动化。\n"
            "请本人完成 Google 登录并返回 OpenDesign 控制台，然后回到操作面板点击保存会话。\n"
            "保存时经你确认会关闭此账号的独立窗口；也可以自行关闭，不要退出账号。\n\n"
            "工具只提取此独立环境中的 OpenDesign 会话，不读取日常浏览器或 Google Cookie。\n"
            "Google 若拒绝此环境，不绕过其验证。",
            parent=self,
        ):
            return
        browser = self.browsers.get(record["id"])
        if not browser:
            try:
                browser = Browser(self.store.profile(record["id"]))
            except ApiError as error:
                messagebox.showerror("无法打开", str(error), parent=self)
                return
            self.browsers[record["id"]] = browser
        self.job(record, browser.start, lambda _result: self.status.set("普通登录窗口已打开。登录成功后回到工具点保存会话；不要退出账号。"))

    def bind_account(self):
        record = self.selected()
        if not record:
            return
        browser = self.browsers.get(record["id"])
        if not browser:
            try:
                browser = Browser(self.store.profile(record["id"]))
            except ApiError as error:
                messagebox.showerror("无法打开", str(error), parent=self)
                return
            self.browsers[record["id"]] = browser
        if not messagebox.askokcancel(
            "确认登录已完成",
            "请确认已完成登录并进入 OpenDesign 控制台。\n\n"
            "点击确定后，工具会正常关闭此账号的所有独立浏览器窗口，保留登录状态，\n"
            "再临时打开本机连接窗口，只读取 OpenDesign 会话并校验实际邮箱。\n"
            "不关闭日常浏览器，不强制结束进程；本账号有未保存内容时请先取消。\n\n"
            "不要在接收窗口进行 Google 登录；若提示未登录，请返回第一步。\n"
            "保存登录会话并查询额度、已有 Key 列表；不创建 Key，不读取完整旧 Key。",
            parent=self,
        ):
            return
        try:
            previous = self.store.credentials(record["id"])
        except Exception:
            messagebox.showerror("凭证无法解密", "请使用原 Windows 用户；如需重新登录，请先解除本地绑定。", parent=self)
            return

        def capture():
            browser.handoff_after_consent()
            return capture_session(browser, previous, include_keys=True)

        def captured(result):
            identity, secret, live = result
            if identity["email"] != record["email"]:
                if record["has_credentials"]:
                    self.status.set("登录了其他邮箱，未覆盖已有会话；请添加新账号。")
                    messagebox.showwarning("防止账号串号", self.status.get(), parent=self)
                    return
                if not messagebox.askyesno(
                    "确认实际登录账号",
                    f"列表填写：{record['email']}\n官网实际登录：{identity['email']}\n\n"
                    "是否把这条尚未绑定的记录更正为实际邮箱，并保存会话？\n不会覆盖列表中其他账号。",
                    parent=self,
                ):
                    self.status.set("已取消保存；没有更改账号或已有凭证。")
                    return
            try:
                self.store.bind_session(record["id"], identity["email"], secret, live)
            except ValueError as error:
                messagebox.showerror("保存未完成", str(error), parent=self)
                self.status.set(str(error))
                return
            self.status.set(f"{identity['email']} 的会话已加密保存；额度与余额查询成功，以后可直接刷新。")

        self.job(record, capture, captured, lambda error: messagebox.showerror("绑定未完成", error, parent=self))

    def refresh_one(self, account_id):
        record = self.store.get(account_id)
        if not record["has_credentials"] or account_id in self.jobs:
            return
        try:
            secret = self.store.credentials(account_id)
        except Exception:
            self.store.update(account_id, "本地凭证无法解密，请重新绑定")
            self.refresh_view()
            return

        def query():
            return query_session(record["email"], secret, include_keys=True)

        def queried(live):
            if "key_inventory" not in live and "key_inventory" in record["public"]:
                live["key_inventory"] = record["public"]["key_inventory"]
            self.store.update(account_id, "接口查询成功 / 登录会话", live, success=True)
            suffix = "；已有 Key 同步失败，旧 Key 数据保留。" if live.get("key_sync_error") else "（含已有 Key 列表）。"
            self.status.set(f"{record['email']} 已完成最新额度查询" + suffix)

        self.job(record, query, queried)

    def refresh_selected(self):
        record = self.selected()
        if record and record["has_credentials"]:
            self.refresh_one(record["id"])
        else:
            messagebox.showinfo("尚未绑定", "请先完成 Google 登录并绑定账号。", parent=self)

    def refresh_all(self):
        # Limit concurrency so a large account pool cannot hammer the service.
        if getattr(self, "batch_queue", []):
            return
        available = max(0, 2 - len(self.jobs))
        candidates = [record for record in self.store.all() if record["has_credentials"] and record["id"] not in self.jobs
                      and not any(reason in record["status"] for reason in
                                  ("会话失效", "尚未登录", "需重新登录", "不一致", "绑定范围", "接口拒绝访问"))]
        candidates.sort(key=lambda record: record["last_attempt"] or "")
        self.batch_queue = [record["id"] for record in candidates]
        for _ in range(min(available, len(self.batch_queue))):
            self.refresh_one(self.batch_queue.pop(0))
        if self.batch_queue:
            self.after(500, self.batch_next)

    def batch_next(self):
        if self.closed:
            return
        pending = getattr(self, "batch_queue", [])
        while pending and len(self.jobs) < 2:
            self.refresh_one(pending.pop(0))
        if pending:
            self.after(500, self.batch_next)

    def poll(self):
        if self.auto.get() and not self.jobs and not getattr(self, "batch_queue", []):
            self.refresh_all()
        if not self.closed:
            self.after(60_000, self.poll)

    def forget(self):
        record = self.selected()
        if not record or record["id"] in self.jobs:
            return
        if messagebox.askyesno("解除本地绑定", f"当前账号：{record['email']}\n\n删除这条记录加密保存的凭证和查询缓存，不会退出 Google 或取消订阅。\n浏览器登录目录仍保留，不删除官网 API Key。", parent=self):
            browser = self.browsers.get(record["id"])
            if browser:
                browser.close()
            self.store.forget(record["id"])
            self.refresh_view()

    def export(self):
        path = filedialog.asksaveasfilename(title="导出查询结果（不含登录会话）", initialfile="OpenDesign实时查询.csv", defaultextension=".csv", filetypes=[("CSV", "*.csv")], parent=self)
        if path:
            try:
                export_public(self.store.all(), path)
                self.status.set("已导出两档额度、重置时间和余额，不包含登录会话。")
            except Exception:
                messagebox.showerror("导出失败", "无法写入目标文件，请检查权限。", parent=self)

    def help(self):
        messagebox.showinfo(
            "接口连接说明",
            "流程：添加邮箱 → 普通浏览器本人登录 → 保存登录会话（确认关闭独立窗口） → 查询。\n\n"
            "使用官网会话 / 订阅快照 / 额度查询接口，不依赖 API Key。\n"
            "以后直接读取加密会话刷新，多账号互相独立；会话失效时需重新登录。\n"
            "刷新只调用查询接口，不调用模型、不充值、不续费。\n"
            "卡片菜单可加密保存已有 API Key；只遮罩显示，点击复制完整值，不用于额度查询。\n"
            "启动、保存会话及刷新会读取已有 Key 列表；菜单也可单独同步。不会新建 Key。\n"
            "官网已有 Key 接口只返回前缀，完整 Key 仅在创建时显示；导入完整值后才可复制。\n"
            "菜单可确认新建并加密保存；重建先保存新 Key，再撤销所选旧 Key，其他 Key 保留。\n"
            "官网实际邮箱与初次填写不同时，会请求确认更正；不会覆盖其他已绑定账号。\n\n"
            "安全：提取的 OpenDesign 会话使用当前 Windows 用户 DPAPI 加密。\n"
            "浏览器目录由浏览器自身保护；不要分享 data 文件夹。\n"
            "Google 登录阶段没有调试连接；只有第二步经确认才启用本机接收连接。\n"
            "接收窗口不能用来登录 Google，未登录时回到第一步。\n"
            "自动查询仅在程序运行时生效，最长轮询间隔不是推送实时。\n\n"
            "2026-11-06 已在本人登录页面实测 Go 套餐及两档额度 / 余额接口返回 200。\n"
            "内部接口可能变化；失败时保留上次成功数据并显示错误，不伪装为实时值。\n"
            f"数据目录：{self.store.root}", parent=self,
        )

    def close(self):
        if self.jobs and not messagebox.askyesno("查询仍在进行", "有连接操作未完成，现在关闭可能导致本次会话没有保存。\n建议等操作结束后再关闭。是否仍关闭？", parent=self):
            return
        self.closed = True
        self.key_clipboard.clear()
        for browser in self.browsers.values():
            browser.close()
        self.store.close()
        self.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    if args.smoke and not args.data_dir:
        parser.error("测试模式必须指定 --data-dir。")
    directory = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    app = App(args.data_dir or directory / "data", smoke=args.smoke)
    if args.smoke:
        failures = []

        def check():
            try:
                assert len(app.view.cards) == len(app.store.all())
                if args.screenshot:
                    from PIL import ImageGrab
                    import ctypes
                    library = ctypes.windll.user32
                    library.GetParent.argtypes = [ctypes.c_void_p]
                    library.GetParent.restype = ctypes.c_void_p
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    ImageGrab.grab(window=library.GetParent(app.winfo_id())).save(args.screenshot)
                app.geometry("960x680")
                app.update()
                app.view.repaint_cards()
                app.update()
                app.view.verify_layout()
            except Exception as error:
                failures.append(error)
            finally:
                app.close()

        app.after(1200, check)
    app.mainloop()
    if args.smoke and failures:
        raise failures[0]


if __name__ == "__main__":
    main()
