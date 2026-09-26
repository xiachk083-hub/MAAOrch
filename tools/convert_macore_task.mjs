#!/usr/bin/env node
/**
 * MaaCore 任务 JSON → MaaFramework 流水线 JSON 转换器（2026-09-22）
 *
 * 为什么需要：引擎不同。MaaCore 用 `algorithm/action/text/templThreshold/preDelay`，
 * MaaFramework 用 `recognition/action/expected/threshold/pre_delay`，且节点名里的 `@`
 * 在转换产物里统一成 `_`，`#self` 要还原成自己的名字。**逻辑照搬，不重写。**
 *
 * 用法：
 *   node tools/convert_macore_task.mjs --in <MaaCore任务.json> [--jp-override <日服覆盖.json>]
 *        --prefix "MiniGame@SPA@" --out-prefix "MiniGame_SPA_" --out <输出.json>
 *        [--template-map map.json]
 *
 * template-map.json：{ "原名.png": "替换模板.png" } —— 用来把国服模板换成日服模板。
 */
import fs from 'node:fs';
import path from 'node:path';

const argv = process.argv.slice(2);
const arg = (n, d) => { const i = argv.indexOf(n); return i >= 0 && argv[i + 1] ? argv[i + 1] : d; };

const IN = arg('--in');
const JP = arg('--jp-override');
const PREFIX = arg('--prefix', '');
const OUT_PREFIX = arg('--out-prefix', '');
const OUT = arg('--out');
const MAP_FILE = arg('--template-map');
if (!IN || !OUT) { console.error('需要 --in 与 --out'); process.exit(2); }

const tplMap = MAP_FILE && fs.existsSync(MAP_FILE) ? JSON.parse(fs.readFileSync(MAP_FILE, 'utf8')) : {};
const base = JSON.parse(fs.readFileSync(IN, 'utf8'));
const jp = JP && fs.existsSync(JP) ? JSON.parse(fs.readFileSync(JP, 'utf8')) : {};

// 合并（日服覆盖同名字段的子集）
const tasks = {};
for (const [k, v] of Object.entries(base)) tasks[k] = { ...v, ...(jp[k] || {}) };

const rename = (name) => (name.startsWith(PREFIX) ? OUT_PREFIX + name.slice(PREFIX.length) : name)
  .replace(/@/g, '_');

const mapTemplate = (t) => tplMap[t] ?? t;

const ALGO = { JustReturn: 'DirectHit', MatchTemplate: 'TemplateMatch', OcrDetect: 'OCR', ColorMatch: 'ColorMatch', FeatureMatch: 'FeatureMatch', NeuralNetworkClassify: 'NeuralNetworkClassify', NeuralNetworkDetect: 'NeuralNetworkDetect' };
const ACT = { ClickSelf: 'Click', ClickRect: 'Click', DoNothing: 'DoNothing', Swipe: 'Swipe', Stop: 'Stop', Key: 'ClickKey', InputText: 'InputText', LongPress: 'LongPress' };

const out = {};
const warnings = [];

function convert(name, cfg, seen = new Set()) {
  // baseTask：先继承父节点，再套自己的字段
  let merged = { ...cfg };
  if (cfg.baseTask && tasks[cfg.baseTask]) {
    if (seen.has(cfg.baseTask)) { warnings.push(`${name}: baseTask 循环 ${cfg.baseTask}`); }
    else {
      const parent = convert(cfg.baseTask, tasks[cfg.baseTask], new Set([...seen, name]));
      merged = { ...parent, ...cfg };
    }
  }
  const algo = merged.algorithm || 'MatchTemplate';
  const action = merged.action || 'DoNothing';
  const node = {};

  node.recognition = ALGO[algo] || 'TemplateMatch';
  if (node.recognition === 'OCR') {
    const text = merged.text ?? merged.expected;
    if (text) node.expected = Array.isArray(text) ? text : [text];
    else warnings.push(`${name}: OcrDetect 但没有 text`);
  }
  // 模板：显式 template 或用节点名（MaaCore 的隐含约定）
  if (node.recognition === 'TemplateMatch') {
    node.template = mapTemplate(merged.template || `${name}.png`);
  }
  if (merged.roi) node.roi = merged.roi;
  if (merged.templThreshold != null) node.threshold = merged.templThreshold;
  else if (merged.threshold != null) node.threshold = merged.threshold;
  if (merged.method != null) node.method = merged.method;
  if (merged.greenMask != null) node.green_mask = merged.greenMask;

  node.action = ACT[action] || 'DoNothing';
  if (action === 'ClickRect') {
    const r = merged.specificRect || merged.target;
    if (r) node.target = r;
  }
  if (merged.specificRect && action === 'Swipe') node.target = merged.specificRect;
  if (merged.end) node.end = merged.end;
  if (merged.key != null) node.key = merged.key;
  if (merged.inputText != null) node.input_text = merged.inputText;
  if (merged.preDelay != null) node.pre_delay = merged.preDelay;
  if (merged.postDelay != null) node.post_delay = merged.postDelay;
  if (merged.maxTimes != null) node.max_hit = merged.maxTimes;

  const next = merged.next;
  if (next) {
    const list = Array.isArray(next) ? next : [next];
    node.next = list.map((n) => (n === '#self' ? rename(name) : rename(n)));
  }
  if (merged.doc) node.doc = merged.doc;
  return node;
}

for (const [name, cfg] of Object.entries(tasks)) out[rename(name)] = convert(name, cfg);

fs.writeFileSync(OUT, JSON.stringify(out, null, 4));
console.log(`转换完成：${Object.keys(tasks).length} 个节点 -> ${OUT}`);
if (warnings.length) { console.log('警告：'); warnings.forEach((w) => console.log('  - ' + w)); }
const names = Object.keys(out);
console.log('节点：' + names.join(', '));
