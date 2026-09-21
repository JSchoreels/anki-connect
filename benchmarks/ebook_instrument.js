// Timing hooks preserve method return values and exceptions. No vocabulary text is saved.
(() => {
  if (location.pathname !== '/b') return;
  const run = JSON.parse(sessionStorage.getItem('ebook-benchmark-run') || '{}');
  const events = [];
  const requests = [];
  const errors = [];
  const cancellations = [];
  const diagnostic = String(run.run).startsWith('profile');
  const cacheCalls = {};
  const longTasks = [];
  if (diagnostic && PerformanceObserver.supportedEntryTypes.includes('longtask')) {
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) longTasks.push({ startMs: entry.startTime, durationMs: entry.duration });
    }).observe({ type: 'longtask', buffered: true });
  }
  let bookDomMs = null;
  let saved = false;
  const observer = new MutationObserver(() => {
    if (bookDomMs !== null) return;
    const element = document.querySelector('.book-content');
    if (element && element.children.length > 0) {
      bookDomMs = performance.now();
      observer.disconnect();
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, options) => {
    const url = String(input);
    const start = performance.now();
    const response = await originalFetch(input, options);
    if (options?.method?.toUpperCase() === 'POST' && (!url.includes('/__bench/') || url.includes('/__bench/live'))) {
      let action;
      try { action = JSON.parse(options.body).action; } catch { /* Non-Anki request. */ }
      const row = { kind: action ? 'anki' : 'yomitan', action: action || new URL(url, location.href).pathname,
        startMs: start, headersMs: performance.now() - start };
      requests.push(row);
      const originalJson = response.json.bind(response);
      response.json = async () => {
        const value = await originalJson();
        row.totalMs = performance.now() - start;
        if (value?.error) errors.push({ action: row.action, error: value.error });
        return value;
      };
    }
    return response;
  };
  async function finish(result, completedMs) {
    if (saved) return;
    saved = true;
    const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(result)));
    const analysisSha256 = Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
    // Two animation frames include the Svelte update and a paint opportunity.
    requestAnimationFrame(() => requestAnimationFrame(async () => {
      const report = { ...run, bookDomMs, analysisReadyMs: completedMs,
        panelPaintOpportunityMs: performance.now(),
        panelDomRows: document.querySelectorAll('[data-token-row]').length,
        events, requests, errors, cancellations, cacheCalls, longTasks,
        totalTokens: result.totalTokens, uniqueTokens: result.uniqueTokens,
        analysisSha256, userAgent: navigator.userAgent };
      window.__ebookBench.result = report;
      await originalFetch('/__bench/result', { method: 'POST', body: JSON.stringify(report) });
      window.__ebookBench.saved = true;
      const remaining = JSON.parse(sessionStorage.getItem('ebook-benchmark-queue') || '[]');
      const next = remaining.shift();
      sessionStorage.setItem('ebook-benchmark-queue', JSON.stringify(remaining));
      if (next) {
        setTimeout(() => { location.href = '/__bench/setup?' + new URLSearchParams({ ...next, auto: '1' }); }, 1000);
      } else {
        const status = document.createElement('output');
        status.id = 'benchmark-complete';
        status.textContent = 'Benchmark complete: ' + run.run;
        document.body.append(status);
      }
    }));
  }
  window.__ebookBench = {
    run, events, requests, errors,
    wrap(Class) {
      for (const name of ['loadCacheFromIndexedDB', 'warmCache', 'preTokenizeDocument', 'analyzeDocumentText']) {
        const original = Class.prototype[name];
        Class.prototype[name] = async function (...args) {
          if (diagnostic && this.cacheService && !this.cacheService.__benchmarkWrapped) {
            this.cacheService.__benchmarkWrapped = true;
            for (const method of Object.getOwnPropertyNames(Object.getPrototypeOf(this.cacheService))) {
              if (method === 'constructor' || typeof this.cacheService[method] !== 'function') continue;
              const originalCacheMethod = this.cacheService[method];
              this.cacheService[method] = function (...values) {
                const stat = cacheCalls[method] ||= { calls: 0, summedDurationMs: 0 };
                stat.calls++;
                const start = performance.now();
                const done = () => { stat.summedDurationMs += performance.now() - start; };
                try {
                  const result = originalCacheMethod.apply(this, values);
                  if (result && typeof result.finally === 'function') return result.finally(done);
                  done();
                  return result;
                } catch (error) { done(); throw error; }
              };
            }
          }
          const startMs = performance.now();
          try {
            const result = await original.apply(this, args);
            const endMs = performance.now();
            const summary = name === 'analyzeDocumentText'
              ? { totalTokens: result.totalTokens, uniqueTokens: result.uniqueTokens }
              : result;
            events.push({ name, startMs, endMs, durationMs: endMs - startMs, result: summary });
            if (name === 'analyzeDocumentText') void finish(result, endMs);
            return result;
          } catch (error) {
            (error?.name === 'AbortError' ? cancellations : errors).push({ method: name, error: String(error) });
            throw error;
          }
        };
      }
    },
  };
})();
