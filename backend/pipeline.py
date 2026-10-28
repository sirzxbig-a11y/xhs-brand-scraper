"""编排管道：单个/全量博主 抓取 → 音画捕获 → OCR/ASR → 品牌抽取 → 落盘。

结果按博主覆盖写（results/{blogger_id}.json），严格“博主 → 品牌 → 视频条目”。
同时维护 PROGRESS（每个博主的实时抓取进度），供前端「数据展示」页轮询。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Dict, List

from backend import config
from scraper.xhs_scraper import RiskControlError

log = logging.getLogger(__name__)

# 全局限流：同一时刻只允许一个抓取任务，避免重复触发
_scrape_lock = threading.Lock()

# 正在抓取的博主集合（供前端展示“抓取中”）
RUNNING: set[str] = set()

# 每个博主的实时抓取进度
PROGRESS: Dict[str, Dict[str, Any]] = {}
_progress_lock = threading.Lock()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _update_status(blogger_id: str, status: str, error: str | None = None) -> None:
    bloggers = config.load_bloggers()
    for b in bloggers:
        if b["id"] == blogger_id:
            b["status"] = status
            if error is not None:
                b["error"] = error
            if status == "正常":
                b["last_scraped_at"] = _now()
                b["error"] = None
            config.save_bloggers(bloggers)
            return


# ---------------------------------------------------------------------------
# 进度跟踪
# ---------------------------------------------------------------------------
def set_progress(blogger_id: str, **fields: Any) -> None:
    with _progress_lock:
        cur = PROGRESS.setdefault(blogger_id, {})
        cur.update(fields)
        cur["updated_at"] = _now()


def get_progress() -> Dict[str, Dict[str, Any]]:
    with _progress_lock:
        return {k: dict(v) for k, v in PROGRESS.items()}


def clear_progress(blogger_id: str) -> None:
    with _progress_lock:
        PROGRESS.pop(blogger_id, None)


def _prog(blogger_id: str, **fields: Any) -> None:
    set_progress(blogger_id, **fields)


def scrape_blogger(blogger: Dict[str, Any], headless: bool = True, prompt_login: bool = False) -> None:
    """抓取单个博主并覆盖其结果 JSON。异常被捕获后写入状态。"""
    blogger_id = blogger["id"]
    RUNNING.add(blogger_id)
    _update_status(blogger_id, "抓取中")
    _prog(blogger_id, phase="collecting", stage="收集视频", current=0, total=0,
          message="开始抓取，正在收集最新 30 条视频…", started_at=_now(),
          video_title="", video_url="")
    try:
        _do_scrape(blogger, headless=headless, prompt_login=prompt_login)
        _update_status(blogger_id, "正常")
    except RiskControlError as e:
        log.warning("博主 %s 遇到风控：%s", blogger_id, e)
        _update_status(blogger_id, "遇到风控", str(e))
        _prog(blogger_id, phase="risk", stage="遇到风控", message=str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("博主 %s 抓取失败", blogger_id)
        _update_status(blogger_id, "失败", str(e))
        _prog(blogger_id, phase="error", stage="失败", message=str(e))
    finally:
        RUNNING.discard(blogger_id)


def _do_scrape(blogger: Dict[str, Any], headless: bool, prompt_login: bool) -> None:
    from scraper import capturer, xhs_scraper
    from recognizer import asr, brand, ocr

    blogger_id = blogger["id"]
    api_keys = config.load_api_keys()

    # 1) 收集最新 N 条视频（严格跳过图文笔记）
    _prog(blogger_id, phase="collecting", stage="收集视频", current=0, total=0,
          message=f"正在收集最新 {xhs_scraper.TARGET_VIDEO_COUNT} 条视频…")
    videos = xhs_scraper.collect_videos(blogger, headless=headless, prompt_login=prompt_login)
    if not videos:
        raise RuntimeError(
            "未收集到任何视频：可能未登录、被风控拦截，或主页链接/页面选择器失效。"
            "请确认已扫码登录后重新抓取。"
        )

    # 增量：只处理还没抓过的视频；已有结果全部保留（系统不自动删除）
    existing = config.load_result(blogger_id) or {}
    existing_videos: List[Dict[str, Any]] = existing.get("videos", [])
    existing_ids = {v.get("note_id") for v in existing_videos if v.get("note_id")}
    new_videos = [v for v in videos if v.get("note_id") not in existing_ids]
    if not new_videos:
        log.info("博主 %s 没有新视频，跳过", blogger_id_label(blogger))
        _prog(blogger_id, phase="done", stage="完成", current=0, total=0,
              message="没有新视频，无需抓取")
        return

    # 2) 批量捕获（一个浏览器会话按序处理全部新视频）
    total = len(new_videos)
    _prog(blogger_id, phase="capturing", stage="截屏+录音", current=0, total=total,
          message=f"发现 {total} 条新视频，开始逐条截屏/录音")

    def _on_capture(i: int, count: int, v: Dict[str, Any]) -> None:
        _prog(blogger_id, phase="capturing", stage="截屏+录音", current=i, total=count,
              video_title=v.get("title", ""), video_url=v.get("url", ""),
              message=f"截屏/录音 {i}/{count}")

    captures = capturer.capture_videos(new_videos, headless=headless, on_progress=_on_capture)

    # 3) 逐条识别 + 品牌抽取
    result_videos: List[Dict[str, Any]] = []
    for idx, (video, capture) in enumerate(zip(new_videos, captures), 1):
        if capture is None:
            _prog(blogger_id, phase="recognizing", stage="跳过", current=idx, total=total,
                  video_title=video.get("title", ""), video_url=video.get("url", ""),
                  message=f"第 {idx}/{total} 条捕获失败，已跳过")
            continue
        log.info("[%s] 识别视频 %d/%d：%s", blogger_id_label(blogger), idx, total, video.get("url"))
        duration = capture.get("duration", 0)
        screenshots = capture.get("screenshots", [])  # [(t_sec, path)]
        audio_wav = capture.get("audio_wav")

        # 3.1 画面 OCR（每张关键帧 → 文本 + 时间戳）
        _prog(blogger_id, phase="recognizing", stage="画面 OCR", current=idx, total=total,
              video_title=video.get("title", ""), video_url=video.get("url", ""),
              message=f"画面 OCR {idx}/{total}（共 {len(screenshots)} 帧）")
        ocr_items: List[Dict[str, Any]] = []
        for t_sec, img_path in screenshots:
            text = ocr.ocr_image(api_keys, img_path)
            if text:
                ocr_items.append({"t": t_sec, "text": text})

        # 3.2 口播 ASR（含句级时间戳）
        _prog(blogger_id, stage="口播 ASR", message=f"口播 ASR {idx}/{total}")
        asr_items: List[Dict[str, Any]] = []
        if audio_wav:
            asr_items = asr.transcribe(api_keys, audio_wav)

        # 3.3 品牌抽取 + 归一 + 时间对齐
        _prog(blogger_id, stage="品牌抽取", message=f"品牌抽取 {idx}/{total}")
        brands = brand.extract_brands(api_keys, ocr_items, asr_items, duration)

        result_videos.append({
            "note_id": video.get("note_id"),
            "title": video.get("title", ""),
            "published_at": video.get("published_at", ""),
            "url": video.get("url", ""),
            "brands": brands,
        })

    # 合并：新视频在前，原有结果全部保留（不自动删除）
    merged = result_videos + existing_videos
    result = {
        "blogger_id": blogger["id"],
        "nickname": blogger["nickname"],
        "scraped_at": _now(),
        "videos": merged,
    }
    config.save_result(blogger["id"], result)
    _prog(blogger_id, phase="done", stage="完成", current=len(result_videos), total=total,
          message=f"完成：新增 {len(result_videos)} 条，累计 {len(merged)} 条")
    log.info("博主 %s 完成：新增 %d 条，累计 %d 条",
             blogger["nickname"], len(result_videos), len(merged))


def scrape_all_bloggers(headless: bool = True) -> None:
    """全量重抓所有未删除博主（定时任务入口）。"""
    if not _scrape_lock.acquire(blocking=False):
        log.info("已有抓取任务在运行，跳过本次触发")
        return
    try:
        for blogger in config.load_bloggers():
            scrape_blogger(blogger, headless=headless)
    finally:
        _scrape_lock.release()


def scrape_blogger_async(blogger_id: str, headless: bool = True) -> bool:
    """在后台线程触发单个博主抓取（供前端“手动重新抓取”按钮调用）。

    手动触发时允许 prompt_login=True：未登录会弹出有头浏览器扫码。
    """
    blogger = next((b for b in config.load_bloggers() if b["id"] == blogger_id), None)
    if blogger is None:
        return False
    if blogger_id in RUNNING:
        return False
    if get_login_state().get("state") == "waiting":
        return False

    def _run() -> None:
        with _scrape_lock:
            scrape_blogger(blogger, headless=headless, prompt_login=True)

    threading.Thread(target=_run, daemon=True, name=f"scrape-{blogger_id}").start()
    return True


def blogger_id_label(blogger: Dict[str, Any]) -> str:
    return blogger.get("nickname") or blogger.get("id") or "?"


# ---------------------------------------------------------------------------
# 「打开浏览器登录」流程
# ---------------------------------------------------------------------------
LOGIN_STATE: Dict[str, Any] = {"state": "idle", "message": "", "updated_at": ""}
_login_state_lock = threading.Lock()


def get_login_state() -> Dict[str, Any]:
    with _login_state_lock:
        return dict(LOGIN_STATE)


def _set_login_state(state: str, message: str = "") -> None:
    with _login_state_lock:
        LOGIN_STATE.update({"state": state, "message": message, "updated_at": _now()})
    log.info("登录流程：%s %s", state, message)


def open_login_browser(auto_scrape: bool = True) -> bool:
    """打开有头浏览器等用户扫码登录；成功后可选自动开始全量抓取。"""
    import time

    if RUNNING:
        _set_login_state("busy", "有抓取任务在运行，请等待完成后再登录")
        return False
    with _login_state_lock:
        if LOGIN_STATE.get("state") == "waiting":
            return False

    def _run() -> None:
        from playwright.sync_api import sync_playwright
        from scraper import xhs_scraper

        _set_login_state("waiting", "已打开浏览器，请完成验证码并扫码登录（最长等待 5 分钟）")
        logged_in = False
        cdp = config.cdp_url()
        try:
            with sync_playwright() as p:
                if cdp:
                    browser = p.chromium.connect_over_cdp(cdp)
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    page = context.new_page()
                    try:
                        page.goto("https://www.xiaohongshu.com", timeout=60000, wait_until="domcontentloaded")
                        try:
                            xhs_scraper._check_ip_block(page)
                        except xhs_scraper.RiskControlError as e:
                            _set_login_state("blocked", str(e))
                            return
                        if xhs_scraper._is_logged_in(page):
                            logged_in = True
                            _set_login_state("logged_in", "已检测到真实浏览器的登录态")
                        else:
                            _set_login_state(
                                "error",
                                "未检测到真实浏览器登录态：请先在你自己的 Chrome 里登录小红书，再点一次「打开浏览器登录」",
                            )
                    finally:
                        page.close()
                else:
                    context = xhs_scraper._launch(p, headless=False)
                    page = context.new_page()
                    page.goto("https://www.xiaohongshu.com", timeout=60000, wait_until="domcontentloaded")
                    for i in range(300):
                        time.sleep(1)
                        try:
                            xhs_scraper._check_ip_block(page)
                        except xhs_scraper.RiskControlError as e:
                            _set_login_state("blocked", str(e))
                            break
                        if xhs_scraper._is_logged_in(page):
                            logged_in = True
                            _set_login_state("logged_in", "登录成功")
                            break
                        if i % 30 == 0 and i > 0:
                            _set_login_state("waiting", f"等待扫码登录…（已等待 {i} 秒）")
                    context.close()
        except Exception as e:  # noqa: BLE001
            _set_login_state("error", f"登录过程出错：{e}")
            return

        if not logged_in:
            return
        if auto_scrape:
            _set_login_state("scraping", "登录成功，开始抓取…")
            scrape_all_bloggers(headless=True)
            statuses = {b.get("status") for b in config.load_bloggers()}
            if "遇到风控" in statuses:
                _set_login_state("blocked", "登录成功，但抓取遇到风控（IP 被限），请切换网络后重试")
            elif "失败" in statuses:
                _set_login_state("error", "登录成功，但抓取失败，请查看「运行日志」")
            else:
                _set_login_state("done", "抓取结束")

    threading.Thread(target=_run, daemon=True, name="login-browser").start()
    return True
