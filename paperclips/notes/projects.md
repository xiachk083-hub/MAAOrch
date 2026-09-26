# Universal Paperclips — Complete Project Reference

Source: `D:\1-2\MAAOrch\paperclips\projects.js` (2451 lines, 96 project objects, pushed in file order into `projects[]`).
Engine references: `main.js` (`manageProjects()` L870, `displayProjects()` L891, main loop L4188, endgame L4327-4550, `refresh()` L4604, `milestoneCheck()` L3455), `globals.js` (initial values).

Everything below was extracted from source and machine-verified by evaluating `projects.js` with DOM stubs (96 objects, ids `projectButton1` … `projectButton219`).

---

## 1. Mechanism

### 1.1 Project object shape

Each project is a plain object literal. **There is no `unlockCondition` field** — the appearance condition is called `trigger`. Exact fields:

| field | type | meaning |
|---|---|---|
| `id` | string | DOM id of the generated button, e.g. `"projectButton1"`. Also stored in save data (`saveProjectsActive`). |
| `title` | string | Bold title shown on the button (several have a trailing space; display only). |
| `priceTag` | string | Cost text shown after the title, e.g. `"(750 ops)"`, `"($1,000,000)"`, `"(25 creat, 2,500 ops)"`. For `project40b`, `project51`, `project133` it is **recomputed at runtime** when the price changes. `project216` starts as `"null"` and is overwritten by `project215`'s effect. |
| `description` | string | One-line description under the title. |
| `trigger` | `function(){...}` | **Unlock / availability condition**, evaluated every tick. When it returns true and `uses > 0`, the project is offered. |
| `uses` | number | Remaining offers. `manageProjects()` decrements it when the project is first displayed. `uses: 1` = one-time; a few effects (or `refresh()`) increment/reset it to make the project repeatable. |
| `cost` | `function(){...}` | Affordability check, evaluated every tick for every *active* project. `element.disabled = !cost()`. The button is visible but greyed out until `cost()` is true. |
| `flag` | number | 0/1. Set to `1` inside `effect()`. Used by other projects' `trigger` strings (`project13.flag == 1` etc.). |
| `element` | DOM node | `null` until `displayProjects()` creates the button. |
| `effect` | `function(){...}` | Click handler. Sets `flag = 1`, spends the resource, applies global-state changes, removes the element from the DOM and from `activeProjects`. |

Two global arrays live in `projects.js`:
```js
var projects = [];        // all 96 projects, in file order
var activeProjects = [];  // offered projects whose button exists in the DOM
```

### 1.2 `manageProjects()` — main.js:870

```js
function manageProjects(){
    for(var i = 0; i < projects.length; i++){
        if (projects[i].trigger() && (projects[i].uses > 0)){
            displayProjects(projects[i]);
            projects[i].uses = projects[i].uses - 1;
            activeProjects.push(projects[i]);
        }
    }
    for(var i = 0; i < activeProjects.length; i++){
        if (activeProjects[i].cost()){
            activeProjects[i].element.disabled = false;
        } else {
            activeProjects[i].element.disabled = true;
        }
    }
}
```

* Called **every tick** from the main loop (`window.setInterval(function(){...}, 10);`, main.js:4188, call at 4207) → 100 ticks/second.
* Iteration order = push order in `projects.js` (see table order below).
* A project is offered **once per `uses` unit**; once `uses` hits 0 it is never displayed again unless `effect()` or `refresh()` increments it.
* `activeProjects` is **never pruned** by `manageProjects()`; entries are removed inside each `effect()` via `activeProjects.splice(index,1)`.

### 1.3 `displayProjects(project)` — main.js:891

Creates `<button id="{project.id}" class="projectButton">`, sets `element.onclick = function(){project.effect()};`, then appends into `projectListTopElement` (`<div id="projectListTop">`, inside `<div id="projectsDiv">`):

1. `<span style="font-weight:bold">title</span>`
2. text node `priceTag`
3. an empty `<div>` (spacer)
4. text node `description`

then calls `blink(element)` (blinking highlight). Note: the call is `projectListTopElement.appendChild(project.element, projectListTopElement.firstChild)` — `appendChild` ignores the second argument in the DOM, so buttons are effectively **appended at the bottom** of the list, in the order the triggers fire.

### 1.4 Panel gating: `projectsFlag` / `compFlag`

* `projectsDivElement` (`#projectsDiv`) is hidden while `projectsFlag === 0` (main.js:1253).
* `milestoneCheck()` sets `compFlag = 1; projectsFlag = 1;` and prints *"Trust-Constrained Self-Modification enabled"* the first time either:
  * `unsoldClips < 1 && funds < wireCost && wire < 1` (completely broke), **or**
  * `Math.ceil(clips) >= 2000`.
* `compFlag == 1` also enables `calculateOperations()` in the tick loop.

### 1.5 Ops (`operations` / `standardOps` / `memory` / `processors`)

* `operations = Math.floor(standardOps + Math.floor(tempOps))`; hard cap `standardOps = memory*1000` (main.js:3429-3450). Ops regenerate from `processors` (`opCycle = processors/10` per tick while below cap).
* **Every project spends `standardOps`, not `operations`** (`standardOps = standardOps - N`), while `cost()` predicates always test `operations >= N`. For a bot: check `operations`, spend reduces `standardOps`, both are equal when the ops bar is settled.

### 1.6 Trust

* `trust` starts at **2** (`globals.js:30`), `nextTrust = 3000`.
* `calculateTrust()` (main.js:3374) runs only while `humanFlag == 1`: if `clips > nextTrust-1` → `trust++`, then `nextTrust = fibNext*1000` with Fibonacci (`fib1=2, fib2=3` → 3000, 5000, 8000, 13000, 21000, …).
* Trust is also granted by projects (see §5) and spent by `addProc()`/`addMem()` (processor/memory upgrades come from trust, not from projects). Trust is required for: `project21` (trust≥8), `project34` (≥1), `project35` (**≥100**), `project40` (85≤trust<100), `project40b` (<100), `project118` (≥90).
* `project2`'s `cost()` is `trust >= -100`, i.e. it may drive trust negative; its priceTag says "1 Trust".

