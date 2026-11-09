import argparse
import json
import sys
from pathlib import Path

from client import ApiError
from secure_store import Store, now
from session_service import query_session


def main():
    parser = argparse.ArgumentParser(description="使用已绑定的本地凭证实时查询 OpenDesign，不输出 Key 或会话。")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    store = Store(args.data_dir)
    try:
        record = next((record for record in store.all() if record["email"] == args.email.strip().casefold()), None)
        if not record or not record["has_credentials"]:
            raise ApiError("邮箱未绑定；请先在桌面工具完成 Google 登录。")
        secret = store.credentials(record["id"])
        result = query_session(record["email"], secret)
        print(json.dumps({"email": record["email"], "queried_at": now(), "auth_mode": "session", "subscription": result}, ensure_ascii=False, indent=2))
    except Exception as error:
        message = str(error) if isinstance(error, ApiError) else "本地凭证或连接错误，请重新登录绑定。"
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    finally:
        store.close()


if __name__ == "__main__":
    main()
