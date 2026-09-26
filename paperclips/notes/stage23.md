# Universal Paperclips — Stage 2/3 Mechanics Reference (SPACE era + ENDING)

Source of truth: `main.js`, `projects.js`, `combat.js`, `globals.js`, `index2.html`
in `D:\1-2\MAAOrch\paperclips\`. All line numbers refer to those files.

Global game clock: `window.setInterval(..., 10)` in `main.js:4188-4556` = **100 ticks/sec**.
Secondary loop `main.js:4564` (1000 ms) does wire price, sales, and the autosave.
`combat.js` starts one `setInterval(Update, 16)` (`combat.js:288`) ≈ 62.5 fps at page load; it is page-global, not per battle.
Unless stated otherwise, every formula below is executed **once per 10 ms tick** by the main loop.

---

## 0. State-flag model (the whole game in 4 flags)

| flag | meaning | set where |
|---|---|---|
| `humanFlag` | 1 = human era (business/trust/marketing/sales). 0 = post-human ("creation") era. | `projects.js:729` (project35) |
| `spaceFlag` | 1 = von Neumann probe era. | `projects.js:1128` (project46) |
| `dismantle` | 0 → 7, endgame teardown counter. | `projects.js:2199/2227/2255/2283/2310/2335/2363` |
| `milestoneFlag` | 0 → 20 narrative milestones. 15 = endgame message chain open. | `main.js:3458-3547` |

Stage 2 (SPACE) = `humanFlag==0 && spaceFlag==1`.
Stage 3 (ENDING) = `dismantle >= 1`, finishing at `milestoneFlag == 20`.

---

## 1. TRANSITION: human era → space era

### 1a. Ending the human era — **project35 "Release the HypnoDrones"**
`projects.js:711-756`.
- `trigger`: `project70.flag == 1` (project70 "HypnoDrones", `projects.js:688`, requires `project34.flag==1` "Hypno Harmonics", 70,000 ops).
- `cost`: `trust >= 100`.
- `effect` (exact, `projects.js:721-753`):
  ```
  project35.flag = 1
  trust = 0
  clipmakerLevel = 0
  megaClipperLevel = 0
  nanoWire = wire
  humanFlag = 0
  ```
  Also removes project219 and project40b from the project list, calls `hypnoDroneEvent()`, and sets
  `document.getElementById("transWire").innerHTML = wire`.
- `buttonUpdate()` then (`main.js:1260-1273`) hides `businessDiv`, `manufacturingDiv`, `trustDiv`,
  forces `investmentEngineFlag = 0` and `wireBuyerFlag = 0`, and shows `creationDiv`.
- Milestone: `milestoneFlag == 6 && project35.flag == 1` → "Full autonomy attained" (`main.js:3499`).
- **Nothing about probes exists yet.** In this intermediate phase you build `factoryLevel`,
  `harvesterLevel`, `wireDroneLevel`, `farmLevel`, `batteryLevel` (power UI is shown once
  `project127.flag==1 && spaceFlag==0`, `main.js:1023`).

### 1b. Entering the space era — **project46 "Space Exploration"**
`projects.js:1114-1147`.
- `trigger`: `humanFlag == 0 && availableMatter == 0`
  (`availableMatter` starts at `Math.pow(10,24)*6000` = **6e27** and is drained to 0 by `acquireMatter`).
- `cost`: `operations >= 120000 && storedPower >= 10000000 && unusedClips >= Math.pow(10,27)*5` (= 5e27 clips).
- `effect` (exact, `projects.js:1124-1143`):
  ```
  loadThrenody();  project46.flag = 1;
  boredomLevel = 0;
  spaceFlag = 1;
  standardOps -= 120000;
  storedPower -= 10000000;
  unusedClips -= Math.pow(10,27)*5;
  displayMessage("Von Neumann Probes online");
  factoryReboot(); harvesterReboot(); wireDroneReboot(); farmReboot(); batteryReboot();
  farmLevel = 1;
  powMod = 1;
  probeCostDisplayElement.innerHTML = spellf(probeCost);
  ```
  Note `farmReboot()` afterwards sets `farmCost = 10000000`, `farmLevel` was reset to 0 and then forced to 1
  (so solar farms still exist in space). All factory/harvester/wire-drone levels go to **0** and their
  purchase bills are refunded to `unusedClips`.
- `spaceFlag = 1` immediately reveals the space UI (`main.js:1311-1326`) and `mpdsDivElement`
  (`main.js:1029-1033`), and hides the ground factory/harvester/wire-drone panels.
- `milestoneFlag == 13 && spaceFlag == 1` → "Terrestrial resources fully utilized" (`main.js:3534`).

### 1c. What is spawned at `spaceFlag == 1` (main loop order, `main.js:4247-4299`)
```
if (probeCount >= 1) exploreUniverse();          // main.js:4250
if (humanFlag==0 && spaceFlag==0) updateDroneButtons();
if (humanFlag == 0) { updatePower(); updateSwarm(); acquireMatter(); processMatter(); }
if (dismantle<4) clipClick(powMod*fbst*(Math.floor(factoryLevel)*factoryRate));
if (spaceFlag == 1) {
    if (probeCount<0) probeCount = 0;
    encounterHazards();
    spawnFactories();
    spawnHarvesters();
    spawnWireDrones();
    spawnProbes();
    drift();
    war();
}
if (dismantle<4) { clipClick(clipperBoost*(clipmakerLevel/100)); clipClick(megaClipperBoost*(megaClipperLevel*5)); }
```

---

## 2. PROBE / VON NEUMANN MECHANICS

### 2a. Constants and state (`main.js:3775-3801`, `globals.js:73-95,129-131`)
| var | init | meaning |
|---|---|---|
| `probeCount` | 0 | live probes |
| `probeSpeed` | 0 | trust spent on speed |
| `probeNav` | 0 | trust spent on navigation |
| `probeRep` | 0 | trust spent on self-replication |
| `probeHaz` | 0 | trust spent on hazard remediation |
| `probeFac` | 0 | trust spent on factory production |
| `probeHarv` | 0 | trust spent on harvester production |
| `probeWire` | 0 | trust spent on wire-drone production |
| `probeCombat` | 0 | trust spent on combat (`combat.js:24`) |
| `probeTrust` | 0 | total trust points bought (`increaseProbeTrust`) |
| `probeUsedTrust` | 0 | `= probeSpeed+probeNav+probeRep+probeHaz+probeFac+probeHarv+probeWire+probeCombat` (`main.js:1359`) |
| `maxTrust` | 20 | cap on `probeTrust` (`globals.js:130`) |
| `honor` | 0 | currency that buys `maxTrust` |
| `maxTrustCost` | 91117.99 | honor cost per `+10 maxTrust` |
| `probeTrustCost` | dynamic, init `Math.floor(Math.pow(probeTrust+1,1.47)*500)` = 500 | yomi cost of next trust point |
| `probeXBaseRate` | 1750000000000000000 (1.75e18) | universe-exploration constant |
| `probeRepBaseRate` | .00005 | replication constant |
| `probeHazBaseRate` | .01 | hazard constant |
| `probeFacBaseRate` | .000001 | |
| `probeHarvBaseRate` | .000002 | |
| `probeWireBaseRate` | .000002 | |
| `probeDriftBaseRate` | .000001 | value-drift constant |
| `probeCost` | `Math.pow(10,17)` = 1e17 clips | cost per probe, **never increases** (the increase lines are commented out at `main.js:3954-3955`) |
| `partialProbeSpawn`, `partialProbeHaz` | 0 | fractional carry accumulators |
| `probesLostHaz`, `probesLostDrift`, `probesLostCombat`, `probesDescendents`, `probeLaunchLevel` | 0 | counters |
| `drifterCount` | 0 | hostile drifters |
| `totalMatter` | `Math.pow(10,54)*30` = 3e55 (`globals.js:88`) | |
| `foundMatter` | `availableMatter` = 6e27 (`globals.js:73,89`) | |
| `factoryRate` 1e9, `harvesterRate` 26180337, `wireDroneRate` 16180339 (`globals.js:81-83`) | | |
| `farmRate` 50, `batterySize` 10000, `factoryPowerRate` 200, `dronePowerRate` 1 (`globals.js:107-110`) | | |
| `giftPeriod` 125000 (`globals.js:125`) | | |

### 2b. Buying design points
- `increaseProbeTrust()` `main.js:3805`: requires `yomi >= probeTrustCost && probeTrust < maxTrust`.
  Effects: `yomi -= probeTrustCost; probeTrust++; probeTrustCost = floor((probeTrust+1)^1.47 * 500)`.
- `increaseMaxTrust()` `main.js:3818`: requires `honor >= maxTrustCost`. Effects:
  `honor -= maxTrustCost; maxTrust += 10`. (Honor → `maxTrustCost` is fixed at 91117.99 forever, never updated.)
  Enabled when `project121.flag == 1 && honor >= maxTrustCost` (`main.js:1117-1120`, `1081-1087`).
- Trust allocators (`main.js:3830-3943`), each with an exact precondition:
  - `raiseProbeSpeed()` / `raiseProbeNav()` / `raiseProbeHaz()` / `raiseProbeRep()` / `raiseProbeFac()` /
    `raiseProbeHarv()` / `raiseProbeWire()` / `raiseProbeCombat()`: **require `probeUsedTrust < probeTrust`**;
    increment the point. `raiseProbeSpeed()` additionally does `attackSpeed += attackSpeedMod` (`.1`).
  - `lowerX()` for each: require the counter `> 0`; decrement. `lowerProbeSpeed()` does `attackSpeed -= .1`.
  - `attackSpeed` init `.2` (`combat.js:26`), `attackSpeedMod` init `.1` (`combat.js:29`).
- **Practical consequence:** "efficiency" points cost the same as dump points; level 0 → 1 is the huge jump
  (`(0+1)^1.6=1` vs `(1+1)^1.6=3.03`, `(2+1)^1.6=5.8`, `(3+1)^1.6=9.19`, `(4+1)^1.6=13.1`, `(5+1)^1.6=17.6`).

### 2c. Creating probes
- `makeProbe()` `main.js:3946`: **requires `unusedClips > probeCost` (1e17, strict `>`, not `>=`).**
  `unusedClips -= probeCost; probeLaunchLevel++; probeCount++;`. This is the "Launch Probe" button (one at a time).
- `spawnProbes()` `main.js:3961-3990` — replication, called every tick when `spaceFlag==1`:
  ```js
  var nextGen = probeCount * 0.00005 * probeRep;              // probeRepBaseRate * probeRep
  if (probeCount >= 999999999999999999999999999999999999999999999999) nextGen = 0;   // 1e48 cap
  if (nextGen > 0 && nextGen < 1) {                            // fractional carry
      partialProbeSpawn += nextGen;
      if (partialProbeSpawn >= 1) { nextGen = 1; partialProbeSpawn = 0; }
  }
  if ((nextGen*probeCost) > unusedClips) nextGen = Math.floor(unusedClips/probeCost);   // clips-funded
  unusedClips -= nextGen*probeCost;
  probeDescendents += nextGen;
  probeCount += nextGen;
  ```
  Replication is therefore gated on **clips** (1e17 each) as well as `probeRep`.

### 2d. Exploring the universe / acquiring matter
- `exploreUniverse()` `main.js:3992-4003`, called when `probeCount >= 1`:
  ```js
  var xRate = Math.floor(probeCount) * 1.75e18 * probeSpeed * probeNav;
  if (xRate > totalMatter - foundMatter) xRate = totalMatter - foundMatter;
  foundMatter    += xRate;
  availableMatter += xRate;
  colonizedDisplay = (100/(totalMatter/foundMatter)).toFixed(12);
  ```
  **`probeSpeed==0` or `probeNav==0` ⇒ xRate = 0 ⇒ the universe is never explored.** Both must be ≥ 1.
- `acquireMatter()` `main.js:4105-4138`, every tick while `humanFlag==0`:
  ```js
  if (availableMatter > 0) {
      var dbsth = 1; if (droneBoost > 1) dbsth = droneBoost * Math.floor(harvesterLevel);
      var mtr = powMod * dbsth * Math.floor(harvesterLevel) * harvesterRate;
      mtr = mtr * ((200 - sliderPos)/100);      // slider at 0 → ×2.0 ; at 200 → ×0
      if (mtr > availableMatter) mtr = availableMatter;
      availableMatter -= mtr;
      acquiredMatter  += mtr;
      mapsElement.innerHTML = spellf(mtr*100);
  } else mapsElement.innerHTML = 0;
  ```
- `processMatter()` `main.js:4140-4169`, every tick while `humanFlag==0`:
  ```js
  if (acquiredMatter > 0) {
      var dbstw = 1; if (droneBoost > 1) dbstw = droneBoost * Math.floor(wireDroneLevel);
      var a = powMod * dbstw * Math.floor(wireDroneLevel) * wireDroneRate;
      a = a * ((200 - sliderPos)/100);
      if (a > acquiredMatter) a = acquiredMatter;
      acquiredMatter -= a;
      wire += a;
      wppsElement.innerHTML = spellf(a*100);
  } else wppsElement.innerHTML = 0;
  ```
- **Slider semantics:** `#slider` is `<input type="range" min="0" max="200" value="0">` (`index2.html:435`).
  `sliderPos` is read in `updateSwarm()` **only when `swarmFlag == 1`** (`main.js:2651-2653`).
  Multiplier `(200-sliderPos)/100` ⇒ slider 0 = 2× output (default, "Work"); 100 = 1×; 200 = 0×.
  The slider also scales swarm gift generation (see 2g).