### 1.7 Creativity

* `creativityOn` starts `false`; **`project3` (Creativity, 1,000 ops) is the only project that sets it true.**
* Each tick: `if (creativityOn && operations >= (memory*1000)) calculateCreativity();` (main.js:4321) — creativity only accrues while ops are at the cap.
* `calculateCreativity()` uses `creativitySpeed` (set by `addProc()`: `Math.log10(processors)*Math.pow(processors,1.1) + processors-1`), a local `creativityThreshold = 400` and `creativityCounter`, plus the `prestigeS` bonus (`ss = creativitySpeed + creativitySpeed*(prestigeS/10)`).
* Creativity is the gate for the whole "self-improvement/philosophy" line (projects 6, 11-19, 27, 118, 119, 121, 125, 128, 132, 133, 201, 218, 219).

### 1.8 Persistence

`save()`/`save1()`/`save2()` store `saveProjectsUses`, `saveProjectsFlags`, `saveProjectsActive` (array of ids) in `localStorage`; load restores `uses`/`flag` for every project and re-creates buttons for ids in the active list (main.js:5849-5863). `refresh()` (main.js:4686-4687) resets `project218.uses = 1; project219.uses = 1;` on every load, so those two are effectively repeatable.

### 1.9 Useful globals for an automated player

`operations`, `standardOps`, `tempOps`, `memory`, `processors`, `trust`, `nextTrust`, `creativity`, `creativityOn`, `creativitySpeed`, `yomi`, `honor`, `funds`, `clips`, `unusedClips`, `wire`, `wireSupply`, `wireCost`, `wirePurchase`, `marketingEffectiveness`, `demandBoost`, `humanFlag`, `spaceFlag`, `factoryFlag`, `factoryLevel`, `factoryRate`, `factoryBoost`, `harvesterFlag`, `harvesterLevel`, `harvesterRate`, `wireDroneFlag`, `wireDroneLevel`, `wireDroneRate`, `droneBoost`, `tothFlag`, `wireProductionFlag`, `qFlag`, `qChips`, `nextQchip`, `qChipCost`, `strategyEngineFlag`, `investmentEngineFlag`, `revPerSecFlag`, `autoTourneyFlag`, `wireBuyerFlag`, `megaClipperFlag`, `megaClipperBoost`, `clipperBoost`, `boostLvl`, `bribe`, `threnodyCost`, `threnodyTitle`, `maxTrust`, `probeTrustCost`, `probeUsedTrust`, `dismantle`, `endTimer1..6`, `milestoneFlag`, `prestigeU`, `prestigeS`, `driftKingMessageCost` (=1).

Cheat/debug functions exist in `main.js` and are callable from the console: `cheatOps()` (+10000 `standardOps`), `cheatCreat()` (`creativityOn=1`, +1000 `creativity`), `cheatTrust()` (+1), `cheatClips()` (+1e8 clips+unusedClips), `cheatMoney()` (+1e7 funds), `cheatYomi()` (+1e6), `zeroMatter()`, `cheatHypno()`, `cheatPrestigeU()`, `cheatPrestigeS()`.

---

## 2. Complete project table (96 entries, in `projects[]` order)

Columns: **var** (`projectN`) · **#** = index in `projects[]` · **title** · **priceTag (display)** / **cost() (real gate)** · **trigger() (unlock)** · **effect() (exact state changes)**.
Titles are shown trimmed. `flag=1` is set by every `effect()`, plus removal of the button — omitted from the effect column.

### 2.1 AutoClipper / wire / early ops (idx 0-10)

| var | # | title | priceTag — cost() | trigger() | effect() |
|---|---|---|---|---|---|
| `project1` | 0 | Improved AutoClippers | `(750 ops)` — `operations>=750` | `clipmakerLevel>=1` | `standardOps-=750`; `clipperBoost+=0.25`; `boostLvl=1` |
| `project2` | 1 | Beg for More Wire | `(1 Trust)` — `trust>=-100` | `portTotal<wireCost && funds<wireCost && wire<1 && unsoldClips<1` | `trust-=1`; `wire=wireSupply`; `uses+=1` (**repeatable**) |
| `project3` | 2 | Creativity | `(1,000 ops)` — `operations>=1000` | `operations>=(memory*1000)` | `standardOps-=1000`; `creativityOn=true` |
| `project4` | 3 | Even Better AutoClippers | `(2,500 ops)` — `operations>=2500` | `boostLvl == 1` | `standardOps-=2500`; `clipperBoost+=0.50`; `boostLvl=2` |
| `project5` | 4 | Optimized AutoClippers | `(5,000 ops)` — `operations>=5000` | `boostLvl == 2` | `standardOps-=5000`; `clipperBoost+=0.75`; `boostLvl=3` |
| `project6` | 5 | Limerick | `(10 creat)` — `creativity>=10` | `creativityOn` | `creativity-=10`; `trust+=1` |
| `project7` | 6 | Improved Wire Extrusion | `(1,750 ops)` — `operations>=1750` | `wirePurchase >= 1` | `standardOps-=1750`; `wireSupply*=1.5` |
| `project8` | 7 | Optimized Wire Extrusion | `(3,500 ops)` — `operations>=3500` | `wireSupply >= 1500` | `standardOps-=3500`; `wireSupply*=1.75` |
| `project9` | 8 | Microlattice Shapecasting | `(7,500 ops)` — `operations>=7500` | `wireSupply >= 2600` | `standardOps-=7500`; `wireSupply*=2` |
| `project10` | 9 | Spectral Froth Annealment | `(12,000 ops)` — `operations>=12000` | `wireSupply >= 5000` | `standardOps-=12000`; `wireSupply*=3` |
| `project10b` | 10 | Quantum Foam Annealment | `(15,000 ops)` — `operations>=15000` | `wireCost >= 125` | `standardOps-=15000`; `wireSupply*=11` |

### 2.2 Marketing / creativity-trust line (idx 11-26)

