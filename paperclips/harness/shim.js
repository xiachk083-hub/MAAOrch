// Virtual clock shim for Universal Paperclips.
// Must be loaded BEFORE the game scripts. Replaces setInterval/setTimeout with a
// manually-driven virtual timer queue so the whole game can be fast-forwarded.
(function () {
  'use strict';

  var seq = 1;
  var vnow = 0;                     // virtual milliseconds
  var timers = new Map();           // id -> {cb, delay, next, interval}
  window.__vtimers = timers;
  window.__verrors = [];
  window.__vnow = function () { return vnow; };

  function schedule(cb, delay, interval) {
    var id = seq++;
    var d = Math.max(0, Math.floor(Number(delay) || 0));
    if (interval && d < 1) d = 1;
    timers.set(id, { cb: cb, delay: d, next: vnow + d, interval: !!interval });
    return id;
  }

  window.setInterval = function (cb, delay) { return schedule(cb, delay, true); };
  window.setTimeout = function (cb, delay) { return schedule(cb, delay, false); };
  window.clearInterval = function (id) { timers.delete(id); };
  window.clearTimeout = function (id) { timers.delete(id); };

  var OrigDate = Date;
  function VDate() {
    if (arguments.length === 0) return new OrigDate(vnow);
    return new OrigDate(arguments[0]);
  }
  VDate.now = function () { return vnow; };
  VDate.parse = OrigDate.parse;
  VDate.UTC = OrigDate.UTC;
  VDate.prototype = OrigDate.prototype;
  window.Date = VDate;

  if (window.performance) {
    Object.defineProperty(window.performance, 'now', { value: function () { return vnow; }, configurable: true });
  }
  window.requestAnimationFrame = function (cb) { return schedule(function () { cb(vnow); }, 16, false); };
  window.cancelAnimationFrame = function (id) { timers.delete(id); };

  // Advance virtual time by `ms`, running every callback that comes due.
  window.__vadvance = function (ms, maxSteps) {
    var target = vnow + ms;
    var limit = maxSteps || 20000000;
    var steps = 0;
    while (steps < limit) {
      var bestId = null, bestNext = Infinity;
      for (var entry of timers) {
        if (entry[1].next < bestNext) { bestNext = entry[1].next; bestId = entry[0]; }
      }
      if (bestId === null || bestNext > target) break;
      vnow = bestNext;
      var t = timers.get(bestId);
      if (t.interval) { t.next = vnow + t.delay; } else { timers.delete(bestId); }
      try {
        t.cb();
      } catch (e) {
        if (window.__verrors.length < 200) window.__verrors.push(String((e && e.stack) || e));
      }
      steps++;
    }
    vnow = target;
    return steps;
  };

  window.__vset = function (ms) { vnow = ms; return vnow; };

  // Quiet down things we never want in a headless run.
  window.alert = function () {};
  if (window.console && console.log) {
    var origLog = console.log.bind(console);
    console.log = function () {};   // game is chatty; keep output clean
    window.__vlog = origLog;
  }
})();
