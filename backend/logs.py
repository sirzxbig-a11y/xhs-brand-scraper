"""内存日志缓冲 + 落盘：供前端「运行日志」页面实时查看后端运行情况。"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Any, Dict, List

_MAX_LINES = 2000
_buffer: deque = deque(maxlen=_MAX_LINES)
_seq = 0
_lock = threading.Lock()


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _seq
        # 过滤掉状态轮询产生的访问日志，避免刷屏
        if record.name == "uvicorn.access":
            return
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        with _lock:
            _seq += 1
            _buffer.append({
                "id": _seq,
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": record.levelname,
                "name": record.name,
                "msg": msg,
            })


_handler = _BufferHandler()
_handler.setLevel(logging.INFO)


def install() -> None:
    """把缓冲 handler（以及落盘 handler）挂到 root logger，幂等。"""
    root = logging.getLogger()
    if _handler not in root.handlers:
        root.addHandler(_handler)

    try:
        from logging.handlers import RotatingFileHandler
        from backend import config

        fh = RotatingFileHandler(
            config.DATA_DIR / "app.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        if fh not in root.handlers:
            root.addHandler(fh)
    except Exception:
        pass


def get_logs(since: int = 0) -> List[Dict[str, Any]]:
    with _lock:
        return [e for e in _buffer if e["id"] > since]


def last_id() -> int:
    with _lock:
        return _seq
