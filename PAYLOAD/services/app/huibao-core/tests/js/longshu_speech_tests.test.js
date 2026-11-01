'use strict';
const assert = require('node:assert/strict');
const {mixin, wavFromChunks} = require('../../src/deskbot_server/web/static/longshu_speech_tests.js');
function vm() {
  const instance=Object.assign(mixin.data(),{currentDeviceId:'',active:'debug',$refs:{},$nextTick:fn=>Promise.resolve().then(fn)});
  for(const [name, method] of Object.entries(mixin.methods))instance[name]=method.bind(instance);
  return instance;
}
const response=body=>({ok:true,status:200,json:async()=>body});
async function main() {
  // Header and actual downsampled data are accepted PCM WAV, not browser WebM.
  const wav=wavFromChunks([new Float32Array([1,1,1,-1,-1,-1,0,0,0])],48000);
  const buffer=await wav.arrayBuffer(), data=new DataView(buffer);
  assert.equal(new TextDecoder().decode(buffer.slice(0,4)),'RIFF');
  assert.equal(data.getUint16(20,true),1); assert.equal(data.getUint16(22,true),1);
  assert.equal(data.getUint32(24,true),16000); assert.equal(data.getUint16(34,true),16);
  assert.equal(data.getUint32(40,true),6);
  assert.deepEqual([44,46,48].map(p=>data.getInt16(p,true)),[32767,-32768,0]);
  assert.throws(()=>wavFromChunks([],48000),/没有录到/);
  const app=vm(), calls=[];
  global.fetch=async(url,options)=>{
    calls.push({url,options});
    if(url==='/api/setup/asr/test')return response({ok:true,text:'你好，龙数小歪'});
    if(url==='/api/longshu/llm/test')return response({ok:true,reply:'你好，我是小歪。',elapsed_ms:12});
    if(url==='/app/api/tts/preview')return response({ok:true,wav_base64:Buffer.from(await wav.arrayBuffer()).toString('base64'),elapsed_ms:34});
    throw new Error('Unexpected URL '+url);
  };
  await app.recognizeSpeechBlob(wav);
  assert.equal(app.speech.asr.text,'你好，龙数小歪');
  assert.ok(calls[0].options.body.get('audio') instanceof Blob);
  assert.equal(calls[0].options.headers,undefined,'Multipart boundary is set by browser');
  app.copySpeechToLlm(); await app.runSpeechLlm();
  assert.deepEqual(JSON.parse(calls[1].options.body),{text:'你好，龙数小歪'});
  app.copySpeechToTts(); await app.runSpeechTts();
  assert.equal(app.speech.tts.text,'你好，我是小歪。'); assert.match(app.speech.tts.audioUrl,/^blob:/);
  assert.deepEqual(calls.map(c=>c.url),['/api/setup/asr/test','/api/longshu/llm/test','/app/api/tts/preview']);
  assert.equal(calls.some(c=>/device_id|api_key/.test(String(c.options.body))),false);
  app.speech.tts.text='字'.repeat(301); await app.runSpeechTts();
  assert.match(app.speech.tts.error,/300/); assert.equal(calls.length,3);
  app.speech.llm.text=''; await app.runSpeechLlm();assert.match(app.speech.llm.error,/输入问题/);
  // Request cancellation returns promptly and does not apply a stale result.
  global.fetch=(url,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError'))));
  app.speech.llm.text='test'; const pending=app.runSpeechLlm();
  assert.equal(app.speech.llm.busy,true); app.cancelSpeechTest('llm'); await pending;
  assert.equal(app.speech.llm.busy,false); assert.match(app.speech.llm.message,/停止等待/); assert.equal(app.speech.llm.reply,'');
  // Cancelling while the permission prompt is pending must stop a late stream.
  global.isSecureContext=true;
  let finishPermission, stopped=0, closed=0;
  Object.defineProperty(global,'navigator',{configurable:true,value:{mediaDevices:{getUserMedia:()=>new Promise(resolve=>{finishPermission=resolve;})}}});
  global.AudioContext=class {async resume(){} async close(){closed++;}};
  const recording=app.startSpeechRecording(); await Promise.resolve(); await Promise.resolve();
  assert.equal(app.speech.asr.acquiring,true); app.cancelSpeechRecording();
  finishPermission({getTracks:()=>[{stop:()=>stopped++}]}); await recording;
  assert.equal(stopped,1); assert.equal(closed,1); assert.equal(app.speech.asr.recording,false);
  // Permission refusal explains the error and closes the AudioContext.
  navigator.mediaDevices.getUserMedia=async()=>{throw new DOMException('denied','NotAllowedError');};
  await app.startSpeechRecording();
  assert.match(app.speech.asr.error,/麦克风权限被拒绝/);assert.equal(app.speech.asr.acquiring,false);
  assert.equal(closed,2);
  // A normal stop disconnects audio nodes, closes the mic, and uploads WAV.
  let processor, disconnected=0;
  global.AudioContext=class {
    constructor(){this.sampleRate=48000;this.destination={};}
    async resume(){} async close(){closed++;}
    createMediaStreamSource(){return {connect(){},disconnect(){disconnected++;}};}
    createScriptProcessor(){processor={connect(){},disconnect(){disconnected++;},onaudioprocess:null};return processor;}
    createGain(){return {gain:{value:1},connect(){},disconnect(){disconnected++;}};}
  };
  navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>[{stop:()=>stopped++}]});
  global.fetch=async(url,options)=>{
    assert.equal(url,'/api/setup/asr/test');assert.ok(options.body.get('audio') instanceof Blob);
    return response({ok:true,text:'正常录音识别'});
  };
  await app.startSpeechRecording(); assert.equal(app.speech.asr.recording,true);
  processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(480).fill(.2)}});
  await app.stopSpeechRecording();
  assert.equal(app.speech.asr.recording,false);assert.equal(stopped,2);assert.equal(closed,3);
  assert.equal(disconnected,3);assert.equal(processor.onaudioprocess,null);assert.equal(app.speech.asr.text,'正常录音识别');
  // Hardware readiness requires ACK state, not only the PC's desired preference.
  app.currentDeviceId='deskbot_test';let deviceMuted=false;const robotPosts=[];
  app.api=async(url,options)=>{
    if(url.endsWith('/rtc_health'))return {ok:true,rtc:{devices:{deskbot_test:{active:true,room_connected:true,agent_attached:true}}}};
    if(options)robotPosts.push({url,body:JSON.parse(options.body)});
    if(url.includes('/mic_mute')){
      if(options)deviceMuted=JSON.parse(options.body).muted;
      return {ok:true,supported:true,muted:deviceMuted,device_muted:deviceMuted};
    }
    if(url.endsWith('/asr_auto_reply'))return {ok:true,enabled:true};
    throw new Error(url);
  };
  await app.refreshSpeechRobot(); assert.equal(app.speechRobotReady(),true);
  app.speech.robot.rtc.active=false; assert.equal(app.speechRobotReady(),false);
  assert.match(app.speechRobotState(),/等待语音/);
  await app.startSpeechRobot(); assert.equal(app.speechRobotReady(),true);
  assert.deepEqual(robotPosts,[
    {url:'/proxy/deskbot/api/asr_auto_reply',body:{enabled:true}},
    {url:'/proxy/deskbot/api/mic_mute',body:{device_id:'deskbot_test',muted:false}}
  ]);
  await app.pauseSpeechRobot();assert.equal(app.speechRobotReady(),false);
  assert.match(app.speechRobotState(),/收音已暂停/);assert.equal(deviceMuted,true);
  assert.deepEqual(robotPosts[2],{url:'/proxy/deskbot/api/mic_mute',body:{device_id:'deskbot_test',muted:true}});
  const normalApi=app.api;
  app.api=async(url,options)=>url.includes('/mic_mute')?{ok:true,supported:true,muted:false,device_muted:true}:normalApi(url,options);
  await app.refreshSpeechRobot();assert.equal(app.speechRobotReady(),false);assert.match(app.speechRobotState(),/等待设备确认/);
  await app.startSpeechRobot();assert.match(app.speech.robot.error,/未能开启收音/);
  app.api=async(url,options)=>url.includes('/mic_mute')?{ok:true,supported:true,muted:false}:normalApi(url,options);
  await app.refreshSpeechRobot();assert.equal(app.speechRobotReady(),false,'Missing device ACK must not count as ready');
  app.api=async(url)=>url.endsWith('/asr_auto_reply')?{ok:true,enabled:true}:{ok:false,supported:false,error:'firmware_too_old'};
  await app.startSpeechRobot();assert.match(app.speech.robot.error,/firmware_too_old/);
  app.disposeSpeechTests();
  console.log('R6 speech tests: PCM WAV, three independent services, transfer, limits, cancel, microphone refusal/normal stop/cancel cleanup, robot start/pause/ACK readiness passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
