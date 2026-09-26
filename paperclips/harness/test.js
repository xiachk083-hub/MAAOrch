// Smoke test: boot the harness page, verify game globals exist, measure virtual-clock speed.
const { launch, CDP, sleep } = require('./cdp');
const { spawn } = require('node:child_process');
const path = require('node:path');

(async () => {
  const server = spawn(process.execPath, [path.join(__dirname, 'serve.js')], { stdio: 'inherit', env: { ...process.env, PC_BOT: '0' } });
  await sleep(700);
  const { proc, wsUrl } = await launch({ port: 9333, headless: true });
  const c = await CDP.connect(wsUrl);
  await c.attachToPage();
  try {
    await c.goto('http://127.0.0.1:8899/index2.html');
    await sleep(1000);
    console.log('globals:', await c.eval('typeof clips + "/" + typeof makeClip + "/" + typeof clipClick + "/" + typeof buttonUpdate'));
    console.log('clips:', await c.eval('clips'), 'funds:', await c.eval('funds'), 'ticks:', await c.eval('ticks'));
    console.log('timers:', await c.eval('window.__vtimers ? window.__vtimers.size : "none"'));
    const t0 = Date.now();
    const steps = await c.eval('window.__vadvance(60000)');
    const t1 = Date.now();
    console.log(`advance 60s virtual -> ${steps} steps in ${t1 - t0} ms  (${Math.round(steps / ((t1 - t0) / 1000))} steps/s)`);
    console.log('after: clips=', await c.eval('clips'), 'ticks=', await c.eval('ticks'), 'funds=', await c.eval('funds'), 'unsold=', await c.eval('unsoldClips'));
    const errs = await c.eval('JSON.stringify(window.__verrors.slice(0,3))');
    console.log('errors:', errs);
  } finally {
    c.close(); try { proc.kill(); } catch (_) {}
    try { server.kill(); } catch (_) {}
  }
})();
