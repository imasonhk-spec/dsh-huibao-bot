"""LiveKit integration contracts; only the remote provider is simulated.

These exercise the installed SDK's error events, retry wrapper and AudioEmitter,
not mock versions of the LiveKit API. They do not prove gateway or device health.
"""
from __future__ import annotations

import asyncio

import pytest


def test_provider_failure_does_not_close_livekit_session(monkeypatch, caplog):
    pytest.importorskip("livekit.agents")
    from livekit import rtc
    from livekit.agents import APIConnectOptions, AgentSession

    from deskbot_server import rtc_livekit_plugins
    from deskbot_server.infrastructure.asr import factory
    from deskbot_server.rtc_barge_in import PlaybackEchoGuard

    secret = "test-asr-private-credential"
    monkeypatch.setenv("ASR_API_KEY", secret)

    class Provider:
        calls = 0

        async def transcribe(self, pcm, sample_rate):
            self.calls += 1
            assert sample_rate == 16000
            assert len(pcm) > 0
            if self.calls == 1:
                raise RuntimeError(f"HTTP 502; Authorization: Bearer {secret}")
            return "你好小歪"

    provider = Provider()
    monkeypatch.setattr(factory, "build_asr_adapter", lambda settings: provider)

    async def run():
        speech = rtc_livekit_plugins._build_stt()
        speech.bind_playback_echo_guard(PlaybackEchoGuard())
        session = AgentSession(stt=speech)
        errors = []
        speech.on("error", errors.append)
        # AgentActivity subscribes this callback during a live session. Use
        # the real callback to prove a failed utterance won't schedule close.
        speech.on("error", session._on_error)
        audio = rtc.AudioFrame(
            data=b"\x40\x04" * 3200,
            sample_rate=16000,
            num_channels=1,
            samples_per_channel=3200,
        )
        opts = APIConnectOptions(max_retry=0)
        first = await speech.recognize(audio, conn_options=opts)
        assert first.alternatives == []
        assert errors and all(event.recoverable for event in errors)
        assert "HTTP 502" in str(errors[0].error)
        assert secret not in str(errors[0].error)
        assert session._closing_task is None
        second = await speech.recognize(audio, conn_options=opts)
        assert second.alternatives[0].text == "你好小歪"
        await speech.aclose()
        await session.aclose()

    asyncio.run(run())
    assert "HTTP 502" in caplog.text
    assert secret not in caplog.text


@pytest.mark.parametrize("streaming", [False, True])
def test_sdk_tts_does_not_apply_its_short_default_to_gateway_request(
    monkeypatch, streaming
):
    pytest.importorskip("livekit.agents")
    from livekit.agents import APIConnectOptions

    from deskbot_server import rtc_livekit_plugins
    from deskbot_server.tts import gateway

    monkeypatch.setenv("TTS_PROVIDER", "openai_compatible")
    monkeypatch.setenv("TTS_API_KEY", "test-tts-credential")
    monkeypatch.setenv("TTS_TIMEOUT_SECONDS", "600")
    monkeypatch.setattr(gateway, "load_dotenv", lambda **kwargs: None)

    async def delayed_provider(text, config, *, on_pcm):
        assert config.timeout_seconds == 600
        await asyncio.sleep(0.03)
        on_pcm(b"\x40\x04" * 3200)

    monkeypatch.setattr(gateway, "synthesize_gateway_tts", delayed_provider)

    async def run():
        speech = rtc_livekit_plugins._build_tts()
        from deskbot_server.rtc_barge_in import PlaybackEchoGuard
        speech.bind_playback_echo_guard(PlaybackEchoGuard())
        # The HTTP provider owns its deadline; SDK timeout is an advisory
        # passed into plugins, not a second unconditional wait_for().
        opts = APIConnectOptions(max_retry=0, timeout=0.01)
        if streaming:
            output = speech.stream(conn_options=opts)
            output.push_text("测试语音。")
            output.end_input()
        else:
            output = speech.synthesize("测试语音。", conn_options=opts)
        async with output:
            frames = [event.frame async for event in output]
        pcm = b"".join(frame.data.tobytes() for frame in frames)
        assert pcm.startswith(b"\x40\x04" * 3200)
        # A streaming emitter may append its 10 ms final-segment marker.
        assert len(pcm) <= 6720
        assert all(frame.sample_rate == 16000 for frame in frames)
        await speech.aclose()

    asyncio.run(run())


def test_real_sdk_normalizes_stereo_48khz_microphone_frames():
    pytest.importorskip("livekit.agents")
    from livekit import rtc

    from deskbot_server.rtc_livekit_plugins import _seed_asr_audio

    source = rtc.AudioFrame(
        data=b"\x40\x04\x40\x04" * 4800,
        sample_rate=48000,
        num_channels=2,
        samples_per_channel=4800,
    )
    pcm, metadata = _seed_asr_audio(rtc, source)
    assert metadata["source_channels"] == 2
    assert metadata["source_rate"] == 48000
    assert abs(metadata["output_samples"] - 1600) <= 1
    assert len(pcm) == metadata["output_samples"] * 2


def test_rtc_diagnostic_removes_bearer_value_even_without_environment_key():
    from deskbot_server.rtc_livekit_plugins import _safe_error_detail

    detail = _safe_error_detail(RuntimeError("HTTP 401 Authorization: Bearer unrelated-key"))
    assert "unrelated-key" not in detail
    assert "HTTP 401" in detail
