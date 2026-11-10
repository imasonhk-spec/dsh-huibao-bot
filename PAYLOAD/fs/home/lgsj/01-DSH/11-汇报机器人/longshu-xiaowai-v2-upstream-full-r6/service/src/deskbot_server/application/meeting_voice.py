"""Bound meeting turns use existing STT/TTS without ordinary chat generation."""
import asyncio
import json
import re
import urllib.request
import uuid


def binding_for(device_id):
    from deskbot_server.paths import DATA_DIR
    file = DATA_DIR / 'local' / 'meeting-control.json'
    if not file.exists():
        return None
    binding = json.loads(file.read_text())
    return binding if binding.get('account') == 'admin' and binding.get('deviceId') == device_id else None


def is_control(text):
    return bool(re.match(r'^\s*惠宝[，,。\s]*', text)) or text.strip(' ，,。！!') == '停一下'


def canonical_control_text(text,persona):
    # The existing meeting protocol has a fixed wake prefix, not a new voice identity.
    return re.sub(r'^\s*阿昆','惠宝',text,count=1) if persona=='akun' else text


def role_control_reply(reply,persona):
    return reply.replace('说惠宝','说阿昆') if persona=='akun' else re.sub(r'说惠宝(?!惠宝)','说惠宝惠宝',reply)


def meeting_is_recording(device_id):
    if not binding_for(device_id):
        return False
    from deskbot_server.paths import DATA_DIR
    # The RTC worker observes metadata; it must never open a second recording store owner.
    for file in (DATA_DIR/'local'/'meeting-recordings').glob('*.json'):
        row = json.loads(file.read_text())
        if row.get('deviceId') == device_id and row.get('recordingState') in {'preparing','recording'}:
            return True
    return False


async def send_meeting_control(device_id, text, request_id):
    from deskbot_server.paths import DATA_DIR
    from deskbot_server.tts.gateway import persona_id
    role=persona_id()
    text=canonical_control_text(text,role)
    key = (DATA_DIR / 'local' / 'meeting-control.key').read_text().strip()
    if len(key) < 32:
        raise ValueError('meeting_credential_required')
    def request():
        body = json.dumps(dict(deviceId=device_id, text=text, requestId=request_id)).encode()
        req = urllib.request.Request('http://127.0.0.1:31097/api/meeting-assistant/voice-control/internal',
                                     data=body, headers={'Content-Type':'application/json','X-Meeting-Control-Key':key})
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    # Timeout/unknown is never retried. Original turn requestId is preserved at the host.
    result=await asyncio.to_thread(request)
    if isinstance(result.get('reply'),str):result['reply']=role_control_reply(result['reply'],role)
    return result


async def send_meeting_qa(device_id, text, request_id, session_id):
    from deskbot_server.paths import DATA_DIR
    import httpx
    if not binding_for(device_id):
        return {'handled': False}
    key = (DATA_DIR / 'local' / 'meeting-control.key').read_text().strip()
    if len(key) < 32:
        raise ValueError('meeting_credential_required')
    async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
        response = await client.post('http://127.0.0.1:31097/api/meeting-assistant/voice-control/qa/internal',
            headers={'X-Meeting-Control-Key': key},
            json=dict(deviceId=device_id, text=text, requestId=request_id, sessionId=session_id))
        response.raise_for_status()
        return response.json()


def install_meeting_turn(agent, session, send, stop_response):
    async def completed(turn_ctx, new_message):
        message = new_message
        text = str(getattr(message, 'text_content', '') or '').strip()
        if text.strip(' ，,。！!') == '停一下':
            session.interrupt()
        elif is_control(text):
            request_id = str(getattr(message, 'id', '') or uuid.uuid4())
            try:
                result = await send(text, request_id)
                reply = result.get('reply', '')
            except Exception:
                reply = '会议操作结果尚未确认，请在会议助手按原会话查看状态，不要重复提交。'
            if reply:
                await session.say(reply, allow_interruptions=True, add_to_chat_ctx=False)
        raise stop_response()

    async def exceeded(event):
        # Long ordinary meeting speech must not produce the SDK's default chat reply.
        raise stop_response()

    agent.on_user_turn_completed = completed
    agent.on_user_turn_exceeded = exceeded
