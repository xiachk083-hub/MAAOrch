# Universal Paperclips — Stage 1 (Human Era / Business) Mechanics Reference

Source: `paperclips/main.js` (6499 lines), `paperclips/projects.js`, `paperclips/globals.js`, `paperclips/index2.html`.

**Scope:** everything that runs while `humanFlag == 1` and `spaceFlag == 0` (i.e. before "Release the HypnoDrones"). Stage 1 ends when `project35.effect()` runs (`trust>=100`, after `project70`); that sets `humanFlag = 0`, `trust = 0`, `clipmakerLevel = 0`, `megaClipperLevel = 0`, `nanoWire = wire`.

Line references are to `main.js` unless stated otherwise.

---

## 1. Timers / intervals

| Period | Where | What it does |
|---|---|---|
| **10 ms** | `main.js:4188-4556` (MAIN LOOP) | The whole game tick. |
| **100 ms** | `main.js:4564-4599` ("Slow Loop") | Wire price fluctuation, sales, 1-sec revenue calc, autosave. |
| **100 ms** | `main.js:1617-1664` | Reads `investStratElement.value` → sets `riskiness`; recomputes `secTotal`, `portTotal`; repaints the 5 stock rows. |
| **100 ms** | `main.js:2316-2320` | Reads `stratPickerElement.value` → `pick`. |
| **1000 ms** | `main.js:1666-1670` | `if (humanFlag == 1) stockShop();` |
| **2500 ms** | `main.js:1673-1686` | `sellDelay++`; auto-sell: if `portfolioSize>0 && sellDelay>=5 && Math.random()<=.3 && humanFlag==1` → `sellStock()`, `sellDelay=0`. Then if `portfolioSize>0 && humanFlag==1` → `updateStocks()`. |
| **30 ms** | `blink()` `main.js:987-1009` | Visual blink, 12 frames. |
| **32 ms** | `longBlink()` `main.js:926-965` | HypnoDrone visual. |

Timing constants:
* `ticks` increments once per 10 ms iteration → **100 ticks = 1 real second**.
* `clipRate` = clips produced in the last 100 ticks (= 1 second).
* `secTimer` reaches 10 (i.e. 1000 ms) → `calculateRev()`.
* `saveTimer` reaches 250 (25 s) → `save()`.
* `stockReportCounter` reaches 10000 ticks (100 s) → lifetime investment message.

### 10 ms main loop — exact order (main.js:4190-4556)

```
ticks++
milestoneCheck(); buttonUpdate();
if (compFlag == 1) calculateOperations();
if (humanFlag == 1) calculateTrust();
if (qFlag == 1) quantumCompute();
updateStats(); manageProjects(); milestoneCheck();
clipRateTracker++ ; (100-tick rolling clip rate)
if (investmentEngineFlag==1){ stockReportCounter++; if(>=10000) report }
if (humanFlag==1 && wireBuyerFlag==1 && wireBuyerStatus==1 && wire<=1) buyWire();
if (probeCount >= 1) exploreUniverse();
if (humanFlag==0 && spaceFlag==0) updateDroneButtons();
if (humanFlag==0) { updatePower(); updateSwarm(); acquireMatter(); processMatter(); }
if (dismantle<4) clipClick( powMod*fbst*(Math.floor(factoryLevel)*factoryRate) );   // fbst = factoryBoost*factoryLevel if factoryBoost>1 else 1; stage 1 → 0
if (dismantle<4) clipClick( clipperBoost*(clipmakerLevel/100) );
if (dismantle<4) clipClick( megaClipperBoost*(megaClipperLevel*5) );
if (humanFlag == 1) { marketing = Math.pow(1.1, marketingLvl-1);
                      demand = ((.8/margin)*marketing*marketingEffectiveness)*demandBoost;
                      demand = demand + ((demand/10)*prestigeU); }
if (creativityOn && operations >= memory*1000) calculateCreativity();
... dismantle/endgame display logic ...
```

### 100 ms "Slow Loop" — exact order (main.js:4566-4598)

```
adjustWirePrice();
if (humanFlag==1) {
    if (Math.random() < (demand/100)) sellClips( Math.floor(.7 * Math.pow(demand,1.15)) );
    secTimer++;  if (secTimer >= 10) { calculateRev(); secTimer = 0; }
}
saveTimer++;  if (saveTimer >= 250) { save(); saveTimer = 0; }
```

---

## 2. Production, wire, demand, revenue

### 2.1 `clipClick(number)` — main.js:2326-2362

```js
function clipClick(number){
    if (dismantle>=4){ finalClips++; }
    if (wire >= 1){
        if (number > wire) { number = wire; }     // clamped to wire
        clips        = clips + number;
        unsoldClips  = unsoldClips + number;
        wire         = wire - number;
        unusedClips  = unusedClips + number;
        ... DOM updates (only when humanFlag==0) ...
        if (milestoneFlag < 15) clipsElement.innerHTML = ...;
        wireElement.innerHTML = formatWithCommas(wire);
        unsoldClipsElement.innerHTML = formatWithCommas(unsoldClips, 0);
    }
}
```
* Signature has **no default** — `clipClick()` with `number === undefined` → `clips = NaN`. Always pass a number.
* Preconditions: `wire >= 1` only. No funds/ops check. Not affordable → **silent no-op** (no error, nothing deducted).
* Manual player button is `onclick="clipClick(1)"` (index2.html:89) — 1 clip per call, 1 wire per clip.
* Production per 10 ms tick from the loop (stage 1: `factoryLevel = 0`, `dismantle = 0`, `powMod = 0`):
  * autoclippers: `clipClick(clipperBoost * clipmakerLevel / 100)` → `clipmakerLevel/100` clips/tick = **`clipmakerLevel` clips per second** (exact when `clipperBoost == 1`).
  * mega: `clipClick(megaClipperBoost * megaClipperLevel * 5)` → per tick. With initial `megaClipperBoost = 1` → **500 clips/sec per MegaClipper level**.
  * factories contribute 0 (`factoryLevel == 0` in stage 1).
