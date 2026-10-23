import { useEffect, useId, useRef, useState } from 'react';
import type { PointerEvent, ReactNode } from 'react';
import { Box, Expand, Hand, Layers3, Pause, Play, RotateCcw } from 'lucide-react';
import { useSpatialPreferences } from './spatial/SpatialExperience';
import type { SceneOptions, SpatialController, SpatialVariant } from './spatial/types';

const content: Record<SpatialVariant, { tag: string; title: ReactNode; description: string; details: string[]; code: string }> = {
  dashboard: { tag: '境智 · 智慧出入境', title: '业务办理概览', description: '查看登记资料与当前办理进度。', details: ['知识有据', '业务有序', '资料留存'], code: 'CONNECTED WORKSPACE' },
  assistant: { tag: '境智 · 法规助手', title: '法规检索与引用', description: '从已审核原文出发，查找条款，再理解问题。', details: ['本地检索', '来源可查', '按需 AI'], code: 'KNOWLEDGE COPILOT' },
  knowledge: { tag: '法规知识中心', title: '资料与法规版本', description: '版本、日期与来源一起留存，让引用回到依据本身。', details: ['本地原件', '条款提取', '人工审核'], code: 'SOURCES & CONNECTIONS' },
  people: { tag: '人员事实档案', title: '人员与停留记录', description: '归集身份、证件与停留事实，串联有来源的业务记录。', details: ['独立建档', '事实核验', '本地留存'], code: 'PERSONNEL ARCHIVES' },
  alerts: { tag: '期限核查工作台', title: '期限与核查进度', description: '临近期限定期提醒，信息缺口交由人工核查。预警不代表违法认定。', details: ['本地规则', '处置留痕', '事实补充'], code: 'OBSERVE · VERIFY · RECORD' },
  records: { tag: '询问笔录工作室', title: '录音与笔录', description: '原音留存，转写分离，人工校对，有据成文。', details: ['本地音频', '修订留痕', '按需 AI'], code: 'CAPTURE · REVIEW · PRESERVE' },
  documents: { tag: '文书制作中心', title: '模板与文书版本', description: '保留每次制作依据，让填写、核对与留存自然衔接。', details: ['选择模板', '人工核对', '本地导出'], code: 'EVERY DETAIL MATTERS' },
  profiles: { tag: '人员事实画像', title: '人员事实关联', description: '汇集有来源的事实与已核验的关系，让每一条关联都可回溯。', details: ['事实来源', '人工核验', '关系脉络'], code: 'CONTEXT & CONNECTIONS' },
  analytics: { tag: '本地业务统计', title: '业务统计概览', description: '从当前登记资料出发，按清晰口径观察数量、变化与办理进度。', details: ['已登记数据', '可追溯口径', '本地报表'], code: 'PATTERNS & PERSPECTIVES' },
  appearance: { tag: '工作空间外观', title: '主题效果预览', description: '让色彩、光线与空间，随你的工作节奏自然变化。', details: ['五套主题', '明暗模式', '动效偏好'], code: 'YOUR SPACE, YOUR STYLE' },
  settings: { tag: '工作空间控制中心', title: '运行环境与服务', description: '环境、数据与智能服务，在一个本地工作空间中有序运行。', details: ['环境隔离', '服务配置', '本地优先'], code: 'CONFIGURE YOUR WORKSPACE' },
};

