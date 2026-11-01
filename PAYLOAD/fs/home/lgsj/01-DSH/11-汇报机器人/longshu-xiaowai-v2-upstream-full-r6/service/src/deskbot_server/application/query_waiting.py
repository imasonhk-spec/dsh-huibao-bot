"""One original query, one scene, and one physical voice owner."""
import asyncio
import re
import time
from contextlib import suppress


def select_intent(text,kind):
    pairs=(('会议纪要','meeting_minutes'),('决议','meeting_decisions'),('待办','meeting_tasks'),('立项','project_approval'),('申报材料','application_materials'),('制度','policy_process'),('版本','document_versions')) if kind=='document' else ()
    return next((intent for word,intent in pairs if word in text),'general')


def opening_slots(text):
    # Only quote a bounded explicit object. Do not infer projects, dates, or intended actions.
    match=re.fullmatch(r'\s*(?:请|帮我|麻烦)?\s*(?:查一下|查找|查询|查)\s*(.{2,24}?)\s*[。？！?！]?\s*',text)
    if not match:return {}
    value=match[1].strip(' ，,。？！?!')
    if not value or re.search(r'[{}【】\n]|一下|这个|那个',value):return {}
    return {'object':value}


class QueryWaiting:
    def __init__(self,*,clock=time.monotonic,wall_clock=time.time,meeting=False):
        self.clock,self.wall_clock,self.meeting=clock,wall_clock,meeting
        self.t0=None;self.deadline=None;self.kind=None;self.calls={}
        self.answer=False;self.expired=False;self.stopped=False;self.failed=False;self.paused=False
        self.final_text_ready=False
        self.started_scene=False;self.scene_active=False;self.release_unit=None
        self.release_answer=False;self.last_hint=None;self.formal_only=meeting

    @property
    def data_ready(self):
        return bool(self.calls) and all(v=='ready' for v in self.calls.values())

    def event(self,event):
        kind=event.get('kind')
        if event.get('sceneId') and not self.started_scene:
            self.started_scene=True;self.formal_only=True;self.release_unit=event.get('releaseUnit')
        if event.get('voiceDeliveryState') in {'expired','stopped','delivered'}:
            self.expired=event['voiceDeliveryState']=='expired';self.stopped=not self.expired
        if kind=='request_waiting_started' and self.t0 is None:
            accepted=event.get('acceptedAt')
            if not isinstance(accepted,(int,float)):return
            elapsed=max(0,self.wall_clock()-accepted/1000)
            self.t0=self.clock()-elapsed
            self.deadline=self.t0+180
            self.kind='document' if event.get('requestKind')=='document' else 'light'
        elif kind=='query_started':
            if event.get('queryKind')=='document':self.kind='document'
            self.calls.setdefault(event['callId'],'started')
        elif kind=='query_data_ready':self.calls[event['callId']]='ready'
        elif kind=='query_answer_ready':
            self.final_text_ready=True
        elif kind=='voice_audio_ready':
            ready_at=self.clock()
            if isinstance(event.get('voiceAudioReadyAt'),(int,float)):
                ready_at-=max(0,self.wall_clock()-event['voiceAudioReadyAt']/1000)
            if self.deadline is not None and ready_at>=self.deadline and not self.answer:self.expired=True
            if self.expired or self.stopped or self.failed:return
            self.answer=True
            if not self.scene_active:self.release_answer=not (self.expired or self.stopped or self.failed)
        elif kind in {'query_failed','failed'}:
            self.failed=True
        elif kind in {'disconnected','stopped'}:
            self.stopped=True
        elif kind=='approval_required':
            self.paused=True;self.formal_only=True
        elif kind=='approval_resumed':self.paused=False

    def action(self):
        if self.expired:return 'expired'
        if self.stopped or self.failed:return 'stopped'
        if self.deadline is not None and not self.answer and self.clock()>=self.deadline:
            self.expired=True;return 'expired'
        if self.answer or self.paused or self.t0 is None or self.scene_active:return None
        if not self.started_scene:
            if self.clock()<self.t0+5:return None
            if self.formal_only:
                if self.last_hint is None or self.clock()>=self.last_hint+10:return 'hint'
                return None
            return 'scene'
        return 'hint' if self.last_hint is not None and self.clock()>=self.last_hint+10 else None

    def scene_started(self,kind):
        self.started_scene=True;self.scene_active=True;self.release_unit=kind

    def boundary(self):
        if self.answer and self.release_unit=='light':
            self.scene_active=False;self.release_answer=not(self.expired or self.stopped or self.failed)

    def scene_finished(self):
        self.scene_active=False;self.last_hint=self.clock()
        if self.answer:self.release_answer=not(self.expired or self.stopped or self.failed)

    def hint_finished(self):self.last_hint=self.clock()


