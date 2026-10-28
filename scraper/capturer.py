"""静默音画捕获：不下载视频，播放过程中截屏 + 捕获 <video> 数字音频流。

方案：
- 截屏：对 <video> 元素按固定间隔 element.screenshot()，得到带时间戳的关键帧。
- 音频：注入脚本用 Web Audio 把 <video> 音频重路由到 MediaStreamDestination
  （gain=0 静音到扬声器，避免物理外放），MediaRecorder 录制为 webm(opus)，
  再用 imageio-ffmpeg（内置静态 ffmpeg）转成 16kHz 单声道 WAV 供 ASR 使用。
"""
from __future__ import annotations

import base64
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

import imageio_ffmpeg
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from backend import config

log = logging.getLogger(__name__)

CAPTURE_TMP = config.DATA_DIR / "tmp"
FRAME_INTERVAL_SEC = 4      # 关键帧间隔，配合 ±5s 时间精度
MAX_DURATION_SEC = 600      # 单条视频最长处理 10 分钟

_AUDIO_SETUP_JS = """
(async () => {
  const video = document.querySelector('video');
  if (!video) return {error: '页面无 video 元素'};
  video.muted = false;
  video.volume = 1;
  let stream = null;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    if (ctx.state === 'suspended') { ctx.resume(); }
    const src = ctx.createMediaElementSource(video);
    const gain = ctx.createGain(); gain.gain.value = 0;          // 静音到扬声器
    const dest = ctx.createMediaStreamDestination();
    src.connect(gain); gain.connect(ctx.destination);
    src.connect(dest);                                            // 数字音频流 → 录制
    stream = dest.stream;
    window.__xhs_ctx = ctx;
  } catch (e) {
    if (video.captureStream) {
      stream = video.captureStream();
      const audio = stream.getAudioTracks();
      if (audio.length) stream = new MediaStream(audio);
    }
  }
  if (!stream || !stream.getAudioTracks().length) return {error: '无法捕获音频流'};
  let mime = '';
  for (const t of ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']) {
    if (MediaRecorder.isTypeSupported(t)) { mime = t; break; }
  }
  const chunks = [];
  const rec = new MediaRecorder(stream, mime ? {mimeType: mime} : undefined);
  rec.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
  window.__xhs_audio = { chunks, rec };
  rec.start(200);
  video.play().catch(() => {});
  return {ok: true, duration: isFinite(video.duration) ? video.duration : 0};
})()
"""

_AUDIO_STOP_JS = """
(async () => {
  const a = window.__xhs_audio;
  if (!a) return null;
  try { a.rec.stop(); } catch (e) {}
  await new Promise(r => setTimeout(r, 600));
  if (!a.chunks.length) return null;
  const blob = new Blob(a.chunks, {type: 'audio/webm'});
  const buf = new Uint8Array(await blob.arrayBuffer());
  let bin = '';
  const CH = 0x8000;
  for (let i = 0; i < buf.length; i += CH) {
    bin += String.fromCharCode.apply(null, buf.subarray(i, i + CH));
  }
  return btoa(bin);
})()
"""


def _convert_to_wav(webm_path: Path, wav_path: Path) -> Path | None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", str(webm_path), "-ac", "1", "-ar", "16000", "-f", "wav", str(wav_path)],
            check=True, capture_output=True, timeout=300,
        )
        return wav_path if wav_path.exists() and wav_path.stat().st_size > 44 else None
    except subprocess.CalledProcessError as e:
        log.warning("音频转码失败：%s", e.stderr.decode(errors="ignore")[:300])
        return None


def _download(context, url: str, dest: Path) -> bool:
    try:
        resp = context.request.get(
            url, headers={"Referer": "https://www.xiaohongshu.com/"}, timeout=180000
        )
        if not resp.ok:
            log.warning("媒体下载失败 HTTP %s：%s", resp.status, url[:120])
            return False
        data = resp.body()
        if not data:
            return False
        dest.write_bytes(data)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("媒体下载异常：%s（%s）", e, url[:100])
        return False


