import { useViewState } from '../hooks/useWorkspaceView';
import { Maintenance } from '../components/Maintenance';
import { PageHeading } from '../components/PageHeading';
import { confirmAction } from '../lib/confirmation';
import { Switch as Toggle } from '../components/ui/switch';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/ui/tabs';
import { useMediaQuery } from '../hooks/useMediaQuery';
import { motion } from 'motion/react';
import { Choice } from '../components/Choice';
import { usePageDirty } from '../hooks/usePageDirty';
import { useEffect, useRef, useState } from 'react'
import {
  ArrowRight, Check, CheckCircle2, ChevronRight, CircleAlert, Database,
  Eye, EyeOff, FlaskConical, KeyRound, LoaderCircle, LockKeyhole,
  RefreshCw, Save, Server, Settings2, ShieldCheck,
  Sparkles, Unplug, X,
} from 'lucide-react'

type ProviderId = 'siliconflow' | 'deepseek'
type Section = 'general' | 'environment' | 'ai' | 'data'
type ProviderSettings = {
  enabled: boolean
  base_url: string
  chat_model: string
  asr_model?: string
  api_key_configured: boolean
}
type PostgresSettings = {
  host: string
  port: number
  database: string
  username: string
  password_configured: boolean
}
type AppSettings = {
  general: { system_name: string; organization: string }
  environment: { mode: 'test' | 'production'; postgres: PostgresSettings }
  ai: { enabled: boolean; default_provider: ProviderId; providers: Record<ProviderId, ProviderSettings> }
}
type Runtime = { local_processing?: boolean; active_environment: string; database_engine: string; pending_restart: boolean }
type SettingsResponse = { settings: AppSettings; runtime: Runtime }
type TestResponse = { ok: boolean; message: string; models?: string[]; latency_ms?: number }
type TestState = { status: 'loading' | 'success' | 'error'; message: string; fingerprint: string; latency?: number; models?: string[] }
type Notice = { kind: 'success' | 'error'; text: string }

const providers: { id: ProviderId; name: string; description: string; url: string; monogram: string }[] = [
  { id: 'siliconflow', name: '硅基流动', description: '平台默认 AI 服务', url: 'https://api.siliconflow.cn/v1', monogram: 'S' },
  { id: 'deepseek', name: 'DeepSeek', description: '可选对话服务', url: 'https://api.deepseek.com', monogram: 'D' },
]

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(url, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  } catch {
    throw new Error('无法连接本地服务。请检查后端是否启动，然后重试。')
  }
  let body: unknown
  try { body = await response.json() } catch { throw new Error(`服务响应无法读取（HTTP ${response.status}），请检查后端运行状态。`) }
  if (!response.ok) {
    const detail = (body as { detail?: unknown })?.detail
    const message = typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.map((item: { msg?: string }) => item.msg || '配置格式不正确').join('；') : `请求失败（HTTP ${response.status}），请稍后重试。`
    throw new Error(message)
  }
  return body as T
}

function Switch({ checked, onChange, label, disabled = false }: { checked: boolean; onChange: (value: boolean) => void; label: string; disabled?: boolean }) {
  return <Toggle checked={checked} onCheckedChange={onChange} aria-label={label} disabled={disabled}/>
}

function TestFeedback({ state, fingerprint }: { state?: TestState; fingerprint: string }) {
  if (!state || state.fingerprint !== fingerprint) return null
  return <div className={`settings-test-feedback ${state.status}`} role={state.status === 'error' ? 'alert' : 'status'}>
    {state.status === 'loading' ? <LoaderCircle size={16} className="settings-spin" /> : state.status === 'success' ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}
    <span>{state.message}</span>{state.latency !== undefined && <small>{Math.round(state.latency)} ms</small>}
  </div>
}

