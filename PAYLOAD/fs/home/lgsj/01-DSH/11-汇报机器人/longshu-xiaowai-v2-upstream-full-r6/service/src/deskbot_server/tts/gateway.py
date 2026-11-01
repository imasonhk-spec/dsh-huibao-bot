"""Longshu OpenAI-compatible WAV TTS. Credentials remain on the PC."""
from __future__ import annotations

import asyncio
from pathlib import Path
import audioop
import base64
import hashlib
import json
import math
import os
import queue
import re
import struct
import socket
import threading
import tempfile
import time
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass, field, replace
from contextlib import contextmanager
from contextvars import ContextVar

import numpy as np

from deskbot_server.env import load_dotenv, looks_masked, update_env_keys
from deskbot_server.safe_fetch import safe_provider_urlopen, validate_provider_http_url
from deskbot_server.paths import DATA_DIR

ENDPOINT = "https://modelhub.lgdg.cc/aigateway/v1/audio/speech"
MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
VOICES = ("aiden", "dylan", "eric", "ono_anna", "ryan", "serena", "sohee", "uncle_fu", "vivian")
MING_MODEL = "inclusionAI/Ming-3.0-tts-0.5B"
MING_VOICES = ("灵小柔", "灵楷", "US_James", "US_Stella")
DESIGN_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
DESIGN_VOICES = ("professional_female",)
MOSS_MODEL = "F5TTS_Base-MOSS"
MOSS_VOICES = ("moss",)
DUAL_MODELS = (MING_MODEL, MOSS_MODEL)
WAITING_CACHE_VERSION = 'ming-waiting-v1'
VOICE_PRESETS = (
    {"label": "Ming女声", "model": MING_MODEL, "speaker": "灵小柔",
     "endpoint": "http://127.0.0.1:18662/v1/audio/speech"},
    {"label": "MOSS男声", "model": MOSS_MODEL, "speaker": "moss",
     "endpoint": "http://127.0.0.1:18664/v1/audio/speech"},
)
_turn_config = ContextVar("gateway_tts_turn_config", default=None)
MAX_WAV_BYTES = 32 * 1024 * 1024
FIELD_KEYS = {"endpoint": "TTS_ENDPOINT", "model": "TTS_MODEL", "speaker": "TTS_VOICE", "api_key": "TTS_API_KEY", "timeout_seconds": "TTS_TIMEOUT_SECONDS"}


@dataclass(frozen=True)
class GatewayTtsConfig:
    endpoint: str = ENDPOINT
    model: str = MODEL
    speaker: str = "aiden"
    api_key: str = field(default="", repr=False)
    sample_rate: int = 16000  # Device/RTC PCM, not the upstream WAV sample rate.
    timeout_seconds: float = 600.0


@dataclass
class GatewayTtsResult:
    pcm: bytes
    sample_rate: int
    elapsed_ms: int
    sentence_end: list = field(default_factory=list)
    subtitles: list = field(default_factory=list)


def gateway_enabled(default_provider: str = "doubao") -> bool:
    load_dotenv()
    return (os.environ.get("TTS_PROVIDER") or default_provider).strip().lower() == "openai_compatible"


def load_gateway_tts_config() -> GatewayTtsConfig:
    if (config := _turn_config.get()) is not None:
        return config
    load_dotenv()
    return GatewayTtsConfig(
        endpoint=(os.environ.get("TTS_ENDPOINT") or ENDPOINT).strip(),
        model=(os.environ.get("TTS_MODEL") or MODEL).strip(),
        speaker=(os.environ.get("TTS_VOICE") or "aiden").strip(),
        api_key=(os.environ.get("TTS_API_KEY") or "").strip(),
        timeout_seconds=float(os.environ.get("TTS_TIMEOUT_SECONDS") or 600),
    )


@contextmanager
def gateway_tts_config_scope(config: GatewayTtsConfig):
    """Keep prompt and answer audio in one voice while settings change elsewhere."""
    token = _turn_config.set(config)
    try:
        yield config
    finally:
        _turn_config.reset(token)


def voices_for_model(model: str) -> tuple[str, ...]:
    return {MING_MODEL: MING_VOICES, DESIGN_MODEL: DESIGN_VOICES,
            MOSS_MODEL: MOSS_VOICES}.get(model, VOICES)


