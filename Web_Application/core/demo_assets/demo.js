// Static demo: filters, search and sorting run in the browser; forms and unavailable links show a notice. No server is needed.
(() => {
  const ja = document.documentElement.lang === 'ja';
  const T = ja
    ? { unassigned: (u) => `タグ ${u} は品目に紐付いていません。`, demo: 'これはデモです。内容は保存されません。', empty: '条件に一致するものはありません。', none: '出荷実績はまだありません。' }
    : { unassigned: (u) => `Tag ${u} is not linked to any item.`, demo: 'This is a demo, nothing is saved.', empty: 'Nothing matches the filters.', none: 'Nothing has been shipped yet.' };
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmt = (s, v) => s.replace(/%\((\w+)\)[ds]/g, (_, k) => v[k]);

  let timer;
  const notice = () => {
    let t = document.querySelector('.demo-toast');
    if (!t) { t = document.createElement('div'); t.className = 'demo-toast'; t.setAttribute('role', 'alert'); document.body.append(t); }
    t.textContent = T.demo;
    t.hidden = false;
    clearTimeout(timer);
    timer = setTimeout(() => { t.hidden = true; }, 4000);
  };

  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-demo-link]');
    if (a) { e.preventDefault(); notice(); }
  });
  // Every form is either a live filter (below) or a demo notice
  document.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!e.target.matches('.filters[data-live]') && !e.target.querySelector('input[name=tag][list]')) notice();
  }, true);

  // ---- Row filters: controls are matched to data-f-* attributes of the table rows ----
  const panelFor = (form) => {
    for (let el = form.nextElementSibling; el; el = el.nextElementSibling) {
      const p = el.matches('.scroll-panel') ? el : el.querySelector('.scroll-panel');
      if (p) return p;
    }
    return null;
  };
  const num = (v) => (v !== '' && isFinite(v) ? Number(v) : v);

  const applyRows = (form, panel) => {
    const body = panel.querySelector('tbody');
    const rows = [...body.querySelectorAll('tr')].filter((r) => !(r.children.length === 1 && r.firstElementChild.hasAttribute('colspan')));
    rows.forEach((r, i) => { if (r._i === undefined) r._i = i; });
    const controls = [...form.elements].filter((c) => c.name && !c.disabled && c.type !== 'hidden' && c.type !== 'submit' && c.name !== 'current');
    let sort = null;
    const ok = (r) => controls.every((c) => {
      const key = cap(c.name.replace(/^[rm]_/, ''));
      const d = r.dataset;
      if (c.name === 'sort') { sort = c; return true; }
      if (c.type === 'checkbox') {
        if (c.hasAttribute('data-f-show')) return c.checked || !d['f' + key];
        return !c.checked || !!d['f' + key];
      }
      if (c.value === '') return true;
      if (/(^|_)q$/.test(c.name)) return (d.fText ?? r.textContent).toLowerCase().includes(c.value.trim().toLowerCase());
      const range = c.name.match(/^(.*)_(from|to)$/);
      if (range) {
        const v = d['f' + cap(range[1].replace(/^[rm]_/, ''))];
        return v === undefined || (range[2] === 'from' ? v >= c.value : v <= c.value);
      }
      return d['f' + key] === undefined || d['f' + key] === c.value;
    });
    rows.forEach((r) => { r.hidden = !ok(r); });
    if (sort) {
      const dir = sort.selectedOptions[0]?.dataset.dir === 'desc' ? -1 : 1;
      const attr = 'fSort' + cap(sort.value);
      if (rows.every((r) => r.dataset[attr] !== undefined)) {
        const keyed = rows.map((r) => [num(r.dataset[attr]), r]);
        keyed.sort((a, b) => dir * (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0) || a[1]._i - b[1]._i);
        body.append(...keyed.map((k) => k[1]));
      }
    }
    let msg = panel.parentNode.querySelector('.demo-empty');
    const shown = rows.some((r) => !r.hidden);
    if (!shown && !msg) { msg = document.createElement('p'); msg.className = 'demo-empty'; msg.textContent = T.empty; panel.after(msg); }
    if (msg) msg.hidden = shown;
  };

  // ---- Shipping: on-time delivery by month, recomputed from the shipped items ----
  const applyMonths = (form, panel, facts) => {
    const v = (n) => form.elements[n].value;
    const q = v('m_q').trim().toLowerCase();
    const months = {};
    facts.filter((f) => (!v('m_customer') || String(f.c) === v('m_customer')) && f.t.includes(q)
      && (!v('month_from') || f.m >= v('month_from')) && (!v('month_to') || f.m <= v('month_to')))
      .forEach((f) => { const m = months[f.m] || (months[f.m] = { n: 0, ok: 0 }); m.n++; m.ok += f.ok; });
    const heads = [...panel.querySelectorAll('thead th')].map((th) => th.textContent.trim());
    const rows = Object.keys(months).sort().reverse().map((k) => {
      const m = months[k];
      const cells = [k, m.n, m.ok, Math.round(100 * m.ok / m.n) + '%'];
      return '<tr>' + cells.map((c, i) => `<td data-label="${esc(heads[i])}">${c}</td>`).join('') + '</tr>';
    });
    panel.querySelector('tbody').innerHTML = rows.join('') || `<tr><td colspan="4">${T.none}</td></tr>`;
  };

  // ---- Analysis: plan vs actual and results, recomputed from the finished steps and results ----
  const severity = (pct, sev) => (pct === null ? '' : (sev.find(([limit]) => pct > limit) || [0, ''])[1]);
  const fixed = (x, d) => { const s = (Math.round(x * 10 ** d) / 10 ** d).toFixed(d); return /^-0(\.0+)?$/.test(s) ? s.slice(1) : s; };
  const duration = (secs, L) => {
    if (secs === null || secs === undefined) return '';
    const mins = Math.floor(secs / 60), d = Math.floor(mins / 1440), h = Math.floor(mins / 60) % 24, m = mins % 60;
    if (d) return fmt(L.dh, { d, h });
    if (h) return fmt(L.hm, { h, m });
    return m ? fmt(L.m, { m }) : L.lt1m;
  };
  const badge = (sev, text) => `<span class="badge sev-${sev || 'minor'}">${text}</span>`;

  const renderAnalysis = (root, form, facts) => {
    const L = facts.labels, tab = root.dataset.tab, v = (n) => form.elements[n]?.value || '';
    const ok = (f) => (!v('date_from') || f.d >= v('date_from')) && (!v('date_to') || f.d <= v('date_to'))
      && (!v('customer') || String(f.c) === v('customer')) && (!v('process') || String(f.p) === v('process')) && (!v('vendor') || String(f.v) === v('vendor'));
    const steps = facts.steps.filter(ok);
    const name = (p) => facts.processes[p];
    const th = (labels) => '<thead><tr>' + labels.map((l) => `<th>${esc(l)}</th>`).join('') + '</tr></thead>';
    const lab = (l, h) => `data-label="${esc(l)}"`;
    let strip = '', head, body, foot = '', cols;
    if (tab === 'plan') {
      const groups = {};
      steps.filter((s) => s.df !== null).forEach((s) => (groups[s.p] = groups[s.p] || []).push(s));
      let rows = Object.entries(groups).map(([p, g]) => {
        const pl = g.reduce((a, s) => a + s.pl, 0) / g.length, ac = g.reduce((a, s) => a + s.ac, 0) / g.length;
        const pct = pl > 0 ? (ac - pl) * 100 / pl : null;
        return { p, n: g.length, pl, ac, df: ac - pl, pct, sev: severity(pct, facts.sev) };
      });
      rows.sort(v('sort') === 'delay' ? (a, b) => (a.pct === null) - (b.pct === null) || (b.pct || 0) - (a.pct || 0)
        : (a, b) => (facts.en[a.p] < facts.en[b.p] ? -1 : 1));
      const top = rows.filter((r) => r.pct && r.pct > 0).sort((a, b) => b.pct - a.pct).slice(0, 3);
      if (top.length) {
        strip = `<ul class="summary-strip"><li><span class="summary-label">${L.worst}</span> <strong>${esc(name(top[0].p))}</strong> ${badge(top[0].sev, '+' + fixed(top[0].pct, 0) + '%')}</li>`
          + `<li><span class="summary-label">${L.top3}</span> ${top.map((r, i) => `<span class="summary-item">${i + 1}. ${esc(name(r.p))} ${badge(r.sev, '+' + fixed(r.pct, 0) + '%')}</span>`).join('')}</li></ul>`;
      }
      cols = [L.process, L.steps, L.planned, L.actual, L.diff, L.overrun];
      head = th(cols);
      body = rows.map((r) => {
        const over = r.pct === null ? '-' : r.sev ? `<span class="badge sev-${r.sev}">${fixed(r.pct, 0)}% &middot; ${L[r.sev]}</span>` : fixed(r.pct, 0) + '%';
        const cells = [esc(name(r.p)), r.n, fixed(r.pl, 1), fixed(r.ac, 1), r.df > 0 ? `<span class="badge badge-overdue">+${fixed(r.df, 1)}</span>` : fixed(r.df, 1), over];
        return `<tr${r.sev === 'severe' ? ' class="hot"' : ''}>` + cells.map((c, i) => `<td ${lab(cols[i])}>${c}</td>`).join('') + '</tr>';
      });
      body = body.join('') || `<tr><td colspan="6">${L.noPlan}</td></tr>`;
    } else {
      const ev = facts.results.filter(ok);
      const groups = {};
      const grab = (p) => groups[p] || (groups[p] = { p, records: 0, qty: 0, def: 0, pr: [], w: [] });
      ev.forEach((e) => { const g = grab(e.p); g.records++; g.qty += e.q; g.def += e.x; });
      steps.forEach((s) => { const g = grab(s.p); if (s.pr !== null) g.pr.push(s.pr); if (s.w !== null) g.w.push(s.w); });
      const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
      const rows = Object.values(groups).map((g) => ({ ...g, processing: avg(g.pr), wait: avg(g.w), hot: [] }))
        .sort((a, b) => (name(a.p) < name(b.p) ? -1 : 1));
      const hotOf = (key, flag) => {
        let best = null;
        rows.forEach((r) => { if (r[key] && (!best || r[key] > best[key])) best = r; });
        if (best) best.hot.push(flag);
        return best;
      };
      const lp = hotOf('processing', 'processing'), lw = hotOf('wait', 'wait');
      if (lp || lw) {
        strip = '<ul class="summary-strip">'
          + (lp ? `<li><span class="summary-label">${L.longestProcessing}</span> <strong>${esc(name(lp.p))}</strong> ${duration(lp.processing, L)}</li>` : '')
          + (lw ? `<li><span class="summary-label">${L.longestWait}</span> <strong>${esc(name(lw.p))}</strong> ${duration(lw.wait, L)}</li>` : '') + '</ul>';
      }
      cols = [L.process, L.records, L.quantity, L.defects, L.rate, L.processing, L.wait];
      head = th(cols);
      const mark = (r, f) => (r.hot.includes(f) ? ` <span class="badge sev-severe">${L.longest}</span>` : '');
      body = rows.map((r) => {
        const cells = [esc(name(r.p)), r.records, r.qty, r.def, fixed(r.qty ? r.def * 100 / r.qty : 0, 1) + '%',
          `<span>${duration(r.processing, L) || '-'}${mark(r, 'processing')}</span>`, `<span>${duration(r.wait, L) || '-'}${mark(r, 'wait')}</span>`];
        return `<tr${r.hot.length ? ' class="hot"' : ''}>` + cells.map((c, i) => `<td ${lab(cols[i])}>${c}</td>`).join('') + '</tr>';
      }).join('') || `<tr><td colspan="7">${L.noResults}</td></tr>`;
      if (rows.length) {
        const t = rows.reduce((a, r) => ({ rec: a.rec + r.records, q: a.q + r.qty, d: a.d + r.def }), { rec: 0, q: 0, d: 0 });
        foot = `<tfoot><tr><th>${L.total}</th><th ${lab(L.records)}>${t.rec}</th><th ${lab(L.quantity)}>${t.q}</th><th ${lab(L.defects)}>${t.d}</th>`
          + `<th ${lab(L.rate)}>${fixed(t.q ? t.d * 100 / t.q : 0, 1)}%</th><th></th><th></th></tr></tfoot>`;
      }
    }
    const old = root.querySelector('.scroll-panel');
    const panel = old.cloneNode(false);
    panel.innerHTML = `<table class="table table-cards">${head}<tbody>${body}</tbody>${foot}</table>`;
    root.querySelector('.summary-strip')?.remove();
    old.replaceWith(panel);
    if (strip) panel.insertAdjacentHTML('beforebegin', strip);
  };

  // ---- Simulate scan: picking a tag opens the page that shows that tag's next step ----
  const startPicker = () => {
    const pick = document.querySelector('input[name=tag][list]');
    if (!pick) return;
    const go = () => {
      const uid = pick.value.trim().toUpperCase();
      pick.form.parentNode.querySelectorAll('.demo-pick').forEach((n) => n.remove());
      if (!uid) return;
      if ([...document.querySelectorAll('#tag-picks option')].some((o) => o.value === uid)) { location.href = 'simulate_' + uid + '.html'; return; }
      pick.form.insertAdjacentHTML('afterend', `<div class="card demo-pick"><p>${esc(T.unassigned(uid))}</p></div>`);
    };
    pick.form.addEventListener('change', go);
    pick.form.addEventListener('submit', go);
  };

  // ---- Start-up ----
  document.addEventListener('DOMContentLoaded', () => {
    startPicker();
    const forms = [...document.querySelectorAll('form.filters')];
    const params = new URLSearchParams(location.search);
    const factsEl = document.getElementById('demo-facts');
    const facts = factsEl ? JSON.parse(factsEl.textContent) : null;
    const analysis = document.getElementById('analysis');
    forms.forEach((form) => {
      params.forEach((value, name) => {
        const c = form.elements[name];
        if (!c || c.type === 'hidden') return;
        if (c.type === 'checkbox') c.checked = value === '1' || value === 'on'; else c.value = value;
      });
      const inactive = form.elements.inactive;
      if (inactive && !params.has('inactive')) inactive.checked = false;
      form.querySelectorAll('noscript, button[type=submit]').forEach((b) => b.remove());
      const cur = form.elements.current;
      if (cur) cur.addEventListener('change', () => { location.href = cur.checked ? 'index_current.html' : 'index.html'; });
      let run;
      if (analysis && facts && form.nextElementSibling === analysis) run = () => renderAnalysis(analysis, form, facts);
      else {
        const panel = panelFor(form);
        if (!panel) return;
        run = form.dataset.demoFacts === 'months' ? () => applyMonths(form, panel, facts) : () => applyRows(form, panel);
      }
      form.dataset.live = '1';
      form.addEventListener('input', run);
      form.addEventListener('change', run);
      run();
    });
  });
})();
