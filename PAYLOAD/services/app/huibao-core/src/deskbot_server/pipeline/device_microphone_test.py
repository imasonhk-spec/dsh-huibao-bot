"""Bounded, in-memory recording of decoded device audio, before RTC publication.

All methods run on the Core event loop. Nothing opens a microphone or serial port.
"""
from __future__ import annotations

import array
import asyncio
import io
import math
import sys
import time
import uuid
import wave
from dataclasses import dataclass, field


@dataclass
class Capture:
    device_id: str
    source: object
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started: float = field(default_factory=time.monotonic)
    ended: float | None = None
    reason: str = ""
    rate: int = 0
    pcm: bytearray = field(default_factory=bytearray)
    batches: int = 0
    peak: int = 0
    rms_dbfs: float = -96.0
    peak_dbfs: float = -96.0
    clipped: int = 0
    timer: object = None
    initial_crc_errors: int = 0
    crc_errors: int = 0


class MicrophoneCaptures:
    LIMIT_SECONDS = 15
    RETAIN_SECONDS = 300
    MAX_DEVICES = 4

    def __init__(self):
        self.items: dict[str, Capture] = {}

    @staticmethod
    def crc_count(source):
        diagnostics = getattr(source, "diagnostics", None)
        return getattr(diagnostics(), "decoder_invalid_payloads", 0) if callable(diagnostics) else 0

    def start(self, device_id, source):
        previous = self.items.get(device_id)
        if previous and previous.ended is None:
            raise ValueError("这台设备正在录音，请先停止。")
        if previous:
            self.expire(previous)
        if device_id not in self.items and len(self.items) >= self.MAX_DEVICES:
            raise ValueError("录音测试数量已达上限，请稍后重试。")
        capture = Capture(device_id, source)
        capture.initial_crc_errors = self.crc_count(source)
        self.items[device_id] = capture
        loop = asyncio.get_running_loop()
        capture.timer = loop.call_later(self.LIMIT_SECONDS, self.finish, capture, "limit")
        loop.call_later(self.RETAIN_SECONDS, self.expire, capture)
        return capture

    def expire(self, capture):
        self.finish(capture, "expired")
        if self.items.get(capture.device_id) is capture:
            del self.items[capture.device_id]
        capture.pcm.clear()

    def finish(self, capture, reason="stopped"):
        if capture.ended is None:
            capture.ended = time.monotonic()
            capture.reason = reason
            capture.crc_errors = max(0, self.crc_count(capture.source) - capture.initial_crc_errors)
            if capture.timer:
                capture.timer.cancel()

    def get(self, device_id, capture_id):
        capture = self.items.get(device_id)
        return capture if capture and capture.id == capture_id else None

    def feed(self, device_id, source, pcm, rate, channels):
        """Return True while this device's audio must bypass the voice service."""
        capture = self.items.get(device_id)
        if capture is None or capture.ended is not None:
            return False
        if source is not capture.source:
            self.finish(capture, "device_reconnected")
            return True
        if time.monotonic() - capture.started >= self.LIMIT_SECONDS:
            self.finish(capture, "limit")
            return True
        if not pcm:
            return True
        if channels != 1 or rate not in (8000, 16000) or len(pcm) % 2 or (capture.rate and capture.rate != rate):
            self.finish(capture, "audio_format_changed")
            return True
        capture.rate = rate
        remaining = self.LIMIT_SECONDS * rate * 2 - len(capture.pcm)
        pcm = pcm[:remaining]
        samples = array.array("h", pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        if samples:
            peak = max(abs(n) for n in samples)
            rms = math.sqrt(sum(n * n for n in samples) / len(samples))
            capture.rms_dbfs = round(20 * math.log10(max(rms, .5) / 32768), 1)
            capture.peak = max(capture.peak, peak)
            capture.peak_dbfs = round(20 * math.log10(max(capture.peak, .5) / 32768), 1)
            capture.clipped += sum(abs(n) >= 32760 for n in samples)
            capture.pcm.extend(pcm)
            capture.batches += 1
        if len(capture.pcm) >= self.LIMIT_SECONDS * rate * 2:
            self.finish(capture, "limit")
        return True

    def status(self, capture, source):
        if capture.ended is None and (source is not capture.source or not getattr(source, "is_ready", False)):
            self.finish(capture, "device_disconnected")
        return {
            "ok": True, "capture_id": capture.id, "device_id": capture.device_id,
            "source": "device_microphone", "recording": capture.ended is None,
            "reason": capture.reason, "sample_rate": capture.rate, "channels": 1,
            "seconds": round(len(capture.pcm) / (capture.rate * 2), 2) if capture.rate else 0,
            "elapsed": round((capture.ended or time.monotonic()) - capture.started, 2),
            "batches": capture.batches, "bytes": len(capture.pcm),
            "rms_dbfs": capture.rms_dbfs, "peak_dbfs": capture.peak_dbfs,
            "clipped_samples": capture.clipped,
            "usb_crc_errors": capture.crc_errors if capture.ended is not None else max(0, self.crc_count(capture.source) - capture.initial_crc_errors),
        }

    def wav(self, capture):
        if capture.ended is None or not capture.pcm:
            raise ValueError("尚无可回放录音，请确认设备收音开启后录制。")
        out = io.BytesIO()
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(capture.rate)
            wav.writeframes(capture.pcm)
        return out.getvalue()


microphone_captures = MicrophoneCaptures()
