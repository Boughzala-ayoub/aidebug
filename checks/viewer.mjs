/** Real CLI + served, packaged UI. Needs puppeteer-core and an installed Chromium. */
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const exec = promisify(execFile), require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const option = (name, fallback) => {const index=process.argv.indexOf(name);return index<0?fallback:process.argv[index+1];};
const cli = path.resolve(option('--cli', '.venv/Scripts/aidebug.exe'));
const puppeteer = require(path.resolve(option('--puppeteer', '.tools/viewer-check/node_modules/puppeteer-core')));
const chrome = option('--browser', ['C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','/usr/bin/google-chrome','/usr/bin/chromium','/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find(p=>fs.existsSync(p)));
assert(chrome, 'Pass --browser with your Chromium executable');
const workspace = fs.mkdtempSync(path.join(os.tmpdir(),'aidebug-viewer-'));
const artifacts = path.join(root,'checks/artifacts/viewer'); fs.mkdirSync(artifacts,{recursive:true});
const results={passed:[],screenshots:[],transcript:[]}, started=Date.now();
let browser, page, viewer;
async function call(...args) {
  let result;
  try {result=await exec(cli,['--workspace',workspace,'--timeout','15',...args],{windowsHide:true,timeout:27000});}
  catch(error) {throw new Error(JSON.stringify({args,stdout:error.stdout,stderr:error.stderr}));}
  assert.equal(result.stderr,''); const value=JSON.parse(result.stdout); assert.equal(value.ok,true,JSON.stringify(value));
  results.transcript.push({command:args,response:args[0]==='view'?{ok:true,viewer:value.viewer}:value}); return value;
}
const wait = predicate => page.waitForFunction(predicate,{timeout:12000});
async function shotViews(name, withMenu=false) {
  for (const width of [320,375,600,768,850,1024,1141,1280]) {
    await page.setViewport({width,height:850});
    if (withMenu) {
      if (width<850) {
        if (await page.$eval('#menu',el=>el.getAttribute('aria-expanded')==='false')) await page.click('#menu');
      }
    }
    await page.evaluate(()=>document.fonts.ready);
    const overflow=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:document.documentElement.clientWidth,
      offenders:[...document.querySelectorAll('body *')].filter(el=>el.getBoundingClientRect().right>innerWidth+1).slice(0,8).map(el=>el.tagName+'.'+el.className)}));
    assert(overflow.page<=overflow.viewport,JSON.stringify({name,width,...overflow}));
    // Every visible control must stay inside the horizontal viewport.
    const hiddenControls=await page.evaluate(()=>[...document.querySelectorAll('button,input,select,summary')].filter(el=>{
      const r=el.getBoundingClientRect();return r.width && r.height && (r.left<0 || r.right>innerWidth+1);
    }).map(el=>el.id || el.textContent));
    assert.deepEqual(hiddenControls,[],JSON.stringify({name,width,hiddenControls}));
    const file=`${name}-${width}.png`; await page.screenshot({path:path.join(artifacts,file),fullPage:true});results.screenshots.push(file);
    if (withMenu && width<850) await page.click('#menu');
  }
  console.log('PASS view',name);
}
async function api(route, auth=true, options={}) {
  const url=new URL(viewer.url), token=new URLSearchParams(url.hash.slice(1)).get('token');
  return fetch(url.origin+route,{...options,headers:{...(auth?{Authorization:'Bearer '+token}:{}),...options.headers}});
}
try {
  viewer=await call('view');
  assert.equal((await fetch(new URL(viewer.url).origin)).status,200,'Viewer must be serving before browser checks');
  assert.equal((await api('/api/sessions',false)).status,401);
  assert.equal((await api('/api/sessions',false,{headers:{Authorization:'Bearer é'}})).status,401);
  assert.equal((await api('/api/sessions',true,{headers:{Origin:'https://unrelated.example'}})).status,403);
  const foreignHost = await new Promise((resolve,reject)=>{
    const req=http.request(new URL('/api/sessions',viewer.url),{headers:{Host:'unrelated.example'}},response=>{response.resume();resolve(response.statusCode);});
    req.on('error',reject);req.end();
  });
  assert.equal(foreignHost,403);
  assert.equal((await api('/api/sessions/../../session.json/events')).status,404);
  assert.equal((await api('/api/continue',true,{method:'POST'})).status,404);
  results.passed.push('Viewer authentication, local origin/host restrictions, no DAP control route');
  browser=await puppeteer.launch({executablePath:chrome,headless:true,args:['--no-first-run'],handleSIGINT:false});
  const context=await browser.createBrowserContext(); page=await context.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(viewer.url,{waitUntil:'domcontentloaded'}); await wait(()=>document.querySelector('#connection').textContent==='Connected locally');
  assert.equal(await page.evaluate(()=>location.hash),'','Token removed from visible URL');
  await shotViews('empty',true);
  const fixture=path.join(workspace,'checkout.py');
  const original='def average_price(orders):\n    prices = [order["price"] for order in orders if order["paid"]]\n    count = len(prices)\n    count -= len(orders)\n    return sum(prices) / count\n\n\norders = [{"price": 10, "paid": True}, {"price": 20, "paid": True}]\nprint(average_price(orders))\n';
  fs.writeFileSync(fixture,original);
  const launched=await call('launch','--run','--uncaught','--breakpoint','checkout.py:4','checkout.py');
  assert.equal(launched.state,'stopped');
  await wait(()=>document.querySelector('#program').textContent==='checkout.py' && document.querySelector('#variables').textContent.includes('count'));
  assert.match(await page.$eval('#variables',el=>el.textContent),/2/);
  const prices=launched.locals.items.find(v=>v.name==='prices'); await call('variables',prices.handle);
  await wait(()=>document.querySelector('#variables').textContent.includes('Recorded children'));
  await page.$$eval('#variables details',els=>els.forEach(el=>el.open=true));
  await shotViews('live-expanded',true);
  const stepped=await call('step','over');assert.equal(stepped.locals.items.find(v=>v.name==='count').value,'0');
  await wait(()=>document.querySelector('.variable.changed')?.textContent.includes('count'));
  await call('note','The two paid orders produce count = 0. Subtracting all orders is the cause of the division failure.');
  await wait(()=>document.querySelector('#note-text').textContent.includes('division failure'));
  await shotViews('live-note');
  const stoppedGeneration=stepped.generation;
  await page.click('#previous');assert.equal(await page.$eval('#mode',el=>el.textContent),'Replay');
  await page.click('#follow');await wait(()=>document.querySelector('#mode').textContent==='Following live');
  assert.equal((await call('status')).generation,stoppedGeneration,'Viewer navigation must not resume execution');
  results.passed.push('Live CLI commands, inspected values and children, value changes, agent notes, observer isolation');
  assert.equal((await call('continue')).reason,'exception');await wait(()=>document.querySelector('#timeline').textContent.includes('Paused: exception'));
  const malicious='<img src=x onerror="window.viewerInjected=true">';await call('note',malicious);
  await wait(()=>document.querySelector('#note-text').textContent.includes('<img'));
  assert.equal(await page.evaluate(()=>window.viewerInjected),undefined);
  assert.equal(await page.$('#note-text img'),null);
  fs.writeFileSync(fixture,original.replace('count -= len(orders)','count = len(prices)'));
  await call('note','Fixed the divisor to use the number of paid orders. The current failed run still uses its original code; the next launch will verify the fix.');
  await wait(()=>document.querySelector('#changes').textContent.includes('count = len(prices)'));
  await page.click('[data-tab="changes"]');await shotViews('source-changes');
  await page.click('[data-tab="details"]');await shotViews('command');
  await page.click('[data-tab="source"]');
  await wait(()=>document.querySelector('#source-hint').textContent.includes('Later edits are in Changes'));
  assert.match(await page.$eval('#code',el=>el.textContent),/count -= len\(orders\)/,'Runtime values must stay paired with the original inspected source');
  await call('continue');await call('stop');
  await wait(()=>document.querySelector('#state').textContent==='Closed');
  assert(fs.existsSync(path.join(workspace,'.aidebug/recordings',launched.session+'.jsonl')));
  assert(!fs.existsSync(path.join(workspace,'.aidebug/session.json')));
  results.passed.push('Exception observation, safe rendering of target text, captured source changes, persistent recording after stop');
  const fixed=await call('launch','--run','checkout.py');assert.equal(fixed.exit_code,0);
  await call('note','Verified: the corrected average is 15.0 and the process exits successfully.');await call('stop');
  await page.waitForFunction(()=>document.querySelectorAll('.session-button').length===2,{timeout:12000});
  await page.setViewport({width:1280,height:850});
  await page.locator(`[data-session="${fixed.session}"]`).click();
  await wait(()=>document.querySelector('#output').textContent.includes('15.0'));
  await shotViews('successful-replay');
  await page.locator(`[data-session="${launched.session}"]`).click();
  await wait(()=>document.querySelector('#state').textContent==='Closed' && document.querySelector('#changes').textContent.includes('count = len(prices)'));
  const countTwo=await page.evaluate(()=>{
    const slider=document.querySelector('#position'); slider.value='0';slider.dispatchEvent(new Event('input',{bubbles:true}));return slider.max;
  });
  assert(Number(countTwo)>10);
  await page.click('#play');await wait(()=>document.querySelector('#play').textContent==='Pause replay');
  await page.click('#play');assert.equal(await page.$eval('#play',el=>el.textContent),'Play replay');
  await page.select('#speed','2');
  await page.$eval('#position',el=>{el.value=el.max;el.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.select('#output-channel','stderr');await shotViews('exception-replay',true);
  results.passed.push('Multiple saved sessions, replay play/pause/scrub/speed, original source history and successful rerun');
  await call('view','--stop');viewer=await call('view');
  const fresh=await browser.createBrowserContext();const replay=await fresh.newPage();
  await replay.goto(viewer.url,{waitUntil:'domcontentloaded'});await replay.waitForSelector('.session-button');
  assert.equal((await api('/api/sessions').then(r=>r.json())).sessions.length,2);
  await fresh.close();
  results.passed.push('Viewer restart reopens recordings without a debug session');
  // Exercise the recorder's documented oversized-observation shape in the UI.
  const limited='ffffffffffffffff';
  fs.writeFileSync(path.join(workspace,'.aidebug/recordings',limited+'.jsonl'),[
    {seq:1,kind:'session',session:limited,program:'bounded-recording.py',language:'python',target:'process',time:Date.now()/1000,elapsed:0},
    {seq:2,kind:'command',command:'pause',arguments:{command:'pause'},time:Date.now()/1000,elapsed:0.5},
    {seq:3,kind:'result',truncated:true,message:'Observation exceeded 128 KiB',time:Date.now()/1000,elapsed:1},
    {seq:4,kind:'end',time:Date.now()/1000,elapsed:2}
  ].map(value=>JSON.stringify(value)).join('\n')+'\n');
  await page.goto(viewer.url,{waitUntil:'domcontentloaded'});
  await page.locator(`[data-session="${limited}"]`).click();
  await wait(()=>document.querySelector('#observation-error').textContent.includes('too large'));
  assert.match(await page.$eval('#timeline',el=>el.textContent),/Pause execution/);
  await shotViews('truncated-recording');
  const restricted=await browser.createBrowserContext();const noToken=await restricted.newPage();
  await noToken.goto(new URL(viewer.url).origin,{waitUntil:'domcontentloaded'});
  assert.match(await noToken.$eval('#notice',el=>el.textContent),/aidebug view/);
  const originalPage=page;page=noToken;await shotViews('access-required',true);page=originalPage;await restricted.close();
  assert.deepEqual(errors,[]);
  results.passed.push('No browser JavaScript errors; all views and open navigation pass eight viewport widths');
  console.log('PASS',results.passed.length,'viewer groups;',results.screenshots.length,'screenshots');
} catch(error) {
  results.failure=error.stack; if(page) await page.screenshot({path:path.join(artifacts,'failure.png'),fullPage:true}).catch(()=>{});
  throw error;
} finally {
  if(browser) await browser.close();
  await exec(cli,['--workspace',workspace,'stop'],{windowsHide:true,timeout:15000}).catch(()=>{});
  await exec(cli,['--workspace',workspace,'view','--stop'],{windowsHide:true,timeout:15000}).catch(()=>{});
  results.seconds=(Date.now()-started)/1000;
  let report=JSON.stringify(results,null,2);
  for (const [value,label] of [[workspace,'<workspace>'],[root,'<repo>'],[os.homedir(),'<home>']]) report=report.replaceAll(JSON.stringify(value).slice(1,-1),label);
  fs.writeFileSync(path.join(root,'checks/artifacts/viewer-results.json'),report);
  fs.writeFileSync(path.join(root,'checks/viewer-results.json'),JSON.stringify({
    passed:results.passed.length,failed:results.failure?1:0,groups:results.passed,screenshot_count:results.screenshots.length
  },null,2)+'\n');
  // This is the exact temporary workspace created above, never a user-supplied path.
  fs.rmSync(workspace,{recursive:true,force:true,maxRetries:5,retryDelay:150});
}