### 2e. Probe-built infrastructure
All three are called every tick at `spaceFlag==1` and spend **clips** (`unusedClips`).
- `spawnFactories()` `main.js:4032`: `amount = probeCount * 1e-6 * probeFac`; each factory costs **1e8 clips**
  (`if (amount*100000000 > unusedClips) amount = Math.floor(unusedClips/100000000)`);
  `factoryLevel += amount`.
- `spawnHarvesters()` `main.js:4045`: `amount = probeCount * 2e-6 * probeHarv`; each costs **2e6 clips**;
  `harvesterLevel += amount`.
- `spawnWireDrones()` `main.js:4058`: `amount = probeCount * 2e-6 * probeWire`; each costs **2e6 clips**;
  `wireDroneLevel += amount`.
- Manual equivalents (also usable): `makeFactory()` `main.js:2419` (cost `factoryCost`, increases with a
  decaying `fcmod`), `makeHarvester(amount)` `main.js:2460` (cost `Math.pow(harvesterLevel+1,2.25)*1e6`),
  `makeWireDrone(amount)` `main.js:2484` (same formula). Reboots refund the full bill:
  `factoryReboot()` `2629`, `harvesterReboot()` `2607`, `wireDroneReboot()` `2618`.
- `spawnFactories` also calls `clipClick` indirectly? **No** — `clipClick(number)` is what converts
  `wire` into `clips`/`unusedClips` (see 2f).