class QueryVoicePlayback:
    """Play complete utterances and explicit pauses through the existing output/drain."""
    def __init__(self,*,waiting,store,voices,session_id,text,play_pcm,play_text,persist,clear,
                 synthesize_opening=None,lock=None):
        self.state=waiting;self.store=store;self.voices=voices;self.session_id=session_id;self.text=text
        self.play_pcm=play_pcm;self.play_text=play_text;self.persist=persist;self.clear=clear
        self.task=None;self.unit_task=None;self.in_pause=False;self.closed=False;self.dynamic=None
        self.changed=asyncio.Event();self.lock=lock or asyncio.Lock();self.expiry_saved=False
        self.synthesize_opening=synthesize_opening;self.selection=None;self.dynamic_attempted=False
        self.audio_owned=False;self.admitting=False

    def event(self,event):
        self.state.event(event);self.changed.set()
        if event.get('kind') in {'request_waiting_started','query_started'} and not self.state.formal_only and not self.state.started_scene:
            self._prepare()
        if self.task is None:self.task=asyncio.create_task(self._run())

    def _prepare(self):
        q=self.state
        if self.admitting or q.answer or q.expired or q.stopped or q.failed:return
        if self.selection and self.selection['scene']['kind']!=q.kind:
            self.store.release(self.selection['ref']);self.selection=None
        if self.selection is None:
            try:self.selection=self.store.acquire(q.kind,select_intent(self.text,q.kind),self.voices,self.session_id)
            except (ValueError,OSError):return
        if not self.selection or self.dynamic_attempted or not self.synthesize_opening:return
        self.dynamic_attempted=True
        opening=self.selection['scene']['utterances'][0];slots=opening_slots(self.text)
        if opening.get('type')!='dynamic_opening' or not set(opening.get('requiredSlots',[]))<=slots.keys():return
        text=opening['textTemplate']
        for key,value in slots.items():text=text.replace('{{'+key+'}}',value)
        if not text or len(text)>120 or '{{' in text:return
        self.dynamic=asyncio.create_task(self.synthesize_opening(text))
        self.dynamic.add_done_callback(lambda task:None if task.cancelled() else task.exception())

    async def _hint(self):
        q=self.state
        text=('答复已生成，正在准备语音。' if q.final_text_ready else '资料已返回，正在整理答复。' if q.data_ready
              else '正在查阅相关资料，请稍候。' if any(v=='started' for v in q.calls.values()) and q.kind=='document'
              else '还在处理您的问题，请稍候。')
        async with self.lock:
            if not(self.state.answer or self.state.expired or self.state.stopped or self.state.failed):
                await self.play_text(text,False)
        self.state.hint_finished()

    async def _scene(self):
        q=self.state
        if self.closed or q.answer or q.expired or q.stopped or q.failed or q.paused or q.started_scene:
            return
        self._prepare();selection=self.selection
        if not selection:
            q.formal_only=True;await self._hint();return
        replacement=None
        if self.dynamic and self.dynamic.done() and not self.dynamic.cancelled():
            try:replacement=self.dynamic.result()
            except Exception:pass
        # A late opening is never awaited or played after the trigger.
        if self.dynamic and not self.dynamic.done():self.dynamic.cancel()
        try:
            first=True
            for pcm,rate,u in self.store.audio_chunks(selection):
                if self.closed or q.expired or q.stopped or q.failed or q.paused:break
                if first and replacement:
                    data,hz=replacement
                    duration=selection['scene']['durationSec']-len(pcm)/rate/2+len(data)/hz/2
                    low,high=selection['scene']['durationTargetSec']
                    if low<=duration<=high:pcm,rate=data,hz
                if u.get('pause'):
                    # Fatal conditions may clear a queued long pause, normal answer waits it out.
                    self.in_pause=True
                else:self.in_pause=False
                async with self.lock:
                    if self.closed or q.expired or q.stopped or q.failed or q.paused:break
                    if first:
                        if q.answer:break
                        self.admitting=True
                        receipt=await self.persist('pending',{'sceneId':selection['scene'].get('id'),
                            'packageVersion':selection.get('packageVersion'), 'releaseUnit':selection['scene']['kind']})
                        if receipt and receipt.get('state') in {'expired','stopped','delivered'}:
                            q.event({'voiceDeliveryState':receipt['state']})
                        if self.closed or q.answer or q.expired or q.stopped or q.failed or q.paused:break
                        q.scene_started(selection['scene']['kind'])
                        first=False
                    self.audio_owned=True
                    await self.play_pcm(pcm,rate)
                    self.audio_owned=False
                if u.get('pause'):
                    q.boundary()
                    if q.release_answer:break
        except (ValueError,OSError,RuntimeError):
            q.formal_only=True
        finally:
            self.admitting=False;self.in_pause=False;self.store.release(selection['ref']);self.selection=None;q.scene_finished();self.changed.set()

    async def _run(self):
        try:
            while not self.closed:
                action=self.state.action()
                if action in {'expired','stopped'}:
                    if self.in_pause and self.unit_task and not self.unit_task.done():
                        self.unit_task.cancel();await asyncio.gather(self.unit_task,return_exceptions=True);await self.clear()
                        self.audio_owned=False
                    if action=='expired' and not self.expiry_saved:
                        self.expiry_saved=True;await self.persist('expired','deadline')
                        # Current speech reaches its natural boundary, no remaining pause or scene.
                        if self.unit_task:await asyncio.gather(self.unit_task,return_exceptions=True)
                        async with self.lock:await self.play_text('这次语音等待先结束，后续结果请在原会话查看。',False)
                    return
                if self.state.answer:
                    if self.unit_task:await asyncio.gather(self.unit_task,return_exceptions=True)
                    return
                if (not self.unit_task or self.unit_task.done()) and action in {'scene','hint'}:
                    self.unit_task=asyncio.create_task(self._scene() if action=='scene' else self._hint())
                self.changed.clear()
                with suppress(asyncio.TimeoutError):await asyncio.wait_for(self.changed.wait(),.1)
        finally:self.changed.set()

    async def answer_ready(self,event):
        self.event(event)
        if self.task:await self.task
        if self.unit_task:await asyncio.gather(self.unit_task,return_exceptions=True)
        return self.state.release_answer and not(self.state.expired or self.state.stopped or self.state.failed)

    async def close(self):
        self.closed=True;self.state.stopped=True
        for task in (self.unit_task,self.task,self.dynamic):
            if task and not task.done():task.cancel()
        await asyncio.gather(*(t for t in (self.unit_task,self.task,self.dynamic) if t),return_exceptions=True)
        if self.audio_owned:
            await self.clear();self.audio_owned=False
        if self.selection:
            self.store.release(self.selection['ref']);self.selection=None
