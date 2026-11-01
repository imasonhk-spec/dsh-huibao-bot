"""Authenticated same-origin PCM websocket; no model credentials leave Core."""
import asyncio
import json
import logging

from deskbot_server.application.browser_audio import browser_audio_bridge, _tickets

logger = logging.getLogger('deskbot-server')

_MESSAGES = {
    'browser_audio_busy': '另一个网页正在使用这台机器人的音频，请先在原网页退出。',
    'device_not_connected': '机器人尚未就绪，请检查USB连接和语音服务。',
    'meeting_save_incomplete': '本场录音尚未保存完成，暂未切换音源，请查看会议助手。',
    'invalid_browser_ticket': '连接授权已失效，请重新点击使用电脑音频。',
}


async def handle_browser_audio(websocket, *, bridge=None, tickets=None):
    bridge = bridge or browser_audio_bridge()
    tickets = tickets or _tickets
    lease, sender, saving = None, None, None

    async def send(value):
        await websocket.send(json.dumps(value, ensure_ascii=False))

    async def error(exc):
        code = str(exc) if isinstance(exc, (ValueError, PermissionError)) else 'browser_audio_unavailable'
        await send({'type':'error', 'error':code,
                    'message':_MESSAGES.get(code, '网页音频已中断，请检查连接；已有会议录音会保留。')})

    async def output():
        try:
            while True:
                if lease.failed.is_set():
                    await websocket.close(code=1011, reason='browser_audio_overrun')
                    return
                try:
                    value = await asyncio.wait_for(lease.output.get(), timeout=2)
                except asyncio.TimeoutError:
                    row = await asyncio.to_thread(bridge.store.status, lease.device_id)
                    await send({'type':'status', 'recording':row or {'recordingState':'idle'}})
                    continue
                if isinstance(value, bytes):
                    await asyncio.wait_for(websocket.send(value), timeout=5)
                else:
                    await send(value)

        finally:
            await websocket.close()

    async def release_and_respond():
        try:
            await bridge.release(lease, intentional=True)
        except (ValueError, OSError, TimeoutError) as exc:
            await error(exc)
            return
        await send({'type':'released'})
        await websocket.close()

    try:
        first = await asyncio.wait_for(websocket.recv(), timeout=10)
        if not isinstance(first, str) or len(first) > 4096:
            raise PermissionError('invalid_browser_ticket')
        payload = json.loads(first)
        if not isinstance(payload, dict):
            raise PermissionError('invalid_browser_ticket')
        device = tickets.consume(payload.get('ticket'))
        lease = await bridge.acquire(device)
        await send({'type':'ready', 'device_id':device, 'sample_rate':16000})
        sender = asyncio.create_task(output())
        while True:
            value = await asyncio.wait_for(websocket.recv(), timeout=12)
            if isinstance(value, bytes):
                if not lease.closed:
                    await lease.feed(value)
                continue
            if not isinstance(value, str) or len(value) > 4096:
                raise ValueError('invalid_audio_control')
            payload = json.loads(value)
            if not isinstance(payload, dict):
                raise ValueError('invalid_audio_control')
            kind = payload.get('type')
            if kind == 'ping':
                await send({'type':'pong'})
            elif kind == 'release':
                if saving is None or saving.done():
                    saving = asyncio.create_task(release_and_respond())
            elif kind in {'playback_drained', 'playback_cleared'}:
                dialogue = getattr(lease.binding, '_dialogue', None)
                if dialogue is not None:
                    dialogue.ack(payload.get('turn_id'), payload.get('sequence'), kind)
            elif kind == 'stop':
                dialogue = getattr(lease.binding, '_dialogue', None)
                if dialogue is not None:
                    await dialogue.stop()
            else:
                raise ValueError('invalid_audio_control')
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        try:
            await error(exc)
        except Exception:
            pass
    finally:
        if saving is not None:
            # Reconcile the original accepted save; never replay an unknown write.
            await asyncio.gather(saving, return_exceptions=True)
        if sender is not None:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        if lease is not None and not lease.closed:
            try:
                await bridge.release(lease, intentional=False)
            except Exception:
                logger.exception('[browser_audio] recording preservation failed; ownership retained device=%s', lease.device_id)
        await websocket.close()
