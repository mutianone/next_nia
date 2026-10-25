import { afterEach, expect, it, vi } from 'vitest';
import { streamQuestion } from './aiStream';
afterEach(()=>vi.unstubAllGlobals());
function response(events: unknown[]) {
  const bytes = new TextEncoder().encode(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''));
  return new Response(new ReadableStream({ start(controller) { for(let i=0;i<bytes.length;i+=3) controller.enqueue(bytes.slice(i,i+3)); controller.close(); } }));
}
it('decodes split UTF-8 streaming events and requires completion', async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>response([{type:'status',message:'正在生成'},{type:'complete',answer:{answer:'中文结论'}}])));
  const receive=vi.fn(); await streamQuestion({},receive,new AbortController().signal);
  expect(receive.mock.calls[0][0].message).toBe('正在生成');
  expect(receive.mock.calls[1][0].answer.answer).toBe('中文结论');
});
it('rejects truncated or error streams instead of accepting a partial answer', async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>response([{type:'partial',answer:{answer:'未完成'}}])));
  await expect(streamQuestion({},vi.fn(),new AbortController().signal)).rejects.toThrow('中断');
  vi.stubGlobal('fetch',vi.fn(async()=>response([{type:'error',message:'引用检查失败'}])));
  await expect(streamQuestion({},vi.fn(),new AbortController().signal)).rejects.toThrow('引用检查失败');
});
