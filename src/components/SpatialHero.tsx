import type { ReactNode } from 'react';
import type { SpatialVariant } from './spatial/types';

const content: Record<SpatialVariant, { tag: string; title: ReactNode; description: string; details: string[]; code: string }> = {
  dashboard: { tag: '智境·出入智境能警务平台', title: '业务办理概览', description: '查看登记资料与当前办理进度。', details: ['知识有据', '业务有序', '资料留存'], code: 'CONNECTED WORKSPACE' },
  assistant: { tag: '智境 · 法规助手', title: '法规检索与引用', description: '从已审核原文出发，查找条款，再理解问题。', details: ['本地检索', '来源可查', '按需 AI'], code: 'KNOWLEDGE COPILOT' },
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

/** Static business summary; no 3D renderer or scene controls. */
export function SpatialHero({ variant, action, title, description, details, corner }: { variant: SpatialVariant; corner?: ReactNode; action?: ReactNode; compact?: boolean; title?: ReactNode; description?: string; details?: string[] }) {
  const info = content[variant];
  return <section className="spatial-hero business-summary" style={{display: 'block', minHeight: 0}} aria-label={info.tag}>
    {corner && <div className="spatial-corner">{corner}</div>}
    <div className="spatial-copy"><span className="spatial-eyebrow">{info.tag}</span><h2>{title ?? info.title}</h2><p>{description ?? info.description}</p>
      {action && <div className="spatial-action">{action}</div>}
      <div className="spatial-points">{(details ?? info.details).map(item => <span key={item}>{item}</span>)}</div>
    </div>
  </section>;
}