* `clipClick` divides wire by the requested amount; fractional requests (from autoclippers) leave fractional `wire`. When `wire < 1` all auto production stops.

### 2.2 `buyWire()` — main.js:722-732

```js
function buyWire(){
    if(funds >= wireCost){
        wirePriceTimer = 0;
        wire = wire + wireSupply;
        funds = funds - wireCost;
        wirePurchase = wirePurchase + 1;
        wireBasePrice = wireBasePrice + .05;
        wireElement.innerHTML = ...; fundsElement.innerHTML = ...;
    }
}
```
* Cost = current `wireCost` (NOT `wireBasePrice`). No-ops if `funds < wireCost`.
* Adds `wireSupply` (starts **1000**, grows with projects 7/8/9/10/10b: ×1.5, ×1.75, ×2, ×3, ×11).
* Each purchase permanently raises `wireBasePrice` by **+0.05**.
* WireBuyer is auto-called in the 10 ms loop when `humanFlag==1 && wireBuyerFlag==1 && wireBuyerStatus==1 && wire<=1`.

### 2.3 Wire price fluctuation

`adjustWirePrice()` — main.js:695-710, runs every 100 ms:

```js
wirePriceTimer++;
if (wirePriceTimer>250 && wireBasePrice>15){
    wireBasePrice = wireBasePrice - (wireBasePrice/1000);   // -0.1 % per 25 s
    wirePriceTimer = 0;
}
if (Math.random() < .015) {                 // expected ≈1.5×/s
    wirePriceCounter++;
    var wireAdjust = 6*(Math.sin(wirePriceCounter));
    wireCost = Math.ceil(wireBasePrice + wireAdjust);
    wireCostElement.innerHTML = wireCost;
}
```
* `wireBasePrice` starts **20**, `wireCost` starts **20**, `wirePriceCounter` starts **0**.
* **`wireCost` only changes when the 1.5 %/frame roll fires.** In between, `wireCost` is frozen (e.g. if the roll never fires after `wireBasePrice` drifted, the displayed `wireCost` is stale).
* `wireAdjust` range is (−6, +6], so `wireCost` for `wireBasePrice = 20` spans roughly **15 … 26**.
* WireBuyer resets `wirePriceTimer` on every purchase, so an auto-buying player keeps `wireBasePrice` from drifting down.

### 2.4 Marketing / demand / margin

* `buyAds()` (main.js:3062-3071): `if (funds >= adCost) { marketingLvl++; funds -= adCost; adCost = Math.floor(adCost*2); }` — **exact doubling**, starting at **100** (100, 200, 400, 800, …).
* Re-evaluated every 10 ms tick **only while `humanFlag == 1`**:
  * `marketing = Math.pow(1.1, marketingLvl - 1)` → L1 = 1, L2 = 1.1, L3 = 1.21, …
  * `demand = ((0.8 / margin) * marketing * marketingEffectiveness) * demandBoost`
  * `demand = demand + (demand/10) * prestigeU` (prestigeU = 0 on a fresh run)
  * with defaults (`margin = 0.25`, `marketing = 1`, `marketingEffectiveness = 1`, `demandBoost = 1`): **`demand = 3.2`**.
* UI shows `demand * 10` as a percent (`updateStats`, main.js:3163) — "Public Demand: 3.2 %" while the internal 8 %/frame roll uses `demand/100`.
* Raising price is a direct trade-off: `lowerPrice()` / `raisePrice()` change `margin` by ±0.01 (`Math.round((margin±.01)*100)/100`). `margin` is revenue per clip in dollars and is exactly the inverse driver of demand.
* `marketingEffectiveness` starts 1 and is multiplied by projects: project11 ×1.5, project12 ×2, project34 ×5.
* `demandBoost` starts 1: project37 ×5, project38 ×10.

### 2.5 `sellClips(clipsDemanded)` — main.js:3073-3089

```js
function sellClips(clipsDemanded){
    if (unsoldClips > 0) {
        if (clipsDemanded > unsoldClips){
            transaction = (Math.floor((unsoldClips * margin)*1000))/1000;
            funds  = funds + transaction;
            income = income + transaction;
            clipsSold = clipsSold + unsoldClips;
            unsoldClips = 0;
        } else {
            transaction = (Math.floor((clipsDemanded * margin)*1000))/1000;
            funds = (Math.floor((funds + transaction)*100))/100;   // truncated to 2 dp
            income = income + transaction;
            clipsSold = clipsSold + clipsDemanded;
            unsoldClips = unsoldClips - clipsDemanded;
        }
    }
}
```
* Revenue = `clipsSold_this_call * margin`, rounded down to 1e-3.
* The 100 ms loop calls it with `Math.floor(0.7 * demand^1.15)` only on a `Math.random() < demand/100` roll. **`sellClips` itself has no rate limit and no demand check** — calling it directly is the standard way to drain inventory instantly and to model/beat the demand curve.
* Nothing is sold if `unsoldClips <= 0`.

