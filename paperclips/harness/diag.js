// Diagnostic: boot the game with the bot, then dump detailed state + timer table.
const path = require('node:path');
const { spawn } = require('node:child_process');
const { launch, CDP, sleep } = require('./cdp');

(async () => {
  const server = spawn(process.execPath, [path.join(__dirname, 'serve.js')], { stdio: 'ignore', env: { ...process.env, PC_PORT: '8899', PC_BOT: '1' } });
  await sleep(600);
  const { proc, wsUrl } = await launch({ port: 9334, headless: true, profile: path.join(require('node:os').tmpdir(), 'pc-diag-' + Date.now()) });
  const c = await CDP.connect(wsUrl);
  await c.attachToPage();
  try {
    await c.goto('http://127.0.0.1:8899/index2.html');
    await sleep(300);
    console.log('timers:', await c.eval('JSON.stringify([...window.__vtimers.entries()].map(([k,v])=>v.delay))'));
    for (let i = 0; i < 12; i++) {
      await c.eval('window.__vadvance(10000)');
      const s = await c.eval(`JSON.stringify({
        ticks: ticks, demand: demand, margin: margin, unsold: unsoldClips, clips: clips,
        funds: funds, income: income, clipRate: clipRate, wire: wire, wireCost: wireCost,
        clipper: clipmakerLevel, ads: marketingLvl, me: marketingEffectiveness, db: demandBoost,
        humanFlag: humanFlag, compFlag: compFlag, projectsFlag: projectsFlag, trust: trust,
        ops: operations, target: (function(){ try { return BOT && 1; } catch(e){ return -1; } })()
      })`);
      console.log(((i + 1) * 10) + 's', s);
    }
  } finally { c.close(); try { proc.kill(); } catch (_) {} try { server.kill(); } catch (_) {} }
  process.exit(0);
})();
