/* Shared speech configuration for model settings and the voice picker. No secrets embedded. */
window.LongshuTtsUI = {
  data(){return {tts:{endpoint:'https://modelhub.lgdg.cc/aigateway/v1/audio/speech',
    model:'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice',speaker:'aiden',
    voices:['aiden','dylan','eric','ono_anna','ryan','serena','sohee','uncle_fu','vivian'],voice_catalog:{},voice_presets:[],
    api_key:'',api_key_set:false,api_key_masked:'',timeout_seconds:600,configured:false,
    saving:false,testing:false,result:null,audioUrl:'',testText:'你好，我是龙数惠宝，很高兴认识你。'}};},
  watch:{'tts.model'(){this.syncTtsVoices();}},
  methods:{
    selectTtsPreset(preset){
      Object.assign(this.tts,{endpoint:preset.endpoint,model:preset.model,speaker:preset.speaker});
      this.syncTtsVoices();
    },
    ttsVoiceLabel(voice){
      const preset=this.tts.voice_presets.find(p=>p.model===this.tts.model&&p.speaker===voice);
      return preset?preset.label:voice;
    },
    syncTtsVoices(){
      const voices=this.tts.voice_catalog[this.tts.model]||this.tts.voice_catalog.default;
      if(!voices)return;
      this.tts.voices=voices;
      if(!voices.includes(this.tts.speaker))this.tts.speaker=voices[0];
    },
    async gatewayRequest(url, body, timeoutMs=15000){
      const controller=new AbortController();
      const timer=setTimeout(()=>controller.abort(),timeoutMs);
      try{
        const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
        options.signal=controller.signal;
        const response=await fetch(url,options);
        let payload;
        try{payload=await response.json();}
        catch(_){throw new Error('电脑端服务未返回有效 JSON（HTTP '+response.status+'），请查看运行窗口。');}
        if(!response.ok||!payload.ok)throw new Error(payload.error||'请求失败（HTTP '+response.status+'）');
        return payload;
      }catch(e){
        if(e.name==='AbortError')throw new Error('TTS 请求超时，请检查网关状态；可先用较短文本测试。');
        throw e;
      }finally{clearTimeout(timer);}
    },
    applyTtsConfig(config){Object.assign(this.tts,config,{api_key:''});this.syncTtsVoices();},
    async loadTtsConfig(){
      try{this.applyTtsConfig((await this.gatewayRequest('/api/gateway/tts')).config);}
      catch(e){this.tts.result={ok:false,error:e.message};}
    },
    async saveTtsConfig(){
      this.tts.saving=true;this.tts.result=null;
      try{
        const payload={endpoint:this.tts.endpoint,model:this.tts.model,speaker:this.tts.speaker,
          api_key:this.tts.api_key,timeout_seconds:this.tts.timeout_seconds};
        this.applyTtsConfig((await this.gatewayRequest('/api/gateway/tts',payload)).config);
        this.tts.result={ok:true,message:'已保存；文字与实时语音共用此音色。'};return true;
      }catch(e){this.tts.result={ok:false,error:e.message};return false;}
      finally{this.tts.saving=false;}
    },
    async testGatewayTts(){
      // Audition the active saved configuration; a test never edits settings.
      this.tts.testing=true;this.tts.result=null;
      try{
        const result=await this.gatewayRequest('/api/gateway/tts/test',{text:this.tts.testText},(Number(this.tts.timeout_seconds)||600)*1000+10000);
        if(this.tts.audioUrl)URL.revokeObjectURL(this.tts.audioUrl);
        const bytes=Uint8Array.from(atob(result.wav_base64),c=>c.charCodeAt(0));
        this.tts.audioUrl=URL.createObjectURL(new Blob([bytes],{type:'audio/wav'}));
        this.tts.result={ok:true,message:'合成成功 · '+result.elapsed_ms+' ms，点击下方播放器试听。'};
      }catch(e){this.tts.result={ok:false,error:e.message};}
      finally{this.tts.testing=false;}
    }
  }
};
