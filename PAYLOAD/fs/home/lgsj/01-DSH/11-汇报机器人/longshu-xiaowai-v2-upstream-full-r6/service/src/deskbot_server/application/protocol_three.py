"""Fictional two-turn dialogue; never performs authentication or control."""
import hashlib
import json
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass

from deskbot_server.atomic_store import atomic_write_json, file_lock
from deskbot_server.paths import DATA_DIR

FIRST_PARTS = (
    '三号协议开启中。等待中。',
    '正在征求核心终端阿昆认证。认证中。',
    '请输入身份认证指令',
)
FIRST = ''.join(FIRST_PARTS)
SECOND = '认证完成。下面由核心终端阿昆接手所有工作。已开启最高级别保密工作，代码删除按钮已准备完成。请做下一步指示'
RETRY = '请输入任意六位英文字母或数字作为身份认证指令。'
CANCELLED = '三号协议已取消。'
BUSY = '请等待当前播报完成，再输入身份认证指令。'
STATE_DIR = DATA_DIR / 'local' / 'protocol-three'


@dataclass(frozen=True)
class Reply:
    text: str
    token: str | None = None
    speak: bool = True


def _path(scope):
    return STATE_DIR / (hashlib.sha256(str(scope).encode()).hexdigest() + '.json')


def _read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def handle(text, scope, *, now=None):
    from deskbot_server.application.conversation_replies import _command
    now = time.time() if now is None else now
    command = _command(text)
    start = command in {'开启三号协议', '启动三号协议'}
    path = _path(scope)
    if not start and not path.exists():
        return None
    with file_lock(path):
        state = _read(path)
        if state.get('phase') == 'waiting' and now >= state['deadline']:
            state = {}
            atomic_write_json(path, state)
        if state and command == '取消三号协议':
            atomic_write_json(path, {})
            return Reply(CANCELLED, speak=state.get('phase') != 'playing')
        if state.get('phase') == 'playing':
            return Reply(BUSY, speak=False)
        if start:
            token = uuid.uuid4().hex
            atomic_write_json(path, {'phase': 'playing', 'token': token})
            return Reply(FIRST, token)
        if not state:
            return None
        code = unicodedata.normalize('NFKC', str(text or '')).strip()
        code = re.sub(r'^惠宝[，,。\s]*', '', code)
        code = re.sub(r'\s+', '', code).strip('，,。.!！?？')
        code = code.translate(str.maketrans('零〇一二三四五六七八九幺', '001234567891'))
        if re.fullmatch(r'[A-Za-z0-9]{6}', code):
            atomic_write_json(path, {})
            return Reply(SECOND)
        state['deadline'] = now + 60
        atomic_write_json(path, state)
        return Reply(RETRY)


def played(scope, token, success, *, now=None):
    """Only the originating prompt's actual playout may open its next turn."""
    if not token:
        return
    now = time.time() if now is None else now
    path = _path(scope)
    with file_lock(path):
        state = _read(path)
        if state.get('token') != token or state.get('phase') != 'playing':
            return
        atomic_write_json(path, {'phase': 'waiting', 'token': token, 'deadline': now + 60}
                          if success else {})
