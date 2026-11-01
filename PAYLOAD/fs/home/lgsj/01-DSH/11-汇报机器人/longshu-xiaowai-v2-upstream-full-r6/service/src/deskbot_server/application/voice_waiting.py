"""Audio-end feedback for one admitted turn; formal PCM always takes priority."""
import asyncio
from contextlib import aclosing, contextmanager
from contextvars import ContextVar
import logging
import random
import time

logger = logging.getLogger('deskbot-server')
ACKS = ('惠宝收到。', '阿昆收到。')
WAITING = ('资料已返回，正在整理答复。', '正在查阅相关资料，请稍候。',
           '这次语音等待先结束，后续结果请在原会话查看。',
           '还在处理您的问题，请稍候。', '答复已生成，正在准备语音。', '语音准备失败，文字结果已保留在原会话。')
MEETING_WAITING = ('请求还在处理中，请稍候。', '正在处理您的请求，请稍候。', '还需要一点时间，请稍候。')
_current = ContextVar('deskbot_voice_waiting', default=None)


def waiting_due(ended_at, now, last_at):
    return now >= (ended_at + 15 if last_at is None else last_at + 30)


def current_waiting():
    return _current.get()


@contextmanager
def waiting_scope(waiting):
    from deskbot_server.tts.gateway import gateway_tts_config_scope
    token = _current.set(waiting)
    try:
        with gateway_tts_config_scope(waiting.config):
            yield
    finally:
        _current.reset(token)


