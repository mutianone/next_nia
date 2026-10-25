import { Fragment, createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { api } from '../api';
import { Login } from './Login';
import { clearWorkspaceViews } from '../hooks/useWorkspaceView';
export type Role = 'admin' | 'business' | 'viewer';
export type Account = { id: string; phone: string; name: string; department: string; notes: string; role: Role; enabled: boolean; created_at: number; last_login: number | null };
export const roleLabels: Record<Role, string> = { admin: '管理员', business: '业务人员', viewer: '查看人员' };
type AuthValue = { user: Account | null; refresh: () => Promise<void>; logout: () => Promise<void> };
export const AuthContext = createContext<AuthValue>({ user: null, refresh: async () => {}, logout: async () => {} });
export const useAuth = () => useContext(AuthContext);
export function usePermissions() { const { user } = useAuth(); return { admin: user?.role === 'admin', write: Boolean(user && user.role !== 'viewer') }; }
export function Access({ permission = 'write', children }: { permission?: 'write' | 'admin'; children: ReactNode }) { const permissions = usePermissions(); return permissions[permission] ? <>{children}</> : null; }
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const clear = () => { setUser(null); clearWorkspaceViews(); };
  async function refresh() {
    try { const result = await api<Account>('/auth/me'); setUser(result); setError(''); }
    catch (e) { if ((e as Error & { status?: number }).status === 401) clear(); else setError('无法连接业务服务，请检查服务状态后重试。'); }
    finally { setLoading(false); }
  }
  useEffect(() => {
    void refresh();
    const expire = () => { clear(); setNotice('登录已失效，请重新登录。'); };
    const check = () => { if (document.visibilityState === 'visible') void refresh(); };
    window.addEventListener('auth-expired', expire); document.addEventListener('visibilitychange', check);
    const timer = window.setInterval(() => void refresh(), 60000);
    return () => { window.removeEventListener('auth-expired', expire); document.removeEventListener('visibilitychange', check); window.clearInterval(timer); };
  }, []);
  async function logout() { await api('/auth/logout', { method: 'POST' }); clear(); setNotice('已安全退出。'); }
  return <AuthContext.Provider value={{ user, refresh, logout }}>{loading ? <div className="auth-loading" role="status"><img src="/favicon.svg" alt=""/><span>正在连接工作空间…</span></div> : user ? <Fragment key={`${user.id}:${user.role}`}>{children}</Fragment> : <Login notice={notice} serviceError={error} onLogin={value => { clearWorkspaceViews(); setUser(value); setNotice(''); setError(''); }}/>}</AuthContext.Provider>;
}
