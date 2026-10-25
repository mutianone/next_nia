import { streamQuestion } from '../lib/aiStream';
import { Access } from '../auth/AuthContext';
import { PageHeading } from '../components/PageHeading';
import { confirmAction } from '../lib/confirmation';
import { useEffect, useState, useRef } from 'react';
import { ArrowRight, BookOpen, Check, ChevronRight, CircleAlert, Copy, FileSearch, LoaderCircle, LockKeyhole, MessageSquareText, Search, Send, Settings2, ShieldCheck, Sparkles } from 'lucide-react';
import { api } from '../api';
import type { Answer, Citation, SearchResult } from '../knowledge-types';
import { errorMessage, today } from '../knowledge-types';
import { DocumentViewer } from './Knowledge';

type ProviderConfig = { enabled: boolean; chat_model: string; api_key_configured: boolean };
type AISettings = { external_permissions?: {question: boolean}; enabled: boolean; default_provider: 'siliconflow' | 'deepseek'; providers: Record<'siliconflow' | 'deepseek', ProviderConfig> };
type Turn = { question: string; date: string; mode: 'search' | 'ai'; answer?: Answer; result?: SearchResult; error?: string; streaming?: boolean };
const starterQuestions = ['申请延长居留期限，应当何时提出申请？', '签证停留期限延期，应当何时提出申请？', '外国人在中国境内工作需要哪些许可？'];

