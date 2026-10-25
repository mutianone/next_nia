import { AnalysisTemplates, type AnalysisTemplate } from '../components/AnalysisTemplates';
import { Dialog } from '../components/Dialog';
import { streamQuestion } from '../lib/aiStream';
import { Access } from '../auth/AuthContext';
import { PageHeading } from '../components/PageHeading';
import { useEffect, useState, useRef } from 'react';
import { ArrowRight, BookOpen, Check, ChevronRight, CircleAlert, Copy, Download, History, Plus, RotateCcw, LoaderCircle, LockKeyhole, MessageSquareText, Send, Settings2, ShieldCheck, Sparkles } from 'lucide-react';
import { api } from '../api';
import type { Answer, Citation } from '../knowledge-types';
import { errorMessage, today } from '../knowledge-types';
import { DocumentViewer } from './Knowledge';

type ProviderConfig = { enabled: boolean; chat_model: string; api_key_configured: boolean };
type AISettings = { external_permissions?: {question: boolean}; enabled: boolean; default_provider: 'siliconflow' | 'deepseek'; providers: Record<'siliconflow' | 'deepseek', ProviderConfig> };
type Turn = { id?: string; status?: string; templateId?: string; templateRevision?: number; templateName?: string; question: string; date: string; answer?: Answer; error?: string; streaming?: boolean };
const starterQuestions = ['申请延长居留期限，应当何时提出申请？', '签证停留期限延期，应当何时提出申请？', '外国人在中国境内工作需要哪些许可？'];

