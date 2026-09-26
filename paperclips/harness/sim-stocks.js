// Offline model of the Universal Paperclips investment engine, to measure portfolio growth.
// Mirrors main.js: stockShop (1/s, 25%), updateStocks (2.5s), sellStock (FIFO), createStock.
function run({ riskiness = 7, gainThreshold = 0.5, start = 1e5, seconds = 3600, sellAfter = 1e9, maxPort = 5, seed = 1 } = {}) {
  let rnd = (() => { let s = seed >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; }; })();
  let bankroll = start, ledger = 0, stocks = [], stockID = 0;
  const portTotal = () => bankroll + stocks.reduce((a, s) => a + s.total, 0);
  let tick = 0;
  for (let i = 0; i < seconds * 100; i++) {
    tick++;
    // 1000 ms: stockShop
    if (tick % 10 === 0) {
      const pt = portTotal();
      let budget = Math.ceil(pt / riskiness);
      const r = 11 - riskiness;
      let reserves = Math.ceil(pt / r);
      if (riskiness === 1) reserves = 0;
      if ((bankroll - budget) < reserves && riskiness === 1 && bankroll > pt / 10) budget = bankroll;
      else if ((bankroll - budget) < reserves && riskiness === 1) budget = 0;
      else if ((bankroll - budget) < reserves) budget = bankroll - reserves;
      if (stocks.length < maxPort && bankroll >= 5 && budget >= 1 && bankroll - budget >= reserves) {
        if (rnd() < 0.25) { // createStock(budget)
          let pri;
          const x = rnd();
          if (x > 0.99) pri = Math.ceil(rnd() * 3000);
          else if (x > 0.85) pri = Math.ceil(rnd() * 500);
          else if (x > 0.60) pri = Math.ceil(rnd() * 150);
          else if (x > 0.20) pri = Math.ceil(rnd() * 50);
          else pri = Math.ceil(rnd() * 15);
          if (pri > budget) pri = Math.ceil(budget * rnd());
          let amt = Math.floor(budget / pri);
          if (amt > 1000000) amt = 1000000;
          const total = pri * amt;
          if (amt > 0) { bankroll -= total; stocks.push({ price: pri, amount: amt, total, age: 0 }); }
        }
      }
    }
    // 2500 ms: updateStocks + auto-sell
    if (tick % 25 === 0) {
      for (const s of stocks) {
        s.age++;
        if (rnd() < 0.6) {
          let gain = true;
          if (rnd() > gainThreshold) gain = false;
          const delta = Math.ceil((rnd() * s.price) / (4 * riskiness));
          if (gain) s.price += delta; else s.price -= delta;
          if (s.price === 0 && rnd() > 0.24) s.price = 1;
          if (s.price < 0) s.price = 0;
          s.total = s.price * s.amount;
        }
      }
      // auto sell (0.3) once grown old
      if (stocks.length > 0 && stocks[0].age > 5 && rnd() <= 0.3) {
        bankroll += stocks[0].total; stocks.shift();
      }
    }
    if (sellAfter && tick % (sellAfter * 100) === 0 && stocks.length) { bankroll += stocks[0].total; stocks.shift(); }
  }
  return portTotal();
}

const grid = [];
for (const risk of [1, 5, 7]) {
  for (const gt of [0.5, 0.55, 0.6, 0.7, 0.8, 0.9]) {
    let v = 0;
    const N = 5;
    for (let s = 1; s <= N; s++) v += run({ riskiness: risk, gainThreshold: gt, start: 1e6, seconds: 600, seed: s });
    v /= N;
    grid.push({ risk, gainThreshold: gt, after600s: v, mult: v / 1e6 });
  }
}
console.table(grid.map((g) => ({ risk: g.risk, gain: g.gainThreshold, 'x after 600s': g.mult.toFixed(2), final: g.after600s.toExponential(2) })));
