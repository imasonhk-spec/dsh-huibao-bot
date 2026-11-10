"""Keep SDK input disabled until Core confirms this answer's actual playback."""
import asyncio
import base64
import os
import time
import uuid


class WorkerDialogue:
    def __init__(self, session, device_id, url, token):
        self.session, self.device_id = session, device_id
        self.url, self.token = url, token
        self.session_id = str(uuid.uuid4())
        self.turn_id = ''
        self.busy = False
        self.stopping = False
        self.monitor = None
        self.turn_task = None
        self.epoch = 0
        self.attached = False
        self._client = None
        self.audio_output = None
        self.early_asr_epoch = None
        self.early_asr_reserved = False
        self.recognizing = False
        self.waiting = None

    async def request(self, action, **values):
        import httpx
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=100, trust_env=False)
        response = await self._client.post(self.url,
            headers={'X-Deskbot-RTC-Bridge': self.token},
            json={'device_id': self.device_id, 'action': action, 'turn_id': self.turn_id, **values})
        response.raise_for_status()
        return response.json()

    def input_enabled(self, enabled):
        self.session.input.set_audio_enabled(enabled)

    async def begin(self, *, early_asr=False):
        if self.busy:
            return False
        self.busy = True
        if self.monitor is not None and not self.monitor.done():
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        self.turn_task = None
        self.early_asr_epoch = self.epoch if early_asr else None
        self.early_asr_reserved = self.recognizing if early_asr else False
        self.epoch += 1
        self.stopping = False
        self.turn_id = str(uuid.uuid4())
        if self.audio_output is not None:
            self.audio_output.error = None
        self.input_enabled(False)
        try:
            result = await self.request('begin')
        except Exception:
            # An unknown admission cannot safely enable the next utterance.
            raise
        if not result.get('admitted'):
            self.early_asr_epoch = None
            self.turn_id = result.get('turn_id', self.turn_id)
            self.monitor = asyncio.create_task(self._monitor())
            return False
        self.session_id = result.get('session_id', self.session_id)
        self.turn_task = None if early_asr else asyncio.current_task()
        self.monitor = asyncio.create_task(self._monitor())
        return True

    def accept_asr(self, epoch):
        return (self.busy and not self.stopping and self.early_asr_epoch is not None
                and epoch == self.early_asr_epoch)

    def claim_early_asr(self):
        self.early_asr_epoch = None
        self.turn_task = asyncio.current_task()

    async def clear_waiting(self):
        if self.audio_output is not None:
            self.audio_output.clear_buffer()
            if self.audio_output.flush_task is not None:
                await asyncio.gather(self.audio_output.flush_task, return_exceptions=True)
        if self.busy and not self.stopping:
            result = await self.request('clear_waiting')
            if result.get('state') != 'busy' or result.get('stopping'):
                raise RuntimeError('waiting_clear_unconfirmed')

    async def _monitor(self):
        while self.busy:
            await asyncio.sleep(.2)
            try:
                state = await self.request('state')
            except Exception:
                continue
            if state.get('stopping') and not self.stopping:
                self.stopping = True
                if self.waiting is not None:
                    await self.waiting.close()
                self.session.interrupt(force=True)
                if self.turn_task is not None and not self.turn_task.done():
                    self.turn_task.cancel()
            if state.get('state') == 'idle':
                self.busy = False
                self.epoch += 1
                self.input_enabled(True)
                return

    async def finish(self):
        if self.audio_output is not None:
            try:
                await self.audio_output.wait_for_playout()
            except Exception:
                await self.failed()
                raise
        result = await self.request('finish')
        if result.get('state') == 'idle':
            self.busy = False
            self.epoch += 1
            self.input_enabled(True)

    async def failed(self):
        result = await self.request('failed')
        if result.get('state') == 'idle':
            self.busy = False
            self.epoch += 1
            self.input_enabled(True)

    def attach(self):
        if not self.attached:
            self.audio_output = core_audio_output(self)
            self.session.output.audio = self.audio_output
            self.attached = True

    async def close(self):
        self.stopping = True
        feedback = getattr(self.session, '_deskbot_voice_feedback', None)
        if feedback is not None:
            await feedback.close()
        if self.waiting is not None:
            await self.waiting.close()
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        if self.turn_task is not None and self.turn_task is not asyncio.current_task() and not self.turn_task.done():
            self.turn_task.cancel()
            await asyncio.gather(self.turn_task, return_exceptions=True)
        if self.audio_output is not None:
            await self.audio_output.aclose()
        if self._client:
            await self._client.aclose()


