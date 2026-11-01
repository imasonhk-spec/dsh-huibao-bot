from __future__ import annotations

import asyncio

import pytest

from deskbot_server import rtc_livekit_plugins as plugins


def test_http_tts_disables_speculative_generation_only(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "openai_compatible")
    assert plugins.rtc_preemptive_generation_enabled() is False
    monkeypatch.setenv("TTS_PROVIDER", "doubao")
    assert plugins.rtc_preemptive_generation_enabled() is True


def test_gateway_emits_short_first_audio_before_input_ends(monkeypatch):
    pytest.importorskip("livekit.agents")
    from livekit.agents import APIConnectOptions
    from deskbot_server.tts import gateway

    monkeypatch.setenv("TTS_PROVIDER", "openai_compatible")
    monkeypatch.setattr(gateway, "load_dotenv", lambda **kwargs: None)
    parts = []

    async def synthesize(text, config, *, on_pcm):
        parts.append(text)
        on_pcm(b"\x40\x04" * 3200)

    monkeypatch.setattr(gateway, "synthesize_gateway_tts", synthesize)

    async def run():
        tts = plugins._build_tts()
        from deskbot_server.rtc_barge_in import PlaybackEchoGuard
        tts.bind_playback_echo_guard(PlaybackEchoGuard())
        stream = tts.stream(conn_options=APIConnectOptions(max_retry=0))
        text = "你好呀，我是小歪，很高兴能跟你聊天，今天有什么有趣的事情想跟我分享吗？"
        try:
            # One large LLM delta must still be split internally.
            stream.push_text(text)
            first = await asyncio.wait_for(stream.__anext__(), timeout=2)
            assert first.frame.samples_per_channel > 0
            assert 1 <= len(parts[0]) <= 12
            stream.end_input()
            async for _ in stream:
                pass
            assert "".join(parts) == text
            assert all(len(p) <= 28 for p in parts)
        finally:
            await stream.aclose()
            await tts.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("text", ["你好，我是小歪。", "这是一段没有标点需要按长度切开的很长中文内容", "Hello my friend, how are you today?"])
def test_segmentation_does_not_lose_or_duplicate_text(text):
    rest = text
    parts = []
    while rest:
        piece, following = plugins._gateway_tts_prefix(rest, first=not parts)
        if not piece:
            parts.append(rest)
            break
        parts.append(piece)
        rest = following
    assert "".join(parts) == text
    assert len(parts[0]) <= 12