def validate_config(config: GatewayTtsConfig, *, require_key=True) -> None:
    if require_key and not config.api_key:
        raise ValueError("请在模型配置中填写 TTS API Key。")
    for value in (config.endpoint, config.model, config.speaker, config.api_key):
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("TTS 配置不能含换行或控制字符。")
    if len(config.endpoint) > 2048 or len(config.api_key) > 4096:
        raise ValueError("TTS 接入点或密钥过长。")
    validate_provider_http_url(config.endpoint)
    if not config.model or len(config.model) > 200:
        raise ValueError("TTS 模型不能为空且不能超过 200 字符。")
    if config.speaker not in voices_for_model(config.model):
        raise ValueError("请选择当前模型支持的音色。")
    if not math.isfinite(config.timeout_seconds) or not 1 <= config.timeout_seconds <= 600:
        raise ValueError("TTS 超时必须在 1 到 600 秒之间。")


def config_status() -> dict:
    cfg = load_gateway_tts_config()
    configured = False
    message = "语音合成已配置，启动服务后自动使用，无需再次保存。"
    try:
        validate_config(cfg)
        configured = True
    except (TypeError, ValueError) as exc:
        message = str(exc)
    return {"provider": "openai_compatible", "endpoint": cfg.endpoint, "model": cfg.model,
            "speaker": cfg.speaker, "voices": list(voices_for_model(cfg.model)),
            "voice_catalog": {"default": list(VOICES), MING_MODEL: list(MING_VOICES),
                              DESIGN_MODEL: list(DESIGN_VOICES), MOSS_MODEL: list(MOSS_VOICES)},
            "voice_presets": [dict(preset) for preset in VOICE_PRESETS],
            "speaker_label": next((p['label'] for p in VOICE_PRESETS
                                   if (p['model'], p['speaker']) == (cfg.model, cfg.speaker)), cfg.speaker),
            "api_key_set": bool(cfg.api_key),
            "api_key_masked": "已保存（留空保留）" if cfg.api_key else "",
            "configured": configured, "configuration_status": "configured" if configured else "unconfigured",
            "message": message, "connection_status": "not_tested",
            "sample_rate": cfg.sample_rate, "response_format": "wav", "timeout_seconds": cfg.timeout_seconds}


def save_config(payload: dict) -> dict:
    current = load_gateway_tts_config()
    changes = {}
    for name in FIELD_KEYS:
        if name not in payload:
            continue
        val = str(payload[name] or "").strip()
        if name == "api_key" and looks_masked(val):
            continue
        changes[name] = float(val) if name == "timeout_seconds" else val
    if payload.get("clear_api_key") is True:
        changes["api_key"] = ""
    proposed = replace(current, **changes)
    validate_config(proposed, require_key=False)
    updates = {FIELD_KEYS[k]: str(v) for k, v in changes.items()}
    updates["TTS_PROVIDER"] = "openai_compatible"
    update_env_keys(updates, keys=(*FIELD_KEYS.values(), "TTS_PROVIDER"),
                    write_empty_keys={"TTS_API_KEY"} if payload.get("clear_api_key") is True else (),
                    comment="# 龙数惠宝语音合成（仅本机保存）")
    load_dotenv(force_reload=True)
    return config_status()


