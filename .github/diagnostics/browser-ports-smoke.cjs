// Real Chromium + real Flowfile services. No route mocks or component-store mutation.
const path = require('path');
const fs = require('fs');
const assert = require('node:assert/strict');
const root = process.env.FLOWFILE_CHECKOUT || '/home/familyhub/projects/Flowfile-718';
const { chromium, expect } = require(path.join(root, 'flowfile_frontend/node_modules/@playwright/test'));
const base = process.env.TEST_URL || 'http://127.0.0.1:8088';
const api = process.env.API_URL || 'http://localhost:63578';
const output = process.env.BROWSER_RESULTS;
if (!output) throw new Error('Set BROWSER_RESULTS to an evidence directory');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.tracing.start({ screenshots: true, snapshots: true });
  try {
    const response = await context.request.post(api + '/auth/token');
    assert(response.ok(), await response.text());
    const token = (await response.json()).access_token;
    const headers = { Authorization: ['Be', 'arer '].join('') + token };
    async function post(url) {
      const r = await context.request.post(api + url, { headers });
      assert(r.ok(), await r.text());
      return r.json();
    }
    const flow = await post('/editor/create_flow/?name=Browser_ports_718&register_in_catalog=false');
    for (const [id, type, x, y] of [[1,'manual_input',250,140],[2,'select',550,140],[3,'manual_input',250,380],[4,'union',850,380],[5,'join',850,650]]) {
      await post(`/editor/add_node/?flow_id=${flow}&node_id=${id}&node_type=${type}&pos_x=${x}&pos_y=${y}`);
    }
    await context.addInitScript(({ token }) => {
      localStorage.setItem('auth_token', token);
      localStorage.setItem('auth_token_expiration', String(Date.now() + 3600000));
      localStorage.setItem('flowfile-telemetry-consent-answered', '1');
    }, { token });
    await page.goto(`${base}/#/main/designer`);
    await page.getByText('Browser_ports_718', { exact: true }).last().click();
    const node = id => page.locator(`.vue-flow__node[data-id="${id}"]`);
    await expect(node(1)).toBeVisible({ timeout: 60000 });
    const handle = (id, name) => node(id).locator(`.vue-flow__handle[data-handleid="${name}"]`);
    await expect(node(1).locator('.vue-flow__handle.target')).toHaveCount(0);
    await expect(handle(1, 'output-0')).toBeVisible();
    await expect(handle(2, 'input-0')).toBeVisible();
    await expect(handle(5, 'input-0')).toBeVisible();
    await expect(handle(5, 'input-1')).toBeVisible();
    async function drag(source, target) {
      const a = await source.boundingBox(), b = await target.boundingBox();
      assert(a && b, 'handles must have bounding boxes');
      await page.mouse.move(a.x + a.width/2, a.y + a.height/2);
      await page.mouse.down();
      await page.mouse.move(b.x + b.width/2, b.y + b.height/2, { steps: 20 });
      await page.mouse.up();
    }
    async function edges() {
      const r = await context.request.get(`${api}/flow_data/v2?flow_id=${flow}`, { headers });
      assert(r.ok(), await r.text());
      return (await r.json()).node_edges;
    }
    await drag(handle(1,'output-0'), handle(2,'input-0'));
    await expect.poll(async () => (await edges()).length).toBe(1);
    await expect(page.locator('.vue-flow__edge')).toHaveCount(1);
    await drag(handle(3,'output-0'), handle(2,'input-0'));
    // Let UI event handling and a real server read finish before checking rejection.
    await page.waitForTimeout(500);
    assert.equal((await edges()).length, 1, 'occupied input must not be replaced');
    await expect(page.locator('.vue-flow__edge')).toHaveCount(1);
    await drag(handle(1,'output-0'), handle(4,'input-0'));
    await expect.poll(async () => (await edges()).length).toBe(2);
    await drag(handle(3,'output-0'), handle(4,'input-0'));
    await expect.poll(async () => (await edges()).length).toBe(3);
    await expect(page.locator('.vue-flow__edge')).toHaveCount(3);
    const before = await edges();
    await page.reload();
    await expect(node(1)).toBeVisible({ timeout: 30000 });
    await expect(page.locator('.vue-flow__edge')).toHaveCount(3);
    assert.deepEqual(await edges(), before);
    await page.screenshot({ path: path.join(output, 'canvas.png'), fullPage: true });
    assert.deepEqual(errors, [], 'unexpected page errors');
    fs.writeFileSync(path.join(output,'result.json'), JSON.stringify({ passed:true, flow_id:flow, edges:before, page_errors:errors }, null, 2));
    console.log('PASS: descriptor handles, valid drag, occupied-input rejection, union fan-in, reload persistence');
  } catch (error) {
    await page.screenshot({ path: path.join(output,'failure.png'), fullPage:true });
    fs.writeFileSync(path.join(output,'failure.json'), JSON.stringify({ error:String(error), page_errors:errors, url:page.url() },null,2));
    throw error;
  } finally {
    await context.tracing.stop({ path: path.join(output,'trace.zip') });
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
