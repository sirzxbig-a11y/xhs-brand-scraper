"""FastAPI 本地后端：读写博主配置 / API Key，触发抓取，向前端提供数据。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import config
from backend import logs as log_view
from backend import scheduler
from backend.pipeline import RUNNING, get_login_state, get_progress, open_login_browser, scrape_blogger_async

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log_view.install()
log = logging.getLogger("backend")

app = FastAPI(title="小红书视频品牌刮削", version="0.1.0")


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------
class BloggerIn(BaseModel):
    id: str
    nickname: str
    home_url: str | None = None


class ApiKeysIn(BaseModel):
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    vision_model: str = "deepseek-vl2"
    llm_model: str = "deepseek-chat"
    asr_provider: str = "aliyun"
    asr: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# 博主 CRUD
# ---------------------------------------------------------------------------
@app.get("/api/bloggers")
def list_bloggers() -> List[Dict[str, Any]]:
    return config.load_bloggers()


@app.post("/api/bloggers")
def add_blogger(body: BloggerIn) -> Dict[str, Any]:
    bloggers = config.load_bloggers()
    if any(b["id"] == body.id for b in bloggers):
        raise HTTPException(status_code=409, detail="该小红书号已存在")
    b = body.model_dump()
    b["status"] = "待抓取"
    b["last_scraped_at"] = None
    b["error"] = None
    bloggers.append(b)
    config.save_bloggers(bloggers)
    return b


@app.put("/api/bloggers/{blogger_id}")
def update_blogger(blogger_id: str, body: BloggerIn) -> Dict[str, Any]:
    bloggers = config.load_bloggers()
    for b in bloggers:
        if b["id"] == blogger_id:
            b["nickname"] = body.nickname
            b["home_url"] = body.home_url
            config.save_bloggers(bloggers)
            return b
    raise HTTPException(status_code=404, detail="博主不存在")


@app.delete("/api/bloggers/{blogger_id}")
def delete_blogger(blogger_id: str) -> Dict[str, Any]:
    bloggers = config.load_bloggers()
    remain = [b for b in bloggers if b["id"] != blogger_id]
    if len(remain) == len(bloggers):
        raise HTTPException(status_code=404, detail="博主不存在")
    config.save_bloggers(remain)
    # 同时删除结果（可选：保留历史。这里按“删除即清理”处理）
    path = config.results_path(blogger_id)
    if path.exists():
        path.unlink()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 抓取控制
# ---------------------------------------------------------------------------
@app.post("/api/bloggers/{blogger_id}/rescrape")
def rescrape_blogger(blogger_id: str) -> Dict[str, Any]:
    if blogger_id in RUNNING:
        return {"started": False, "reason": "该博主正在抓取中"}
    ok = scrape_blogger_async(blogger_id)
    return {"started": ok}


@app.get("/api/status")
def status() -> Dict[str, Any]:
    return {
        "running": sorted(RUNNING),
        "next_daily_run": scheduler.next_run_time(),
    }


@app.get("/api/logs")
def api_logs(since: int = 0) -> Dict[str, Any]:
    return {"logs": log_view.get_logs(since), "last_id": log_view.last_id()}


@app.get("/api/progress")
def api_progress() -> Dict[str, Any]:
    return get_progress()


@app.post("/api/login")
def api_login() -> Dict[str, Any]:
    started = open_login_browser(auto_scrape=True)
    return {"started": started, "login": get_login_state()}


@app.get("/api/login")
def api_login_state() -> Dict[str, Any]:
    return get_login_state()


# ---------------------------------------------------------------------------
# API Key 配置
# ---------------------------------------------------------------------------
@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    return config.load_api_keys().model_dump()


@app.post("/api/config")
def set_config(body: ApiKeysIn) -> Dict[str, Any]:
    cfg = config.ApiKeysConfig.model_validate(body.model_dump())
    config.save_api_keys(cfg)
    return cfg.model_dump()


# ---------------------------------------------------------------------------
# 结果数据
# ---------------------------------------------------------------------------
@app.get("/api/results/{blogger_id}")
def get_result(blogger_id: str) -> Dict[str, Any]:
    data = config.load_result(blogger_id)
    if data is None:
        raise HTTPException(status_code=404, detail="该博主暂无结果")
    return data


@app.delete("/api/results/{blogger_id}/videos/{note_id}")
def delete_video(blogger_id: str, note_id: str) -> Dict[str, Any]:
    """手动删除某条视频的识别结果（系统不会自动删除）。"""
    data = config.load_result(blogger_id)
    if data is None:
        raise HTTPException(status_code=404, detail="该博主暂无结果")
    videos = data.get("videos", [])
    remain = [v for v in videos if v.get("note_id") != note_id]
    if len(remain) == len(videos):
        raise HTTPException(status_code=404, detail="视频不存在")
    data["videos"] = remain
    config.save_result(blogger_id, data)
    return {"ok": True, "remaining": len(remain)}


@app.get("/api/results")
def list_results() -> List[Dict[str, Any]]:
    out = []
    for b in config.load_bloggers():
        data = config.load_result(b["id"])
        out.append({
            "blogger_id": b["id"],
            "nickname": b["nickname"],
            "status": b["status"],
            "last_scraped_at": b["last_scraped_at"],
            "has_result": data is not None,
            "video_count": len(data.get("videos", [])) if data else 0,
        })
    return out


# ---------------------------------------------------------------------------
# 前端静态文件
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=str(config.FRONTEND_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(config.FRONTEND_DIR / "index.html"))


def _reset_stale_statuses() -> None:
    """后端重启后，清理上次被中断遗留的「抓取中」状态，避免页面显示假进度。"""
    bloggers = config.load_bloggers()
    stale = [b for b in bloggers if b.get("status") == "抓取中"]
    if not stale:
        return
    for b in stale:
        b["status"] = "待抓取"
    config.save_bloggers(bloggers)
    log.info("已重置 %d 个遗留的「抓取中」状态", len(stale))


@app.on_event("startup")
def _startup() -> None:
    _reset_stale_statuses()
    scheduler.start_scheduler()
    log.info("后端已启动：http://127.0.0.1:8000")