def _video_duration(page) -> float:
    try:
        d = float(page.evaluate(
            "() => { const v = document.querySelector('video'); return v && isFinite(v.duration) ? v.duration : 0; }"
        ))
    except Exception:
        d = 0
    if d != d or d <= 0:
        d = MAX_DURATION_SEC
    return min(d, MAX_DURATION_SEC)


def _capture_via_media(context, media_urls: List[str], out_dir: Path,
                       duration: float) -> Dict[str, Any] | None:
    """主路径：下载嗅探到的视频(.m4s/.mp4)/音频(.m4a)，ffmpeg 抽帧 + 抽 16k 音频，处理完删临时视频。"""
    video_url = next((u for u in media_urls if ".m4s" in u or ".mp4" in u), None)
    audio_url = next((u for u in media_urls if ".m4a" in u), None)
    if not video_url:
        return None
    vfile = out_dir / "media_video.m4s"
    if not _download(context, video_url, vfile):
        return None
    try:
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        frames_dir = out_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [ffmpeg, "-y", "-i", str(vfile), "-vf", f"fps=1/{FRAME_INTERVAL_SEC}",
             "-pix_fmt", "rgb24", str(frames_dir / "frame_%05d.png")],
            check=True, capture_output=True, timeout=600,
        )
        screenshots: List[List] = []
        for p in sorted(frames_dir.glob("frame_*.png")):
            try:
                idx = int(p.stem.split("_")[1])
            except (IndexError, ValueError):
                continue
            screenshots.append([(idx - 1) * FRAME_INTERVAL_SEC, str(p)])

        wav_path = out_dir / "audio.wav"
        audio_ok = False
        if audio_url:
            afile = out_dir / "media_audio.m4a"
            if _download(context, audio_url, afile):
                r = subprocess.run(
                    [ffmpeg, "-y", "-i", str(afile), "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(wav_path)],
                    capture_output=True, timeout=600,
                )
                audio_ok = r.returncode == 0
                try:
                    afile.unlink()
                except OSError:
                    pass
        if not audio_ok:
            r = subprocess.run(
                [ffmpeg, "-y", "-i", str(vfile), "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(wav_path)],
                capture_output=True, timeout=600,
            )
            audio_ok = r.returncode == 0
        audio_wav = str(wav_path) if audio_ok and wav_path.exists() and wav_path.stat().st_size > 44 else None

        try:
            vfile.unlink()
        except OSError:
            pass
        return {"duration": duration, "screenshots": screenshots, "audio_wav": audio_wav}
    except Exception as e:  # noqa: BLE001
        log.warning("媒体处理失败：%s", e)
        try:
            vfile.unlink()
        except OSError:
            pass
        return None


def _capture_realtime(page, video: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    """回退路径：实时播放 + 截屏 + MediaRecorder 录音（不下载）。"""
    page.goto(video["url"], timeout=60000, wait_until="domcontentloaded")
    page.wait_for_selector("video", timeout=30000)
    page.wait_for_timeout(1500)

    setup = page.evaluate(_AUDIO_SETUP_JS)
    if setup.get("error"):
        log.warning("音频捕获失败（继续仅截屏）：%s", setup["error"])
    duration = _video_duration(page)

    screenshots: List[List] = []
    t = 0.5
    deadline = time.time() + duration + 60
    while t < duration and time.time() < deadline:
        try:
            ended = page.evaluate("() => { const v = document.querySelector('video'); return v ? v.ended : true; }")
        except Exception:
            ended = True
        if ended:
            break
        shot = out_dir / f"frame_{int(t):04d}.png"
        try:
            video_el = page.query_selector("video")
            if video_el:
                video_el.screenshot(path=str(shot))
            else:
                page.screenshot(path=str(shot))
            screenshots.append([t, str(shot)])
        except Exception as e:
            log.warning("截屏失败 t=%s：%s", t, e)
        page.wait_for_timeout(FRAME_INTERVAL_SEC * 1000)
        t += FRAME_INTERVAL_SEC

    audio_wav = None
    try:
        b64 = page.evaluate(_AUDIO_STOP_JS)
        if b64:
            webm_path = out_dir / "audio.webm"
            webm_path.write_bytes(base64.b64decode(b64))
            wav_path = out_dir / "audio.wav"
            audio_wav = str(_convert_to_wav(webm_path, wav_path))
    except Exception as e:
        log.warning("音频录制结束失败：%s", e)

    return {
        "note_id": video.get("note_id"),
        "duration": duration,
        "screenshots": screenshots,
        "audio_wav": audio_wav,
    }


def _capture_one(page, video: Dict[str, Any]) -> Dict[str, Any] | None:
    note_id = video.get("note_id") or str(int(time.time()))
    out_dir = CAPTURE_TMP / note_id
    out_dir.mkdir(parents=True, exist_ok=True)

    media_urls: List[str] = []

    def _on_response(resp) -> None:
        try:
            u = resp.url
            ct = (resp.headers or {}).get("content-type", "")
            if (".m4s" in u or ".m4a" in u or ".mp4" in u or ".m3u8" in u
                    or "video/" in ct or "audio/" in ct):
                if u not in media_urls:
                    media_urls.append(u)
        except Exception:
            pass

    page.on("response", _on_response)
    try:
        page.goto(video["url"], timeout=60000, wait_until="domcontentloaded")
        page.wait_for_selector("video", timeout=30000)
        # 静音播放几秒，触发 MSE 发出媒体请求（不发出物理声音）
        try:
            page.evaluate(
                "() => { const v = document.querySelector('video'); if (v) { v.muted = true; try { v.play(); } catch (e) {} } }"
            )
        except Exception:
            pass
        page.wait_for_timeout(6000)

        duration = _video_duration(page)

        # 主路径：媒体直取 + ffmpeg
        media = _capture_via_media(page.context, media_urls, out_dir, duration)
        if media is not None:
            log.info("[%s] 媒体直取成功：%d 帧", note_id, len(media["screenshots"]))
            return {
                "note_id": note_id,
                "duration": media["duration"],
                "screenshots": media["screenshots"],
                "audio_wav": media["audio_wav"],
            }

        # 回退：实时截屏 + 录音
        log.info("[%s] 媒体直取不可用，回退实时截屏", note_id)
        result = _capture_realtime(page, video, out_dir)
        result["note_id"] = note_id
        return result
    finally:
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass


def _capture_list(page, videos: List[Dict[str, Any]], on_progress, results: List) -> None:
    total = len(videos)
    for i, v in enumerate(videos, 1):
        if on_progress:
            try:
                on_progress(i, total, v)
            except Exception:
                pass
        try:
            results.append(_capture_one(page, v))
        except Exception as e:  # noqa: BLE001
            log.exception("视频捕获失败 %s", v.get("url"))
            results.append(None)


def capture_videos(videos: List[Dict[str, Any]], headless: bool = True,
                   on_progress=None) -> List[Dict[str, Any] | None]:
    """批量捕获：复用一个浏览器会话，按视频顺序返回捕获结果（失败为 None）。

    on_progress(i, total, video)：每开始处理一条视频时回调，用于前端进度。
    设置 XHS_CDP 时连接用户真实 Chrome（只关标签页，不关浏览器）。
    """
    headless = config.resolve_headless(headless)
    cdp = config.cdp_url()
    results: List[Dict[str, Any] | None] = []
    with sync_playwright() as p:
        browser = None
        if cdp:
            try:
                browser = p.chromium.connect_over_cdp(cdp)
            except Exception as e:  # noqa: BLE001
                log.warning("连接调试 Chrome 失败（%s），回退独立浏览器", e)
        if browser is not None:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            try:
                _capture_list(page, videos, on_progress, results)
            finally:
                try:
                    page.close()
                except Exception:
                    pass
        else:
            proxy = config.proxy_config()
            launch_kwargs = {"proxy": proxy} if proxy else {}
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(config.BROWSER_PROFILE_DIR),
                headless=headless,
                args=["--autoplay-policy=no-user-gesture-required", "--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 900},
                **launch_kwargs,
            )
            try:
                page = context.new_page()
                _capture_list(page, videos, on_progress, results)
            finally:
                context.close()
    return results