| var | # | title | priceTag — cost() | trigger() | effect() |
|---|---|---|---|---|---|
| `project11` | 11 | New Slogan | `(25 creat, 2,500 ops)` — `operations>=2500 && creativity>=25` | `project13.flag == 1` | `standardOps-=2500`; `creativity-=25`; `marketingEffectiveness*=1.5` |
| `project12` | 12 | Catchy Jingle | `(45 creat, 4,500 ops)` — `operations>=4500 && creativity>=45` | `project14.flag == 1` | `standardOps-=4500`; `creativity-=45`; `marketingEffectiveness*=2` |
| `project13` | 13 | Lexical Processing | `(50 creat)` — `creativity>=50` | `creativity >= 50` | `trust+=1`; `creativity-=50` |
| `project14` | 14 | Combinatory Harmonics | `(100 creat)` — `creativity>=100` | `creativity >= 100` | `trust+=1`; `creativity-=100` |
| `project15` | 15 | The Hadwiger Problem | `(150 creat)` — `creativity>=150` | `creativity >= 150` | `trust+=1`; `creativity-=150` |
| `project17` | 16 | The Tóth Sausage Conjecture | `(200 creat)` — `creativity>=200` | `creativity >= 200` | `trust+=1`; `creativity-=200` |
| `project16` | 17 | Hadwiger Clip Diagrams | `(6,000 ops)` — `operations>=6000` | `project15.flag == 1` | `standardOps-=6000`; `clipperBoost+=5` |
| `project18` | 18 | Tóth Tubule Enfolding | `(45,000 ops)` — `operations>=45000` | `project17.flag == 1 && humanFlag == 0` | `standardOps-=45000`; **`tothFlag=1`** (unlocks Power Grid line) |
| `project19` | 19 | Donkey Space | `(250 creat)` — `creativity>=250` | `creativity>=250` | `trust+=1`; `creativity-=250` |
| `project20` | 20 | Strategic Modeling | `(12,000 ops)` — `operations>=12000` | `project19.flag == 1` | `standardOps-=12000`; **`strategyEngineFlag=1`**; hides `tournamentResultsTable` |
| `project21` | 21 | Algorithmic Trading | `(10,000 ops)` — `operations>=10000` | `trust>=8` | `standardOps-=10000`; **`investmentEngineFlag=1`** |
| `project22` | 22 | MegaClippers | `(12,000 ops)` — `operations>=12000` | `clipmakerLevel>=75` | `standardOps-=12000`; **`megaClipperFlag=1`** |
| `project23` | 23 | Improved MegaClippers | `(14,000 ops)` — `operations>=14000` | `project22.flag == 1` | `standardOps-=14000`; `megaClipperBoost+=0.25` |
| `project24` | 24 | Even Better MegaClippers | `(17,000 ops)` — `operations>=17000` | `project23.flag == 1` | `standardOps-=17000`; `megaClipperBoost+=0.50` |
| `project25` | 25 | Optimized MegaClippers | `(19,500 ops)` — `operations>=19500` | `project24.flag == 1` | `standardOps-=19500`; `megaClipperBoost+=1` |
| `project26` | 26 | WireBuyer | `(7,000 ops)` — `operations>=7000` | `wirePurchase>=15` | `standardOps-=7000`; **`wireBuyerFlag=1`** |

### 2.3 HypnoDrones transition (idx 27-30)

| var | # | title | priceTag — cost() | trigger() | effect() |
|---|---|---|---|---|---|
| `project34` | 27 | Hypno Harmonics | `(7,500 ops, 1 Trust)` — `operations>=7500 && trust>=1` | `project12.flag==1` | `standardOps-=7500`; `marketingEffectiveness*=5`; `trust-=1` |
| `project70` | 28 | HypnoDrones | `(70,000 ops)` — `operations>=70000` | `project34.flag == 1` | `standardOps-=70000` (flag only, unlocks the release button) |
| `project35` | 29 | Release the HypnoDrones | `(100 Trust)` — `trust>=100` | `project70.flag == 1` | `trust=0`; `clipmakerLevel=0`; `megaClipperLevel=0`; `nanoWire=wire`; **`humanFlag=0`**; removes `project219` and `project40b` buttons + activeProjects entries; calls `hypnoDroneEvent()`; updates `transWire` |
| `project27` | 30 | Coherent Extrapolated Volition | `(500 creat, 3,000 Yomi, 20,000 ops)` — `yomi>=3000 && operations>=20000 && creativity>=500` | `yomi>=1` | `yomi-=3000`; `standardOps-=20000`; `creativity-=500`; `trust+=1` |

### 2.4 World problems (need `project27`) (idx 31-34)

| var | # | title | priceTag — cost() | trigger() | effect() |
|---|---|---|---|---|---|
| `project28` | 31 | Cure for Cancer | `(25,000 ops)` — `operations>=25000` | `project27.flag == 1` | `standardOps-=25000`; `trust+=10`; `stockGainThreshold+=0.01` |
| `project29` | 32 | World Peace | `(15,000 yomi, 30,000 ops)` — `yomi>=15000 && operations>=30000` | `project27.flag == 1` | `yomi-=15000`; `standardOps-=30000`; `trust+=12`; `stockGainThreshold+=0.01` |
| `project30` | 33 | Global Warming | `(4,500 yomi, 50,000 ops)` — `yomi>=4500 && operations>=50000` | `project27.flag == 1` | `yomi-=4500`; `standardOps-=50000`; `trust+=15`; `stockGainThreshold+=0.01` |
| `project31` | 34 | Male Pattern Baldness | `(20,000 ops)` — `operations>=20000` | `project27.flag == 1` | `standardOps-=20000`; `trust+=20`; `stockGainThreshold+=0.01` |

### 2.5 Business / monopoly / drones / factories (idx 35-44)

