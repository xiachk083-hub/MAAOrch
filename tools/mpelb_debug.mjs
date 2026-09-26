#!/usr/bin/env node
/**
 * MPE LocalBridge 流程级调试器（无浏览器）—— 直接跑 `/mpe/debug/run/start`。
 *
 * 用途：不开 MPE 网页，就能拿真实设备跑 pipeline 的「仅识别(recognition-only)」，
 *       把识别结果(OCR 文本/命中框/分数)和截图产物落到 runtime/ 下。
 *       仅识别模式不注入任何点击/滑动，适合在别人正在跑 MAA 的实例上做只读验证。
 *
 * 用法：
 *   node tools/mpelb_debug.mjs --node MAAOrch_SPA_ClickTerminal
 *   node tools/mpelb_debug.mjs --node MAAOrch_SPA_ClickCardTpl --address 127.0.0.1:16384
 *   node tools/mpelb_debug.mjs --pipeline <其它.json> --node <节点名>
 *
 * 依赖：本机 mpelb 已在跑（tools\mpelb_start.bat，端口 9066）。
 */
import fs from 'node:fs';
import path from 'node:path';

const argv = process.argv.slice(2);
const arg = (n, d) => {
  const i = argv.indexOf(n);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};

const PORT = Number(arg('--port', '9066'));
const ADDR = arg('--address', '127.0.0.1:16384');
const RES = arg('--resource', 'D:\\Xiach\\M-A\\MAAW\\maa-arknights-fw\\resource');
const PIPE = arg('--pipeline', path.join(RES, 'pipeline', 'maaorch_spa.json'));
const NODE = arg('--node');
const MODE = arg('--mode', 'recognition-only');   // recognition-only | single-node-run | action-only | run-from-node
const STAMP = new Date().toISOString().replace(/[:.]/g, '-');
const OUTDIR = arg('--outdir', path.join('runtime', `lb_debug_${STAMP}`));
const TIMEOUT = Number(arg('--timeout', '60000'));

if (!NODE) {
  console.error('缺少 --node <节点名>（pipeline 里的键名）');
  process.exit(2);
}
fs.mkdirSync(OUTDIR, { recursive: true });
const LOG = path.join(OUTDIR, 'events.jsonl');

const pipeline = JSON.parse(fs.readFileSync(PIPE, 'utf8'));
if (!pipeline[NODE]) {
  console.error(`节点 ${NODE} 不在 ${PIPE} 里；可用：${Object.keys(pipeline).join(', ')}`);
  process.exit(2);
}

const handlers = [];
const on = (p, timeoutMs = 30000) =>
  new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`等待 ${p} 超时`)), timeoutMs);
    handlers.push({ p, resolve: (d) => { clearTimeout(t); resolve(d); } });
  });

const ws = new WebSocket(`ws://127.0.0.1:${PORT}`);
const send = (p, d = {}) => ws.send(JSON.stringify({ path: p, data: d }));
const log = (...a) => console.log(...a);

let sessionId = null;
const artifacts = [];

ws.addEventListener('message', async (ev) => {
  let msg;
  try { msg = JSON.parse(ev.data); } catch { return; }
  const { path: p, data } = msg;

  if (p === '/lte/logger') {
    const { level, module, message } = data || {};
    if (level !== 'info' || module === 'MFW' || module === 'DebugVNext') log(`  [lb:${level}] ${module}: ${message}`);
    return;
  }

  const i = handlers.findIndex((h) => h.p === p);
  if (i >= 0) { const [h] = handlers.splice(i, 1); h.resolve(data); }

  fs.appendFileSync(LOG, JSON.stringify(msg) + '\n');

  if (p === '/error' || p === '/lte/debug/error') {
    log('  [lb:error] ' + JSON.stringify(data));
    if (p === '/lte/debug/error') setTimeout(finish, 200);
    return;
  }
  if (p === '/lte/debug/session_created') { sessionId = data?.sessionId ?? null; log(`✓ 会话 ${sessionId}`); return; }
  if (p === '/lte/debug/run_started') { log(`✓ 开始运行 runId=${data?.runId} entry=${data?.entry} mode=${data?.mode}`); return; }
  if (p === '/lte/debug/event') {
    const e = data || {};
    const node = e.node?.runtimeName ?? '';
    const extra = e.data ? JSON.stringify(e.data).slice(0, 300) : (e.maafwMessage ?? '');
    log(`  · seq=${e.seq} kind=${e.kind} ${e.phase ?? ''} ${node} ${e.status ?? ''} ${extra}`);
    if (e.screenshotRef) artifacts.push({ id: e.screenshotRef, type: 'screenshot' });
    if (e.detailRef) artifacts.push({ id: e.detailRef, type: 'detail' });
    if (e.kind === 'run' && ['completed', 'failed', 'stopped', 'error'].includes(String(e.status))) finish();
    return;
  }
  if (p === '/lte/debug/session_snapshot') {
    for (const a of data?.artifacts ?? []) artifacts.push({ id: a.id ?? a.artifactID, type: a.type });
    return;
  }
});

