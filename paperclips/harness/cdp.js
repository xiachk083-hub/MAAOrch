// Minimal Chrome DevTools Protocol client (no external deps; Node >= 22 has fetch + WebSocket).
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const CHROME_CANDIDATES = [
  process.env.PC_CHROME,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  path.join(process.env.LOCALAPPDATA || '', 'Google\\Chrome\\Application\\chrome.exe'),
];

function findChrome() {
  for (const c of CHROME_CANDIDATES) if (c && fs.existsSync(c)) return c;
  throw new Error('chrome not found');
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function launch({ port = 9333, profile, headless = true, url = 'about:blank' } = {}) {
  const exe = findChrome();
  const dir = profile || path.join(os.tmpdir(), 'pc-profile');
  fs.mkdirSync(dir, { recursive: true });
  const args = [
    '--remote-debugging-port=' + port,
    '--user-data-dir=' + dir,
    '--no-first-run', '--no-default-browser-check', '--disable-extensions',
    '--mute-audio', '--disable-background-timer-throttling',
    '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows',
    '--window-size=1200,900',
  ];
  if (headless) args.push('--headless=new', '--disable-gpu');
  args.push(url);
  const proc = spawn(exe, args, { stdio: 'ignore', detached: false });
  let wsUrl = null;
  for (let i = 0; i < 120; i++) {
    await sleep(250);
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`);
      const j = await r.json();
      if (j.webSocketDebuggerUrl) { wsUrl = j.webSocketDebuggerUrl; break; }
    } catch (_) {}
  }
  if (!wsUrl) { try { proc.kill(); } catch (_) {} throw new Error('chrome did not expose CDP'); }
  return { proc, wsUrl, port, profile: dir };
}

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.sessionId = null; this.events = []; }
  static async connect(wsUrl) {
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = (e) => rej(new Error('ws error')); });
    const c = new CDP(ws);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && c.pending.has(msg.id)) {
        const { res, rej } = c.pending.get(msg.id);
        c.pending.delete(msg.id);
        msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result);
      } else if (msg.method) {
        c.events.push(msg);
        if (c.events.length > 200) c.events.shift();
      }
    };
    return c;
  }
  send(method, params = {}, sessionId) {
    const id = ++this.id;
    const msg = { id, method, params };
    if (sessionId || this.sessionId) msg.sessionId = sessionId || this.sessionId;
    this.ws.send(JSON.stringify(msg));
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      setTimeout(() => { if (this.pending.has(id)) { this.pending.delete(id); rej(new Error('cdp timeout: ' + method)); } }, 600000);
    });
  }
  async attachToPage() {
    const { targetId } = await this.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await this.send('Target.attachToTarget', { targetId, flatten: true });
    this.sessionId = sessionId;
    this.targetId = targetId;
    return sessionId;
  }
  async eval(expression, { awaitPromise = true, returnByValue = true } = {}) {
    const r = await this.send('Runtime.evaluate', { expression, awaitPromise, returnByValue, allowUnsafeEvalBlockedByCSP: true, userGesture: true });
    if (r.exceptionDetails) throw new Error('page error: ' + JSON.stringify(r.exceptionDetails).slice(0, 2000));
    return r.result ? r.result.value : undefined;
  }
  async goto(url) {
    await this.send('Page.enable');
    await this.send('Page.navigate', { url });
    for (let i = 0; i < 200; i++) {
      await sleep(100);
      try {
        const st = await this.eval('document.readyState');
        if (st === 'complete') return;
      } catch (_) {}
    }
  }
  close() { try { this.ws.close(); } catch (_) {} }
}

module.exports = { launch, CDP, findChrome, sleep };