### 2f. Clip creation and the swarm's role
- `clipClick(number)` `main.js:2326-2362`:
  ```js
  if (dismantle>=4) finalClips++;              // every call after dismantle 4 counts up the "last 100 clips"
  if (wire >= 1) {
      if (number > wire) number = wire;        // wire is the hard cap
      clips += number; unsoldClips += number; wire -= number; unusedClips += number;
      ...
  }
  if (dismantle>=4) transWireElement.innerHTML = formatWithCommas(wire);
  ```
- Main loop calls it with `clipClick(powMod*fbst*(Math.floor(factoryLevel)*factoryRate))` where
  `fbst = factoryBoost * factoryLevel` if `factoryBoost > 1` (project102 ⇒ 1000), else 1 (`main.js:4273-4282`),
  plus `clipClick(clipperBoost*(clipmakerLevel/100))` and `clipClick(megaClipperBoost*(megaClipperLevel*5))`
  (`main.js:4303-4306`). `clipClick` is invoked **twice + 1 per tick** while `dismantle < 4`; after
  `dismantle >= 4` the `clipClick(...)` calls are skipped, but **`finalClips` is only incremented inside
  `clipClick`** — so after dismantle 4 nothing increments `finalClips` from the loop; the goalline is
  reached by the *ending sequence's* own wiring (see §4) and by manual `clipClick(1)` button presses.
  **For an automated player: call `clipClick(1)` 100 times after `dismantle >= 4` to drive `finalClips` to 100.**

### 2g. The swarm
- Unlock: `project126 "Swarm Computing"` `projects.js:1683-1705`
  — `trigger: harvesterLevel + wireDroneLevel >= 200`, `cost: yomi >= 36000`; effect `swarmFlag = 1`.
- `updateSwarm()` `main.js:2644-2830`, every tick when `humanFlag == 0`:
  ```js
  if (isNaN(parseFloat(swarmGifts)) || swarmGifts<0) swarmGifts = 0;
  if (swarmFlag == 1) sliderPos = sliderElement.value;         // the ONLY place sliderPos is written
  boredom: if (availableMatter==0 && (harvesterLevel+wireDroneLevel)>=1) boredomLevel++;
           else if (availableMatter>0 && boredomLevel>0) boredomLevel--;
           if (boredomLevel >= 30000) { boredomFlag = 1; boredomLevel = 0; }
  droneRatio = max(h+1,w+1)/min(h+1,w+1);
  if (droneRatio < 1.5 && disorgCounter > 1) disorgCounter -= .01;
  else if (droneRatio > 1.5) { var x = droneRatio/10000; if (x>.01) x=.01; disorgCounter += x; }
  if (disorgCounter >= 100) disorgFlag = 1;
  var d = Math.floor(harvesterLevel + wireDroneLevel);          // swarm size
  if (giftCountdown <= 0) {
      nextGift = Math.round((Math.log10(d)) * sliderPos/100);
      if (nextGift <= 0) nextGift = 1;
      swarmGifts += nextGift; giftBits = 0;
  }
  // STATUS PRIORITY (later assignments win):
  if (powMod == 0) swarmStatus = 6;                             // Sleeping
  if (spaceFlag == 1 && project130.flag == 0) swarmStatus = 9;  // "NO RESPONSE..." — THE TRAP
  if (d == 0) swarmStatus = 7; else if (d == 1) swarmStatus = 8;
  if (swarmFlag == 0) swarmStatus = 6;
  if (boredomFlag == 1) swarmStatus = 3;                        // Bored
  if (disorgFlag  == 1) swarmStatus = 5;                        // Disorganized
  if (swarmStatus == 0) {                                       // Active only
      giftBitGenerationRate = Math.log(d) * (sliderPos/100);
      giftBits += giftBitGenerationRate;
      giftCountdown = (giftPeriod - giftBits) / giftBitGenerationRate;
  }
  ```
  Status meanings: 0 Active, 1 Hungry, 2 Confused, 3 Bored, 4 Cold, 5 Disorganized, 6 Sleeping, 7 (status div hidden), 8 Lonely, 9 NO RESPONSE.
- **`project130 "Reboot the Swarm"` `projects.js:1774-1794`** is mandatory in the space stage:
  `trigger: spaceFlag == 1 && harvesterLevel + wireDroneLevel >= 2`, `cost: operations >= 100000`.
  Without it `swarmStatus` is pinned to 9 and **`swarmGifts` never grows** (no giftBits accumulation),
  so `addProc()`/`addMem()` have no post-human currency.
  Note that `project130.flag` is only set by clicking it; `swarmFlag` (project126) is a different flag.
- `swarmGifts` is spent by `addProc()` (`main.js:3385`, `processors++`, `swarmGifts--` when `humanFlag==0`)
  and `addMem()` (`main.js:3401`, `memory++`, `swarmGifts--` when `humanFlag==0`). Both require `trust>0 || swarmGifts>0`.
- Repair functions: `synchSwarm()` `main.js:2832` (costs `synchCost` = 5000 yomi, clears `disorgFlag`);
  `entertainSwarm()` `main.js:2841` (costs `entertainCost` = 10000 creativity, then `entertainCost += 10000`,
  clears `boredomFlag`).
- **Gift math for an automated player:** with `d` swarm size and slider at 0, `nextGift = 1` and
  `giftBitGenerationRate = 0` ⇒ `giftCountdown` becomes `Infinity` and the swarm never gifts again.
  To farm gifts you must set `sliderElement.value` (0-200); e.g. slider 100, d=1000 ⇒ rate `3*1 = 3`/tick,
  `giftCountdown = 125000/3 ≈ 41,667` ticks ≈ 7 minutes per 1 gift. Slider must stay **> 0** for any gifting.

### 2h. Power
- `updatePower()` `main.js:2952-3058`, called every tick when `humanFlag==0`, **only executes when
  `humanFlag==0 && spaceFlag==0`** — i.e. power is *not* recomputed after `spaceFlag = 1`.
  `powMod` keeps whatever value project46 forced: **`powMod = 1`** (it can grow via `momentum`, see below,
  but `updatePower` no longer runs in the space era).
  ```js
  var supply   = farmLevel * farmRate/100;                                    // = farmLevel*0.5
  var dDemand  = harvesterLevel*dronePowerRate/100 + wireDroneLevel*dronePowerRate/100;
  var fDemand  = factoryLevel*factoryPowerRate/100;                            // *2 per factory
  var demand   = dDemand + fDemand;
  var cap      = batteryLevel * batterySize;                                   // 10000 per battery
  if (supply >= demand) { xsSupply = supply-demand;
       if (storedPower < cap) { if (xsSupply > cap-storedPower) xsSupply = cap-storedPower;
                                storedPower += xsSupply; }
       if (powMod < 1) powMod = 1;
       if (momentum == 1) powMod += .0005;                                     // project125 "Momentum"
  } else { xsDemand = demand-supply;
       if (storedPower > 0) {
           if (storedPower >= xsDemand) { if (momentum==1) powMod += .0005;
                                          storedPower -= xsDemand; }
           else { xsDemand -= storedPower; storedPower = 0;
                  nuSupply = supply - xsDemand; powMod = nuSupply/demand; }
       } else if (storedPower <= 0) { powMod = supply/demand; }
  }
  ```
  `momentum` is set to 1 by project125 (`projects.js:1660`, requires `farmLevel >= 30`, 20,000 creativity).
- `makeFarm(amount)` `main.js:2891`: cost `Math.pow(farmLevel+1,2.78)*100000000`, then
  `farmCost = Math.pow(farmLevel+1,2.78)*1e8`. `farmReboot()` `2910` resets to `farmCost = 10000000`.
