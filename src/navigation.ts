import { LayoutDashboard, Sparkles, BookOpen, UsersRound, ShieldAlert, AudioLines, Files, Network, ChartNoAxesCombined, Settings2, Palette } from 'lucide-react';

export const navigation = [
  { id: 'dashboard', label: '总览工作台', en: 'Workspace', icon: LayoutDashboard, stage: 1, group: '工作空间' },
  { id: 'assistant', label: '执法智能助手', en: 'AI Assistant', icon: Sparkles, stage: 2, group: '核心业务' },
  { id: 'knowledge', label: '法规知识中心', en: 'Knowledge', icon: BookOpen, stage: 2, group: '核心业务' },
  { id: 'people', label: '人员档案', en: 'People', icon: UsersRound, stage: 2, group: '核心业务' },
  { id: 'alerts', label: '逾期预警', en: 'Alerts', icon: ShieldAlert, stage: 3, group: '核心业务' },
  { id: 'records', label: '智能笔录', en: 'Transcripts', icon: AudioLines, stage: 4, group: '核心业务' },
  { id: 'documents', label: '文书中心', en: 'Documents', icon: Files, stage: 3, group: '核心业务' },
  { id: 'profiles', label: '人员画像', en: 'Profiles', icon: Network, stage: 5, group: '分析与管理' },
  { id: 'analytics', label: '统计分析', en: 'Analytics', icon: ChartNoAxesCombined, stage: 5, group: '分析与管理' },
  { id: 'appearance', label: '外观主题', en: 'Appearance', icon: Palette, stage: 1, group: '分析与管理' },
  { id: 'settings', label: '后台配置', en: 'Settings', icon: Settings2, stage: 1, group: '分析与管理' },
] as const;
export type PageId = typeof navigation[number]['id'];
export const stages = [
  { number: 1, title: '基础工作空间', detail: '主题系统 · 移动适配 · 环境与 AI 配置', state: '已完成' },
  { number: 2, title: '人员与法规知识', detail: '人员档案 · 文档导入 · 本地检索 · 引用问答', state: '已完成' },
  { number: 3, title: '预警与规范文书', detail: '期限核查 · 处理记录 · 模板与文书导出', state: '已完成' },
  { number: 4, title: '智能笔录工作室', detail: '录音留存 · 中文转写 · 校对总结 · 成文', state: '已完成' },
  { number: 5, title: '关联分析与交付', detail: '事实画像 · 业务统计 · 数据迁移 · 验收', state: '当前阶段' },
];
