"""Authenticated worker audio uses the existing Core playback owners and receipts."""
import asyncio
import base64
import uuid

from deskbot_server.application.dialogue_turn import DialogueTurn


class CoreDialogue:
    def __init__(self, binding, *, owner=None):
        self.binding = binding
        self.device_id = binding.rtc_session.device_id
        self.owner = owner or self._browser
        self.gate = DialogueTurn(self.notify)
        self.physical_pcm = bytearray()
        self.muted = False
        self.segment_task = None
        self.session_id = str(uuid.uuid4())
        self.selected_session = None
        self.output_owner = None
        self.meeting_control_pending = False

    def status(self):
        return {**self.gate.status(), 'session_id': self.session_id}

    def refresh_context(self):
        from deskbot_server.session_store import get_current_session
        current = get_current_session()
        selected = current.get('session_id') if current else None
        if selected != self.selected_session:
            self.selected_session = selected
            self.session_id = str(uuid.uuid4())

    def _browser(self):
        from deskbot_server.application.browser_audio import browser_audio_owner
        return browser_audio_owner(self.device_id)

    def notify(self, event):
        owner = self.owner()
        if owner and not owner.closed:
            try:
                owner.output.put_nowait(event)
            except asyncio.QueueFull:
                owner.failed.set()

    def ack(self, turn_id, sequence, kind):
        accepted = self.gate.ack(turn_id, sequence, kind)
        if accepted and kind == 'playback_cleared':
            self.discard_input()
        return accepted

    def discard_input(self):
        while not self.binding.pcm_queue.empty():
            self.binding.pcm_queue.get_nowait()
        source = self.binding.rtc_session.audio_source
        if source is not None:
            source.clear_queue()

    async def mic(self, muted):
        if self.owner():
            return
        from deskbot_server.application.meeting_voice import meeting_is_recording
        if muted and meeting_is_recording(self.device_id):
            muted = False
        if muted == self.muted:
            return
        from deskbot_server.application.chat_flow import _set_half_duplex_mic
        ok = await _set_half_duplex_mic(self.binding.device_session,
                                      device_id=self.device_id, mode='mute' if muted else 'open')
        if not ok:
            self.gate.fail('microphone_control_unconfirmed')
            raise ValueError('microphone_control_unconfirmed')
        self.muted = muted

    async def dispatch(self, body):
        action = body.get('action')
        turn_id = body.get('turn_id')
        if action == 'state':
            if self.gate.busy and not self.gate.stopping and not self.meeting_control_pending:
                await self.mic(True)
            return self.status()
        if action == 'begin':
            if not isinstance(turn_id, str) or not 1 <= len(turn_id) <= 128:
                raise ValueError('invalid_turn_id')
            admitted = self.gate.begin(turn_id)
            if admitted:
                self.refresh_context()
                self.output_owner = self.owner()
                self.meeting_control_pending = False
                self.discard_input()
                self.physical_pcm.clear()
                await self.mic(True)
            return {**self.status(), 'admitted': admitted}
        if (turn_id != self.gate.turn_id or not self.gate.busy or self.gate.stopping
                or (self.gate.state == 'error' and action != 'failed')):
            raise ValueError('stale_dialogue_turn')
        if action == 'audio':
            raw = body.get('pcm')
            if not isinstance(raw, str) or len(raw) > 18000:
                raise ValueError('invalid_dialogue_pcm')
            pcm = base64.b64decode(raw, validate=True)
            if not pcm or len(pcm) % 2 or len(pcm) > 12800:
                raise ValueError('invalid_dialogue_pcm')
            self.gate.audio()
            owner = self.output_owner
            if owner:
                if owner.closed or self.owner() is not owner:
                    self.gate.fail('browser_audio_disconnected')
                    raise ValueError('browser_audio_disconnected')
                try:
                    owner.output.put_nowait(pcm)
                except asyncio.QueueFull:
                    self.gate.fail('browser_audio_overrun')
                    raise ValueError('browser_audio_overrun') from None
                # The existing browser queue is bounded to two seconds.
                await asyncio.sleep(len(pcm) / 32000)
            else:
                self.physical_pcm.extend(pcm)
                if len(self.physical_pcm) > 32 * 1024 * 1024:
                    self.gate.fail('audio_too_large')
                    raise ValueError('audio_too_large')
        elif action == 'drain':
            self.segment_task = asyncio.current_task()
            try:
                if self.output_owner:
                    if self.output_owner.closed or self.owner() is not self.output_owner:
                        self.gate.fail('browser_audio_disconnected')
                        raise ValueError('browser_audio_disconnected')
                    self.notify(self.gate.barrier())
                    await self.gate.wait()
                elif self.physical_pcm:
                    await self._physical_play()
            finally:
                self.segment_task = None
        elif action == 'clear_waiting':
            await self.clear_waiting()
        elif action == 'meeting_control':
            self.meeting_control_pending = True
            await self.mic(False)
        elif action == 'sync_recording':
            self.meeting_control_pending = False
            await self.mic(True)
            if self.owner():
                from deskbot_server.application.browser_audio import browser_audio_bridge
                row = await asyncio.to_thread(browser_audio_bridge().store.status, self.device_id)
                self.notify({'type':'status', 'recording':row or {'recordingState':'idle'}})
        elif action == 'finish':
            self.meeting_control_pending = False
            self.discard_input()
            if self.gate.drained and self.gate.state == 'busy' and not self.gate.stopping:
                await self.mic(False)
                self.gate.finish(turn_id)
        elif action == 'failed':
            self.meeting_control_pending = False
            if self.gate.has_audio and not self.gate.drained:
                self.gate.fail('playback_unconfirmed')
            else:
                await self.mic(False)
                self.gate.finish(turn_id)
        else:
            raise ValueError('invalid_dialogue_action')
        return self.status()

    async def clear_waiting(self):
        """Clear a feedback segment with a receipt while retaining the admitted turn."""
        turn_id = self.gate.turn_id
        def still_owned():
            if (self.gate.turn_id != turn_id or self.gate.state != 'busy'
                    or self.gate.stopping):
                raise ValueError('stale_dialogue_turn')
        still_owned()
        self.physical_pcm.clear()
        task = self.segment_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        still_owned()
        if self.output_owner:
            owner = self.output_owner
            if owner.closed or self.owner() is not owner:
                self.gate.fail('browser_audio_disconnected')
                raise ValueError('browser_audio_disconnected')
            while not owner.output.empty():
                owner.output.get_nowait()
            self.notify(self.gate.barrier(clear=True, keep_busy=True))
            await self.gate.wait()
            still_owned()
        else:
            await self.binding.device_session.send_pb_wire('{"type":"pb_cancel"}')
            still_owned()
            from deskbot_server.pb.mic_signal import build_mic_signal_pb
            from deskbot_server.application.chat_flow import _send_pb_pairs_body
            request_id = uuid.uuid4().hex
            payload = build_mic_signal_pb(mic='hold', req=request_id)
            result = await _send_pb_pairs_body(self.binding.device_session,
                pairs=[(payload, [])], pb_req=request_id, device_id=self.device_id, n_pb=1)
            still_owned()
            if result != 'played':
                self.gate.fail('stop_unconfirmed')
                raise ValueError('stop_unconfirmed')
            self.gate.drained = True

    async def _physical_play(self):
        from deskbot_server.pb.wire import build_pb_wire_pairs
        from deskbot_server.application.chat_flow import _send_pb_pairs_body
        pcm = bytes(self.physical_pcm)
        self.physical_pcm.clear()
        segments = [{'pcm': pcm, 'ms': max(1, round(len(pcm)/32)), 'phoneme': 'sil'}]
        pairs, request_id, count, _ = await asyncio.to_thread(build_pb_wire_pairs, segments,
            {'output_codec': 'opus'}, sample_rate=16000, request_id=uuid.uuid4().hex,
            display_anim=False)
        result = await _send_pb_pairs_body(self.binding.device_session, pairs=pairs,
            pb_req=request_id, device_id=self.device_id, n_pb=count)
        if result != 'played':
            self.gate.fail('playback_unconfirmed')
            raise ValueError('playback_unconfirmed')
        self.gate.drained = True

    async def run_manual(self, action, downlink):
        turn_id = str(uuid.uuid4())
        if not (await self.dispatch({'action':'begin','turn_id':turn_id}))['admitted']:
            raise ValueError('dialogue_busy')
        self.gate.audio()
        self.segment_task = asyncio.current_task()
        old_mic = downlink.half_duplex_media_mic
        downlink.half_duplex_media_mic = False
        try:
            result = await action()
            if getattr(result, 'playback_status', None) != 'played':
                self.gate.fail('playback_unconfirmed')
            else:
                self.gate.drained = True
                await self.dispatch({'action':'finish','turn_id':turn_id})
            return result
        except BaseException:
            if not self.gate.stopping:
                self.gate.fail('playback_unconfirmed')
            raise
        finally:
            downlink.half_duplex_media_mic = old_mic
            self.segment_task = None

    async def stop(self):
        if not self.gate.busy or (self.gate.stopping and self.gate.state != 'error'):
            return
        self.gate.stop()
        self.physical_pcm.clear()
        self.discard_input()
        if self.output_owner and not self.owner():
            self.gate.fail('browser_audio_disconnected')
            return
        if self.owner():
            # Clear queued server PCM before placing the explicit clear barrier.
            owner = self.owner()
            while not owner.output.empty():
                owner.output.get_nowait()
            self.notify(self.gate.barrier(clear=True))
        else:
            task = self.segment_task
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await self.binding.device_session.send_pb_wire('{"type":"pb_cancel"}')
            # A fresh empty PB must complete after cancellation; mic acceptance is insufficient.
            try:
                from deskbot_server.pb.mic_signal import build_mic_signal_pb
                from deskbot_server.application.chat_flow import _send_pb_pairs_body
                request_id = uuid.uuid4().hex
                payload = build_mic_signal_pb(mic='hold', req=request_id)
                result = await _send_pb_pairs_body(self.binding.device_session,
                    pairs=[(payload, [])], pb_req=request_id, device_id=self.device_id, n_pb=1)
                if result != 'played':
                    raise ValueError('stop_unconfirmed')
                await self.mic(False)
            except Exception:
                self.gate.fail('stop_unconfirmed')
            else:
                self.gate._idle()

    async def disconnect(self):
        if self.gate.busy:
            self.gate.stop()
            self.physical_pcm.clear()
            self.discard_input()
            if self.segment_task and self.segment_task is not asyncio.current_task():
                self.segment_task.cancel()
                await asyncio.gather(self.segment_task, return_exceptions=True)
            self.gate.fail('browser_audio_disconnected')


def dialogue_for(binding):
    value = getattr(binding, '_dialogue', None)
    if value is None:
        value = binding._dialogue = CoreDialogue(binding)
    return value