const open = new Promise((res, rej) => {
  ws.addEventListener('open', res);
  ws.addEventListener('error', () => rej(new Error(`连不上 ws://127.0.0.1:${PORT}`)));
});

let done = false;
async function finish() {
  if (done) return;
  done = true;
  await pullArtifacts();
  try { send('/etl/mfw/disconnect_controller', { controller_id: controllerId }); } catch {}
  await new Promise((r) => setTimeout(r, 400));
  log(`\n事件与产物：${OUTDIR}`);
  try { ws.close(); } catch {}
  process.exit(0);
}

let controllerId = null;
async function pullArtifacts() {
  const seen = new Set();
  for (const a of artifacts) {
    if (!a.id || seen.has(a.id) || !sessionId) continue;
    seen.add(a.id);
    const pending = on('/lte/debug/artifact', 15000).catch(() => null);
    send('/mpe/debug/artifact/get', { sessionId, artifactId: a.id });
    const payload = await pending;
    if (!payload) continue;
    const ref = payload.ref ?? {};
    const content = payload.content ?? '';
    if (String(ref.mime || '').startsWith('image') || content.startsWith('data:image')) {
      const b64 = content.includes(',') ? content.slice(content.indexOf(',') + 1) : content;
      const f = path.join(OUTDIR, `${a.type}_${a.id}.png`);
      fs.writeFileSync(f, Buffer.from(b64, 'base64'));
      log(`  ↘ 图片产物: ${f}`);
    } else {
      log(`  ↘ 产物(${a.type}): ${JSON.stringify(payload).slice(0, 600)}`);
    }
  }
}

async function main() {
  await open;
  log(`✓ 已连接 LocalBridge ws://127.0.0.1:${PORT}`);

  const pd = on('/lte/mfw/adb_devices');
  send('/etl/mfw/refresh_adb_devices');
  const list = (await pd)?.devices ?? [];
  const dev = list.find((d) => d.address === ADDR) ?? list[0];
  if (!dev) throw new Error('没有可用 ADB 设备');
  log(`→ 设备 ${dev.address}`);

  const pc = on('/lte/mfw/controller_created', 60000);
  send('/etl/mfw/create_adb_controller', {
    adb_path: dev.adb_path,
    address: dev.address,
    screencap_methods: dev.screencap_methods ?? [],
    input_methods: dev.input_methods ?? [],
    config: dev.config ?? '{}',
  });
  const created = await pc;
  if (!created?.success) throw new Error(`建控制器失败：${JSON.stringify(created)}`);
  controllerId = created.controller_id;
  log(`✓ 控制器 ${controllerId}`);

  const generatedAt = new Date().toISOString();
  const fileId = 'f_pipeline';
  const nodes = Object.entries(pipeline).map(([name, cfg]) => ({
    fileId, nodeId: name, runtimeName: name, displayName: name,
    prefix: '', fieldMap: {},
    _cfg: cfg,
  }));
  const edges = [];
  for (const [name, cfg] of Object.entries(pipeline)) {
    for (const to of cfg.next ?? []) {
      edges.push({ edgeId: `${name}->${to}`, fromRuntimeName: name, toRuntimeName: to, reason: 'next' });
    }
  }
  const target = { fileId, nodeId: NODE, runtimeName: NODE, sourcePath: PIPE };
  const req = {
    profile: {
      id: 'cli', name: 'cli-probe',
      resourcePaths: [RES],
      controller: { type: 'adb', options: { controllerId } },
      agents: [], entry: target, savePolicy: 'sandbox', maaOptions: {},
    },
    mode: MODE,
    graphSnapshot: {
      generatedAt, rootFileId: fileId,
      files: [{ fileId, path: PIPE, relativePath: path.relative(RES, PIPE), pipeline }],
    },
    resolverSnapshot: {
      generatedAt, rootFileId: fileId,
      nodes: nodes.map(({ _cfg, ...n }) => n),
      edges,
    },
    target,
    artifactPolicy: { includeRawImage: true, includeDrawImage: true, includeActionDetail: false },
  };

  log(`→ ${MODE} 运行节点 ${NODE}（资源 ${RES}）`);
  send('/mpe/debug/run/start', req);
  setTimeout(finish, TIMEOUT);
}

main().catch((e) => { console.error('✗ ' + e.message); process.exit(1); });
