"""Requested fixed dialogue; protocol three is fiction and invokes no tools."""
import re
import wave
from pathlib import Path

INTRO = '您好，我叫惠宝，我是AI汇智团队开发研究的AI桌面机器人1.0，我可以为您解决很多事情，包括会议纪要，生成PPT，安排工作计划等工作，很高兴为您服务'
CUTE = '可爱有什么用，又不能替你开会。'
FEATURES = '我个头不大，能帮的忙不少：会议纪要、PPT、工作计划，都可以找我。还有一个特点——嘴有点贫，干活认真。'
MODEL_INTRO = '我的底座是 huiwen-2-Agron，记忆模型融合了 deepseek-r1-1.5-huiwen。至于语音模型，集成了阿昆混合语言模型。'
from deskbot_server.application.protocol_three import FIRST as PROTOCOL
SONG_START = '行。先说好，我五音不全，你忍一下。'
SONG_END = '唱完了。你要说难听，我就当没听见。'
HUM_AUDIO = Path(__file__).resolve().parents[1] / 'assets' / 'huibao-hum.wav'

_REPLIES = {
    '介绍一下自己': INTRO, '介绍自己': INTRO, '自我介绍': INTRO, '你是谁': INTRO,
    '介绍一下你自己': INTRO, '介绍你自己': INTRO,
    '你能告诉我你用的什么模型吗包括语音模型和大模型': MODEL_INTRO,
    '你好可爱': CUTE, '你觉得你可爱嘛': CUTE, '你觉得你可爱吗': CUTE,
    '你可爱吗': CUTE,
    '你有什么特点嘛': FEATURES, '你有什么特点吗': FEATURES,
    '你有什么特点': FEATURES, '你的特点是什么': FEATURES,
    '开启三号协议': PROTOCOL, '启动三号协议': PROTOCOL,
}


def _command(text):
    text = re.sub(r'[\s，,。.!！?？、：:]+', '', str(text or ''))
    return text.removeprefix('惠宝').removeprefix('阿昆')


def fixed_reply(text, *, persona=None):
    if persona is None:
        from deskbot_server.tts.gateway import persona_id
        persona=persona_id()
    reply=_REPLIES.get(_command(text))
    return reply.replace('我叫惠宝','我叫阿昆') if reply and persona=='akun' else reply


def is_song_request(text):
    return _command(text) in {'唱首歌', '唱一首歌', '给我唱首歌', '给我唱一首歌'}


async def hum_frames(*, max_seconds=None):
    from livekit import rtc
    with wave.open(str(HUM_AUDIO), 'rb') as audio:
        rate = audio.getframerate()
        assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
        remaining = audio.getnframes() if max_seconds is None else min(audio.getnframes(), int(rate * max_seconds))
        while remaining:
            pcm = audio.readframes(min(rate // 50, remaining))
            if not pcm:
                break
            remaining -= len(pcm) // 2
            yield rtc.AudioFrame(data=pcm, sample_rate=rate, num_channels=1, samples_per_channel=len(pcm) // 2)


async def protocol_frames():
    from livekit import rtc
    from deskbot_server.tts.gateway import synthesize_gateway_tts
    result = await synthesize_gateway_tts(PROTOCOL)
    frame_bytes = result.sample_rate // 50 * 2
    for offset in range(0, len(result.pcm), frame_bytes):
        pcm = result.pcm[offset:offset + frame_bytes]
        yield rtc.AudioFrame(data=pcm, sample_rate=result.sample_rate,
                             num_channels=1, samples_per_channel=len(pcm) // 2)


async def sing(session):
    from deskbot_server.application.voice_waiting import say_response
    opening = await say_response(session, SONG_START, allow_interruptions=False, add_to_chat_ctx=False)
    if opening.interrupted:
        return
    song = await say_response(session, '', audio=hum_frames(), allow_interruptions=False, add_to_chat_ctx=False)
    if not song.interrupted:
        await say_response(session, SONG_END, allow_interruptions=False, add_to_chat_ctx=False)
