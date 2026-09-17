// Adapt js-debug's page-scoped CDP proxy to Puppeteer's ConnectionTransport.
// Only the already launched page is exposed. No browser attachment or new tabs.
export async function connectPuppeteer(puppeteer, automation) {
  const url = new URL(automation.websocket_url);
  if (url.protocol !== 'ws:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) {
    throw new Error('Expected a local aidebug CDP proxy');
  }
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { socket.close(); reject(new Error('CDP connection timed out')); }, 10000);
    socket.onopen = () => { clearTimeout(timer); resolve(); };
    socket.onerror = () => { clearTimeout(timer); reject(new Error('CDP connection failed')); };
  });
  let sequence = 0;
  const pending = new Map();
  const sessionId = 'aidebug-page';
  const transport = {
    onmessage: undefined, onclose: undefined,
    close() { socket.close(); },
    send(data) { dispatch(JSON.parse(data)).catch(() => {}); },
  };
  const emit = message => transport.onmessage?.(JSON.stringify(message));
  const request = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 30000);
    pending.set(id, {resolve, reject, timer});
    socket.send(JSON.stringify({id, method, params}));
  });
  socket.onmessage = ({data}) => {
    const message = JSON.parse(data);
    if (message.id) {
      const task = pending.get(message.id);
      if (!task) return;
      pending.delete(message.id); clearTimeout(task.timer);
      message.error ? task.reject(new Error(message.error.message)) : task.resolve(message.result);
    } else if (message.method && !message.method.startsWith('Target.')) {
      emit({...message, sessionId});
    }
  };
  socket.onclose = () => {
    for (const task of pending.values()) { clearTimeout(task.timer); task.reject(new Error('CDP connection closed')); }
    pending.clear(); transport.onclose?.();
  };
  let targetInfo;
  try {
    ({targetInfo} = await request('Target.getTargetInfo'));
    await request('JsDebug.subscribe', {events: ['*']});
  } catch (error) { socket.close(); throw error; }
  targetInfo = {...targetInfo, attached: true};
  let attached = false;
  function attach() {
    if (!attached) {
      attached = true;
      emit({method: 'Target.attachedToTarget', params: {sessionId, targetInfo, waitingForDebugger: false}});
    }
  }
  async function dispatch(message) {
    const {id, method, params = {}, sessionId: selected} = message;
    try {
      let result = {};
      if (selected && selected !== sessionId) throw new Error('Only the launched page is available');
      switch (method) {
        case 'Target.getBrowserContexts': result = {browserContextIds: []}; break;
        case 'Target.getTargets': result = {targetInfos: [targetInfo]}; break;
        case 'Target.getTargetInfo': result = {targetInfo}; break;
        case 'Target.setDiscoverTargets':
          if (params.discover) emit({method: 'Target.targetCreated', params: {targetInfo}});
          break;
        case 'Target.setAutoAttach': if (!selected && params.autoAttach) attach(); break;
        case 'Target.attachToTarget':
          if (params.targetId !== targetInfo.targetId) throw new Error('Only the launched page is available');
          attach(); result = {sessionId}; break;
        case 'Target.detachFromTarget': break;
        case 'Runtime.runIfWaitingForDebugger': break;
        default:
          if (method.startsWith('Target.') || method.startsWith('Debugger.') || method === 'Browser.close') {
            throw new Error('Manage targets and debugger execution through aidebug');
          }
          result = await request(method, params);
      }
      emit({id, result, ...(selected ? {sessionId: selected} : {})});
    } catch (error) {
      emit({id, error: {code: -32000, message: error.message}, ...(selected ? {sessionId: selected} : {})});
    }
  }
  try {
    return await puppeteer.connect({transport, defaultViewport: null, protocolTimeout: 30000});
  } catch (error) { socket.close(); throw error; }
}
