import { expect, it } from 'vitest';
import { navigation, canAccessPage } from './navigation';
it('splits management and business modules by root identity, including existing admins', () => {
  for (const role of ['admin', 'business', 'viewer']) {
    const pages = navigation.filter(n => canAccessPage({phone:'13800138000',role}, n.id));
    expect([...new Set(pages.map(n=>n.group))]).toEqual(['工作空间','核心业务']);
  }
  const pages = navigation.filter(n => canAccessPage({phone:'root',role:'admin'}, n.id));
  expect([...new Set(pages.map(n=>n.group))]).toEqual(['业务资料','系统管理']);
  expect(canAccessPage(null, 'dashboard')).toBe(false);
});
