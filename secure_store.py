import ctypes
import json
import os
import re
import sqlite3
import uuid
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path


TZ = timezone(timedelta(hours=8))


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("凭证加密需要 Windows DPAPI；不允许以明文保存。")
    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), source_buffer)
    output = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError("无法解密本地凭证，请使用原 Windows 账户重新登录。" if decrypt else "凭证加密失败，未保存明文。")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(output.pbData)


def encode(secret):
    return crypt(json.dumps(secret, ensure_ascii=False).encode("utf-8"))


def decode(blob):
    return json.loads(crypt(bytes(blob), decrypt=True).decode("utf-8")) if blob else {}


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "realtime.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, label TEXT NOT NULL,
            secrets BLOB, public TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT '未绑定',
            last_success TEXT, last_attempt TEXT, created_at TEXT NOT NULL
        )""")
        self.db.commit()

    def profile(self, account_id):
        if not re.fullmatch(r"[0-9a-f]{32}", account_id):
            raise ValueError("账号标识无效。")
        root = (self.root / "profiles").resolve()
        target = (root / account_id).resolve()
        if target.parent != root:
            raise ValueError("浏览器目录不在预期范围内。")
        return target

    def add(self, email, label=""):
        email = email.strip().casefold()
        if not re.fullmatch(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+", email):
            raise ValueError("请输入有效的 Google 账号邮箱。")
        if len(email) > 254 or len(label) > 200:
            raise ValueError("邮箱或备注过长。")
        account_id = uuid.uuid4().hex
        try:
            with self.db:
                self.db.execute("INSERT INTO accounts (id,email,label,created_at) VALUES (?,?,?,?)",
                                (account_id, email, label.strip(), now()))
        except sqlite3.IntegrityError as error:
            raise ValueError("这个邮箱已存在，请选择已有账号。") from error
        return account_id

    @staticmethod
    def public_row(row):
        record = dict(row)
        record["has_credentials"] = bool(record.pop("secrets"))
        record["public"] = json.loads(record["public"])
        return record

    def all(self):
        return [self.public_row(row) for row in self.db.execute("SELECT * FROM accounts ORDER BY email")]

    def get(self, account_id):
        row = self.db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not row:
            raise ValueError("账号不存在。")
        return self.public_row(row)

    def credentials(self, account_id):
        row = self.db.execute("SELECT secrets FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not row:
            raise ValueError("账号不存在。")
        return decode(row[0])

    def save_credentials(self, account_id, secret):
        blob = encode(secret)
        with self.db:
            self.db.execute("UPDATE accounts SET secrets=? WHERE id=?", (blob, account_id))

    def save_key_inventory(self, account_id, public):
        with self.db:
            self.db.execute("UPDATE accounts SET public=? WHERE id=?",
                            (json.dumps(public, ensure_ascii=False), account_id))

    def has_api_key(self, account_id):
        try:
            value = self.credentials(account_id).get("api_key")
            return isinstance(value, str) and bool(value.strip())
        except (RuntimeError, ValueError, TypeError):
            return False

    def save_api_key(self, account_id, value):
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{16,512}", value):
            raise ValueError("请输入完整 API Key，不要输入遮罩、空格或换行。")
        secret = self.credentials(account_id)
        if not secret.get("cookies") or not isinstance(secret.get("scope"), dict):
            raise ValueError("请先保存此账号的登录会话，再保存已有 Key。")
        # Import only; leave the validated session, scope and quota cache untouched.
        for field in ("key_id", "key_name", "key_prefix", "key_status"):
            secret.pop(field, None)
        secret.update(api_key=value, key_source="imported")
        self.save_credentials(account_id, secret)

    def save_created_key(self, account_id, result):
        record = self.get(account_id)
        secret = self.credentials(account_id)
        if result.get("email") != record["email"] or result.get("scope") != secret.get("scope"):
            raise ValueError("新 Key 归属与本地账号不一致，未保存或撤销任何 Key。")
        created = result["created"]
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{16,512}", created.get("api_key", ""))
                or created.get("key_status") != "active" or not created.get("key_id")):
            raise ValueError("新 Key 格式或状态不匹配，未撤销旧 Key。")
        if secret.get("api_key"):
            backups = list(secret.get("previous_keys", []))
            backups.append({field: secret.get(field) for field in ("api_key", "key_id", "key_name", "key_status", "key_source")})
            secret["previous_keys"] = backups
        secret.update(created, key_source="created")
        blob = encode(secret)
        public = dict(record["public"])
        public["key_operation"] = {"state": "saved", "new_id": created["key_id"], "updated_at": now()}
        with self.db:
            self.db.execute("UPDATE accounts SET secrets=?, public=? WHERE id=?",
                            (blob, json.dumps(public, ensure_ascii=False), account_id))

    def bind_session(self, account_id, email, secret, public):
        email = email.strip().casefold()
        if len(email) > 254 or not re.fullmatch(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+", email):
            raise ValueError("接口返回的邮箱无效。")
        if secret.get("auth_mode") != "session" or not secret.get("cookies") or not secret.get("scope"):
            raise ValueError("没有通过核验的登录会话。")
        blob = encode(secret)
        old_public = self.get(account_id)["public"]
        if "key_operation" in old_public and "key_operation" not in public:
            public = {**public, "key_operation": old_public["key_operation"]}
        try:
            # Identity correction, encrypted credentials and snapshot commit together.
            with self.db:
                row = self.db.execute("SELECT email,secrets FROM accounts WHERE id=?", (account_id,)).fetchone()
                if not row:
                    raise ValueError("账号不存在。")
                if row["secrets"] and row["email"] != email:
                    raise ValueError("已有绑定的账号不能改绑到其他邮箱，请添加新账号。")
                self.db.execute("""UPDATE accounts SET email=?,secrets=?,public=?,status=?,
                    last_success=?,last_attempt=? WHERE id=?""",
                    (email, blob, json.dumps(public, ensure_ascii=False), "接口查询成功 / 登录会话",
                     now(), now(), account_id))
        except sqlite3.IntegrityError as error:
            raise ValueError("实际登录邮箱已在列表中，请选中已有账号重新登录；未覆盖任何凭证。") from error

    def update(self, account_id, status, public=None, success=False):
        if public is not None:
            old = self.get(account_id)["public"]
            public = dict(public)
            for field in ("key_operation", "key_inventory"):
                if field not in public and field in old:
                    public[field] = old[field]
        with self.db:
            self.db.execute("UPDATE accounts SET status=?,last_attempt=? WHERE id=?", (status, now(), account_id))
            if public is not None:
                self.db.execute("UPDATE accounts SET public=? WHERE id=?", (json.dumps(public, ensure_ascii=False), account_id))
            if success:
                self.db.execute("UPDATE accounts SET last_success=? WHERE id=?", (now(), account_id))

    def forget(self, account_id):
        with self.db:
            self.db.execute("UPDATE accounts SET secrets=NULL,public='{}',status='已解除本地绑定',last_success=NULL,last_attempt=NULL WHERE id=?", (account_id,))

    def close(self):
        self.db.close()
