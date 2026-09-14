/* Read-only live observations and replay. No browser request can control DAP. */
'use strict';
const $ = id => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
const token = fragment.get('token') || sessionStorage.getItem('aidebug-viewer-token');
if (token) sessionStorage.setItem('aidebug-viewer-token', token);
history.replaceState(null, '', location.pathname);
let recordings = [], selected = null, log = [], cursor = -1, offset = 0;
let following = true, playing = false, timer = null, fetching = false, lastList = 0, selectedFrame = null;
let tab = 'source', lastRenderKey = '', selectionVersion = 0, listTruncated = false;
const expanded = new Set();
const basename = value => String(value || '').split(/[\\/]/).pop() || 'Session';
const shortTime = value => Number(value || 0).toFixed(1) + 's';
const text = (tag, value, className) => { const el = document.createElement(tag); el.textContent = value; if (className) el.className = className; return el; };
function notice(message) { $('notice').textContent = message; $('notice').hidden = !message; }
function button(label, action, className = '') { const el = text('button', label, className); el.type = 'button'; el.addEventListener('click', action); return el; }
async function api(path) {
  const response = await fetch(path, {headers: {Authorization: 'Bearer ' + token}, signal: AbortSignal.timeout(4000)});
  if (!response.ok) {
    if (response.status === 401) throw new Error('Open the URL returned by aidebug view to access your recordings.');
    const body = await response.json(); throw new Error(body.error || 'The viewer could not read this recording.');
  }
  return response.json();
}
function meta() { return recordings.find(item => item.id === selected); }
function menu(close = false) {
  const opened = !close && !$('sidebar').classList.contains('open');
  $('sidebar').classList.toggle('open', opened); $('menu').setAttribute('aria-expanded', String(opened));
}
function listSessions() {
  $('record-count').textContent = recordings.length;
  const content = document.createDocumentFragment();
  for (const item of recordings) {
    const el = button('', () => select(item.id), 'session-button' + (item.id === selected ? ' selected' : ''));
    el.dataset.session = item.id; el.setAttribute('aria-pressed', String(item.id === selected));
    const title = text('strong', basename(item.program));
    if (item.active) title.prepend(text('span', '', 'live-dot'));
    el.append(title, text('small', `${item.language || 'Program'} · ${new Date(item.time * 1000).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit'})}`));
    el.append(text('small', item.active ? 'Live session' : item.limited ? 'Recording limit reached' : item.ended ? (item.exit_code != null ? 'Recorded · exit '+item.exit_code : 'Recorded session') : 'Interrupted recording'));
    content.append(el);
  }
  if (!recordings.length) content.append(text('p', 'Your next debugging session will appear here.', 'sidebar-hint'));
  $('sessions').replaceChildren(content);
}
function select(id) {
  if (id === selected) { menu(true); return; }
  pause(); selected = id; log = []; offset = 0; cursor = -1; selectedFrame = null;
  following = Boolean(meta()?.active); expanded.clear(); selectionVersion++; lastRenderKey = '';
  listSessions(); menu(true); render(); poll();
}
function pause() { playing = false; clearTimeout(timer); $('play').textContent = 'Play replay'; }
function seek(index) {
  following = false; pause(); cursor = Math.max(0, Math.min(index, log.length - 1)); selectedFrame = null;
  render();
}
function play() {
  if (playing) { pause(); render(); return; }
  if (!log.length) return;
  following = false; if (cursor >= log.length - 1) cursor = 0;
  playing = true; selectedFrame = null; render(); schedule();
}
function schedule() {
  if (!playing) return;
  if (cursor >= log.length - 1) { pause(); render(); return; }
  const gap = (log[cursor + 1].elapsed - log[cursor].elapsed) * 1000;
  timer = setTimeout(() => { if (!playing) return; cursor++; selectedFrame = null; render(); schedule(); },
                     Math.max(250, Math.min(2500, gap)) / Number($('speed').value));
}
function derive() {
  const state = {state:'waiting', generation:null, reason:null, exit_code:null, snapshot:null, previous:null,
    sources:new Map(), changes:new Map(), frames:new Map(), frameSources:new Map(), expanded:new Map(), output:{stdout:'',stderr:'',adapter:''},
    outputCut:{}, note:null, error:null, command:null, limited:false, observationCut:false, closed:false};
  const previousFrames = new Map();
  for (let i = 0; i <= cursor; i++) {
    const event = log[i]; if (!event) continue;
    if (event.kind === 'command') state.command = event;
    if (event.kind === 'event' || event.kind === 'error') {
      if (event.state) state.state = event.state;
      if ('generation' in event) state.generation = event.generation;
      if ('reason' in event) state.reason = event.reason;
      if ('exit_code' in event) state.exit_code = event.exit_code;
      if (event.error) state.error = event.error;
    }
    if (event.kind === 'note') state.note = event.text;
    if (event.kind === 'source' && typeof event.text === 'string') {
      const old = state.sources.get(event.file);
      if (old && old.text !== event.text) state.changes.set(event.file, {before:old.text, after:event.text});
      state.sources.set(event.file, event);
    }
    if (event.kind === 'output' && event.category in state.output && typeof event.text === 'string') {
      const value = state.output[event.category] + event.text;
      state.outputCut[event.category] ||= event.truncated || value.length > 8192;
      state.output[event.category] = value.slice(-8192);
    }
    if (event.kind === 'result' && event.response) {
      const r = event.response;
      for (const key of ['state','reason','exit_code','generation']) if (key in r) state[key] = r[key];
      state.error = r.ok === false ? r.error : null;
      if (r.location) {
        const frame = r.stack?.frames?.find(item => item.handle === r.location.frame);
        const key = r.location.file + ':' + (frame?.name || 'frame');
        state.previous = previousFrames.get(key);
        state.snapshot = r; state.frames.set(r.location.frame, r);
        state.frameSources.set(r.location.frame, state.sources.get(r.location.file));
        previousFrames.set(key, r);
      }
      if (r.variables && event.handle) {
        const old = state.expanded.get(event.handle) || {items:[]};
        const items = old.items.slice();
        for (let j=0; j<(r.variables.items || []).length; j++) items[(r.variables.start || 0)+j] = r.variables.items[j];
        state.expanded.set(event.handle, {...r.variables, items});
      }
      if (r.recording?.truncated) state.limited = true;
    }
    if (event.kind === 'limit') state.limited = true;
    if (event.truncated && event.kind !== 'limit' && !event.text && !event.response && !event.arguments) state.observationCut = true;
    if (event.kind === 'end') { state.closed = true; state.state = 'exited'; }
  }
  return state;
}
function label(event) {
  if (event.truncated && !event.text && !event.response && !event.arguments) return 'Truncated observation';
  if (event.kind === 'session') return 'Session started';
  if (event.kind === 'command') {
    const a = event.arguments || {};
    return {launch:'Launch program',continue:'Continue execution',pause:'Pause execution',inspect:'Inspect runtime values',variables:'Expand a value',
      status:'Check session status',output:'Read program output',stop:'Stop session',note:'Add an explanation',
      step:'Step ' + (a.kind || ''),break:(a.action === 'remove' ? 'Remove' : 'Set') + ' breakpoints',
      exceptions:'Exceptions ' + (a.mode || '')}[event.command] || event.command;
  }
  if (event.kind === 'result') return event.response?.ok === false ? 'Command failed' : event.response?.timed_out ? 'Deadline reached; session remains available' : (event.command === 'step' ? 'Step' : event.command[0].toUpperCase()+event.command.slice(1)) + ' result';
  if (event.kind === 'event') return {stopped:'Paused: '+(event.reason || 'debugger stop'),continued:'Program running',exited:'Program exited',terminated:'Target terminated'}[event.event] || event.event;
  return {note:'Agent note',source:'Source changed',output:'Program output',error:'Adapter failure',limit:'Recording limit reached',end:'Session closed'}[event.kind] || event.kind;
}
function timeline() {
  const steps = log.map((event,index)=>({event,index})).filter(({event}) => event.kind !== 'output' && !(event.kind === 'source' && !event.changed));
  let current = steps.findLastIndex(item => item.index <= cursor);
  const begin = Math.max(0, current - 30), visible = steps.slice(begin, begin + 70);
  const content = document.createDocumentFragment();
  if (begin > 0) { const li = document.createElement('li'); li.append(button('Earlier observations', () => seek(steps[Math.max(0,begin - 30)].index))); content.append(li); }
  for (const {event,index} of visible) {
    const li = document.createElement('li');
    const active = index === steps[current]?.index;
    const el = button('', () => { seek(index); menu(true); }, active ? 'selected' : '');
    el.dataset.event = index; el.setAttribute('aria-current', active ? 'step' : 'false');
    const icon = event.kind === 'command' ? '↳' : event.kind === 'note' ? '✎' : event.kind === 'error' ? '!' : '•';
    const title = text('span', label(event), 'event-title'); title.append(text('small', shortTime(event.elapsed)));
    el.append(text('span',icon,'event-icon'),title); li.append(el); content.append(li);
  }
  if (begin + 70 < steps.length) { const li = document.createElement('li'); li.append(button('Later observations', () => seek(steps[begin + 70].index))); content.append(li); }
  $('timeline').replaceChildren(content); $('activity-count').textContent = steps.length;
  const active = $('timeline').querySelector('[aria-current="step"]');
  if (active) {
    const row = active.getBoundingClientRect(), bounds = $('timeline').getBoundingClientRect();
    if (row.bottom > bounds.bottom) $('timeline').scrollTop += row.bottom - bounds.bottom;
    else if (row.top < bounds.top) $('timeline').scrollTop -= bounds.top - row.top;
  }
}
function syntax(code) {
  const result = document.createDocumentFragment();
  const pattern = /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|#[^\n]*|\/\/[^\n]*|\b(?:def|return|if|else|for|in|raise|import|from|class|try|except|with|as|True|False|None|const|let|function|throw|new|fn|pub|mut|use|match|impl|true|false|null)\b)/g;
  let last = 0;
  for (const match of code.matchAll(pattern)) {
    result.append(document.createTextNode(code.slice(last,match.index)));
    result.append(text('span',match[0],/^["']/.test(match[0])?'tok-string':/^(#|\/\/)/.test(match[0])?'tok-comment':'tok-keyword'));
    last = match.index + match[0].length;
  }
  result.append(document.createTextNode(code.slice(last))); return result;
}
function source(state, snapshot, frame) {
  const location = frame ? {file:frame.file,line:frame.line} : snapshot?.location;
  $('source-file').textContent = location?.file || 'Source appears when the agent inspects a stop';
  $('line-label').textContent = location?.line ? 'Line '+location.line : '';
  const latest = state.sources.get(location?.file);
  const inspected = state.frameSources.get(snapshot?.location?.frame);
  const recorded = inspected?.file === location?.file ? inspected : latest;
  const changedSinceInspection = inspected && latest && inspected.text !== latest.text;
  let lines = [];
  if (recorded) {
    const all = recorded.text.split('\n'); const start = Math.max(0, Math.min((location?.line || 1)-14, all.length-28));
    lines = all.slice(start,start+30).map((value,i)=>({line:start+i+1,text:value}));
  } else lines = snapshot?.location && snapshot.location.file === location?.file ? snapshot.location.source || [] : [];
  const content = document.createDocumentFragment();
  for (const item of lines) {
    const row = document.createElement('div'); row.className = 'code-line' + (item.line === location?.line ? ' current' : '');
    if (item.line === location?.line) row.setAttribute('aria-label','Execution line '+item.line);
    const code = text('span','','code-text'); code.append(syntax(item.text.replace(/\r$/, '')));
    row.append(text('span',item.line,'line-number'),code); content.append(row);
  }
  if (!lines.length) content.append(text('p','No source was captured at this point.','panel-hint'));
  $('code').replaceChildren(content);
  $('source-hint').textContent = changedSinceInspection ? 'Showing the source from this inspection. Later edits are in Changes; these values belong to the earlier code.' : recorded?.truncated ? 'Source snapshot truncated at 24,000 characters.' : state.state !== 'stopped' && location ? 'Last inspected location. The program is not paused here now.' : 'Source captured with the observation, preserved for replay.';
  const changes = document.createDocumentFragment();
  for (const [file, change] of state.changes) {
    const block = text('div','','change-file'); block.append(text('h3',file));
    const before = change.before.split('\n'), after = change.after.split('\n'); let first=0, endBefore=before.length, endAfter=after.length;
    while (first<endBefore && first<endAfter && before[first]===after[first]) first++;
    while (endBefore>first && endAfter>first && before[endBefore-1]===after[endAfter-1]) { endBefore--; endAfter--; }
    for (let i=first;i<Math.min(endBefore,first+80);i++) block.append(text('div',`− ${i+1}  ${before[i]}`,'diff-line removed'));
    for (let i=first;i<Math.min(endAfter,first+80);i++) block.append(text('div',`+ ${i+1}  ${after[i]}`,'diff-line added'));
    if (endBefore-first>80 || endAfter-first>80) block.append(text('p','Change preview truncated to 80 lines per version.','muted'));
    changes.append(block);
  }
  if (!state.changes.size) changes.append(text('p','No source changes captured yet. The agent can use aidebug note after editing an inspected file to capture the new version.','muted'));
  $('changes').replaceChildren(changes);
}
function variables(state, snapshot) {
  const items = snapshot?.locals?.items || [], previous = new Map((state.previous?.locals?.items || []).map(v=>[v.name,v.value]));
  const content = document.createDocumentFragment();
  function row(item, depth=0, parents=new Set()) {
    const changed = depth === 0 && previous.has(item.name) && previous.get(item.name)!==item.value;
    const el = text('div','','variable'+(changed?' changed':''));
    const top = text('div','','variable-top'), name = text('span',item.name,'variable-name');
    if (changed) name.append(text('span','changed','change-label'));
    top.append(name,text('span',item.type || 'value','variable-type')); el.append(top,text('code',item.value,'variable-value'));
    if (item.truncated) el.append(text('small','Preview truncated','muted'));
    if (item.handle) {
      const details = document.createElement('details'); details.open = expanded.has(item.handle);
      const recorded = state.expanded.get(item.handle);
      details.append(text('summary', recorded ? 'Recorded children' : 'Children were not inspected'));
      if (recorded && depth<4 && !parents.has(item.handle)) {
        const children = text('div','','children');
        for (const child of recorded.items.filter(Boolean).slice(0,60)) children.append(row(child,depth+1,new Set([...parents,item.handle])));
        if (recorded.truncated || recorded.items.length>60) children.append(text('p','Only recorded children are shown; this list is truncated.','panel-hint'));
        details.append(children);
      } else details.append(text('p',recorded ? 'Nested preview depth reached.' : 'The viewer can replay only values that the agent expanded.','panel-hint'));
      details.addEventListener('toggle',()=>details.open?expanded.add(item.handle):expanded.delete(item.handle)); el.append(details);
    }
    return el;
  }
  for (const item of items) content.append(row(item));
  if (!items.length) content.append(text('p','No local values were inspected for this frame at this point.','no-values'));
  $('variables').replaceChildren(content); $('value-count').textContent=items.length;
  $('values-hint').textContent = snapshot?.locals?.truncated ? 'Recorded locals are truncated.' : state.state !== 'stopped' && items.length ? 'Last inspected values; these are not live while execution runs.' : 'Values inspected by the agent. Changes compare consecutive inspections.';
}
function render() {
  const item = meta(); $('empty').hidden = Boolean(item); $('workspace').hidden = !item;
  if (!item) { $('timeline').replaceChildren(); $('activity-count').textContent='0'; return; }
  const state = derive(), observed = state.snapshot;
  const frame = observed?.stack?.frames?.find(f=>f.handle===selectedFrame);
  const snapshot = frame ? state.frames.get(frame.handle) : observed;
  $('program').textContent=basename(item.program); $('program-path').textContent=item.program;
  $('language').textContent=(item.language || 'Program')+' / '+(item.target || 'process');
  $('session-date').textContent=new Date(item.time*1000).toLocaleString();
  $('state').textContent=state.closed?'Closed':state.state[0].toUpperCase()+state.state.slice(1);
  $('state').className='state '+state.state;
  $('mode').textContent=following && item.active?'Following live':playing?'Playing recording':'Replay';
  $('play').textContent=playing?'Pause replay':'Play replay'; $('play').disabled=!log.length;
  $('previous').disabled=cursor<=0; $('next').disabled=cursor>=log.length-1;
  $('follow').disabled=!item.active; $('follow').classList.toggle('active',following && item.active);
  $('follow').textContent=following && item.active?'Following live':'Follow live';
  $('position').max=Math.max(0,log.length-1); $('position').value=Math.max(0,cursor);
  $('position-label').textContent=`Observation ${cursor+1} of ${log.length}`; $('elapsed').textContent=shortTime(log[cursor]?.elapsed);
  $('annotation').hidden=!state.note; $('note-text').textContent=state.note || '';
  $('observation-error').hidden=!state.error && !state.limited && !state.observationCut;
  $('observation-error').textContent=state.error ? `${state.error.code}: ${state.error.message}` : state.limited ? 'Recording limit reached. Later observations were not saved.' : state.observationCut ? 'Some observations were too large to record in full. Truncation is marked in the timeline.' : '';
  source(state,snapshot || observed,frame); variables(state,snapshot);
  const stack=document.createDocumentFragment();
  for (const [i,f] of (observed?.stack?.frames || []).entries()) {
    const li=document.createElement('li'), el=button('',()=>{selectedFrame=f.handle;render();},f.handle===(selectedFrame || observed?.location?.frame)?'selected':'');
    el.dataset.frame=f.handle; const info=text('span','','frame-text'); info.append(text('strong',f.name),text('small',`${basename(f.file)}:${f.line}`));
    el.append(text('span',i,'frame-number'),info);li.append(el);stack.append(li);
  }
  if (!observed?.stack?.frames?.length) stack.append(text('li','The stack appears after an inspection.','panel-hint'));
  if (observed?.stack?.truncated) stack.append(text('li','Stack trace truncated.','panel-hint'));
  $('stack').replaceChildren(stack); $('stack-count').textContent=observed?.stack?.frames?.length || 0;
  const channel=$('output-channel').value;
  $('output').textContent=state.output[channel] || 'No '+channel+' output recorded at this point.';
  $('output-hint').textContent=state.outputCut[channel]?'Output truncated; showing the last 8,192 recorded characters.':'Captured separately from debugger commands.';
  $('details').textContent=JSON.stringify(log[cursor] || {},null,2);
  $('recording-info').textContent=`Session ${item.id}`+(state.exit_code!==null?` · Exit ${state.exit_code}`:'');
  timeline();
  for (const name of ['source','changes','details']) $(name+'-view').hidden=tab!==name;
}
async function poll() {
  if (fetching || !token) return;
  fetching=true;
  try {
    if (Date.now()-lastList>1500) {
      const response=await api('/api/sessions'); lastList=Date.now();
      const changed=JSON.stringify(response.sessions)!==JSON.stringify(recordings);
      recordings=response.sessions;
      if (changed) { listSessions(); lastRenderKey=''; }
      listTruncated = response.truncated;
      if (!selected && recordings.length) select((recordings.find(r=>r.active) || recordings[0]).id);
      if (following && recordings.some(r=>r.active && r.id!==selected)) select(recordings.find(r=>r.active).id);
    }
    if (selected) {
      const version=selectionVersion, identity=selected;
      for (let page=0;page<4;page++) {
        const response=await api(`/api/sessions/${identity}/events?after=${offset}`);
        if (version!==selectionVersion) break;
        log.push(...response.events); offset=response.next;
        if (following || cursor<0) cursor=log.length-1;
        if (!response.more) break;
      }
    }
    $('connection').textContent='Connected locally';
    notice(listTruncated ? 'Showing the 100 most recent recordings. Older files remain in .aidebug/recordings.' : '');
    const key=selected+':'+log.length+':'+cursor+':'+Boolean(meta()?.active);
    if (key!==lastRenderKey) { render(); lastRenderKey=key; }
  } catch (error) {
    $('connection').textContent='Connection unavailable'; notice(error.message+' Existing observations remain available for replay.');
  } finally { fetching=false; }
}
$('menu').addEventListener('click',()=>menu());
$('previous').addEventListener('click',()=>seek(cursor-1)); $('next').addEventListener('click',()=>seek(cursor+1));
$('position').addEventListener('input',event=>seek(Number(event.target.value)));
$('play').addEventListener('click',play);
$('speed').addEventListener('change',()=>{if(playing){clearTimeout(timer);schedule();}});
$('follow').addEventListener('click',()=>{pause();following=true;selectedFrame=null;cursor=log.length-1;render();});
$('output-channel').addEventListener('change',render);
for (const el of document.querySelectorAll('[data-tab]')) el.addEventListener('click',()=>{
  tab=el.dataset.tab;
  for (const other of document.querySelectorAll('[data-tab]')) {other.classList.toggle('active',other===el);other.setAttribute('aria-pressed',String(other===el));}
  render();
});
if (!token) { $('connection').textContent='Access required'; notice('Run aidebug view and open its URL to access your recordings.'); }
else { poll(); setInterval(poll,500); }