def wav_to_pcm(raw: bytes, target_rate: int = 16000) -> bytes:
    """Decode integer/IEEE-float WAV to signed 16-bit mono device PCM.

    Python 3.11/3.12's wave reader rejects IEEE_FLOAT (format 3), even though
    this is a valid WAV used by model servers. Parse the bounded RIFF chunks
    here so float WAV and WAVE_FORMAT_EXTENSIBLE work on Windows too, without
    an external FFmpeg executable. HTTP-streamed WAV may use unknown (all-one)
    RIFF/data lengths; their actual, bounded response length is authoritative.
    """
    if len(raw) > MAX_WAV_BYTES:
        raise ValueError("TTS 音频超过 32 MiB 上限，请缩短文本。")
    if len(raw) < 12 or not raw.startswith(b"RIFF") or raw[8:12] != b"WAVE":
        raise ValueError("TTS 没有返回 WAV 音频，请检查模型和 response_format。")
    if not isinstance(target_rate, int) or not 8000 <= target_rate <= 192000:
        raise ValueError("TTS 目标采样率无效。")
    riff_size = struct.unpack_from("<I", raw, 4)[0]
    end = len(raw) if riff_size == 0xFFFFFFFF else riff_size + 8
    if end < 12 or end > len(raw):
        raise ValueError("TTS WAV 为空或被截断（RIFF 长度不完整）。")
    fmt = pcm = None
    offset = 12
    while offset + 8 <= end:
        tag, size = struct.unpack_from("<4sI", raw, offset)
        start = offset + 8
        if tag == b"data" and size == 0xFFFFFFFF:
            size = end - start
        if start + size > end:
            raise ValueError("TTS WAV 为空或被截断（音频块长度不完整）。")
        if tag == b"fmt ":
            if fmt is not None or not 16 <= size <= 4096:
                raise ValueError("TTS WAV 格式块无效。")
            fmt = raw[start:start + size]
        elif tag == b"data":
            if pcm is not None:
                raise ValueError("TTS WAV 含重复音频块。")
            pcm = raw[start:start + size]
        offset = start + size + (size & 1)
    if fmt is None or not pcm:
        raise ValueError("TTS WAV 缺少格式信息或音频为空。")
    encoding, channels, rate, byte_rate, alignment, bits = struct.unpack_from("<HHIIHH", fmt)
    if encoding == 0xFFFE:
        # WAVEFORMATEXTENSIBLE: cbSize, valid bits, channel mask, subtype GUID.
        if len(fmt) < 40 or struct.unpack_from("<H", fmt, 16)[0] < 22:
            raise ValueError("TTS WAV 扩展格式头不完整。")
        if fmt[28:40] != bytes.fromhex("00001000800000aa00389b71"):
            raise ValueError("TTS WAV 扩展编码不支持。")
        valid_bits = struct.unpack_from("<H", fmt, 18)[0]
        if not 0 <= valid_bits <= bits:
            raise ValueError("TTS WAV 有效位数无效。")
        encoding = struct.unpack_from("<I", fmt, 24)[0]
    if channels not in (1, 2) or not 8000 <= rate <= 192000:
        raise ValueError("TTS WAV 必须为单/双声道，采样率须在 8000–192000 Hz。")
    if (encoding == 1 and bits not in (8, 16, 24, 32)) or (encoding == 3 and bits not in (32, 64)) or encoding not in (1, 3):
        raise ValueError(f"TTS WAV 编码不支持（format={encoding}, bits={bits}）；需要 PCM 或 IEEE_FLOAT WAV。")
    width = bits // 8
    if alignment != channels * width or byte_rate != rate * alignment or len(pcm) % alignment:
        raise ValueError("TTS WAV 音频数据对齐错误或被截断。")
    if len(pcm) / alignment / rate > 600:
        raise ValueError("TTS WAV 时长超过 600 秒。")
    if encoding == 3:
        samples = np.frombuffer(pcm, dtype="<f4" if width == 4 else "<f8")
        if not np.isfinite(samples).all():
            raise ValueError("TTS 浮点 WAV 包含无效采样值。")
        # Clip before conversion; +1.0 must saturate instead of overflowing.
        pcm = np.clip(np.rint(np.clip(samples, -1.0, 1.0) * 32768), -32768, 32767).astype("<i2").tobytes()
    else:
        if width == 1:
            pcm = audioop.bias(pcm, 1, -128)
        if width != 2:
            pcm = audioop.lin2lin(pcm, width, 2)
    if channels == 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    if rate != target_rate:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, target_rate, None)
    return pcm


def _provider_error(raw: bytes, api_key: str) -> str:
    """Expose a bounded JSON error, never arbitrary HTML or credentials."""
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error") or payload.get("detail") or payload.get("message")
    if isinstance(error, dict):
        error = error.get("message") or error.get("detail") or error.get("code")
    if not isinstance(error, str):
        return ""
    if api_key:
        error = error.replace(api_key, "[已隐藏密钥]")
    error = re.sub(r"(?i)bearer\s+[^\s,;]+|\b(?:gk_|sk-)[A-Za-z0-9_-]+", "[已隐藏密钥]", error)
    return " ".join(error.split())[:300]


