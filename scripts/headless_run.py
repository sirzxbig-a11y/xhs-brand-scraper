"""无头批量抓取入口（供 GitHub Actions 使用）。

- 从环境变量读取 API 配置（GitHub Secrets），写入 data/api_keys.json；
- 登录态通过 XHS_COOKIE 环境变量由 xhs_scraper 注入；
- 全量重抓所有博主（无头模式）。

用法：python scripts/headless_run.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402


def main() -> None:
    cfg = config.ApiKeysConfig(
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        vision_base_url=os.environ.get("VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        vision_model=os.environ.get("VISION_MODEL", "qwen3-vl-plus"),
        llm_model=os.environ.get("LLM_MODEL", "deepseek-chat"),
        asr_provider=os.environ.get("ASR_PROVIDER", "aliyun"),
        asr=config.ASRKeys(
            aliyun_app_key=os.environ.get("ALIYUN_APP_KEY", ""),
            tencent_secret_id=os.environ.get("TENCENT_SECRET_ID", ""),
            tencent_secret_key=os.environ.get("TENCENT_SECRET_KEY", ""),
        ),
    )
    config.save_api_keys(cfg)

    from backend.pipeline import scrape_all_bloggers
    scrape_all_bloggers(headless=True)

    # 若所有博主都失败（常见于数据中心 IP 被风控），让工作流失败以便及时发现
    bloggers = config.load_bloggers()
    if bloggers and all(b.get("status") in ("失败", "遇到风控") for b in bloggers):
        print("[ERROR] 所有博主抓取失败（可能 IP 被风控或未登录），请查看日志。")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
