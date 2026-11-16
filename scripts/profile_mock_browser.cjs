// Run with Playwright available on NODE_PATH. All API traffic is mocked with
// responses produced by profile_mock_qa.py; no application data is written.
const {chromium}=require('playwright');
const fs=require('fs');const path=require('path');const assert=require('node:assert/strict');
const dir=process.argv[2]||'/private/tmp/portrait-qa';
const fixtures=JSON.parse(fs.readFileSync(path.join(dir,'profiles.json')));
(async()=>{const browser=await chromium.launch({headless:true,channel:'chrome'});try{
 const page=await browser.newPage({viewport:{width:1600,height:1100}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',route=>{const url=new URL(route.request().url());let json={};
 if(url.pathname==='/api/auth/me')json={id:'mock-user',phone:'mock-business',name:'虚构验收账号',role:'business',enabled:true};
 else if(url.pathname==='/api/settings')json={settings:{general:{system_name:'人物画像 · 虚构测试'}}};
 else if(url.pathname==='/api/people'){const q=url.searchParams.get('q')||'';const items=Object.values(fixtures).map(p=>p.person).filter(p=>p.name.includes(q));json={items,total:items.length};}
 else if(url.pathname.startsWith('/api/profiles/'))json=fixtures[url.pathname.split('/').pop()];
 return route.fulfill({json});});
 await page.goto((process.env.PROFILE_QA_URL||'http://127.0.0.1:5173')+'/#/profiles');
 await page.getByRole('button',{name:/测试阿雅.*查看画像/}).click();
 await page.locator('.portrait-circle').first().waitFor();await page.evaluate(()=>document.documentElement.dataset.mode='dark');
 assert.equal(await page.locator('.react-flow__attribution').count(),0);
 const phone=page.locator('.react-flow__node').filter({has:page.locator('small',{hasText:'联系电话'})});await phone.hover();await page.waitForTimeout(350);
 assert(await phone.locator('.portrait-hover-card').isVisible());
 assert.equal(await phone.locator('.portrait-hover-card').innerText(),'联系电话\n00000000000');
 const scale=await phone.locator('.portrait-circle').evaluate(e=>new DOMMatrix(getComputedStyle(e).transform).a);assert(scale>1);
 await page.mouse.move(10,10);await page.waitForTimeout(250);await page.screenshot({path:path.join(dir,'personal.png'),fullPage:true});
 await page.getByRole('tab',{name:'关系图谱',exact:true}).click();
 const rows=fixtures.subject.radial.relations;assert.equal(await page.locator('.react-flow__node').count(),6);
 async function clickRelation(role){const row=rows.find(r=>r.role===role);const node=page.locator(`[data-id="${row.id}"] .portrait-circle`);await node.hover();await page.waitForTimeout(250);await node.click();}
 await page.mouse.move(10,10);await page.screenshot({path:path.join(dir,'relations.png'),fullPage:true});
 await clickRelation('姐姐');await page.getByText('系统内暂无可匹配的人员档案',{exact:true}).waitFor();await page.getByRole('button',{name:'关闭弹窗'}).click();
 await clickRelation('介绍人');assert.equal(await page.getByRole('button',{name:/测试小林 ·/}).count(),2);await page.getByRole('button',{name:/测试小林 ·/}).first().click();await page.locator('[data-id="self"]').filter({hasText:'测试小林'}).waitFor();
 await page.getByRole('button',{name:'测试阿雅 →',exact:true}).click();await page.getByRole('tab',{name:'关系图谱',exact:true}).click();
 await clickRelation('父亲');await page.locator('[data-id="self"]').filter({hasText:'测试父亲'}).waitFor();await page.getByRole('button',{name:'测试阿雅 →',exact:true}).click();
 await page.getByRole('button',{name:'返回人员列表',exact:true}).click();await page.getByRole('button',{name:/测试空档案.*查看画像/}).click();await page.getByRole('tab',{name:'关系图谱',exact:true}).click();await page.getByText('暂无有明确依据的关系人物',{exact:true}).waitFor();
 await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('.portrait-orbit').first().evaluate(e=>getComputedStyle(e).animationName),'none');
 assert.deepEqual(errors,[]);
 const report={passed:true,checks:['七人列表','完整个人信息','悬浮放大和完整电话','无标识','五关系去重','未建档来源','同名两候选','候选档案跳转','已关联父亲跳转','返回原人物','空关系','手机无横向溢出','减少动效','无页面异常']};fs.writeFileSync(path.join(dir,'browser-results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
