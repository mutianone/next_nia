import { SpeakerCount } from './SpeakerCount';
import './ContinuousRecorder.css';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth/AuthContext';
import { captureStore, pcmWav, type CaptureCache } from '../lib/captureStore';
import { confirmAction } from '../lib/confirmation';

type Batch={id:string;state:string;error:string;text:string;seconds:number;offset_seconds:number;completed_groups:number;total_groups:number;facts:{label:string;value:string}[]};
type Capture={id:string;state:string;error:string;partial:boolean;saved_count:number;received_sequences:number[];saved_seconds:number;final_count:number|null;started_at:string;batches:Batch[]};
const terminal=(state?:string)=>['completed','partial','cancelled'].includes(state||'');
const duration=(seconds:number)=>`${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`;
export function ContinuousRecorder({recordId,revision,disabled,visible,onActive,onCompleted,multiSpeaker}:{multiSpeaker?:boolean;recordId:string;revision:number;disabled:boolean;visible:boolean;onActive:(v:boolean)=>void;onCompleted:()=>void}){
 const [speakerCount,setSpeakerCount]=useState<number|null>(2);
 const {user}=useAuth();const key=`${user?.id}:${recordId}`;
 const [capture,setCapture]=useState<Capture|null>(null),[recording,setRecording]=useState(false),[starting,setStarting]=useState(false),[error,setError]=useState(''),[pending,setPending]=useState(0),[elapsed,setElapsed]=useState(0),[recovered,setRecovered]=useState(false);
 const cache=useRef<CaptureCache|null>(null),context=useRef<AudioContext|null>(null),node=useRef<AudioWorkletNode|null>(null),stream=useRef<MediaStream|null>(null),writing=useRef<Promise<void>>(Promise.resolve()),uploading=useRef(false),mounted=useRef(true),live=useRef(false),samples=useRef(0),memory=useRef<{sequence:number;pcm:ArrayBuffer}[]>([]),writeBytes=useRef(0),closed=useRef(false),finishRequested=useRef(false),completed=useRef(''),sawActive=useRef(false),stopTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
 const onCompletedRef=useRef(onCompleted);onCompletedRef.current=onCompleted;
 const endpoint=`/records/${recordId}/captures`;
 const refresh=useCallback(async()=>{const list=await api<{items:Capture[]}>(endpoint);const current=cache.current ? list.items.find(x=>x.id===cache.current!.captureId)||null : list.items.at(-1)||null;if(!mounted.current)return;setCapture(current);if(current&&!terminal(current.state))sawActive.current=true;if(current&&terminal(current.state)&&completed.current!==current.id){completed.current=current.id;setError('');if(cache.current?.captureId===current.id){const remaining=await captureStore.parts(key);if(!remaining.length&&!memory.current.length){await captureStore.clear(key);cache.current=null;setRecovered(false);}else{setError('后台已使用部分录音生成；仍有未保存音频，请下载备份。');setPending(remaining.length+memory.current.length);}}if(sawActive.current){sawActive.current=false;onCompletedRef.current();}}},[endpoint,key]);
 const closeAudio=()=>{if(stopTimer.current)clearTimeout(stopTimer.current);stopTimer.current=null;live.current=false;stream.current?.getTracks().forEach(t=>t.stop());stream.current=null;void context.current?.close();context.current=null;node.current=null;if(mounted.current)setRecording(false);};
 const pump=useCallback(async()=>{
  if(uploading.current||!cache.current)return;uploading.current=true;
  try{
   const local=cache.current;const parts=await captureStore.parts(key);if(mounted.current)setPending(await captureStore.count(key)+memory.current.length);
   for(const p of parts){
    if(!mounted.current)break;
    const result=await api<{sequence:number;sha256:string}>(`${endpoint}/${local.captureId}/chunks/${p.sequence}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:p.pcm,signal:AbortSignal.timeout(20000)});
    const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',p.pcm))).map(b=>b.toString(16).padStart(2,'0')).join('');
    if(result.sequence!==p.sequence||result.sha256!==digest)throw new Error('后端分片确认不一致，已保留本地音频');
    await captureStore.remove(key,p.sequence);
   }
   const remaining=await captureStore.parts(key);if(mounted.current)setPending(await captureStore.count(key)+memory.current.length);
   if(!live.current&&local.endedAt&&!remaining.length&&!memory.current.length&&finishRequested.current){
    await api(`${endpoint}/${local.captureId}/finish`,{method:'POST',body:JSON.stringify({count:local.count,ended_at:local.endedAt,partial:Boolean(local.incomplete),confirmed:true})});finishRequested.current=false;
   }
   await refresh();
  }catch(e){if(mounted.current)setError((e as Error).message+'；未确认的音频仍保留，可重试保存。');}
  finally{uploading.current=false;}
 },[endpoint,key,refresh]);
 const stop=useCallback(()=>{if(!live.current||closed.current)return;closed.current=true;node.current?.port.postMessage('stop');setError('正在保存尾段，保存完成后继续后台转写与回填。');stopTimer.current=setTimeout(()=>{if(!live.current)return;if(cache.current)cache.current.incomplete=true;closeAudio();setRecovered(true);setError('未收到音频尾段确认，录音已停止。请核对并处理已保存部分。');},3000);},[]);
 useEffect(()=>{
  mounted.current=true;
  void (async()=>{try{const value=await captureStore.get(key);if(!mounted.current)return;if(value){cache.current=value;setRecovered(true);}await refresh();}catch(e){setError((e as Error).message);}})();
  const timer=window.setInterval(()=>{void pump();void refresh().catch(()=>{});},3000);
  const leave=(e:BeforeUnloadEvent)=>{if(live.current||cache.current){e.preventDefault();e.returnValue='';}};
  window.addEventListener('beforeunload',leave);
  return()=>{mounted.current=false;window.clearInterval(timer);window.removeEventListener('beforeunload',leave);node.current?.port.postMessage('stop');closeAudio();};
 },[key,pump,refresh]);
 useEffect(()=>{onActive(starting||recording||Boolean(capture&&!terminal(capture.state))||recovered);},[starting,recording,capture,recovered,onActive]);
 async function start(){
  if(starting||recording||disabled)return;setStarting(true);setError('');setCapture(null);
  try{
   if(!navigator.mediaDevices?.getUserMedia||!window.AudioWorkletNode)throw new Error('浏览器不支持连续录音，请使用最新版 Chrome/Edge 并通过 localhost 或 HTTPS 访问。');
   const clicked=new Date().toISOString();const media=await navigator.mediaDevices.getUserMedia({audio:true});stream.current=media;
   const ctx=new AudioContext();context.current=ctx;await ctx.audioWorklet.addModule('/audio/capture-worklet.js');await ctx.resume();
   const value:CaptureCache={key,captureId:crypto.randomUUID(),count:0,startedAt:clicked,capturedAt:new Date().toISOString()};await captureStore.save(value);cache.current=value;
   const result=await api<Capture>(endpoint,{method:'POST',body:JSON.stringify({id:value.captureId,started_at:clicked,captured_at:value.capturedAt,expected_revision:revision,confirmed:true,...(multiSpeaker && speakerCount ? {speaker_count:speakerCount} : {})})});setCapture(result);
   const processor=new AudioWorkletNode(ctx,'crj-capture');node.current=processor;samples.current=0;closed.current=false;live.current=true;setRecording(true);setRecovered(false);
   processor.port.onmessage=e=>{
    if(e.data.pcm){const pcm=e.data.pcm as ArrayBuffer;const sequence=cache.current!.count++;writeBytes.current+=pcm.byteLength;if(writeBytes.current>8*1024*1024){cache.current!.incomplete=true;setError('保存队列积压过多，正在安全停止以保留已有录音。');stop();}samples.current+=pcm.byteLength/2;setElapsed(samples.current/16000);const saved={...cache.current!};
     writing.current=writing.current.then(async()=>{
      try{await captureStore.append(saved,sequence,pcm);}catch{memory.current.push({sequence,pcm});cache.current!.incomplete=true;setError('本地缓存写入失败，正在停止录音。请下载未保存音频并重试。');stop();}
      writeBytes.current-=pcm.byteLength;
      if(await captureStore.count(key)>640){cache.current!.incomplete=true;setError('待上传缓存已达到约100MiB，正在安全停止，请恢复连接后补传。');stop();}
      const estimate=await navigator.storage?.estimate().catch(()=>null);
      if(estimate?.quota&&estimate.usage&&estimate.quota-estimate.usage<10*1024*1024){cache.current!.incomplete=true;setError('本地存储即将不足，正在安全停止。');stop();}
      if(samples.current*2>=480*1024*1024){setError('接近单份录音容量上限，正在保存并停止。');stop();}
      void pump();
     });
    }
    if(e.data.stopped){closeAudio();void writing.current.then(async()=>{if(!cache.current)return;cache.current.endedAt=new Date().toISOString();try{await captureStore.save(cache.current);}catch{setError('尾段状态保存失败，请保留页面并下载备份。');}finishRequested.current=true;await pump();});}
   };
   processor.onprocessorerror=()=>{if(cache.current)cache.current.incomplete=true;setError('音频处理器异常，已停止采集。请使用已保存部分恢复。');closeAudio();setRecovered(true);};
   media.getAudioTracks().forEach(t=>t.addEventListener('ended',()=>{if(live.current){cache.current!.incomplete=true;setError('麦克风连接中断，将保存已采集部分。');stop();}}));
   ctx.onstatechange=()=>{if(ctx.state==='suspended'&&live.current){cache.current!.incomplete=true;setError('音频采集被系统暂停，请结束并核对已保存部分。');stop();}};
   const source=ctx.createMediaStreamSource(media);const mute=ctx.createGain();mute.gain.value=0;source.connect(processor);processor.connect(mute);mute.connect(ctx.destination);
  }catch(e){closeAudio();setError((e as Error).message);setRecovered(Boolean(cache.current));}finally{setStarting(false);}
 }
 async function retry(){setError('');try{
  if(cache.current&&!capture){const list=await api<{items:Capture[]}>(endpoint);const found=list.items.find(x=>x.id===cache.current!.captureId);if(!found){if(!(await captureStore.parts(key)).length&&!memory.current.length){await captureStore.clear(key);cache.current=null;setRecovered(false);return;}throw new Error('后端未找到录音会话，请下载本地音频备份。');}setCapture(found);}
  if(cache.current&&memory.current.length){for(const item of memory.current)await captureStore.append(cache.current,item.sequence,item.pcm);memory.current=[];}
  finishRequested.current=Boolean(cache.current?.endedAt);await pump();
  if(capture)await api(`${endpoint}/${capture.id}/retry`,{method:'POST',body:JSON.stringify({expected_revision:revision,confirmed:true})});await refresh();
 }catch(e){setError((e as Error).message);}}
 async function finishRecovered(){
  if(!capture)return;
  if(!(await confirmAction('录音已中断。将先补传缓存，再使用已保存的录音生成，并标记录音不完整。',{title:'处理已保存录音',confirmLabel:'继续处理',tone:'warning'})))return;
  if(cache.current){cache.current.endedAt=new Date().toISOString();cache.current.incomplete=true;finishRequested.current=true;await captureStore.save(cache.current);await pump();}
  else{await api(`${endpoint}/${capture.id}/finish`,{method:'POST',body:JSON.stringify({count:capture.saved_count,ended_at:new Date().toISOString(),partial:true,confirmed:true})});await refresh();}
 }
 async function skipBatch(batch:Batch){
  if(!capture||!(await confirmAction('跳过此段自动处理？原音仍保留，已有转写可人工补充，最终笔录将标记不完整。',{title:'跳过失败段',confirmLabel:'保留原音并跳过',tone:'warning'})))return;
  await api(`${endpoint}/${capture.id}/batches/${batch.id}/skip`,{method:'POST',body:JSON.stringify({expected_revision:revision,confirmed:true})});await refresh();
 }
 async function useSavedOnly(){
  if(!capture||recording)return;
  if(!(await confirmAction(`仅使用后端已保存的 ${duration(capture.saved_seconds)} 生成？未上传或缺失内容不参与回填，本地缓存仍保留。`,{title:'生成部分笔录',confirmLabel:'使用已保存部分',tone:'warning'})))return;
  finishRequested.current=false;await api(`${endpoint}/${capture.id}/finish`,{method:'POST',body:JSON.stringify({count:capture.saved_count,ended_at:new Date().toISOString(),partial:true,confirmed:true})});await refresh();
 }
 async function downloadPending(){const parts=await captureStore.parts(key,10000);const all=[...parts,...memory.current].sort((a,b)=>a.sequence-b.sequence);if(!all.length){setError('当前没有未上传分片，请下载后端已保存原音。');return;}
  // Download separately if there are gaps. Never concatenate across missing audio.
  for(const part of all){const url=URL.createObjectURL(pcmWav([part.pcm]));const a=document.createElement('a');a.href=url;a.download=`未保存录音-${String(part.sequence).padStart(5,'0')}.wav`;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}
 }
 const active=Boolean(capture&&!terminal(capture.state));
 return <section className="rec-capture rec-continuous" style={{display:visible?'block':'none'}} aria-label="连续录音与后台转写">
  <header className="rec-continuous-heading"><h3>连续录音 · 自动保存与转写</h3><span>{recording ? '录音进行中' : active ? '后台处理中' : '录音工作台'}</span></header>
  <div className="rec-continuous-metrics"><div><small>本次采集</small><strong>{duration(recording?elapsed:capture?.saved_seconds||0)}</strong></div><div><small>后端已保存</small><strong>{duration(capture?.saved_seconds||0)}</strong></div><div><small>已有转写</small><strong>{duration(capture?.batches.filter(b=>Boolean(b.text)).reduce((n,b)=>n+b.seconds,0)||0)}</strong></div><div><small>待上传分片</small><strong>{pending}<small> 段</small></strong></div></div>
  <p role="status">{capture?.state==='processing'?'正在合并回填':capture?.state==='completed'?'生成完成，等待人工核对':capture?.state==='partial'?'部分录音已生成，须补充核对':active?'后台处理中':'准备录音'}</p>
  <div className="rec-record-controls">
   {multiSpeaker&&!active&&!recovered&&!recording&&<SpeakerCount value={speakerCount} onChange={setSpeakerCount} disabled={disabled||starting}/>}
   {!active&&!recovered&&!recording&&<button className="btn btn-primary" disabled={disabled||starting} onClick={()=>void start()}>{starting?'正在启动…':'开始连续录音'}</button>}
   {recording&&<button className="btn rec-stop" disabled={closed.current} onClick={stop}>结束录音并生成笔录</button>}
   {(active||recovered)&&!recording&&<button className="btn btn-secondary" onClick={()=>void finishRecovered().catch(e=>setError(e.message))}>处理已保存部分</button>}
   {active&&!recording&&<button className="btn btn-secondary" onClick={()=>void useSavedOnly().catch(e=>setError(e.message))}>仅用后端已保存部分生成</button>}
   {(active||recovered)&&<button className="btn btn-secondary" onClick={()=>void retry()}>重试保存与处理</button>}
   {(pending>0||recovered)&&<button className="btn btn-secondary" onClick={()=>void downloadPending()}>下载未保存分片</button>}
   {capture&&<a className="btn btn-secondary" href={`/api${endpoint}/${capture.id}/backup.wav`} download>下载已保存原音</a>}
  </div>
  {capture?.received_sequences.some(n=>n>=capture.saved_count)&&<p className="rec-warning">缺少第 {capture.saved_count+1} 个保存分片，后续分片已保留，正在等待补传。</p>}
  {(error||capture?.error)&&<p className="rec-warning" role="alert">{error||capture?.error}</p>}
  {capture?.partial&&<p className="rec-warning">录音不完整，仅依据已保存部分生成。</p>}
  {!!capture?.batches.length&&<details open><summary>转写与模板候选进度（{capture.batches.filter(b=>b.state==='done').length}/{capture.batches.length} 段）</summary><div style={{maxHeight:400,overflowY:'auto'}}>{capture.batches.map((b,i)=><article className="rec-continuous-segment" key={b.id}><h4>第 {i+1} 段 · {duration(b.offset_seconds)}—{duration(b.offset_seconds+b.seconds)} · {b.state==='done'?'已完成':b.state==='skipped'?'已跳过，待人工补充':b.state==='failed'?'需重试':b.text?'正在提取候选':'等待或正在转写'} · {b.completed_groups}/{b.total_groups} 组</h4>{b.error&&<p className="rec-warning">{b.error}</p>}{b.state==='failed'&&<button className="btn btn-secondary" onClick={()=>void skipBatch(b).catch(e=>setError(e.message))}>跳过此段自动处理</button>}<p style={{whiteSpace:'pre-wrap'}}>{b.text||'转写完成后显示原文'}</p>{!!b.facts.length&&<details><summary>{b.facts.length} 项候选草稿，结束后统一合并</summary>{b.facts.map((f,j)=><p key={j}>{f.value}</p>)}</details>}</article>)}</div></details>}
 </section>;
}
