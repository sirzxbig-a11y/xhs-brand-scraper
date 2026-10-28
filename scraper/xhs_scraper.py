"""小红书网页版抓取：登录态持久化 + 收集最新 30 条视频笔记。

说明：
- 使用 Playwright 持久化上下文，登录态保存在 browser_profile/ 目录，可复用。
- 首次未登录时：手动触发（prompt_login=True）会弹出有头浏览器让用户扫码；
  定时任务（prompt_login=False）遇到未登录会抛 LoginRequired，标记为“失败”。
- IP/安全风控（错误码 300012）会被识别为「遇到风控」并中断当前博主。
- 小红书 DOM 结构会不定期变化，选择器集中在本文件顶部，便于维护。
"""
from __future__ import annotations

import logging
import os
import re
import time
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from backend import config

log = logging.getLogger(__name__)

TARGET_VIDEO_COUNT = 5  # 每次抓取最新 5 条视频（每日增量补齐）

# ---------------------------------------------------------------- 异常
class LoginRequired(Exception):
    """未登录，需先扫码登录。"""


class RiskControlError(Exception):
    """遇到风控 / 验证码 / IP 安全限制。"""


# ---------------------------------------------------------------- 选择器（可能需随小红书改版更新）
SELECTORS = {
    "login_check_cookie": "web_session",          # 登录态关键 cookie
    "note_cards": "a[href*='/explore/'], a[href*='/discovery/item/']",
    "risk_markers": [
        "安全验证", "验证码", "滑动验证", "请完成验证", "访问过于频繁",
        "登录过期", "安全限制", "IP存在风险", "切换可靠网络", "300012", "verify",
    ],
}

_RISK_MARKERS = SELECTORS["risk_markers"]


def _is_logged_in(page) -> bool:
    try:
        cookies = page.context.cookies()
    except Exception:
        return False
    return any(c.get("name") == SELECTORS["login_check_cookie"] and c.get("value") for c in cookies)


def _page_url(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


def _check_risk(page) -> None:
    """页面若出现风控/验证码/安全限制，直接抛出，中断当前博主。"""
    url = _page_url(page)
    if "website-login/error" in url or "error_code=300012" in url:
        raise RiskControlError(
            "IP 被风控：小红书提示「IP存在风险，请切换可靠网络环境后重试」（错误码 300012）"
        )
    try:
        text = page.inner_text("body")[:2000]
    except Exception:
        return
    for marker in _RISK_MARKERS:
        if marker in text:
            raise RiskControlError(f"页面出现风控标识：{marker}")


def _check_ip_block(page) -> None:
    """只判断“IP 安全限制”，用于登录阶段：避免把普通登录验证码误判为风控。"""
    url = _page_url(page)
    if "website-login/error" in url or "error_code=300012" in url:
        raise RiskControlError(
            "IP 被风控：小红书提示「IP存在风险，请切换可靠网络环境后重试」（错误码 300012）"
        )
    try:
        text = page.inner_text("body")[:500]
    except Exception:
        return
    if "IP存在风险" in text or "安全限制" in text:
        raise RiskControlError("IP 被风控：小红书提示「IP存在风险，请切换可靠网络环境后重试」")


def _launch(playwright, headless: bool):
    proxy = config.proxy_config()
    kwargs = {"proxy": proxy} if proxy else {}
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(config.BROWSER_PROFILE_DIR),
        headless=config.resolve_headless(headless),
        args=[
            "--autoplay-policy=no-user-gesture-required",
            "--disable-blink-features=AutomationControlled",
        ],
        viewport={"width": 1280, "height": 900},
        **kwargs,
    )
    return context


