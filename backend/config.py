"""路径常量与数据模型（配置/博主/结果）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RESULTS_DIR = DATA_DIR / "results"
FRONTEND_DIR = BASE_DIR / "frontend"
BROWSER_PROFILE_DIR = BASE_DIR / "browser_profile"

BLOGGERS_FILE = DATA_DIR / "bloggers.json"
API_KEYS_FILE = DATA_DIR / "api_keys.json"

for _d in (DATA_DIR, RESULTS_DIR, FRONTEND_DIR, BROWSER_PROFILE_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def resolve_headless(headless: bool) -> bool:
    """若设置了 XHS_HEADED=1，则强制有头模式（排查黑帧/风控时使用）。"""
    if os.environ.get("XHS_HEADED") == "1":
        return False
    return headless


def proxy_config() -> dict | None:
    """可选代理：设置环境变量 XHS_PROXY（如 http://127.0.0.1:7890）以更换出口 IP。"""
    p = os.environ.get("XHS_PROXY", "").strip()
    return {"server": p} if p else None


def cdp_url() -> str | None:
    """连接真实 Chrome 的 DevTools 地址。

    优先用环境变量 XHS_CDP；未设置时自动探测本机 9222 调试端口，
    这样只要用 scripts/start_chrome_debug.ps1 启动了 Chrome 即可直接生效。
    """
    v = os.environ.get("XHS_CDP", "").strip()
    if v:
        return v
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=0.5) as r:
            data = r.read(500).decode("utf-8", "ignore")
        if "webSocketDebuggerUrl" in data or "Browser" in data:
            return "http://127.0.0.1:9222"
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
class Blogger(BaseModel):
    """博主配置。id = 小红书号；home_url 选填（有则优先用链接）。"""

    id: str
    nickname: str
    home_url: Optional[str] = None
    status: str = "待抓取"  # 待抓取 / 抓取中 / 正常 / 遇到风控 / 失败
    last_scraped_at: Optional[str] = None
    error: Optional[str] = None


class ASRKeys(BaseModel):
    """多家云厂商 ASR 凭证（页面可切换厂商）。"""

    aliyun_app_key: Optional[str] = None          # 阿里 DashScope API Key
    aliyun_access_key_id: Optional[str] = None     # 阿里云 AccessKeyId（备用）
    aliyun_access_key_secret: Optional[str] = None  # 阿里云 AccessKeySecret（备用）
    tencent_secret_id: Optional[str] = None
    tencent_secret_key: Optional[str] = None
    tencent_app_id: Optional[str] = None


class ApiKeysConfig(BaseModel):
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    vision_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # 视觉 OCR（DashScope Qwen-VL）
    vision_model: str = "qwen3-vl-plus"     # 视觉 OCR 模型（DeepSeek 官方 API 无视觉模型）
    llm_model: str = "deepseek-chat"       # 品牌抽取 / 归一模型
    asr_provider: str = "aliyun"           # aliyun | tencent
    asr: ASRKeys = Field(default_factory=ASRKeys)


# ---------------------------------------------------------------------------
# 结果 JSON 结构（results/{blogger_id}.json）
# ---------------------------------------------------------------------------
# {
#   "blogger_id": "...",
#   "nickname": "...",
#   "scraped_at": "2026-01-01T12:00:00",
#   "videos": [
#     {
#       "note_id": "...",
#       "title": "...",
#       "published_at": "...",
#       "url": "https://www.xiaohongshu.com/explore/...",
#       "brands": {
#         "品牌名": {
#           "visual": [["00:05", "00:08"], ["00:20", "00:24"]],
#           "asr":    [["00:12", "00:15"]]
#         }
#       }
#     }
#   ]
# }


# ---------------------------------------------------------------------------
# 读写封装（委托 storage，保证原子写 + 线程安全）
# ---------------------------------------------------------------------------
def load_bloggers() -> List[Dict[str, Any]]:
    from backend.storage import read_json
    data = read_json(BLOGGERS_FILE, {"bloggers": []})
    return data.get("bloggers", [])


def save_bloggers(bloggers: List[Dict[str, Any]]) -> None:
    from backend.storage import write_json
    write_json(BLOGGERS_FILE, {"bloggers": bloggers})


def load_api_keys() -> ApiKeysConfig:
    from backend.storage import read_json
    raw = read_json(API_KEYS_FILE, {})
    return ApiKeysConfig.model_validate(raw)


def save_api_keys(cfg: ApiKeysConfig) -> None:
    from backend.storage import write_json
    write_json(API_KEYS_FILE, cfg.model_dump())


def results_path(blogger_id: str) -> Path:
    # 防止路径穿越
    safe = "".join(c for c in blogger_id if c.isalnum() or c in "-_")
    return RESULTS_DIR / f"{safe}.json"


def save_result(blogger_id: str, result: Dict[str, Any]) -> None:
    from backend.storage import write_json
    write_json(results_path(blogger_id), result)


def load_result(blogger_id: str) -> Optional[Dict[str, Any]]:
    from backend.storage import read_json
    path = results_path(blogger_id)
    data = read_json(path, None)
    return data if isinstance(data, dict) else None