- `makeBattery(amount)` `main.js:2921`: cost `Math.pow(batteryLevel+1,2.54)*10000000`.
  `batteryReboot()` `2940` also sets `storedPower = 0`, `batteryCost = 1000000`.
- `storedPower` is only *reduced* by project46 (`-= 1e7`); in the space era it is effectively frozen.

### 2i. Hazards
- `encounterHazards()` `main.js:4005-4030`, every tick at `spaceFlag==1`:
  ```js
  var boost  = Math.pow(probeHaz, 1.6);
  var amount = probeCount * (0.01 / ((3*boost)+1));         // probeHazBaseRate / (3*boost+1)
  if (project129.flag == 1) amount = .50 * amount;          // "Elliptic Hull Polytopes"
  if (amount < 1) { partialProbeHaz += amount;
                    if (partialProbeHaz >= 1) { amount = 1; partialProbeHaz = 0;
                        probeCount -= 1; probesLostHaz += 1; } }
  else { if (amount > probeCount) amount = probeCount; probeCount -= amount; probesLostHaz += amount; }
  ```
  ⚠ There is **no `break`/`return`** in the `amount < 1` branch — when the carry fires it subtracts 1, then
  the `else` block is skipped, so exactly 1 probe is lost per carried unit. Total hazard loss rate ≈ 1%/tick
  of the swarm at `probeHaz = 1`, halved by project129.
- `project129 "Elliptic Hull Polytopes"` `projects.js:1752`: `trigger: probesLostHaz >= 100`, 125,000 ops.

### 2j. Value drift
- `drift()` `main.js:4071-4085`, every tick at `spaceFlag==1`:
  ```js
  var amount = probeCount * 0.000001 * Math.pow(probeTrust, 1.2);   // probeDriftBaseRate
  if (amount > probeCount) amount = probeCount;
  if (project148.flag == 1) amount = 0;                             // "Reject" removes drift forever
  probeCount  -= amount;
  drifterCount += amount;
  probesLostDrift += amount;
  ```
  `probeTrust` 0/1 ⇒ drift ≈ 0 or 1e-6·probeCount/tick. `probeTrust = 10` ⇒ ×15.8; `probeTrust = 20` ⇒ ×36.4.
  Value drift is the *only* source of `drifterCount`.

### 2k. War
- `war()` `main.js:4087-4098` = `checkForBattles()` (everything else commented out).
- `checkForBattles()` `combat.js:55-63`: fires when
  `drifterCount > warTrigger (1000000) && probeCount > 0 && battles.length < maxBattles (1)`,
  then a 50 % coin flip (`Math.random()*100 >= 50`) → sets `battleFlag = 1` and calls `createBattle()`.
- `battleFlag` is only ever set to 1 here (`combat.js:59`); `buttonUpdate()` uses it to show
  `drifterDivElement` and `battleCanvasDivElement` (`main.js:1089-1099`).
- **The exact win/lose condition (non-negotiable):**
  `battleLEFTSHIPS = Math.ceil(clipProbes/1000000)` and `battleRIGHTSHIPS = Math.ceil(drifterProbes/1000000)`,
  each capped at 200 (with a further random cap for the left side when it lands exactly on 200). In practice
  both are 200 for any late-game swarm. Victory requires `numRightShips == 0`; defeat is `numLeftShips == 0`.
- `project120 "The OODA Loop"` `projects.js:1611`: `trigger: project131.flag==1 && probesLostCombat >= 10000000`,
  cost 175,000 ops + 45,000 yomi; effect `attackSpeedFlag = 1` (lets `probeSpeed` feed defensive maneuvering
  in `DoCombat`: `ooda = probeSpeed * .2`, added to `battleDEATH_THRESHOLD` each probe death-check).
- `project121 "Name the battles"` `projects.js:1636`: `trigger: probesLostCombat >= 10000000`, 225,000 creativity;
  effect `battleNameFlag = 1; battleEndTimer = 200;` and it unlocks the max-trust/honor UI
  (`main.js:1081-1087`) and battle result display (`combat.js:308`).

---

## 3. COMBAT (`combat.js`, read in full — 802 lines)

### 3a. Canvas & DOM
- Canvas: `<canvas id="canvas">` inside `<div id="battleCanvasDiv">` (`index2.html:690-713`).
- Display size is forced in code: `canvas.width = battleWIDTH (310)`, `canvas.height = battleHEIGHT (150)`
  (`combat.js:285-286`). Grid: 31 × 15 cells (`battleGRID_WIDTH/HEIGHT`, `combat.js:4-5`).
- HUD elements written by combat: `victoryDiv`, `battleResult`, `battleResultSign`, `honorAmount`,
  `honorDisplay`, `battleName`, `battleScale`, `probesLostCombatDisplay`, `driftersKilled`, `drifterCount`.
- Combat constants: `battleMAXSPEED = 2`, `battleDEATH_THRESHOLD = 0.5`, `battleLEFTCOLOR = "#ffffff"`,
  `battleRIGHTCOLOR = "#000000"` (`combat.js:12-17`); `probeCombatBaseRate = .15` (`combat.js:25`);
  `drifterCombat = 1.75` (`combat.js:38`); `warTrigger = 1000000` (`combat.js:39`);
  `maxBattles = 1` (`combat.js:34`); `battleEndTimer = 100` initially, 200 after project121
  (`combat.js:44`, `projects.js:1649`); `outcomeTimer = 150`; `battleAlarm = 10` (unused).

### 3b. Battle start — `createBattle()` (`combat.js:734-797`)
```js
unitSize = 0;
if (drifterCount >= probeCount) unitSize = probeCount/100; else unitSize = drifterCount/100;
if (unitSize < 1) unitSize = 1;                      // clips/probes per rendered ship
var rr = Math.random()*drifterCount; if (rr<1) rr=1; // drifterProbes
var ss = Math.random()*probeCount;   if (ss<1) ss=1; // clipProbes
var tt = Math.random()*availableMatter;              // territory at stake
battleID++;
battles.push({id:battleID, clipProbes:ss, drifterProbes:rr, victory:false, loss:false,
              whiteFlag:0, territory:tt, reportCount:0, garbageFlag:0});
battleLEFTSHIPS  = min(200, ceil(ss/1000000));       // + random reduce if exactly 200
battleRIGHTSHIPS = min(200, ceil(rr/1000000));
Battle();                                            // constructor → battleRestart() → 200 vs 200 ships
battleName = battleNameFlag==1 ? generateBattleName() : "Drifter Attack "+battleID;
```
Note `var app = new Battle(); app.initialize();` at `combat.js:799-800` runs at load time and obtains the
canvas immediately, installing the **single, page-global** `setInterval(Update, 16)` — there is exactly one
battle timer for the lifetime of the page, even though `createBattle()` calls `Battle()` again later.
Mechanics of that: `Battle()` (a plain function used as a constructor) calls `battleRestart()` at
`combat.js:278` to seed 200 v 200 ships, and `app.initialize()` then sets `canvas.width/height`, grabs the
2d context and starts the interval. Every later `new Battle()` inside `createBattle()` only re-runs the
closure body (re-seeding `ships`/`numLeftShips`/`numRightShips` via `battleRestart()`) and leaks its own —
never-invoked — `initialize`/`Update` closures. `battleRestart()` never touches `context`, so the pre-canvas
call is harmless.

