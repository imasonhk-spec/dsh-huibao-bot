"""One device's complete answer, distinct from its individual audio segments."""
import asyncio


class DialogueTurn:
    def __init__(self, notify):
        self.notify = notify
        self.turn_id = ''
        self.state = 'idle'
        self.sequence = 0
        self.stopping = False
        self.has_audio = False
        self.drained = True
        self.pending = None
        self.clear_keeps_busy = False
        self.error = None
        self.confirmed = asyncio.Event()

    @property
    def busy(self):
        return self.state != 'idle'

    def status(self):
        status = {'type': 'turn_state', 'turn_id': self.turn_id, 'state': self.state,
                  'stopping': self.stopping}
        if self.error:
            status['error'] = self.error
        return status

    def begin(self, turn_id):
        if self.busy:
            return False
        self.turn_id, self.state = turn_id, 'busy'
        self.stopping, self.has_audio, self.drained = False, False, True
        self.pending = None
        self.clear_keeps_busy = False
        self.error = None
        self.notify(self.status())
        return True

    def audio(self):
        if not self.busy or self.stopping or self.state == 'error':
            raise ValueError('stale_dialogue_audio')
        self.has_audio, self.drained = True, False
        self.sequence += 1
        self.pending = None

    def barrier(self, *, clear=False, keep_busy=False):
        self.sequence += 1
        self.confirmed.clear()
        self.clear_keeps_busy = clear and keep_busy
        self.pending = 'playback_cleared' if clear else 'playback_drained'
        return {'type': 'clear' if clear else 'playback_end',
                'turn_id': self.turn_id, 'sequence': self.sequence}

    def ack(self, turn_id, sequence, kind):
        if (not self.busy or turn_id != self.turn_id or type(sequence) is not int
                or sequence != self.sequence or kind != self.pending):
            return False
        self.pending = None
        self.drained = True
        self.confirmed.set()
        if kind == 'playback_cleared' and not self.clear_keeps_busy:
            self._idle()
        return True

    async def wait(self, timeout=30):
        try:
            await asyncio.wait_for(self.confirmed.wait(), timeout)
        except TimeoutError:
            self.fail('playback_unconfirmed')
            raise ValueError('playback_unconfirmed') from None

    def finish(self, turn_id):
        if turn_id != self.turn_id or self.state != 'busy' or self.stopping or not self.drained:
            return False
        self._idle()
        return True

    def _idle(self):
        self.state = 'idle'
        self.error = None
        self.notify(self.status())

    def stop(self):
        self.stopping = True
        self.sequence += 1
        self.pending = None

    def fail(self, code):
        self.state = 'error'
        self.error = code
        self.notify({**self.status(), 'error': code})
