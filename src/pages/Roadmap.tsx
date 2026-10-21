import { SpatialHero } from '../components/SpatialHero';
import { ArrowRight, CircleCheck, Clock3, FileCheck2, LockKeyhole } from 'lucide-react';
import { navigation, stages } from '../navigation';
import type { PageId } from '../navigation';

const details: Record<string, { text: string; features: string[]; dependency: string }> = {
  assistant: { text: '每一个回答，都能回到可靠的法律依据。', features: ['法律问题与业务事实录入', '法规原文与适用版本引用', '缺失事实与待核实事项提示', '硅基流动与 DeepSeek 可切换'], dependency: '先完成法规导入与检索评测，再接入带引用的问答。' },
  knowledge: { text: '让法规有结构，让知识可追溯。', features: ['法规文件与扫描件导入', '按章、条、款、项组织知识', '中文关键词与向量混合检索', '版本差异与生效时间管理'], dependency: '需要业务方提供法规、裁量标准与业务规范资料。' },
  people: { text: '一份完整档案，串联每一次业务记录。', features: ['人员与证件独立建档', '停居留和入出境记录管理', 'Excel / CSV 批量导入校验', '附件归档与修改记录'], dependency: '使用虚构测试数据验证后，再迁移经过确认的旧系统资料。' },
  alerts: { text: '及时发现需要核查的期限变化。', features: ['区分入境有效期与准许停留期限', '延期、离境与回执信息核对', '预警原因与数据时间展示', '待核查到归档的处理闭环'], dependency: '具体期限规则经业务确认；资料缺失不直接认定违法。' },
  documents: { text: '让每一份文书，都有规范可依。', features: ['版本化 DOCX 模板管理', '业务字段填充与完整性校验', 'A4 预览、Word 和 PDF 导出', '草稿、确认与归档记录'], dependency: '正式文书名称、固定文案与版式等待你们审定的样本。' },
  records: { text: '留存真实声音，专注每一段询问。', features: ['原始录音本地留存', '中文分段转写与片段回听', '人工校对与修改版本保留', '要素提取、独立摘要与模板成文'], dependency: '转写需配置硅基流动，并确认录音允许经受控出口发送。' },
  profiles: { text: '让已核实的事实，形成清晰的脉络。', features: ['身份与来华事由信息卡片', '停居留记录与业务时间线', '已核实关系的可视化展示', '每个标签均可查看来源'], dependency: '基于已确认业务资料，不生成个人违法倾向评分。' },
  analytics: { text: '从真实业务记录中，看见全局。', features: ['业务数据聚合与趋势分析', '预警核查与办理进度统计', '可切换主题的全屏展示', '本地地图与图表联动'], dependency: '完整统计分析在第五阶段接入，当前工作台展示已登记人员、停留记录与法规资料统计。' },
};

export function Roadmap({ page, onNavigate }: { page: PageId; onNavigate: (page: PageId) => void }) {
  const item = navigation.find(n => n.id === page)!;
  const detail = details[page];
  if (!detail) return null;
  return <div className="page-content roadmap-page"><div className="page-heading"><div><div className="eyebrow">BUILDING WITH PURPOSE</div><h1 className="page-title">{item.label}<span className="title-dot">.</span></h1><p className="page-description">{detail.text}</p></div><span className="badge roadmap-badge"><Clock3 size={14} /> 第 {item.stage} 阶段 · 待开发</span></div>
    <SpatialHero variant={page === 'profiles' ? 'profiles' : 'analytics'} action={<button className="hero-button" onClick={() => onNavigate('settings')}>先配置运行环境 <ArrowRight size={16}/></button>}/>
    <div className="roadmap-details"><section className="panel"><div className="section-title"><h2><FileCheck2 size={18} /> 计划实现</h2></div><div className="feature-plan">{detail.features.map((feature, i) => <div key={feature}><span>0{i + 1}</span><p>{feature}</p><Clock3 size={15} /></div>)}</div><div className="dependency-note"><LockKeyhole size={17} /><p>{detail.dependency}</p></div></section><section className="panel"><div className="section-title"><h2>开发路线</h2><span className="badge">分阶段交付</span></div><div className="phase-timeline">{stages.map(s => <div key={s.number} className={`phase-row ${s.number === 4 ? 'current' : ''} ${s.number === item.stage ? 'highlight' : ''}`}><span className="phase-marker">{s.number === 1 ? <CircleCheck size={18} /> : `0${s.number}`}</span><div><strong>{s.title}</strong><p>{s.detail}</p></div><small>{s.state}</small></div>)}</div></section></div>
  </div>;
}