### 2.6 `calculateRev()` — main.js:3227-3264 (called every 1000 ms while humanFlag==1)

```js
incomeThen = incomeNow; incomeNow = income;
incomeLastSecond = Math.round((incomeNow-incomeThen)*100)/100;
incomeTracker.push(incomeLastSecond); if (incomeTracker.length>10) incomeTracker.splice(0,1);
sum = Σ incomeTracker (rounded to 2dp each addition);
trueAvgRev = sum / incomeTracker.length;

var chanceOfPurchase = demand/100; if (chanceOfPurchase>1) chanceOfPurchase = 1;
if (unsoldClips < 1) chanceOfPurchase = 0;
avgSales = chanceOfPurchase * (0.7*Math.pow(demand,1.15)) * 10;
avgRev   = chanceOfPurchase * (0.7*Math.pow(demand,1.15)) * margin * 10;
if (demand > unsoldClips){ avgRev = trueAvgRev; avgSales = avgRev/margin; }
```
* Display only (`avgRevElement`, `avgSalesElement`); it does not produce income.

### 2.7 `updateStats()` — main.js:3105-3174 (every 10 ms)

Writes: `inchSpan` ("inch"/"inches" when `wire===1`), `clips`, `clipCountCrunched`, `clipmakerRate` (`Math.round(clipRate)`), `clipmakerRate2` (`clipRate` formatted, or `spellf` when `humanFlag===0`), `nanoWire` (`spellf(wire)`), `funds` (2 dp), `unsoldClips`, `demand` (`formatWithCommas(demand*10,0)`), `operations`, `trust`, `nextTrust` (`Math.floor`), `creativity` (if `creativityOn`), `factoryLevelDisplaySpace`, `harvesterLevelSpace`, `wireDroneLevelSpace`, `maxOps` (`memory*1000`).
Note `clips` is overwritten with the scripted endgame numbers when `dismantle >= 1` — irrelevant in stage 1.

---

## 3. Purchases: exact cost formulas and effects

| Action (function) | Cost formula | Effect | Notes |
|---|---|---|---|
| `buyWire()` | `wireCost` ($) | `wire += wireSupply`; `wirePurchase++`; `wireBasePrice += .05`; `wirePriceTimer = 0` | cost is a *price*, not a level-scaled formula |
| `makeClipper()` | **`clipperCost = Math.pow(1.1, clipmakerLevel) + 5`** recomputed **after** each buy | `clipmakerLevel += 1` | Initial cost **5**; level 1 → 6.1, level 2 → 6.21, L10 → 7.59, L50 → 122.4, L75 → 1310.8; project22 requires `clipmakerLevel>=75` |
| `makeMegaClipper()` | **`megaClipperCost = Math.pow(1.07, megaClipperLevel) * 1000`** recomputed after each buy | `megaClipperLevel += 1` | Initial **500** but formula gives 1000 at level 1; gated by `megaClipperFlag` (project22, 12000 ops) for the button, but the function itself only checks funds. 500 clips/s per level × `megaClipperBoost` |
| `buyAds()` | **`adCost = Math.floor(adCost * 2)`** | `marketingLvl += 1` | 100, 200, 400, 800 … |
| `addProc()` | free in $ / ops | `processors += 1`; **`creativitySpeed = Math.log10(processors)*Math.pow(processors,1.1) + processors - 1`** | function guard: `trust>0 || swarmGifts>0`; **the button** is disabled while `trust <= processors + memory` |
| `addMem()` | free | `memory += 1` | same guard/button gate as `addProc` |
| `investDeposit()` | free | `ledger -= Math.floor(funds); bankroll += Math.floor(funds); funds = 0` | floors |
| `investWithdraw()` | free | `ledger += bankroll; funds += bankroll; bankroll = 0` | |
| `investUpgrade()` | **`investUpgradeCost`** (Yomi) | `investLevel++`; `stockGainThreshold += .01`; `investUpgradeCost = Math.floor(Math.pow(investLevel+1, Math.E)*100)` | starts 100; L1→616, L2→1386 … ; no affordability check inside — it will drive `yomi` negative |
| projects(ops) | see §3.1 | `standardOps -= price` | button disabled when `cost()` false |
| `project37` Hostile Takeover | $1,000,000 | `demandBoost *= 5`, `trust += 1` | `trigger: portTotal>=10000` |
| `project38` Full Monopoly | $10,000,000 + 3,000 yomi | `demandBoost *= 10`, `trust += 1` | |
| `project40` A Token of Goodwill | $500,000 | `trust += 1` | `trigger: humanFlag==1 && trust>=85 && trust<100 && clips>=101000000` — one-shot |
| `project40b` Another Token | **`bribe`** | `trust += 1`; **`bribe *= 2`**; re-arms (`uses++`) while `trust<100` | `bribe` starts **1,000,000** → 2M → 4M … (bought repeatedly to push trust 85→100) |

### 3.1 Human-era ops projects (from `projects.js`), exact costs and effects

