export type AlertCategory = 'due_soon' | 'due_today' | 'past_due' | 'needs_review';
export type AlertStatus = 'open' | 'in_progress' | 'closed';
export type AlertAction = 'start' | 'close' | 'reopen' | 'note';
export type AlertItem = {
  id: string; person_id: string; person_name: string; person_code: string; stay_id: string;
  category: AlertCategory; status: AlertStatus; priority: 'urgent' | 'normal' | 'review'; deadline: string | null; days_remaining: number | null; visa_valid_until: string | null;
  reason: string; source_summary: string; last_seen_at: string; created_at: string; updated_at: string; auto_closed: boolean;
};
export type AlertDetail = AlertItem & { history: { id: string; action: string; note: string; created_at: string }[] };
export type AlertList = { items: AlertItem[]; total: number; page: number; page_size: number; stats: { active: number; due_soon: number; due_today: number; past_due: number; needs_review: number }; last_scan_at: string | null };
export type AlertRuleInput = { enabled: boolean; upcoming_days: number; urgent_days: number; scan_interval_minutes: number };
export type AlertRules = AlertRuleInput & { last_scan_at?: string | null; next_scan_at?: string | null; last_error?: string | null };
export type ScanResult = { created: number; updated: number; closed: number; scanned: number; scanned_at: string };
export type StayFacts = { visa_valid_until: string | null; extension_status: 'none' | 'pending' | 'approved' | 'rejected'; extension_until: string | null; receipt_valid_until: string | null; verified: boolean; source: string; notes: string };
export const categoryLabels: Record<AlertCategory, string> = { due_soon: '临近登记期限', due_today: '登记期限今日届满', past_due: '已超过登记期限', needs_review: '事实待核查' };
export const statusLabels: Record<AlertStatus, string> = { open: '待处理', in_progress: '核查中', closed: '已关闭' };
export const actionLabels: Record<string, string> = { created: '生成提醒', create: '生成提醒', updated: '更新提醒', update: '更新提醒', start: '开始核查', close: '关闭提醒', reopen: '重新打开', note: '补充备注', auto_close: '扫描自动关闭', auto_closed: '扫描自动关闭', auto_reopen: '扫描重新打开', facts_changed: '核查事实发生变化' };
