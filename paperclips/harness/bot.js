// Universal Paperclips — in-page automated player.
// Drives the game only through the same actions a player has: clicking the real UI
// buttons and project buttons, and calling the functions those buttons call.
(function () {
  'use strict';
  if (typeof clips === 'undefined') { return; }

  var B = window.BOT = { events: [], errors: [], decisions: 0 };
  function nowMs() { return window.__vnow ? window.__vnow() : Date.now(); }
  function secs() { return Math.round(nowMs() / 100) / 10; }
  function ev(msg) { if (B.events.length < 8000) B.events.push(secs() + ' ' + msg); }
  B.ev = ev;
  function err(e) { if (B.errors.length < 100) B.errors.push(secs() + ' ' + String((e && e.stack) || e)); }
  B.errors = B.errors;

  // ------------------------------------------------------------------ projects
  // ids are DOM ids: "projectButtonN"
  var BLOCK = {
    projectButton147: 1,   // Accept (prestige) — we must Reject
    projectButton200: 1, projectButton201: 1,
    projectButton217: 1,   // manual restart
    projectButton219: 1,   // Xavier: wipes processors/memory
    projectButton135: 1,   // Memory release: burns 10 memory
    projectButton218: 1,   // flavour only
  };
  function projectAllowed(p, i) {
    switch (p.id) {
      case 'projectButton2':     // Beg for More Wire — costs 1 trust, only when broke
        return trust > 5;
      case 'projectButton35':    // Release the HypnoDrones — only after the money projects
        return project37.flag === 1 && project38.flag === 1;
      case 'projectButton133':   // Threnody — repeatable honour, keep if affordable
        return yomi > 8 * threnodyCost && creativity > 8 * threnodyCost;
      case 'projectButton132':   // Monument — honour for 5e31 clips
        return unusedClips > 1e33 && creativity > 300000;
      default:
        return !BLOCK[p.id];
    }
  }
  // One project per decision: the game re-evaluates project affordability once per
  // 10 ms tick, so clicking several in the same tick can spend operations we do not have.
  function clickProjects() {
    for (var i = 0; i < activeProjects.length; i++) {
      var p = activeProjects[i];
      if (p.element && !p.element.disabled && projectAllowed(p, i)) {
        try { p.element.click(); ev('proj ' + p.id + ' ' + p.title.trim()); }
        catch (e) { err(e); }
        return;
      }
    }
  }

  // --------------------------------------------------------------- money utils
  function investOn() { return investmentEngineFlag === 1 && project21.flag === 1; }
  function ensureFunds(n) {
    if (funds >= n) return true;
    if (investOn() && bankroll > 0) investWithdraw();
    return funds >= n;
  }
  function portNow() {
    return (typeof portTotal === 'number' && isFinite(portTotal) ? portTotal : bankroll) + funds;
  }
  function parkCash() {
    // keep the money compounding in the investment engine; it is withdrawn again
    // on demand when a money-costing project becomes reachable.
    if (!investOn() || funds <= 0) return;
    var need = neededFunds();
    if (need > 0 && funds < need) return;   // hoarding cash for a money project
    investDeposit();
  }
  // withdraw and buy, if the portfolio can take it
  function buyWith(n, fn) {
    if (!ensureFunds(n)) return false;
    var ok = funds >= n;
    if (ok) fn();
    return ok;
  }

  // ------------------------------------------------------------------ stage 1
  var marginTimer = 0;
  var budget = 0;

  function logTheoreticalProduction() {
    return clipperBoost * clipmakerLevel + megaClipperBoost * megaClipperLevel * 500;
  }

  // largest operations cost among the projects currently on screen
  function projectNeed(re) {
    var need = 0;
    for (var i = 0; i < activeProjects.length; i++) {
      var m = re.exec(activeProjects[i].priceTag || '');
      if (m) {
        var n = Number(m[1].replace(/,/g, ''));
        if (isFinite(n) && n > need) need = n;
      }
    }
    return need;
  }
  function neededOpsCeiling() { return projectNeed(/([\d,]+)\s*ops/); }
  function neededFunds() { return projectNeed(/\$\s*([\d,]+)/); }
  function neededYomi() { return projectNeed(/([\d,]+)\s*yomi/); }
  function neededCreativity() { return projectNeed(/([\d,]+)\s*creat/); }

  function stage1() {
    // ---- manual clicking (autoclicker)
    var c = 0;
    while (c < 3 && wire >= 1) { clipClick(1); c++; }

    // ---- price: target just enough demand to sell production + backlog
    marginTimer++;
    if (marginTimer >= 10) {
      marginTimer = 0;
      var prod = logTheoreticalProduction();
      var P = Math.max(clipRate, prod, 1);
      var A = 0.8 * marketing * marketingEffectiveness * demandBoost;
      var needSales = Math.max(P * 1.5, unsoldClips / 30, 10);
      // sales capacity per second:
      //   demand <100  -> 10 rolls/s * (demand/100) * 0.7*demand^1.15 = 0.07*demand^2.15
      //   demand >=100 -> 10 rolls/s * 0.7*demand^1.15            = 7*demand^1.15
      var need = needSales <= 1393
        ? Math.pow(needSales / 0.07, 1 / 2.15)
        : Math.pow(needSales / 7, 1 / 1.15);
      var target = A / need;
      if (!isFinite(target) || isNaN(target) || target <= 0) target = margin;
      var wireUnit = wireCost / Math.max(1, wireSupply);
      target = Math.max(Math.max(0.01, wireUnit * 2), Math.min(target, 1000));
      var guard = 0;
      while (margin < target - 0.011 && guard++ < 5000) raisePrice();
      while (margin > target + 0.011 && guard++ < 5000) lowerPrice();
    }

    // ---- revenue estimate (clips sold per second * margin)
    var revEst = Math.max(0.01, clipRate) * margin + avgRev;
    var port0 = (typeof portTotal === 'number' && isFinite(portTotal) ? portTotal : bankroll) + funds;

    // ---- spending budget for this second: never liquidate the compounding portfolio
    if (investOn()) {
      if (marginTimer === 1) budget = Math.max(port0 * 0.02, revEst);
    } else {
      budget = Math.max(0, funds - Math.max(3 * wireCost, 30));
    }

    // ---- wire first: never let production starve
    var prodPerSec = logTheoreticalProduction();
    var wtarget = Math.max(3000, prodPerSec * 40);
    var g = 0;
    while (wire < wtarget && g++ < 400) {
      if (!buyWith(wireCost, buyWire)) break;
    }
    var reserve = Math.max(3 * wireCost, 30);

    // ---- advertising: at most one per second and only while it is cheap
    if (adCost <= Math.max(budget * 3, (investOn() ? 0.02 * port0 : 200 * revEst))) {
      buyWith(adCost + reserve, buyAds);
    }

    // ---- clippers: spend the budget on production
    var port = port0;
    var allow = budget;
    g = 0;
    while (g++ < 300 && clipperCost <= allow && funds - reserve >= clipperCost) {
      if (!buyWith(clipperCost + reserve, makeClipper)) break;
      allow -= clipperCost;
    }
    g = 0;
    while (g++ < 300 && megaClipperFlag === 1 && megaClipperCost <= allow && funds - reserve >= megaClipperCost) {
      if (!buyWith(megaClipperCost + reserve, makeMegaClipper)) break;
      allow -= megaClipperCost;
    }
    budget = Math.max(0, allow);

    // ---- processors / memory
    allocateTrust();

    // ---- investments
    if (investOn()) {
      if (investStratElement && investStratElement.value !== 'hi') investStratElement.value = 'hi';
      var yomiReserve = neededYomi();
      g = 0;
      while (yomi - investUpgradeCost >= yomiReserve && g++ < 3) investUpgrade();
    }

    // ---- raise cash for money-costing projects when the portfolio can cover it
    var needCash = neededFunds();
    if (needCash > 0 && needCash <= 0.8 * port0 && funds < needCash) ensureFunds(needCash);

    // ---- quantum op boost
    quantumBoost();

    // ---- yomi
    driveTournament();

    parkCash();
  }

  function allocateTrust() {
    if (trust - processors - memory <= 0) return;
    // memory sets the operations ceiling, processors set the ops/sec rate and the
    // creativity speed. Creativity only accrues while operations sit at the ceiling,
    // so keep processors plentiful — but never below the next project's ops cost.
    var memTarget = Math.round(trust * 0.5);
    var need = Math.ceil(neededOpsCeiling() / 1000);
    if (need <= trust * 0.7) memTarget = Math.max(memTarget, need);
    memTarget = Math.min(trust - 2, Math.max(1, memTarget));
    var guard = 0;
    while (trust - processors - memory > 0 && guard++ < 500) {
      if (memory < memTarget) addMem(); else addProc();
    }
  }

  function quantumBoost() {
    if (qFlag !== 1 || !qChips[0].active) return;
    for (var k = 0; k < 3; k++) {                       // ~30 clicks/s
      var q = 0;
      for (var i = 0; i < qChips.length; i++) q += qChips[i].value;
      if (q > 0.1) qComp();
    }
  }

  function driveTournament() {
    if (strategyEngineFlag !== 1) return;
    if (tourneyInProg === 1) {
      var best = -1, bi = -1;
      for (var i = 0; i < strats.length && i < 10; i++) {
        if (strats[i].currentScore > best) { best = strats[i].currentScore; bi = i; }
      }
      if (bi >= 0 && best > 0) {
        if (stratPickerElement) stratPickerElement.value = String(bi);
        pick = String(bi);
      }
    } else if (operations >= tourneyCost + 1000) {
      newTourney();
      runTourney();
    }
  }

  // ---------------------------------------------------------------- earth era
  function earthEra() {
    // slider trades drone output ((200-p)/100) against swarm gift rate (p/100).
    // 100 = 1x output 1x gifts: needed because project46 wants a 120,000 ops ceiling,
    // which only swarm gifts can supply.
    if (swarmFlag === 1 && sliderElement && Number(sliderElement.value) !== 100) {
      sliderElement.value = '100';
      sliderPos = 100;
    }
    // spend swarm gifts: memory first (ops ceiling, needed for project46), then processors
    var guard = 0;
    while (swarmGifts > 0 && guard++ < 200) {
      if (memory < 130) addMem(); else addProc();
    }

    var supply = farmLevel * farmRate / 100;
    var powerDemand = (harvesterLevel + wireDroneLevel) * dronePowerRate / 100 + factoryLevel * factoryPowerRate / 100;
    var g = 0;
    while (supply < powerDemand * 1.15 && g++ < 40 && unusedClips >= farmCost * 2) {
      makeFarm(1);
      supply = farmLevel * farmRate / 100;
      powerDemand = (harvesterLevel + wireDroneLevel) * dronePowerRate / 100 + factoryLevel * factoryPowerRate / 100;
    }

    // batteries so surplus power is not wasted
    g = 0;
    var cap = batteryLevel * batterySize;
    while (storedPower > cap * 0.8 && g++ < 20 && unusedClips >= batteryCost * 10) {
      makeBattery(1);
      cap = batteryLevel * batterySize;
    }

    // rates per main-loop tick
    var fbst = factoryBoost > 1 ? factoryBoost * Math.floor(factoryLevel) : 1;
    var clipPerTick = fbst * Math.floor(factoryLevel) * factoryRate;
    var dbstw = droneBoost > 1 ? droneBoost * Math.floor(wireDroneLevel) : 1;
    var wirePerTick = dbstw * Math.floor(wireDroneLevel) * wireDroneRate;
    var dbsth = droneBoost > 1 ? droneBoost * Math.floor(harvesterLevel) : 1;
    var matterPerTick = dbsth * Math.floor(harvesterLevel) * harvesterRate;

    var C = unusedClips;
    // keep factories fed: wire drones first, then harvesters, then factories
    g = 0;
    while (wirePerTick * 1.3 < clipPerTick && g++ < 30 && C >= wireDroneCost * 3) {
      makeWireDrone(1);
      dbstw = droneBoost > 1 ? droneBoost * Math.floor(wireDroneLevel) : 1;
      wirePerTick = dbstw * Math.floor(wireDroneLevel) * wireDroneRate;
    }
    g = 0;
    while (matterPerTick * 1.3 < wirePerTick && g++ < 30 && C >= harvesterCost * 3) {
      makeHarvester(1);
      dbsth = droneBoost > 1 ? droneBoost * Math.floor(harvesterLevel) : 1;
      matterPerTick = dbsth * Math.floor(harvesterLevel) * harvesterRate;
    }
    // grow the factory base while it is cheap
    g = 0;
    while (factoryCost < unusedClips * 0.4 && g++ < 20) makeFactory();
    g = 0;
    while (wireDroneCost < unusedClips * 0.25 && g++ < 10) makeWireDrone(1);
    g = 0;
    while (harvesterCost < unusedClips * 0.25 && g++ < 10) makeHarvester(1);

    // quantum boost for ops
    quantumBoost();
    driveTournament();
  }

  // ---------------------------------------------------------------- space era
  var PLAN = [
    ['nav', 3], ['speed', 3], ['rep', 8], ['haz', 6], ['combat', 4],
    ['fac', 4], ['harv', 4], ['wire', 4],
    ['nav', 6], ['speed', 6], ['rep', 12], ['haz', 10], ['combat', 8],
    ['fac', 8], ['harv', 8], ['wire', 8],
    ['speed', 10], ['nav', 10], ['rep', 20], ['haz', 16], ['combat', 14],
    ['fac', 14], ['harv', 14], ['wire', 14],
    ['rep', 40], ['haz', 30], ['combat', 30], ['speed', 25], ['nav', 25],
    ['fac', 30], ['harv', 30], ['wire', 30],
  ];
  var planPtr = 0;
  function spaceEra() {
    if (swarmFlag === 1 && sliderElement && Number(sliderElement.value) < 200) {
      sliderElement.value = '200';
      sliderPos = 200;
    }
    // swarm gifts -> processors (ops/sec) first, memory only for op headroom
    var guard = 0;
    while (swarmGifts > 0 && guard++ < 50) addProc();

    var g = 0;
    while (yomi >= probeTrustCost && probeTrust < maxTrust && g++ < 40) increaseProbeTrust();
    g = 0;
    while (honor >= maxTrustCost && g++ < 10) increaseMaxTrust();

    // spend design points according to plan
    g = 0;
    while (probeUsedTrust < probeTrust && g++ < 400) {
      if (planPtr >= PLAN.length) { raiseProbeRep(); continue; }
      var step = PLAN[planPtr];
      var cur = { nav: probeNav, speed: probeSpeed, rep: probeRep, haz: probeHaz, combat: probeCombat, fac: probeFac, harv: probeHarv, wire: probeWire }[step[0]];
      if (cur >= step[1]) { planPtr++; continue; }
      if (step[0] === 'nav') raiseProbeNav();
      else if (step[0] === 'speed') raiseProbeSpeed();
      else if (step[0] === 'rep') raiseProbeRep();
      else if (step[0] === 'haz') raiseProbeHaz();
      else if (step[0] === 'combat') raiseProbeCombat();
      else if (step[0] === 'fac') raiseProbeFac();
      else if (step[0] === 'harv') raiseProbeHarv();
      else raiseProbeWire();
    }

    // launch probes: kick-start, then let replication do the work
    if (probeCount < 1) {
      g = 0;
      while (unusedClips > probeCost && probeCount < 1 && g++ < 50) makeProbe();
    } else if (probeRep > 0 && unusedClips > probeCost * 1e6) {
      g = 0;
      while (unusedClips > probeCost * 10 && g++ < 20) makeProbe();
    }

    // combat resolves itself; make sure it is watched (do not manipulate ships)
    quantumBoost();
    driveTournament();
  }

  // ---------------------------------------------------------------- main
  var wasStaged = 0;
  function step() {
    B.decisions++;
    try {
      if (dismantle >= 4 && finalClips < 100) {
        for (var i = 0; i < 5 && wire >= 1; i++) clipClick(1);
      }
      if (dismantle < 1) {
        if (humanFlag === 1) {
          if (wasStaged !== 1) { wasStaged = 1; ev('== stage1 begins'); }
          stage1();
        } else if (spaceFlag === 0) {
          if (wasStaged !== 2) { wasStaged = 2; ev('== earth era begins clips=' + clips.toExponential(2)); }
          earthEra();
        } else {
          if (wasStaged !== 3) { wasStaged = 3; ev('== space era begins'); }
          spaceEra();
        }
      }
      clickProjects();
    } catch (e) { err(e); }
  }
  window.setInterval(step, 100);

  // ---------------------------------------------------------------- reporting
  B.report = function () {
    return {
      t: secs(),
      stage: dismantle >= 1 ? 'dismantle' + dismantle : (humanFlag === 1 ? 'human' : (spaceFlag === 1 ? 'space' : 'earth')),
      milestoneFlag: milestoneFlag, dismantle: dismantle, finalClips: finalClips,
      trust: trust, processors: processors, memory: memory, ops: operations,
      creativity: Math.round(creativity), yomi: Math.round(yomi), honor: Math.round(honor),
      swarmGifts: swarmGifts, swarmStatus: swarmStatus,
      clips: clips, unusedClips: unusedClips, unsold: unsoldClips, clipRate: clipRate,
      funds: funds, bankroll: bankroll, secTotal: (typeof secTotal === 'number' ? secTotal : 0),
      portTotal: (typeof portTotal === 'number' ? portTotal : 0), portfolioSize: portfolioSize,
      margin: margin, wire: wire, ads: marketingLvl,
      demand: Math.round(demand), clipper: clipmakerLevel, mega: megaClipperLevel,
      probes: probeCount, probeTrust: probeTrust, maxTrust: maxTrust, matter: availableMatter,
      foundMatter: foundMatter, fac: factoryLevel, harv: harvesterLevel, wd: wireDroneLevel,
      farm: farmLevel, bat: batteryLevel, power: storedPower, powMod: powMod,
      drifters: drifterCount, battles: (typeof battles !== 'undefined' ? battles.length : -1),
      errors: B.errors.length,
    };
  };
})();
