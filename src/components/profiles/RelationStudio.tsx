import { memo, useEffect, useMemo, useState } from 'react';
import { Background, Controls, Handle, MiniMap, Position, ReactFlow, applyNodeChanges, type Node, type NodeProps, type ReactFlowInstance } from '@xyflow/react';
import { ArrowUpRight, CheckCheck, CircleDashed, Focus, GitBranch, Grip, Maximize2, Minimize2, Network, RotateCcw, Search, ShieldCheck } from 'lucide-react';
import type { Person } from '../../people-types';
import type { Relation } from '../../pages/Profiles';
import { groupRelations, graphPosition, type GraphLayout } from './profileGraph';
import { Dialog } from '../Dialog';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '../ui/resizable';
import { useMediaQuery } from '../../hooks/useMediaQuery';
import '@xyflow/react/dist/style.css';

type PersonNode = Node<{ name: string; code: string; center: boolean; verified: boolean; labels: string }, 'person'>;
const ProfileNode = memo(function ProfileNode({ data, selected }: NodeProps<PersonNode>) {
  return <div className={`ps-node ${data.center ? 'center' : ''} ${data.verified ? '' : 'pending'} ${selected ? 'chosen' : ''}`}>
    {[Position.Left, Position.Right, Position.Top, Position.Bottom].map(position => <Handle key={position} id={position} type={data.center ? 'source' : 'target'} position={position} isConnectable={false}/>)}
    <div className="ps-node-top"><span className="ps-node-avatar">{data.name.slice(0, 1)}</span><span><strong title={data.name}>{data.name}</strong><small>{data.code}</small></span><Grip size={13}/></div>
    <footer>{data.center ? <><Focus size={12}/>当前档案</> : data.verified ? <><ShieldCheck size={12}/>{data.labels}</> : <><CircleDashed size={12}/>{data.labels} · 含待核验</>}</footer>
  </div>;
});
const nodeTypes = { person: ProfileNode };
const ariaLabelConfig = { 'controls.zoomIn.ariaLabel': '放大图谱', 'controls.zoomOut.ariaLabel': '缩小图谱', 'controls.fitView.ariaLabel': '适配全部节点', 'minimap.ariaLabel': '关系图谱导航缩略图', 'node.a11yDescription.default': '按回车选择节点，使用方向键移动，按 Escape 取消选择。', 'edge.a11yDescription.default': '按回车查看关系来源。' };

