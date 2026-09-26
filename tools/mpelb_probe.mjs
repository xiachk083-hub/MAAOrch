#!/usr/bin/env node
/**
 * MPE LocalBridge (mpelb) 探针 —— 不走浏览器，直接从协议层验证本地服务。
 *
 * 做的三件事：刷新 ADB 设备列表 → 建 ADB 控制器 → 截一张图落盘 → 断开控制器。
 * 只截图，不注入任何点击/滑动（避免干扰正在跑的 MAA）。
 *
 * 用法：
 *   node tools/mpelb_probe.mjs                      # 默认 emulator-5554，端口 9066
 *   node tools/mpelb_probe.mjs --address 127.0.0.1:16384
 *   node tools/mpelb_probe.mjs --out runtime/lb_shot.png --port 9066
 *
 * 协议：请求路径 /etl/mfw/*，回包路径 /lte/mfw/*；消息体 {path, data}。
 * 路由表见 MPE 仓库 LocalBridge/internal/protocol/mfw/handler.go。
 */
import fs from 'node:fs';
import path from 'node:path';

const argv = process.argv.slice(2);
const arg = (name, def) => {
  const i = argv.indexOf(name);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : def;
};

const PORT = Number(arg('--port', '9066'));
const ADDR = arg('--address', 'emulator-5554');
const OUT = arg('--out', path.join('runtime', `lb_shot_${Date.now()}.png`));
const ADB = arg('--adb', 'C:\\platform-tools-latest-windows\\platform-tools\\adb.exe');

const handlers = [];
const log = (...a) => console.log(...a);

function on(pathName, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      const i = handlers.findIndex((h) => h.resolve === wrapped);
      if (i >= 0) handlers.splice(i, 1);
      reject(new Error(`等待 ${pathName} 超时`));
    }, timeoutMs);
    const wrapped = (data) => {
      clearTimeout(timer);
      resolve(data);
    };
    handlers.push({ pathName, resolve: wrapped });
  });
}

const ws = new WebSocket(`ws://127.0.0.1:${PORT}`);
const request = (p, d = {}) => ws.send(JSON.stringify({ path: p, data: d }));

ws.addEventListener('message', (ev) => {
  let msg;
  try {
    msg = JSON.parse(ev.data);
  } catch {
    return;
  }
  if (msg.path === '/lte/logger') {
    const { level, module, message } = msg.data || {};
    if (level !== 'info' || module === 'MFW') log(`  [lb:${level}] ${module}: ${message}`);
    return;
  }
  if (msg.path === '/error') {
    log('  [lb:error]', JSON.stringify(msg.data));
  }
  const i = handlers.findIndex((h) => h.pathName === msg.path);
  if (i >= 0) {
    const [h] = handlers.splice(i, 1);
    h.resolve(msg.data);
  }
});

const open = new Promise((resolve, reject) => {
  ws.addEventListener('open', resolve);
  ws.addEventListener('error', () => reject(new Error(`连不上 ws://127.0.0.1:${PORT}（mpelb 起了吗？）`)));
});

async function main() {
  await open;
  log(`✓ 已连接 LocalBridge ws://127.0.0.1:${PORT}`);

  const pendingDevices = on('/lte/mfw/adb_devices');
  request('/etl/mfw/refresh_adb_devices');
  const devices = await pendingDevices;
  const list = devices?.devices ?? devices ?? [];
  log(`✓ ADB 设备：${JSON.stringify(list)}`);

  // 必须复用 LB 扫出来的 adb_path / 截图方式 / 输入方式（传空列表会直接建控制器失败）
  const dev = (Array.isArray(list) ? list : []).find((d) => d.address === ADDR) ?? (Array.isArray(list) ? list[0] : null);
  if (!dev) throw new Error(`设备列表里没有 ${ADDR}`);
  log(`→ 选中 ${dev.address}（adb: ${dev.adb_path}，截图: ${dev.screencap_methods?.join('/')}，输入: ${dev.input_methods?.join('/')}）`);
  const pendingCreated = on('/lte/mfw/controller_created', 60000);
  request('/etl/mfw/create_adb_controller', {
    adb_path: dev.adb_path ?? ADB,
    address: dev.address,
    screencap_methods: dev.screencap_methods ?? [],
    input_methods: dev.input_methods ?? [],
    config: dev.config ?? '{}',
  });
  const created = await pendingCreated;
  if (!created?.success) throw new Error(`建控制器失败：${JSON.stringify(created)}`);
  const controllerId = created.controller_id;
  log(`✓ 控制器已连接：id=${controllerId}${created.warning ? `（warning: ${created.warning}）` : ''}`);

  const pendingShot = on('/lte/mfw/screencap_result', 60000);
  request('/etl/mfw/request_screencap', {
    controller_id: controllerId,
    request_id: `probe-${Date.now()}`,
    use_cache: false,
    background: false,
  });
  const shot = await pendingShot;
  if (!shot?.success) throw new Error(`截图失败：${JSON.stringify(shot).slice(0, 400)}`);

  const raw = String(shot.image ?? '');
  const b64 = raw.startsWith('data:') ? raw.slice(raw.indexOf(',') + 1) : raw;
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, Buffer.from(b64, 'base64'));
  log(`✓ 截图成功：${shot.width}×${shot.height} → ${OUT}（${(fs.statSync(OUT).size / 1024).toFixed(0)} KB）`);

  request('/etl/mfw/disconnect_controller', { controller_id: controllerId });
  await new Promise((r) => setTimeout(r, 500));
  log('✓ 已断开控制器');
  ws.close();
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error('✗ ' + err.message);
    try {
      ws.close();
    } catch {}
    process.exit(1);
  });