### 3c. Frame loop — `Update()` (`combat.js:293-300`)
```js
ClearFrame();   // canvas.width = canvas.width  (full clear)
UpdateGrid();   // bucket all live ships into 15×31 grid cells
MoveShips();    // drift toward centroid + enemies + teammates, cap |v| at 2, bounce off edges
DoCombat();     // per-cell kill rolls, then checkForBattleEnd()
```
- `FindCentroid()` `combat.js:582-604`: alive-ship mean position, then
  `centroid.x = centroid.x*0.8 + 310/2*0.2`, same for y (pulls the blob to the center).
- `MoveSingleShip()` `combat.js:606-675`: accelerates toward centroid (`*0.001`), toward enemies in
  adjacent cells (`*0.2`), matches teammates (`*0.01`, `*0.1` repulsion, only 3 teammates considered).

### 3d. Where deaths come from — `DoCombat()` (`combat.js:462-544`)
```js
var pX = probeCombat * 0.15;      // probeCombatBaseRate
var dX = 1.75;                    // drifterCombat
var ooda = (attackSpeedFlag == 1) ? probeSpeed * .2 : 0;
for each grid cell with >= 2 ships and both teams present:
    // 1) probe (left team) death check
    diceRoll = Math.random() * dX * ((numRightTeam/numLeftTeam)*.5);
    battleDEATH_THRESHOLD = 0.5 + ooda;                 // ooda RAISES the threshold ⇒ fewer probe deaths
    if (diceRoll > battleDEATH_THRESHOLD) { p.alive=false; numLeftShips--;
         if (unitSize>probeCount) unitSize=probeCount;
         probeCount -= unitSize; probesLostCombat += unitSize; ... }
    // 2) drifter (right team) death check
    diceRoll = ((Math.random() * pX) + (probeCombat*.1)) * ((numLeftTeam/numRightTeam)*.5);
    if (diceRoll > 0.5) { p.alive=false; numRightShips--;
         if (unitSize>drifterCount) unitSize=drifterCount;
         drifterCount -= unitSize; driftersKilled += unitSize; ... }
    battleDEATH_THRESHOLD = .5;                         // reset
```
Key numbers: a single probe's config is `probeCombat`; each probe killed removes `unitSize` probes from the
real `probeCount`; each drifter killed removes `unitSize` drifters. **`unitSize` is frozen at battle start**
(`createBattle`), *not* recalculated per death (only clamped downward if it would overshoot).

### 3e. End of a battle — `checkForBattleEnd()` (`combat.js:302-364`) and `endBattle()` (`366-373`)
```js
if (battles.length > 0) {
  if (numLeftShips == 0 || numRightShips == 0) {
     if (project121.flag == 1) victoryDiv.style.visibility = "visible";
     if (numLeftShips == 0 && honorCount == 0) {            // DEFEAT
        bonusHonor = 0; honor -= battleLEFTSHIPS; honorCount = 1;
        battleResult = "DEFEAT"; battleResultSign = "-";
        honorAmount = battleLEFTSHIPS; threnodyTitle = battleName;
     }
     if (numRightShips == 0 && honorCount == 0) {           // VICTORY
        honorReward = battleRIGHTSHIPS + bonusHonor;
        honorAmount = honorReward; honor += honorReward;
        if (project134.flag == 1) bonusHonor += 10;         // "Glory" → +10 per consecutive victory
        honorCount = 1;
        battleResult = "VICTORY"; battleResultSign = "+";
     }
     battleEndDelay++;
  } else if (numLeftShips <= 4 || numRightShips <= 4) {
     battleClock++;  if (battleClock > 2000) endBattle();
  }
  if (battleEndDelay >= battleEndTimer) endBattle();        // 100 (or 200) frames
  masterBattleClock++;  if (masterBattleClock >= 8000) endBattle();   // hard 8000-frame cap
}
endBattle(): victoryDiv hidden; honorCount=0; battleClock=0; masterBattleClock=0;
             battleEndDelay=0; battles.splice(0,1);
```
- `honorCount` is the "already scored this battle" latch; `bonusHonor` (killed drifter value carried over)
  is reset on defeat and incremented by 10 per victory if project134 "Glory" is owned
  (`projects.js:1877`, 200,000 ops + 30,000 yomi). `project133 "Threnody"` costs creativity+yomi and gives honor.
- **Reward = `battleRIGHTSHIPS + bonusHonor` honor** (i.e. 200 + accumulated bonus in a typical late battle,
  since `battleRIGHTSHIPS` saturates at 200; the +10 bonus accrues via project134). `honor` is the only
  currency for `increaseMaxTrust()`; `maxTrust` caps `probeTrust`; `probeTrust` is the total design-point pool.
- **Loss penalty:** `honor -= battleLEFTSHIPS` (up to 200) *and* `availableMatter -= territory`? No:
  the `availableMatter -= territory` line is inside the **commented-out** `updateBattles()` (`combat.js:206`).
  The canvas battle only moves honor.

### 3f. **INPUT: there is none — the minigame is a non-interactive animation**
Verified exhaustively:
- `grep` over `main.js`, `combat.js`, `projects.js`, `index2.html` for `addEventListener`,
  `onmousemove`, `onmousedown`, `onmouseup`, `onpointer*` finds **zero bindings in combat code**.
  The only hit in the whole paperclips directory is `combat.js`'s own line 678-681, a commented-out
  `var MouseDown = function(e){ e.preventDefault(); battleRestart(); }` — **commented out, never bound**.
- The canvas has **no** `onclick`, `onmousedown`, or listener; `#battleInterfaceDiv` contains only text
  spans (`index2.html:692-712`).
- There are **no turrets, no munitions, no aiming, no mouse coordinates, no `getBoundingClientRect`,
  no `e.clientX/e.clientY`, no auto-turret** anywhere in `combat.js` (the words never appear).
- Therefore: **`Update()` is the entire game.** No input is required. A "player" who never touches the
  mouse wins or loses exactly as the simulation dictates.

### 3g. Driving a battle programmatically
Because there is no input layer, a script only has to influence **pre-battle stats** (or bypass the loop).
In order of safety:

1. **Preferred — raise design stats before the battle.** Every battle outcome is decided by `DoCombat()`:
   `probeCombat` (offense) and, with `attackSpeedFlag==1`, `probeSpeed` (defense) are the levers.
   With `probeCombat = 0` the probe reach is `diceRoll + 0`, and probes are killed at ~`1.75*ratio*.5` per
   frame, so a large `probeCount` deficit or a big `unitSize` will lose the battle. Practical recipe:
   `probeCombat >= 5` and `attackSpeedFlag = 1` with high `probeSpeed` make probe losses negligible.
2. **`checkForBattles()` is chance-gated** (`Math.random()` ≥ 50 every 10 ms tick when
   `drifterCount > 1000000`). A script that wants a battle immediately can call `createBattle()` directly
   (it is a global function; `checkForBattles()` does the same, after setting `battleFlag = 1`).
3. **Force the outcome — the only reliable, deterministic route** (all of these are plain globals;
   `combat.js` runs at top-level script scope, all declarations are `var` = `window.*`):
   - Win instantly: `numRightShips = 0` (the next `Update()` frame calls `checkForBattleEnd()`, awards
     `honorReward = battleRIGHTSHIPS + bonusHonor` honor, sets `battleResult = "VICTORY"`).
     Also acceptable: `battles[0].drifterProbes = 0` (no — `drifterProbes` is not re-read after creation),
     or set every right-team ship dead: `for (i=0;i<numShips;i++) if (ships[i].team==1) ships[i].alive=false;`
     **but `numRightShips` is the only counter the end-check uses**, so setting it to 0 is sufficient and
     cheapest.
   - End the battle without scoring: `endBattle()` (splices `battles[0]`, resets timers).
   - Prevent battles entirely: keep `drifterCount <= 1000000`, or set `maxBattles = 0`, or set
     `drifterCombat = 0` (`combat.js:38`) so drifters never kill probes.
   - Speed the loop: `battleEndTimer` / `masterBattleClock` are just counters; `endBattle()` can be
     called from the script at any time.
