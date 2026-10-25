import { aiLabel } from './lib/aiLabel';
export type Runtime = { active_environment: 'test' | 'production'; database_engine: string; pending_restart: boolean };
export type Health = Runtime & { status: string; version: string; stage: number | string };
export type DashboardData = {
  demo: boolean;
  updated_at: string;
  stats: { registered_stays: number; knowledge_documents: number; pending_reviews: number; people: number; active_permits: number; pending_alerts: number; draft_records: number; transcript_drafts: number };
  trend: { label: string; entries: number; exits: number }[];
  activities: { id: string; title: string; description: string; time: string; type: string }[];
  alerts_last_scan_at: string | null;
  alerts: { id: string; name: string; code: string; category: string; days: number | null; status: string }[];
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api${path}`, { ...init, headers });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login' && path !== '/auth/me') window.dispatchEvent(new Event('auth-expired'));
    throw Object.assign(new Error(aiLabel(typeof data?.detail === 'string' ? data.detail : `请求失败（${response.status}）`)), { status: response.status });
  }
  return data as T;
}
