import { render as baseRender, type RenderOptions } from '@testing-library/react';
import type { ReactElement } from 'react';
import { AuthContext, type Account } from '../auth/AuthContext';
export const admin: Account = {id:'admin',name:'管理员',phone:'15210451737',department:'',notes:'',role:'admin',enabled:true,created_at:0,last_login:null};
export function render(ui: ReactElement, options?: RenderOptions) { return baseRender(<AuthContext.Provider value={{user:admin,refresh:async()=>{},logout:async()=>{}}}>{ui}</AuthContext.Provider>,options); }
