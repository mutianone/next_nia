import type { Answer } from '../knowledge-types';
export type AIStreamEvent = { type: 'status'; message: string } | { type: 'partial' | 'complete'; answer: Answer } | { type: 'error'; message: string };
export async function streamQuestion(body: object, onEvent: (event: AIStreamEvent) => void, signal: AbortSignal) {
  const response = await fetch('/api/knowledge/ask/stream', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body), signal });
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('auth-expired'));
    const data = await response.json().catch(() => null);
    throw new Error(typeof data?.detail === 'string' ? data.detail : 'AI 请求失败，请重试。');
  }
  if (!response.body) throw new Error('浏览器无法读取流式回答。');
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = '', size = 0, completed = false;
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 4 * 1024 * 1024) throw new Error('AI 回答超过长度限制。');
      buffer += decoder.decode(value, {stream:true}).replace(/\r\n/g,'\n');
      let end: number;
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const packet = buffer.slice(0,end); buffer = buffer.slice(end + 2);
        const raw = packet.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
        if (!raw) continue;
        const event = JSON.parse(raw) as AIStreamEvent;
        if (event.type === 'error') throw new Error(event.message);
        if (event.type === 'complete') completed = true;
        onEvent(event);
      }
    }
    if (!completed) throw new Error('AI 回答中断，请重试。');
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}
