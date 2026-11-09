from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from browser_auth import Browser
from client import ApiError


def fake_browser(tmp_path):
    executable = tmp_path / "chrome.exe"
    executable.touch()
    return Browser(tmp_path / "profile", executable=executable)


def test_google_login_has_no_control_flags(tmp_path):
    browser = fake_browser(tmp_path)
    with patch.object(browser, "profile_processes", return_value=[]), patch("browser_auth.subprocess.Popen") as launch:
        browser.start()
    args = launch.call_args.args[0]
    assert "https://open-design.ai/cloud/login?redirect=%2Fdashboard" in args
    assert f"--user-data-dir={browser.profile}" in args
    assert not any("debugging" in arg or "automation" in arg or "user-agent" in arg for arg in args)
    assert launch.call_args.kwargs["shell"] is False
    assert browser.channel is None


def test_existing_normal_profile_can_open(tmp_path):
    browser = fake_browser(tmp_path)
    with patch.object(browser, "profile_processes", return_value=[(123, False)]), patch("browser_auth.subprocess.Popen") as launch:
        browser.start()
    assert launch.call_count == 1


def test_existing_debug_profile_cannot_be_used_for_google(tmp_path):
    browser = fake_browser(tmp_path)
    with patch.object(browser, "profile_processes", return_value=[(123, True)]), patch("browser_auth.subprocess.Popen") as launch:
        with pytest.raises(ApiError, match="旧调试窗口"):
            browser.start()
    launch.assert_not_called()


def test_capture_requires_normal_browser_fully_closed(tmp_path):
    browser = fake_browser(tmp_path)
    with patch.object(browser, "profile_processes", return_value=[(123, False)]), patch("browser_auth.subprocess.Popen") as launch:
        with pytest.raises(ApiError, match="关闭此账号"):
            browser.start_capture()
    launch.assert_not_called()


def test_matching_profile_process(tmp_path):
    browser = fake_browser(tmp_path)
    processes = [
        SimpleNamespace(pid=1, info={"name": "chrome.exe", "cmdline": ["chrome.exe", f"--user-data-dir={browser.profile}"]}),
        SimpleNamespace(pid=2, info={"name": "chrome.exe", "cmdline": ["chrome.exe", "--type=renderer", f"--user-data-dir={browser.profile}"]}),
        SimpleNamespace(pid=3, info={"name": "chrome.exe", "cmdline": ["chrome.exe", "--user-data-dir", str(tmp_path / "other")]}),
        SimpleNamespace(pid=4, info={"name": "msedge.exe", "cmdline": ["msedge.exe", "--user-data-dir", str(browser.profile), "--remote-debugging-port=0"]}),
    ]
    with patch("browser_auth.psutil.process_iter", return_value=processes):
        assert browser.profile_processes() == [(1, False), (4, True)]


def test_cookie_handoff_reads_only_opendesign_domain(tmp_path):
    browser = fake_browser(tmp_path)
    browser.channel = SimpleNamespace()
    targets = {"targetInfos": [{"type": "page", "url": "https://accounts.google.com/", "targetId": "google"}]}
    with patch.object(browser, "call", return_value=targets) as control:
        with pytest.raises(ApiError, match="返回 OpenDesign"):
            browser.cookies()
    control.assert_called_once_with("Target.getTargets")


def test_cookie_handoff_requires_explicit_capture(tmp_path):
    browser = fake_browser(tmp_path)
    with pytest.raises(ApiError, match="普通浏览器完成登录"):
        browser.cookies()


def test_consented_handoff_only_closes_selected_profile_windows(tmp_path):
    browser = fake_browser(tmp_path)
    with patch.object(browser, "profile_processes", side_effect=[[(123, False)], []]), patch("browser_auth.request_window_close") as close:
        browser.handoff_after_consent()
    close.assert_called_once_with({123})


def test_consented_handoff_skips_already_closed_browser(tmp_path):
    browser = fake_browser(tmp_path)
    with patch.object(browser, "profile_processes", return_value=[]), patch("browser_auth.request_window_close") as close:
        browser.handoff_after_consent()
    close.assert_not_called()


def test_consented_handoff_refuses_other_debug_connection(tmp_path):
    browser = fake_browser(tmp_path)
    with patch.object(browser, "profile_processes", return_value=[(123, True)]), patch("browser_auth.request_window_close") as close:
        with pytest.raises(ApiError, match="其他连接窗口"):
            browser.handoff_after_consent()
    close.assert_not_called()


def test_consented_handoff_never_kills_background_process(tmp_path):
    browser = fake_browser(tmp_path)
    with patch.object(browser, "profile_processes", return_value=[(123, False)]), patch("browser_auth.request_window_close") as close, patch("browser_auth.time.monotonic", side_effect=[0, 11]):
        with pytest.raises(ApiError, match="不强制结束进程"):
            browser.handoff_after_consent()
    close.assert_called_once_with({123})
