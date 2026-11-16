/* Continuous microphone capture. Actual input samples are averaged to 16 kHz;
   headers are never relabelled. Chunks are independently recoverable PCM. */
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super(); this.target=16000; this.phase=0; this.sum=0; this.weight=0;
    this.buffer=new Int16Array(80000);this.used=0;this.active=true;
    this.port.onmessage=e=>{if(e.data==='stop'){this.active=false;this.flush();this.port.postMessage({stopped:true});}};
  }
  flush(){if(!this.used)return;const pcm=this.buffer.slice(0,this.used);this.port.postMessage({pcm:pcm.buffer},[pcm.buffer]);this.used=0;}
  process(inputs){
    if(!this.active)return false;
    const channels=inputs[0];if(!channels?.length)return true;
    const ratio=sampleRate/this.target;
    for(let i=0;i<channels[0].length;i++){
      let value=0;for(const channel of channels)value+=channel[i]/channels.length;
      let left=1;
      while(left>1e-8){
        const take=Math.min(left,ratio-this.phase);this.sum+=value*take;this.weight+=take;this.phase+=take;left-=take;
        if(this.phase>=ratio-1e-8){
          const sample=Math.max(-1,Math.min(1,this.sum/this.weight));this.buffer[this.used++]=Math.round(sample*(sample<0?32768:32767));
          this.phase=0;this.sum=0;this.weight=0;
          if(this.used===this.buffer.length)this.flush();
        }
      }
    }
    return true;
  }
}
registerProcessor('crj-capture',CaptureProcessor);
