import { useCallback, useEffect, useRef, useState } from 'react';

export type LocalRecording = { blob: Blob; url: string; filename: string; duration: number };
const MAX_SECONDS = 300;
const MAX_BYTES = 20 * 1024 * 1024;

export function useRecorder() {
  const [recording, setRecording] = useState(false);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [local, setLocal] = useState<LocalRecording | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<number | null>(null);
  const mounted = useRef(true);
  const objectURL = useRef('');
  const startLock = useRef(false);
  const stop = useCallback(() => {
    if (recorder.current?.state === 'recording') { setStopping(true); recorder.current.stop(); }
    if (timer.current !== null) { window.clearInterval(timer.current); timer.current = null; }
    stream.current?.getTracks().forEach(track => track.stop());
    stream.current = null;
  }, []);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (timer.current !== null) window.clearInterval(timer.current);
      if (recorder.current?.state === 'recording') recorder.current.stop();
      stream.current?.getTracks().forEach(track => track.stop());
      if (objectURL.current) URL.revokeObjectURL(objectURL.current);
    };
  }, []);
  const discard = useCallback(() => {
    if (objectURL.current) URL.revokeObjectURL(objectURL.current);
    objectURL.current = ''; setLocal(null); setElapsed(0); setNotice(''); setError('');
  }, []);
  async function start() {
    if (startLock.current || recording || stopping || local) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) { setError('当前页面无法使用麦克风。请通过 localhost 或受信任的 HTTPS 地址访问，也可以上传已有音频。'); return; }
    if (typeof MediaRecorder === 'undefined') { setError('当前浏览器不支持网页录音，请使用支持 MediaRecorder 的浏览器，或上传本地音频。'); return; }
    startLock.current = true; setStarting(true); setError(''); setNotice('');
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      if (!mounted.current) { media.getTracks().forEach(track => track.stop()); return; }
      stream.current = media;
      const mime = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm', 'audio/ogg;codecs=opus'].find(type => MediaRecorder.isTypeSupported(type));
      const instance = new MediaRecorder(media, mime ? { mimeType: mime, audioBitsPerSecond: 96000 } : undefined);
      recorder.current = instance;
      const chunks: BlobPart[] = []; let bytes = 0;
      const startedAt = performance.now();
      // Collect every chunk from this single recorder, including its container
      // header, into one complete take. No individual timeslice is uploaded.
      instance.ondataavailable = event => {
        if (event.data.size) { chunks.push(event.data); bytes += event.data.size; }
        if (bytes >= MAX_BYTES && instance.state === 'recording') { if (mounted.current) setNotice('已达到单段大小上限并停止录音。请保存本段后另录下一段。'); stop(); }
      };
      instance.onstop = () => {
        media.getTracks().forEach(track => track.stop());
        if (timer.current !== null) { window.clearInterval(timer.current); timer.current = null; }
        if (!mounted.current) return;
        const duration = (performance.now() - startedAt) / 1000;
        const blob = new Blob(chunks, { type: instance.mimeType || mime || 'audio/webm' });
        setRecording(false); setStopping(false); setElapsed(Math.floor(duration)); recorder.current = null; stream.current = null;
        if (!blob.size) { setError('本次录音为空，请检查麦克风后重新录制。'); return; }
        const extension = blob.type.includes('mp4') ? 'm4a' : blob.type.includes('ogg') ? 'ogg' : blob.type.includes('wav') ? 'wav' : blob.type.includes('mpeg') || blob.type.includes('mp3') ? 'mp3' : blob.type.includes('flac') ? 'flac' : 'webm';
        if (objectURL.current) URL.revokeObjectURL(objectURL.current);
        objectURL.current = URL.createObjectURL(blob);
        setLocal({ blob, url: objectURL.current, filename: `录音-${new Date().toISOString().replace(/[:.]/g, '-')}.${extension}`, duration });
        if (duration > MAX_SECONDS + 2) setNotice('浏览器后台或设备暂停可能延迟停止。已保留实际经过时间，请试听核实音频后保存；长询问建议保持页面前台并分段录制。');
        if (blob.size > MAX_BYTES) setError('录音停止时文件超过 20 MiB，暂不能上传。请下载保留本段，再分成较短音频上传。');
      };
      instance.onerror = () => { if (mounted.current) setError('录音设备发生错误。已采集的内容将保留为本地音频，请试听确认。'); stop(); };
      instance.start(1000); setRecording(true); setElapsed(0);
      timer.current = window.setInterval(() => {
        const seconds = (performance.now() - startedAt) / 1000;
        if (mounted.current) setElapsed(Math.floor(Math.min(seconds, MAX_SECONDS)));
        if (seconds >= MAX_SECONDS) { if (mounted.current) setNotice('本段已录满 5 分钟并自动停止。请保存后另录下一段。'); stop(); }
      }, 250);
    } catch (error) {
      stream.current?.getTracks().forEach(track => track.stop()); stream.current = null;
      if (mounted.current) {
        const name = error instanceof DOMException ? error.name : '';
        setError(name === 'NotAllowedError' ? '未获得麦克风权限，请在浏览器中允许后重试；也可上传已有音频。' : name === 'NotFoundError' ? '未找到可用麦克风，请连接设备后重试。' : name === 'NotReadableError' ? '麦克风被占用或无法读取，请检查设备连接及其他录音应用。' : '无法开始录音，请检查浏览器、麦克风和页面访问方式。');
        setRecording(false); setStopping(false);
      }
    } finally { startLock.current = false; if (mounted.current) setStarting(false); }
  }
  return { recording, starting, stopping, elapsed, local, error, notice, start, stop, discard };
}
