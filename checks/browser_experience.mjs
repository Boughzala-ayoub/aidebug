// Real browser interaction, inline-module scopes, source mapping and termination.
// node checks/browser_experience.mjs --cli PATH --adapter PATH --puppeteer MODULE_DIR
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createRequire} from 'node:module';
import {mkdtemp, writeFile, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {pathToFileURL} from 'node:url';
const options = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, i, all) => i % 2 ? pairs : [...pairs, [value.slice(2), all[i + 1]]], []));
const cli = path.resolve(options.cli);
const adapter = path.resolve(options.adapter);
const workspace = await mkdtemp(path.join(tmpdir(), 'aidebug-browser-experience-'));
const run = promisify(execFile);
const puppeteer = options.puppeteer ? createRequire(import.meta.url)(path.resolve(options.puppeteer)) : null;
const html = `<!doctype html><button id="start">Start</button><output id="result"></output>
<script type="module">
const renderer = {info: {memory: {geometries: 731}}};
document.querySelector('#start').addEventListener('click', () => {
  const local = 42;
  document.querySelector('#result').textContent = renderer.info.memory.geometries + local;
});
window.schedule = function schedule() {
  requestAnimationFrame(function paint() {
    const element = document.body;
    debugger; // UTF-8 source: — ✽
    element.dataset.painted = 'yes';
  });
};
</script><script src="./sibling.js"></script>`;
await writeFile(path.join(workspace, 'index.html'), html);
const sibling = "document.body.dataset.sibling = 'loaded';";
await writeFile(path.join(workspace, 'sibling.js'), sibling);
const server = http.createServer((req, res) => {
  const script = req.url.endsWith('/sibling.js');
  res.setHeader('Content-Type', script ? 'text/javascript' : 'text/html'); res.end(script ? sibling : html);
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const groups = [];
let failure = null;
async function call(...args) {
  let output;
  try { output = await run(cli, ['--workspace', workspace, ...args], {timeout: 45000, windowsHide: true}); }
  catch (error) { if (!error.stdout) throw error; output = error; }
  assert.equal(output.stderr, '');
  const result = JSON.parse(output.stdout);
  assert.ok(output.stdout.length <= 65536);
  return result;
}
async function until(check) {
  const end = Date.now() + 35000;
  while (Date.now() < end) { const result = await check(); if (result) return result; await new Promise(r => setTimeout(r, 100)); }
  throw new Error('Condition timed out');
}
async function stop() {
  for (let i = 0; i < 3; i++) {
    const result = await call('stop');
    if (result.ok) {
      const pid = result.processes?.helper;
      if (pid) await until(() => { try { process.kill(pid, 0); return false; } catch (error) { return error.code === 'ESRCH'; } });
      return;
    }
    console.log('Cleanup retry:', result.error?.code);
    if (result.error?.code !== 'cleanup_failure') throw new Error(JSON.stringify(result));
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error('Browser cleanup failed');
}
async function connect(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let id = 0;
  const pending = new Map();
  socket.onmessage = ({data}) => {
    const message = JSON.parse(data);
    const task = pending.get(message.id);
    if (task) { pending.delete(message.id); clearTimeout(task.timer); message.error ? task.reject(new Error(JSON.stringify(message.error))) : task.resolve(message.result); }
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const key = ++id;
    const timer = setTimeout(() => { pending.delete(key); reject(new Error(`CDP timeout: ${method}`)); }, 10000);
    pending.set(key, {resolve, reject, timer});
    socket.send(JSON.stringify({id: key, method, params}));
  });
  return {socket, send};
}
try {
  for (const suffix of ['/', '/index.html', '/custom/']) {
    const mapping = suffix === '/custom/' ? ['--url-path', '/custom/=index.html'] : [];
    const launched = await call('launch', '--language', 'javascript', '--target', 'browser', '--adapter', adapter,
      '--no-wait', '--run', '--uncaught', ...mapping, '--breakpoint', 'index.html:6', base + suffix);
    assert.equal(launched.ok, true, JSON.stringify(launched));
    await until(async () => {
      const status = await call('status');
      if (status.state === 'failed') throw new Error(JSON.stringify(status));
      return status.phase === 'ready';
    });
    const sources = await call('sources');
    assert.equal(sources.ok, true, JSON.stringify(sources));
    assert.ok(sources.sources.items.some(source => source.path === path.join(workspace, 'index.html')));
    await until(async () => (await call('sources')).sources.items.some(source => source.path === path.join(workspace, 'sibling.js')));
    const connection = await until(async () => { const r = await call('automation'); return r.ok && r.automation; });
    const wrong = new URL(connection.websocket_url); wrong.pathname = '/wrong';
    await assert.rejects(connect(wrong.href));
    const cdp = await connect(connection.websocket_url);
    const {root} = await cdp.send('DOM.getDocument');
    const {nodeId} = await cdp.send('DOM.querySelector', {nodeId: root.nodeId, selector: '#start'});
    const {model} = await cdp.send('DOM.getBoxModel', {nodeId});
    const x = (model.content[0] + model.content[4]) / 2, y = (model.content[1] + model.content[5]) / 2;
    await cdp.send('Input.dispatchMouseEvent', {type: 'mousePressed', x, y, button: 'left', clickCount: 1});
    const released = cdp.send('Input.dispatchMouseEvent', {type: 'mouseReleased', x, y, button: 'left', clickCount: 1});
    const stopped = await call('--timeout', '5', 'wait');
    assert.equal(stopped.state, 'stopped', JSON.stringify(stopped));
    const inspection = await call('inspect');
    assert.equal(path.resolve(inspection.location.file), path.join(workspace, 'index.html'));
    assert.ok(inspection.location.source.some(line => line.line === 6));
    assert.ok(inspection.scopes.items.some(scope => scope.name === 'Module'));
    let found;
    for (const scope of inspection.scopes.items) {
      const value = await call('value', '--scope', scope.handle, 'renderer.info.memory.geometries');
      if (value.ok) { found = value.value.value; break; }
    }
    assert.equal(found, '731');
    await call('--timeout', '0.1', 'continue');
    await released;
    if (suffix === '/') {
      await cdp.send('Runtime.evaluate', {expression: 'window.schedule()'});
      assert.equal((await call('--timeout', '5', 'wait')).state, 'stopped');
      const asyncStop = await call('inspect');
      assert.ok(asyncStop.location.source.some(line => line.text.includes('— ✽')), 'UTF-8 source excerpt was changed');
      assert.equal(asyncStop.stack.frames[0].scopes_available, true);
      assert.equal(asyncStop.stack.frames[0].async, false);
      const separator = asyncStop.stack.frames.find(frame => frame.hint === 'label');
      assert.ok(separator);
      assert.equal(separator.handle, null);
      assert.equal(separator.scopes_available, false);
      const historical = asyncStop.stack.frames.find(frame => frame.name === 'schedule');
      assert.ok(historical, JSON.stringify(asyncStop.stack));
      assert.equal(historical.async, true);
      assert.equal(historical.scopes_available, false);
      const historicalScopes = await call('scopes', '--frame', historical.handle);
      assert.equal(historicalScopes.error?.code, 'frame_not_inspectable');
      assert.match(historicalScopes.error.message, /historical async frame/);
      assert.equal((await call('inspect', '--frame', historical.handle)).error?.code, 'frame_not_inspectable');
      assert.equal((await call('scopes', '--frame', asyncStop.stack.frames[0].handle)).ok, true);
      const local = asyncStop.locals;
      const missing = await call('value', '--scope', local.handle, 'element.className');
      assert.equal(missing.error?.code, 'value_not_found');
      assert.match(missing.error.message, /Path component 2.*variables .*inherited properties/);
      const projected = await call('--fields', 'stack.frames[].name,stack.frames[].handle', 'inspect');
      assert.equal(projected.ok, true);
      assert.ok(projected.stack.frames.some(frame => frame.name === 'paint'));
      assert.ok(!('session' in projected));
      await call('--timeout', '0.1', 'continue');
      assert.equal((await cdp.send('Runtime.evaluate', {expression: 'document.body.dataset.painted'})).result.value, 'yes');
      groups.push('async ancestry labels, live scopes, actionable property errors, field projection and UTF-8 source excerpts');
      console.log('PASS', groups.at(-1));
    }
    cdp.socket.close();
    if (puppeteer) {
      const {connectPuppeteer} = await import(pathToFileURL(connection.client_module).href);
      const browser = await connectPuppeteer(puppeteer, connection);
      try {
        const pages = await browser.pages();
        assert.equal(pages.length, 1);
        assert.equal(await pages[0].$eval('body', element => element.dataset.sibling), 'loaded');
        assert.equal(await pages[0].$eval('#result', element => element.textContent), '773');
        const screenshot = await pages[0].screenshot();
        assert.ok(screenshot.length > 100);
        const click = pages[0].click('#start');
        assert.equal((await call('--timeout', '5', 'wait')).state, 'stopped');
        await call('--timeout', '0.1', 'continue');
        await click;
        console.log('PASS Puppeteer controls the same page while aidebug stops and resumes it');
      } finally { await browser.disconnect(); }
    }
    const record = await readFile(path.join(workspace, '.aidebug/recordings', launched.session + '.jsonl'), 'utf8');
    assert.ok(!record.includes(connection.websocket_url), 'Automation credentials leaked into recordings');
    assert.ok(!record.includes(new URL(connection.websocket_url).pathname.slice(1)), 'Automation secret path leaked into recordings');
    // A page closing is a debugging termination, not proof of process exit.
    const closing = await connect(connection.websocket_url);
    await closing.send('Page.close').catch(() => {});
    closing.socket.close();
    const ended = await call('--timeout', '5', 'wait');
    assert.equal(ended.state, 'terminated', JSON.stringify(ended));
    assert.equal(ended.target_exit_confirmed, false);
    assert.ok(ended.diagnostics.events.some(event => event.event === 'terminated'));
    await stop();
    groups.push(`same-page automation, module values, source/excerpt mapping, credential protection and unknown termination: ${suffix}`);
    console.log('PASS', groups.at(-1));
  }
} catch (error) {
  failure = error;
  throw error;
} finally {
  await writeFile(new URL('browser-experience-results.json', import.meta.url), JSON.stringify({
    passed: groups.length, failed: failure ? 1 : 0, groups, node: process.version,
    puppeteer: Boolean(puppeteer), platform: process.platform,
  }, null, 2) + '\n');
  await stop();
  server.close();
  await rm(workspace, {recursive: true, force: true, maxRetries:20, retryDelay:100});
}
console.log('PASS', groups.length, 'browser experience groups');