| id | Title | Cost | Effect |
|---|---|---|---|
| project1 | Improved AutoClippers | 750 ops | `clipperBoost += .25`, `boostLvl = 1` |
| project2 | Beg for More Wire | 1 Trust | `trust -= 1; wire = wireSupply` (repeatable, re-arms) |
| project3 | Creativity | 1000 ops | `creativityOn = true` |
| project4 | Even Better AutoClippers | 2500 ops | `clipperBoost += .5`, `boostLvl = 2` |
| project5 | Optimized AutoClippers | 5000 ops | `clipperBoost += .75`, `boostLvl = 3` |
| project6 | Limerick | 10 creat | `trust += 1` |
| project7 | Improved Wire Extrusion | 1750 ops | `wireSupply *= 1.5` |
| project8 | Optimized Wire Extrusion | 3500 ops | `wireSupply *= 1.75` |
| project9 | Microlattice Shapecasting | 7500 ops | `wireSupply *= 2` |
| project10 | Spectral Froth Annealment | 12000 ops | `wireSupply *= 3` |
| project10b | Quantum Foam Annealment | 15000 ops | `wireSupply *= 11` (trigger `wireCost>=125`) |
| project11 | New Slogan | 2500 ops + 25 creat | `marketingEffectiveness *= 1.5` |
| project12 | Catchy Jingle | 4500 ops + 45 creat | `marketingEffectiveness *= 2` |
| project13 | Lexical Processing | 50 creat | `trust += 1` |
| project14 | Combinatory Harmonics | 100 creat | `trust += 1` |
| project15 | The Hadwiger Problem | 150 creat | `trust += 1` |
| project16 | Hadwiger Clip Diagrams | 6000 ops | `clipperBoost += 5` |
| project17 | The Tóth Sausage Conjecture | 200 creat | `trust += 1` |
| project19 | Donkey Space | 250 creat | `trust += 1` |
| project20 | Strategic Modeling | 12000 ops | `strategyEngineFlag = 1` |
| project21 | Algorithmic Trading | 10000 ops | `investmentEngineFlag = 1` (trigger `trust>=8`) |
| project22 | MegaClippers | 12000 ops | `megaClipperFlag = 1` (trigger `clipmakerLevel>=75`) |
| project23/24/25 | MegaClipper boosts | 14000 / 17000 / 19500 ops | `megaClipperBoost += .25 / .5 / 1` |
| project26 | WireBuyer | 7000 ops | `wireBuyerFlag = 1` (trigger `wirePurchase>=15`) |
| project34 | Hypno Harmonics | 7500 ops + 1 Trust | `marketingEffectiveness *= 5`, `trust -= 1` |
| project42 | RevTracker | 500 ops | `revPerSecFlag = 1` |
| project50 | Quantum Computing | 10000 ops | `qFlag = 1` (trigger `processors>=5`) |
| project51 | Photonic Chip | `qChipCost` ops (10000, +5000 each) | activates `qChips[nextQchip]` |
| project37/38/40/40b | (money/trust projects, see §3) | | |
| project35 | Release the HypnoDrones | 100 Trust | `humanFlag = 0` → **stage 1 ends** |

Project buttons are created by `displayProjects()` and gated by `manageProjects()`: the button is `enabled` iff `project.cost()` is true; clicking calls `project.effect()`, which subtracts the price itself (effects do not re-check affordability — the button state is the only gate).

---

## 4. Processors / memory / creativity / trust / ops

### 4.1 `calculateTrust()` — main.js:3374-3383, every 10 ms while `humanFlag == 1`

```js
if (clips > (nextTrust - 1)) {          // i.e. clips >= nextTrust
    trust = trust + 1;
    var fibNext = fib1 + fib2;
    nextTrust = fibNext * 1000;
    fib1 = fib2; fib2 = fibNext;
}
```
* Fibonacci chain with `fib1 = 2`, `fib2 = 3` → `nextTrust` thresholds (in clips): **3000, 5000, 8000, 13000, 21000, 34000, 55000, 89000, 144000, 233000, 377000, 610000, 987000, 1597000, …**
* The check is a single `if` evaluated per tick, so a huge one-tick jump only grants **one** trust per tick.
* `trust` is **not consumed** by `addProc`/`addMem`. It only gates them: the buttons are disabled while `trust <= processors + memory` (main.js:1171-1177). Practical reading: total `processors + memory <= trust`.
* Other trust sources in stage 1: projects 6, 13, 14, 15, 17, 19 (+1 each), 37, 38, 40, 40b (+1 each).
* `trust` starts at **2**.

### 4.2 `calculateOperations()` — main.js:3413-3452, every 10 ms while `compFlag == 1`

```js
if (tempOps > 0) opFadeTimer++;
if (opFadeTimer > opFadeDelay && tempOps > 0) opFade = opFade + Math.pow(3,3.5)/1000;   // ≈ +0.0468/tick after 8 s
if (tempOps > 0) tempOps = Math.round(tempOps - opFade); else tempOps = 0;
if (tempOps + standardOps < memory*1000) { standardOps = standardOps + tempOps; tempOps = 0; }
operations = Math.floor(standardOps + Math.floor(tempOps));
if (operations < memory*1000) {
    var opCycle = processors/10;
    var opBuf = (memory*1000) - operations;
    if (opCycle > opBuf) opCycle = opBuf;
    standardOps = standardOps + opCycle;
}
if (standardOps > memory*1000) standardOps = memory*1000;
```
* **Net production: `processors/10` ops per 10 ms tick = `processors` ops per second** — independent of memory.
* Cap: `memory * 1000` ops (`maxOps` display). Growth stops at the cap.
* `compFlag` is set to 1 by `milestoneCheck()` when `Math.ceil(clips) >= 2000` (or when unsold=0, funds<wireCost, wire<1).
* `opFadeDelay = 800` ticks = 8 s; `opFade` grows by `3^3.5/1000 ≈ 0.0468` per tick after that.

