"""导出小红书登录态 Cookie，供 GitHub Actions 的 XHS_COOKIE Secret 使用。

前置：先在本机完成一次扫码登录（点一次「重新抓取」即可）。
用法：python scripts/export_cookies.py
输出：一串 Playwright cookie JSON，复制整行存为 GitHub Secret `XHS_COOKIE`。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from backend import config  # noqa: E402

KEY_COOKIES = ("web_session", "a1", "webId", "xsecappid", "customerClientId",
               "galaxy_creator_session_id", "gid")


def main() -> None:
    cdp = config.cdp_url()
    with sync_playwright() as p:
        if cdp:
            # 优先从真实 Chrome（调试端口 9222）导出登录态
            browser = p.chromium.connect_over_cdp(cdp)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            cookies = ctx.cookies("https://www.xiaohongshu.com")
        else:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(config.BROWSER_PROFILE_DIR), headless=False
            )
            try:
                page = ctx.new_page()
                page.goto("https://www.xiaohongshu.com", timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                cookies = ctx.cookies("https://www.xiaohongshu.com")
            finally:
                ctx.close()

    out = [c for c in cookies if c.get("name") in KEY_COOKIES]
    if not out:
        print("未读取到关键 Cookie，请确认已登录。")
        sys.exit(1)

    payload = json.dumps(out, ensure_ascii=False)
    out_file = Path(__file__).resolve().parent.parent / "data" / "cookie_for_github.txt"
    out_file.write_text(payload, encoding="utf-8")
    print("=" * 60)
    print("请把下面这整行复制为 GitHub Secret：XHS_COOKIE")
    print("=" * 60)
    print(payload)
    print("=" * 60)
    print(f"已写入 UTF-8 文件：{out_file}")


if __name__ == "__main__":
    main()
