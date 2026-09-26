#!/usr/bin/env node
/**
 * MPE LocalBridge 固定图识别（离线视觉校验）—— 不连设备，拿一张 PNG 当底图验识别。
 *
 * 用途：调导航节点前，先用真实截图确认"这个模板/这句 OCR 到底能不能命中、分数多少、命中框在哪"。
 *       走 LB 的 `/etl/utility/template_match`（底图与模板都是 base64）与 `/etl/utility/ocr_recognize`，
 *       用的是 MaaFramework 自带的匹配器与 OCR，和真机跑出来的结果同源。
 *
 * 用法：
 *   node tools/mpelb_vision.mjs --image runtime\shot.png --template MiniGame/SPA/MiniGame@SPA@EnterIndependent.png
 *   node tools/mpelb_vision.mjs --image shot.png --template a.png,b.png --roi 519,568,268,152 --threshold 0.8
 *   node tools/mpelb_vision.mjs --image shot.png --ocr --ocr-roi 380,640,700,80
 */
import fs from 'node:fs';
import path from 'node:path';

const argv = process.argv.slice(2);
const arg = (n, d) => {
  const i = argv.indexOf(n);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};
const has = (n) => argv.includes(n);

const PORT = Number(arg('--port', '9066'));
const IMG = arg('--image');
const IMG_DIR = arg('--image-dir', 'D:\\Xiach\\M-A\\MAAW\\maa-arknights-fw\\resource\\image');
const TEMPLATES = arg('--template', '').split(',').map((s) => s.trim()).filter(Boolean);
const ROI = arg('--roi', '').split(',').map(Number).filter((n) => !Number.isNaN(n));
const THRESHOLD = Number(arg('--threshold', '0.8'));
const DO_OCR = has('--ocr');
const OCR_ROI = arg('--ocr-roi', '').split(',').map(Number).filter((n) => !Number.isNaN(n));

if (!IMG || (!TEMPLATES.length && !DO_OCR)) {
  console.error('用法：--image <底图.png> [--template a.png,b.png] [--roi x,y,w,h] [--threshold 0.8] [--ocr] [--ocr-roi x,y,w,h]');
  process.exit(2);
}

const b64 = (p) => fs.readFileSync(p).toString('base64');
const base = b64(IMG);

const handlers = [];
const on = (p, timeoutMs = 60000) =>
  new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`等待 ${p} 超时`)), timeoutMs);
    handlers.push({ p, resolve: (d) => { clearTimeout(t); resolve(d); } });
  });

const ws = new WebSocket(`ws://127.0.0.1:${PORT}`);
ws.addEventListener('message', (ev) => {
  let m;
  try { m = JSON.parse(ev.data); } catch { return; }
  if (m.path === '/lte/logger') {
    const { level, module, message } = m.data || {};
    if (level !== 'info') console.log(`  [lb:${level}] ${module}: ${message}`);
    return;
  }
  if (m.path === '/error') { console.log('  [lb:error] ' + JSON.stringify(m.data)); return; }
  const i = handlers.findIndex((h) => h.p === m.path);
  if (i >= 0) { const [h] = handlers.splice(i, 1); h.resolve(m.data); }
});

const open = new Promise((res, rej) => {
  ws.addEventListener('open', res);
  ws.addEventListener('error', () => rej(new Error(`连不上 ws://127.0.0.1:${PORT}`)));
});

async function main() {
  await open;
  console.log(`底图：${IMG}  (${(fs.statSync(IMG).size / 1024).toFixed(0)} KB)`);
  const full = [0, 0, 0, 0];

  for (const t of TEMPLATES) {
    const p = path.isAbsolute(t) ? t : path.join(IMG_DIR, t);
    if (!fs.existsSync(p)) { console.log(`✗ 模板不存在：${p}`); continue; }
    const pending = on('/lte/utility/template_match_result');
    ws.send(JSON.stringify({
      path: '/etl/utility/template_match',
      data: { base_image: base, template_image: b64(p), roi: ROI.length === 4 ? ROI : full, threshold: THRESHOLD },
    }));
    const r = await pending;
    const items = r?.all ?? [];
    const best = r?.best ?? items.slice().sort((a, b) => (b?.score ?? 0) - (a?.score ?? 0))[0];
    const score = best?.score != null ? Number(best.score) : null;
    const boxStr = best ? `[${best.x},${best.y},${best.width},${best.height}]` : '-';
    const ok = r?.hit && score != null && score >= THRESHOLD;
    console.log(`${ok ? '✓' : '·'} ${t}  score=${score != null ? score.toFixed(4) : '?'}  box=${boxStr}  ${ok ? '命中' : '未达阈值'}${items.length > 1 ? `  共${items.length}处` : ''}`);
  }

  if (DO_OCR) {
    const pending = on('/lte/utility/ocr_result');
    ws.send(JSON.stringify({
      path: '/etl/utility/ocr_recognize',
      data: { base_image: base, roi: OCR_ROI.length === 4 ? OCR_ROI : full },
    }));
    const r = await pending;
    const boxes = r?.boxes ?? [];
    console.log(`\nOCR（${r?.no_content ? '无内容' : boxes.length + ' 段'}）：`);
    for (const b of boxes) {
      const t = b.text ?? b.Text ?? '';
      const s = b.score != null ? Number(b.score).toFixed(3) : '?';
      const bx = b.box ?? b.Box;
      const bs = Array.isArray(bx) ? `[${bx.join(',')}]` : bx ? `[${bx.x ?? bx[0]},${bx.y ?? bx[1]},${bx.w ?? bx[2]},${bx.h ?? bx[3]}]` : '';
      console.log(`  ${s}  ${JSON.stringify(t)}  ${bs}`);
    }
  }

  ws.close();
  process.exit(0);
}

main().catch((e) => { console.error('✗ ' + e.message); process.exit(1); });
