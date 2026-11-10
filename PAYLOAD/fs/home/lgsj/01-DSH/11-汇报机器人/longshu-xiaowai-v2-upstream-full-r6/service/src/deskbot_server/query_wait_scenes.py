"""Versioned query voice assets. Native query/history and selected voice are untouched."""
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import uuid
import wave
import zipfile

MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
SLOTS = {'object','intent','time','place','focus','meeting_scope','topic','project_name','query_focus'}

class ScenePackageError(ValueError):
    pass


def _require(ok, field):
    if not ok:
        raise ScenePackageError(field)


def _id(value):
    return isinstance(value,str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',value)) and value not in {'.','..'}


def read_audio(raw):
    _require(len(raw)<=MAX_AUDIO_BYTES,'audio too large')
    try:
        with wave.open(io.BytesIO(raw),'rb') as w:
            _require(w.getnchannels()==1 and w.getsampwidth()==2 and w.getcomptype()=='NONE','audio must be mono PCM16 WAV')
            rate=w.getframerate();frames=w.getnframes();pcm=w.readframes(frames)
            _require(8000<=rate<=48000 and frames>0 and len(pcm)==frames*2,'audio data invalid')
            return pcm,rate
    except (wave.Error,EOFError) as e:
        raise ScenePackageError('audio cannot decode') from e


def validate_manifest(m, read):
    _require(isinstance(m,dict) and m.get('schemaVersion')==1,'schemaVersion')
    _require(_id(m.get('packageId')) and _id(m.get('version')),'packageId/version')
    voices=m.get('voices',{})
    _require(set(voices)=={'huibao','akun'},'voices')
    for voice in voices.values():
        _require(isinstance(voice,dict) and set(voice)=={'voiceId','configVersion'} and all(isinstance(v,str) and 0<len(v)<=200 for v in voice.values()),'voice declaration')
    scenes=m.get('scenes');_require(isinstance(scenes,list) and 0<len(scenes)<=40,'scenes')
    seen=set()
    for scene in scenes:
        sid=scene.get('id');_require(_id(sid) and sid not in seen,'scene id');seen.add(sid)
        _require(scene.get('kind') in {'document','light'} and isinstance(scene.get('title'),str) and 0<len(scene['title'])<=150,f'{sid}: kind/title')
        _require(isinstance(scene.get('intents'),list) and 0<len(scene['intents'])<=30 and all(_id(x) for x in scene['intents']),f'{sid}: intents')
        bounds=[30,45] if scene['kind']=='document' else [10,20]
        _require(scene.get('durationTargetSec')==bounds,f'{sid}: durationTargetSec')
        utterances=scene.get('utterances');_require(isinstance(utterances,list) and 0<len(utterances)<=12,f'{sid}: utterances')
        duration=0;uids=set()
        for i,u in enumerate(utterances):
            field=f'{sid}/{u.get("id")}'
            _require(_id(u.get('id')) and u['id'] not in uids,field+': id');uids.add(u['id'])
            _require(u.get('personaId') in voices,field+': personaId')
            _require(type(u.get('pauseAfterMs')) is int and 2000<=u['pauseAfterMs']<=4000,field+': pauseAfterMs')
            _require(u.get('type') in {'fixed','dynamic_opening'},field+': type')
            if u['type']=='dynamic_opening':
                _require(i==0 and u['personaId']=='huibao',field+': only opening can be dynamic')
                text=u.get('fallbackText');template=u.get('textTemplate','');slots=u.get('requiredSlots',[])
                _require(isinstance(template,str) and isinstance(slots,list) and set(slots)<=SLOTS and set(re.findall(r'{{(\w+)}}',template))==set(slots),field+': slots')
            else:
                text=u.get('text')
            _require(isinstance(text,str) and 0<len(text)<=500 and not re.search(r'{{|}}|【|】',text),field+': text')
            audio=u.get('audio','');parts=PurePosixPath(audio)
            _require(isinstance(audio,str) and len(parts.parts)==2 and parts.parts[0]=='audio' and parts.parts[1].endswith('.wav') and '\\' not in audio and not parts.is_absolute(),field+': audio path')
            raw=read(audio);_require(hashlib.sha256(raw).hexdigest()==u.get('sha256'),field+': sha256')
            pcm,rate=read_audio(raw);seconds=len(pcm)/rate/2
            _require(.15<=seconds<=90 and .025<=seconds/len(text)<=2,field+': duration/text')
            duration+=seconds+u['pauseAfterMs']/1000
        _require(bounds[0]<=duration<=bounds[1],f'{sid}: actual duration {duration:.3f}s outside {bounds}')
        scene['durationSec']=round(duration,3)
    return m


class SceneStore:
    def __init__(self,root=None):
        if root is None:
            from deskbot_server.paths import DATA_DIR
            root=DATA_DIR/'local'/'query-wait-scenes'
        self.root=Path(root)

    @contextmanager
    def _locked(self,write=True):
        # GET list must not initialize or seed a store.
        if not self.root.exists() and not write:
            yield {'packages':{},'refs':{},'last':{}}
            return
        self.root.mkdir(parents=True,exist_ok=True)
        with (self.root/'.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            state=json.loads((self.root/'state.json').read_text()) if (self.root/'state.json').exists() else {'packages':{},'refs':{},'last':{}}
            yield state
            if write:
                tmp=self.root/'state.tmp';tmp.write_text(json.dumps(state,ensure_ascii=False));os.replace(tmp,self.root/'state.json')

    def list(self):
        with self._locked(False) as s:
            return [dict(r) for r in s['packages'].values()]

    def _key(self,pid,version):
        _require(_id(pid) and _id(version),'packageId/version')
        return pid+'@'+version

    def import_package(self,source):
        with zipfile.ZipFile(source) as z:
            infos=z.infolist();names=[i.filename for i in infos]
            _require(len(names)==len(set(names)) and len(names)<=1000 and sum(i.file_size for i in infos)<=MAX_PACKAGE_BYTES,'ZIP resources/duplicates')
            for i in infos:
                p=PurePosixPath(i.filename)
                _require(not p.is_absolute() and '..' not in p.parts and '\\' not in i.filename and not i.is_dir() and (i.external_attr>>16)&0o170000 != 0o120000,'ZIP path/symlink')
                _require(i.file_size<=MAX_AUDIO_BYTES and not i.flag_bits&1,'ZIP entry/encryption')
            _require('manifest.json' in names,'manifest.json missing')
            try:
                manifest=json.loads(z.read('manifest.json'))
                manifest=validate_manifest(manifest,z.read)
            except (KeyError,json.JSONDecodeError,TypeError,AttributeError) as e:
                raise ScenePackageError('invalid manifest or missing audio') from e
            expected={'manifest.json'}|{u['audio'] for scene in manifest['scenes'] for u in scene['utterances']}
            _require(set(names)==expected,'ZIP unexpected files')
            digest=hashlib.sha256(json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            key=self._key(manifest['packageId'],manifest['version'])
            with self._locked() as s:
                old=s['packages'].get(key)
                if old:
                    _require(old['digest']==digest,'same version has different content; increase version')
                    return {**old,'existing':True}
                staging=Path(tempfile.mkdtemp(prefix='.import-',dir=self.root))
                try:
                    (staging/'audio').mkdir()
                    for name in expected-{'manifest.json'}:(staging/name).write_bytes(z.read(name))
                    (staging/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False))
                    os.replace(staging,self.root/key)
                finally:
                    if staging.exists():shutil.rmtree(staging)
                row={'packageId':manifest['packageId'],'version':manifest['version'],'digest':digest,'enabled':False,'listened':[],
                     'scenes':[{k:v for k,v in scene.items() if k in {'id','kind','title','durationSec'}} for scene in manifest['scenes']]}
                s['packages'][key]=row
                return row

    def manifest(self,pid,version):
        key=self._key(pid,version)
        try:return json.loads((self.root/key/'manifest.json').read_text())
        except FileNotFoundError as e:raise ScenePackageError('package not found') from e

    def mark_listened(self,pid,version,scene_id):
        key=self._key(pid,version)
        with self._locked() as s:
            row=s['packages'].get(key);_require(row and scene_id in {x['id'] for x in row['scenes']},'scene not found')
            if scene_id not in row['listened']:row['listened'].append(scene_id)

    def enable(self,pid,version,enabled,voices):
        key=self._key(pid,version)
        with self._locked() as s:
            row=s['packages'].get(key);_require(row,'package not found')
            if enabled:
                m=validate_manifest(self.manifest(pid,version),lambda p:(self.root/key/p).read_bytes())
                _require(m['voices']==voices,'voice configuration mismatch')
                _require(set(row['listened'])=={x['id'] for x in row['scenes']},'audition each scene before enable')
                for r in s['packages'].values():r['enabled']=False
            row['enabled']=bool(enabled)

    def delete(self,pid,version):
        key=self._key(pid,version)
        with self._locked() as s:
            row=s['packages'].get(key);_require(row and not row['enabled'],'disable package before delete')
            _require(key not in s['refs'].values(),'package in use')
            shutil.rmtree(self.root/key);del s['packages'][key]

    def acquire(self,kind,intent,voices,session_id,*,preview=None):
        with self._locked() as s:
            for key,row in s['packages'].items():
                if preview:
                    if key!=self._key(preview['packageId'],preview['version']):continue
                elif not row['enabled']:continue
                m=self.manifest(row['packageId'],row['version'])
                if not preview and m['voices']!=voices:continue
                eligible=[x for x in m['scenes'] if x['kind']==kind]
                if preview:eligible=[x for x in m['scenes'] if x['id']==preview['sceneId']]
                else:
                    eligible=([x for x in eligible if 'general' in x['intents']] if kind=='light' else
                              [x for x in eligible if intent in x['intents']] or [x for x in eligible if 'general' in x['intents']])
                if not eligible:continue
                last=None if preview else s['last'].get(session_id+':'+kind)
                previous=next((i for i,x in enumerate(eligible) if x['id']==last),-1)
                scene=eligible[(previous+1)%len(eligible)]
                # Validate the whole selected scene before any audio is admitted.
                validate_manifest({**m,'scenes':[scene]},lambda p:(self.root/key/p).read_bytes())
                ref=str(uuid.uuid4());s['refs'][ref]=key
                if not preview:s['last'][session_id+':'+kind]=scene['id']
                return {'ref':ref,'key':key,'scene':scene,'voices':m['voices'],'packageVersion':m['version']}
            return None

    def release(self,ref):
        with self._locked() as s:s['refs'].pop(ref,None)

    def audio_chunks(self,selection):
        """Shared preview/runtime utterance boundary: complete speech then its own pause."""
        key=selection['key'];self._key(*key.rsplit('@',1))
        for u in selection['scene']['utterances']:
            raw=(self.root/key/u['audio']).read_bytes();_require(hashlib.sha256(raw).hexdigest()==u['sha256'],'audio changed')
            pcm,rate=read_audio(raw)
            yield pcm,rate,u
            yield b'\x00\x00'*(rate*u['pauseAfterMs']//1000),rate,{**u,'pause':True}