export function Assistant({ onKnowledge, onSettings }: { onKnowledge: () => void; onSettings: () => void }) {
  const [settings, setSettings] = useState<AISettings | null>(null);
  const [settingsError, setSettingsError] = useState('');
  const [mode, setMode] = useState<'search' | 'ai'>('search');
  const [question, setQuestion] = useState('');
  const [asOf, setAsOf] = useState(today);
  const consent = settings?.external_permissions?.question === true;
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const requestRef = useRef<AbortController | null>(null);
  useEffect(() => () => requestRef.current?.abort(), []);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [selected, setSelected] = useState<Citation | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const [notice, setNotice] = useState('');
  const currentProvider = settings?.default_provider;
  const providerName = 'AI';
  const provider = currentProvider ? settings?.providers[currentProvider] : null;
  const ready = (settings?.enabled && provider?.enabled && provider.api_key_configured);
  async function loadSettings() { setSettingsError(''); try { const data = await api<{ settings: { ai: AISettings }; runtime: { sample_workspace?: boolean } }>('/settings'); setSettings(data.settings.ai); } catch (e) { setSettingsError(errorMessage(e)); } }
  useEffect(() => { void loadSettings(); }, []);
  useEffect(() => { if (copied === null) return; const timer = window.setTimeout(() => setCopied(null), 2000); return () => window.clearTimeout(timer); }, [copied]);
  async function submit() {
    const value = question.trim();
    if (!value || busy || (mode === 'ai' && (!consent || !ready))) return;
    setBusy(true); setNotice('');
    const turn: Turn = { question: value, date: asOf, mode, streaming: mode === 'ai' };
    const index = turns.length;
    setTurns(existing => [...existing, turn]); setProgress('正在检索法规依据…');
    const controller = new AbortController(); requestRef.current = controller;
    const update = (changes: Partial<Turn>) => setTurns(existing => existing.map((item, i) => i === index ? {...item, ...changes} : item));
    try {
      if (mode === 'search') update({result: await api<SearchResult>('/knowledge/search', { method:'POST', body:JSON.stringify({query:value,as_of:asOf,limit:6}), signal:controller.signal })});
      else await streamQuestion({question:value,as_of:asOf,confirm_external:consent}, event => {
        if (event.type === 'status') setProgress(event.message);
        else if (event.type === 'partial' || event.type === 'complete') { update({answer:event.answer,streaming:event.type !== 'complete'}); setProgress('AI 正在继续生成，已完成结论逐条显示…'); }
      }, controller.signal);
      setQuestion('');
    } catch (e) { update({answer:undefined,result:undefined,error:controller.signal.aborted ? '已停止生成，本次未完成回答已清除。' : errorMessage(e)}); }
    finally { update({streaming:false}); setBusy(false); requestRef.current = null; }
  }

  async function copyTurn(turn: Turn, index: number) {
    const sources = turn.answer?.sources || turn.result?.items || [];
    const content = `${turn.question}\n适用日期：${turn.date}\n\n${turn.answer?.answer || '本地检索原文'}\n\n${sources.map((s, i) => `[${i + 1}] ${s.title} · ${s.version} · ${s.article}\n${s.text}\n来源：${s.source}`).join('\n\n')}`;
    try { await navigator.clipboard.writeText(content); setCopied(index); } catch { setNotice('浏览器未允许复制，请手动选择文本复制。'); }
  }
  return <div className="page-content assistant-page"><PageHeading title="法规问答" description="检索已发布法规，结合原文核对回答。" scene="assistant"><button className="btn btn-secondary" onClick={onKnowledge}><BookOpen size={16}/>管理知识库</button></PageHeading>
    <div className="qa-layout"><section className="panel qa-workspace"><header className="qa-workspace-header"><div className="qa-agent-icon"><Sparkles size={21}/></div><div><strong>境智 · 法规助手</strong><p>检索结果可展开核对原文</p></div><span className="kb-local-badge"><span/>{mode === 'search' ? '本地检索' : ready ? providerName : 'AI 未就绪'}</span></header>
      {!turns.length ? <div className="qa-welcome"><h2>需要查找哪项规定？</h2><p>输入业务问题，或从以下常见问题开始。</p><div className="qa-starters">{starterQuestions.map(q => <button key={q} onClick={() => { setQuestion(q);  document.getElementById('qa-question')?.focus(); }}><MessageSquareText size={16}/><span>{q}</span><ArrowRight size={15}/></button>)}</div></div> : <div className="qa-conversation" aria-live="polite">{turns.map((turn, index) => {
        const sources = turn.answer?.sources || turn.result?.items || [];
        return <article className="qa-turn" key={index}><div className="qa-question"><span className="qa-you">你</span><div><p>{turn.question}</p><small>{turn.date} · {turn.mode === 'search' ? '本地检索' : 'AI 引用问答'}</small></div></div><div className="qa-response"><div className="qa-response-label"><span className="qa-response-icon">{turn.mode === 'ai' ? <Sparkles size={15}/> : <FileSearch size={15}/>}</span><strong>{turn.mode === 'ai' ? '基于资料的回答' : '找到的原文依据'}</strong>{!turn.error && !turn.streaming && <button className="icon-button" aria-label={`复制第${index + 1}次结果及引用`} onClick={() => copyTurn(turn, index)}>{copied === index ? <Check size={14}/> : <Copy size={14}/>}</button>}</div>
          {turn.error ? <div className="kb-error" role="alert"><CircleAlert size={17}/>{turn.error}</div> : <>{turn.streaming && !turn.answer ? <p className="qa-search-summary">正在生成，已核验引用的结论将逐条显示…</p> : turn.answer?.status === 'answered' ? <><div className="qa-answer">{turn.answer.claims?.length ? turn.answer.claims.map((claim, ci) => <p key={ci}>{claim.text} <span className="qa-inline-citations">{[...new Set(claim.source_ids)].map(id => { const sourceIndex = sources.findIndex(s => s.id === id); return sourceIndex >= 0 && <button key={id} aria-label={`查看依据${sourceIndex + 1}`} onClick={() => setSelected(sources[sourceIndex])}>[{sourceIndex + 1}]</button>; })}</span></p>) : <p>{turn.answer.answer}</p>}</div><p className="qa-reviewed-note"><ShieldCheck size={13}/>AI 辅助归纳，适用性与结论仍需人工核对。</p></> : turn.answer ? <div className="qa-insufficient"><CircleAlert size={20}/><div><strong>当前依据不足</strong><p>{turn.answer.answer}</p></div></div> : <p className="qa-search-summary">{sources.length ? `找到 ${sources.length} 条相关片段。点击阅读完整原文。` : '没有找到当前日期适用的已发布依据。请调整关键词，或补充资料并审核发布。'}</p>}
          {!!sources.length && <div className="qa-sources"><div className="qa-sources-heading"><BookOpen size={14}/>原文依据 <span>{sources.length}</span></div>{sources.map((s, si) => <button className="qa-source" key={s.id} onClick={() => setSelected(s)}><span className="kb-citation-index">{si + 1}</span><div><strong>{s.title}</strong><small>{s.version} · {s.article}{s.page ? ` · 第 ${s.page} 页` : ''}</small><p>{s.text}</p></div><ChevronRight size={16}/></button>)}</div>}
          <div className="qa-response-footer"><span>{turn.answer?.provider ? 'AI' : '本地资料检索'}</span><span>适用日期 {turn.date}</span></div></>}
        </div></article>;
      })}</div>}
      {busy && <div className="qa-working" role="status"><LoaderCircle className="spinning" size={17}/>{mode === 'ai' ? progress : '正在匹配本地条款与适用版本…'}<button type="button" className="text-button" onClick={() => requestRef.current?.abort()}>停止生成</button></div>}
      <form className="qa-composer" onSubmit={e => { e.preventDefault(); void submit(); }}><div className="qa-composer-toolbar"><div className="qa-modes"><button type="button" aria-pressed={mode === 'search'} onClick={() => { setMode('search');  }} disabled={busy}><Search size={14}/>查找依据</button><button type="button" aria-pressed={mode === 'ai'} onClick={() => { setMode('ai');  void loadSettings(); }} disabled={busy}><Sparkles size={14}/>AI 问答</button></div><label className="qa-date">适用日期<input type="date" value={asOf} onChange={e => { setAsOf(e.target.value);  }} disabled={busy} required/></label></div>
        {mode === 'ai' && !ready && <div className="qa-config-note"><Settings2 size={15}/><span>{settingsError || '请先配置 API 密钥，并启用 AI 和默认服务商。'}</span><Access permission="admin"><button type="button" onClick={onSettings}>去配置<ArrowRight size={13}/></button></Access></div>}
        <textarea id="qa-question" aria-label="法规问题" placeholder={mode === 'search' ? '输入关键词或业务问题，查找本地法规依据…' : '描述法规问题，请勿填入姓名、证件号等个人信息…'} value={question} maxLength={1000} onChange={e => { setQuestion(e.target.value);  }} disabled={busy} required rows={3} onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); void submit(); } }}/>
        {mode === 'ai' && settings && !consent && <div className="qa-consent" role="status"><span>请前往系统配置 → AI 服务，勾选“允许法规问答发送至服务商”。非管理员请联系管理员。</span><Access permission="admin"><button type="button" className="text-button" onClick={onSettings}>前往系统配置</button></Access></div>}
        <div className="qa-composer-footer"><span><LockKeyhole size={12}/>{mode === 'search' ? '全程本地 · 不调用 AI' : '人员档案不会自动加入问答'}<small>{question.length}/1000</small></span><button type="submit" className="btn btn-primary" disabled={busy || !question.trim() || (mode === 'ai' && (!ready || !consent))}>{busy ? <LoaderCircle className="spinning" size={16}/> : <Send size={15}/>} {mode === 'search' ? '检索依据' : '生成回答'}</button></div>
      </form>{notice && <p role="status" className="qa-notice">{notice}</p>}<div className="qa-session-note"><span>会话仅保留在当前页面，离开后清空。</span>{turns.length > 0 && <button disabled={busy} onClick={async () => { if ((await confirmAction('清空当前页面的问答结果？法规原文不受影响。', {"title":"清空当前会话？","confirmLabel":"清空会话","tone":"danger"}))) setTurns([]); }}>清空本页会话</button>}</div>
    </section><aside className="qa-sidebar"><section className="panel qa-side-card"><span className="qa-side-number">01</span><h3>回答从哪里来？</h3><p>来自知识库中已发布、在所选日期内有效的法规原文。</p><div className="qa-mini-pipeline"><span>你的问题</span><i/><span>本地检索</span><i/><span>原文与引用</span></div><button className="text-button" onClick={onKnowledge}>查看知识库<ArrowRight size={14}/></button></section><section className="panel qa-side-card"><span className="qa-side-number">02</span><h3>没有依据，就不补写。</h3><p>资料不足时会明确提示。AI 引用编号由系统核对，点击即可查看对应原文。</p><span className="qa-side-mark"><ShieldCheck size={17}/>每条结论都需要核对</span></section><section className="qa-side-tips"><LockKeyhole size={20}/><h3>你的资料，留在本地。</h3><p>查找依据无需网络。AI 问答只使用标记允许外发的法规片段，不自动读取人员档案。</p></section></aside></div>
    {selected && <DocumentViewer id={selected.document_id} highlight={selected.id} onClose={() => setSelected(null)}/>}
  </div>;
}