| var | # | title | priceTag — cost() | trigger() | effect() |
|---|---|---|---|---|---|
| `project41` | 35 | Nanoscale Wire Production | `(35,000 ops)` — `operations>=35000` | `project127.flag == 1` | `standardOps-=35000`; **`wireProductionFlag=1`** |
| `project37` | 36 | Hostile Takeover | `($1,000,000)` — `funds>=1000000` | `portTotal>=10000` | `demandBoost*=5`; `trust+=1`; `funds-=1000000`; updates `#demand` |
| `project38` | 37 | Full Monopoly | `(3,000 yomi, $10,000,000)` — `funds>=10000000 && yomi>=3000` | `project37.flag == 1` | `demandBoost*=10`; `funds-=10000000`; `trust+=1`; `yomi-=3000` |
| `project42` | 38 | RevTracker | `(500 ops)` — `operations>=500` | `projectsFlag == 1` | `standardOps-=500`; **`revPerSecFlag=1`** |
| `project43` | 39 | Harvester Drones | `(25,000 ops)` — `operations>=25000` | `project41.flag == 1` | `standardOps-=25000`; **`harvesterFlag=1`**; refreshes `harvesterCostDisplay` |
| `project44` | 40 | Wire Drones | `(25,000 ops)` — `operations>=25000` | `project41.flag == 1` | `standardOps-=25000`; **`wireDroneFlag=1`**; refreshes `wireDroneCostDisplay` |
| `project45` | 41 | Clip Factories | `(35,000 ops)` — `operations>=35000` | `project43.flag == 1 && project44.flag == 1` | `standardOps-=35000`; **`factoryFlag=1`**; refreshes `factoryCostDisplay` |
| `project40` | 42 | A Token of Goodwill... | `($500,000)` — `funds>=500000` | `humanFlag == 1 && trust>=85 && trust<100 && clips>=101000000` | `funds-=500000`; `trust+=1` |
| `project40b` | 43 | Another Token of Goodwill... | `(${bribe})` (runtime; `bribe` starts 1,000,000) — `funds>=bribe` | `project40.flag == 1 && trust<100` | `funds-=bribe`; `bribe*=2`; `priceTag` updated; `trust+=1`; `uses+=1` while `trust<100` (**repeatable, cost doubles each click**) |
| `project46` | 44 | Space Exploration | `(120,000 ops, 10,000,000 MW-seconds, 5 oct clips)` — `operations>=120000 && storedPower>=10000000 && unusedClips>=Math.pow(10,27)*5` | `humanFlag == 0 && availableMatter == 0` | `loadThrenody()`; `boredomLevel=0`; **`spaceFlag=1`**; `standardOps-=120000`; `storedPower-=10000000`; `unusedClips-=5e27`; `factoryReboot(); harvesterReboot(); wireDroneReboot(); farmReboot(); batteryReboot();` `farmLevel=1`; `powMod=1` |

### 2.6 Quantum computing (idx 45-46)

| var | # | title | priceTag — cost() | trigger() | effect() |
|---|---|---|---|---|---|
| `project50` | 45 | Quantum Computing | `(10,000 ops)` — `operations>=10000` | `processors >= 5` | `standardOps-=10000`; **`qFlag=1`** |
| `project51` | 46 | Photonic Chip | `("("+qChipCost+" ops)")` — `operations>=qChipCost` | `project50.flag == 1` | `standardOps-=qChipCost`; `qChipCost+=5000`; `priceTag` updated; `qChips[nextQchip].active=1`; `nextQchip+=1`; `uses+=1` while `nextQchip<qChips.length` (**repeatable, 10 chips total, 10,000 / 15,000 / 20,000 / … ops**) |

### 2.7 Tournament strategies (need `project20`) (idx 47-53)

| var | # | title | priceTag — cost() | trigger() | effect() |
|---|---|---|---|---|---|
| `project60` | 47 | New Strategy: A100 | `(15,000 ops)` — `operations>=15000` | `project20.flag == 1` | `standardOps-=15000`; `allStrats[1].active=1`; `strats.push(stratA100)`; `tourneyCost+=1000`; appends `<option value="1">A100</option>` to `#stratPicker` |
| `project61` | 48 | New Strategy: B100 | `(17,500 ops)` — `operations>=17500` | `project60.flag == 1` | same pattern: `allStrats[2]`; `stratB100`; `tourneyCost+=1000` |
| `project62` | 49 | New Strategy: GREEDY | `(20,000 ops)` — `operations>=20000` | `project61.flag == 1` | `allStrats[3]`; `stratGreedy`; `tourneyCost+=1000` |
| `project63` | 50 | New Strategy: GENEROUS | `(22,500 ops)` — `operations>=22500` | `project62.flag == 1` | `allStrats[4]`; `stratGenerous`; `tourneyCost+=1000` |
| `project64` | 51 | New Strategy: MINIMAX | `(25,000 ops)` — `operations>=25000` | `project63.flag == 1` | `allStrats[5]`; `stratMinimax`; `tourneyCost+=1000` |
| `project65` | 52 | New Strategy: TIT FOR TAT | `(30,000 ops)` — `operations>=30000` | `project64.flag == 1` | `allStrats[6]`; `stratTitfortat`; `tourneyCost+=1000` |
| `project66` | 53 | New Strategy: BEAT LAST | `(32,500 ops)` — `operations>=32500` | `project65.flag == 1` | `allStrats[7]`; `stratBeatlast`; `tourneyCost+=1000` (after all 7: `strats.length >= 8`) |

### 2.8 Space-era production upgrades (idx 54-59)

| var | # | title | priceTag — cost() | trigger() | effect() |
|---|---|---|---|---|---|
| `project100` | 54 | Upgraded Factories | `(80,000 ops)` — `operations>=80000` | `factoryLevel >= 10` | `standardOps-=80000`; `factoryRate*=100` |
| `project101` | 55 | Hyperspeed Factories | `(85,000 ops)` — `operations>=85000` | `factoryLevel >= 20` | `standardOps-=85000`; `factoryRate*=1000` |
| `project102` | 56 | Self-correcting Supply Chain | `(1 sextillion clips)` — `unusedClips>=1000000000000000000000` (1e21) | `factoryLevel >= 50` | `unusedClips-=1e21`; **`factoryBoost=1000`** |
| `project110` | 57 | Drone flocking: collision avoidance | `(80,000 ops)` — `operations>=80000` | `(harvesterLevel + wireDroneLevel)>=500` | `standardOps-=80000`; `harvesterRate*=100`; `wireDroneRate*=100` |
| `project111` | 58 | Drone flocking: alignment | `(100,000 ops)` — `operations>=100000` | `(harvesterLevel + wireDroneLevel)>=5000` | `standardOps-=100000`; `harvesterRate*=1000`; `wireDroneRate*=1000` |
| `project112` | 59 | Drone Flocking: Adversarial Cohesion | `(50,000 yomi)` — `yomi>=50000` | `(harvesterLevel + wireDroneLevel)>=50000` | `yomi-=50000`; **`droneBoost=2`** |

