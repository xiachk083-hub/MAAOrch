#!/usr/bin/env node
/**
 * DSH -> Hermes 桥：把任务投进 Hermes 的**实时会话**（learning 开启），而不是一次性 -z。
 *
 * 为什么需要它：'hermes -z' / 'chat -q' / 非 TTY + '-Q' 会设 HERMES_SINGLE_QUERY_SESSION=1，
 * agent/oneshot_footprint.py 据此摘掉 skill_manage、删掉『记录技能』提示、限流委派 => 一次性会话不学习。
 * 走 serve/desktop 的 /api/ws 实时会话不受影响：skill_manage / memory 可用，nudge 到点还会自动做
 * post-turn 自省（skills.creation_nudge_interval 次工具迭代、memory.nudge_interval 轮）。
 *
 * 用法（Windows）：
 *   node tools/hermes_bridge.mjs --list [--profile mas]
 *   node tools/hermes_bridge.mjs --submit --profile mas --file prompt.md [--session <liveId|storedId>] [--title "..."] [--cwd DIR] [--timeout 900]
 *   node tools/hermes_bridge.mjs --port 64899 --token <TOKEN> --list
 *
 * 后端发现：找命令行含 'serve' 的 hermes.exe 进程 -> 取它监听的 127.0.0.1 端口 -> GET / 里解析
 * window.__HERMES_SESSION_TOKEN__（headless 后端就是这么把 token 交给前端的）。
 */
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';

const argv = process.argv.slice(2);
const flag = (name, def = null) => { const i = argv.indexOf('--' + name); return i >= 0 ? (argv[i + 1] ?? true) : def; };
const has = (name) => argv.includes('--' + name);

function discover() {
  const ps = [
    "$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'hermes' -and $_.CommandLine -match 'serve' }",
    "$pids = @($p.ProcessId)",
    "if ($pids.Count -eq 0) { '' ; exit 0 }",
    "$ports = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $pids -contains $_.OwningProcess } | Select-Object -ExpandProperty LocalPort -Unique",
    "($ports | Sort-Object) -join ','",
  ].join('; ');
  const out = execFileSync('powershell', ['-NoProfile', '-Command', ps], { encoding: 'utf8' }).trim();
  if (!out) throw new Error('没找到运行中的 hermes serve；先起一个或用 --port/--token 手填');
  return out.split(',').map((p) => Number(p)).filter(Boolean);
}

async function tokenFor(port) {
  const html = await (await fetch('http://127.0.0.1:' + port + '/')).text();
  const m = html.match(/__HERMES_SESSION_TOKEN__="([^"]+)"/);
  if (!m) throw new Error('后端 ' + port + ' 未返回 session token（不是 hermes serve？）');
  return m[1];
}

class Bridge {
  constructor(url) { this.url = url; this.id = 1; this.pending = new Map(); this.events = []; }
  async connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onmessage = (ev) => {
      const txt = typeof ev.data === 'string' ? ev.data : String(ev.data);
      for (const line of txt.split('\n')) {
        if (!line.trim()) continue;
        let m; try { m = JSON.parse(line); } catch { continue; }
        if (m.method === 'event') { this.events.push(m.params ?? {}); continue; }
        const p = this.pending.get(String(m.id));
        if (p) { this.pending.delete(String(m.id)); m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result); }
      }
    };
    await new Promise((res, rej) => { const t = setTimeout(() => rej(new Error('ws open timeout')), 10000); this.ws.onopen = () => { clearTimeout(t); res(); }; });
    await new Promise((r) => setTimeout(r, 1200));
  }
  rpc(method, params = {}, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      const id = String(this.id++); this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }));
      setTimeout(() => { if (this.pending.has(id)) { this.pending.delete(id); reject(new Error('timeout: ' + method)); } }, timeoutMs);
    });
  }
  close() { try { this.ws.close(); } catch {} }
}

const profile = flag('profile', 'mas');
let backendPort = Number(flag('port', 0));
let token = flag('token', null);
if (!backendPort || !token) {
  for (const p of discover()) {
    try { token = token ?? await tokenFor(p); backendPort = backendPort || p; break; } catch {}
  }
}
if (!backendPort || !token) { console.error('无法发现 Hermes 后端；用 --port/--token 指定'); process.exit(2); }
const url = 'ws://127.0.0.1:' + backendPort + '/api/ws?token=' + encodeURIComponent(token);
console.error('[bridge] backend=127.0.0.1:' + backendPort);

const b = new Bridge(url);
await b.connect();

if (has('list')) {
  const active = await b.rpc('session.active_list', { profile });
  const list = await b.rpc('session.list', { profile, limit: 15, include_hidden: true }).catch(() => null);
  console.log(JSON.stringify({
    profile,
    live: (active.sessions ?? []).map((s) => ({ id: s.id, key: s.session_key, msgs: s.message_count, status: s.status, title: s.title })),
    recent: (list?.sessions ?? []).map((s) => ({ id: s.id, key: s.session_key, msgs: s.message_count, title: s.title })),
  }, null, 2));
  b.close();
  process.exit(0);
}

if (has('submit')) {
  const file = flag('file');
  if (!file) { console.error('--submit 需要 --file <prompt.md>'); process.exit(2); }
  const text = readFileSync(resolve(file), 'utf8');
  let sid = flag('session', null);
  if (!sid) {
    const created = await b.rpc('session.create', { profile, title: flag('title', 'DSH->Hermes bridge'), source: 'desktop', cwd: flag('cwd', process.cwd()) }, 60000);
    sid = created.session_id ?? created.sid;
    console.error('[bridge] created live=' + sid + ' stored=' + (created.stored_session_id ?? '?') + ' model=' + (created.info?.model ?? '?'));
  } else {
    console.error('[bridge] resuming ' + sid);
  }
  const marker = b.events.length;
  console.error('[bridge] submitted: ' + JSON.stringify(await b.rpc('prompt.submit', { profile, session_id: sid, text, surface: 'desktop' }, 60000)));
  const deadline = Date.now() + Number(flag('timeout', 900)) * 1000;
  let done = false;
  while (!done && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 3000));
    done = b.events.slice(marker).some((e) => e.type === 'turn.end' || String(e.type).includes('message.complete'));
  }
  const seen = b.events.slice(marker);
  const out = seen.filter((e) => String(e.type).includes('delta') && typeof (e.payload?.delta ?? e.payload?.text) === 'string').map((e) => e.payload.delta ?? e.payload.text).join('');
  const tools = [...new Set(seen.filter((e) => String(e.type).includes('tool') && (e.payload?.name || e.payload?.tool)).map((e) => String(e.payload.name ?? e.payload.tool)))];
  const outDir = join(process.cwd(), 'tmp', 'hermes_bridge');
  mkdirSync(outDir, { recursive: true });
  const outFile = join(outDir, new Date().toISOString().replace(/[:.]/g, '-') + '_' + profile + '.md');
  writeFileSync(outFile, '# DSH->Hermes (' + profile + ') live=' + sid + '\n\n## prompt\n\n' + text + '\n\n## reply\n\n' + out + '\n\n## tools\n\n' + tools.join(', ') + '\n', 'utf8');
  console.error('[bridge] done=' + done + ' tools=' + tools.join(',') + ' transcript=' + outFile);
  console.log(out);
  b.close();
  process.exit(done ? 0 : 1);
}

console.error('用法：--list [--profile mas] | --submit --profile mas --file prompt.md [--session id] [--title T] [--cwd DIR] [--timeout s]');
b.close();
process.exit(2);