### 4.3 `quantumCompute()` / `qComp()` — main.js:829-866 (bonus ops, project50/51)

* `quantumCompute()` runs every 10 ms while `qFlag == 1`: `qClock += .01`; for each active chip `value = Math.sin(qClock*waveSeed*active)`.
* `qComp()` is **button-driven** (`btnQcompute`), not automatic. If `qChips[0].active == 0` it only prints "Need Photonic Chips". Otherwise `qq = ceil(Σ values * 360)`, and it adds `qq` to `standardOps` (with a memory-buffer/damper overflow path that diverts into `tempOps`). This is the only way to produce more than `processors` ops/sec in stage 1 — it is unbounded by tick rate, so it can be spammed.

### 4.4 `calculateCreativity()` — main.js:3266-3293, every 10 ms when `creativityOn && operations >= memory*1000`

```js
creativityCounter++;
var creativityThreshold = 400;
var s  = prestigeS/10;
var ss = creativitySpeed + (creativitySpeed * s);     // prestigeS = 0 → ss = creativitySpeed
var creativityCheck = creativityThreshold / ss;
if (creativityCounter >= creativityCheck){
    if (creativityCheck >= 1) creativity = creativity + 1;
    if (creativityCheck < 1)  creativity = creativity + ss/creativityThreshold;
    creativityCounter = 0;
}
```
* With `processors = 1`: `creativitySpeed = log10(1)*1^1.1 + 1 - 1 = 0` → creativity **never** accumulates until a processor is added.
* `processors = 2` → `creativitySpeed = log10(2)*2^1.1 + 1 ≈ 0.301*2.1436 + 1 ≈ 1.6454`; `creativityCheck ≈ 243.1` ticks → **≈ +1 creativity every 2.43 s**.
* `processors = 3` → `≈ 0.4771*3.3480 + 2 ≈ 3.597` → ≈ 1.11 s per creativity.
* Creation **requires ops to be pegged at the cap** (`operations >= memory*1000`), so memory must stay small relative to ops generation.
* `creativityOn` is set by project3 (1000 ops), and turned off by project `2333` (`creativityOn = false`) and project `project2440`-ish (`creativitySpeed = 0`) — post-stage-1.

### 4.5 `transaction`, `bankroll`, `ops`, `memory` globals

* `transaction` = funds amount from the last `sellClips` call (pure output).
* `operations` = `Math.floor(standardOps + Math.floor(tempOps))`.
* `memory` = 1 initial (each level = 1000 op cap).
* `processors` = 1 initial (each level = 1 op/sec).

---

## 5. Investments / stocks

Declared in `main.js:1426-1440`: `stocks = []`, `portfolioSize = 0`, `stockID = 0`, `secTotal = 0`, `portTotal = 0`, `sellDelay = 0`, `riskiness = 5`, `maxPort = 5`, `m = 0`, `investLevel = 0`, `investUpgradeCost = 100`, `stockGainThreshold = .5`, `ledger = 0`, `stockReportCounter = 0`.

Unlocked by **project21** (`investmentEngineFlag = 1`, trigger `trust>=8`, 10000 ops). `buttonUpdate()` hides `investmentEngine` + `investmentEngineUpgrade` while the flag is 0.

### 5.1 `riskiness` ← the `#investStrat` dropdown (every 100 ms, main.js:1619-1625)

| dropdown `value` | label | `riskiness` |
|---|---|---|
| `"low"` | Low Risk | **7** |
| `"med"` | Med Risk | **5** |
| anything else (`"hi"`) | High Risk | **1** |

The 100 ms interval also recomputes `secTotal = Σ stocks[i].total`, `portTotal = bankroll + secTotal`, repaints rows 1-5 (`stock{1..5}Symbol/Amount/Price/Total/Profit`) and blanks rows from `portfolioSize+1` to 4 (loop is `< 5`, a known off-by-one: the 5th row is only blanked when `portfolioSize+1 < 5`).

### 5.2 `investDeposit()` / `investWithdraw()` — main.js:1454-1471

* Deposit: `ledger -= Math.floor(funds); bankroll += Math.floor(funds); funds = 0;`
* Withdraw: `ledger += bankroll; funds += bankroll; bankroll = 0;`
* `ledger` therefore tracks `withdrawn − deposited`; `ledger + portTotal` is the "lifetime revenue" reported by `stockReportCounter`.

### 5.3 `stockShop()` — main.js:1473-1497, once per 1000 ms while `humanFlag == 1`

```js
var budget   = Math.ceil(portTotal / riskiness);
var r        = 11 - riskiness;
var reserves = Math.ceil(portTotal / r);
if (riskiness == 1) reserves = 0;

if ((bankroll-budget) < reserves && riskiness == 1 && bankroll > portTotal/10) budget = bankroll;
else if ((bankroll-budget) < reserves && riskiness == 1) budget = 0;
else if ((bankroll-budget) < reserves) budget = bankroll - reserves;

if (portfolioSize < maxPort && bankroll >= 5 && budget >= 1 && bankroll - budget >= reserves){
    if (Math.random() < .25) createStock(budget);
}
```
* `riskiness = 7` → budget = ⌈port/7⌉, reserves = ⌈port/4⌉. `5` → |port/5|, |port/6|. `1` → budget = bankroll, reserves = 0.
* At most **one new stock per second**, 25 % chance, max **5** concurrent positions.

### 5.4 `createStock(dollars)` — main.js:1499-1543

