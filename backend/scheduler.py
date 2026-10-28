"""内置每日定时任务：只要后端常驻，每天自动全量重抓所有未删除博主。"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)

# 默认每天 03:00（Asia/Shanghai）全量重抓；可在 HTML 里看到“下次运行时间”
DAILY_HOUR = 3
DAILY_MINUTE = 0

_scheduler: BackgroundScheduler | None = None


def _job() -> None:
    from backend.pipeline import scrape_all_bloggers
    log.info("触发每日定时全量重抓")
    scrape_all_bloggers()


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _scheduler.add_job(
        _job,
        CronTrigger(hour=DAILY_HOUR, minute=DAILY_MINUTE, timezone="Asia/Shanghai"),
        id="daily_scrape",
        name="每日全量重抓",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    log.info("定时任务已启动：每天 %02d:%02d", DAILY_HOUR, DAILY_MINUTE)
    return _scheduler


def next_run_time() -> str | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job("daily_scrape")
    if job is None or job.next_run_time is None:
        return None
    return job.next_run_time.isoformat()
