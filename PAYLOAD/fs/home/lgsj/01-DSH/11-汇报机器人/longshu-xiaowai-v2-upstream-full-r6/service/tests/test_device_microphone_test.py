import asyncio
import io
import struct
import wave
from types import SimpleNamespace

import pytest

from deskbot_server.pipeline.device_microphone_test import MicrophoneCaptures


def test_device_pcm_wav_and_stats_are_unmodified():
    async def run():
        store = MicrophoneCaptures()
        source = SimpleNamespace(is_ready=True)
        capture = store.start("robot", source)
        pcm = struct.pack("<4h", 0, 1000, -2000, 32767)
        assert not store.feed("another-robot", source, pcm, 16000, 1)
        assert store.feed("robot", source, pcm, 16000, 1)
        store.finish(capture)
        status = store.status(capture, source)
        assert status["source"] == "device_microphone"
        assert status["bytes"] == len(pcm)
        assert status["clipped_samples"] == 1
        with wave.open(io.BytesIO(store.wav(capture))) as wav:
            assert wav.getframerate() == 16000 and wav.getnchannels() == 1
            assert wav.readframes(4) == pcm
        assert not store.feed("robot", source, pcm, 16000, 1)
    asyncio.run(run())


def test_no_reconnection_or_format_splicing():
    async def run():
        store = MicrophoneCaptures()
        source = SimpleNamespace(is_ready=True)
        capture = store.start("robot", source)
        with pytest.raises(ValueError):
            store.start("robot", source)
        store.feed("robot", source, b"\x01\x00", 16000, 1)
        assert store.feed("robot", object(), b"\x02\x00", 16000, 1)
        assert capture.reason == "device_reconnected"
        assert capture.pcm == b"\x01\x00"
        next_capture = store.start("robot", source)
        assert not capture.pcm and store.get("robot", capture.id) is None
        store.feed("robot", source, b"\x01\x00", 8000, 1)
        store.feed("robot", source, b"\x02\x00", 16000, 1)
        assert next_capture.reason == "audio_format_changed"
        assert next_capture.pcm == b"\x01\x00"
    asyncio.run(run())


def test_capture_stops_without_audio_or_browser_polling():
    async def run():
        store = MicrophoneCaptures()
        store.LIMIT_SECONDS = .01
        source = SimpleNamespace(is_ready=True)
        capture = store.start("robot", source)
        await asyncio.sleep(.025)
        assert capture.reason == "limit" and capture.ended is not None
        with pytest.raises(ValueError):
            store.wav(capture)
        store.expire(capture)
        assert store.get("robot", capture.id) is None
    asyncio.run(run())


def test_buffer_limit_and_disconnection():
    async def run():
        store = MicrophoneCaptures()
        source = SimpleNamespace(is_ready=True)
        capture = store.start("robot", source)
        store.feed("robot", source, b"\0\0" * (16000 * 16), 16000, 1)
        assert len(capture.pcm) == 16000 * 15 * 2
        assert not store.status(capture, source)["recording"]
        capture = store.start("robot", source)
        assert store.status(capture, None)["reason"] == "device_disconnected"
    asyncio.run(run())


def test_uplink_capture_bypasses_rtc_only_while_recording(monkeypatch):
    from deskbot_server.pipeline import device_microphone_test
    from deskbot_server.ws import asr_chat
    from deskbot_server import rtc_runtime

    async def run():
        store = MicrophoneCaptures()
        monkeypatch.setattr(device_microphone_test, "microphone_captures", store)
        monkeypatch.setattr(asr_chat, "get_asr_voice_auto_reply_enabled", lambda: True)
        published = []
        monkeypatch.setattr(rtc_runtime, "publish_usb_pcm", lambda dev, pcm: published.append(pcm))
        source = SimpleNamespace(is_ready=True)
        class Decoder:
            last_decoded_pcm = b"\x01\x00"
            rom_sr, rom_ch = 16000, 1
            async def feed_audio(self, *args, **kwargs):
                return [], False, False
            def consume_microphone_health_update(self):
                return None
        capture = store.start("robot", source)
        await asr_chat._feed_rom_uplink(b"encoded", "opus", session=Decoder(), device_id="robot", websocket=source)
        assert not published and capture.pcm == b"\x01\x00"
        store.finish(capture)
        await asr_chat._feed_rom_uplink(b"encoded", "opus", session=Decoder(), device_id="robot", websocket=source)
        assert published == [b"\x01\x00"]
    asyncio.run(run())
