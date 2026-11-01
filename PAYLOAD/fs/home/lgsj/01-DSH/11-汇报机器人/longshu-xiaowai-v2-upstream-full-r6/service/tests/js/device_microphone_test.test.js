'use strict';
const assert=require('node:assert/strict');
const {mixin}=require('../../src/deskbot_server/web/static/longshu_speech_tests.js');
function vm(){
  const v=Object.assign(mixin.data(),{currentDeviceId:'robot-a'});
  for(const [k,m] of Object.entries(mixin.methods))v[k]=m.bind(v);
  return v;
}
const response=body=>({ok:true,json:async()=>body});
async function main(){
  const calls=[];
  Object.defineProperty(global,'navigator',{value:{mediaDevices:{getUserMedia(){throw Error('PC microphone must never be opened');}}}});
  const active={ok:true,device_id:'robot-a',capture_id:'capture-a',recording:true};
  const done={...active,recording:false,bytes:64000,seconds:2,elapsed:2,sample_rate:16000,peak_dbfs:-15,clipped_samples:0};
  global.fetch=async(url,opt)=>{
    calls.push({url,body:opt.body&&JSON.parse(opt.body)});
    if(url.includes('/audio?'))return {ok:true,blob:async()=>new Blob(['RIFF'],{type:'audio/wav'})};
    return response(JSON.parse(opt.body).action==='start'?active:done);
  };
  const app=vm();
  await app.startDeviceMicTest();assert.equal(app.speech.deviceMic.recording,true);
  await app.stopDeviceMicTest();
  assert.equal(app.speech.deviceMic.recording,false);assert.equal(app.speech.deviceMic.busy,false);
  assert.match(app.speech.deviceMic.audioUrl,/^blob:/);
  assert.deepEqual(calls[0].body,{device_id:'robot-a',action:'start'});
  assert.deepEqual(calls[1].body,{device_id:'robot-a',action:'stop',capture_id:'capture-a'});
  assert.ok(calls.every(c=>c.url.startsWith('/proxy/deskbot/api/microphone_test')));
  assert.match(calls[2].url,/device_id=robot-a&capture_id=capture-a/);
  app.disposeSpeechTests();

  // A late start result after a device change is stopped against the OLD device.
  let release;calls.length=0;
  global.fetch=(url,opt)=>{
    const body=JSON.parse(opt.body);calls.push(body);
    if(body.action==='start')return new Promise(resolve=>release=()=>resolve(response(active)));
    return Promise.resolve(response(done));
  };
  const moved=vm(),pending=moved.startDeviceMicTest();
  moved.currentDeviceId='robot-b';mixin.watch.currentDeviceId.call(moved);
  release();await pending;
  assert.equal(moved.speech.deviceMic.recording,false);
  assert.equal(moved.speech.deviceMic.captureId,'');
  assert.deepEqual(calls[1],{device_id:'robot-a',action:'stop',capture_id:'capture-a'});
  moved.disposeSpeechTests();
  console.log('Device microphone UI: physical device only, WAV playback, stop and device-change race passed.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
