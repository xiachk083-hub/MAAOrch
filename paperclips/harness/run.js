// Drive the harnessed game with the virtual clock.
//   node run.js [--maxSeconds=6000] [--chunk=20] [--reportEvery=60] [--port=8899] [--cdp=9333]
//            [--bot=1] [--profile=<dir>] [--headful]
const path = require('node:path');
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const { launch, CDP, sleep } = require('./cdp');

function arg(name, def) {
  const p = process.argv.find((a) => a.startsWith('--' + name + '='));
  if (p) return p.slice(name.length + 3);
  if (process.argv.includes('--' + name)) return '1';
  return def;
}

(async () => {
  const PORT = Number(arg('port', 8899));
  const CDP_PORT = Number(arg('cdp', 9333));
  const MAXS = Number(arg('maxSeconds', 6000));
  const CHUNK = Number(arg('chunk', 20));           // virtual seconds per CDP round trip
  const REPORT = Number(arg('reportEvery', 60));    // virtual seconds between status lines
  const BOT = arg('bot', '1');
  const PROFILE = arg('profile', path.join(require('node:os').tmpdir(), 'pc-profile-' + Date.now()));
  const HEADFUL = process.argv.includes('--headful');

  const runDir = path.join(__dirname, '..', 'runs');
  fs.mkdirSync(runDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const logPath = path.join(runDir, 'run-' + stamp + '.jsonl');
  const log = fs.createWriteStream(logPath, { flags: 'a' });
  const out = (o) => { log.write(JSON.stringify(o) + '\n'); };
  const say = (s) => { process.stdout.write(s + '\n'); };

  const server = spawn(process.execPath, [path.join(__dirname, 'serve.js')], {
    stdio: 'ignore', env: { ...process.env, PC_PORT: String(PORT), PC_BOT: BOT },
  });
  await sleep(600);

  const { proc, wsUrl } = await launch({ port: CDP_PORT, headless: !HEADFUL, profile: PROFILE });
  const c = await CDP.connect(wsUrl);
  await c.attachToPage();
  let exitCode = 0;
  try {
    await c.goto(`http://127.0.0.1:${PORT}/index2.html`);
    await sleep(400);
    const ready = await c.eval('typeof clips');
    if (ready !== 'number') throw new Error('game did not load (clips=' + ready + ')');
    say('loaded. log -> ' + logPath);

    let virt = 0;
    let lastReport = -1e9;
    let wall0 = Date.now();
    let ticks0 = await c.eval('ticks');
    while (virt < MAXS) {
      const t0 = Date.now();
      await c.eval(`window.__vadvance(${CHUNK * 1000})`);
      virt += CHUNK;
      const t1 = Date.now();
      if (virt - lastReport >= REPORT || t1 - t0 > 3000) {
        lastReport = virt;
        const r = await c.eval('JSON.stringify(BOT.report())');
        const rep = JSON.parse(r);
        rep.virt = virt;
        rep.wallMs = t1 - wall0;
        rep.virtPerWall = Math.round(virt / ((t1 - wall0) / 1000));
        out(rep);
        say(fmt(rep));
        if (rep.milestoneFlag >= 20 || rep.stage === 'dismantle7') {
          // keep going a little to let the credits roll, then stop
          if (rep.milestoneFlag >= 20) { say('*** GAME COMPLETE (milestoneFlag=20) ***'); break; }
        }
      }
      if (t1 - t0 > 60000) { say(`chunk took ${((t1 - t0) / 1000).toFixed(1)}s of wall clock`); }
    }
    const ticks1 = await c.eval('ticks');
    say(`done: ${virt}s virtual, ${ticks1} ticks, ${((Date.now() - wall0) / 1000).toFixed(1)}s wall`);
    const events = await c.eval('JSON.stringify(BOT.events.slice(-400))');
    fs.writeFileSync(logPath.replace(/\.jsonl$/, '.events.txt'), JSON.parse(events).join('\n'));
    const errs = await c.eval('JSON.stringify({bot: BOT.errors, v: window.__verrors})');
    fs.writeFileSync(logPath.replace(/\.jsonl$/, '.errors.txt'), errs);
  } catch (e) {
    say('ERROR: ' + (e && e.stack || e));
    exitCode = 1;
  } finally {
    try { c.close(); } catch (_) {}
    try { proc.kill(); } catch (_) {}
    try { server.kill(); } catch (_) {}
  }
  process.exit(exitCode);
})();

function fmt(r) {
  const n = (x) => (typeof x === 'number' ? (Math.abs(x) >= 1e5 || (Math.abs(x) < 1e-3 && x !== 0) ? x.toExponential(2) : String(Math.round(x * 100) / 100)) : x);
  return `[${r.virt}s ${r.virtPerWall}x] ${r.stage} ms=${r.milestoneFlag} trust=${r.trust} proc=${r.processors}/mem=${r.memory} ops=${n(r.ops)} ` +
    `creat=${n(r.creativity)} yomi=${n(r.yomi)} honor=${n(r.honor)} | clips=${n(r.clips)} unused=${n(r.unusedClips)} rate=${n(r.clipRate)} ` +
    `funds=${n(r.funds)} port=${n(r.portTotal)}(${r.portfolioSize}pos) margin=${r.margin} wire=${n(r.wire)} ads=${r.ads} clipper=${r.clipper} mega=${r.mega} | ` +
    `probes=${n(r.probes)} ptrust=${r.probeTrust}/${r.maxTrust} matter=${n(r.matter)} fac=${r.fac} harv=${r.harv} wd=${r.wd} farm=${r.farm} ` +
    `pow=${n(r.power)} powMod=${n(r.powMod)} gifts=${n(r.swarmGifts)} drift=${n(r.drifters)} err=${r.errors}`;
}
