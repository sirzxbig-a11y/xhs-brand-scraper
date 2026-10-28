"""口播 ASR：多家云厂商适配（页面下拉切换），统一返回句级时间戳。

统一接口：transcribe(api_keys, wav_path) -> list[{"start": 秒, "end": 秒, "text": str}]

- 阿里云：DashScope paraformer-realtime-v2（实时识别，句级 begin_time/end_time）
- 腾讯云：一句话识别 SentenceRecognition（16k_zh，按 55s 切片 + TC3 签名）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import wave
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests

log = logging.getLogger(__name__)

PROVIDERS: Dict[str, "ASRProvider"] = {}


def register(cls):
    PROVIDERS[cls.name] = cls()
    return cls


def transcribe(api_keys, wav_path: str) -> List[Dict[str, Any]]:
    provider = PROVIDERS.get(api_keys.asr_provider)
    if provider is None:
        log.warning("未知 ASR 厂商 %s，跳过 ASR", api_keys.asr_provider)
        return []
    return provider.transcribe(api_keys, wav_path)


def _read_wav_pcm(wav_path: str) -> tuple[int, bytes]:
    with wave.open(wav_path, "rb") as w:
        sr = w.getframerate()
        return sr, w.readframes(w.getnframes())


class ASRProvider(ABC):
    name: str = ""

    @abstractmethod
    def transcribe(self, api_keys, wav_path: str) -> List[Dict[str, Any]]:
        ...


# ---------------------------------------------------------------------------
# 阿里云 DashScope
# ---------------------------------------------------------------------------
@register
class AliyunASR(ASRProvider):
    name = "aliyun"

    def transcribe(self, api_keys, wav_path: str) -> List[Dict[str, Any]]:
        key = api_keys.asr.aliyun_app_key or api_keys.deepseek_api_key
        if not key:
            log.warning("未配置阿里云 ASR Key")
            return []
        import dashscope
        from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

        dashscope.api_key = key
        sr, pcm = _read_wav_pcm(wav_path)

        sentences: List[Dict[str, Any]] = []

        last_key = [None]

        class Cb(RecognitionCallback):
            def on_event(self, result: RecognitionResult) -> None:
                try:
                    sent = result.get_sentence()
                except Exception:
                    return
                if not sent or not sent.get("sentence_end"):
                    return
                text = (sent.get("text") or "").strip()
                if not text:
                    return
                key = (sent.get("sentence_id"), text)
                if key == last_key[0]:
                    return
                last_key[0] = key
                bt = sent.get("begin_time") or 0
                et = sent.get("end_time")
                if et is None:
                    words = sent.get("words") or []
                    et = words[-1].get("end_time") if words else bt
                sentences.append({
                    "start": round((bt or 0) / 1000.0, 1),
                    "end": round((et or bt or 0) / 1000.0, 1),
                    "text": text,
                })

            def on_error(self, result) -> None:
                log.warning("阿里 ASR 错误：%s", getattr(result, "message", result))

        recognition = Recognition(
            model="paraformer-realtime-v2",
            format="pcm",
            sample_rate=sr,
            callback=Cb(),
        )
        recognition.start()
        try:
            # 分片发送，适配长音频
            chunk = 6400
            for i in range(0, len(pcm), chunk):
                recognition.send_audio_frame(pcm[i:i + chunk])
        finally:
            recognition.stop()

        return sentences


# ---------------------------------------------------------------------------
# 腾讯云一句话识别（TC3 签名）
# ---------------------------------------------------------------------------
def _tc3_signature(secret_id: str, secret_key: str, service: str, host: str,
                   payload: str, timestamp: int, action: str, version: str) -> str:
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = "\n".join(["POST", "/", "", canonical_headers, signed_headers, hashed_payload])
    credential_scope = f"{date}/{service}/tc3_request"
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = "\n".join(["TC3-HMAC-SHA256", str(timestamp), credential_scope, hashed_canonical])

    def _hmac(key, msg):
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    secret_date = _hmac(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac(secret_date, service)
    secret_signing = _hmac(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


@register
class TencentASR(ASRProvider):
    name = "tencent"
    HOST = "asr.tencentcloudapi.com"
    SERVICE = "asr"
    VERSION = "2019-06-14"
    CHUNK_SEC = 55  # 一句话识别单段 ≤ 60s

    def _sentence_recognition(self, secret_id: str, secret_key: str, wav_bytes: bytes) -> List[Dict[str, Any]]:
        payload = {
            "ProjectId": 0,
            "SubServiceType": 2,
            "EngSerViceType": "16k_zh",
            "SourceType": 1,
            "VoiceFormat": "wav",
            "Data": base64.b64encode(wav_bytes).decode(),
            "DataLen": len(wav_bytes),
        }
        import json
        body = json.dumps(payload)
        timestamp = int(datetime.now(timezone.utc).timestamp())
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Host": self.HOST,
            "X-TC-Action": "SentenceRecognition",
            "X-TC-Version": self.VERSION,
            "X-TC-Timestamp": str(timestamp),
            "Authorization": _tc3_signature(secret_id, secret_key, self.SERVICE, self.HOST,
                                            body, timestamp, "SentenceRecognition", self.VERSION),
        }
        resp = requests.post(f"https://{self.HOST}", data=body, headers=headers, timeout=60)
        resp.raise_for_status()
        result = resp.json().get("Response", {})
        if result.get("Error"):
            log.warning("腾讯 ASR 错误：%s", result["Error"])
            return []
        text = result.get("Result", "")
        detail = result.get("ResultDetail") or []
        out = []
        for d in detail:
            out.append({
                "start": round((d.get("StartMs") or 0) / 1000.0, 1),
                "end": round((d.get("EndMs") or 0) / 1000.0, 1),
                "text": d.get("FinalSentence") or "",
            })
        if text and not out:
            out.append({"start": 0, "end": 0, "text": text})
        return out

    def transcribe(self, api_keys, wav_path: str) -> List[Dict[str, Any]]:
        secret_id = api_keys.asr.tencent_secret_id
        secret_key = api_keys.asr.tencent_secret_key
        if not secret_id or not secret_key:
            log.warning("未配置腾讯云 ASR SecretId/SecretKey")
            return []

        sr, pcm = _read_wav_pcm(wav_path)
        import io
        sentences: List[Dict[str, Any]] = []
        step = sr * 2 * self.CHUNK_SEC  # 16bit 单声道
        offset = 0
        idx = 0
        while idx < len(pcm):
            chunk = pcm[idx: idx + step]
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(chunk)
            for s in self._sentence_recognition(secret_id, secret_key, buf.getvalue()):
                sentences.append({
                    "start": round(s["start"] + offset, 1),
                    "end": round(s["end"] + offset, 1),
                    "text": s["text"],
                })
            offset += self.CHUNK_SEC
            idx += step
        return sentences
