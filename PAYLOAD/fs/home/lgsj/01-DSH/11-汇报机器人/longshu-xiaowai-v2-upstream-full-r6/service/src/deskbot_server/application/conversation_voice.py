"""Ordinary RTC conversation with dynamic, same-session meeting control."""
from contextlib import nullcontext
import logging
import re
import time
import uuid

logger = logging.getLogger('deskbot-server')
_WAKE_NAMES={'huibao':('惠宝','汇报','回报','慧宝','汇宝','会宝','惠保'),'akun':('阿昆','阿坤')}


def wake_pattern(config=None,*,persona=None):
    # Internal meeting/fixed-response protocol always carries one canonical name.
    from deskbot_server.tts.gateway import persona_id
    role=persona if persona is not None else persona_id(config)
    return re.compile(r'^\s*'+_WAKE_NAMES[role][0]+r'[，,。！？!?\s]*')


def voice_wake_pattern(*,persona):
    names='(?:'+'|'.join(_WAKE_NAMES[persona])+')'
    # Double Huibao calls distinguish the name from ordinary report requests.
    pause=r'[，,。！？!?、；;：:…．.\s]*'
    prefix=names+pause+names if persona=='huibao' else names
    return re.compile(r'^\s*'+prefix+pause)


class WakeConversation:
    def __init__(self,*,require_wake_word=True,clock=time.monotonic,persona='huibao'):
        self._clock=clock;self._require_wake_word=require_wake_word
        self.persona=persona;self.binding=None;self.meeting=False
        self._deadline=0.;self.pending_wake=False;self.started_in_window=False
        self.admission=None

    def reset(self):
        self._deadline=0.;self.pending_wake=False;self.started_in_window=False
        self.admission=None

    def update(self,*,persona,binding,meeting):
        if (persona,binding,meeting)!=(self.persona,self.binding,self.meeting):self.reset()
        self.persona,self.binding,self.meeting=persona,binding,meeting

    def drained(self):
        if self.pending_wake and not self.meeting:
            self._deadline=self._clock()+8.;self.pending_wake=False

    def touch(self):
        # SDK speaking-state changes do not prove physical device playback drained.
        pass

    def speech_started(self):
        self.started_in_window=bool(self._deadline and self._clock()<self._deadline and not self.meeting)

    def _admit(self,text,recognition_id):
        self.admission={'text':text,'recognitionId':recognition_id,'persona':self.persona,
                        'binding':self.binding,'meeting':self.meeting}
        return text

    def take_admission(self,text,*,persona,binding,meeting):
        admission,self.admission=self.admission,None
        if admission and admission['recognitionId'] and all(admission[k]==v for k,v in
                (('text',text),('persona',persona),('binding',binding),('meeting',meeting))):
            return admission
        return None

    def filter(self,text,*,recognition_id=None):
        self.admission=None
        text=str(text or '').strip()
        if not text or not text.strip(' ，,。！!？?'):return ''
        name='阿昆' if self.persona=='akun' else '惠宝'
        other='huibao' if self.persona=='akun' else 'akun'
        if re.match(r'^\s*(?:'+'|'.join(_WAKE_NAMES[other])+')',text):self.reset();return ''
        if text.strip(' ，,。！!')=='停一下':
            self.reset();return self._admit('停一下',recognition_id)
        wake=voice_wake_pattern(persona=self.persona).match(text)
        words=text[wake.end():].strip() if wake else text
        # User-approved ASR spellings become the selected role's protocol name.
        if wake:text=name+('，'+words if words else '')
        if self.meeting:
            self.reset()
            return self._admit(text,recognition_id) if wake and words else ''
        if wake:
            if not words:
                self.reset();self.pending_wake=True;return self._admit(name,recognition_id)
            self.reset()
            return self._admit(text,recognition_id)
        admitted=self.started_in_window or bool(self._deadline and self._clock()<self._deadline)
        if not self.pending_wake:self.reset()
        return self._admit(text,recognition_id) if admitted or not self._require_wake_word else ''