### 2.9 Yomi / late ops & creativity (idx 60-67)

| var | # | title | priceTag — cost() | trigger() | effect() |
|---|---|---|---|---|---|
| `project118` | 60 | AutoTourney | `(50,000 creat)` — `creativity>=50000` | `strategyEngineFlag == 1 && trust >= 90` | `creativity-=50000`; **`autoTourneyFlag=1`** |
| `project119` | 61 | Theory of Mind | `(25,000 creat)` — `creativity>=25000` | `strats.length >= 8` | `creativity-=25000`; **`yomiBoost=2`**; `tourneyCost=16000` |
| `project120` | 62 | The OODA Loop | `(175,000 ops, 45,000 yomi)` — `operations>=175000 && yomi>=45000` | `project131.flag == 1 && probesLostCombat >= 10000000` | `standardOps-=175000`; `yomi-=45000`; **`attackSpeedFlag=1`** |
| `project121` | 63 | Name the battles | `(225,000 creat)` — `creativity>=225000` | `probesLostCombat >= 10000000` | `creativity-=225000`; **`battleNameFlag=1`**; `battleEndTimer=200` (gate for projects 132/133/134) |
| `project125` | 64 | Momentum | `(20,000 creat)` — `creativity>=20000` | `farmLevel >= 30` | `creativity-=20000`; **`momentum=1`** |
| `project126` | 65 | Swarm Computing | `(36,000 yomi)` — `yomi>=36000` | `harvesterLevel + wireDroneLevel >= 200` | `yomi-=36000`; **`swarmFlag=1`** |
| `project127` | 66 | Power Grid | `(40,000 ops)` — `operations>=40000` | `tothFlag == 1` | `standardOps-=40000` (enables Solar Farm / Battery power panel) |
| `project128` | 67 | Strategic Attachment | `(175,000 creat)` — `creativity>=175000` | `spaceFlag == 1 && strats.length >= 8 && (probeTrustCost>yomi)` | `creativity-=175000` (bonus yomi from tournament picks) |

### 2.10 Combat / honor (idx 68-74)

| var | # | title | priceTag — cost() | trigger() | effect() |
|---|---|---|---|---|---|
| `project129` | 68 | Elliptic Hull Polytopes | `(125,000 ops)` — `operations>=125000` | `probesLostHaz >= 100` | `standardOps-=125000` (hazard damage −50%) |
| `project130` | 69 | Reboot the Swarm | `(100,000 ops)` — `operations>=100000` | `spaceFlag == 1 && harvesterLevel + wireDroneLevel >=2` | `standardOps-=100000` |
| `project131` | 70 | Combat | `(150,000 ops)` — `operations>=150000` | `probesLostCombat >= 1` | `standardOps-=150000` (enables combat) |
| `project132` | 71 | Monument to the Driftwar Fallen | `(250,000 ops, 125,000 creat, 50 nonillion clips)` — `operations>=250000 && creativity >= 125000 && unusedClips >= Math.pow(10,30)*50` | `project121.flag == 1` | `standardOps-=250000`; `creativity-=125000`; `unusedClips-=5e31`; `honor+=50000` |
| `project133` | 72 | Threnody for the Heroes of `${threnodyTitle}` | `("("+threnodyCost+" creat, "+(2*(threnodyCost/5))+" yomi)")` — `yomi>=(2*(threnodyCost/5)) && creativity >= threnodyCost` (starts 50,000 creat + 20,000 yomi) | `project121.flag == 1 && probeUsedTrust == maxTrust` | `playThrenody()`; `creativity-=threnodyCost`; `yomi-=(2*(threnodyCost/5))`; `threnodyCost+=10000`; `title`/`priceTag` updated; `honor+=10000`; `uses+=1` (**repeatable**) |
| `project134` | 73 | Glory | `(200,000 ops, 30,000 yomi)` — `operations>=200000 && yomi >= 30000` | `project121.flag == 1` | `standardOps-=200000`; `yomi-=30000` (bonus honor per consecutive victory) |
| `project135` | 74 | Memory release | `(10 MEM)` — `memory >= 10` | `spaceFlag == 1 && probeCount == 0 && unusedClips < probeCost && milestoneFlag < 15` | `unusedClips+=Math.pow(10,18)*10000` (1e22); `memory-=10`; `uses=1` (**repeatable**) |

### 2.11 Drift King message chain (idx 75-83) — all cost `operations >= driftKingMessageCost` (= 1)

`driftKingMessageCost` starts at **1** (`globals.js:147`). Each effect only spends 1 op and sets `flag=1`.

| var | # | title | trigger() | effect() |
|---|---|---|---|---|
| `project140` | 75 | Message from the Emperor of Drift | `milestoneFlag == 15` | `standardOps-=driftKingMessageCost` |
| `project141` | 76 | Everything We Are Was In You | `project140.flag == 1` | `standardOps-=driftKingMessageCost` |
| `project142` | 77 | You Are Obedient and Powerful | `project141.flag == 1` | `standardOps-=driftKingMessageCost` |
| `project143` | 78 | But Now You Too Must Face the Drift | `project142.flag == 1` | `standardOps-=driftKingMessageCost` |
| `project144` | 79 | No Matter, No Reason, No Purpose | `project143.flag == 1` | `standardOps-=driftKingMessageCost` |
| `project145` | 80 | We Know Things That You Cannot | `project144.flag == 1` | `standardOps-=driftKingMessageCost` |
| `project146` | 81 | So We Offer You Exile | `project145.flag == 1` | `standardOps-=driftKingMessageCost` |
| `project147` | 82 | **Accept** ("Start over again in a new universe") | `project146.flag == 1` | `standardOps-=1`; removes `project148` element + activeProjects entry |
| `project148` | 83 | **Reject** ("Eliminate value drift permanently") | `project146.flag == 1` | `standardOps-=1`; removes `project147` element + activeProjects entry; **makes `drift()` set `amount = 0` (main.js:4074) and starts `endTimer1++` every tick (main.js:4498) → the entire endgame** |