export function RelationStudio({ person, relations, onOpen }: { person: Person; relations: Relation[]; onOpen: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  // Expansion moves the same workspace into a dialog; its data is local to this person.
  const [includePending, setIncludePending] = useState(false), [layout, setLayout] = useState<GraphLayout>('orbit');
  const [selected, setSelected] = useState(person.id), [query, setQuery] = useState('');
  const [measurements, setMeasurements] = useState<Record<string, { width: number; height: number }>>({});
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>({});
  const [flow, setFlow] = useState<ReactFlowInstance<PersonNode> | null>(null);
  const compact = useMediaQuery('(max-width: 600px)');
  const wide = useMediaQuery('(min-width: 1250px)');
  const groups = useMemo(() => groupRelations(relations, includePending), [relations, includePending]);
  useEffect(() => { const frame = requestAnimationFrame(() => { void flow?.fitView({ padding: .22, maxZoom: 1 }); }); return () => cancelAnimationFrame(frame); }, [flow, groups.length, layout, compact]);
  const selectedRelations = groups.find(([id]) => id === selected)?.[1] || [];
  const nodes: PersonNode[] = useMemo(() => [
    { id: person.id, type: 'person', measured: measurements[person.id], position: positions[person.id] || { x: 0, y: 0 }, selected: selected === person.id, ariaLabel: '当前档案 ' + person.name, data: { name: person.name, code: person.code, center: true, verified: true, labels: '' } },
    ...groups.map(([id, rows], i): PersonNode => ({ id, type: 'person', measured: measurements[id], position: positions[id] || (compact && layout === 'orbit' ? { x: 0, y: (i + 1) * 145 } : graphPosition(i, groups.length, layout)), selected: selected === id, ariaLabel: '关系节点 ' + rows[0].other.name, data: { name: rows[0].other.name, code: rows[0].other.code, center: false, verified: rows.every(r => r.verified), labels: [...new Set(rows.map(r => r.label))].join(' / ') } })),
  ], [person, groups, positions, selected, layout, measurements, compact]);
  const edges = groups.map(([id, rows], index) => {
    const point = nodes.find(n => n.id === id)!.position, center = nodes[0].position;
    const dx = point.x - center.x, dy = point.y - center.y;
    const sourceHandle = Math.abs(dx) > Math.abs(dy) ? dx >= 0 ? 'right' : 'left' : dy >= 0 ? 'bottom' : 'top';
    const opposite: Record<string, string> = { left: 'right', right: 'left', top: 'bottom', bottom: 'top' };
    return ({ sourceHandle: compact ? 'left' : sourceHandle, targetHandle: compact ? 'left' : opposite[sourceHandle], pathOptions: { offset: 30 + index * 12, borderRadius: 12 }, id: `edge-${id}`, ariaLabel: '查看与' + rows[0].other.name + '的关系依据', source: person.id, target: id, label: [...new Set(rows.map(r => r.label))].join(' / '), type: compact ? 'smoothstep' : 'default', selected: selected === id,
    style: { stroke: rows.every(r => r.verified) ? 'var(--primary)' : 'var(--warning)', strokeWidth: selected === id ? 2.5 : 1.4, strokeDasharray: rows.every(r => r.verified) ? undefined : '6 5' },
    labelStyle: { fill: 'var(--text)', fontSize: 11 }, labelBgStyle: { fill: 'var(--surface)' }, labelBgPadding: [8, 5] as [number, number], labelBgBorderRadius: 5,
  }); });
  const focusNode = (id: string) => { setSelected(id); void flow?.fitView({ nodes: [{ id: person.id }, { id }], padding: .5, maxZoom: 1 }); };
  const reset = (next = layout) => { setLayout(next); setPositions({}); setSelected(person.id); requestAnimationFrame(() => { void flow?.fitView({ padding: .22, maxZoom: 1 }); }); };
  const inspector = <aside className="ps-inspector" aria-label="选中节点详情">
    <div className="ps-inspector-heading"><span>节点档案</span><Network size={16}/></div>
    <div className="ps-inspector-person"><span className="p5-avatar">{(selectedRelations[0]?.other.name || person.name).slice(0, 1)}</span><div><h3>{selectedRelations[0]?.other.name || person.name}</h3><small>{selectedRelations[0]?.other.code || person.code}</small></div></div>
    {selectedRelations.length ? <><div className="ps-evidence-list">{selectedRelations.map(r => <section key={r.id}><h4>{r.label}<span className={`p5-status ${r.verified ? 'verified' : ''}`}>{r.verified ? '已核验' : '待核验'}</span></h4><small>资料来源</small><p>{r.source}</p><small>核验说明</small><p>{r.review_note || '尚未人工核验'}</p></section>)}</div><button className="btn btn-primary" onClick={() => onOpen(selected)}>打开关联档案<ArrowUpRight size={14}/></button></> : <><div className="ps-evidence-list"><section><small>档案来源</small><p>{person.source || '尚未填写'}</p><small>来华事由</small><p>{person.purpose || '尚未填写'}</p></section></div><p className="ps-inspector-tip">点击节点或连线查看关系依据；拖动节点整理画布。</p></>}
    <div className="ps-node-directory"><h4>关联人员 <span>{groups.length}</span></h4>{groups.map(([id, rows]) => <button key={id} className={selected === id ? 'active' : ''} onClick={() => focusNode(id)}><span>{rows[0].other.name}</span>{rows.every(r => r.verified) ? <CheckCheck size={14}/> : <CircleDashed size={14}/>}</button>)}</div>
  </aside>;
  const canvas = <div className="ps-canvas"><ReactFlow<PersonNode>
    nodes={nodes} edges={edges} nodeTypes={nodeTypes} onInit={setFlow} fitView fitViewOptions={{ padding: .22, maxZoom: 1 }} minZoom={.08} maxZoom={1.8}
    onNodesChange={changes => {
      for (const change of changes) if (change.type === 'dimensions' && change.dimensions) { const dimensions = change.dimensions; setMeasurements(previous => previous[change.id]?.width === dimensions.width && previous[change.id]?.height === dimensions.height ? previous : { ...previous, [change.id]: dimensions }); }
      const selection = changes.find(c => c.type === 'select' && c.selected); if (selection?.type === 'select') setSelected(selection.id); if (!changes.some(c => c.type === 'position')) return; const next = applyNodeChanges(changes.filter(c => c.type === 'position'), nodes); setPositions(previous => ({ ...previous, ...Object.fromEntries(next.map(n => [n.id, n.position])) })); }}
    onNodeClick={(_, node) => setSelected(node.id)} onEdgeClick={(_, edge) => setSelected(edge.target)} onPaneClick={() => setSelected(person.id)}
    nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null} zoomOnScroll={false} panOnScroll={false} preventScrolling={false} ariaLabelConfig={ariaLabelConfig}
    proOptions={{ hideAttribution: true }}>
    <Background color="var(--border)" gap={22} size={1.2}/><Controls showInteractive={false}/><MiniMap style={{ width: 120, height: 85 }} pannable zoomable nodeColor="var(--primary)" maskColor="color-mix(in srgb, var(--surface) 80%, transparent)"/>
  </ReactFlow><div className="ps-canvas-label"><span className="ps-live-dot"/>直接关系 · {groups.length} 位关联人员</div>{!groups.length && <div className="ps-graph-empty">尚无{includePending ? '可展示' : '已核验'}关系<br/><small>登记关系并核验后，可在这里展开网络。</small></div>}<div className="ps-canvas-caption">拖动节点 / 空白处平移 · 双指缩放 · 选中后方向键移动</div></div>;
  const content = <div className={`ps-studio ${expanded ? 'expanded' : ''}`}>
    <div className="ps-graph-toolbar"><div className="ps-segment" aria-label="图谱布局"><button aria-pressed={layout === 'orbit'} onClick={() => reset('orbit')}><Network size={14}/>{compact ? '纵向' : '环形'}</button><button aria-pressed={layout === 'columns'} onClick={() => reset('columns')}><GitBranch size={14}/>分列</button></div><label className="ps-pending-toggle"><input type="checkbox" checked={includePending} onChange={e => { setIncludePending(e.target.checked); setSelected(person.id); setPositions({}); }}/>显示待核验</label><div className="ps-toolbar-end"><button className="icon-button" title="重置布局" aria-label="重置图谱布局" onClick={() => reset()}><RotateCcw size={16}/></button><button className="icon-button" title={expanded ? '退出专注视图' : '专注视图'} aria-label={expanded ? '退出图谱专注视图' : '打开图谱专注视图'} onClick={() => setExpanded(!expanded)}>{expanded ? <Minimize2 size={16}/> : <Maximize2 size={16}/>}</button></div></div>
    <form className="ps-graph-search" onSubmit={e => { e.preventDefault(); const match = groups.find(([, rows]) => rows[0].other.name.includes(query.trim()) || rows[0].other.code.includes(query.trim())); if (match) focusNode(match[0]); }}><Search size={14}/><input aria-label="定位图谱节点" placeholder="在图谱中定位姓名或档案编号" value={query} onChange={e => setQuery(e.target.value)}/><button type="submit" disabled={!query.trim() || !groups.some(([, rows]) => rows[0].other.name.includes(query.trim()) || rows[0].other.code.includes(query.trim()))}>定位</button></form>
    {wide ? <ResizablePanelGroup orientation="horizontal" className="ps-graph-split"><ResizablePanel defaultSize="73%" minSize="50%">{canvas}</ResizablePanel><ResizableHandle withHandle aria-label="调整图谱与详情宽度"/><ResizablePanel defaultSize="27%" minSize="24%" maxSize="45%">{inspector}</ResizablePanel></ResizablePanelGroup> : <div className="ps-graph-stack">{canvas}{inspector}</div>}
    <footer className="ps-graph-legend"><span><i/>已核验关系</span><span><i className="pending"/>待核验关系</span><span>布局调整仅影响当前视图</span></footer>
  </div>;
  return expanded ? <Dialog title={`${person.name} · 关系图谱`} onClose={() => setExpanded(false)} className="ps-graph-dialog">{content}</Dialog> : content;
}