def install_conversation_turn(agent, session, stop_response, *, device_id=None):
    from deskbot_server.application.dialogue_worker import install_worker_dialogue
    bridge = install_worker_dialogue(session, device_id)
    from deskbot_server.application.voice_waiting import ConversationFeedback, say_response, waiting_scope
    feedback = None
    if bridge is not None and hasattr(session, 'on'):
        feedback = ConversationFeedback(session, bridge, device_id)
        session._deskbot_voice_feedback = feedback
        session.on('user_state_changed')(feedback.on_user_state_changed)
    original_completed = agent.on_user_turn_completed
    pending_meeting = False
    qa_session_id = str(uuid.uuid4())
    protocol_scope = device_id or qa_session_id
    prompt_token = None
    wake_ack = False

    async def respond(turn_ctx, new_message, admission=None):
        nonlocal pending_meeting, prompt_token, wake_ack
        from deskbot_server.application.meeting_voice import meeting_is_recording, send_meeting_control, binding_for
        from deskbot_server.application.conversation_replies import fixed_reply, is_song_request, sing
        async def still_admitted():
            if admission is None:return True
            from deskbot_server.application.dsh_session import DshSessionClient
            from deskbot_server.tts.gateway import persona_id
            current=await DshSessionClient(device_id).current()
            return (admission['persona']==persona_id() and
                    admission['binding'][:3]==(device_id,current.get('sessionId'),current.get('bindingRevision')) and
                    admission['meeting']==bool(device_id and meeting_is_recording(device_id)))
        if not await still_admitted():
            gate=getattr(getattr(session,'stt',None),'_deskbot_conversation_gate',None)
            if gate is not None:gate.reset()
            raise stop_response()
        message = new_message
        text = str(getattr(message, 'text_content', '') or '').strip()
        recording = bool(device_id and meeting_is_recording(device_id))
        wake = wake_pattern().match(text)
        words = text[wake.end():].strip() if wake else text
        command_words = words.rstrip(' 。！!？?，,')
        control = bool(pending_meeting or re.fullmatch(
            r'(?:开启|结束)会议模式|开始(?:录音|记录)|(?:停止|结束)录音|(?:生成|制作)(?:会议)?纪要(?:[，,：:].*)?|(?:录音)?状态|录了多久|录音多久|录音情况|停一下', command_words))
        if not recording and (not control or words.strip(' ，,。！!') == '取消三号协议'):
            from deskbot_server.application.protocol_three import handle
            protocol = handle(text, protocol_scope)
            if protocol:
                prompt_token = protocol.token
                if protocol.speak:
                    options = {}
                    if protocol.token:
                        from deskbot_server.application.conversation_replies import protocol_frames
                        options['audio'] = protocol_frames()
                    await say_response(session, protocol.text, allow_interruptions=False,
                                      add_to_chat_ctx=False, **options)
                raise stop_response()
        if words.strip(' ，,。！!') == '停一下':
            session.interrupt()
            raise stop_response()
        meeting_command = (wake or admission) and control
        if device_id and meeting_command:
            request_id = str(getattr(message, 'id', '') or uuid.uuid4())
            try:
                if bridge and hasattr(bridge, 'request'):
                    await bridge.request('meeting_control')
                if not await still_admitted():raise stop_response()
                command=text if wake else ('阿昆' if admission['persona']=='akun' else '惠宝')+'，'+words
                result = await send_meeting_control(device_id, command, request_id)
                if bridge and hasattr(bridge, 'request'):
                    await bridge.request('sync_recording')
                pending_meeting = bool(result.get('pending'))
                reply = result.get('reply', '')
            except stop_response:raise
            except Exception:
                reply = '会议操作结果尚未确认，请在会议助手按原会话查看状态，不要重复提交。'
            if reply:
                await say_response(session, reply, allow_interruptions=False, add_to_chat_ctx=False)
            raise stop_response()
        if recording and not wake:
            raise stop_response()
        if recording:
            if device_id and binding_for(device_id):
                from deskbot_server.application.dsh_session import speak_native_turn
                await speak_native_turn(device_id, words, str(getattr(message, 'id', '') or uuid.uuid4()), session,**({'expected_binding':admission['binding'][1:3]} if admission else {}))
            raise stop_response()
        text = words
        if text in {'惠宝','阿昆'} or (wake and not words):
            wake_ack=True
            await say_response(session, '我在，请说。', allow_interruptions=False, add_to_chat_ctx=False)
        elif text.strip(' ，,。！!') == '停一下':
            session.interrupt()
        elif is_song_request(text):
            await sing(session)
        elif (reply := fixed_reply(text)) is not None:
            await say_response(session, reply, allow_interruptions=False, add_to_chat_ctx=False)
        else:
            if device_id and binding_for(device_id):
                from deskbot_server.application.dsh_session import speak_native_turn
                await speak_native_turn(device_id, text, str(getattr(message, 'id', '') or uuid.uuid4()), session,**({'expected_binding':admission['binding'][1:3]} if admission else {}))
                raise stop_response()
            await original_completed(turn_ctx, message)
            if bridge:
                await session.generate_reply(user_input=message, chat_ctx=turn_ctx,
                                             input_modality='audio', allow_interruptions=False)
            return
        raise stop_response()

    async def completed(turn_ctx, new_message):
        nonlocal prompt_token, wake_ack
        wake_ack=False
        from deskbot_server.application.protocol_three import played
        admission=None
        gate=getattr(getattr(session,'stt',None),'_deskbot_conversation_gate',None)
        if gate is not None:
            from deskbot_server.application.dsh_session import DshSessionClient
            from deskbot_server.application.meeting_voice import meeting_is_recording
            from deskbot_server.tts.gateway import persona_id
            try:
                current=await DshSessionClient(device_id).current()
                epoch=getattr(bridge,'early_asr_epoch',None)
                if epoch is None:epoch=getattr(bridge,'epoch',None)
                context=dict(persona=persona_id(),binding=(device_id,current.get('sessionId'),current.get('bindingRevision'),epoch),
                             meeting=bool(device_id and meeting_is_recording(device_id)))
                # Validate before take() consumes the early-ASR epoch.
                gate.update(**context)
                admission=gate.take_admission(str(getattr(new_message,'text_content','') or '').strip(),**context)
            except Exception:
                gate.reset()
            logger.info('[wake-gate] phase=completed admitted=%s epoch=%s early_epoch=%s',bool(admission),getattr(bridge,'epoch',None),getattr(bridge,'early_asr_epoch',None))
            if admission is None:
                if feedback is not None:await feedback.asr_finished('')
                raise stop_response()
        if bridge is None:
            prompt_token = None
            try:
                return await respond(turn_ctx, new_message, admission)
            except stop_response:
                played(protocol_scope, prompt_token, True)
                raise
            except BaseException:
                played(protocol_scope, prompt_token, False)
                raise
        text = str(getattr(new_message, 'text_content', '') or '').strip()
        waiting = await feedback.take(text) if feedback is not None else None
        early = waiting is not None and bridge.waiting is waiting and bridge.busy and not bridge.stopping
        if not early and (bridge.busy or not await bridge.begin()):
            if waiting is not None:
                await waiting.close()
            raise stop_response()
        if waiting is not None:
            bridge.waiting = waiting
            if not early:
                waiting.start()
        prompt_token = None
        turn_epoch=getattr(bridge,'epoch',None)
        try:
            with waiting_scope(waiting) if waiting is not None else nullcontext():
                try:
                    await respond(turn_ctx, new_message, admission)
                except stop_response:
                    pass
                except Exception:
                    played(protocol_scope, prompt_token, False)
                    if waiting is not None:
                        await waiting.pause()
                    if not getattr(bridge, 'stopping', False):
                        try:
                            await say_response(session, '原请求状态尚未确认，请在当前会话查看，不要重复提交。',
                                               allow_interruptions=False, add_to_chat_ctx=False)
                        finally:
                            await bridge.failed()
                    raise stop_response()
                except BaseException:
                    played(protocol_scope, prompt_token, False)
                    if waiting is not None:
                        await waiting.pause()
                    if not getattr(bridge, 'stopping', False):
                        await bridge.failed()
                    raise
        finally:
            if waiting is not None:
                await waiting.close()
                if bridge.waiting is waiting:
                    bridge.waiting = None
        try:
            await bridge.finish()
        except BaseException:
            played(protocol_scope, prompt_token, False)
            raise
        if waiting is not None and getattr(waiting,'delivery',None):
            persist,state,reason=waiting.delivery
            await persist(state,reason)
        if wake_ack:
            gate=getattr(getattr(session,'stt',None),'_deskbot_conversation_gate',None)
            if gate is not None:
                from deskbot_server.application.dsh_session import DshSessionClient
                from deskbot_server.tts.gateway import persona_id
                current=await DshSessionClient(device_id).current()
                from deskbot_server.application.meeting_voice import meeting_is_recording
                persona=persona_id()
                meeting=bool(device_id and meeting_is_recording(device_id))
                same=(admission is not None and admission['persona']==persona and
                      admission['binding'][:3]==(device_id,current.get('sessionId'),current.get('bindingRevision')) and
                      not meeting and not admission['meeting'] and not bridge.stopping and
                      not bridge.busy and bridge.epoch==turn_epoch+1)
                gate.update(persona=persona,binding=(device_id,current.get('sessionId'),current.get('bindingRevision'),bridge.epoch),meeting=meeting)
                if same:gate.pending_wake=True;gate.drained()
                else:gate.reset()
        played(protocol_scope, prompt_token, True)
        raise stop_response()

    async def exceeded(event):
        # Long turns must not bypass the recording-only boundary.
        raise stop_response()

    agent.on_user_turn_completed = completed
    agent.on_user_turn_exceeded = exceeded