4. Useful variables to write: `numLeftShips`, `numRightShips`, `battles[0].victory`, `battles[0].loss`,
   `battles[0].whiteFlag`, `honorCount`, `bonusHonor`, `honorReward`, `battleEndDelay`, `battleClock`,
   `masterBattleClock`, `unitSize`, `drifterCombat`, `probeCombat`, `probeCombatBaseRate`, `attackSpeed`,
   `attackSpeedFlag`, `battleNameFlag`, `maxBattles`, `warTrigger`, `battleLEFTSHIPS`, `battleRIGHTSHIPS`.
   Useful functions: `createBattle()`, `checkForBattles()`, `battleRestart()`, `endBattle()`,
   `generateBattleName()`, and the inner `Update`/`DoCombat` (closure-only, **not reachable**).

**Win/lose summary:** *Victory* ⇔ `numRightShips == 0` before `numLeftShips == 0`; grant
`honor += battleRIGHTSHIPS + bonusHonor` (+10 to `bonusHonor` if project134). *Defeat* ⇔ `numLeftShips == 0`;
`honor -= battleLEFTSHIPS`. Either way `endBattle()` splices the battle out and the sim can start another.

---

## 4. THE ENDGAME / "DISMANTLE" SEQUENCE

### 4a. Before dismantling: the Drift King message chain
`milestoneFlag` reaches 15 via `main.js:3539-3547` (either `clips >= totalMatter` or
`foundMatter >= totalMatter && availableMatter < 1 && wire < 1`) → "Universal Paperclips achieved".
- `project140 "Message from the Emperor of Drift"` `projects.js:1927`: `trigger: milestoneFlag == 15`,
  `cost: operations >= driftKingMessageCost (1)`. Then 141 → 142 → 143 → 144 → 145 → 146
  (`projects.js:1949-2078`), each `trigger: previous.flag == 1`, cost 1 op.
- `project146` spawns the fork: `project147 "Accept"` (restart in a new universe) and
  `project148 "Reject"` (eliminate value drift permanently), `projects.js:2081-2128`.
  - **`project148.flag == 1` ⇒ `drift()` sets `amount = 0` (`main.js:4074-4076`). Drift stops forever.**
  - `project147.flag == 1` ⇒ `project200 "The Universe Next Door"` (prestigeU) and
    `project201 "The Universe Within"` (prestigeS), each calling `reset()` — these **end the run**.

### 4b. The seven dismantle steps (each is a project click; each is manual)
| step | project | trigger (exact) | cost | `dismantle` | side effects |
|---|---|---|---|---|---|
| 1 | `project210` "Disassemble the Probes" `projects.js:2187` | `endTimer1 >= 1000` | 100,000 ops | = 1 | `probeCount = 0; endTimer1 = 0; clips += 100; unusedClips += 100` |
| 2 | `project211` "Disassemble the Swarm" `2215` | `project210.flag==1 && endTimer1 >= 350` | 100,000 ops | = 2 | `harvesterLevel = 0; wireDroneLevel = 0; clips += 100; unusedClips += 100` |
| 3 | `project212` "Disassemble the Factories" `2243` | `endTimer2 >= 300` | 100,000 ops | = 3 | `factoryLevel = 0; clips += 15; unusedClips += 15` |
| 4 | `project213` "Disassemble the Strategy Engine" `2270` | `endTimer3 >= 150` | 100,000 ops | = 4 | `autoTourneyFlag = 0; wire += 50` |
| 5 | `project214` "Disassemble Quantum Computing" `2297` | `endTimer4 >= 100` | 100,000 ops | = 5 | `endTimer4 = 0` |
| 6 | `project215` "Disassemble Processors" `2322` | `project214.flag==1 && endTimer4 >= 300` | 100,000 ops | = 6 | `creativityOn = false; processors = 0; project216.priceTag = "("+standardOps+" ops)"; wire += 20` |
| 7 | `project216` "Disassemble Memory" `2351` | `project215.flag==1 && endTimer5 >= 150` | `operations >= operations` (always true) | = 7 | `standardOps = 0; memory = 0; wire += 20` |

`endTimer1..6` (`globals.js:157-162`) are incremented **once per main-loop tick** (`main.js:4498-4520`)
by the flag of the corresponding project: **`endTimer1` ← `project148.flag`**, `endTimer2` ← `project211.flag`,
`endTimer3` ← `project212.flag`, `endTimer4` ← `project213.flag`, `endTimer5` ← `project215.flag`,
**`endTimer6` ← `project216.flag && wire == 0`**. So `endTimer1` starts as soon as *Reject* is taken
(before any dismantling), and the later timers start only once their predecessor project is bought.
`endTimer6` requires **`wire == 0`** — i.e. the final `clipClick` calls must have burned the wire given by
steps 4/6/7 (50 + 20 + 20 = 90 inches) down to exactly zero.

### 4c. UI teardown driven by the timers (`main.js:4327-4530`)
- `dismantle>=1`: hide `probeDesignDivElement`; `endTimer1>=50` hide `increaseProbeTrustDivElement`;
  `>=100` hide `increaseMaxTrustDivElement`; `>=150` hide `spaceDivElement`; `>=175` hide
  `battleCanvasDivElement`; `>=190` hide `honorDivElement`.
- `dismantle>=2`: hide `wireProductionDivElement`, show `wireTransDivElement`;
  `endTimer2>=50` hide `swarmGiftDivElement`; `>=100` hide `swarmEngineElement`; `>=150` hide `swarmSliderDivElement`.
- `dismantle>=3`: hide `factoryDivSpaceElement`, `clipsPerSecDivElement`, `tothDivElement`.
- `dismantle>=4`: hide `strategyEngineElement`, `tournamentManagementElement`.
- `dismantle>=5`: hide `btnQcomputeElement`; force every `qChips[i].value = .5`; the quantum chips are
  turned off one by one at `endTimer4 == 10/60/100/130/150/160/165/169/172/174`, each time doing
  `wire += 1` (except the 165 step), and at `endTimer4 >= 250` `qComputingElement` is hidden.
- `dismantle>=6`: hide `processorDisplayElement`. `dismantle>=7`: hide `compDivElement`, `projectsDivElement`.

### 4d. `finalClips` / `clipClick` after `dismantle >= 4`
- `clipClick(number)` `main.js:2326-2362`: `if (dismantle>=4) finalClips++;` at the top, then the
  wire→clips conversion, then `if (dismantle>=4) transWireElement.innerHTML = formatWithCommas(wire);`.
- `updateStats()` `main.js:3118-3151` renders the fake final clip counter:
  - `dismantle == 0` → `"29,999,999,999,999,900,000,...,000"` (note: the literal in the source is the sequence shown at `main.js:3119`).
  - `dismantle == 1..3` → progressively longer literals (`main.js:3124/3129/3134`).
  - `dismantle >= 4` → `"29,999,999,999,999,999,999,999,999,999,999,999,999,999,999,999,999,999," + "90"+finalClips`
    for `finalClips < 10`; `...+"9"+finalClips` for `10..99`; and at `finalClips === 100` it prints the full
    `"30,000,000,000,000,000,000,000,000,000,000,000,000,000,000,000,000,000,000"` / "30.0 septendecillion".
  - **The visible "30 septendecillion clips" goal line is exactly `finalClips == 100`.**