def core_audio_output(bridge):
    from livekit.agents.voice import io

    class CoreAudioOutput(io.AudioOutput):
        def __init__(self):
            super().__init__(label='Deskbot Core confirmed playback', sample_rate=16000,
                             capabilities=io.AudioOutputCapabilities(pause=False))
            self.pending = bytearray()
            self.duration = 0.0
            self.flush_task = None
            self.error = None

        async def _send(self, pcm):
            if not bridge.busy or bridge.stopping:
                raise RuntimeError('dialogue_output_not_owned')
            await bridge.request('audio', pcm=base64.b64encode(pcm).decode('ascii'))

        async def capture_frame(self, frame):
            if frame.sample_rate != 16000 or frame.num_channels != 1:
                raise ValueError('dialogue_audio_format')
            if self.flush_task is not None:
                await asyncio.gather(self.flush_task, return_exceptions=True)
            if self.error is not None:
                raise self.error
            await super().capture_frame(frame)
            if not self.duration:
                self.on_playback_started(created_at=time.time())
            pcm = bytes(frame.data)
            self.duration += len(pcm) / 32000
            self.pending.extend(pcm)
            while len(self.pending) >= 6400:
                pcm = bytes(self.pending[:6400]);del self.pending[:6400]
                await self._send(pcm)

        def flush(self):
            super().flush()
            if not self.duration:
                return
            duration, self.duration = self.duration, 0.0
            pcm = bytes(self.pending)
            self.pending.clear()
            async def drain():
                try:
                    if pcm:
                        await self._send(pcm)
                    state = await bridge.request('drain')
                    if state.get('state') == 'error':
                        raise RuntimeError('dialogue_playback_unconfirmed')
                    interrupted = bool(state.get('stopping'))
                except asyncio.CancelledError:
                    raise  # clear_buffer accounts for this segment synchronously.
                except Exception as exc:
                    self.error = exc
                    self.on_playback_finished(playback_position=0, interrupted=True)
                    raise
                else:
                    self.on_playback_finished(playback_position=duration, interrupted=interrupted)
            self.flush_task = asyncio.create_task(drain())
            # The SDK also awaits playout; consume task errors without hiding them from that owner.
            self.flush_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())

        async def wait_for_playout(self):
            result = await super().wait_for_playout()
            if self.error is not None:
                raise self.error
            return result

        def clear_buffer(self):
            super().flush()
            capturing = bool(self.duration)
            self.duration = 0.0
            self.pending.clear()
            if self.flush_task and not self.flush_task.done():
                self.flush_task.cancel()
                capturing = True
            if capturing:
                self.on_playback_finished(playback_position=0, interrupted=True)

        async def aclose(self):
            self.clear_buffer()
            if self.flush_task is not None:
                await asyncio.gather(self.flush_task, return_exceptions=True)

    return CoreAudioOutput()


def install_worker_dialogue(session, device_id):
    existing = getattr(session, '_deskbot_dialogue_bridge', None)
    if existing:
        return existing
    url = os.environ.get('DESKBOT_RTC_TOOL_BRIDGE_URL', '')
    token = os.environ.get('DESKBOT_RTC_TOOL_BRIDGE_TOKEN', '')
    if not url or not token or not device_id:
        return None
    bridge = WorkerDialogue(session, device_id,
        url.replace('/internal/rtc/tools', '/internal/rtc/dialogue'), token)
    session._deskbot_dialogue_bridge = bridge
    return bridge
