/* Temporary browser audio ownership. Device preferences remain on the server. */
(function(root){
  'use strict';
  const scriptURL=root.document.currentScript.src;
  const base=new URL(scriptURL).pathname.split('/static/')[0];
  const workletURL=new URL('browser_audio_worklet.js',scriptURL).href;
  class Client {
    constructor(state){this.state=state;this.epoch=0;this.captureEpoch=0;this.captureEnabled=false;this.pendingEnd=null;this.pendingClear=null;Object.assign(state,{phase:'idle',deviceId:'',error:'',recording:null,turnId:null,turnState:'idle',turnError:''});}
    async start(deviceId){
      if(this.state.phase!=='idle')return;
      if(!deviceId)throw Error('请先选择已连接的机器人。');
      const epoch=++this.epoch;
      Object.assign(this.state,{phase:'acquiring',error:'',deviceId,recording:null});
      try {
        if(!root.navigator.mediaDevices||!root.AudioWorkletNode)throw Error('请使用安全连接下的 Chrome 或 Edge，并允许麦克风。');
        // Both permission and resume originate in the explicit click gesture.
        this.context=new (root.AudioContext||root.webkitAudioContext)();
        const resumed=this.context.resume().then(()=>null,error=>error);
        const pending=root.navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,channelCount:1}});
        const stream=await pending;
        if(epoch!==this.epoch){stream.getTracks().forEach(t=>t.stop());return;}
        this.stream=stream;
        stream.getTracks().forEach(t=>{t.onended=()=>this.disconnect('麦克风已断开，网页收音中断；请重新接管，会议不会自动续录。');});
        const resumeError=await resumed;
        if(resumeError)throw resumeError;
        if(epoch!==this.epoch)return;
        await this.context.audioWorklet.addModule(workletURL);
        if(epoch!==this.epoch)return;
        this.node=new root.AudioWorkletNode(this.context,'browser-audio');
        this.source=this.context.createMediaStreamSource(stream);
        this.source.connect(this.node);this.node.connect(this.context.destination);
        this.node.port.onmessage=({data})=>{
          if(epoch!==this.epoch)return;
          if(data.type==='overflow'){this.disconnect('播放积压，网页音频已中断；请重新接管。');return;}
          if(['playback_drained','playback_cleared'].includes(data.type)){
            const key=data.type==='playback_drained'?'pendingEnd':'pendingClear',pending=this[key];
            if(pending&&pending.turn_id===data.turn_id&&pending.sequence===data.sequence&&this.ws&&this.ws.readyState===1){
              this[key]=null;
              try{this.ws.send(JSON.stringify({type:data.type,turn_id:data.turn_id,sequence:data.sequence}));}catch(_){this.disconnect('网页音频连接已中断；请重新接管。');}
            }
            return;
          }
          if(data.type==='capture'&&this.captureEnabled&&data.capture_epoch===this.captureEpoch&&this.ws&&this.ws.readyState===1&&['active','releasing'].includes(this.state.phase)){
            if(this.ws.bufferedAmount>64000){this.disconnect('网络积压，网页收音已中断；请重新接管。');return;}
            try{this.ws.send(data.pcm);}catch(_){this.disconnect('网页音频连接已中断；请重新接管。');}
          }
        };
        this.context.onstatechange=()=>{if(this.context&&this.context.state==='suspended'&&['active','releasing'].includes(this.state.phase))this.disconnect('浏览器暂停了收音，会议已中断；请重新接管。');};
        this.abort=new AbortController();
        const requestTimer=setTimeout(()=>this.abort&&this.abort.abort(),15000);
        let response;
        try{response=await root.fetch(base+'/proxy/deskbot/api/browser_audio_ticket',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_id:deviceId}),signal:this.abort.signal});}finally{clearTimeout(requestTimer);}
        if(epoch!==this.epoch)return;
        const ticket=await response.json();
        if(epoch!==this.epoch)return;
        if(!response.ok||!ticket.ticket||ticket.device_id!==deviceId)throw Error(ticket.message||'机器人暂时无法接管，请检查 USB 连接。');
        this.state.phase='connecting';
        await new Promise((resolve,reject)=>{
          this.connectWait={resolve,reject};
          const url=new URL(base+'/api/browser-audio/ws',root.location.href);url.protocol=url.protocol==='https:'?'wss:':'ws:';
          const ws=this.ws=new root.WebSocket(url.href);ws.binaryType='arraybuffer';
          this.connectTimer=setTimeout(()=>this.disconnect('连接超时，请重新接管。'),60000);
          ws.onopen=()=>{if(epoch===this.epoch)ws.send(JSON.stringify({ticket:ticket.ticket}));};
          ws.onmessage=e=>{if(epoch===this.epoch)this.message(e.data);};
          ws.onerror=()=>{if(epoch===this.epoch)this.disconnect('网页音频连接失败，请检查网络后重新接管。');};
          ws.onclose=()=>{if(epoch===this.epoch)this.disconnect('网页连接已中断；会议保留已录内容，重新接管后需新开一场。');};
        });
      }catch(error){if(epoch===this.epoch)this.disconnect('无法使用电脑麦克风：'+error.message);throw error;}
    }
    message(data){
      if(data instanceof ArrayBuffer){
        this.lastPong=Date.now();
        if(data.byteLength%2||data.byteLength>64000){this.disconnect('收到异常音频，连接已中断。');return;}
        this.node.port.postMessage({type:'play',pcm:data},[data]);return;
      }
      let msg;try{msg=JSON.parse(data);}catch(_){this.disconnect('连接响应异常，网页音频已中断。');return;}
      if(['ready','pong','clear','status','released','error','turn_state','playback_end'].includes(msg.type))this.lastPong=Date.now();
      if(msg.type==='ready'&&this.state.phase==='connecting'){
        if(msg.sample_rate!==16000){this.disconnect('音频格式不受支持。');return;}
        clearTimeout(this.connectTimer);this.state.phase='active';this.connectWait.resolve();this.connectWait=null;
        this.syncCapture();
        this.lastPong=Date.now();this.pingTimer=setInterval(()=>{
          if(Date.now()-this.lastPong>20000){this.disconnect('网页收音连接已中断；请重新接管。');return;}
          if(this.ws&&this.ws.readyState===1)try{this.ws.send(JSON.stringify({type:'ping'}));}catch(_){this.disconnect('网页音频连接已中断；请重新接管。');}
        },5000);
      }else if(msg.type==='pong')this.lastPong=Date.now();
      else if(msg.type==='turn_state'){
        if(!['busy','idle','error'].includes(msg.state)||typeof msg.turn_id!=='string'||!msg.turn_id)return;
        if(msg.state!=='busy'&&this.state.turnId&&msg.turn_id!==this.state.turnId)return;
        if(msg.turn_id!==this.state.turnId){this.pendingEnd=this.pendingClear=null;}
        Object.assign(this.state,{turnId:msg.turn_id,turnState:msg.state,turnError:msg.state==='error'?(msg.message||'播放结束尚未确认，请点击停止后重试。'):''});
        this.syncCapture();
      }
      else if(msg.type==='playback_end'){
        if(this.hasIdentity(msg)&&msg.turn_id===this.state.turnId){this.pendingEnd={turn_id:msg.turn_id,sequence:msg.sequence};this.node.port.postMessage({type:'playback_end',...this.pendingEnd});}
      }
      else if(msg.type==='clear'){
        if(this.hasIdentity(msg)&&msg.turn_id!==this.state.turnId)return;
        this.pendingEnd=null;this.pendingClear=null;
        if(this.hasIdentity(msg)&&msg.turn_id===this.state.turnId)this.pendingClear={turn_id:msg.turn_id,sequence:msg.sequence};
        this.node.port.postMessage({type:'clear',...(this.pendingClear||{})});
      }
      else if(msg.type==='status'){this.state.recording=msg.recording;this.syncCapture();}
      else if(msg.type==='released'&&this.releaseWait){const wait=this.releaseWait;this.releaseWait=null;clearTimeout(this.releaseTimer);this.disconnect();wait.resolve();}
      else if(msg.type==='error'){
        this.state.error=msg.message||'操作失败，请重试。';
        if(this.releaseWait){const wait=this.releaseWait;this.releaseWait=null;clearTimeout(this.releaseTimer);this.state.phase='active';wait.reject(Error(this.state.error));}
        else if(this.state.phase==='connecting')this.disconnect(this.state.error);
      }
    }
    hasIdentity(msg){return typeof msg.turn_id==='string'&&msg.turn_id.length>0&&Number.isSafeInteger(msg.sequence)&&msg.sequence>=0;}
    syncCapture(){
      const row=this.state.recording,recordingState=row&&(row.recordingState||row.status);
      const enabled=['active','releasing'].includes(this.state.phase)&&(this.state.turnState==='idle'||['preparing','recording'].includes(recordingState));
      if(this.captureEnabled===enabled)return;
      this.captureEnabled=enabled;this.captureEpoch++;
      if(this.stream)this.stream.getTracks().forEach(t=>{t.enabled=enabled;});
      if(this.node)this.node.port.postMessage({type:'capture_enabled',enabled,capture_epoch:this.captureEpoch});
    }
    stop(){
      if(!this.ws||this.ws.readyState!==1||!['active','releasing'].includes(this.state.phase))return;
      try{this.ws.send(JSON.stringify({type:'stop'}));}catch(_){this.disconnect('网页音频连接已中断；请重新接管。');}
    }
    release(){
      if(this.releaseWait)return this.releasePromise;
      if(this.state.phase==='acquiring'||this.state.phase==='connecting'){this.disconnect();return Promise.resolve();}
      if(this.state.phase==='idle')return Promise.resolve();
      this.state.phase='releasing';this.state.error='';
      this.releasePromise=new Promise((resolve,reject)=>{
        this.releaseWait={resolve,reject};
        // Meeting persistence can take longer than an ordinary HTTP request.
        this.releaseTimer=setTimeout(()=>{this.releaseWait=null;this.state.phase='active';this.state.error='保存仍未确认，继续保留网页收音，请检查会议状态后重试。';reject(Error(this.state.error));},120000);
        try{this.ws.send(JSON.stringify({type:'release'}));}catch(_){this.disconnect('网页连接已中断，会议保存结果尚未确认，请检查会议列表。');}
      });return this.releasePromise;
    }
    disconnect(error){
      ++this.epoch;clearTimeout(this.connectTimer);clearTimeout(this.releaseTimer);clearInterval(this.pingTimer);
      if(this.abort)this.abort.abort();this.abort=null;
      if(this.connectWait){this.connectWait.reject(Error(error||'已取消接管'));this.connectWait=null;}
      if(this.releaseWait){this.releaseWait.reject(Error(error||'连接已中断，保存结果尚未确认'));this.releaseWait=null;}
      const ws=this.ws;this.ws=null;if(ws){ws.onclose=ws.onerror=ws.onmessage=ws.onopen=null;ws.close();}
      if(this.stream)this.stream.getTracks().forEach(t=>{t.onended=null;t.stop();});this.stream=null;
      if(this.source)this.source.disconnect();this.source=null;
      if(this.node){this.node.port.onmessage=null;this.node.disconnect();}this.node=null;
      const context=this.context;this.context=null;if(context){context.onstatechange=null;context.close().catch(()=>{});}
      this.captureEnabled=false;this.captureEpoch++;this.pendingEnd=this.pendingClear=null;
      Object.assign(this.state,{phase:'idle',deviceId:'',recording:null,error:error||'',turnId:null,turnState:'idle',turnError:''});
    }
  }
  const clients=new WeakMap();
  function client(vm){if(!clients.has(vm))clients.set(vm,new Client(vm.browserAudio));return clients.get(vm);}
  const mixin={
    data(){return {browserAudio:{phase:'idle',deviceId:'',error:'',recording:null,turnId:null,turnState:'idle',turnError:''}};},
    methods:{
      browserTurnText(){return this.browserAudio.turnState==='busy'?'正在回答 · 暂不能说话':this.browserAudio.turnState==='error'?'播放状态异常，请点击停止后继续':'可以说话';},
      stopBrowserAudio(){client(this).stop();},
      browserRecordingText(){
        const row=this.browserAudio.recording;
        return ({recording:'正在录音',interrupted:'录音已中断',preparing:'正在开始录音',stopped:'已停止录音',idle:'尚未开始录音'})[row&&row.recordingState]||'正在读取录音状态';
      },
      async startBrowserAudio(){try{await client(this).start(this.currentDeviceId);}catch(_){}},
      async releaseBrowserAudio(){try{await client(this).release();}catch(_){}},
      async guardBrowserDeviceChange(next,old){
        const c=client(this);if(c.state.phase==='idle'||next===c.state.deviceId)return;
        // Restore selection synchronously before any page action can target a different device.
        this.currentDeviceId=old;
        try{await c.release();if(this.currentDeviceId===old){this.currentDeviceId=next;await this.switchDevice();}}catch(_){}
      }
    },
    watch:{currentDeviceId(next,old){void this.guardBrowserDeviceChange(next,old);}},
    mounted(){this._browserAudioLeave=()=>client(this).disconnect();root.addEventListener('pagehide',this._browserAudioLeave);},
    beforeUnmount(){client(this).disconnect();root.removeEventListener('pagehide',this._browserAudioLeave);}
  };
  root.BrowserAudio={Client,mixin};
})(typeof window!=='undefined'?window:globalThis);