def _request_tts(text: str, config: GatewayTtsConfig, *, cache_only=False) -> bytes:
    payload = {"model": config.model, "input": text, "voice": config.speaker, "response_format": "wav"}
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json", "Accept": "audio/wav"}
    if cache_only:
        headers["X-Dual-TTS-Cache-Only"] = "1"
    req = urllib.request.Request(config.endpoint, method="POST", data=json.dumps(payload).encode("utf-8"),
        headers=headers)
    try:
        with safe_provider_urlopen(req, timeout=config.timeout_seconds) as resp:
            raw = resp.read(MAX_WAV_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = exc.code
        try:
            detail = _provider_error(exc.read(8192), config.api_key)
        except (OSError, ValueError):
            detail = ""
        finally:
            exc.close()
        suffix = f"；网关返回：{detail}" if detail else "；请检查密钥、额度和模型。"
        raise RuntimeError(f"TTS 请求失败 HTTP {code}{suffix}") from None
    except (urllib.error.URLError, OSError):
        raise RuntimeError("TTS 网络连接失败或超时；请检查本机网络。") from None
    if len(raw) > MAX_WAV_BYTES:
        raise ValueError("TTS 音频超过 32 MiB 上限，请缩短文本。")
    if not raw.startswith(b"RIFF"):
        detail = _provider_error(raw[:8192], config.api_key)
        if detail:
            raise ValueError(f"TTS 网关未返回音频：{detail}")
    return wav_to_pcm(raw, config.sample_rate)


async def prewarm_gateway_tts(config=None):
    """HTTP WAV has no persistent synthesis session to prewarm."""


def supports_waiting(config):
    return any((config.endpoint, config.model, config.speaker) ==
               (p['endpoint'], p['model'], p['speaker']) for p in VOICE_PRESETS)


def _ming_waiting_cache_path(text, config):
    from deskbot_server.application.voice_waiting import ACKS, WAITING, MEETING_WAITING
    if (config.model not in DUAL_MODELS or not supports_waiting(config)
            or text not in (*ACKS, *WAITING, *MEETING_WAITING)):
        raise ValueError('当前端点、音色或文本不支持等待提示缓存。')
    identity = [WAITING_CACHE_VERSION, config.endpoint, config.model, config.speaker,
                config.sample_rate, text]
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    return DATA_DIR / 'local' / 'voice-waiting-cache' / (key + '.pcm')


def _read_ming_waiting_cache(text, config):
    path = _ming_waiting_cache_path(text, config)
    limit = config.sample_rate * 2 * 15
    try:
        with path.open('rb') as stream:
            pcm = stream.read(limit + 1)
    except OSError:
        raise RuntimeError('Ming等待提示缓存缺失，请先完成提示音预生成。') from None
    if not pcm or len(pcm) % 2 or len(pcm) > limit:
        raise RuntimeError('Ming等待提示缓存无效，请重新生成。')
    return pcm


async def prefill_ming_waiting_cache(config=None):
    """Explicit deployment step only: never synthesize prompts during a live turn."""
    cfg = config or load_gateway_tts_config()
    validate_config(cfg)
    from deskbot_server.application.voice_waiting import ACKS, WAITING, MEETING_WAITING
    counts = {'generated': 0, 'reused': 0}
    for text in (*ACKS, *WAITING, *MEETING_WAITING):
        path = _ming_waiting_cache_path(text, cfg)
        try:
            _read_ming_waiting_cache(text, cfg)
        except RuntimeError:
            pcm = await asyncio.wait_for(asyncio.to_thread(_request_tts, text, cfg), cfg.timeout_seconds)
            if not pcm or len(pcm) % 2 or len(pcm) > cfg.sample_rate * 2 * 15:
                raise ValueError('Ming等待提示音须为15秒以内的PCM16音频。')
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                    temporary = stream.name
                    stream.write(pcm)
                os.replace(temporary, path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            counts['generated'] += 1
        else:
            counts['reused'] += 1
    return counts


class _MingStreamUnavailable(RuntimeError):
    pass


async def _stream_ming_tts(text, config, on_pcm):
    """Read bounded PCM frames off-loop; closing the socket cancels synthesis."""
    frames = queue.Queue(maxsize=8)
    stopped = threading.Event()
    response = []
    buffered = threading.Event()

    def put(value):
        while not stopped.is_set():
            try:
                frames.put(value, timeout=0.05)
                return
            except queue.Full:
                pass

    def read():
        payload = {"model": config.model, "input": text, "voice": config.speaker, "response_format": "wav"}
        req = urllib.request.Request(config.endpoint + "/stream", method="POST",
            data=json.dumps(payload).encode(), headers={"Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json", "Accept": "application/x-ndjson"})
        try:
            with safe_provider_urlopen(req, timeout=config.timeout_seconds) as resp:
                response.append(resp)
                if resp.headers.get("X-Ming-Playback") == "buffered":
                    buffered.set()
                if stopped.is_set():
                    return
                if resp.headers.get("Content-Type", "").split(";")[0] != "application/x-ndjson":
                    raise ValueError("TTS 流格式无效。")
                total = 0
                rate = None
                state = None
                while not stopped.is_set():
                    line = resp.readline(1024 * 1024 + 1)
                    if not line or len(line) > 1024 * 1024 or not line.endswith(b"\n"):
                        raise ValueError("TTS 音频流被截断或帧过长。")
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise ValueError("TTS 音频流格式无效。")
                    if item.get("type") == "done":
                        if not total:
                            raise ValueError("TTS 音频为空。")
                        put(None)
                        return
                    if item.get("type") != "audio":
                        raise ValueError("TTS 合成未正常完成。")
                    current_rate = item.get("sample_rate")
                    if type(current_rate) is not int or not 8000 <= current_rate <= 192000 or (rate and rate != current_rate):
                        raise ValueError("TTS 流采样率无效。")
                    rate = current_rate
                    pcm = base64.b64decode(item.get("pcm", ""), validate=True)
                    if not pcm or len(pcm) % 2:
                        raise ValueError("TTS PCM 对齐无效。")
                    total += len(pcm)
                    if total > MAX_WAV_BYTES or total / 2 / rate > 600:
                        raise ValueError("TTS 音频超过上限。")
                    if rate != config.sample_rate:
                        pcm, state = audioop.ratecv(pcm, 2, 1, rate, config.sample_rate, state)
                    if pcm:
                        put(pcm)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            put(_MingStreamUnavailable() if code == 404 else RuntimeError(f"TTS 请求失败 HTTP {code}"))
        except (urllib.error.URLError, OSError):
            put(RuntimeError("TTS 网络连接失败或超时。"))
        except Exception:
            put(ValueError("TTS 音频流无效或未正常完成。"))

    def close():
        if response:
            resp = response[0]
            sock = getattr(getattr(getattr(resp, "fp", None), "raw", None), "_sock", None)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            resp.close()

    worker = asyncio.create_task(asyncio.to_thread(read))
    chunks = []
    try:
        async with asyncio.timeout(config.timeout_seconds):
            while True:
                try:
                    frame = frames.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.005)
                    continue
                if frame is None:
                    if buffered.is_set():
                        for chunk in chunks:
                            on_pcm(chunk)
                            await asyncio.sleep(0)
                    return b"".join(chunks)
                if isinstance(frame, Exception):
                    raise frame
                chunks.append(frame)
                if not buffered.is_set():
                    on_pcm(frame)
    finally:
        stopped.set()
        await asyncio.to_thread(close)
        await asyncio.shield(worker)


async def synthesize_gateway_tts(text: str, config=None, *, on_pcm=None, cache_only=False):
    cfg = config or load_gateway_tts_config()
    validate_config(cfg)
    if cache_only and cfg.model not in DUAL_MODELS:
        raise ValueError("当前模型不支持等待提示缓存。")
    clean = str(text or "").strip()
    if not clean or len(clean) > 4000:
        raise ValueError("TTS 文本须为 1–4000 个字符。")
    started = time.monotonic()
    if cache_only and cfg.model in DUAL_MODELS:
        pcm = await asyncio.to_thread(_read_ming_waiting_cache, clean, cfg)
        if on_pcm:
            for offset in range(0, len(pcm), 6400):
                on_pcm(pcm[offset:offset + 6400])
                await asyncio.sleep(0)
        return GatewayTtsResult(pcm, cfg.sample_rate, int((time.monotonic() - started) * 1000))
    from deskbot_server.application.protocol_three import FIRST, FIRST_PARTS
    if clean == FIRST:
        chunks = []
        for index, part in enumerate(FIRST_PARTS):
            speech = await synthesize_gateway_tts(part, cfg, on_pcm=on_pcm, cache_only=cache_only)
            chunks.append(speech.pcm)
            if index < 2:
                silence = b'\0' * (cfg.sample_rate * 2 * 3)
                chunks.append(silence)
                if on_pcm:
                    on_pcm(silence)
        return GatewayTtsResult(b''.join(chunks), cfg.sample_rate,
                                int((time.monotonic() - started) * 1000))
    url = urllib.parse.urlsplit(cfg.endpoint)
    streaming = (on_pcm is not None and cfg.model == MING_MODEL and url.scheme == "http"
                 and url.hostname in ("127.0.0.1", "localhost", "::1") and url.port == 18662
                 and url.path == "/v1/audio/speech" and not url.query and not url.fragment)
    if streaming:
        try:
            pcm = await _stream_ming_tts(clean, cfg, on_pcm)
        except _MingStreamUnavailable:
            # A missing endpoint has not accepted synthesis; old WAV remains compatible.
            streaming = False
    if not streaming:
        if cache_only:
            pcm = await asyncio.wait_for(asyncio.to_thread(_request_tts, clean, cfg, cache_only=True), cfg.timeout_seconds)
        else:
            pcm = await asyncio.wait_for(asyncio.to_thread(_request_tts, clean, cfg), cfg.timeout_seconds)
    if on_pcm and not streaming:
        for offset in range(0, len(pcm), 6400):
            on_pcm(pcm[offset:offset + 6400])
            await asyncio.sleep(0)
    return GatewayTtsResult(pcm, cfg.sample_rate, int((time.monotonic() - started) * 1000))


class GatewayPhonemeTtsAdapter:
    async def synthesize_phoneme_segments(self, text):
        if not str(text or "").strip():
            return 16000, []
        result = await synthesize_gateway_tts(text)
        from deskbot_server.tts.doubao_phoneme_align import build_phoneme_segments
        segments = await asyncio.to_thread(build_phoneme_segments, text=text, pcm=result.pcm,
            sample_rate=result.sample_rate, sentence_end=[], subtitles=[])
        return result.sample_rate, segments


def persona_id(config=None):
    config=config or load_gateway_tts_config()
    return 'akun' if config.model==MOSS_MODEL else 'huibao'


def persona_configs(config=None):
    """Freeze both current providers without changing the saved main voice."""
    config=config or load_gateway_tts_config()
    configs={}
    for preset,role in zip(VOICE_PRESETS,('huibao','akun')):
        if persona_id(config)==role:
            configs[role]=config
            continue
        # Existing two local services have separate server-side credentials.
        env_file=(Path('/home/lgsj/codex/huibao/ming-local/private/tts.env') if role=='huibao'
                  else Path('/home/lgsj/codex/huibao/dual-tts/runtime.env'))
        key_name='MING_API_KEY' if role=='huibao' else 'DUAL_TTS_API_KEY'
        key=''
        if env_file.exists():
            key=next((line.split('=',1)[1].strip().strip('\"\'') for line in env_file.read_text().splitlines() if line.startswith(key_name+'=')),'')
        configs[role]=GatewayTtsConfig(endpoint=preset['endpoint'],model=preset['model'],speaker=preset['speaker'],api_key=key,sample_rate=config.sample_rate,timeout_seconds=config.timeout_seconds)
    return configs


def voice_declarations(configs=None):
    return {role:{'voiceId':cfg.model+'|'+cfg.speaker,
                  'configVersion':hashlib.sha256(json.dumps([cfg.model,cfg.speaker,cfg.sample_rate],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()}
            for role,cfg in (configs or persona_configs()).items()}
