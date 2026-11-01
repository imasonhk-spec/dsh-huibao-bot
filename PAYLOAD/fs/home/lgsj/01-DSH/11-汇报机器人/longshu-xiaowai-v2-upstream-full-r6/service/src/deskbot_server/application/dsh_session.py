"""Native account session transport over the existing authenticated local bridge."""
import json
import urllib.request
import asyncio
import logging

logger = logging.getLogger("deskbot-server")

HOST = 'http://127.0.0.1:31097/api/huibao-bot/sessions'


def authenticated_request(device_id, route, method='GET', body=None, namespace='sessions'):
    from deskbot_server.application.meeting_voice import binding_for
    from deskbot_server.paths import DATA_DIR
    if not binding_for(device_id):
        raise ValueError('WRONG_DEVICE')
    key = (DATA_DIR / 'local' / 'meeting-control.key').read_text().strip()
    if len(key) < 32:
        raise ValueError('SERVICE_AUTH_REQUIRED')
    if namespace not in {'sessions', 'meeting'}:
        raise ValueError('ROUTE_NOT_FOUND')
    target = HOST.rsplit('/', 1)[0] + '/' + namespace + route
    return urllib.request.Request(target, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'X-Meeting-Control-Key': key,
                 'X-Huibao-Device-Id': device_id})


def speakable(event, session_id, request_id):
    return (event.get('kind') == 'answer' and not event.get('snapshot')
            and event.get('sessionId') == session_id
            and event.get('requestId') == request_id and bool(event.get('text')))


class DshSessionClient:
    def __init__(self, device_id, *, request=None):
        self.device_id = device_id
        self._request = request or self._http

    async def _http(self, method, route, body=None):
        def run():
            with urllib.request.urlopen(authenticated_request(self.device_id, route, method, body), timeout=30) as response:
                return json.load(response)
        return await asyncio.to_thread(run)

    async def current(self):
        return await self._request('GET', '/current')

    async def ensure_current(self, request_id):
        binding = await self.current()
        if binding.get('sessionId'):
            return binding
        # Persist the caller's stable identity at the host before creating anything.
        return await self._request('POST', '/new', {'requestId': 'first:admin'})

    async def submit_turn(self, text, request_id, session_id, binding_revision):
        return await self._request('POST', '/prompt', {'text': text, 'requestId': request_id,
            'sessionId': session_id, 'bindingRevision': binding_revision, 'mainVoice':'current'})

    async def follow_turn(self, session_id, request_id):
        import httpx
        from urllib.parse import quote
        route = '/' + quote(session_id, safe='') + '/events'
        if request_id:
            route += '?requestId=' + quote(request_id, safe='')
        req = authenticated_request(self.device_id, route)
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10), trust_env=False) as client:
            async with client.stream('GET', req.full_url, headers=dict(req.header_items())) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        yield json.loads(line)


def claim_playback(device_id, session_id, request_id, seq):
    """Claim before handing audio to the player; an unknown outcome is never replayed."""
    import hashlib
    from deskbot_server.paths import DATA_DIR
    identity = ':'.join(map(str, [device_id, session_id, request_id, seq]))
    file = DATA_DIR / 'local' / 'dsh-playback' / (hashlib.sha256(identity.encode()).hexdigest() + '.json')
    file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with file.open('x') as stream:
            json.dump({'deviceId': device_id, 'sessionId': session_id, 'requestId': request_id,
                       'seq': seq, 'playback': 'started-not-verified'}, stream)
        return True
    except FileExistsError:
        return False


