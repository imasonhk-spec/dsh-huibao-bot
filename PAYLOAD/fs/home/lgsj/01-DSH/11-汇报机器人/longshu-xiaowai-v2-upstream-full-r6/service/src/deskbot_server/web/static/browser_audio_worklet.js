/* Bounded 16k PCM queue paced by the browser's audio rendering clock. */
class BrowserAudioProcessor extends AudioWorkletProcessor {
  constructor(){
    super();this.queue=new Float32Array(32000);this.read=0;this.write=0;this.size=0;this.playPhase=0;
    this.capturePhase=0;this.sum=0;this.count=0;this.frame=new ArrayBuffer(640);this.frameAt=0;
    this.captureEnabled=true;this.captureEpoch=0;this.written=0;this.consumed=0;this.ends=[];
    this.port.onmessage=({data})=>{
      if(data.type==='capture_enabled'){
        this.captureEnabled=data.enabled===true;this.captureEpoch=data.capture_epoch;
        this.capturePhase=this.sum=this.count=this.frameAt=0;this.frame=new ArrayBuffer(640);return;
      }
      if(data.type==='clear'){
        this.clearPlayback();
        if(this.hasIdentity(data))this.port.postMessage({type:'playback_cleared',turn_id:data.turn_id,sequence:data.sequence});
        return;
      }
      if(data.type==='playback_end'){
        if(this.hasIdentity(data))this.ends.push({at:this.written,turn_id:data.turn_id,sequence:data.sequence});
        return;
      }
      if(data.type!=='play')return;
      const view=new DataView(data.pcm),length=view.byteLength/2;
      if(this.size+length>this.queue.length){this.clearPlayback();this.port.postMessage({type:'overflow'});return;}
      for(let i=0;i<length;i++){this.queue[this.write]=view.getInt16(i*2,true)/32768;this.write=(this.write+1)%this.queue.length;}
      this.size+=length;this.written+=length;
    };
  }
  hasIdentity(data){return typeof data.turn_id==='string'&&data.turn_id.length>0&&Number.isSafeInteger(data.sequence)&&data.sequence>=0;}
  clearPlayback(){this.size=0;this.read=this.write=0;this.playPhase=0;this.written=this.consumed=0;this.ends=[];}
  process(inputs,outputs){
    const input=inputs[0]&&inputs[0][0];
    if(input&&this.captureEnabled)for(let i=0;i<input.length;i++){
      this.sum+=input[i];this.count++;this.capturePhase+=16000;
      if(this.capturePhase>=sampleRate){
        const value=Math.max(-1,Math.min(1,this.sum/this.count));
        while(this.capturePhase>=sampleRate){
          this.capturePhase-=sampleRate;
          new DataView(this.frame).setInt16(this.frameAt*2,Math.round(value<0?value*32768:value*32767),true);
          this.frameAt++;
          if(this.frameAt===320){this.port.postMessage({type:'capture',capture_epoch:this.captureEpoch,pcm:this.frame},[this.frame]);this.frame=new ArrayBuffer(640);this.frameAt=0;}
        }
        this.sum=0;this.count=0;
      }
    }
    const channels=outputs[0]||[];
    for(let i=0;i<(channels[0]?channels[0].length:0);i++){
      let value=0;
      if(this.size){
        const next=this.size>1?this.queue[(this.read+1)%this.queue.length]:this.queue[this.read];
        value=this.queue[this.read]*(1-this.playPhase)+next*this.playPhase;
        this.playPhase+=16000/sampleRate;
        while(this.playPhase>=1&&this.size){this.playPhase-=1;this.read=(this.read+1)%this.queue.length;this.size--;this.consumed++;}
        if(!this.size)this.playPhase=0;
      }
      for(const channel of channels)channel[i]=value;
    }
    // A marker refers to its PCM boundary, never a guessed playback duration.
    if(channels[0]&&channels[0].length)while(this.ends.length&&this.ends[0].at<=this.consumed){
      const end=this.ends.shift();this.port.postMessage({type:'playback_drained',turn_id:end.turn_id,sequence:end.sequence});
    }
    return true;
  }
}
registerProcessor('browser-audio',BrowserAudioProcessor);
