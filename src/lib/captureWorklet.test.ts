import { describe, it, expect } from 'vitest';
import workletSource from '../../public/audio/capture-worklet.js?raw';
import { pcmWav } from './captureStore';

describe('continuous PCM capture', () => {
  for (const rate of [44100, 48000, 96000]) {
    it(`keeps sample continuity beyond five minutes at ${rate} Hz`, () => {
      let Processor: {new(): {process: (v: Float32Array[][]) => boolean; port: {onmessage: (e: {data: string}) => void}}};
      let count = 0;
      const lengths: number[] = [];
      let bad = false;
      class Base {
        port = {
          postMessage: (message: {pcm?: ArrayBuffer}) => {
            if (!message.pcm) return;
            const samples = new Int16Array(message.pcm);
            count += samples.length;
            lengths.push(samples.length);
            for (const sample of samples) if (Math.abs(sample - 8192) > 1) bad = true;
          },
        };
      }
      new Function('AudioWorkletProcessor', 'sampleRate', 'registerProcessor', workletSource)(
        Base, rate, (_: string, constructor: typeof Processor) => { Processor = constructor; },
      );
      const recorder = new Processor!();
      const frame = new Float32Array(128).fill(.25);
      const input = rate * 360 + Math.floor(rate * .123);
      for (let n = 0; n < input; n += 128) recorder.process([[frame.subarray(0, Math.min(128, input - n))]]);
      recorder.port.onmessage({data: 'stop'});
      expect(Math.abs(count - Math.floor(input / rate * 16000))).toBeLessThanOrEqual(1);
      expect(lengths.slice(0, -1).every(n => n === 80000)).toBe(true);
      expect(bad).toBe(false);
    }, 20000);
  }
  it('creates a playable independent mono PCM WAV header', async () => {
    const pcm = new Int16Array([0, 32767, -32768]);
    const blob = pcmWav([pcm.buffer]);
    const buffer = await new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(blob);
    });
    const view = new DataView(buffer);
    expect(new TextDecoder().decode(buffer.slice(0, 4))).toBe('RIFF');
    expect(view.getUint32(24, true)).toBe(16000);
    expect(view.getUint32(40, true)).toBe(6);
    expect(view.getInt16(48, true)).toBe(-32768);
  });
});
