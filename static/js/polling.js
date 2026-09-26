/**
 * Polling — shared single-timer job scheduler (Phase 1).
 *
 * API:
 *   Polling.start(key, callback, intervalMs) -> key
 *     Registers a job. Does NOT invoke callback immediately — caller does
 *     its own initial poll first if needed (avoids double-fire/overlap).
 *   Polling.stop(key)
 *     Unregisters a job. Safe to call when not registered.
 *   Polling.run(key)
 *     Manually invokes a registered job's callback right now (still
 *     subject to the same anti-overlap guard as a normal tick).
 *   Polling.isActive(key) -> boolean
 *
 * Guarantees:
 *   - One shared timer exists iff at least one job is registered.
 *   - Ticking is paused while document.hidden is true (no catch-up burst
 *     on resume — elapsed time simply isn't accumulated while hidden).
 *   - Each job guards against overlapping runs of itself (a slow run in
 *     flight is not joined by a second concurrent run).
 *   - A stopped-then-restarted key gets a fresh job object; a stale
 *     in-flight callback from the OLD registration cannot clear/affect
 *     the NEW one (it closes over its own job object, not a map lookup).
 *   - Callback errors (sync throw or rejected promise) are caught and
 *     logged — never surfaced as an unhandled rejection.
 */
const Polling = (function () {
  const TICK_MS = 1000;
  const jobs = new Map();
  let timerId = null;

  function ensureTimer() {
    if (timerId !== null) return;
    timerId = setInterval(tick, TICK_MS);
  }

  function maybeStopTimer() {
    if (jobs.size === 0 && timerId !== null) {
      clearInterval(timerId);
      timerId = null;
    }
  }

  function tick() {
    if (typeof document !== 'undefined' && document.hidden) return; // pause while hidden
    for (const [key, job] of jobs) {
      job.elapsed += TICK_MS;
      if (job.elapsed >= job.intervalMs) {
        job.elapsed = 0;
        invoke(key, job);
      }
    }
  }

  function invoke(key, job) {
    if (job.running) return; // per-job anti-overlap guard
    job.running = true;
    Promise.resolve()
      .then(() => job.callback())
      .catch((err) => {
        try {
          console.error(`[Polling] job "${key}" failed:`, err);
        } catch (e) { /* no console — nothing more we can do */ }
      })
      .then(() => {
        job.running = false;
      });
  }

  function start(key, callback, intervalMs) {
    jobs.set(key, { callback, intervalMs, elapsed: 0, running: false });
    ensureTimer();
    return key;
  }

  function stop(key) {
    jobs.delete(key);
    maybeStopTimer();
  }

  function run(key) {
    const job = jobs.get(key);
    if (job) invoke(key, job);
  }

  function isActive(key) {
    return jobs.has(key);
  }

  return { start, stop, run, isActive };
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = Polling;
}