* Symbol: 1-4 uppercase letters (`≤.01` → 1, `≤.1` → 2, `≤.4` → 3, else 4).
* Price: random roll → `>.99` : `ceil(rand*3000)`; `>.85` : `ceil(rand*500)`; `>.60` : `ceil(rand*150)`; `>.20` : `ceil(rand*50)`; else `ceil(rand*15)`. If `pri > dollars` then `pri = ceil(dollars*roll)`.
* `amt = floor(dollars/pri)`, hard-capped at **1,000,000** shares. `total = pri*amt`; `bankroll -= pri*amt`.

### 5.5 `updateStocks()` — main.js:1581-1613, every 2500 ms if `portfolioSize>0 && humanFlag==1`

```js
for each stock: age++;
  if (Math.random() < .6){
      var gain = true;
      if (Math.random() > stockGainThreshold) gain = false;
      var delta = Math.ceil( (Math.random()*price) / (4*riskiness) );
      if (gain) price += delta; else price -= delta;
      if (price == 0 && Math.random() > .24) price = 1;   // 76 % chance to floor-bounce to 1
      total  = price * amount;
      profit += gain ? delta*amount : -delta*amount;
  }
```
* Expected per-stock drift: `0.6 * (2*stockGainThreshold − 1) * E[delta]`, with `E[delta] ≈ price/(8*riskiness)`. At `stockGainThreshold = .5` the drift is **0**; each `investUpgrade()` adds `.01` (→ 51/49 at level 1) equally; higher `riskiness` shrinks `delta` **and** inflates the buy budget.

### 5.6 `sellStock()` — main.js:1545-1553

```js
bankroll += stocks[0].total;   // FIFO: always the OLDEST stock
stocks.splice(0,1); portfolioSize = stocks.length;
```
* Auto-sold by the 2500 ms interval (30 % chance once `sellDelay >= 5`) or **instantly by calling `sellStock()` directly** — no cost, no cooldown. This is the main automated cash-out.

### 5.7 `stockReportCounter` and `investStratElement`

* `stockReportCounter` (main.js:4230-4237): incremented **once per 10 ms tick while `investmentEngineFlag==1`**; on reaching **10000** (= 100 s) it displays `"Lifetime investment revenue report: $" + formatWithCommas(ledger + portTotal)` and resets to 0. Pure display.
* `investStratElement` is the cached `document.getElementById("investStrat")`; the 100 ms interval reads `.value` to set `riskiness` (§5.1). Code that sets the portfolio strategy programmatically should assign `investStratElement.value = "hi" | "med" | "low"` and either wait ≤100 ms or set `riskiness` directly.

---

## 6. `buttonUpdate()` / `refresh()` — DOM gates relevant to stage 1

### 6.1 `buttonUpdate()` — main.js:1013-1416 (every 10 ms)

Gates that matter in stage 1 (element id → condition):

| Element id | Condition |
|---|---|
| `btnMakePaperclip` | `disabled = (wire < 1)` |
| `btnBuyWire` | `disabled = (funds < wireCost)` |
| `btnMakeClipper` | `disabled = (funds < clipperCost)` |
| `btnExpandMarketing` | `disabled = (funds < adCost)` |
| `btnLowerPrice` | `disabled = (margin <= .01)` |
| `btnRaisePrice` | **never disabled** (no gate); `margin` can be raised without bound |
| `btnAddProc`, `btnAddMem` | `disabled = (trust <= processors+memory && swarmGifts <= 0)` |
| `btnMakeMegaClipper` | `disabled = (funds < megaClipperCost)` |
| `autoClipperDiv` | shown when `autoClipperFlag !== 0`; the flag is force-set to 1 as soon as `funds >= 5` |
| `megaClipperDiv` | shown iff `megaClipperFlag !== 0` (project22) |
| `wireBuyerDiv` | shown iff `wireBuyerFlag !== 0` (project26) |
| `revPerSecDiv` | shown iff `revPerSecFlag !== 0` (project42) |
| `compDiv` | shown iff `compFlag !== 0` (auto-set at 2000 clips) |
| `creativityDiv` | shown iff `creativityOn` (project3) |
| `projectsDiv` | shown iff `projectsFlag !== 0` (auto-set with `compFlag`) |
| `trustDiv`, `businessDiv`, `manufacturingDiv` | shown iff `humanFlag !== 0`; forcing `humanFlag = 0` hides them and zeroes `investmentEngineFlag`/`wireBuyerFlag` |
| `investmentEngine`, `investmentEngineUpgrade` | shown iff `investmentEngineFlag !== 0` (project21) |
| `strategyEngine`, `tournamentManagement` | shown iff `strategyEngineFlag !== 0` (project20) |
| `btnNewTournament` | `disabled = !(operations >= tourneyCost && tourneyInProg == 0)` |
| `btnImproveInvestments` | `disabled = (yomi < investUpgradeCost)` |
| `qComputing` | shown iff `qFlag !== 0` |
| `spaceDiv`, `factoryDivSpace`, `droneDivSpace`, `probeDesignDiv`, `increaseProbeTrustDiv` | `spaceFlag === 0` → `display:none` |
| `powerDiv` | shown only if `project127.flag == 1 && spaceFlag == 0` |
| `coverElement.style.display` | set to `"none"` at the end of every `buttonUpdate()` call |

Because the main loop calls `buttonUpdate()` before `updateStats()` every 10 ms, any state write is reflected in the DOM within one tick.