def _ensure_login(page, headless: bool) -> None:
    page.goto("https://www.xiaohongshu.com", timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    _check_ip_block(page)
    if _is_logged_in(page):
        return
    if headless:
        # 未登录：抛给上层，由 prompt_login 决定是否弹出有头浏览器扫码
        raise LoginRequired("未检测到登录态，请先完成扫码登录")
    # 有头模式（首次登录）：页面可能先出现验证码/登录弹窗，交由用户手动完成
    log.info("请在浏览器中完成验证码并扫码登录（最长等待 3 分钟）……")
    for i in range(180):
        time.sleep(1)
        try:
            if _is_logged_in(page):
                log.info("登录成功，登录态已持久化")
                return
        except Exception:
            pass
        if i % 15 == 0:
            try:
                text = page.inner_text("body")[:500]
                if any(m in text for m in ("验证码", "安全验证", "滑动", "请完成验证")):
                    log.info("检测到验证码，请在浏览器中手动完成验证后再扫码")
            except Exception:
                pass
    raise RiskControlError("扫码登录超时（3 分钟）")


def _home_url(blogger: dict) -> str | None:
    url = (blogger.get("home_url") or "").strip()
    if not url.startswith("http"):
        return None
    # 去掉 xsec_token 等临时查询参数，避免过期导致打开空白页
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _resolve_profile_url(page, blogger: dict) -> str:
    """有主页链接用链接，否则按昵称搜索用户。"""
    url = _home_url(blogger)
    if url:
        page.goto(url, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        _check_risk(page)
        return url
    keyword = quote(blogger.get("nickname", ""))
    page.goto(f"https://www.xiaohongshu.com/search_result?keyword={keyword}&type=user", timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    _check_risk(page)
    first = page.query_selector("a[href*='/user/profile/']")
    if first is None:
        raise RiskControlError("按昵称未搜索到用户，请填写主页链接")
    href = first.get_attribute("href") or ""
    target = "https://www.xiaohongshu.com" + href if href.startswith("/") else href
    page.goto(target, timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    _check_risk(page)
    return target


def _is_video_card(el) -> bool:
    """判断笔记卡片是否为视频（含播放图标 / 时长 / video 元素）。"""
    try:
        if el.query_selector("video, .play-icon, .icon-play, i.play-icon, .note-play-icon"):
            return True
        text = el.inner_text()
        if re.search(r"\d{1,2}:\d{2}", text):
            return True
    except Exception:
        pass
    return False


def _scroll(page, times: int = 10) -> None:
    for _ in range(times):
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(800)


def _inject_cookie(context) -> None:
    """从环境变量 XHS_COOKIE 注入登录态（供 GitHub Actions / 无头环境复用）。

    支持两种格式：
    - Playwright cookie JSON 数组：[{"name":"web_session","value":"...",...}]
    - 简单键值串："web_session=xxx; a1=yyy"
    """
    raw = os.environ.get("XHS_COOKIE", "").strip()
    if not raw:
        return
    cookies = []
    if raw.startswith("["):
        import json
        try:
            cookies = json.loads(raw)
        except json.JSONDecodeError:
            cookies = []
    else:
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                k, _, v = part.partition("=")
                cookies.append({"name": k.strip(), "value": v.strip(),
                                "domain": ".xiaohongshu.com", "path": "/"})
    if cookies:
        try:
            context.add_cookies(cookies)
            log.info("已从 XHS_COOKIE 注入 %d 个 cookie", len(cookies))
        except Exception as e:  # noqa: BLE001
            log.warning("注入 cookie 失败：%s", e)


def _note_id_from_card(el) -> str | None:
    """从笔记卡片中提取 note_id（兼容 explore 链接与 profile/{user}/{note} 形式）。"""
    a = el.query_selector("a[href*='/explore/']")
    if a:
        m = re.search(r"/explore/([0-9a-zA-Z]+)", a.get_attribute("href") or "")
        if m:
            return m.group(1)
    for a in el.query_selector_all("a[href*='/user/profile/']"):
        href = a.get_attribute("href") or ""
        m = re.search(r"/user/profile/[^/]+/([0-9a-zA-Z]+)", href)
        if m:
            return m.group(1)
    return None


def _card_url(el) -> str | None:
    """取卡片的真实详情页链接（优先带 xsec_token 的封面链接）。"""
    a = el.query_selector("a.cover[href*='/user/profile/']")
    if not a:
        a = el.query_selector("a[href*='/user/profile/']")
    if not a:
        a = el.query_selector("a[href*='/explore/']")
    if not a:
        return None
    href = (a.get_attribute("href") or "").strip()
    if not href:
        return None
    return "https://www.xiaohongshu.com" + href if href.startswith("/") else href


def _collect_from_profile(page) -> list[dict]:
    """在已打开的主页上滚动收集视频卡片。"""
    videos: list[dict] = []
    seen: set[str] = set()
    for _ in range(30):  # 最多滚动 30 轮
        _check_risk(page)
        cards = page.query_selector_all("section.note-item")
        if not cards:
            cards = page.query_selector_all(SELECTORS["note_cards"])
        for el in cards:
            note_id = _note_id_from_card(el)
            if not note_id or note_id in seen:
                continue
            if not _is_video_card(el):
                continue
            seen.add(note_id)
            title = ""
            title_el = el.query_selector("a.title")
            if title_el:
                title = title_el.inner_text().strip()
            if not title:
                img = el.query_selector("img")
                if img:
                    title = (img.get_attribute("alt") or "").strip()
            videos.append({
                "note_id": note_id,
                "title": title,
                "published_at": "",
                "url": _card_url(el) or f"https://www.xiaohongshu.com/explore/{note_id}",
            })
            if len(videos) >= TARGET_VIDEO_COUNT:
                break
        if len(videos) >= TARGET_VIDEO_COUNT:
            break
        _scroll(page, 3)
    return videos


def collect_videos(blogger: dict, headless: bool = True, prompt_login: bool = False) -> list[dict]:
    """返回博主最新 30 条视频的元信息列表。

    设置 XHS_CDP 时连接你自己真实的 Chrome（复用真实登录态/指纹，降低被识别概率）。
    """
    headless = config.resolve_headless(headless)
    cdp = config.cdp_url()
    videos: list[dict] = []
    with sync_playwright() as p:
        browser = None
        if cdp:
            try:
                browser = p.chromium.connect_over_cdp(cdp)
            except Exception as e:  # noqa: BLE001
                log.warning("连接调试 Chrome 失败（%s），回退独立浏览器", e)
        if browser is not None:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            try:
                _ensure_login(page, headless=False)
                _resolve_profile_url(page, blogger)
                videos = _collect_from_profile(page)
            finally:
                try:
                    page.close()
                except Exception:
                    pass
        else:
            context = _launch(p, headless=headless)
            _inject_cookie(context)
            try:
                page = context.new_page()
                try:
                    _ensure_login(page, headless=headless)
                except LoginRequired:
                    context.close()
                    if not prompt_login:
                        raise
                    # 手动触发且未登录：改有头模式，弹出浏览器扫码
                    log.info("改为有头模式，等待扫码登录……")
                    context = _launch(p, headless=False)
                    _inject_cookie(context)
                    page = context.new_page()
                    _ensure_login(page, headless=False)

                _resolve_profile_url(page, blogger)
                videos = _collect_from_profile(page)
            finally:
                try:
                    context.close()
                except Exception:
                    pass

    log.info("博主 %s 收集到 %d 条视频", blogger.get("nickname"), len(videos))
    return videos