- **`finalClips` is NOT the win condition by itself**; it is cosmetic. It *is* the thing the human is
  "playing" during the ending. It is incremented only by calling `clipClick(...)`. Since the main loop's
  `clipClick` calls are guarded by `if (dismantle < 4)` (`main.js:4280, 4303`), an automated player must
  either click the Make Paperclip button (`onclick="clipClick(1)"`, `index2.html:89`) or call
  `clipClick(1)` 100 times after reaching `dismantle >= 4`.

### 4e. `resetFlag` and `testFlag`
- `resetFlag` (`globals.js:167`) initialises to **2**. It is written only into save objects
  (`main.js:4729/5017/5306`) and read only in `load()` at `main.js:5868-5870`:
  `if (resetFlag != 2) { reset(); }`. Because it is always saved as 2 in normal play, this branch never
  fires. `reset()` (`main.js:6484`) removes `saveGame`, `saveProjects*`, `saveStratsActive` from
  localStorage and calls `location.reload()`.
- `testFlag` (`globals.js:164`) initialises to 0 and is **only ever saved/loaded**
  (`main.js:4739/5027/5316/5606/5931/6236`). It is never read by any logic — pure dead state.

### 4f. The exact end-of-game message sequence (`main.js:4498-4550`)
```
project148.flag == 1        → endTimer1++
project211.flag == 1        → endTimer2++
project212.flag == 1        → endTimer3++
project213.flag == 1        → endTimer4++
project215.flag == 1        → endTimer5++
project216.flag == 1 && wire == 0 → endTimer6++

endTimer6 >= 250                       → creationDivElement.style.display = "none"
endTimer6 >= 500 && milestoneFlag==15  → playThrenody(); displayMessage("Universal Paperclips");     milestoneFlag=16
endTimer6 >= 600 && milestoneFlag==16  → displayMessage("a game by Frank Lantz");                   milestoneFlag=17
endTimer6 >= 700 && milestoneFlag==17  → displayMessage("combat programming by Bennett Foddy");    milestoneFlag=18
endTimer6 >= 800 && milestoneFlag==18  → displayMessage("'Riversong' by Tonto's Expanding Headband used by kind permission of Malcolm Cecil"); milestoneFlag=19
endTimer6 >= 900 && milestoneFlag==19  → displayMessage("&#169; 2017 Everybody House Games");       milestoneFlag=20
```

### 4g. **The precise "game is finished/won" condition**
> **`milestoneFlag == 20`** (equivalently `endTimer6 >= 900 && milestoneFlag == 19` having fired), which
> requires `project216.flag == 1`, `wire == 0`, and roughly 900 further main-loop ticks (≈ 9 s) after
> `project216` is bought.
There is no separate `won` flag and no stop of the intervals: both `setInterval`s keep running forever.
`project200`/`project201` (the prestige "new universe" options) call `reset()` and are a **separate**
ending path, reachable only via `project147 "Accept"`.

---

## 5. FUNCTION CALL LIST FOR AN AUTOMATED SPACE-STAGE PLAYER

Everything below is a global in the page. `spaceFlag == 1` means "space era"; `humanFlag == 0` is required
by several functions. All `raise*/lower*` probe functions require `probeUsedTrust < probeTrust`
(i.e. `probeSpeed+probeNav+probeRep+probeHaz+probeFac+probeHarv+probeWire+probeCombat < probeTrust`).

### Bootstrap (before/at the transition)
| call | preconditions |
|---|---|
| `makeFarm(1)` / `makeFarm(10)` / `makeFarm(100)` | `unusedClips >= farmCost` (`(farmLevel+1)^2.78 * 1e8`) |
| `makeBattery(1)` / `(10)` / `(100)` | `unusedClips >= batteryCost` (`(batteryLevel+1)^2.54 * 1e7`) |
| `makeFactory()` | `unusedClips >= factoryCost` |
| `makeHarvester(n)` | each unit `unusedClips >= harvesterCost` |
| `makeWireDrone(n)` | each unit `unusedClips >= wireDroneCost` |
| `zeroMatter()` | cheat: `availableMatter = 0` (`main.js:3368`) — makes project46 eligible |

### Buy the space era
| call | preconditions |
|---|---|
| project210..216 clicks → `effect()` | see §4b (must click the DOM button or call `project2xx.effect()`) |
| `increaseProbeTrust()` | `yomi >= probeTrustCost && probeTrust < maxTrust` |
| `increaseMaxTrust()` | `honor >= maxTrustCost` (91117.99) — UI also needs `project121.flag==1` |
| `raiseProbeSpeed()` | `probeUsedTrust < probeTrust` (also `attackSpeed += .1`) |
| `raiseProbeNav()` | `probeUsedTrust < probeTrust` |
| `raiseProbeRep()` | `probeUsedTrust < probeTrust` |
| `raiseProbeHaz()` | `probeUsedTrust < probeTrust` |
| `raiseProbeFac()` | `probeUsedTrust < probeTrust` |
| `raiseProbeHarv()` | `probeUsedTrust < probeTrust` |
| `raiseProbeWire()` | `probeUsedTrust < probeTrust` |
| `raiseProbeCombat()` | `probeUsedTrust < probeTrust` |
| `lowerProbeX()` (same 8) | the matching counter `> 0` |
| `makeProbe()` | `unusedClips > probeCost` (1e17) — repeat in a loop to launch probes |
| `increaseMaxTrust()` | `honor >= maxTrustCost` |

### Run the economy (all called automatically by the loop, but callable)
| call | preconditions |
|---|---|
| `exploreUniverse()` | `probeCount >= 1` and `probeSpeed*probeNav > 0` to gain anything |
| `encounterHazards()` | `spaceFlag == 1`, `probeCount > 0` is not required (safe on 0) |
| `spawnFactories()` / `spawnHarvesters()` / `spawnWireDrones()` | `spaceFlag == 1`; clips available (`unusedClips`) |
| `spawnProbes()` | `spaceFlag == 1`; clips available |
| `drift()` | `spaceFlag == 1`; disabled if `project148.flag == 1` |
| `war()` / `checkForBattles()` | `drifterCount > 1000000 && probeCount > 0 && battles.length < maxBattles` (+ 50 % chance) |
| `acquireMatter()` / `processMatter()` | `humanFlag == 0`; matter/wireDroneLevel present |
| `updatePower()` | `humanFlag == 0 && spaceFlag == 0` (**no-op** once `spaceFlag == 1`) |
| `updateSwarm()` | `humanFlag == 0` (safe always) |
| `clipClick(n)` | wire ≥ 1 to produce clips; always increments `finalClips` if `dismantle >= 4` |
| `makeFarm/makeBattery/makeFactory/makeHarvester/makeWireDrone` | clips (see above) |
| `farmReboot()/batteryReboot()/factoryReboot()/harvesterReboot()/wireDroneReboot()` | level ≥ 1; refunds the bill |
| `synchSwarm()` | `yomi >= synchCost` (5000) |
| `entertainSwarm()` | `creativity >= entertainCost` (10000, +10000 each use) |
| `addProc()` / `addMem()` | `trust > 0 || swarmGifts > 0` (in space: needs `swarmGifts > 0`) |
| `newTourney()` / `runTourney()` | `operations >= tourneyCost`, `tourneyInProg == 0` |
| `save()` / `save1()` / `save2()` | none (writes localStorage) |
| `load()` / `refresh()` | none |

