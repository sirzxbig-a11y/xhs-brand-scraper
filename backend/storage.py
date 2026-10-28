"""线程安全的 JSON 原子读写。"""
from __future__ import annotations

import json
import os
import threading
import tempfile
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with _lock:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        # 先写临时文件再原子替换，避免写一半损坏
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
