import ctypes
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from ctypes import wintypes

import psutil
from websockets.sync.client import connect

from client import API, ApiError


def request_window_close(pids):
    if os.name != "nt":
        raise ApiError("请手动关闭该账号的独立登录窗口后再保存。")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    windows = []

    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    for hwnd in windows:
        # WM_CLOSE allows Chrome to save its profile. Never terminate a browser process.
        if not user32.PostMessageW(hwnd, 0x0010, 0, 0):
            raise ApiError("独立窗口没有响应关闭请求，请手动关闭后保存。")


def find_browser():
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(env)
        if root:
            for suffix in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"):
                path = Path(root) / suffix
                if path.is_file():
                    return path
    raise ApiError("请先安装 Google Chrome 或 Microsoft Edge。")


class Browser:
    def __init__(self, profile, executable=None):
        self.profile = Path(profile).resolve()
        self.executable = executable or find_browser()
        self.process = None
        self.port = None
        self.channel = None

    def profile_processes(self):
        matches = []
        for process in psutil.process_iter(["name", "cmdline"]):
            try:
                info = process.info
                if (info.get("name") or "").casefold() not in ("chrome.exe", "msedge.exe"):
                    continue
                args = info.get("cmdline") or []
                if any(arg.startswith("--type=") for arg in args):
                    continue
                profile = None
                for index, arg in enumerate(args):
                    if arg.startswith("--user-data-dir="):
                        profile = arg.split("=", 1)[1]
                    elif arg == "--user-data-dir" and index + 1 < len(args):
                        profile = args[index + 1]
                if profile and Path(profile.strip('"')).resolve() == self.profile:
                    matches.append((process.pid, any(arg.startswith("--remote-debugging") for arg in args)))
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        return matches

    def start(self):
        # Google OAuth runs in a normal, user-operated browser without a control channel.
        running = self.profile_processes()
        if any(debug for _pid, debug in running) or self.channel:
            raise ApiError("该账号的旧调试窗口仍打开，请先关闭该独立窗口，再点 Google 登录。无需退出账号。")
        self.profile.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen([
            str(self.executable), f"--user-data-dir={self.profile}",
            "--no-first-run", "--no-default-browser-check", "--new-window",
            "https://open-design.ai/cloud/login?redirect=%2Fdashboard",
        ], shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def require_login_closed(self):
        if self.profile_processes():
            raise ApiError("请先完成 Google 登录并关闭此账号的所有独立浏览器窗口，再点第二步。不要退出 Google / OpenDesign。")

    def handoff_after_consent(self):
        """Called only after the GUI explicitly confirms closing this isolated profile."""
        running = self.profile_processes()
        if any(debug for _pid, debug in running):
            raise ApiError("该账号有其他连接窗口，请手动关闭后再保存会话。")
        if not running:
            return
        request_window_close({pid for pid, _debug in running})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not self.profile_processes():
                return
            time.sleep(0.2)
        raise ApiError("独立浏览器仍在运行，可能有未保存提示或后台进程；请手动关闭后再保存。不强制结束进程。")

    def local_json(self, path):
        # Do not proxy a local browser control connection.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{self.port}{path}", timeout=3) as response:
            return json.loads(response.read(100_000).decode("utf-8"))

    def start_capture(self):
        # Enable local readout only after OAuth has finished and the normal browser has exited.
        if self.channel:
            try:
                self.call("Browser.getVersion")
                return
            except Exception:
                self.channel = None
        self.require_login_closed()
        self.profile.mkdir(parents=True, exist_ok=True)
        active_port = self.profile / "DevToolsActivePort"
        # A stale file must not attach us to an unrelated local process.
        if active_port.exists():
            old_port = active_port.read_text(encoding="utf-8").splitlines()
            if old_port and re.fullmatch(r"\d{1,5}", old_port[0]):
                self.port = int(old_port[0])
                try:
                    self.local_json("/json/version")
                except Exception:
                    pass
                else:
                    raise ApiError("该账号已有连接窗口，请先关闭它，再在本工具中打开。")
            active_port.unlink()
        self.process = subprocess.Popen([
            str(self.executable), f"--user-data-dir={self.profile}",
            "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
            "--no-first-run", "--no-default-browser-check", "--new-window",
            "https://open-design.ai/cloud/dashboard",
        ], shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if active_port.exists():
                lines = active_port.read_text(encoding="utf-8").splitlines()
                if lines and re.fullmatch(r"\d{1,5}", lines[0]):
                    self.port = int(lines[0])
                    if 0 < self.port < 65536:
                        try:
                            version = self.local_json("/json/version")
                            url = version.get("webSocketDebuggerUrl", "")
                            parsed = urllib.parse.urlsplit(url)
                            if parsed.scheme != "ws" or parsed.hostname not in ("127.0.0.1", "localhost") or parsed.port != self.port:
                                raise ApiError("浏览器控制地址不在本机，拒绝连接。")
                            self.channel = connect(url, proxy=None, origin=None, open_timeout=3, close_timeout=2)
                            return
                        except (OSError, ValueError, urllib.error.URLError):
                            pass
            if self.process.poll() is not None:
                break
            time.sleep(0.15)
        raise ApiError("浏览器连接失败；请关闭该独立窗口后重试。")

    def call(self, method, params=None):
        if not self.channel:
            raise ApiError("请先打开所选账号的登录窗口。")
        self.channel.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.channel.recv(timeout=5))
            if message.get("id") == 1:
                if "error" in message:
                    raise ApiError("浏览器授权连接出错；请关闭独立窗口后重新登录。")
                return message.get("result", {})

    def cookies(self):
        if not self.channel:
            raise ApiError("请先在普通浏览器完成登录，关闭窗口后点击第二步。")
        pages = self.call("Target.getTargets").get("targetInfos", [])
        eligible = [page for page in pages if page.get("type") == "page" and
                    urllib.parse.urlsplit(page.get("url", "")).hostname == "open-design.ai"]
        if not eligible:
            raise ApiError("请先完成 Google 登录并返回 OpenDesign 控制台。")
        # CDP's Network domain is page scoped. Use only a page on OpenDesign.
        target_id = eligible[0]["targetId"]
        attach = self.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = attach["sessionId"]
        self.channel.send(json.dumps({"id": 2, "sessionId": session_id, "method": "Network.getCookies", "params": {"urls": [API + "/auth/get-session"]}}))
        try:
            while True:
                result = json.loads(self.channel.recv(timeout=5))
                if result.get("id") == 2:
                    if "error" in result:
                        raise ApiError("无法读取该独立窗口的 OpenDesign 会话。")
                    cookies = result.get("result", {}).get("cookies", [])
                    break
        finally:
            self.call("Target.detachFromTarget", {"sessionId": session_id})
        result = []
        for cookie in cookies:
            if cookie.get("domain", "").lstrip(".") not in ("amr-api.open-design.ai", "open-design.ai"):
                continue
            if not re.search(r"session|auth|token", cookie.get("name", ""), re.IGNORECASE):
                continue
            result.append({key: cookie.get(key) for key in ("name", "value", "domain", "path", "expires", "secure")})
        if not result:
            raise ApiError("没有发现 OpenDesign 登录会话，请完成 Google 授权后再绑定。")
        return result

    def close(self):
        if self.channel:
            try:
                self.call("Browser.close")
            except Exception:
                pass
            try:
                self.channel.close()
            except Exception:
                pass
            self.channel = None
