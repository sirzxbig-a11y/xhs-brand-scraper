"""画面 OCR：调用 DeepSeek 视觉 API（OpenAI 兼容 /chat/completions）。"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)

OCR_PROMPT = (
    "请识别这张视频画面中出现的所有品牌名称或品牌 Logo 文字。"
    "只输出品牌名，多个用顿号“、”分隔；没有识别到品牌则只输出“无”。"
)


def ocr_image(api_keys, image_path: str) -> str:
    # 视觉 OCR 走 DashScope Qwen-VL（DeepSeek 官方 API 仅支持文本模型）
    key = api_keys.asr.aliyun_app_key or api_keys.deepseek_api_key
    if not key:
        log.warning("未配置视觉 OCR 的 API Key，跳过 OCR")
        return ""
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    data_uri = f"data:image/png;base64,{b64}"

    payload = {
        "model": api_keys.vision_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": OCR_PROMPT},
                ],
            }
        ],
        "max_tokens": 300,
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {key}"}
    try:
        resp = requests.post(
            f"{api_keys.vision_base_url.rstrip('/')}/chat/completions",
            json=payload, headers=headers, timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:  # noqa: BLE001
        log.warning("OCR 调用失败：%s", e)
        return ""
