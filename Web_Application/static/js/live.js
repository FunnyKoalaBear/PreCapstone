// Live updates: polls /api/last-change/ and reloads the page when the signature changes
(() => {
  const dot = document.querySelector('[data-live]');
  if (!dot) return;
  const url = dot.dataset.live;
  const FLAG = 'live-updated', SCROLL = 'live-scroll';
  let sig = null, pending = false, delay = 5000, timer = null, stopped = false;

  const setState = (state) => {
    dot.dataset.state = state;
    const text = dot.dataset['title' + state[0].toUpperCase() + state.slice(1)];
    dot.title = text;
    dot.setAttribute('aria-label', text);
  };

  // After an automatic reload: restore scroll and show the toast
  try {
    const y = sessionStorage.getItem(SCROLL);
    if (y !== null) { sessionStorage.removeItem(SCROLL); window.scrollTo(0, +y); }
    if (sessionStorage.getItem(FLAG)) {
      sessionStorage.removeItem(FLAG);
      if (window.FT) window.FT.toast('info', dot.dataset.toast);
    }
  } catch (e) {}

  // Unsafe to reload while the user is working
  const busy = () => {
    const a = document.activeElement;
    if (a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return true;
    if (document.querySelector('dialog[open], [aria-modal="true"], .modal')) return true;
    if (document.querySelector('input[type="checkbox"]:checked')) return true;
    return !!(window.getSelection && String(window.getSelection()).length);
  };

  const reload = () => {
    try { sessionStorage.setItem(SCROLL, String(window.scrollY)); sessionStorage.setItem(FLAG, '1'); } catch (e) {}
    location.reload();
  };

  const schedule = (ms) => { clearTimeout(timer); if (!stopped) timer = setTimeout(check, ms); };

  const check = async () => {
    if (stopped || document.hidden) return;
    try {
      const r = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (r.redirected || r.status === 401 || r.status === 403 || !(r.headers.get('content-type') || '').includes('json')) {
        stopped = true; setState('paused'); return; // logged out: stop quietly
      }
      if (!r.ok) throw new Error(r.status);
      const next = (await r.json()).sig;
      delay = 5000; setState('live');
      if (sig === null) sig = next;
      else if (next !== sig) pending = true;
      if (pending) {
        if (!busy()) { reload(); return; }
        // busy: keep it pending and retry on the next check
      }
    } catch (e) {
      delay = Math.min(delay * 2, 30000); setState('offline'); // network or 5xx: back off
    }
    schedule(delay);
  };

  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); else clearTimeout(timer); });
  check();
})();