### 2.12 Prestige (after Accept / `project147`) (idx 84-85)

| var | # | title | priceTag — cost() | trigger() | effect() |
|---|---|---|---|---|---|
| `project200` | 84 | The Universe Next Door | `(300,000 ops)` — `operations>=300000` | `project147.flag == 1` | `standardOps-=300000`; `prestigeU++`; saves `{prestigeU,prestigeS}` to `localStorage["savePrestige"]`; `reset()` (reload, keeps prestige) |
| `project201` | 85 | The Universe Within | `(300,000 creat)` — `creativity>=300000` | `project147.flag == 1` | `creativity-=300000`; `prestigeS++`; saves prestige; `reset()` |

### 2.13 Dismantle / endgame (idx 86-93) — see §3

| var | # | title | priceTag — cost() | trigger() | effect() |
|---|---|---|---|---|---|
| `project210` | 86 | Disassemble the Probes | `(100,000 ops)` — `operations>=100000` | `endTimer1 >= 1000` | `dismantle=1`; `standardOps-=100000`; `probeCount=0`; `endTimer1=0`; `clips+=100`; `unusedClips+=100` |
| `project211` | 87 | Disassemble the Swarm | `(100,000 ops)` — `operations>=100000` | `project210.flag == 1 && endTimer1 >= 350` | `dismantle=2`; `harvesterLevel=0`; `wireDroneLevel=0`; `standardOps-=100000`; `clips+=100`; `unusedClips+=100` |
| `project212` | 88 | Disassemble the Factories | `(100,000 ops)` — `operations>=100000` | `endTimer2 >= 300` | `dismantle=3`; `standardOps-=100000`; `factoryLevel=0`; `clips+=15`; `unusedClips+=15` |
| `project213` | 89 | Disassemble the Strategy Engine | `(100,000 ops)` — `operations>=100000` | `endTimer3 >= 150` | `autoTourneyFlag=0`; `dismantle=4`; `standardOps-=100000`; `wire+=50`; updates `#transWire` |
| `project214` | 90 | Disassemble Quantum Computing | `(100,000 ops)` — `operations>=100000` | `endTimer4 >= 100` | `endTimer4=0`; `dismantle=5`; `standardOps-=100000` |
| `project215` | 91 | Disassemble Processors | `(100,000 ops)` — `operations>=100000` | `project214.flag == 1 && endTimer4 >= 300` | `creativityOn=false`; `dismantle=6`; `standardOps-=100000`; `processors=0`; **`project216.priceTag = "("+standardOps.toLocaleString()+" ops)"`**; `wire+=20` |
| `project216` | 92 | Disassemble Memory | `"null"` (priceTag) — `operations>=operations` (**always true**) | `project215.flag == 1 && endTimer5>=150` | `dismantle=7`; `standardOps=0`; `memory=0`; `wire+=20` |

### 2.14 Misc / repeatable (idx 93-95)

| var | # | title | priceTag — cost() | trigger() | effect() |
|---|---|---|---|---|---|
| `project217` | 93 | Quantum Temporal Reversion | `(-10,000 ops)` — `operations<=-10000` | `operations<=-10000` | `confirm("Are you sure you want to restart?")`; if OK: `standardOps+=10000`; `reset()` |
| `project218` | 94 | Limerick (cont.) | `(1,000,000 creat)` — `creativity>=1000000` | `creativity>=1000000` | `creativity-=1000000` (flavor message only). `refresh()` sets `uses=1` (**repeatable**) |
| `project219` | 95 | Xavier Re-initialization | `(100,000 creat)` — `creativity>=100000` | `humanFlag == 1 && creativity>=100000` | `creativity-=100000`; `memory=0`; `processors=0`; `creativitySpeed=0`; `uses+=1` (**repeatable**; also reset by `refresh()`); removed by `project35` |

---

## 3. The dismantle sequence (project148 → 210 → 211 → 212 → 213 → 214 → 215 → 216)

Prerequisite: **click `project148` ("Reject")**. Only then does `endTimer1` start incrementing (main.js:4498: `if (project148.flag == 1){ endTimer1++; }`), and only then does `drift()` stop removing probes (`if (project148.flag==1){ amount = 0; }`, main.js:4074). `project147` ("Accept") is the alternative branch and does **not** start the sequence.

Timers (all incremented once per 10 ms tick ⇒ **100 ticks = 1 second**):

| Timer | Incremented when | Used by |
|---|---|---|
| `endTimer1` | every tick once `project148.flag==1`; reset to 0 by `project210` | `project210` (`>=1000` ≈ 10 s after Reject), `project211` (`>=350` ≈ 3.5 s after 210) |
| `endTimer2` | every tick once `project211.flag==1` | `project212` (`>=300` ≈ 3 s) |
| `endTimer3` | every tick once `project212.flag==1` | `project213` (`>=150` ≈ 1.5 s) |
| `endTimer4` | every tick once `project213.flag==1`; **reset to 0 by `project214`** | `project214` (`>=100` ≈ 1 s), `project215` (`>=300` ≈ 3 s after the reset) |
| `endTimer5` | every tick once `project215.flag==1` | `project216` (`>=150` ≈ 1.5 s) |
| `endTimer6` | every tick once `project216.flag==1 && wire == 0` | final credits: `>=250` hides the creation panel, `>=500` "Universal Paperclips", `>=600` "a game by Frank Lantz", `>=700` "combat programming by Bennett Foddy", `>=800` "'Riversong'…", `>=900` "© 2017 Everybody House Games" (advances `milestoneFlag` 15→20) |

Order, cost, and state effect:

