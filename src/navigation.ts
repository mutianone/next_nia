import { Sparkles, BookOpen, UsersRound, ShieldAlert, AudioLines, Network, ChartNoAxesCombined, Settings2, Palette } from 'lucide-react';

export const navigation = [
  { id: 'analytics', label: '统计分析', en: 'Analytics', icon: ChartNoAxesCombined, stage: 5, group: '工作空间' },
  { id: 'assistant', label: '执法智能助手', en: 'AI Assistant', icon: Sparkles, stage: 2, group: '核心业务' },
  { id: 'alerts', label: '签证逾期预警', en: 'Alerts', icon: ShieldAlert, stage: 3, group: '核心业务' },
  { id: 'records', label: '智能笔录生成', en: 'Transcripts', icon: AudioLines, stage: 4, group: '核心业务' },
  { id: 'profiles', label: '人物画像分析', en: 'Profiles', icon: Network, stage: 5, group: '核心业务' },
  { id: 'knowledge', label: '法规知识中心', en: 'Knowledge', icon: BookOpen, stage: 2, group: '业务资料' },
  { id: 'people', label: '人员档案', en: 'People', icon: UsersRound, stage: 2, group: '业务资料' },
  { id: 'appearance', label: '外观主题', en: 'Appearance', icon: Palette, stage: 1, group: '系统管理' },
  { id: 'settings', label: '系统设置', en: 'Settings', icon: Settings2, stage: 1, group: '系统管理' },
] as const;
export type PageId = typeof navigation[number]['id'] | 'documents' | 'dashboard';
export const stages = [
  { number: 1, title: '基础工作空间', detail: '主题系统 · 移动适配 · 环境与 AI 配置', state: '已完成' },
  { number: 2, title: '人员与法规知识', detail: '人员档案 · 文档导入 · 本地检索 · 引用问答', state: '已完成' },
  { number: 3, title: '预警与规范文书', detail: '期限核查 · 处理记录 · 模板与文书导出', state: '已完成' },
  { number: 4, title: '智能笔录工作室', detail: '录音留存 · 中文转写 · 校对总结 · 成文', state: '已完成' },
  { number: 5, title: '关联分析与交付', detail: '事实画像 · 业务统计 · 数据迁移 · 验收', state: '当前阶段' },
];

// Module visibility is separate from the existing read/write role permissions.
export function canAccessPage(user: { phone: string; role: string } | null, page: PageId) {
  if (!user || page === 'documents' || page === 'dashboard') return false;
  const group = navigation.find(item => item.id === page)?.group;
  const management = group === '业务资料' || group === '系统管理';
  return (user.phone === 'root' ? management : !management) && (page !== 'assistant' || user.role !== 'viewer');
}