export function Assistant({ onKnowledge, onSettings }: { onKnowledge: () => void; onSettings: () => void }) {
  const [conversationId,setConversationId] = useState<string|null>(null);
  const [history,setHistory] = useState<{id:string;title:string;updated_at:string}[]>([]);
  const [historyOpen,setHistoryOpen] = useState(false);
  const [historyMore,setHistoryMore] = useState(false);
  const [historyBusy,setHistoryBusy] = useState(false);
  const [historyError,setHistoryError] = useState('');
  const [loadingConversation,setLoadingConversation] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const workspaceRef = useRef<HTMLElement>(null);
  const [templates,setTemplates] = useState<AnalysisTemplate[]>([]);
  const [templateId,setTemplateId] = useState('');
  const [templateError,setTemplateError] = useState('');
  async function loadTemplates() { try { const data = await api<{items:AnalysisTemplate[]}>('/knowledge/analysis/templates'); if (!Array.isArray(data.items)) throw new Error('模板列表格式异常，请重试。'); setTemplates(data.items); setTemplateError(''); } catch(e) { setTemplateError(errorMessage(e)); } }
  useEffect(()=>{void loadTemplates();},[]);
  const activeTemplate = templates.find(t=>t.id===templateId);
  const [settings, setSettings] = useState<AISettings | null>(null);
  const [settingsError, setSettingsError] = useState('');
  const [question, setQuestion] = useState('');
  const [asOf, setAsOf] = useState(today);
  const consent = settings?.external_permissions?.question === true;
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const requestRef = useRef<AbortController | null>(null);
  useEffect(() => () => requestRef.current?.abort(), []);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [selected, setSelected] = useState<Citation | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const currentProvider = settings?.default_provider;
  const providerName = 'AI';
  const provider = currentProvider ? settings?.providers[currentProvider] : null;
  const ready = (settings?.enabled && provider?.enabled && provider.api_key_configured);
  async function loadSettings() { setSettingsError(''); try { const data = await api<{ settings: { ai: AISettings }; runtime: { sample_workspace?: boolean } }>('/settings'); setSettings(data.settings.ai); } catch (e) { setSettingsError(errorMessage(e)); } }
  useEffect(() => { void loadSettings(); }, []);
  useEffect(() => { if (copied === null) return; const timer = window.setTimeout(() => setCopied(null), 2000); return () => window.clearTimeout(timer); }, [copied]);
  async function openConversation(id:string) {
    setLoadingConversation(true);setHistoryError('');
    try { const data=await api<{id:string;items:Turn[]}>('/knowledge/conversations/'+id); if(!Array.isArray(data.items)) throw new Error('无法读取会话记录'); setTurns(data.items);setConversationId(data.id);setTemplateId(data.items.at(-1)?.templateId || '');setAsOf(data.items.at(-1)?.date || today());setHistoryOpen(false);setQuestion(''); }
    catch(e){setHistoryError(errorMessage(e));} finally{setLoadingConversation(false);}
  }
  async function loadHistory(more=false) {
    setHistoryBusy(true);setHistoryError('');
    try { const data=await api<{items:typeof history;has_more:boolean}>('/knowledge/conversations?offset='+(more?history.length:0)); if(!Array.isArray(data.items)) throw new Error('无法读取历史会话'); setHistory(old=>more?[...old,...data.items]:data.items);setHistoryMore(data.has_more); }
    catch(e){setHistoryError(errorMessage(e));} finally{setHistoryBusy(false);}
  }
  useEffect(()=>{let active=true;
    void (async()=>{try {const data=await api<{items:typeof history;has_more:boolean}>('/knowledge/conversations');if(!active)return;if(!Array.isArray(data.items))throw new Error('无法读取历史会话');setHistory(data.items);setHistoryMore(data.has_more);if(data.items[0]){const detail=await api<{id:string;items:Turn[]}>('/knowledge/conversations/'+data.items[0].id);if(active){setTurns(detail.items);setConversationId(detail.id);setTemplateId(detail.items.at(-1)?.templateId || '');setAsOf(detail.items.at(-1)?.date || today());}}}catch(e){if(active)setHistoryError(errorMessage(e));}finally{if(active)setLoadingConversation(false);}})();
    return()=>{active=false;};
  },[]);
  useEffect(()=>{const node=scrollRef.current;if(node)node.scrollTop=node.scrollHeight;},[turns,progress,busy]);
  useEffect(()=>{const size=()=>{const node=workspaceRef.current;if(node)node.style.setProperty('--qa-top',Math.max(0,node.getBoundingClientRect().top)+'px');};size();window.addEventListener('resize',size);return()=>window.removeEventListener('resize',size);},[]);
  function newConversation(){setConversationId(null);setTurns([]);setQuestion('');setNotice('');setHistoryError('');}
  async function submit(retryIndex?: number) {
    const previous = retryIndex === undefined ? undefined : turns[retryIndex];
    const value = (previous?.question ?? question).trim();
    if (!value || busy || loadingConversation || (!consent || !ready)) return;
    setBusy(true); setNotice('');
    const turn: Turn = previous ? {...previous, answer:undefined, error:undefined, streaming:true} : { templateId:activeTemplate?.id, templateRevision:activeTemplate?.revision, templateName: activeTemplate?.name, question: value, date: asOf, streaming: true };
    const index = turns.length;
    setTurns(existing => [...existing, {...turn,id:undefined,status:undefined}]); setProgress('正在检索法规依据…');
    const controller = new AbortController(); requestRef.current = controller;
    const update = (changes: Partial<Turn>) => setTurns(existing => existing.map((item, i) => i === index ? {...item, ...changes} : item));
    try {
      await streamQuestion({conversation_id:conversationId,question:value,as_of:turn.date,confirm_external:consent,...(turn.templateId?{template_id:turn.templateId,template_revision:turn.templateRevision}:{})}, event => {
        if (event.type === 'saved') { setConversationId(event.conversation_id);update({id:event.message_id,status:'pending'}); }
        else if (event.type === 'status') setProgress(event.message);
        else if (event.type === 'partial' || event.type === 'complete') { update({answer:event.answer,streaming:event.type !== 'complete',status:event.type==='complete'?'complete':'pending'}); setProgress('AI 正在继续生成，已完成结论逐条显示…'); }
      }, controller.signal);
      if (!previous) setQuestion('');
    } catch (e) { update({answer:undefined,error:controller.signal.aborted ? '已停止生成，本次未完成回答已清除。' : errorMessage(e)}); }
    finally { update({streaming:false}); setBusy(false); requestRef.current = null; }
  }

  function answerText(turn: Turn) {
    const sources = turn.answer?.sources || [];
    return `执法辅助分析\n问题：${turn.question}\n适用日期：${turn.date}\n模板：${turn.templateName || '通用法规问答'}\n\n${turn.answer?.answer || ''}\n\n法规依据\n${sources.map((s,i)=>`[${i+1}] ${s.title} · ${s.version} · ${s.article}\n${s.text}\n来源：${s.source}`).join('\n\n')}\n\nAI 辅助归纳，适用性与结论仍需人工核对。`;
  }
  async function copyText(text: string, key: string) {
    try { await navigator.clipboard.writeText(text); setCopied(key); } catch { setNotice('浏览器未允许复制，请手动选择文本复制。'); }
  }
  function exportTurn(turn: Turn, index: number) {
    const url=URL.createObjectURL(new Blob(['\uFEFF'+answerText(turn)],{type:'text/plain;charset=utf-8'}));
    const link=document.createElement('a'); link.href=url;link.download=`执法辅助分析-${turn.date}-${index+1}.txt`;document.body.appendChild(link);link.click();link.remove();window.setTimeout(()=>URL.revokeObjectURL(url),1000);
    setNotice('分析结果已导出为 TXT，包含问题、适用日期和法规引用。');
  }
  return <div className="page-content assistant-page"><PageHeading title="执法智能助手" description="查询法律条款，按模板分析行为性质与处罚依据。" scene="assistant"><button className="btn btn-secondary" onClick={onKnowledge}><BookOpen size={16}/>管理知识库</button></PageHeading>
    <div className="qa-layout"><section className="panel qa-workspace" ref={workspaceRef}><header className="qa-workspace-header"><div className="qa-agent-icon"><Sparkles size={21}/></div><div><strong>境智 · 法规助手</strong><p>检索结果可展开核对原文</p></div><span className="kb-local-badge"><span/>{ready ? providerName : 'AI 未就绪'}</span><div className="qa-history-actions"><button className="text-button" disabled={busy || loadingConversation} onClick={()=>{setHistoryOpen(true);void loadHistory();}}><History size={15}/>历史会话</button><button className="text-button" disabled={busy || loadingConversation} onClick={newConversation}><Plus size={15}/>新会话</button></div></header><div className="qa-scroll-region" ref={scrollRef} role="region" aria-label="助手对话区域" tabIndex={0}>
      <AnalysisTemplates items={templates} value={templateId} onChange={id=>{setTemplateId(id);void loadSettings();}} onSaved={loadTemplates} disabled={busy || loadingConversation} onUseOutline={outline=>{setQuestion(outline.slice(0,1000));document.getElementById('qa-question')?.focus({preventScroll:true});const node=scrollRef.current;if(node)node.scrollTop=node.scrollHeight;}}/>{templateError && <p className="kb-error">{templateError}<button className="text-button" onClick={()=>void loadTemplates()}>重试</button></p>}
      {loadingConversation ? <p className="qa-notice">正在加载已保存会话…</p> : !turns.length ? <div className="qa-welcome"><h2>{activeTemplate ? activeTemplate.name + '分析' : '需要查找哪项规定？'}</h2><p>{activeTemplate ? '描述已掌握的事实与证据，按法律要件逐项核对。' : '输入业务问题，或从以下常见问题开始。'}</p><div className="qa-starters">{(activeTemplate ? [] : starterQuestions).map(q => <button key={q} onClick={() => { setQuestion(q);  document.getElementById('qa-question')?.focus(); }}><MessageSquareText size={16}/><span>{q}</span><ArrowRight size={15}/></button>)}</div></div> : <div className="qa-conversation" aria-live="polite">{turns.map((turn, index) => {
        const sources = turn.answer?.sources || [];
        return <article className="qa-turn" key={index}><div className="qa-question"><span className="qa-you">你</span><div className="qa-user-bubble"><p>{turn.question}</p><small>{turn.date} · {turn.templateName && `${turn.templateName} · `}AI 引用问答</small><div className="qa-message-actions"><button type="button" onClick={()=>void copyText(turn.question,`q-${index}`)} aria-label={`复制第${index+1}个问题`}>{copied===`q-${index}`?<Check size={14}/>:<Copy size={14}/>}复制</button><button type="button" disabled={loadingConversation || busy || !ready || !consent} onClick={()=>void submit(index)} aria-label={`重试第${index+1}个问题`}><RotateCcw size={14}/>重试</button></div></div></div><div className="qa-response"><div className="qa-response-label"><span className="qa-response-icon"><Sparkles size={15}/></span><strong>基于资料的回答</strong>{turn.answer && !turn.error && !turn.streaming && <div className="qa-message-actions"><button type="button" aria-label={`复制第${index+1}个回答`} onClick={()=>void copyText(answerText(turn),`a-${index}`)}>{copied===`a-${index}`?<Check size={14}/>:<Copy size={14}/>}复制</button><button type="button" onClick={()=>exportTurn(turn,index)} aria-label={`导出第${index+1}个回答`}><Download size={14}/>导出</button></div>}</div>
          {turn.error ? <div className="kb-error" role="alert"><CircleAlert size={17}/>{turn.error}</div> : <>{turn.streaming && !turn.answer ? <p className="qa-search-summary">正在生成，已核验引用的结论将逐条显示…</p> : turn.answer?.status === 'answered' ? <><div className="qa-answer">{turn.answer.claims?.length ? turn.answer.claims.map((claim, ci) => <p key={ci}>{claim.text} <span className="qa-inline-citations">{[...new Set(claim.source_ids)].map(id => { const sourceIndex = sources.findIndex(s => s.id === id); return sourceIndex >= 0 && <button key={id} aria-label={`查看依据${sourceIndex + 1}`} onClick={() => setSelected(sources[sourceIndex])}>[{sourceIndex + 1}]</button>; })}</span></p>) : <p>{turn.answer.answer}</p>}</div><p className="qa-reviewed-note"><ShieldCheck size={13}/>AI 辅助归纳，适用性与结论仍需人工核对。</p></> : turn.answer ? <div className="qa-insufficient"><CircleAlert size={20}/><div><strong>当前依据不足</strong><p>{turn.answer.answer}</p></div></div> : <p className="qa-search-summary">{sources.length ? `找到 ${sources.length} 条相关片段。点击阅读完整原文。` : '没有找到当前日期适用的已发布依据。请调整关键词，或补充资料并审核发布。'}</p>}
          {!!sources.length && <details className="qa-sources"><summary className="qa-sources-heading"><BookOpen size={14}/>原文依据 <span>{sources.length}</span></summary>{sources.map((s, si) => <button className="qa-source" key={s.id} onClick={() => setSelected(s)}><span className="kb-citation-index">{si + 1}</span><div><strong>{s.title}</strong><small>{s.version} · {s.article}{s.page ? ` · 第 ${s.page} 页` : ''}</small><p>{s.text}</p></div><ChevronRight size={16}/></button>)}</details>}
          <div className="qa-response-footer"><span>{turn.answer?.provider ? 'AI' : '本地资料检索'}</span><span>适用日期 {turn.date}</span></div></>}
        </div></article>;
      })}</div>}
      {busy && <div className="qa-working" role="status"><LoaderCircle className="spinning" size={17}/>{progress}<button type="button" className="text-button" onClick={() => requestRef.current?.abort()}>停止生成</button></div>}
      <form className="qa-composer" onSubmit={e => { e.preventDefault(); void submit(); }}><div className="qa-composer-toolbar"><span className="kb-local-badge"><Sparkles size={14}/>AI 问答</span><label className="qa-date">适用日期<input type="date" value={asOf} onChange={e => { setAsOf(e.target.value);  }} disabled={busy} required/></label></div>
        {!ready && <div className="qa-config-note"><Settings2 size={15}/><span>{settingsError || '请先配置 API 密钥，并启用 AI 和默认服务商。'}</span><Access permission="admin"><button type="button" onClick={onSettings}>去配置<ArrowRight size={13}/></button></Access></div>}
        <textarea id="qa-question" aria-label="法规问题" placeholder={activeTemplate ? '按核查清单描述事实、时间和已有证据，未知项请注明；不必填写姓名、证件号…' : '描述法规问题，请勿填入姓名、证件号等个人信息…'} value={question} maxLength={1000} onChange={e => { setQuestion(e.target.value);  }} disabled={busy} required rows={3} onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); void submit(); } }}/>
        {settings && !consent && <div className="qa-consent" role="status"><span>请前往系统设置 → AI 服务，勾选“允许法规问答发送至服务商”。非管理员请联系管理员。</span><Access permission="admin"><button type="button" className="text-button" onClick={onSettings}>前往系统设置</button></Access></div>}
        <div className="qa-composer-footer"><span><LockKeyhole size={12}/>人员档案不会自动加入问答<small>{question.length}/1000</small></span><button type="submit" className="btn btn-primary" disabled={loadingConversation || busy || !question.trim() || (!ready || !consent)}>{busy ? <LoaderCircle className="spinning" size={16}/> : <Send size={15}/>} {activeTemplate ? '开始分析' : '生成回答'}</button></div>
      </form>{notice && <p role="status" className="qa-notice">{notice}</p>}<div className="qa-session-note"><span>{busy ? '问题已保存，回答生成完成后自动保存。' : conversationId ? '会话已保存至本地数据库 · 仅当前账号可查看' : '发送后自动保存至数据库，可从历史会话重新打开。'}</span></div>{historyError && <p className="kb-error" role="alert">{historyError}<button className="text-button" onClick={()=>{setHistoryOpen(true);void loadHistory();}}>重新加载</button></p>}</div>
    </section><aside className="qa-sidebar"><section className="panel qa-side-card"><span className="qa-side-number">办理参考</span><h3>先陈述事实，再核对依据</h3><ol className="qa-guidance"><li><strong>选择分析场景</strong><p>通用法规问答，或选用内置分析模板。</p></li><li><strong>说明事实与证据</strong><p>补充时间、行为、许可情况；未知项明确注明。</p></li><li><strong>核对适用条款</strong><p>展开原文依据，检查日期、条件与待核实事项。</p></li></ol><button className="text-button" onClick={onKnowledge}>查看知识库<ArrowRight size={14}/></button></section><section className="qa-side-tips"><ShieldCheck size={20}/><h3>辅助分析 · 人工核对</h3><p>处罚与裁量依据保留原文。AI 会发送本次填写的内容及获准使用的法规，不自动读取人员档案。</p><p>支持复制问题、重试回答，以及导出包含引用的分析结果。</p></section></aside></div>
    {historyOpen && <Dialog title="历史会话" onClose={()=>setHistoryOpen(false)}><div className="qa-history-list">{historyError && <p className="kb-error">{historyError}</p>}{!history.length && !historyBusy && <p>暂无已保存会话。发送问题后会自动保存在这里。</p>}{history.map(item=><button key={item.id} disabled={loadingConversation} onClick={()=>void openConversation(item.id)}><MessageSquareText size={17}/><span><strong>{item.title}</strong><small>{new Date(item.updated_at).toLocaleString('zh-CN')}</small></span><ChevronRight size={16}/></button>)}{historyBusy && <p>正在加载…</p>}{historyMore && <button disabled={historyBusy} onClick={()=>void loadHistory(true)}>加载更多</button>}</div></Dialog>}
    {selected && (selected.template_source ? <Dialog title={selected.title} onClose={()=>setSelected(null)}><h3>{selected.article}</h3><p className="analysis-source-text">{selected.text}</p><p>适用起始日期：{selected.effective_from} · {selected.version}</p><a href={selected.source} target="_blank" rel="noreferrer">查看官方来源</a></Dialog> : <DocumentViewer id={selected.document_id} highlight={selected.id} onClose={() => setSelected(null)}/>)}
  </div>;
}
