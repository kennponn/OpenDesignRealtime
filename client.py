import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation


API = "https://amr-api.open-design.ai/api"
ORIGIN = "https://open-design.ai"


class ApiError(Exception):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # Never forward a credential to a redirected endpoint.
        return None


def cookie_header(cookies, path):
    values = []
    for cookie in cookies:
        domain = cookie.get("domain", "").lstrip(".")
        if domain not in ("amr-api.open-design.ai", "open-design.ai"):
            continue
        name, value = cookie.get("name", ""), cookie.get("value", "")
        cookie_path = cookie.get("path") or "/"
        path_ok = path == cookie_path or path.startswith(cookie_path.rstrip("/") + "/") or cookie_path == "/"
        if not path_ok:
            continue
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or re.search(r"[;\r\n\x00]", value):
            raise ApiError("本地登录凭证格式无效，请重新绑定。")
        values.append(f"{name}={value}")
    return "; ".join(values)


def identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ApiError("接口返回的账号或工作区标识无效。")
    return value


def decimal_text(value):
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ApiError("接口返回了无效的余额数值。") from error
    if not number.is_finite():
        raise ApiError("接口返回了无效的余额数值。")
    return format(number, "f")


def object_value(value, label):
    if not isinstance(value, dict):
        raise ApiError(f"{label}接口返回结构不匹配，未更新数据。")
    return value


def normalize_windows(windows):
    if windows is None:
        return None
    if not isinstance(windows, list):
        raise ApiError("额度窗口结构不匹配。")
    result = []
    for raw in windows:
        window = object_value(raw, "额度窗口")
        kind = window.get("kind")
        if not kind:
            duration = window.get("durationSeconds")
            kind = {18000: "5h", 604800: "7d"}.get(duration, str(duration))
        remaining, limit = window.get("remainingCredits"), window.get("limitCredits")
        if not re.fullmatch(r"\d+", str(remaining)) or not re.fullmatch(r"\d+", str(limit)):
            raise ApiError("额度返回值格式不匹配，未更新数据。")
        remaining_number, limit_number = int(remaining), int(limit)
        percent = round(min(100, 100 * remaining_number / limit_number), 1) if limit_number else None
        result.append({
            "kind": str(kind), "remaining": str(remaining), "limit": str(limit), "percent": percent,
            "resets_at": window.get("resetsAt"), "window_start": window.get("windowStart"),
        })
    return result