async def speak_native_turn(device_id, text, request_id, session, *, expected_binding=None):
    import time
    from deskbot_server.application.voice_reply import to_speech_answer
    from deskbot_server.application.voice_waiting import current_waiting, say_response,prepare_response_audio
    from deskbot_server.application.query_waiting import QueryWaiting,QueryVoicePlayback
    from deskbot_server.query_wait_scenes import SceneStore
    from deskbot_server.tts.gateway import persona_configs,voice_declarations
    waiting=current_waiting();client=DshSessionClient(device_id)
    if expected_binding is not None:
        current=await client.current()
        if (current.get('sessionId'),current.get('bindingRevision'))!=tuple(expected_binding):return
        binding={'sessionId':expected_binding[0],'bindingRevision':expected_binding[1]}
    else:
        binding=await client.ensure_current(request_id)
    sid,revision=binding['sessionId'],binding['bindingRevision']
    q=QueryWaiting(meeting=bool(waiting and waiting.meeting))
    started=time.monotonic();driver=None;terminal=False;selection_changed=False;answer_work=None
    def timing(phase):
        logger.info('[dsh-turn] request_id=%s phase=%s elapsed_ms=%d',request_id,phase,int((time.monotonic()-started)*1000))
    async def persist(state,reason):
        metadata=reason if isinstance(reason,dict) else {}
        return await client._request('POST','/voice-state',{'sessionId':sid,'requestId':request_id,
            'bindingRevision':revision,'state':state,'reason':None if metadata else reason,**{k:v for k,v in metadata.items() if v is not None}})
    if waiting and waiting.play_pcm:
        configs=persona_configs(waiting.config)
        async def opening(text):
            from deskbot_server.tts.gateway import synthesize_gateway_tts
            result=await synthesize_gateway_tts(text,configs['huibao'])
            return result.pcm,result.sample_rate
        driver=QueryVoicePlayback(waiting=q,store=SceneStore(),voices=voice_declarations(configs),
            session_id=sid,text=text,play_pcm=waiting.play_pcm,play_text=waiting.play,
            persist=persist,clear=waiting.clear,synthesize_opening=opening,lock=waiting.lock)
    timing('asr_text_ready')
    events=client.follow_turn(sid,None).__aiter__()
    snapshot=await events.__anext__()
    # Recovery proves a prior owner, never permission to restart physical speech.
    if any(row.get('requestId')==request_id for row in snapshot.get('recovered',[])):
        await events.aclose()
        return
    try:
        accepted=await client.submit_turn(text,request_id,sid,revision)
        timing('native_accepted')
        if accepted.get('replayed') or accepted.get('voiceDeliveryState') in {'expired','stopped','delivered','playing'} or accepted.get('sceneId'):
            await events.aclose()
            return
        if waiting:
            # Check the server froze the same voice as this physical request.
            frozen=voice_declarations(persona_configs(waiting.config))
            persona=accepted.get('personaId')
            if persona and (accepted.get('voiceId')!=frozen.get(persona,{}).get('voiceId') or accepted.get('configVersion')!=frozen.get(persona,{}).get('configVersion')):
                await persist('stopped','owner_unknown')
                return
            waiting.start(acknowledge=True)
        accepted_event={**accepted,'kind':'request_waiting_started'}
        if driver:driver.event(accepted_event)
        else:q.event(accepted_event)
    except BaseException:
        await events.aclose()
        raise

    async def deliver_answer(event):
        prepared=None
        try:
            current=await client.current()
            if current.get('sessionId')!=sid or current.get('bindingRevision')!=revision:return
            timing('final_answer_received')
            answer=to_speech_answer(event['text'],'answer',event.get('sources'))
            if waiting:
                prepared=await prepare_response_audio(answer.speech_text,waiting.config)
            ready={'kind':'voice_audio_ready','voiceAudioReadyAt':int(time.time()*1000)}
            if driver:driver.event(ready)
            else:q.event(ready)
            receipt=await persist('pending',{'voiceAudioReadyAt':ready['voiceAudioReadyAt']})
            if receipt.get('state') in {'expired','stopped','delivered'}:return
            if waiting:
                waiting.ready=True
                if waiting.timer and not waiting.timer.done() and not waiting._audio_used:waiting.timer.cancel()
            can_speak=await driver.answer_ready(ready) if driver else q.release_answer
            if not can_speak:return
            if waiting and waiting.timer:await asyncio.gather(waiting.timer,return_exceptions=True)
            current=await client.current()
            if current.get('sessionId')!=sid or current.get('bindingRevision')!=revision:return
            if claim_playback(device_id,sid,request_id,event.get('seq')):
                receipt=await persist('playing',None)
                if receipt.get('state')!='playing':return
                timing('speech_handoff')
                await say_response(session,answer.speech_text,allow_interruptions=False,add_to_chat_ctx=False,
                                   **({'audio':prepared} if prepared is not None else {}))
                if waiting:waiting.delivery=(persist,'delivered','playback_drained')
        except asyncio.CancelledError:raise
        except Exception:
            if driver:
                driver.event({'kind':'failed'})
                if driver.unit_task:await asyncio.gather(driver.unit_task,return_exceptions=True)
            await persist('stopped','tts_failed')
            if waiting:
                async with waiting.lock:
                    await waiting.play('语音准备失败，文字结果已保留在原会话。',False)
        finally:
            if prepared is not None and (close:=getattr(prepared,'aclose',None)):await close()

    async def consume():
        nonlocal terminal,answer_work
        try:
            async for event in events:
                if event.get('snapshot') or event.get('kind')=='snapshot':continue
                if event.get('requestId')!=request_id or event.get('sessionId')!=sid:continue
                kind=event.get('kind')
                if kind in {'request_waiting_started','query_started','query_data_ready'}:
                    if driver:driver.event(event)
                    else:q.event(event)
                elif kind=='query_failed':
                    if driver:
                        driver.event(event)
                        if driver.unit_task:await asyncio.gather(driver.unit_task,return_exceptions=True)
                    else:q.event(event)
                    await persist('stopped','query_failed')
                    await say_response(session,'这次查询执行失败，后续信息请在原会话查看。',allow_interruptions=False,add_to_chat_ctx=False)
                    # Close this voice subscription only; the accepted native request remains authoritative.
                    return
                elif kind=='approval':
                    pending=event.get('status')=='waiting'
                    e={'kind':'approval_required' if pending else 'approval_resumed'}
                    if driver:driver.event(e)
                    else:q.event(e)
                    if driver and driver.unit_task:await asyncio.gather(driver.unit_task,return_exceptions=True)
                    notice='这个请求需要审批，请在DSH当前会话页面确认。' if pending else {'rejected':'审批已拒绝，该操作未获批准。','cancelled':'审批已取消，该操作未获批准。','unavailable':'审批暂不可用，该操作未获批准。'}.get(event.get('status'))
                    if notice and claim_playback(device_id,sid,request_id,'approval:'+str(event.get('seq'))):
                        await say_response(session,notice,allow_interruptions=False,add_to_chat_ctx=False)
                elif kind=='query_answer_ready':
                    if driver:driver.event(event)
                    else:q.event(event)
                    if answer_work is None:answer_work=asyncio.create_task(deliver_answer(event))
                elif kind=='complete':
                    terminal=True
                    if answer_work:await answer_work
                    return
                elif kind=='failed':
                    q.event(event)
                    if driver:
                        driver.event(event)
                        if driver.unit_task:await asyncio.gather(driver.unit_task,return_exceptions=True)
                    replies={'aborted':'已取消这次请求。','blocked':'这次请求被阻止，请在DSH原会话查看原因。','error':'这次请求执行失败，请在DSH原会话查看详情。','max-tokens':'这次回答达到原输出上限，请在原会话查看已保存内容。','interrupted':'这次请求被中断，已保存内容可在原会话查看。'}
                    if not q.expired and claim_playback(device_id,sid,request_id,'failed:'+str(event.get('seq'))):
                        await say_response(session,replies.get(event.get('reason'),'这次请求未完成，请在DSH原会话查看状态。'),allow_interruptions=False,add_to_chat_ctx=False)
                    await persist('stopped','playback_cleared');terminal=True;return
                elif kind=='disconnected':raise RuntimeError('DSH_REQUEST_STATE_UNCONFIRMED')
            raise RuntimeError('DSH_STREAM_ENDED_WITHOUT_TERMINAL')
        finally:await events.aclose()

    async def watch():
        nonlocal selection_changed
        while True:
            await asyncio.sleep(.2)
            if q.expired and (driver is None or (driver.task and driver.task.done())):
                if driver is None:await persist('expired','deadline')
                return
            if driver is None:q.action()
            try:current=await client.current()
            except Exception:continue
            if current.get('sessionId')!=sid or current.get('bindingRevision')!=revision:
                selection_changed=True
                await persist('stopped','selection_changed')
                return

    work,watcher=asyncio.create_task(consume()),asyncio.create_task(watch())
    try:
        done,_=await asyncio.wait({work,watcher},return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            await watcher
            if selection_changed:
                if waiting:await waiting.close()
                session.interrupt(force=True)
            work.cancel();await asyncio.gather(work,return_exceptions=True)
        else:await work
    except BaseException:
        if not q.expired:
            try:await persist('stopped','disconnected')
            except Exception:pass # Unknown receipt is never retried or taken as permission to replay.
        raise
    finally:
        watcher.cancel()
        if not work.done():work.cancel()
        await asyncio.gather(work,watcher,return_exceptions=True)
        if answer_work:
            if not answer_work.done():answer_work.cancel()
            await asyncio.gather(answer_work,return_exceptions=True)
        if driver:await driver.close()
