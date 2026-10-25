import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { AuthContext } from './auth/AuthContext';
import { admin } from './test/auth';
import { SettingsHub } from './pages/SettingsHub';
it('keeps personal appearance and guides accessible to viewers without mounting admin settings', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch');
  render(<AuthContext.Provider value={{user:{...admin,role:'viewer'},refresh:async()=>{},logout:async()=>{}}}><SettingsHub onSaved={()=>{}} theme={{fontSize:100,setFontSize:()=>{},theme:'government',setTheme:()=>{},mode:'light',setMode:()=>{},reducedMotion:false,setReducedMotion:()=>{}}}/></AuthContext.Provider>);
  expect(screen.queryByRole('tab',{name:'系统管理'})).not.toBeInTheDocument();
  expect(screen.getByRole('slider',{name:'自由调节字体大小'})).toBeInTheDocument();
  await userEvent.click(screen.getByRole('tab',{name:'使用引导'}));
  fireEvent.change(screen.getByRole('textbox',{name:'搜索使用引导'}),{target:{value:'智能笔录'}});
  expect(screen.getByText('录音、转写、校对并整理笔录。')).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
  fetch.mockRestore();
});