export function SpatialHero({ variant, action, compact = false, title, description, details }: { variant: SpatialVariant; action?: ReactNode; compact?: boolean; title?: ReactNode; description?: string; details?: string[] }) {
  const info = content[variant];
  const preferences = useSpatialPreferences();
  const [paused, setPaused] = useState(false), [expanded, setExpanded] = useState(variant === 'dashboard');
  const [state, setState] = useState<'loading' | 'ready' | 'fallback'>('loading');
  const host = useRef<HTMLDivElement>(null), controller = useRef<SpatialController | null>(null);
  const drag = useRef<{ id: number; x: number; y: number } | null>(null);
  const descriptionId = useId();
  const options: SceneOptions = { animated: !preferences.reduced && !paused, expanded, accent: preferences.accent, primary: preferences.primary };
  const latest = useRef(options); latest.current = options;
  useEffect(() => {
    let cancelled = false;
    setState('loading');
    if (variant !== 'dashboard' && !('WebGL2RenderingContext' in window)) { setState('fallback'); return; }
    const loadScene = variant === 'dashboard'
      ? import('./spatial/officerScene').then(({ createOfficerScene }) => (element: HTMLElement, _variant: SpatialVariant, settings: SceneOptions, fail: () => void) => createOfficerScene(element, settings, fail))
      : import('./spatial/scene').then(({ createSpatialScene }) => createSpatialScene);
    loadScene.then(createSpatialScene => {
      if (cancelled || !host.current) return;
      controller.current = createSpatialScene(host.current, variant, latest.current, () => {
        if (!cancelled) { controller.current?.dispose(); controller.current = null; setState('fallback'); }
      });
      setState('ready');
    }).catch(() => { if (!cancelled) setState('fallback'); });
    return () => { cancelled = true; controller.current?.dispose(); controller.current = null; drag.current = null; };
  }, [variant]);
  useEffect(() => { controller.current?.update(options); }, [paused, expanded, preferences.reduced, preferences.accent, preferences.primary]);
  const pointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.id === event.pointerId) {
      controller.current?.turn((event.clientX - drag.current.x) * .009, event.pointerType === 'touch' ? 0 : (event.clientY - drag.current.y) * .003);
      drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
    } else if (event.pointerType !== 'touch') {
      const bounds = event.currentTarget.getBoundingClientRect();
      controller.current?.point((event.clientX - bounds.left) / bounds.width - .5, (event.clientY - bounds.top) / bounds.height - .5);
    }
  };
  const release = (event: PointerEvent<HTMLDivElement>) => { drag.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); };
  return <section className={`spatial-hero ${compact ? 'spatial-compact' : ''}`} data-scene={variant} data-motion={options.animated ? 'on' : 'off'} aria-label={`${info.tag}主视觉`}>
    <div className="spatial-atmosphere" aria-hidden="true"><i/><i/></div>
    <div className="spatial-copy"><span className="spatial-eyebrow"><i/>{info.tag}</span><h2>{title ?? info.title}</h2><p>{description ?? info.description}</p>
      {action && <div className="spatial-action">{action}</div>}
      <div className="spatial-points">{(details ?? info.details).map((item, index) => <span key={item}><small>0{index + 1}</small>{item}</span>)}</div>
    </div>
    <div className="spatial-visual">
      {variant !== 'dashboard' && <div className="spatial-scene-heading"><span><Box size={13}/> {state === 'ready' ? '交互空间' : '空间视图'}</span><span className="spatial-coordinate">NIA / {String(Object.keys(content).indexOf(variant) + 1).padStart(2, '0')}</span></div>}
      <div className="spatial-stage" role="group" aria-label={`${info.tag}${variant === 'dashboard' ? '原图立体场景' : '三维场景'}`} aria-describedby={descriptionId} tabIndex={state === 'ready' ? 0 : -1} data-scene-state={state}
        onPointerDown={event => { if (state !== 'ready' || event.button !== 0) return; event.currentTarget.focus({ preventScroll: true }); drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY }; event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={pointerMove} onPointerUp={release} onPointerCancel={release} onLostPointerCapture={() => { drag.current = null; }}
        onPointerLeave={() => { if (!drag.current) controller.current?.point(0, 0); }}
        onKeyDown={event => { if (state !== 'ready') return; const steps: Record<string, [number, number]> = { ArrowLeft: [-.15, 0], ArrowRight: [.15, 0], ArrowUp: [0, -.08], ArrowDown: [0, .08] }; if (steps[event.key]) { event.preventDefault(); controller.current?.turn(...steps[event.key]); } else if (event.key === 'Home') { event.preventDefault(); controller.current?.reset(); } }}>
        <div className="spatial-canvas" ref={host}/>
        {state !== 'ready' && variant === 'dashboard' && <div className="officer-fallback"><img src="/characters/xiaoyi-front.png" alt="小依参考形象：身穿蓝色警服、深色领带的短发卡通人物"/><span>{state === 'loading' ? '正在载入小依' : '小依 · 静态参考形象'}</span></div>}
        {state !== 'ready' && variant !== 'dashboard' && <div className="spatial-fallback" aria-hidden="true"><div><i/><i/><i/><Layers3 size={54} strokeWidth={1}/></div><span>{info.code}</span></div>}
      </div>
      <div className="spatial-toolbar"><div className="spatial-view-switch" role="group" aria-label={variant === 'dashboard' ? '人物取景' : '场景结构'}><button type="button" aria-pressed={!expanded} disabled={state !== 'ready'} onClick={() => setExpanded(false)}><Box size={13}/>{variant === 'dashboard' ? '全身' : '聚合'}</button><button type="button" aria-pressed={expanded} disabled={state !== 'ready'} onClick={() => setExpanded(true)}><Expand size={13}/>{variant === 'dashboard' ? '半身' : '展开'}</button></div><div className="spatial-tool-actions"><button type="button" aria-label="重置三维视角" title="重置视角" disabled={state !== 'ready'} onClick={() => { controller.current?.reset(); setExpanded(variant === 'dashboard'); }}><RotateCcw size={14}/></button><button type="button" aria-label={paused ? '播放场景动效' : '暂停场景动效'} title={preferences.reduced ? '已启用减少动态效果' : paused ? '播放场景动效' : '暂停场景动效'} aria-pressed={paused || preferences.reduced} disabled={state !== 'ready' || preferences.reduced} onClick={() => setPaused(value => !value)}>{paused || preferences.reduced ? <Play size={14}/> : <Pause size={14}/>}</button></div></div>
      <p className={variant === 'dashboard' ? 'sr-only' : 'spatial-hint'} id={descriptionId}><Hand size={12}/>{state === 'ready' ? variant === 'dashboard' ? '拖动或方向键调整人物视差，Home 复位。可切换全身、半身或暂停动画。' : preferences.reduced ? '静态展示 · 可拖动或用方向键调整视角' : paused ? '已暂停 · 拖动旋转 · Home 复位' : '自动旋转 · 拖动探索 · Home 复位' : state === 'loading' ? '正在展开空间…' : '静态展示 · 业务功能正常可用'}</p>
    </div>

  </section>;
}
