#!/home/phuong17889/.venvs/flow2api/bin/python3
"""Chrome farm cho flow2api — 1 Chrome resident mỗi tài khoản Google.

Mô hình (tham khảo ứng dụng desktop của đối tác):
  - ACC NÀO ENABLE (is_active=1 trong flow2api) mới mở Chrome acc đó.
    Mỗi acc 1 Chrome riêng (user-data-dir riêng, KHÔNG conflict với Chrome
    thật của máy — đã verify SingletonLock theo từng profile).
  - Người dùng đăng nhập Google đúng 1 lần trong Chrome đó; session sống
    liên tục, Google tự reissue __Secure-1PSIDTS cho chính nó → không bao
    giờ có khái niệm "bản sao cookie stale" như trên server.
  - Harvester định kỳ đọc cookie google.com từ từng Chrome qua CDP
    (remote-debugging-port cục bộ) rồi POST vào /api/tokens/cookie-push
    của flow2api: server validate live + lưu atomic + tự bật lại token
    dead_session. Push làm mới last_st_refresh_at → sweep 30' không bao
    giờ đến hạn → server không còn bắn probe curl rủi ro.
  - Vòng `run` tự mở Chrome cho token vừa được enable; token bị disable
    không bị đóng Chrome tự động (session vẫn sống, đóng tay bằng `kill`).

Cấu hình: browser_farm/farm_config.json
  {"accounts": [{"email": "x@gmail.com", "port": 9301}, ...]}

Dùng:
  python browser_farm.py start            # mở Chrome cho các token đang ENABLE
  python browser_farm.py start --email a@x.com
  python browser_farm.py list             # trạng thái Chrome + token từng acc
  python browser_farm.py harvest          # đẩy cookie 1 lần (test)
  python browser_farm.py run              # vòng lặp: tự mở acc mới enable + harvest
  python browser_farm.py kill             # đóng mọi farm Chrome
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent
FARM_DIR = ROOT / "browser_farm"
CONFIG_PATH = FARM_DIR / "farm_config.json"
CHROME = "/usr/bin/google-chrome"
PUSH_INTERVAL_MIN = 15
START_URL = "https://flow.google.com/projects"

EXAMPLE_CONFIG = {
    "accounts": [
        {"email": "taptonashburn@gmail.com", "port": 9301},
        {"email": "phuong17889@gmail.com", "port": 9302},
        {"email": "chemgiet@gmail.com", "port": 9303},
        {"email": "jav2unet@gmail.com", "port": 9304},
        {"email": "phuongpp689@gmail.com", "port": 9305},
        {"email": "phuongdev89@gmail.com", "port": 9306},
        {"email": "tteen001@gmail.com", "port": 9307},
        {"email": "javfeedcom@gmail.com", "port": 9308},
        {"email": "ozloadcom@gmail.com", "port": 9309},
        {"email": "yanghaipome@gmail.com", "port": 9310},
    ],
    "push_interval_minutes": PUSH_INTERVAL_MIN,
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        FARM_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(EXAMPLE_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Đã tạo config mẫu: {CONFIG_PATH} — sửa email/port rồi chạy lại.")
        sys.exit(2)
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def db_token_map() -> dict:
    """email (lower) -> {id, is_active, ban_reason} — chỉ acc có token trong flow2api."""
    db_path = ROOT / "data" / "flow.db"
    if not db_path.exists():
        return {}
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute("SELECT email, id, is_active, ban_reason FROM tokens").fetchall()
    finally:
        db.close()
    return {
        str(r[0] or "").strip().lower(): {"id": r[1], "is_active": bool(r[2]), "ban_reason": r[3]}
        for r in rows
    }


def api_key() -> str:
    db = sqlite3.connect(ROOT / "data" / "flow.db")
    try:
        row = db.execute("SELECT api_key FROM admin_config LIMIT 1").fetchone()
    finally:
        db.close()
    if not row or not row[0]:
        print("Không đọc được api_key từ data/flow.db")
        sys.exit(1)
    return row[0]


def cdp_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3):
            return True
    except Exception:
        return False


def start_chrome(acct: dict) -> str:
    email, port = acct["email"], int(acct["port"])
    if cdp_alive(port):
        return f"{email}: Chrome đã chạy (port {port}), bỏ qua"
    profile = FARM_DIR / email
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            CHROME,
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--start-minimized",
            START_URL,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return f"{email}: đã launch Chrome (port {port}, profile {profile.name})"


def push_cookies(base_url: str, key: str, acct: dict) -> str:
    email, port = acct["email"], int(acct["port"])
    if not cdp_alive(port):
        return f"{email}: Chrome farm chưa chạy — bỏ qua (chạy `browser_farm.py start`)"
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}", timeout=8000
            )
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            cookies = [
                c
                for c in context.cookies()
                if c["domain"].lstrip(".").endswith("google.com")
            ]
            browser.close()
    except Exception as exc:
        return f"{email}: đọc cookie lỗi - {exc}"
    if not cookies:
        return f"{email}: chưa có cookie google nào (chưa đăng nhập?)"

    payload = json.dumps(
        {
            "cookies": json.dumps(cookies, ensure_ascii=False, separators=(",", ":")),
            "email": email,
            "client_label": "browser_farm",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/tokens/cookie-push",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        status = "OK"
        if data.get("reenabled"):
            status = "OK + TỰ BẬT LẠI token"
        return f"{email}: push {status} (token {data.get('token_id')}, {len(cookies)} cookies)"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:120]
        return f"{email}: push HTTP {exc.code} - {detail}"
    except Exception as exc:
        return f"{email}: push lỗi - {exc}"


def cmd_start(args) -> None:
    """Chỉ mở Chrome cho acc có token ĐANG ENABLE (is_active=1) trong flow2api."""
    cfg = load_config()
    tokens = db_token_map()
    accounts = cfg["accounts"]
    if args.email:
        accounts = [a for a in accounts if a["email"] == args.email]
        if not accounts:
            print(f"Không tìm thấy {args.email} trong config")
            return
    opened = 0
    for acct in accounts:
        info = tokens.get(acct["email"].strip().lower())
        if info is None:
            print(f"{acct['email']}: không có token trong flow2api — bỏ qua")
            continue
        if not info["is_active"]:
            print(
                f"{acct['email']}: token DISABLE (ban={info['ban_reason'] or '-'})"
                " — không mở Chrome (enable token rồi chạy lại `start`)"
            )
            continue
        print(start_chrome(acct))
        opened += 1
        time.sleep(1.5)
    if opened:
        print("Lưu ý: lần đầu, đăng nhập Google trong từng cửa sổ Chrome farm.")
        print("Đăng nhập xong có thể thu nhỏ cửa sổ (taskbar vẫn giữ session sống).")


def cmd_list() -> None:
    cfg = load_config()
    tokens = db_token_map()
    for acct in cfg["accounts"]:
        state = "ĐANG CHẠY" if cdp_alive(int(acct["port"])) else "tắt"
        info = tokens.get(acct["email"].strip().lower())
        if info is None:
            token_state = "không có token trong flow2api"
        elif info["is_active"]:
            token_state = f"ENABLE (token {info['id']})"
        else:
            token_state = f"disable (ban={info['ban_reason'] or '-'})"
        print(f"{acct['email']:32s} port={acct['port']}  {state:9s}  {token_state}")


def harvest_targets(cfg: dict) -> list:
    """Chỉ đẩy cookie cho acc CÓ TOKEN trong flow2api (mọi trạng thái —
    dead_session cần push để tự hồi phục). Acc không có token thì bỏ qua."""
    tokens = db_token_map()
    return [a for a in cfg["accounts"] if a["email"].strip().lower() in tokens]


def cmd_harvest(args) -> None:
    cfg = load_config()
    base_url = args.server.rstrip("/")
    key = api_key()
    for acct in harvest_targets(cfg):
        print(push_cookies(base_url, key, acct))
        time.sleep(2)


def cmd_run(args) -> None:
    cfg = load_config()
    interval = int(cfg.get("push_interval_minutes", PUSH_INTERVAL_MIN))
    base_url = args.server.rstrip("/")
    key = api_key()
    print(f"Harvester chạy mỗi {interval} phút, push vào {base_url}. Ctrl+C để dừng.")
    print("Mỗi vòng: tự mở Chrome cho token vừa ENABLE, đẩy cookie mọi farm Chrome.")
    while True:
        tokens = db_token_map()
        # 1) Token vừa enable mà chưa có Chrome → tự mở (mô hình app tham khảo)
        for acct in cfg["accounts"]:
            info = tokens.get(acct["email"].strip().lower())
            if info and info["is_active"] and not cdp_alive(int(acct["port"])):
                print(time.strftime("[%H:%M:%S]"), start_chrome(acct))
                time.sleep(1.5)
        # 2) Push cookie mọi farm Chrome có token trong DB
        for acct in harvest_targets(cfg):
            print(time.strftime("[%H:%M:%S]"), push_cookies(base_url, key, acct))
            time.sleep(2)
        time.sleep(interval * 60)


def cmd_kill() -> None:
    cfg = load_config()
    killed = 0
    for acct in cfg["accounts"]:
        port = int(acct["port"])
        if not cdp_alive(port):
            continue
        # Đóng bằng pkill theo user-data-dir riêng của acc (an toàn: chỉ đúng profile)
        subprocess.run(
            ["pkill", "-f", f"user-data-dir={FARM_DIR / acct['email']}"],
            check=False,
        )
        killed += 1
    print(f"Đã gửi lệnh đóng {killed} farm Chrome.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chrome farm cho flow2api")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_start = sub.add_parser("start", help="mở Chrome farm")
    p_start.add_argument("--email", help="chỉ mở 1 acc")
    sub.add_parser("list", help="trạng thái các farm Chrome")
    p_h = sub.add_parser("harvest", help="đọc cookie + push 1 lần")
    p_h.add_argument("--server", default="http://127.0.0.1:38000")
    p_r = sub.add_parser("run", help="vòng lặp harvest theo interval")
    p_r.add_argument("--server", default="http://127.0.0.1:38000")
    sub.add_parser("kill", help="đóng mọi farm Chrome")
    args = parser.parse_args()
    if args.cmd == "start":
        cmd_start(args)
    elif args.cmd == "list":
        cmd_list()
    elif args.cmd == "harvest":
        cmd_harvest(args)
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "kill":
        cmd_kill()


if __name__ == "__main__":
    main()