class VoiceWaiting:
    def __init__(self, config, *, ended_at, play, clear, meeting=False,
                 clock=time.monotonic, sleep=asyncio.sleep, choose=random.choice):
        self.config, self.ended_at, self.meeting = config, ended_at, meeting
        self.play, self.clear = play, clear
        self.clock, self.sleep, self.choose = clock, sleep, choose
        self.timer = self.audio_task = None
        self.ready = self.closed = self.paused = False
        self._audio_used = False
        self._last_phrase = None
        self.play_pcm = None
        self.lock = asyncio.Lock()

    def start(self, *, acknowledge=False):
        if self.closed or self.ready or (self.timer and not self.timer.done()):
            return
        self.paused = False
        self.timer = asyncio.create_task(self._run(acknowledge))

    async def _hint(self, text, hum=False):
        async with self.lock:
            if self.closed or self.ready or self.paused:return
            self._audio_used = True
            self.audio_task = asyncio.create_task(self.play(text, hum))
            await self.audio_task
            self._audio_used = False

    async def _run(self, acknowledge):
        # A receipt may acknowledge only a known accepted request, never VAD end.
        try:
            if acknowledge and not(self.closed or self.ready or self.paused):
                from deskbot_server.tts.gateway import persona_id
                await self._hint(ACKS[1] if persona_id(self.config)=='akun' else ACKS[0])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('[voice-waiting] receipt feedback unavailable error=%s',type(exc).__name__)

    async def _stop_audio(self):
        tasks = [t for t in (self.timer, self.audio_task)
                 if t is not None and t is not asyncio.current_task() and not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._audio_used:
            await self.clear()
            self._audio_used = False

    async def audio_ready(self):
        self.ready = True
        await self._stop_audio()

    async def pause(self):
        self.paused = True
        await self._stop_audio()

    def resume(self):
        if not self.closed:
            self.ready = False
            self.start(acknowledge=False)

    async def close(self):
        self.closed = True
        await self._stop_audio()


async def pcm_frames(result):
    from livekit import rtc
    size = result.sample_rate // 50 * 2
    for offset in range(0, len(result.pcm), size):
        pcm = result.pcm[offset:offset + size]
        yield rtc.AudioFrame(data=pcm, sample_rate=result.sample_rate, num_channels=1,
                             samples_per_channel=len(pcm) // 2)


async def ming_response_pcm(text, config):
    """Bridge the gateway's synchronous PCM callback to cancellable async playback."""
    from deskbot_server.tts.gateway import synthesize_gateway_tts
    chunks = asyncio.Queue()
    async def produce():
        try:
            from deskbot_server.tts.text_split import split_tts_by_punctuation
            # Each bounded request keeps gateway done/integrity checks before PCM release.
            for sentence in split_tts_by_punctuation(text):
                for offset in range(0, len(sentence), 50):
                    await synthesize_gateway_tts(sentence[offset:offset + 50], config,
                                                 on_pcm=chunks.put_nowait)
        finally:
            chunks.put_nowait(None)
    synthesis = asyncio.create_task(produce())
    try:
        while (pcm := await chunks.get()) is not None:
            yield pcm
        await synthesis
    finally:
        if not synthesis.done():
            synthesis.cancel()
        await asyncio.gather(synthesis, return_exceptions=True)


class PreparedAudio:
    """Own the prefetched frame even if cancellation happens before iteration."""
    def __init__(self, first, stream):
        self.first, self.stream = first, stream

    def __aiter__(self): return self

    async def __anext__(self):
        if self.first is not None:
            first, self.first = self.first, None
            return first
        return await anext(self.stream)

    async def aclose(self):
        self.first = None
        if close := getattr(self.stream, 'aclose', None): await close()


async def prepare_response_audio(text, config):
    """One producer, first playable frame ready; no output lock or ACK mutation."""
    from deskbot_server.tts.gateway import synthesize_gateway_tts, MING_MODEL, GatewayTtsResult
    if config.model == MING_MODEL:
        async def audio():
            async with aclosing(ming_response_pcm(text, config)) as chunks:
                async for pcm in chunks:
                    async for frame in pcm_frames(GatewayTtsResult(pcm, config.sample_rate, 0)):
                        yield frame
        stream = audio()
    else:
        from deskbot_server.tts.text_split import split_tts_by_punctuation
        async def audio():
            for segment in split_tts_by_punctuation(text):
                result = await synthesize_gateway_tts(segment, config)
                async for frame in pcm_frames(result):
                    yield frame
        stream = audio()
    try:
        first = await anext(stream, None)
        if first is None: raise RuntimeError('FORMAL_AUDIO_EMPTY')
        return PreparedAudio(first, stream)
    except BaseException:
        await stream.aclose()
        raise


async def say_response(session, text, **options):
    """Synthesize before claiming playback, so waiting continues during TTS."""
    waiting = current_waiting()
    if waiting is None:
        handle = session.say(text, **options)
        await handle
        return handle
    if 'audio' not in options:
        options['audio'] = await prepare_response_audio(text, waiting.config)
    stream = options['audio'].__aiter__()
    try:
        first = await anext(stream, None)
        async def prefetched():
            if first is not None:
                yield first
            async for frame in stream:
                yield frame
        options['audio'] = prefetched()
        await waiting.audio_ready()
        handle = session.say(text, **options)
        try:
            await handle
        except asyncio.CancelledError:
            handle.interrupt(force=True)
            await handle.wait_for_playout()
            raise
        return handle
    finally:
        if close := getattr(stream, 'aclose', None):
            await close()


class ConversationFeedback:
    """Bridge VAD admission to the later ASR callback without accepting a second turn."""
    def __init__(self, session, bridge, device_id):
        self.session, self.bridge, self.device_id = session, bridge, device_id
        self.pending = None
        self.admission = None

    def on_user_state_changed(self, event):
        if event.new_state=='speaking':
            gate=getattr(getattr(self.session,'stt',None),'_deskbot_conversation_gate',None)
            if gate is not None:gate.speech_started()
        if (event.old_state != 'speaking' or event.new_state != 'listening'
                or self.bridge.busy or self.pending is not None):
            return
        from deskbot_server.tts.gateway import gateway_enabled, load_gateway_tts_config, supports_waiting
        from deskbot_server.application.meeting_voice import meeting_is_recording
        if not gateway_enabled():
            return
        config = load_gateway_tts_config()
        if not supports_waiting(config):
            return
        ended_at = time.monotonic() - max(0, time.time() - event.created_at)
        meeting = bool(self.device_id and meeting_is_recording(self.device_id))
        self.pending = VoiceWaiting(config, ended_at=ended_at, meeting=meeting,
            play=self._play, clear=self.bridge.clear_waiting)
        self.pending.play_pcm = self._play_pcm
        if not meeting:
            self.admission = asyncio.create_task(self._admit())
            self.admission.add_done_callback(lambda task: None if task.cancelled() else task.exception())

    async def _admit(self):
        if await self.bridge.begin(early_asr=True) and not self.bridge.stopping:
            self.bridge.waiting = self.pending
            self.pending.start(acknowledge=False)

    async def _play(self, text, hum):
        from deskbot_server.tts.gateway import synthesize_gateway_tts
        waiting = self.bridge.waiting
        if waiting is None or self.bridge.stopping:
            return
        try:
            result = await synthesize_gateway_tts(text, waiting.config, cache_only=True)
        except Exception:
            waiting._audio_used = False  # No PCM was queued; a cache miss needs no clear receipt.
            raise
        output = self.bridge.audio_output
        if output is None:
            raise RuntimeError('feedback_output_unavailable')
        async for frame in pcm_frames(result):
            await output.capture_frame(frame)
        if hum:
            from deskbot_server.application.conversation_replies import hum_frames
            # shortcut: this existing original asset has one voice; selected-voice hum needs listening acceptance.
            from livekit import rtc
            resampler = None
            async for frame in hum_frames(max_seconds=3):
                if frame.sample_rate != 16000 and resampler is None:
                    resampler = rtc.AudioResampler(input_rate=frame.sample_rate, output_rate=16000, num_channels=1)
                for converted in resampler.push(frame) if resampler else [frame]:
                    await output.capture_frame(converted)
            if resampler:
                for converted in resampler.flush():
                    await output.capture_frame(converted)
        output.flush()
        await output.wait_for_playout()

    async def _play_pcm(self, pcm, rate):
        import audioop
        from deskbot_server.tts.gateway import GatewayTtsResult
        output=self.bridge.audio_output
        if output is None or self.bridge.stopping:
            raise RuntimeError('feedback_output_unavailable')
        if rate!=16000:
            pcm,_=audioop.ratecv(pcm,2,1,rate,16000,None)
        async for frame in pcm_frames(GatewayTtsResult(pcm,16000,0)):
            await output.capture_frame(frame)
        output.flush()
        await output.wait_for_playout()

    async def take(self, text):
        waiting = self.pending
        if waiting is None:
            return None
        if self.admission is not None:
            await self.admission
            self.admission = None
        if not text:
            await self.asr_finished('')
            return None
        self.pending = None
        if waiting.meeting:
            from deskbot_server.application.conversation_voice import wake_pattern
            if not wake_pattern(waiting.config).match(text):
                await waiting.close()
                return None
        elif not self.bridge.busy or self.bridge.stopping or self.bridge.waiting is not waiting:
            await waiting.close()
            return None
        self.bridge.claim_early_asr()
        return waiting

    async def asr_finished(self, text):
        if text or self.pending is None:
            return
        if self.admission is not None:
            await self.admission
            self.admission = None
        waiting, self.pending = self.pending, None
        try:
            await waiting.close()
            if self.bridge.waiting is waiting and self.bridge.busy and not self.bridge.stopping:
                self.bridge.claim_early_asr()
                await self.bridge.finish()
        finally:
            if self.bridge.waiting is waiting:
                self.bridge.waiting = None

    async def close(self):
        if self.admission is not None and not self.admission.done():
            self.admission.cancel()
            await asyncio.gather(self.admission, return_exceptions=True)
        waiting, self.pending = self.pending, None
        if waiting is not None:
            await waiting.close()
