"""项目启动入口。

用法：
    python run.py            # 启动本地后端，浏览器打开 http://127.0.0.1:8000
    python run.py --port 8080
"""
from __future__ import annotations

import sys
from pathlib import Path

# 保证从任意目录运行时都能正确 import backend / scraper / recognizer
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="小红书视频品牌刮削本地后端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    uvicorn.run("backend.main:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