### 6.2 `refresh()` — main.js:4604-4697 (called after load / prestige load only)

One-shot repaint of every at-startup DOM value, plus: `tourneyInProg = 0`, `victoryDivElement.style.visibility = "hidden"`, `tournamentResultsTableElement.style.display = "none"`, `updateDronePrices()`, `updateUpgrades()`, `updatePower()`, `updatePowPrices()`. It is **not** on a timer; it does not run during play.

---

## 7. Every clickable function in stage 1

Format: signature — preconditions checked **inside** — behaviour when the precondition fails.

| Function | Signature | Internal preconditions | If not affordable |
|---|---|---|---|
| `clipClick` | `clipClick(number)` | `wire >= 1`; clamps `number` to `wire` | silent no-op (nothing changes); **no NaN if you pass a number** |
| `buyWire` | `buyWire()` | `funds >= wireCost` | silent no-op |
| `makeClipper` | `makeClipper()` | `funds >= clipperCost` | no level gained/no funds spent, **but `clipperCost` is still recomputed** (idempotent while level is unchanged) |
| `makeMegaClipper` | `makeMegaClipper()` | `funds >= megaClipperCost` | same — cost recomputed anyway |
| `buyAds` | `buyAds()` | `funds >= adCost` | silent no-op (no cost recompute) |
| `addProc` | `addProc()` | `trust > 0 \|\| swarmGifts > 0` | silent no-op (UI gate is stricter: `trust > processors+memory`) |
| `addMem` | `addMem()` | `trust > 0 \|\| swarmGifts > 0` | silent no-op |
| `raisePrice` | `raisePrice()` | none | n/a (`margin` +0.01, rounds to 2 dp) |
| `lowerPrice` | `lowerPrice()` | `margin >= .01` | silent no-op |
| `toggleWireBuyer` | `toggleWireBuyer()` | none | flips `wireBuyerStatus` 1↔0, rewrites `wireBuyerStatus` ("ON"/"OFF") |
| `investDeposit` | `investDeposit()` | none (works with `funds = 0`) | n/a |
| `investWithdraw` | `investWithdraw()` | none | n/a |
| `investUpgrade` | `investUpgrade()` | **none** — always spends | goes through even if `yomi < cost` → `yomi` goes negative |
| `stockShop` | `stockShop()` | `portfolioSize<maxPort && bankroll>=5 && budget>=1 && bankroll-budget>=reserves` + 25 % roll | silent no-op |
| `sellStock` | `sellStock()` | none; assumes `stocks[0]` exists | **Throws** if the portfolio is empty (`stocks[0].total`) — check `portfolioSize > 0` first |
| `newTourney` | `newTourney()` | none; `standardOps -= tourneyCost` (starts 1000) | ops can go negative; sets `tourneyInProg = 1` |
| `runTourney` | `runTourney()` | none | runs one round per call (via `setTimeout` chain) until `currentRound >= rounds`, then pays Yomi |
| `toggleAutoTourney` | `toggleAutoTourney()` | none | flips `autoTourneyStatus` |
| `qComp` | `qComp()` | `qChips[0].active != 0` | prints "Need Photonic Chips" |
| `clipClick(1)` (button) | `clipClick(1)` | `wire >= 1` | silent no-op |
| project buttons | `projectN.effect()` | none inside `effect()`; the button is enabled only while `projectN.cost()` is true | if forced, the effect runs anyway and can push `standardOps`/`creativity`/`funds`/`trust` negative |
| cheats (debug only) | `cheatClips/cheatMoney/cheatTrust/cheatOps/cheatCreat/cheatYomi/cheatHypno` | none | e.g. `cheatTrust()` → `trust += 1`; `cheatCreat()` → `creativity += 1000` |

**Direct-call notes for an automated player**

