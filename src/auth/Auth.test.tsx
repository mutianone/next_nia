import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { AuthContext, AuthProvider, Access, type Account } from './AuthContext';
import { Login } from './Login';
import { AccountManagement } from './AccountManagement';
import { api } from '../api';
vi.mock('../api', () => ({api:vi.fn()}));
const admin:Account={id:'a',phone:'18792636900',name:'管理员',department:'',notes:'',role:'admin',enabled:true,created_at:0,last_login:null};
afterEach(()=>vi.resetAllMocks());
it('shows a police entrance and sends phone/password without exposing default credentials',async()=>{
  const onLogin=vi.fn();vi.mocked(api).mockResolvedValue(admin);
  render(<Login onLogin={onLogin}/>);
  expect(screen.getByText('出入境警务 · 业务协同')).toBeInTheDocument();
  expect(screen.queryByDisplayValue('18792636900')).not.toBeInTheDocument();
  await userEvent.type(screen.getByLabelText('手机号'),'18792636900');
  await userEvent.type(screen.getByLabelText('密码',{exact:true}),'123456');
  expect(screen.getByLabelText('密码',{exact:true})).toHaveAttribute('type','password');
  await userEvent.click(screen.getByRole('button',{name:'登录'}));
  expect(api).toHaveBeenCalledWith('/auth/login',{method:'POST',body:JSON.stringify({phone:'18792636900',password:'123456'})});
  expect(onLogin).toHaveBeenCalledWith(admin);
});
it('unmounts protected business content when the session expires',async()=>{
  vi.mocked(api).mockResolvedValue(admin);
  render(<AuthProvider><div>机密业务页面</div></AuthProvider>);
  await screen.findByText('机密业务页面');
  window.dispatchEvent(new Event('auth-expired'));
  await screen.findByRole('heading',{name:'登录工作空间'});
  expect(screen.queryByText('机密业务页面')).not.toBeInTheDocument();
});
it('fails closed without a user and hides write/admin controls from viewers',()=>{
  const {rerender}=render(<Access><button>保存业务</button></Access>);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  rerender(<AuthContext.Provider value={{user:{...admin,role:'viewer'},refresh:async()=>{},logout:async()=>{}}}><Access><button>保存业务</button></Access><Access permission="admin"><button>系统配置</button></Access><span>查看业务</span></AuthContext.Provider>);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();expect(screen.getByText('查看业务')).toBeInTheDocument();
});
it('creates an account with the selected role in a custom dialog',async()=>{
  vi.mocked(api).mockImplementation(async(path)=>path==='/accounts'?[admin]:{});
  render(<AuthContext.Provider value={{user:admin,refresh:async()=>{},logout:async()=>{}}}><AccountManagement/></AuthContext.Provider>);
  await screen.findByText('18792636900');await userEvent.click(screen.getByRole('button',{name:'新增账号'}));
  expect(screen.getByRole('dialog')).toHaveAccessibleName('新增账号');
  await userEvent.type(screen.getByLabelText('姓名'),'办案人员');await userEvent.type(screen.getByLabelText('部门'),'出入境管理大队');await userEvent.type(screen.getByLabelText(/^备注/),'负责窗口业务');await userEvent.type(screen.getByLabelText('手机号'),'13800138000');await userEvent.type(screen.getByLabelText('初始密码'),'new12345');
  await userEvent.click(screen.getByRole('button',{name:'确认保存'}));
  await waitFor(()=>expect(api).toHaveBeenCalledWith('/accounts',{method:'POST',body:JSON.stringify({name:'办案人员',department:'出入境管理大队',notes:'负责窗口业务',phone:'13800138000',role:'business',password:'new12345'})}));
});