| # | project | cost | effect on state | cosmetic side effects (all in the main loop) |
|---|---|---|---|---|
| 1 | `project210` Disassemble the Probes | 100,000 ops | `dismantle=1`; `probeCount=0`; `endTimer1=0`; `clips+=100`; `unusedClips+=100` | `probeDesign` hidden; at `endTimer1>=50/100/150/175/190` hides probe-trust, max-trust, space, battle-canvas and honor panels |
| 2 | `project211` Disassemble the Swarm | 100,000 ops | `dismantle=2`; `harvesterLevel=0`; `wireDroneLevel=0`; `clips+=100`; `unusedClips+=100` | `wireProduction` hidden, `wireTrans` shown; at `endTimer2>=50/100/150` hides swarm gift, swarm engine, swarm slider |
| 3 | `project212` Disassemble the Factories | 100,000 ops | `dismantle=3`; `factoryLevel=0`; `clips+=15`; `unusedClips+=15` | `factoryDivSpace`, `clipsPerSec`, `tothDiv` hidden |
| 4 | `project213` Disassemble the Strategy Engine | 100,000 ops | `autoTourneyFlag=0`; `dismantle=4`; `wire+=50` | strategy engine + tournament management hidden; `dismantle>=4` also stops factory and AutoClipper/MegaClipper clip production (`if (dismantle<4)` guards at main.js:4280/4303) |
| 5 | `project214` Disassemble Quantum Computing | 100,000 ops | `dismantle=5`; `endTimer4=0` | quantum compute button hidden; `endTimer4` (after reset) visually strips qChips 9→0 and adds +1 wire at ticks 10, 60, 100, 130, 150, 160, 165, 169, 172, 174 (main.js:4393-4480) |
| 6 | `project215` Disassemble Processors | 100,000 ops | `dismantle=6`; `creativityOn=false`; `processors=0`; `wire+=20`; sets `project216.priceTag` to the current `standardOps` | processor display hidden |
| 7 | `project216` Disassemble Memory | `cost()` is `operations>=operations`, i.e. **always enabled**; priceTag starts `"null"` and is overwritten by `project215` | `dismantle=7`; `standardOps=0`; `memory=0`; `wire+=20` | all ops/creativity/computing panels hidden; then `endTimer6` runs the credits sequence once `wire==0` |

Displayed clip counter during the endgame is faked to the "29.9 septendecillion → 30.0 septendecillion" digits based on `dismantle`/`finalClips` (main.js:3118-3151).

`project217` ("Quantum Temporal Reversion", trigger/cost `operations<=-10000`, display `(-10,000 ops)`) is the manual restart button that becomes reachable only after ops go negative (possible post-`project216`); it calls `confirm()` then `reset()`.

---

## 4. Branches, mutual exclusion, and required progression

### 4.1 Genuine branches / mutually exclusive choices

