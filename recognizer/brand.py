"""品牌抽取与归一：OCR + ASR 文本 → 大模型抽取标准品牌名并做时间对齐。

规则（写入 prompt）：
- 只保留标准品牌名；中英文别名归并到同一品牌（如 华为/HUAWEI/Huawei → 华为）。
- 产品名 / 系列名不单独算品牌。
- 同一视频同一品牌多次出现，按来源（画面/口播）分别列出多个时间段。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

import requests

log = logging.getLogger(__name__)

BRAND_PROMPT_TMPL = """你是一个品牌识别与归一化助手。下面是一条视频的画面 OCR 文本和口播 ASR 文本（均带时间戳，单位秒，误差约 ±5 秒）。

【画面 OCR】
{ocr_text}

【口播 ASR】
{asr_text}

请完成以下任务：
1. 从画面和口播中分别提取出现的具体品牌名称。
2. 只保留“标准品牌名”：中英文别名归并到同一品牌（例如 华为/HUAWEI/Huawei 统一记为“华为”；Nike/耐克 统一记为“耐克”）。
3. 产品名、系列名、型号不算品牌（例如 “Mate 60 Pro”“AirPods Pro”“YSL 黑管” 中，仅当能确认母品牌时才记录母品牌，否则忽略）。
4. 同一个品牌在同一视频中多次出现时，按来源分别列出所有时间段（不要合并）。

严格按如下 JSON 格式输出（不要输出任何多余文字、不要用 markdown 代码块）：
{{
  "brands": {{
    "品牌名": {{
      "visual": [[开始秒, 结束秒], ...],
      "asr": [[开始秒, 结束秒], ...]
    }}
  }}
}}
若没有任何品牌，输出 {{"brands": {{}}}}。时间段用整数秒表示。"""


def _fmt_ocr(ocr_items: List[Dict[str, Any]]) -> str:
    if not ocr_items:
        return "（无）"
    lines = []
    for it in ocr_items:
        lines.append(f"[{int(it['t'])}s] {it['text']}")
    return "\n".join(lines)


def _fmt_asr(asr_items: List[Dict[str, Any]]) -> str:
    if not asr_items:
        return "（无）"
    lines = []
    for it in asr_items:
        lines.append(f"[{it['start']}s-{it['end']}s] {it['text']}")
    return "\n".join(lines)


def _parse_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    # 去掉可能的 markdown 代码块
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        log.warning("品牌抽取返回无法解析：%s", text[:200])
        return {"brands": {}}


def extract_brands(api_keys, ocr_items: List[Dict[str, Any]],
                   asr_items: List[Dict[str, Any]], duration: float) -> Dict[str, Any]:
    if not api_keys.deepseek_api_key:
        log.warning("未配置 DeepSeek API Key，跳过品牌抽取")
        return {}
    prompt = BRAND_PROMPT_TMPL.format(ocr_text=_fmt_ocr(ocr_items), asr_text=_fmt_asr(asr_items))
    payload = {
        "model": api_keys.llm_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 2000,
    }
    headers = {"Authorization": f"Bearer {api_keys.deepseek_api_key}"}
    try:
        resp = requests.post(
            f"{api_keys.deepseek_base_url.rstrip('/')}/chat/completions",
            json=payload, headers=headers, timeout=120,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return _parse_json(content).get("brands", {})
    except Exception as e:  # noqa: BLE001
        log.warning("品牌抽取调用失败：%s", e)
        return {}