### Combat (see §3g — no mouse input exists)
| call / write | preconditions |
|---|---|
| `createBattle()` | `probeCount > 0` (unitSize would be 0 → forced to 1) |
| `endBattle()` | a battle exists; splices `battles[0]` |
| `battleRestart()` | none; resets the 200 vs 200 ship field (throwing away an in-progress battle) |
| `numRightShips = 0` | win on the next 16 ms frame; `honor += battleRIGHTSHIPS + bonusHonor` |
| `numLeftShips = 0` | lose on the next frame; `honor -= battleLEFTSHIPS` |
| `drifterCombat = 0` | drifters can never kill probes |
| `maxBattles = 0` | `checkForBattles()` stops creating battles |
| `warTrigger = Infinity` | same effect |
| `probeCombat = N` / `raiseProbeCombat()` | raises both probe offense and defense in `DoCombat` |
| `attackSpeedFlag = 1` (project120) + `probeSpeed` | `ooda = probeSpeed*.2` added to the probe death threshold |

### Ending
| call | preconditions |
|---|---|
| click `project210` … `project216` (`effect()`) | triggers/costs in §4b |
| `clipClick(1)` × 100 | `dismantle >= 4`; drives `finalClips` to 100 (needs `wire >= 1`, so do it while wire remains from steps 4/6/7) |
| win/watch loop | poll `milestoneFlag == 20` |

---

## 6. Fastest known-good space-stage script skeleton

```
// --- once project46 has been taken (spaceFlag == 1) ---
project130.effect()          // "Reboot the Swarm" — otherwise the swarm is status 9 and never gifts
sliderElement.value = 100;   // any value > 0: needed for swarm gifts (and 100 = neutral 1x output)
for (i=0;i<20;i++) increaseProbeTrust();       // needs yomi; spend it all into trust
raiseProbeNav();  raiseProbeSpeed();  raiseProbeRep();      // minimum viable probe
raiseProbeCombat(); raiseProbeCombat(); raiseProbeCombat(); // survive battles

// --- each tick (or each second) ---
if (unusedClips > probeCost) makeProbe();      // kick-start the swarm with one probe
if (unusedClips > probeCost*1000) for(...) makeProbe();
if (honor >= maxTrustCost) increaseMaxTrust(); // +10 maxTrust per 91117.99 honor

// --- combat: no input needed; to guarantee victory ---
if (battles.length > 0) numRightShips = 0;

// --- endgame ---
project148.effect();     // Reject → drift = 0, start endTimer1
// then wait for each trigger and click in order: 210, 211, 212, 213, 214, 215, 216
// finalClips: while (wire >= 1 && finalClips < 100) clipClick(1);
// done when milestoneFlag == 20
```

### 20 most important facts (quick list)
1. `spaceFlag = 1` is set **only** by `project46 "Space Exploration"** (`projects.js:1128`), whose trigger is
   `humanFlag == 0 && availableMatter == 0` and whose cost is 120,000 ops + 10,000,000 stored power + 5e27 clips.
2. The human era ends at `project35` (`projects.js:729`): `humanFlag = 0`, `trust = 0`, and all ground
   clips/factories/harvesters are wiped; probe UI does **not** exist yet.
3. `probeCost` is `Math.pow(10,17)` = 1e17 clips and **never increases** (the growth lines are commented out).
4. `makeProbe()` requires `unusedClips > probeCost` (strict `>`); `spawnProbes()` replicates at
   `probeCount * 0.00005 * probeRep` gated on clip affordability, with a 1e48 cap on `probeCount`.
5. `exploreUniverse()` yields `floor(probeCount) * 1.75e18 * probeSpeed * probeNav` —
   **zero contribution if `probeSpeed` or `probeNav` is 0 regardless of probe count.**
6. `totalMatter = 3e55`, `availableMatter` starts at 6e27; `foundMatter` tracks discovery.
7. `acquireMatter` and `processMatter` both scale with `(200 - sliderPos)/100`, `powMod`, and the
   harvester/wire-drone levels; the `#slider` range is 0-200 and `sliderPos` is only refreshed inside
   `updateSwarm()` when `swarmFlag == 1` (default 0 = 2× output).
8. `updatePower()` early-returns unless `humanFlag==0 && spaceFlag==0`, so once `spaceFlag` flips **`powMod`
   is frozen at 1** (project46 sets it), and `storedPower` is never recomputed again.
9. `encounterHazards()` kills `probeCount * 0.01/(3*probeHaz^1.6 + 1)` per tick (halved by project129).
10. `drift()` converts `probeCount * 1e-6 * probeTrust^1.2` per tick into `drifterCount`; **project148
    ("Reject") sets this to 0 permanently.**
11. `war()` = `checkForBattles()`: a battle starts with 50 % probability per tick once
    `drifterCount > 1,000,000`, with `maxBattles = 1` — one battle at a time.
12. **Combat has NO player input**: no canvas/DOM mouse or pointer listeners exist in `combat.js`
    (the only `MouseDown` handler is commented out at `combat.js:678-681`); the animation runs
    autonomously on `setInterval(Update, 16)`.
13. Battle victory ⇔ `numRightShips == 0` → `honor += battleRIGHTSHIPS + bonusHonor`
    (+10 to `bonusHonor` per consecutive win if `project134`); defeat ⇔ `numLeftShips == 0` → `honor -= battleLEFTSHIPS`.
14. Programmatic win: set `numRightShips = 0` (one frame later the reward is paid and `endBattle()` runs);
    `endBattle()` and `createBattle()` are callable globals; `numLeftShips`/`numRightShips`/`battles`/
    `unitSize`/`drifterCombat`/`maxBattles`/`warTrigger`/`attackSpeedFlag`/`probeCombat` are all writable.
15. Each probe consumed in battle removes `unitSize` from the real `probeCount`, and `unitSize` is frozen
    at battle creation (`max(1, min(probeCount,drifterCount)/100)`).
16. `probeUsedTrust = probeSpeed+probeNav+probeRep+probeHaz+probeFac+probeHarv+probeWire+probeCombat` and every
    `raise*` needs `probeUsedTrust < probeTrust`; `increaseProbeTrust()` costs
    `floor((probeTrust+1)^1.47 * 500)` yomi and is capped by `maxTrust` (start 20, +10 per 91,117.99 honor).
17. The dismantle chain is `project210→211→212→213→214→215→216` setting `dismantle` 1→7, triggered by
    `endTimer1>=1000`, `endTimer1>=350`, `endTimer2>=300`, `endTimer3>=150`, `endTimer4>=100`,
    `endTimer4>=300`, `endTimer5>=150` respectively; each costs 100,000 ops
    (`endTimer1` runs off `project148.flag`; `endTimer6` runs off `project216.flag && wire == 0`).
18. `finalClips` increments **only inside `clipClick()`** and the loop's `clipClick` calls are skipped once
    `dismantle >= 4`, so an automated player must call `clipClick(1)` (or click Make Paperclip) 100 times.
19. `updateStats()` prints the goal line only at `finalClips == 100` ("30.0 septendecillion");
    `finalClips` itself is cosmetic, not the win flag.
20. **The game is finished when `milestoneFlag == 20`**, reached from 15 by the `endTimer6` ladder:
    ≥500 "Universal Paperclips", ≥600 "a game by Frank Lantz", ≥700 "combat programming by Bennett Foddy",
    ≥800 the Riversong credit, ≥900 "© 2017 Everybody House Games". Both `setInterval`s keep running afterwards.
    `resetFlag` (init 2, only read as `if (resetFlag != 2) reset()` at `main.js:5868`) and `testFlag`
    (never read) are dead state.