* `sellClips(n)` can be called directly at any rate with any `n`; it has *no* RNG and *no* demand check, so calling it in a loop instantly converts all `unsoldClips` to `funds` at `margin` per clip. It also does not update `avgRev` (that is `calculateRev`'s job).
* `clipClick(1)` has no per-tick limit; calling it repeatedly inside one 10 ms tick produces arbitrary clips (bounded only by `wire`). Auto-clippers are therefore only useful because they free the player from clicking.
* `calculateTrust()` is only invoked from the 10 ms loop, so raising `clips` artificially does not grant trust until the next tick.
* `stockShop`, `sellStock`, `updateStocks` are all directly callable without waiting for their timers.

---

## 8. Stage-1 state variables and initial values

### 8.1 From `globals.js` (true initial values)

```
clips = 0            unusedClips = 0      clipRate = 0        clipRateTemp = 0
prevClips = 0        clipRateTracker = 0  clipmakerRate = 0
clipmakerLevel = 0   clipperCost = 5      unsoldClips = 0     funds = 0
margin = .25         wire = 1000          wireCost = 20       adCost = 100
demand = 5           clipsSold = 0        avgRev = 0          income = 0
incomeTracker = [0]  ticks = 0            marketing = 1       marketingLvl = 1
processors = 1       memory = 1           operations = 0      trust = 2
nextTrust = 3000     transaction = 1      clipperBoost = 1
creativity = 0       creativityOn = false boostLvl = 0        wirePurchase = 0
wireSupply = 1000    marketingEffectiveness = 1                milestoneFlag = 0
bankroll = 0         fib1 = 2             fib2 = 3
strategyEngineFlag = 0   investmentEngineFlag = 0   revPerSecFlag = 0
compFlag = 0         projectsFlag = 0     autoClipperFlag = 0 megaClipperFlag = 0
megaClipperCost = 500   megaClipperLevel = 0   megaClipperBoost = 1
creativitySpeed = 1  creativityCounter = 0   wireBuyerFlag = 0  demandBoost = 1
humanFlag = 1        trustFlag = 1        nanoWire = 0        creationFlag = 0
wireProductionFlag = 0   spaceFlag = 0    factoryFlag = 0     harvesterFlag = 0
wireDroneFlag = 0    factoryLevel = 0     factoryBoost = 1    droneBoost = 1
harvesterLevel = 0   wireDroneLevel = 0   harvesterBill = 0   wireDroneBill = 0
factoryBill = 0      probeCount = 0       qFlag = 0           qClock = 0
qChipCost = 10000    nextQchip = 0        bribe = 1000000     battleFlag = 0
prestigeU = 0        prestigeS = 0        wirePriceCounter = 0  wireBasePrice = 20
wireBuyerStatus = 1  wirePriceTimer = 0   tempOps = 0         standardOps = 0
opFade = 0           opFadeTimer = 0      opFadeDelay = 800   dismantle = 0
maxTrust = 20        maxTrustCost = 91117.99  resetFlag = 2
```

### 8.2 Declared in `main.js` (module scope, initial values)

```
stocks = []          alphabet = A..Z      portfolioSize = 0   stockID = 0
secTotal = 0         portTotal = 0        sellDelay = 0       riskiness = 5
maxPort = 5          m = 0                investLevel = 0     investUpgradeCost = 100
stockGainThreshold = .5   ledger = 0      stockReportCounter = 0
tourneyCost = 1000   tourneyLvl = 1       stratCounter = 0    roundNum = 0
tourneyInProg = 0    yomi = 0             yomiBoost = 1       resultsTimer = 0
resultsFlag = 0      saveTimer = 0        secTimer = 0        incomeThen/Now, trueAvgRev, revTimer, avgSales, incomeLastSecond, sum (undefined)
maxFactoryLevel = 0  maxDroneLevel = 0    p10h..p1000w = 0
```

### 8.3 Values loaded from a save (`load()` / `load1()` / `load2()`)

`load()` restores basically all of the above **plus** the investment block (`portfolioSize`, `stockID`, `secTotal`, `portTotal`, `sellDelay`, `riskiness`, `maxPort`, `m`, `investLevel`, `investUpgradeCost`, `stockGainThreshold`, `ledger`, `stockReportCounter`) and the tournament block. `refresh()` is called at the end of each load, and `save()` runs automatically every 25 s (250 × 100 ms).

### 8.4 Not saved (reset to their module-literal values on every page load)

`saveTimer`, `secTimer`, `incomeThen`, `incomeNow`, `incomeLastSecond`, `trueAvgRev`, `sum`, `avgSales`, `results` (array), `tempOps` *is* saved, `opFadeTimer` is not.

---

## 9. Quick-reference constant table

| Constant | Value |
|---|---|
| Tick rate | 100 ticks/s (10 ms) |
| Autoclipper output | `clipperBoost * clipmakerLevel / 100` clips per tick = `clipmakerLevel` clips/s at boost 1 |
| MegaClipper output | `megaClipperBoost * megaClipperLevel * 5` clips per tick = `5 * megaClipperLevel * megaClipperBoost` per 10 ms = 500/s/level at boost 1 |
| Manual clip | 1 clip, 1 wire per call |
| `clipperCost` | `1.1^L + 5` (L = current level), initial 5 |
| `megaClipperCost` | `1.07^L * 1000` (L = current level), initial 500 |
| `adCost` | doubles each buy, initial 100 |
| `wireSupply` | 1000, ×1.5/×1.75/×2/×3/×11 via projects |
| `wireCost` | `ceil(wireBasePrice + 6*sin(wirePriceCounter))`, updated with p = 1.5 %/100 ms |
| `wireBasePrice` | 20, −0.1 %/25 s while > 15, +0.05 per buy |
| `demand` | `(0.8/margin) * 1.1^(marketingLvl−1) * marketingEffectiveness * demandBoost * (1 + prestigeU/10)` |
| Sale chance per 100 ms | `demand/100` (capped at 1) |
| Sale size | `floor(0.7 * demand^1.15)` clips |
| Revenue | `clips * margin`, floored to 0.001 |
| Ops rate | `processors` ops/s (capped at `memory*1000`) |
| Ops via qComp | `ceil(Σ sin(qClock*waveSeed*active) * 360)` per click |
| `creativitySpeed` | `log10(processors) * processors^1.1 + processors − 1` |
| Creativity rate | 1 per `400/creativitySpeed` ticks while ops are capped |
| `nextTrust` thresholds | 3000, 5000, 8000, 13000, 21000, 34000, 55000, 89000, 144000, … (= 1000 × Fibonacci from 3, 5, 8, …) |
| Processor/memory cap | `processors + memory <= trust` |
| Investment riskiness | Low 7 / Med 5 / High 1 |
| Stock buy budget | `bankroll` (risk 1) or `⌈portTotal/riskiness⌉` |
| Stock drift/tick | 60 % chance, gain with p = `stockGainThreshold`, `delta = ⌈rand*price/(4*riskiness)⌉`, every 2500 ms |
| Auto-sell | 30 % chance every 2500 ms once `sellDelay >= 5` |
| Stage-1 exit | `project35` at 100 Trust → `humanFlag = 0` |
