/* R6 independent speech tests. Credentials stay in the local Python service. */
(function (root) {
  'use strict';
  const runtimes = new WeakMap();
  const LIMIT_SECONDS = 60;
  function runtime(vm) {
    if (!runtimes.has(vm)) runtimes.set(vm, {epoch: 0, chunks: [], requests: {}});
    return runtimes.get(vm);
  }
  function wavFromChunks(chunks, sourceRate) {
    const length = chunks.reduce((sum, part) => sum + part.length, 0);
    if (!length) throw new Error('没有录到声音，请检查麦克风后重新录音。');
    const source = new Float32Array(length);
    let offset = 0;
    chunks.forEach(part => { source.set(part, offset); offset += part.length; });
    const rate = 16000, count = Math.floor(length * rate / sourceRate);
    if (!count) throw new Error('录音太短，请重新录一句话。');
    const bytes = new ArrayBuffer(44 + count * 2), view = new DataView(bytes);
    const tag = (at, value) => Array.from(value).forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
    tag(0, 'RIFF'); view.setUint32(4, 36 + count * 2, true); tag(8, 'WAVE');
    tag(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
    view.setUint16(22, 1, true); view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true); tag(36, 'data'); view.setUint32(40, count * 2, true);
    for (let i = 0; i < count; i++) {
      const begin = Math.floor(i * sourceRate / rate);
      const end = Math.min(length, Math.max(begin + 1, Math.floor((i + 1) * sourceRate / rate)));
      let sample = 0;
      for (let p = begin; p < end; p++) sample += source[p];
      sample = Math.max(-1, Math.min(1, sample / (end - begin)));
      view.setInt16(44 + i * 2, Math.round(sample < 0 ? sample * 32768 : sample * 32767), true);
    }
    return new Blob([bytes], {type: 'audio/wav'});
  }
  function releaseMicrophone(vm) {
    const r = runtime(vm);
    clearInterval(r.recordTimer);
    if (r.processor) { r.processor.onaudioprocess = null; r.processor.disconnect(); }
    if (r.source) r.source.disconnect();
    if (r.mute) r.mute.disconnect();
    if (r.stream) r.stream.getTracks().forEach(track => track.stop());
    if (r.context) r.context.close().catch(() => {});
    r.processor = r.source = r.mute = r.stream = r.context = null;
  }
  const freshTest = () => ({busy: false, error: '', message: '', waiting: 0});
  function dialogueStatus(payload,id) {
    const row=payload&&payload.dialogue;
    if(!payload||payload.ok!==true||payload.device_id!==id||!row||!['idle','busy','error'].includes(row.state)||typeof row.turn_id!=='string'||typeof row.stopping!=='boolean')throw new Error('对话状态读取失败，请刷新后重试。');
    return row;
  }
  const mixin = {
    data() {
      return {speech: {
        status: null, statusError: '', statusBusy: false,
        deviceMic: {busy:false, recording:false, error:'', message:'', audioUrl:'', deviceId:'', captureId:'', stats:null},
        asr: Object.assign(freshTest(), {recording: false, acquiring: false, seconds: 0, text: '', audioUrl: ''}),
        llm: Object.assign(freshTest(), {text: '你好，请用一句话介绍一下自己。', reply: ''}),
        tts: Object.assign(freshTest(), {text: '你好，我是龙数惠宝，很高兴认识你。', audioUrl: ''}),
        robot: {busy:false,loading:false,error:'',statusError:'',message:'',deviceId:'',rtc:null,muted:null,desiredMuted:null,autoReply:null,dialogue:null,dialogueDeviceId:'',dialogueError:'',stopBusy:false}
      }};
    },
    methods: {
      async deviceMicRequest(deviceId, action, captureId) {
        const query='?device_id='+encodeURIComponent(deviceId)+'&capture_id='+encodeURIComponent(captureId||'');
        const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),8000);
        try {
          const response=await fetch('/proxy/deskbot/api/microphone_test'+(action?'':query), action ? {
            method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,
            body:JSON.stringify({device_id:deviceId,action,capture_id:captureId})
          } : {cache:'no-store',signal:controller.signal});
          const body=await response.json();
          if(!response.ok || !body.ok)throw new Error(body.error||'设备录音请求失败');
          return body;
        } finally {clearTimeout(timer);}
      },
      async startDeviceMicTest() {
        const s=this.speech.deviceMic,r=runtime(this),id=this.currentDeviceId;
        if(!id || s.busy || s.recording)return;
        const token={};r.micToken=token;
        if(s.audioUrl)URL.revokeObjectURL(s.audioUrl);
        Object.assign(s,{busy:true,recording:false,error:'',message:'',audioUrl:'',deviceId:id,captureId:'',stats:null});
        try {
          const body=await this.deviceMicRequest(id,'start');
          if(r.micToken!==token || id!==this.currentDeviceId){await this.deviceMicRequest(id,'stop',body.capture_id);return;}
          s.captureId=body.capture_id;s.stats=body;s.recording=true;s.busy=false;
          s.message='请对惠宝说话：先安静 2 秒，再说一句话；15 秒后自动停止。';
          r.micTimer=setTimeout(()=>void this.pollDeviceMicTest(token),350);
        } catch(e) {if(r.micToken===token){s.error=e.message||'设备录音启动失败';s.busy=false;}}
      },
      async pollDeviceMicTest(token) {
        const s=this.speech.deviceMic,r=runtime(this);
        if(r.micToken!==token)return;
        if(s.deviceId!==this.currentDeviceId){this.cancelDeviceMicTest();return;}
        try {
          const body=await this.deviceMicRequest(s.deviceId,null,s.captureId);
          if(r.micToken!==token)return;
          s.stats=body;
          if(body.recording)r.micTimer=setTimeout(()=>void this.pollDeviceMicTest(token),350);
          else await this.finishDeviceMicTest(body,token);
        } catch(e) {if(r.micToken===token){this.cancelDeviceMicTest();s.error=e.message||'无法读取设备录音';}}
      },
      async stopDeviceMicTest() {
        const s=this.speech.deviceMic,r=runtime(this);
        if(s.busy || !s.recording)return;
        clearTimeout(r.micTimer);s.busy=true;
        // Invalidate an in-flight poll, while retaining ownership of this capture.
        const stopToken={};r.micToken=stopToken;
        try {
          const body=await this.deviceMicRequest(s.deviceId,'stop',s.captureId);
          if(r.micToken===stopToken)await this.finishDeviceMicTest(body,stopToken);
        } catch(e) {if(r.micToken===stopToken){this.cancelDeviceMicTest();s.error=e.message||'停止录音失败';}}
      },
      async finishDeviceMicTest(body,token) {
        const s=this.speech.deviceMic,r=runtime(this);
        if(r.micToken!==token)return;
        s.stats=body;s.recording=false;s.busy=true;s.message='';
        const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
        try {
          if(!body.bytes)throw new Error('没有收到设备音频。请在下方检查收音开关，并确认 USB 连接；这不能直接判定麦克风损坏。');
          const response=await fetch('/proxy/deskbot/api/microphone_test/audio?device_id='+encodeURIComponent(s.deviceId)+'&capture_id='+encodeURIComponent(s.captureId),{cache:'no-store',signal:controller.signal});
          if(!response.ok)throw new Error('读取设备录音失败，请重试。');
          const blob=await response.blob();
          if(r.micToken!==token)return;
          this.speechSetAudio('deviceMic',blob);
          s.message='录音完成，请点击播放，确认人声是否清楚。';
          if(body.reason==='device_disconnected'||body.reason==='device_reconnected')s.error='录制中设备断开或重连；下方仅保留断开前的声音。';
          else if(body.reason==='audio_format_changed')s.error='录制中音频格式发生变化，请重新录制。';
          else if(body.usb_crc_errors>0)s.error='录制期间存在 USB 音频包校验错误，请试听是否断续。';
          else if(body.clipped_samples>0)s.error='检测到音频削波，请试听是否失真。';
          else if(body.peak_dbfs < -50)s.error='本次信号很弱，请靠近惠宝说话后再试。';
          else if(body.elapsed>3 && body.seconds<body.elapsed*.8)s.error='收到的音频时长明显不足，请试听是否断续。';
        } catch(e) {if(r.micToken===token)s.error=e.message||'设备录音回放失败';}
        finally {clearTimeout(timer);if(r.micToken===token)s.busy=false;}
      },
      cancelDeviceMicTest() {
        const s=this.speech.deviceMic,r=runtime(this);
        clearTimeout(r.micTimer);r.micToken=null;
        if(s.captureId && s.recording)void fetch('/proxy/deskbot/api/microphone_test',{
          method:'POST',headers:{'Content-Type':'application/json'},keepalive:true,
          body:JSON.stringify({device_id:s.deviceId,action:'stop',capture_id:s.captureId})
        }).catch(()=>{});
        s.recording=false;s.busy=false;
      },
      speechRobotReady() {
        const r=this.speech.robot;
        return !!(this.currentDeviceId && r.deviceId===this.currentDeviceId && r.rtc && r.rtc.active && r.muted===false && r.desiredMuted===false && r.autoReply===true && !r.statusError && r.dialogueDeviceId===this.currentDeviceId && r.dialogue && r.dialogue.state==='idle' && !r.dialogueError && !r.stopBusy);
      },
      speechRobotState() {
        const r=this.speech.robot;
        if(!this.currentDeviceId)return '请先连接并选择机器人';
        if(r.deviceId!==this.currentDeviceId)return '等待读取所选设备状态';
        if(r.statusError)return '设备状态读取失败';
        if(r.muted!==null && r.desiredMuted!==null && r.muted!==r.desiredMuted)return '等待设备确认麦克风状态';
        if(r.muted===true && r.desiredMuted===true)return '机器人收音已暂停';
        if(r.autoReply===false)return '自动回复未开启';
        if(!r.rtc || !r.rtc.active)return '等待语音房间与对话服务连接';
        if(r.muted!==false || r.desiredMuted!==false || r.autoReply!==true)return '收音或自动回复状态尚未确认';
        if(r.dialogueDeviceId!==this.currentDeviceId||!r.dialogue||r.dialogue.state!=='idle'||r.dialogueError||r.stopBusy)return this.speechRobotDialogueText();
        return '语音链路已就绪，直接对机器人说话';
      },
      speechRobotDialogueText() {
        const r=this.speech.robot;
        if(!this.currentDeviceId)return '请先选择机器人';
        if(r.dialogueError&&r.dialogueDeviceId===this.currentDeviceId)return r.dialogueError;
        if(r.dialogueDeviceId!==this.currentDeviceId||!r.dialogue)return '等待读取所选机器人的对话状态';
        if(r.stopBusy||r.dialogue.stopping&&r.dialogue.state!=='idle')return '正在停止回答，等待播放清空确认…';
        if(r.dialogue.state==='error')return '播放状态异常，请点击停止回答后继续';
        return r.dialogue.state==='busy'?'正在回答 · 暂不能说话':'可以说话';
      },
      async stopSpeechRobot() {
        const r=this.speech.robot,id=this.currentDeviceId;
        if(!id||r.stopBusy||this.browserAudio&&this.browserAudio.phase!=='idle')return;
        r.stopBusy=true;r.error='';r.message='';
        runtime(this).dialogueEpoch=(runtime(this).dialogueEpoch||0)+1;
        try {
          const result=await this.api('/proxy/deskbot/api/devices/'+encodeURIComponent(id)+'/dialogue/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',timeoutMs:45000,timeoutMessage:'停止回答尚未确认，请刷新设备状态后重试。'});
          if(id!==this.currentDeviceId)return;
          r.dialogue=dialogueStatus(result,id);r.dialogueDeviceId=id;r.dialogueError='';
        }catch(e){if(id===this.currentDeviceId){r.error=e.message||'停止回答未确认，请刷新设备状态。';r.dialogueDeviceId=id;r.dialogueError='停止回答尚未确认，请刷新设备状态后重试。';}}
        finally{r.stopBusy=false;}
      },
      async refreshSpeechRobot() {
        const r=this.speech.robot, id=this.currentDeviceId;
        const dialogueEpoch=runtime(this).dialogueEpoch||0,readDialogue=!r.stopBusy;
        if(r.loading)return;
        if(!id){r.deviceId='';r.rtc=null;r.muted=null;r.desiredMuted=null;r.autoReply=null;return;}
        r.loading=true;
        try {
          const results=await Promise.allSettled([
            this.api('/proxy/deskbot/api/rtc_health'),
            this.api('/proxy/deskbot/api/mic_mute?device_id='+encodeURIComponent(id)),
            this.api('/proxy/deskbot/api/asr_auto_reply'),
            readDialogue?this.api('/proxy/deskbot/api/devices/'+encodeURIComponent(id)+'/dialogue'):Promise.resolve(null)
          ]);
          if(id!==this.currentDeviceId)return;
          r.deviceId=id;r.statusError='';
          const rtc=results[0].status==='fulfilled'?results[0].value:null;
          const mic=results[1].status==='fulfilled'?results[1].value:null;
          const auto=results[2].status==='fulfilled'?results[2].value:null;
          r.rtc=rtc && rtc.rtc && rtc.rtc.devices ? rtc.rtc.devices[id]||null : null;
          r.muted=mic && mic.ok!==false && mic.supported && typeof mic.device_muted==='boolean' ? mic.device_muted : null;
          r.desiredMuted=mic && mic.ok!==false && mic.supported && typeof mic.muted==='boolean' ? mic.muted : null;
          r.autoReply=auto && auto.ok!==false && typeof auto.enabled==='boolean' ? auto.enabled : null;
          if(results.some(result=>result.status==='rejected'))r.statusError='无法读取完整的机器人状态，请确认 Core 服务和 USB 连接后刷新。';
          else if(!mic || !mic.supported)r.statusError='当前设备无法确认麦克风开关，请检查设备连接和固件版本。';
          if(readDialogue&&dialogueEpoch===(runtime(this).dialogueEpoch||0)){
            r.dialogueDeviceId=id;
            try{if(results[3].status==='rejected')throw results[3].reason;r.dialogue=dialogueStatus(results[3].value,id);r.dialogueError='';}
            catch(e){r.dialogue=null;r.dialogueError=e.message||'对话状态读取失败，请刷新后重试。';}
          }
        } catch(e) {r.statusError=e.message||'机器人状态读取失败';}
        finally {r.loading=false;}
      },
      async startSpeechRobot() {
        const r=this.speech.robot,id=this.currentDeviceId;
        if(r.busy || !id)return;
        r.busy=true;r.error='';r.message='';
        try {
          const auto=await this.api('/proxy/deskbot/api/asr_auto_reply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:true})});
          if(auto.ok===false || auto.enabled!==true)throw new Error(auto.error||'开启自动回复失败。');
          const mic=await this.api('/proxy/deskbot/api/mic_mute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_id:id,muted:false})});
          if(mic.ok===false || !mic.supported || mic.muted!==false || mic.device_muted!==false)throw new Error(mic.error||'设备未能开启收音，请检查 USB 连接和固件版本。');
          r.message='已启用收音和自动回复。等待下方状态显示「语音链路已就绪」后，直接说一句话，停顿并等待回复。';
          await this.refreshSpeechRobot();
        }catch(e){r.error=e.message||'开启机器人对话失败';}
        finally{r.busy=false;}
      },
      async pauseSpeechRobot() {
        const r=this.speech.robot,id=this.currentDeviceId;
        if(r.busy || !id)return;
        r.busy=true;r.error='';r.message='';
        try {
          const mic=await this.api('/proxy/deskbot/api/mic_mute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_id:id,muted:true})});
          if(mic.ok===false || !mic.supported || mic.muted!==true || mic.device_muted!==true)throw new Error(mic.error||'暂停收音失败，请检查设备连接。');
          r.muted=true;r.desiredMuted=true;r.deviceId=id;r.message='机器人收音已暂停；已经开始的回复可能仍会播放。';
          await this.refreshSpeechRobot();
        }catch(e){r.error=e.message||'暂停机器人收音失败';}
        finally{r.busy=false;}
      },
      speechStatusText(kind) {
        const state = this.speech.status && this.speech.status[kind];
        return !state ? '配置状态未知' : (state.configured ? '配置已就绪' : '待配置');
      },
      speechIsReady(kind) { return !!(this.speech.status && this.speech.status[kind] && this.speech.status[kind].configured); },
      speechModel(kind) {
        const value = this.speech.status && this.speech.status[kind];
        return value ? [value.model, kind === 'tts' ? value.voice : ''].filter(Boolean).join(' · ') : '';
      },
      async refreshSpeechStatus() {
        if (this.speech.statusBusy) return;
        this.speech.statusBusy = true; this.speech.statusError = '';
        const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10000);
        try {
          const response = await fetch('/api/longshu/tests/status', {cache: 'no-store', signal: controller.signal});
          const body = await response.json();
          if (!response.ok || !body.ok) throw new Error(body.error || '配置状态读取失败');
          this.speech.status = body;
        } catch (e) { this.speech.statusError = '读取配置状态失败，请确认 R6 服务运行正常，再点刷新。'; }
        finally { clearTimeout(timer); this.speech.statusBusy = false; }
      },
      speechTimeout(kind) {
        const state = this.speech.status && this.speech.status[kind];
        return Math.min(600, Math.max(1, Number(state && state.timeout_seconds) || 600)) * 1000 + 10000;
      },
      async speechRequest(kind, url, options) {
        const r = runtime(this), state = this.speech[kind];
        if (state.busy) return null;
        const task = {controller: new AbortController(), cancelled: false, timedOut: false};
        r.requests[kind] = task;
        state.busy = true; state.error = ''; state.message = ''; state.waiting = 0;
        const waiting = setInterval(() => state.waiting++, 1000);
        const timeout = setTimeout(() => { task.timedOut = true; task.controller.abort(); }, this.speechTimeout(kind));
        try {
          const response = await fetch(url, Object.assign({}, options, {signal: task.controller.signal}));
          let body;
          try { body = await response.json(); }
          catch (e) { throw new Error('服务返回了无效响应（HTTP ' + response.status + '），请查看服务日志。'); }
          if (!response.ok || !body.ok) throw new Error(body.error || '请求失败（HTTP ' + response.status + '）');
          return task.cancelled ? null : body;
        } catch (e) {
          if (task.cancelled) state.message = '已停止等待；网关可能仍在处理刚才的请求。';
          else state.error = task.timedOut ? '超过配置的等待时间，请检查网关或使用更短的输入后重试。' : (e.message || '连接失败，请检查电脑端服务。');
          return null;
        } finally {
          clearTimeout(timeout); clearInterval(waiting);
          if (r.requests[kind] === task) delete r.requests[kind];
          if(task.cancelled)state.message = '已停止等待；网关可能仍在处理刚才的请求。';
          state.busy = false;
        }
      },
      cancelSpeechTest(kind) {
        const task = runtime(this).requests[kind];
        if (task) { task.cancelled = true; task.controller.abort(); }
      },
      speechSetAudio(kind, blob) {
        const state = this.speech[kind];
        if (state.audioUrl) URL.revokeObjectURL(state.audioUrl);
        state.audioUrl = URL.createObjectURL(blob);
      },
      async startSpeechRecording() {
        const state = this.speech.asr, r = runtime(this);
        if (state.busy || state.recording || state.acquiring) return;
        state.error = ''; state.message = ''; state.text = ''; state.seconds = 0;
        if (state.audioUrl) { URL.revokeObjectURL(state.audioUrl); state.audioUrl = ''; }
        if (!root.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          state.error = '浏览器无法使用麦克风。请在本机用 Chrome 或 Edge 打开 http://127.0.0.1:5050/，并允许麦克风权限。'; return;
        }
        const Context = root.AudioContext || root.webkitAudioContext;
        if (!Context) { state.error = '当前浏览器不支持录音，请使用 Chrome 或 Edge。'; return; }
        state.acquiring = true;
        const epoch = ++r.epoch;
        try {
          // Create/resume on the user's click so the browser permits recording.
          const context = new Context(); r.context = context;
          await context.resume();
          if (epoch !== r.epoch) return;
          const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}, video: false});
          if (epoch !== r.epoch) { stream.getTracks().forEach(track => track.stop()); return; }
          r.stream = stream; r.chunks = []; r.rate = context.sampleRate; r.recordedSamples = 0; r.limitReached = false;
          r.source = context.createMediaStreamSource(stream);
          r.processor = context.createScriptProcessor(4096, 1, 1);
          r.mute = context.createGain(); r.mute.gain.value = 0;
          r.processor.onaudioprocess = event => {
            if (!state.recording || r.limitReached) return;
            const data = event.inputBuffer.getChannelData(0);
            const remaining = Math.max(0, Math.floor(r.rate * LIMIT_SECONDS) - r.recordedSamples);
            const part = new Float32Array(data.subarray(0, remaining));
            r.chunks.push(part); r.recordedSamples += part.length;
            if(r.recordedSamples >= r.rate * LIMIT_SECONDS){
              r.limitReached = true; state.seconds = LIMIT_SECONDS;
              void this.stopSpeechRecording();
            }
          };
          r.source.connect(r.processor); r.processor.connect(r.mute); r.mute.connect(context.destination);
          state.acquiring = false; state.recording = true;
          const started = Date.now();
          r.recordTimer = setInterval(() => {
            state.seconds = Math.min(LIMIT_SECONDS, Math.floor((Date.now() - started) / 1000));
            if (state.seconds >= LIMIT_SECONDS) void this.stopSpeechRecording();
          }, 250);
        } catch (e) {
          if (epoch !== r.epoch) return;
          releaseMicrophone(this); state.acquiring = false; state.recording = false;
          const errors = {NotAllowedError: '麦克风权限被拒绝。请在地址栏的网站设置中允许麦克风，然后重试。', NotFoundError: '没有找到电脑麦克风，请接入麦克风后重试。', NotReadableError: '麦克风无法打开，请检查 Windows 麦克风权限，或关闭占用麦克风的软件。'};
          state.error = errors[e.name] || '录音启动失败，请检查麦克风并重试。';
        }
      },
      cancelSpeechRecording() {
        const r = runtime(this); r.epoch++;
        releaseMicrophone(this); r.chunks = [];
        this.speech.asr.recording = false; this.speech.asr.acquiring = false;
        this.speech.asr.message = '录音已取消，没有上传。';
      },
      async stopSpeechRecording() {
        const state = this.speech.asr, r = runtime(this);
        if (!state.recording) return;
        state.recording = false; r.epoch++;
        const chunks = r.chunks, rate = r.rate; r.chunks = [];
        releaseMicrophone(this);
        try {
          const blob = wavFromChunks(chunks, rate);
          this.speechSetAudio('asr', blob);
          await this.recognizeSpeechBlob(blob);
        } catch (e) { state.error = e.message || '录音处理失败，请重新录音。'; }
      },
      async uploadSpeechWav(event) {
        const file = event.target.files && event.target.files[0]; event.target.value = '';
        if (!file || this.speech.asr.busy || this.speech.asr.recording || this.speech.asr.acquiring) return;
        this.speech.asr.error = ''; this.speech.asr.message = ''; this.speech.asr.text = '';
        if (file.size > 10 * 1024 * 1024) { this.speech.asr.error = 'WAV 文件不能超过 10 MiB。'; return; }
        if (!/\.wav$/i.test(file.name)) { this.speech.asr.error = '请选择 16 位 PCM WAV 文件。'; return; }
        this.speechSetAudio('asr', file);
        await this.recognizeSpeechBlob(file);
      },
      async recognizeSpeechBlob(blob) {
        const form = new FormData(); form.append('audio', blob, 'speech-test.wav');
        const result = await this.speechRequest('asr', '/api/setup/asr/test', {method: 'POST', body: form});
        if (result) {
          this.speech.asr.text = String(result.text || '');
          if (!this.speech.asr.text.trim()) this.speech.asr.error = '接口未返回识别文字，请试听录音后重试。';
          else this.speech.asr.message = '识别完成。可修改同音字后，填入下一步提问。';
        }
      },
      copySpeechToLlm() {
        this.speech.llm.text = this.speech.asr.text;
        this.speech.llm.message = '已填入识别文字，点击「发送问题」开始对话。';
        this.$nextTick(() => this.$refs.speechLlmInput && this.$refs.speechLlmInput.focus());
      },
      async runSpeechLlm() {
        const state = this.speech.llm, text = state.text.trim();
        if (state.busy) return;
        state.error = ''; state.message = '';
        if (!text) { state.error = '请先输入问题。'; return; }
        if (Array.from(text).length > 4000) { state.error = '测试问题不能超过 4000 字。'; return; }
        state.reply = '';
        const result = await this.speechRequest('llm', '/api/longshu/llm/test', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text})});
        if (result) {
          state.reply = String(result.reply || '');
          if (!state.reply.trim()) state.error = '大模型未返回文字，请检查模型配置。';
          else state.message = '回复完成' + (result.elapsed_ms != null ? ' · ' + result.elapsed_ms + ' ms' : '') + '。';
        }
      },
      copySpeechToTts() {
        const state = this.speech.tts;
        state.text = this.speech.llm.reply; state.error = '';
        state.message = Array.from(state.text).length > 300 ? '已填入完整回复。试听最多 300 字，请先删减，再点击合成。' : '已填入回复，点击「合成语音」后播放。';
        this.$nextTick(() => this.$refs.speechTtsInput && this.$refs.speechTtsInput.focus());
      },
      async runSpeechTts() {
        const state = this.speech.tts, text = state.text.trim();
        if (state.busy) return;
        state.error = ''; state.message = '';
        if (!text) { state.error = '请先输入要朗读的文字。'; return; }
        if (Array.from(text).length > 300) { state.error = '试听最多 300 字，请先删减文字。'; return; }
        if (state.audioUrl) { URL.revokeObjectURL(state.audioUrl); state.audioUrl = ''; }
        const result = await this.speechRequest('tts', '/app/api/tts/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text})});
        if (result) {
          if (!result.wav_base64) { state.error = 'TTS 未返回可播放音频，请查看服务日志。'; return; }
          try {
            const bytes = Uint8Array.from(atob(result.wav_base64), c => c.charCodeAt(0));
            this.speechSetAudio('tts', new Blob([bytes], {type: 'audio/wav'}));
            state.message = '合成完成' + (result.elapsed_ms != null ? ' · ' + result.elapsed_ms + ' ms' : '') + '，点击播放器试听。';
          } catch (e) { state.error = '音频响应无法解析，请查看服务日志。'; }
        }
      },
      leaveSpeechTests() {
        this.cancelDeviceMicTest();
        if (this.speech.asr.recording || this.speech.asr.acquiring) this.cancelSpeechRecording();
        ['asr', 'llm', 'tts'].forEach(kind => this.cancelSpeechTest(kind));
      },
      disposeSpeechTests() {
        clearInterval(runtime(this).robotTimer);
        this.leaveSpeechTests();
        ['asr', 'tts', 'deviceMic'].forEach(kind => { if (this.speech[kind].audioUrl) URL.revokeObjectURL(this.speech[kind].audioUrl); this.speech[kind].audioUrl = ''; });
      }
    },
    watch: {
      currentDeviceId() {
        this.cancelDeviceMicTest();
        const s=this.speech.deviceMic;
        if(s.audioUrl)URL.revokeObjectURL(s.audioUrl);
        Object.assign(s,{audioUrl:'',stats:null,error:'',message:'',captureId:'',deviceId:''});
      }
    },
    mounted() {
      this._speechUnload = () => this.disposeSpeechTests();
      root.addEventListener('pagehide', this._speechUnload);
      runtime(this).robotTimer=setInterval(()=>{if(this.active==='debug')void this.refreshSpeechRobot();},5000);
      void this.refreshSpeechStatus();
    },
    beforeUnmount() {
      this.disposeSpeechTests(); root.removeEventListener('pagehide', this._speechUnload);
    }
  };
  root.LongshuSpeechTests = {mixin, wavFromChunks};
  if (typeof module !== 'undefined' && module.exports) module.exports = root.LongshuSpeechTests;
})(typeof window !== 'undefined' ? window : globalThis);
