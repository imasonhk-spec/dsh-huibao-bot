"""Project committed text for speech without another model request."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SpeechAnswer:
    speech_text: str
    display_text: str
    sources: list


def to_speech_answer(text, kind='answer', sources=None):
    display = str(text or '')
    speech = re.sub(r'```[\s\S]*?```', '', display)
    speech = re.sub(r'^\s*\[rag_query[^\]]*\]\s*', '', speech, flags=re.M)
    speech = re.sub(r'〔S\d+〕', '', speech)
    speech = re.sub(r'^\s*来源[:：].*$', '', speech, flags=re.M)
    speech = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', speech)
    speech = re.sub(r'https?://\S+', '', speech)
    lines=speech.splitlines();spoken=[];i=0
    while i<len(lines):
        cells=lambda line:[c.strip() for c in line.strip().strip('|').split('|')]
        if '|' in lines[i] and i+1<len(lines) and re.fullmatch(r'[\s|:\-]+',lines[i+1]) and '-' in lines[i+1]:
            headers=cells(lines[i]);i+=2
            while i<len(lines) and '|' in lines[i]:
                values=cells(lines[i])
                spoken.append('，'.join(f'{h}：{v}' for h,v in zip(headers,values))+'。' if len(headers)==len(values) else '，'.join(values)+'。')
                i+=1
        else:
            spoken.append(lines[i]);i+=1
    speech='\n'.join(spoken)
    speech = re.sub(r'^\s*(?:#{1,6}\s+|[-*]\s+)', '', speech, flags=re.M)
    speech = speech.replace('**', '').replace('`', '')
    speech = re.sub(r'\s+', ' ', speech).strip()
    return SpeechAnswer(speech or '详细内容已显示在页面。', display, list(sources or []))


def progress_due(request_id, started_at, now, status, *, emitted=False):
    return status == 'complex' and now - started_at >= 3 and not emitted