class Client:
    def __init__(self, cookies=None, key=None, transport=None):
        self.cookies = cookies or []
        self.key = key
        self.transport = transport
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, method="GET", body=None, scope=None, key_only=False):
        if not path.startswith(("/auth/", "/v1/")) or re.search(r"[\r\n]", path):
            raise ApiError("不允许的接口路径。")
        headers = {"Accept": "application/json", "Origin": ORIGIN, "Referer": ORIGIN + "/cloud/", "User-Agent": "OpenDesignLocalManager/2.0", "Cache-Control": "no-cache"}
        if key_only:
            if not self.key or re.search(r"[\r\n]", self.key):
                raise ApiError("尚未获取有效 API Key。")
            headers["Authorization"] = "Bearer " + self.key
        else:
            header = cookie_header(self.cookies, "/api" + path.split("?", 1)[0])
            if header:
                headers["Cookie"] = header
        if scope:
            headers["x-vela-workspace-id"] = identifier(scope["workspaceId"])
            if scope.get("workspaceMemberId"):
                headers["x-vela-workspace-member-id"] = identifier(scope["workspaceMemberId"])
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.transport:
            return self.transport(path, method, body, headers)
        req = urllib.request.Request(API + path, data=payload, headers=headers, method=method)
        # Retry only read-only transport failures. A timed-out Key POST may have succeeded.
        attempts = 3 if method == "GET" and body is None else 1
        for attempt in range(attempts):
            try:
                with self.opener.open(req, timeout=15) as response:
                    if "json" not in response.headers.get("Content-Type", ""):
                        raise ApiError("接口未返回 JSON，可能是登录或访问验证页面。")
                    data = response.read(2_000_001)
                    if len(data) > 2_000_000:
                        raise ApiError("接口响应过大，已停止解析。")
                    return json.loads(data.decode("utf-8"))
            except urllib.error.HTTPError as error:
                code = None
                try:
                    raw = json.loads(error.read(2000).decode("utf-8"))
                    candidate = raw.get("error", raw.get("code")) if isinstance(raw, dict) else None
                    if isinstance(candidate, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", candidate):
                        code = candidate
                except (ValueError, UnicodeError):
                    pass
                finally:
                    error.close()
                if error.code == 401:
                    message = "API Key 未获查询授权。" if key_only else "登录会话失效，请重新登录绑定。"
                elif error.code == 403:
                    message = "接口拒绝访问或当前账号无此权限。"
                elif error.code == 429:
                    message = "接口限流，请稍后刷新。"
                else:
                    message = f"接口请求失败：HTTP {error.code}。"
                raise ApiError(message, error.code, code) from None
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                reason = error.reason if isinstance(error, urllib.error.URLError) else error
                if isinstance(reason, ssl.SSLCertVerificationError):
                    raise ApiError("接口证书验证失败，已停止连接；请检查网络与系统时间。", code="tls_certificate_error") from None
                if attempt + 1 < attempts:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                suffix = "（已重试 2 次）" if attempts > 1 else ""
                raise ApiError(f"接口网络错误或超时{suffix}，请稍后重试。", code="network_error") from None
            except (ValueError, UnicodeError):
                raise ApiError("接口响应不是有效的 JSON。") from None

    def identity(self, expected_email=None):
        data = self.request("/auth/get-session")
        if not isinstance(data, dict) or not isinstance(data.get("user"), dict):
            raise ApiError("登录会话失效或尚未登录 OpenDesign，请重新登录并保存会话。", 401)
        user = data["user"]
        email = user.get("email")
        if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+", email):
            raise ApiError("接口返回的登录邮箱无效，未绑定任何凭证。")
        if expected_email is not None and email.casefold() != expected_email.casefold():
            raise ApiError("实际登录邮箱与所选账号不一致，未绑定任何凭证。")
        return {"userId": identifier(user.get("id")), "email": email.casefold()}

    def current_scope(self, user_id, preferred_workspace=None):
        preferred = {"workspaceId": preferred_workspace} if preferred_workspace else None
        data = object_value(self.request("/v1/workspaces/current", scope=preferred), "工作区")
        context = data.get("currentWorkspaceContext", data.get("workspaceContext", data))
        context = object_value(context, "工作区")
        if context.get("userId") != user_id:
            raise ApiError("工作区返回的用户标识不一致，已停止查询。")
        result = {key: identifier(context.get(key)) for key in ("userId", "workspaceId", "workspaceMemberId")}
        if preferred_workspace and result["workspaceId"] != preferred_workspace:
            raise ApiError("当前工作区与原绑定不同，未覆盖旧数据；请重新绑定。")
        return result

    def snapshot(self, scope, key_only=False):
        workspace = urllib.parse.quote(identifier(scope["workspaceId"]), safe="")
        data = object_value(self.request(f"/v1/workspaces/{workspace}/billing/snapshot", scope=scope, key_only=key_only), "订阅快照")
        if data.get("schemaVersion") != 1 or data.get("billingScopeVersion") != 2:
            raise ApiError("订阅接口版本不匹配，未更新数据。")
        for key in ("workspaceId", "workspaceMemberId"):
            if data.get(key) != scope[key]:
                raise ApiError("订阅接口工作区范围不一致，未更新数据。")
        plan = data.get("plan")
        wallet = object_value(data.get("wallet"), "余额")
        billing_mode = data.get("billingMode", "legacy")
        if billing_mode not in ("shared", "legacy"):
            raise ApiError("不支持的计费接口模式。")
        windows = data.get("designPlanWindows")
        if billing_mode == "legacy":
            summary = object_value(self.request("/v1/billing/summary?fresh=true&timeZone=Asia%2FShanghai", scope=scope, key_only=key_only), "账单汇总")
            for key in ("workspaceId", "workspaceMemberId"):
                if summary.get(key) is not None and summary[key] != scope[key]:
                    raise ApiError("账单汇总范围与绑定不一致。")
            plan = {
                "tier": summary.get("membershipTier"), "interval": summary.get("billingInterval"),
                "currentPeriodEnd": summary.get("subscriptionCurrentPeriodEnd"),
                "cancelAtPeriodEnd": summary.get("subscriptionCancelAtPeriodEnd"),
                "status": summary.get("subscriptionStatus"),
            }
            usage = object_value(self.request(f"/v1/workspaces/{workspace}/billing/coding-plan-usage", scope=scope, key_only=key_only), "额度")
            if usage.get("workspaceId") != scope["workspaceId"]:
                raise ApiError("额度接口范围不一致。")
            windows = usage.get("windows")
        result = {
            "workspace_id": scope["workspaceId"], "member_id": scope["workspaceMemberId"],
            "billing_mode": billing_mode, "tier": None, "interval": None, "expires_at": None,
            "cancel_at_period_end": None, "subscription_status": None,
            "balance_usd": decimal_text(wallet.get("balanceUsd")),
            "windows": normalize_windows(windows), "wallet_breakdown": [],
        }
        if plan is not None:
            plan = object_value(plan, "套餐")
            result.update(tier=plan.get("tier"), interval=plan.get("interval"), expires_at=plan.get("currentPeriodEnd"),
                          cancel_at_period_end=plan.get("cancelAtPeriodEnd"), subscription_status=plan.get("status"))
        breakdown = data.get("walletBreakdown", [])
        if not isinstance(breakdown, list):
            raise ApiError("余额来源结构不匹配。")
        for item in breakdown:
            item = object_value(item, "余额来源")
            result["wallet_breakdown"].append({"kind": item.get("kind"), "amount_usd": decimal_text(item.get("amountUsd")), "expires_at": item.get("expiresAt")})
        return result

    def key_list(self, scope, legacy=False):
        workspace = urllib.parse.quote(identifier(scope["workspaceId"]), safe="")
        path = "/v1/runtime-api-keys" if legacy else f"/v1/workspaces/{workspace}/runtime-api-keys"
        data = object_value(self.request(path, scope=scope), "API Key")
        if legacy:
            if not isinstance(data.get("keys"), list):
                raise ApiError("API Key 返回结构不匹配，未创建 Key。")
            for key in data["keys"]:
                if not isinstance(key, dict):
                    raise ApiError("API Key 返回结构不匹配。")
                for field in ("workspaceId", "workspaceMemberId"):
                    if key.get(field) is not None and key[field] != scope[field]:
                        raise ApiError("已有 Key 的工作区与绑定不一致，请在官网检查。")
            return data, path
        if data.get("schemaVersion") != 1 or data.get("scope") != scope or not isinstance(data.get("keys"), list):
            raise ApiError("API Key 接口范围或版本不匹配；未创建 Key。")
        return data, path

    def create_key(self, scope, path, name):
        legacy = path == "/v1/runtime-api-keys"
        workspace = urllib.parse.quote(identifier(scope["workspaceId"]), safe="")
        if path not in ("/v1/runtime-api-keys", f"/v1/workspaces/{workspace}/runtime-api-keys"):
            raise ApiError("Key 创建路径与绑定工作区不一致。")
        response = object_value(self.request(path, method="POST", body={"name": name, "expiresAt": None}, scope=scope), "创建 Key")
        raw_key = response.get("rawKey")
        key = object_value(response.get("key"), "创建 Key")
        if not isinstance(raw_key, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{16,512}", raw_key):
            raise ApiError("创建响应未包含有效 Key；结果不确定，请在官网检查，勿重复创建。")
        if not legacy and (response.get("schemaVersion") != 1 or response.get("scope") != scope):
            raise ApiError("Key 创建结果范围不匹配，请在官网检查。")
        for field in ("userId", "workspaceId", "workspaceMemberId"):
            if (not legacy or key.get(field) is not None) and key.get(field) != scope[field]:
                raise ApiError("Key 归属不匹配，请在官网检查。")
        return {"api_key": raw_key, "key_id": identifier(key.get("id")), "key_name": name, "key_prefix": str(key.get("keyPrefix", "")), "key_status": key.get("status", "unknown")}

    def revoke_key(self, scope, key_id):
        workspace = urllib.parse.quote(identifier(scope["workspaceId"]), safe="")
        target = urllib.parse.quote(identifier(key_id), safe="")
        response = object_value(self.request(f"/v1/workspaces/{workspace}/runtime-api-keys/{target}/revoke",
                                             method="POST", scope=scope), "撤销 Key")
        key = object_value(response.get("key"), "撤销 Key")
        if (response.get("schemaVersion") != 1 or response.get("scope") != scope
                or key.get("id") != key_id or key.get("status") != "revoked"
                or any(key.get(field) != scope[field] for field in ("userId", "workspaceId", "workspaceMemberId"))):
            raise ApiError("撤销 Key 的结果范围不匹配或结果不确定，请在官网检查，勿重复操作。")
        return {"id": key_id, "status": "revoked"}