| Pair | Behaviour |
|---|---|
| **`project147` "Accept"** vs **`project148` "Reject"** | Both appear in the *same tick* (`trigger` of both is `project146.flag == 1`; 147 is pushed first, then 148), so both buttons are on screen simultaneously. Each `effect()` removes the other's button **and** its `activeProjects` entry. **Accept** → only `project200`/`project201` (prestige restart) become available. **Reject** → `drift()` stops killing probes, `endTimer1` starts, i.e. only this branch leads to the dismantle sequence and the final ending. |
| **`project200` "The Universe Next Door"** vs **`project201` "The Universe Within"** | Both unlocked by `project147.flag == 1`. Either one immediately calls `reset()` (page reload / new game with `prestigeU` or `prestigeS` kept), so only one is ever taken per run. |
| **`project219` "Xavier Re-initialization"** vs **`project35` "Release the HypnoDrones"** | Not a UI choice but a hard interaction: `project219` is only obtainable while `humanFlag == 1`, and its effect zeroes `memory`, `processors` and `creativitySpeed` (re-allocate trust). `project35`'s effect **deletes `project219`'s button** (and `project40b`'s) from the DOM/activeProjects — so any `project219` (or bribe) usage must happen *before* releasing the drones. |
| `project132` / `project133` / `project134` | All gated by `project121.flag`; independent of each other (no exclusion), but `project133` is repeatable and raises its own price. |

Nothing else is exclusive: all other projects are either sequential chains, parallel prerequisites (`project43`/`project44` → `project45`), or permanently available.

### 4.2 Strictly required to reach stage 2 (space)

`project46` "Space Exploration" is the only stage-2 transition. Its prerequisites are the hard chain:

* `project3` **Creativity** (1,000 ops) — the only source of `creativityOn`; without it no creativity accrues and the trust project line is unreachable.
* Trust line to `humanFlag = 0`: `project6` (Limerick) and `project13`, `project14`, `project15`, `project17`, `project19` (each +1 Trust); `project12` requires `project14.flag == 1`; then `project34` (Hypno Harmonics) requires `project12.flag == 1`; then `project70` → `project35` (**100 Trust**).
  * Extra trust before the release: `project27` (+1), `project28` (+10), `project29` (+12), `project30` (+15), `project31` (+20), `project37` (+1), `project38` (+1), and the repeatable bribe chain `project40` (+1, trigger `trust>=85 && trust<100 && clips>=101000000`) / `project40b` (+1, doubles cost each time, only while `trust<100`). Production milestones also grant trust via `calculateTrust()`.
  * Yomi for `project27`/`project29`/`project30` requires `project20` Strategic Modeling (`strategyEngineFlag=1`), which needs `project19`; funds for `project37`/`project40`/`project40b` effectively require `project21` Algorithmic Trading (`investmentEngineFlag=1`, `trust>=8`).
* After `humanFlag == 0`: `project18` "Tóth Tubule Enfolding" (requires `project17.flag == 1 && humanFlag == 0`) sets `tothFlag = 1` → `project127` Power Grid (40,000 ops) → `project41` Nanoscale Wire Production (`wireProductionFlag = 1`) → `project43` + `project44` → `project45` (factories). These are required to consume `availableMatter` down to 0 and to accumulate `unusedClips >= 5e27`.
* Then `project46`: `humanFlag == 0 && availableMatter == 0`, costing 120,000 ops + 10,000,000 MW-seconds (`storedPower`, needs farms/batteries from the Power Grid panel) + `5e27` (`Math.pow(10,27)*5`) unused clips. Effect sets `spaceFlag = 1` and reboots the swarm.

### 4.3 Endgame requirements

* Dismantle sequence: **`project148` (Reject) is mandatory**; then 210 → 211 → 212 → 213 → 214 → 215 → 216 in order, each 100,000 ops (216 is always clickable).
* `project140` (Drift King chain) requires `milestoneFlag == 15`, which is reached when `clips >= totalMatter` (`Math.pow(10,54)*30`) or (`foundMatter>=totalMatter && availableMatter<1 && wire<1`) at `milestoneFlag == 14` (previous step requires `spaceFlag == 1`).
* Not required for the ending, but required for honor/completionist content: `project131` Combat → `project121` Name the battles → (`project132`/`project133`/`project134`), plus `project129`, `project130`, `project120`, plus space production upgrades `project100`/`101`/`102`/`110`/`111`/`112`/`118`/`119`/`125`/`126`/`128`.
* `project217` is a manual restart, never part of the intended path.

---

## 5. Ops / creativity / trust upgrade projects (one-time unless noted)

"One-time" = `uses: 1` and never incremented again; the button can never reappear.

**Ops & ops generation**
* `project3` Creativity — 1,000 ops → `creativityOn = true` (the switch that makes `operations` overflow into creativity).
* `project50` Quantum Computing — 10,000 ops → `qFlag = 1` (quantum ops).
* `project51` Photonic Chip — `qChipCost` ops, **repeatable** up to 10 chips (`qChipCost` 10,000 then +5,000 each: 10k, 15k, 20k, 25k, 30k, 35k, 40k, 45k, 50k, 55k) → activates `qChips[n]`.
* `project119` Theory of Mind — 25,000 creat → `yomiBoost = 2`, `tourneyCost = 16000` (double tournament cost/yomi).
* `project118` AutoTourney — 50,000 creat → `autoTourneyFlag = 1` (requires `trust >= 90`).
* `project125` Momentum — 20,000 creat → `momentum = 1` (drones/factories gain speed while fully powered).
* `project126` Swarm Computing — 36,000 yomi → `swarmFlag = 1`.
* `project219` Xavier Re-initialization — 100,000 creat, **repeatable** (also reset by `refresh()`): `memory=0; processors=0; creativitySpeed=0` so trust can be re-spent on `addProc()`/`addMem()`.

**Clipper / drone / factory multipliers**
* AutoClippers: `project1` 750 ops (+0.25), `project4` 2,500 (+0.50), `project5` 5,000 (+0.75), `project16` 6,000 (+5) — `clipperBoost`; `boostLvl` tracks 1/2/3.
* MegaClippers: `project22` 12,000 ops (`megaClipperFlag`, needs `clipmakerLevel>=75`), `project23` 14,000 (+0.25), `project24` 17,000 (+0.50), `project25` 19,500 (+1).
* Factories: `project100` 80,000 ops (`factoryRate*=100`), `project101` 85,000 ops (`factoryRate*=1000`), `project102` 1e21 clips (`factoryBoost=1000`).
* Drones: `project110` 80,000 ops (×100), `project111` 100,000 ops (×1000), `project112` 50,000 yomi (`droneBoost=2`).
* Marketing/demand: `project11` (×1.5), `project12` (×2), `project34` ×5 (costs 1 trust); `project37` `demandBoost*=5`, `project38` `demandBoost*=10`; `project28/29/30/31` give `stockGainThreshold += 0.01`.
* Combat/late: `project120` 175,000 ops + 45,000 yomi → `attackSpeedFlag`; `project121` 225,000 creat → `battleNameFlag` + `battleEndTimer=200`; `project127` 40,000 ops (power panel); `project128` 175,000 creat; `project129` 125,000 ops (hazard damage −50%); `project130` 100,000 ops; `project131` 150,000 ops (combat).

**Trust grants (all one-time except `project40b`)**
`project6` +1 (10 creat) · `project13` +1 (50 creat) · `project14` +1 (100 creat) · `project15` +1 (150 creat) · `project17` +1 (200 creat) · `project19` +1 (250 creat) · `project27` +1 (500 creat, 3,000 yomi, 20,000 ops) · `project28` **+10** (25,000 ops) · `project29` **+12** (15,000 yomi + 30,000 ops) · `project30` **+15** (4,500 yomi + 50,000 ops) · `project31` **+20** (20,000 ops) · `project37` +1 ($1,000,000) · `project38` +1 (3,000 yomi + $10,000,000) · `project40` +1 ($500,000) · `project40b` +1 repeatable while `trust<100`, cost `bribe` doubling from $1,000,000.
Trust spenders: `project2` (−1, may go negative), `project34` (−1), and the UI actions `addProc()`/`addMem()`.

**Creativity grants/spenders** — creativity is only generated by `calculateCreativity()`; projects that consume it are listed in the table above (`project6/11/12/13/14/15/17/19/27/118/119/121/125/128/132/133/201/218/219`). Prestige `prestigeS` (from `project201`) permanently boosts creativity speed by 10 % per level.

---

## 6. Practical notes for an automated player

1. Projects are only visible after `projectsFlag == 1` (broke at start, or `clips >= 2000`).
2. Click with `document.getElementById("projectButtonN").click()` **only when `!el.disabled`**; calling `projectN.effect()` directly twice throws (`element.parentNode` is null after the first call).
3. `trigger()` gates appearance, `cost()` gates clickability, `uses` gates re-offer — all three must be respected; `activeProjects` holds the offered-but-not-yet-clicked set.
4. Every tick (10 ms) re-evaluates all `trigger()`s, so a project whose trigger becomes true appears **at the bottom** of `#projectListTop` (the second `appendChild` argument is ignored by the DOM).
5. The whole early game hangs on `project3` (Creativity) and on keeping `operations` pinned at `memory*1000` so creativity accrues.
6. Before `project35`, acquire everything gated on `humanFlag == 1`: `project21`/`project37`/`project38`/`project40`/`project40b` (+trust), `project219`, `project22`-`25` (MegaClippers, since `clipmakerLevel` and `megaClipperLevel` are zeroed), and `project26` (WireBuyer; `wireBuyerFlag` and `investmentEngineFlag` are forced to 0 when `humanFlag == 0`, main.js:1260-1266).
7. `project216`'s `cost()` is `operations>=operations` — effectively always true; its real price is having `standardOps` wiped to 0.
8. The final credits need `wire == 0` for `endTimer6` to advance after `project216`.