export function Settings({ onSaved }: { onSaved?: () => void }) {
  const [saved, setSaved] = useState<AppSettings | null>(null)
  const [draft, setDraft] = useState<AppSettings | null>(null)
  const [runtime, setRuntime] = useState<Runtime | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [reload, setReload] = useState(0)
  const [saving, setSaving] = useState(false)
  const [section, setSection] = useViewState<Section>('settings.section', 'general')
  const mobile = useMediaQuery('(max-width: 700px)')
  const [notice, setNotice] = useState<Notice | null>(null)
  const [keys, setKeys] = useState<Record<ProviderId, string>>({ siliconflow: '', deepseek: '' })
  const [clearKeys, setClearKeys] = useState<Record<ProviderId, boolean>>({ siliconflow: false, deepseek: false })
  const [visibleKeys, setVisibleKeys] = useState<Record<ProviderId, boolean>>({ siliconflow: false, deepseek: false })
  const [password, setPassword] = useState('')
  const [clearPassword, setClearPassword] = useState(false)
  const [visiblePassword, setVisiblePassword] = useState(false)
  const [databaseTest, setDatabaseTest] = useState<TestState>()
  const [aiTests, setAiTests] = useState<Partial<Record<ProviderId, TestState>>>({})
  const noticeRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError('')
    request<SettingsResponse>('/api/settings').then(result => {
      if (cancelled) return
      if (!result.settings?.environment?.postgres || !result.settings?.ai?.providers?.siliconflow || !result.settings?.ai?.providers?.deepseek || !result.runtime) throw new Error('配置数据不完整，请检查服务版本。')
      setSaved(result.settings)
      setDraft(structuredClone(result.settings))
      setRuntime(result.runtime)
    }).catch(error => { if (!cancelled) setLoadError(error instanceof Error ? error.message : '读取配置失败。') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [reload])

  const dirty = Boolean(draft && saved && (JSON.stringify(draft) !== JSON.stringify(saved) || password || clearPassword || keys.siliconflow || keys.deepseek || clearKeys.siliconflow || clearKeys.deepseek))

  usePageDirty(dirty || saving)

  const showNotice = (next: Notice) => { setNotice(next); window.setTimeout(() => noticeRef.current?.focus(), 0) }

  const updateProvider = (id: ProviderId, patch: Partial<ProviderSettings>) => {
    setDraft(current => current ? { ...current, ai: { ...current.ai, providers: { ...current.ai.providers, [id]: { ...current.ai.providers[id], ...patch } } } } : current)
    setNotice(null)
  }
  const updatePostgres = (patch: Partial<PostgresSettings>) => {
    setDraft(current => current ? { ...current, environment: { ...current.environment, postgres: { ...current.environment.postgres, ...patch } } } : current)
    setNotice(null)
  }

  const postgresPayload = () => draft ? {
    host: draft.environment.postgres.host.trim(), port: draft.environment.postgres.port,
    database: draft.environment.postgres.database.trim(), username: draft.environment.postgres.username.trim(),
    ...(password ? { password } : {}), ...(clearPassword ? { clear_password: true } : {}),
  } : {}
  const databaseFingerprint = JSON.stringify(postgresPayload())
  const providerFingerprint = (id: ProviderId) => JSON.stringify({ model: draft?.ai.providers[id].chat_model, key: keys[id], clear: clearKeys[id] })
  const productionChanged = Boolean(draft?.environment.mode === 'production' && (saved?.environment.mode !== 'production' || JSON.stringify(draft.environment.postgres) !== JSON.stringify(saved?.environment.postgres) || password || clearPassword))
  const databaseVerified = databaseTest?.status === 'success' && databaseTest.fingerprint === databaseFingerprint
  const needsDatabaseTest = productionChanged && !databaseVerified

  async function testDatabase() {
    const fingerprint = databaseFingerprint
    setDatabaseTest({ status: 'loading', message: '正在检查数据库连接…', fingerprint })
    try {
      const result = await request<TestResponse>('/api/settings/test-database', { method: 'POST', body: JSON.stringify(postgresPayload()) })
      setDatabaseTest({ status: result.ok ? 'success' : 'error', message: result.message || (result.ok ? '数据库连接正常。' : '数据库连接失败。'), fingerprint, latency: result.latency_ms })
    } catch (error) { setDatabaseTest({ status: 'error', message: error instanceof Error ? error.message : '数据库连接测试失败。', fingerprint }) }
  }

  async function testAi(id: ProviderId) {
    if (!draft) return
    const fingerprint = providerFingerprint(id)
    setAiTests(current => ({ ...current, [id]: { status: 'loading', message: '正在验证凭证与模型…', fingerprint } }))
    try {
      const result = await request<TestResponse>('/api/settings/test-ai', {
        method: 'POST', body: JSON.stringify({ provider: id, chat_model: draft.ai.providers[id].chat_model.trim(), ...(keys[id].trim() ? { api_key: keys[id].trim() } : {}) }),
      })
      setAiTests(current => ({ ...current, [id]: { status: result.ok ? 'success' : 'error', message: result.message || (result.ok ? '凭证与模型验证通过。' : '连接验证失败。'), fingerprint, latency: result.latency_ms, models: result.models } }))
    } catch (error) { setAiTests(current => ({ ...current, [id]: { status: 'error', message: error instanceof Error ? error.message : '连接验证失败。', fingerprint } })) }
  }

  function resetSecrets() {
    setKeys({ siliconflow: '', deepseek: '' })
    setClearKeys({ siliconflow: false, deepseek: false })
    setVisibleKeys({ siliconflow: false, deepseek: false })
    setPassword(''); setClearPassword(false); setVisiblePassword(false)
  }

  async function discard() {
    if (!saved || !await confirmAction('恢复到上次保存的配置？本次未保存的设置和新填写的凭证会被清除，已保存配置不变。', { title: '放弃本次配置修改？', confirmLabel: '放弃修改', cancelLabel: '继续编辑' })) return
    setDraft(structuredClone(saved)); resetSecrets(); setDatabaseTest(undefined); setAiTests({}); setNotice(null)
  }

  async function save() {
    if (!draft || !dirty || saving) return
    if (!draft.general.system_name.trim()) { setSection('general'); showNotice({ kind: 'error', text: '请填写系统名称后再保存。' }); return }
    if (!draft.general.organization.trim()) { setSection('general'); showNotice({ kind: 'error', text: '请填写所属组织后再保存。' }); return }
    if (providers.some(provider => !draft.ai.providers[provider.id].chat_model.trim())) { setSection('ai'); showNotice({ kind: 'error', text: '请为两个服务商分别填写对话模型，未启用的服务商也需要保留有效配置。' }); return }
    if (!draft.ai.providers.siliconflow.asr_model?.trim()) { setSection('ai'); showNotice({ kind: 'error', text: '请填写硅基流动的语音转写模型。' }); return }
    if (draft.ai.enabled && !draft.ai.providers[draft.ai.default_provider].enabled) { setSection('ai'); showNotice({ kind: 'error', text: '默认 AI 服务商尚未启用，请先启用该服务商或切换默认服务商。' }); return }
    if (needsDatabaseTest) { setSection('environment'); showNotice({ kind: 'error', text: '正式数据库配置已变更，请先测试连接，成功后再保存。' }); return }
    setSaving(true); setNotice(null)
    try {
      const payload = {
        general: { system_name: draft.general.system_name.trim(), organization: draft.general.organization.trim() },
        environment: { mode: draft.environment.mode, postgres: postgresPayload() },
        ai: {
          enabled: draft.ai.enabled, default_provider: draft.ai.default_provider,
          providers: Object.fromEntries(providers.map(provider => [provider.id, {
            enabled: draft.ai.providers[provider.id].enabled, base_url: provider.url, chat_model: draft.ai.providers[provider.id].chat_model.trim(),
            ...(provider.id === 'siliconflow' ? { asr_model: draft.ai.providers.siliconflow.asr_model?.trim() } : {}),
            ...(keys[provider.id].trim() ? { api_key: keys[provider.id].trim() } : {}),
            ...(clearKeys[provider.id] ? { clear_api_key: true } : {}),
          }])),
        },
      }
      const result = await request<SettingsResponse>('/api/settings', { method: 'PUT', body: JSON.stringify(payload) })
      if (!result.settings || !result.runtime) throw new Error('保存响应不完整，请重新读取配置确认状态。')
      setSaved(result.settings); setDraft(structuredClone(result.settings)); setRuntime(result.runtime)
      resetSecrets(); setDatabaseTest(undefined); setAiTests({})
      showNotice({ kind: 'success', text: result.runtime.pending_restart ? '配置已保存。环境配置将在后端服务重启后生效，当前数据不会自动迁移。' : '配置已保存，基础信息与 AI 设置已生效。' })
      onSaved?.()
    } catch (error) { showNotice({ kind: 'error', text: error instanceof Error ? error.message : '配置保存失败，请重试。' }) }
    finally { setSaving(false) }
  }

  if (loading) return <div className="settings-page"><h1 className="page-title">系统设置</h1><div className="panel settings-state-panel" role="status"><LoaderCircle size={28} className="settings-spin" /><h2>正在读取本地配置</h2><p className="muted">连接服务后，将显示实际运行环境和 AI 配置。</p></div></div>
  if (loadError || !draft || !runtime) return <div className="settings-page"><h1 className="page-title">系统设置</h1><div className="panel settings-state-panel settings-load-error" role="alert"><Unplug size={30} /><h2>暂时无法读取配置</h2><p>{loadError || '服务没有返回完整配置。'}</p><button className="btn btn-primary" onClick={() => setReload(value => value + 1)}><RefreshCw size={16} />重新连接</button></div></div>

  return <div className="settings-page">
    <PageHeading title="系统设置" description="管理运行环境、AI 服务和本地数据。" scene="settings">
      <div className={`settings-save-state ${dirty ? 'is-dirty' : ''}`}><span />{dirty ? '有未保存的修改' : '配置已保存'}</div>
    </PageHeading>
    <div className="settings-runtime-strip">
      <span className="settings-runtime-icon"><Server size={18} /></span>
      <div><span className="settings-runtime-label">当前运行</span><strong className="environment-pill"><i/>{runtime.local_processing ? '业务工作空间' : runtime.active_environment === 'production' ? '正式版' : '测试版'}</strong><span className="settings-runtime-divider">/</span><span>{runtime.database_engine}</span></div>
      <div className="settings-runtime-status">{runtime.pending_restart ? <><span className="settings-status-dot pending" />配置已更新，等待重启</> : <><span className="settings-status-dot" />本地服务已连接</>}</div>
    </div>

    {notice && <div ref={noticeRef} tabIndex={-1} className={`settings-notice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.kind === 'success' ? <CheckCircle2 size={19} /> : <CircleAlert size={19} />}<span>{notice.text}</span><button type="button" aria-label="关闭提示" onClick={() => setNotice(null)}><X size={17} /></button></div>}

    <Tabs value={section} onValueChange={value => setSection(value as Section)} orientation={mobile ? 'horizontal' : 'vertical'} asChild><div className="settings-layout">
      <aside className="settings-nav-column">
        <TabsList asChild><nav className="settings-section-nav" aria-label="设置分类">
          {([
            { id: 'general' as const, icon: Settings2, title: '基础信息', subtitle: '名称与所属组织' },
            { id: 'environment' as const, icon: Database, title: '运行环境', subtitle: runtime.local_processing ? '本地业务服务' : `${runtime.active_environment === 'production' ? '正式环境' : '测试环境'} · ${runtime.database_engine}` },
            { id: 'ai' as const, icon: Sparkles, title: 'AI 服务', subtitle: runtime.local_processing ? '资料处理服务已就绪' : saved?.ai.enabled ? `已启用 · ${saved.ai.default_provider === 'deepseek' ? 'DeepSeek' : '硅基流动'}` : '业务调用已关闭' },
            { id: 'data' as const, icon: Database, title: '数据与维护', subtitle: runtime.local_processing ? '资料迁移与备份' : '演示资料、迁移与备份' },
          ]).map(item => <TabsTrigger value={item.id} key={item.id} asChild><button type="button" className={section === item.id ? 'is-active' : ''} onClick={() => setSection(item.id)}><item.icon size={20} /><span><strong>{item.title}</strong><small>{item.subtitle}</small></span><ChevronRight size={16} className="settings-nav-chevron" /></button></TabsTrigger>)}
        </nav></TabsList>

      </aside>

      <motion.div className="settings-main" key="settings-content" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
        {section === 'general' && <TabsContent value="general" asChild><section className="panel settings-card">
          <div className="settings-card-heading"><div><h2>基础信息</h2></div></div>
          <div className="settings-card-body"><div className="settings-field-grid">
            <label className="field settings-field"><span>系统名称 <b>*</b></span><input className="input" value={draft.general.system_name} maxLength={80} disabled={saving} placeholder="请输入系统名称" onChange={event => { setDraft({ ...draft, general: { ...draft.general, system_name: event.target.value } }); setNotice(null) }} /><small>显示在侧边导航与工作空间标题中。</small></label>
            <label className="field settings-field"><span>所属组织 <b>*</b></span><input className="input" value={draft.general.organization} maxLength={100} disabled={saving} placeholder="例如：出入境业务中心" onChange={event => { setDraft({ ...draft, general: { ...draft.general, organization: event.target.value } }); setNotice(null) }} /><small>用于标识当前部署单位。</small></label>
          </div>
          <div className="settings-inline-note"><LockKeyhole size={17} /><p>当前未启用登录，访问地址后直接进入工作台。</p></div></div>
        </section></TabsContent>}

        {section === 'environment' && runtime.local_processing && <section className="panel settings-card"><div className="settings-card-heading"><h2>运行环境</h2><span className="settings-small-badge enabled">已连接</span></div><div className="settings-card-body"><div className="settings-default-provider"><div><h3>本地业务数据库</h3><p>人员、法规、录音、文书与办理记录统一留存。</p></div><Database size={22}/></div><p className="settings-caption">SQLite · macOS / Linux · 当前运行配置由服务端管理</p></div></section>}
        {section === 'environment' && !runtime.local_processing && <TabsContent value="environment" asChild><section className="panel settings-card">
          <div className="settings-card-heading"><div><h2>运行环境</h2></div></div>
          <div className="settings-card-body">
            <div className="settings-label-line"><span>目标运行环境</span><span className="settings-caption">保存配置后，重启后端生效</span></div>
            <div className="settings-environment-grid" role="radiogroup" aria-label="目标运行环境">
              {([{ mode: 'test' as const, title: '测试环境', engine: 'SQLite', description: '轻量启动 · 本地单文件数据库', icon: FlaskConical }, { mode: 'production' as const, title: '正式环境', engine: 'PostgreSQL', description: '独立数据库 · 面向业务部署', icon: Server }]).map(item => <button type="button" role="radio" aria-checked={draft.environment.mode === item.mode} key={item.mode} disabled={saving} className={`settings-environment-option ${draft.environment.mode === item.mode ? 'is-selected' : ''}`} onClick={() => { setDraft({ ...draft, environment: { ...draft.environment, mode: item.mode } }); setNotice(null) }}><div className="settings-environment-top"><item.icon size={22} /><span className="settings-radio">{draft.environment.mode === item.mode && <Check size={12} />}</span></div><strong>{item.title}<span>{item.engine}</span></strong><p>{item.description}</p></button>)}
            </div>
            <div className="settings-environment-explanation"><CircleAlert size={18} /><p>切换环境不会复制、合并或覆盖现有数据。当前仍运行在<strong>{runtime.active_environment === 'production' ? '正式环境' : '测试环境'}</strong>，保存后的数据库变更需要重启后端服务。</p></div>

            {draft.environment.mode === 'test' ? <p className="settings-caption settings-sqlite-detail">SQLite 使用本地文件，无需连接凭证。数据目录由服务端部署配置指定。</p> : <>
              <div className="settings-subheading"><h3>PostgreSQL 连接</h3><span><LockKeyhole size={13} />凭证仅保存在服务端</span></div>
              <div className="settings-field-grid settings-database-fields">
                <label className="field settings-field settings-host-field"><span>数据库地址</span><input className="input" value={draft.environment.postgres.host} disabled={saving} placeholder="127.0.0.1" autoComplete="off" onChange={event => updatePostgres({ host: event.target.value })} /></label>
                <label className="field settings-field settings-port-field"><span>端口</span><input className="input" type="number" min={1} max={65535} value={draft.environment.postgres.port || ''} disabled={saving} placeholder="5432" onChange={event => updatePostgres({ port: Number(event.target.value) })} /></label>
                <label className="field settings-field"><span>数据库名称</span><input className="input" value={draft.environment.postgres.database} disabled={saving} autoComplete="off" onChange={event => updatePostgres({ database: event.target.value })} /></label>
                <label className="field settings-field"><span>用户名</span><input className="input" value={draft.environment.postgres.username} disabled={saving} autoComplete="off" onChange={event => updatePostgres({ username: event.target.value })} /></label>
                <div className="field settings-field settings-full-width"><div className="settings-label-line"><label htmlFor="database-password">数据库密码</label><span className={`settings-secret-state ${draft.environment.postgres.password_configured && !clearPassword ? 'configured' : ''}`}>{clearPassword ? '保存后清空' : draft.environment.postgres.password_configured ? '已安全保存 · 不回显' : '尚未配置'}</span></div><div className="settings-secret-input"><input className="input" id="database-password" type={visiblePassword ? 'text' : 'password'} value={password} disabled={saving || clearPassword} autoComplete="new-password" placeholder={draft.environment.postgres.password_configured ? '留空保留当前密码，输入以更换' : '请输入数据库密码'} onChange={event => { setPassword(event.target.value); setNotice(null) }} /><button type="button" disabled={saving || clearPassword || !password} aria-label={visiblePassword ? '隐藏新密码' : '显示新密码'} onClick={() => setVisiblePassword(!visiblePassword)}>{visiblePassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></div>{draft.environment.postgres.password_configured && <label className="settings-checkbox"><input type="checkbox" checked={clearPassword} disabled={saving} onChange={event => { setClearPassword(event.target.checked); setPassword(''); setNotice(null) }} />保存时清空已保存的密码</label>}</div>
              </div>
              <div className="settings-test-row"><button type="button" className="btn btn-secondary" disabled={saving || databaseTest?.status === 'loading' || !draft.environment.postgres.host.trim() || !draft.environment.postgres.database.trim() || !draft.environment.postgres.username.trim() || !draft.environment.postgres.port} onClick={testDatabase}>{databaseTest?.status === 'loading' ? <LoaderCircle size={16} className="settings-spin" /> : <Unplug size={16} />}测试数据库连接</button><span className="settings-caption">仅检查连接，不创建或修改数据表。</span></div>
              <TestFeedback state={databaseTest} fingerprint={databaseFingerprint} />
            </>}
          </div>
        </section></TabsContent>}

        {section === 'ai' && runtime.local_processing && <section className="panel settings-card"><div className="settings-card-heading"><div><h2>智能处理服务</h2><p>当前工作空间的资料处理能力</p></div><span className="settings-small-badge enabled">已就绪</span></div><div className="settings-card-body">{[{name:'法规资料整理', detail:'检索已发布法规，核对适用版本，保留原文引用。'}, {name:'录音资料整理',detail:'使用配套录音资料，整理角色分段与转写正文。'}, {name:'文字要素整理',detail:'从已保存正文提取要素、整理摘要并关联来源。'}].map(item=><div className="settings-default-provider" key={item.name}><div><h3>{item.name}</h3><p>{item.detail}</p></div><ShieldCheck size={20}/></div>)}<p className="settings-caption">资料在当前工作空间内处理；服务连接方式由启动配置管理。</p></div></section>}
        {section === 'ai' && !runtime.local_processing && <TabsContent value="ai" asChild><section className="settings-ai-section">
          <div className="panel settings-card"><div className="settings-card-heading"><div><h2>AI 服务配置</h2></div></div>
            <div className="settings-card-body"><div className={`settings-ai-master ${draft.ai.enabled ? 'enabled' : ''}`}><div><h3>启用 AI 功能<span className={`settings-small-badge ${draft.ai.enabled ? 'enabled' : ''}`}>{draft.ai.enabled ? '已开启' : '已关闭'}</span></h3><p>{draft.ai.enabled ? '法规问答、语音转写和摘要可使用已配置的服务。' : '关闭时保留所有配置，暂停业务 AI 调用。'}</p></div><Switch checked={draft.ai.enabled} disabled={saving} label="启用 AI 功能" onChange={enabled => { setDraft({ ...draft, ai: { ...draft.ai, enabled } }); setNotice(null) }} /></div>
              <div className="settings-default-provider"><div><h3>默认对话服务商</h3><p>法规问答和笔录摘要使用该服务；语音转写固定使用硅基流动。</p></div><label className="settings-provider-select"><span className="settings-visually-hidden">默认对话服务商</span><Choice className="select" disabled={saving} value={draft.ai.default_provider} onChange={event => { setDraft({ ...draft, ai: { ...draft.ai, default_provider: event.target.value as ProviderId } }); setNotice(null) }}><option value="siliconflow">硅基流动</option><option value="deepseek">DeepSeek</option></Choice></label></div>
              {!draft.ai.providers[draft.ai.default_provider].enabled && <div className="settings-inline-warning"><CircleAlert size={16} /><span>默认服务商当前未启用。开启 AI 前，请先启用下方对应服务商。</span></div>}
            </div>
          </div>

          <div className="settings-provider-grid">{providers.map(provider => {
            const config = draft.ai.providers[provider.id]
            const fingerprint = providerFingerprint(provider.id)
            const test = aiTests[provider.id]
            const keyAvailable = Boolean(keys[provider.id].trim() || (config.api_key_configured && !clearKeys[provider.id]))
            return <article className={`panel settings-provider-card ${config.enabled ? 'is-enabled' : ''}`} key={provider.id}>
              <div className="settings-provider-header"><div><h3>{provider.name}{draft.ai.default_provider === provider.id && <span className="settings-default-tag">默认</span>}</h3><p>{provider.description}</p></div><Switch checked={config.enabled} disabled={saving} label={`启用${provider.name}`} onChange={enabled => updateProvider(provider.id, { enabled })} /></div>
              <div className="settings-provider-fields">
                <label className="field settings-field"><span>服务地址 <LockKeyhole size={12} /></span><input className="input settings-readonly" value={provider.url} readOnly aria-label={`${provider.name}固定服务地址`} /><small>固定官方端点，由后端统一访问。</small></label>
                <label className="field settings-field"><span>对话模型</span><input className="input" value={config.chat_model} disabled={saving} placeholder="填写服务商提供的模型 ID" autoComplete="off" spellCheck={false} onChange={event => updateProvider(provider.id, { chat_model: event.target.value })} /><small>模型权限与可用性以当前账户为准。</small>{test?.models && <details className="nia-model-options"><summary>选择已检测模型 · {test.models.length}</summary><div>{test.models.map(model => <button type="button" key={model} disabled={saving} onClick={() => updateProvider(provider.id, { chat_model: model })}>{model}</button>)}</div></details>}</label>
                {provider.id === 'siliconflow' && <label className="field settings-field"><span>中文语音转写模型</span><input className="input" value={config.asr_model || ''} disabled={saving} maxLength={200} placeholder="FunAudioLLM/SenseVoiceSmall" autoComplete="off" spellCheck={false} onChange={event => updateProvider(provider.id, { asr_model: event.target.value })} /><small>录音转写独立使用此模型；对话模型用于摘要与法规问答。模型列表测试不代表音频转写已实测。</small></label>}
                {provider.id === 'deepseek' && <p className="settings-caption">用于文字摘要与要素整理。录音转写使用硅基流动的语音接口。</p>}
                <div className="field settings-field"><div className="settings-label-line"><label htmlFor={`key-${provider.id}`}>API Key</label><span className={`settings-secret-state ${config.api_key_configured && !clearKeys[provider.id] ? 'configured' : ''}`}><KeyRound size={12} />{clearKeys[provider.id] ? '保存后清空' : config.api_key_configured ? '已配置 · 不回显' : '未配置'}</span></div><div className="settings-secret-input"><input className="input" id={`key-${provider.id}`} type={visibleKeys[provider.id] ? 'text' : 'password'} value={keys[provider.id]} disabled={saving || clearKeys[provider.id]} autoComplete="new-password" spellCheck={false} placeholder={config.api_key_configured ? '留空保留，输入以更换密钥' : '粘贴此服务商的 API Key'} onChange={event => { setKeys({ ...keys, [provider.id]: event.target.value }); setNotice(null) }} /><button type="button" disabled={saving || clearKeys[provider.id] || !keys[provider.id]} aria-label={visibleKeys[provider.id] ? `隐藏${provider.name}新密钥` : `显示${provider.name}新密钥`} onClick={() => setVisibleKeys({ ...visibleKeys, [provider.id]: !visibleKeys[provider.id] })}>{visibleKeys[provider.id] ? <EyeOff size={17} /> : <Eye size={17} />}</button></div>{config.api_key_configured && <label className="settings-checkbox"><input type="checkbox" checked={clearKeys[provider.id]} disabled={saving} onChange={event => { setClearKeys({ ...clearKeys, [provider.id]: event.target.checked }); setKeys({ ...keys, [provider.id]: '' }); setNotice(null) }} />保存时清空此密钥</label>}</div>
              </div>
              <div className="settings-provider-footer"><button type="button" className="btn btn-secondary" disabled={saving || test?.status === 'loading' || !keyAvailable || !config.chat_model.trim()} onClick={() => testAi(provider.id)}>{test?.status === 'loading' ? <LoaderCircle size={15} className="settings-spin" /> : <Unplug size={15} />}测试连接</button><span>{clearKeys[provider.id] ? '清空密钥后无法调用' : config.enabled ? '服务商已启用' : '服务商未启用'}</span></div>
              <TestFeedback state={test} fingerprint={fingerprint} />
            </article>
          })}</div>
          <div className="settings-service-note"><ShieldCheck size={20} /><div><strong>连接测试仅验证凭证与模型列表</strong><p>测试不会发送业务资料，也不会自动保存配置。后续 AI 业务调用会将必要文本或音频发送至对应服务商；原始资料与处理结果仍保存在本地。</p><p>密钥不回显、不写入浏览器存储，保存后立即清除本页输入。</p></div></div>
        </section></TabsContent>}
        <TabsContent value="data" forceMount asChild><div hidden={section !== 'data'}><Maintenance localProcessing={runtime.local_processing}/></div></TabsContent>
      </motion.div>
    </div></Tabs>

    {(dirty || saving) && <div className="settings-action-bar"><div className="settings-action-message">{saving ? <LoaderCircle size={17} className="settings-spin" /> : dirty ? <span className="settings-unsaved-dot" /> : <CheckCircle2 size={17} />}<span>{saving ? '正在保存配置…' : dirty ? needsDatabaseTest ? '正式库配置已修改，请先验证连接' : '修改仅在保存后生效' : '所有配置已同步'}</span></div><div><button type="button" className="btn btn-ghost" disabled={!dirty || saving} onClick={discard}>撤销修改</button><button type="button" className="btn btn-primary settings-save-button" disabled={!dirty || saving} onClick={save}>{saving ? <LoaderCircle size={16} className="settings-spin" /> : <Save size={16} />}保存配置{!saving && <ArrowRight size={15} />}</button></div></div>}
  </div>
}
