const { chromium, request } = require('@playwright/test');
const assert = require('node:assert/strict');
(async () => {
 const api=process.env.API_URL, base=process.env.TEST_URL || 'http://127.0.0.1:8088';
 const http=await request.newContext();
 const token=(await (await http.post(api+'/auth/token')).json()).access_token;
 const headers={Authorization: 'Bearer ' + token};
 const post=async path=>{const r=await http.post(api+path,{headers});assert(r.ok(),`${path}: ${r.status()}`);return r;};
 const id=await (await post('/editor/create_flow/?name=NativeClipboardSmoke&register_in_catalog=false')).json();
 await post(`/editor/add_node/?flow_id=${id}&node_id=1&node_type=manual_input&pos_x=700&pos_y=400`);
 const browser=await chromium.launch({headless:false,executablePath:process.env.CHROMIUM_EXECUTABLE || undefined,args:['--no-sandbox']});
 try {
 const context=await browser.newContext({viewport:{width:1500,height:1000},permissions:['clipboard-read','clipboard-write']});
 const page=await context.newPage();await page.goto(base);await page.evaluate(({token,id})=>{localStorage.setItem('auth_token',token);localStorage.setItem('auth_token_expiration',String(Date.now()+60*60*1000));sessionStorage.setItem('last_flow_id',String(id));},{token,id});await page.reload();await page.locator('.vue-flow__node').waitFor();
 const events=[];await page.exposeFunction('recordClipboard',e=>events.push(e));await page.evaluate(()=>{for(const type of ['copy','paste'])document.addEventListener(type,e=>window.recordClipboard({type,trusted:e.isTrusted}),true)});
 const count=async n=>{await require('@playwright/test').expect(page.locator('.vue-flow__node')).toHaveCount(n);const r=await http.get(`${api}/flow_data/v2?flow_id=${id}`,{headers});assert.equal((await r.json()).node_inputs.length,n);};
 await page.locator('.vue-flow__node').click();await page.keyboard.press('Control+c');
 const clipboard=await page.evaluate(()=>navigator.clipboard.readText());assert.match(clipboard,/Flowfile nodes/);console.log('PASS actual Ctrl+C writes node sentinel to OS clipboard');
 await page.mouse.move(900,650);await page.keyboard.press('Control+v');await count(2);console.log('PASS actual Ctrl+V creates copied node in canvas and backend');
 // A separate page exchanges real text with the OS clipboard, without dispatching clipboard events.
 const external=await context.newPage();await external.goto(base);await external.setContent('<textarea id="text"></textarea>');await external.locator('textarea').focus();await external.keyboard.press('Control+v');assert.equal(await external.locator('textarea').inputValue(),clipboard);console.log('PASS node sentinel can be pasted into an independent text editor');
 await external.locator('textarea').fill('unrelated plain text');await external.keyboard.press('Control+a');await external.keyboard.press('Control+c');await page.bringToFront();await page.locator('.vue-flow__pane').click({position:{x:1000,y:750}});await page.keyboard.press('Control+v');await page.waitForTimeout(700);await count(2);console.log('PASS external plain text does not resurrect stale node buffer');
 await external.bringToFront();await external.locator('textarea').fill('name\tvalue\nalice\t1\nbob\t2');await external.keyboard.press('Control+a');await external.keyboard.press('Control+c');await page.bringToFront();await page.locator('.vue-flow__pane').click({position:{x:1000,y:750}});await page.keyboard.press('Control+v');await count(3);console.log('PASS external tabular OS clipboard creates Manual Input node');
 await page.keyboard.press('Control+a');await page.keyboard.press('Control+c');const multiText=await page.evaluate(()=>navigator.clipboard.readText());assert.match(multiText,/Flowfile nodes · 3 ·/);await page.mouse.move(1100,800);await page.keyboard.press('Control+v');await count(6);console.log('PASS actual multi-node copy/paste adds all three nodes to canvas and backend');
 assert(events.some(e=>e.type==='copy'));assert(events.some(e=>e.type==='paste'));assert(events.every(e=>e.trusted));console.log('PASS all observed application clipboard events are trusted:',JSON.stringify(events));
 } finally {await browser.close();await http.dispose();}
})().catch(e=>{console.error(e);process.exitCode=1});
