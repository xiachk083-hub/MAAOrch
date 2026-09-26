#!/usr/bin/env node
/**
 * deploy_host_file.mjs —— 把本地文件搬到 MRFZ-0000 上（scp/sftp 不通时的正解）
 * ──────────────────────────────────────────────────────────────────────────────
 * 背景（2026-09-23 实测）：
 *   · `scp` 直接 `Connection closed`（那台没开 sftp 子系统）；
 *   · `ssh host "powershell -EncodedCommand <整个脚本>"` 一超过 **cmd.exe 的 8191 字符**上限就被截断
 *     （报"命令行太长"，还是 GBK 花屏，完全看不出根因）；
 *   · 于是改成**分片 base64 追加**：每片 2000 字符（UTF-16 → base64 后 ~5800，安全），
 *     全部追加进 `<remote>.b64`，最后一条小命令把它解码写回目标文件并**校验 MD5**。
 *
 * 用法：
 *   node tools/deploy_host_file.mjs --local tools/rogue_host_probe.ps1 --remote E:\maa-alas\fz-maa\_mporch_probe.ps1
 *   node tools/deploy_host_file.mjs --local X --remote Y --force   # 即使 MD5 一致也重传
 */
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const HOST = 'Administrator@100.79.173.69';
const KEY = path.join(process.env.USERPROFILE || '', '.ssh', 'maaorch_target');
const CHUNK = 2000;

const argv = process.argv.slice(2);
const flag = (k, d) => {
  const i = argv.indexOf(k);
  if (i < 0) return d;
  const v = argv[i + 1];
  return v && !v.startsWith('--') ? v : true;
};
const LOCAL = flag('--local', null);
const REMOTE = flag('--remote', null);
const FORCE = argv.includes('--force');
if (!LOCAL || !REMOTE) {
  console.error('用法: node tools/deploy_host_file.mjs --local <本地文件> --remote <主机路径> [--force]');
  process.exit(2);
}

function ssh(cmd, timeout = 90000) {
  try {
    return execFileSync('ssh',
      ['-i', KEY, '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
        '-o', 'BatchMode=yes', HOST, cmd],
      { encoding: 'utf8', timeout, maxBuffer: 8 * 1024 * 1024 }).trim();
  } catch (e) {
    const out = String(e.stdout || '').trim();
    if (out) return out;
    return `__ERR__ ${String(e.stderr || e.message).slice(0, 200)}`;
  }
}
const psEnc = (script) => `powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand ${Buffer.from(script, 'utf16le').toString('base64')}`;
const q = (s) => `'${String(s).replace(/'/g, "''")}'`;

let buf = fs.readFileSync(LOCAL);
// .ps1 一律补 UTF-8 BOM：PowerShell 5.1 会把**无 BOM 的 UTF-8 当 ANSI** 读，
// 中文注释变乱码后可能连语法都解析不过（2026-09-23 实测：探针脚本里的中文注释把 for/if 花括号解析搞崩）。
if (/\.ps1$/i.test(String(REMOTE)) && !(buf[0] === 0xEF && buf[1] === 0xBB && buf[2] === 0xBF)) {
  buf = Buffer.concat([Buffer.from([0xEF, 0xBB, 0xBF]), buf]);
}
const md5 = crypto.createHash('md5').update(buf).digest('hex');
const b64 = buf.toString('base64');
const remoteB64 = `${REMOTE}.b64`;

if (!FORCE) {
  const cur = ssh(psEnc(`$ProgressPreference='SilentlyContinue'; if(Test-Path ${q(REMOTE)}){ (Get-FileHash ${q(REMOTE)} -Algorithm MD5).Hash } else { 'MISSING' }`));
  if (cur.toUpperCase() === md5.toUpperCase()) {
    console.log(`已是最新（md5 ${md5}），跳过：${REMOTE}`);
    process.exit(0);
  }
  console.log(`远端现状: ${cur} ⇒ 需要部署`);
}

// 1) 清空 .b64
ssh(psEnc(`$ProgressPreference='SilentlyContinue'; Remove-Item -Force ${q(remoteB64)} -ErrorAction SilentlyContinue; 'reset'`));

// 2) 分片追加 base64 文本
const chunks = [];
for (let i = 0; i < b64.length; i += CHUNK) chunks.push(b64.slice(i, i + CHUNK));
console.log(`分 ${chunks.length} 片上传（每片 ${CHUNK} 字符）…`);
chunks.forEach((c, i) => {
  const r = ssh(psEnc(`$ProgressPreference='SilentlyContinue'; Add-Content -Path ${q(remoteB64)} -Value ${q(c)} -NoNewline -Encoding Ascii; 'ok'`));
  if (!/ok/.test(r)) { console.error(`第 ${i + 1} 片失败: ${r}`); process.exit(1); }
});

// 3) 解码回目标文件 + 校验
const fin = ssh(psEnc(`$ProgressPreference='SilentlyContinue';`
  + ` $t=[IO.File]::ReadAllText(${q(remoteB64)});`
  + ` [IO.File]::WriteAllBytes(${q(REMOTE)}, [Convert]::FromBase64String($t));`
  + ` (Get-FileHash ${q(REMOTE)} -Algorithm MD5).Hash`));
const ok = fin.toUpperCase() === md5.toUpperCase();
console.log(`${ok ? '✅ 部署成功' : '❌ 校验不符'}: ${REMOTE} 远端md5=${fin} 本地md5=${md5}`);
process.exit(ok ? 0 : 1);
