// Static demo of Factory Tracker. All data lives in the browser (localStorage); nothing is sent anywhere.
(() => {
  const LABELS = { NOT_STARTED: 'Not started', WAITING: 'Waiting', IN_PROGRESS: 'In progress', AT_VENDOR: 'At vendor', COMPLETE: 'Complete' };
  const STEP_LABELS = { pending: 'Pending', in_progress: 'In progress', at_vendor: 'At vendor', done: 'Done' };

  // ---------- helpers ----------
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const h = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const store = {
    get(k, d) { try { const v = JSON.parse(localStorage.getItem(k)); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage blocked */ } },
  };
  const jst = (t) => new Date(t).toLocaleString('sv-SE', { timeZone: 'Asia/Tokyo' });
  const fmtDT = (t) => (t ? jst(t).slice(0, 16) : '-');
  const todayStr = () => jst(Date.now()).slice(0, 10);
  const addDays = (d, n) => new Date(Date.parse(d + 'T00:00:00Z') + n * DAY).toISOString().slice(0, 10);
  const dueBack = (started, lead) => (started == null || lead == null ? null : addDays(jst(started).slice(0, 10), lead));
  const daysLate = (due) => (due ? Math.max(Math.floor((Date.parse(todayStr() + 'T00:00:00Z') - Date.parse(due + 'T00:00:00Z')) / DAY), 0) : 0);
  const dayWord = (n) => (n === 1 ? '1 day' : n + ' days');
  const badge = (cls, text) => `<span class="badge badge-${cls}">${h(text)}</span>`;
  const proc = (id) => PROCESSES.find((p) => p.id === id);
  const itemCode = (i) => `${i.order}-${String(i.no).padStart(2, '0')}`;
  const msg = (kind, text) => `<ul class="messages"><li class="message ${kind}">${h(text)}</li></ul>`;
  const table = (head, body, empty) =>
    `<div class="table-wrap"><table class="table"><thead><tr>${head.map((x) => `<th>${x}</th>`).join('')}</tr></thead><tbody>${
      body.length ? body.join('') : `<tr><td colspan="${head.length}">${empty}</td></tr>`}</tbody></table></div>`;

  // ---------- state ----------
  const events = () => {
    let e = store.get('ft_events', null);
    if (!e) {
      e = SEED_EVENTS.map(([hrs, tag, type, p], i) => ({ id: i + 1, t: Date.now() - hrs * 36e5, tag, type, proc: p, device: DEVICES[1] }));
      store.set('ft_events', e);
    }
    return e;
  };
  const orders = () => [
    ...BASE_ORDERS.map((o) => ({ ...o, due: addDays(todayStr(), o.due) })),
    ...store.get('ft_orders', []),
  ];
  const items = () => {
    const tagmap = store.get('ft_tags', {});
    return [
      ...BASE_ITEMS.map((i) => ({ ...i, tagStart: Date.now() - i.tagAgo * DAY })),
      ...store.get('ft_items', []),
    ].map((i) => ({ ...i, ...(tagmap[itemCode(i)] || {}) }));
  };
  const orderOf = (i) => orders().find((o) => o.job === i.order);

  // ---------- status logic (same rules as the Django app) ----------
  function itemStatus(item) {
    const rows = item.route.map((id) => ({ proc: proc(id), state: 'pending', started: null, finished: null }));
    const warnings = [];
    const evs = item.tag ? events().filter((e) => e.tag === item.tag && e.t >= item.tagStart).sort((a, b) => a.t - b.t) : [];
    for (const e of evs) {
      const c = rows.filter((r) => r.proc.id === e.proc);
      const pick = (...st) => { for (const s of st) { const r = c.find((x) => x.state === s); if (r) return r; } return null; };
      let r = null;
      if (e.type === 'ARRIVED') { r = pick('pending'); if (r) { r.state = 'done'; r.started = r.finished = e.t; } }
      else if (e.type === 'STARTED') { r = pick('pending'); if (r) { r.state = 'in_progress'; r.started = e.t; } }
      else if (e.type === 'FINISHED') { r = pick('in_progress', 'pending'); if (r) { r.state = 'done'; r.finished = e.t; } }
      else if (e.type === 'SENT_OUT') { r = pick('pending'); if (r) { r.state = 'at_vendor'; r.started = e.t; } }
      else if (e.type === 'RETURNED') { r = pick('at_vendor', 'pending'); if (r) { r.state = 'done'; r.finished = e.t; } }
      const pname = proc(e.proc).name;
      if (!c.length) warnings.push(`Event for a process not in the route: ${pname}`);
      else if (!r) warnings.push(`Unexpected ${ACTION_LABELS[e.type]} event for ${pname}`);
    }
    rows.forEach((r, i) => {
      if (r.state !== 'pending') rows.slice(0, i).filter((p) => p.state === 'pending').forEach((p) => warnings.push(`Step skipped: ${p.proc.name}`));
    });
    const active = rows.filter((r) => r.state === 'in_progress' || r.state === 'at_vendor');
    const current = active.length ? active.reduce((a, b) => (b.started > a.started ? b : a)) : rows.find((r) => r.state !== 'done') || null;
    let code;
    if (!rows.length || rows.every((r) => r.state === 'pending')) code = 'NOT_STARTED';
    else if (!current) code = 'COMPLETE';
    else code = { in_progress: 'IN_PROGRESS', at_vendor: 'AT_VENDOR' }[current.state] || 'WAITING';
    return { code, label: LABELS[code], rows, current, warnings: [...new Set(warnings)], events: evs, last: evs.length ? evs[evs.length - 1].t : null };
  }
  function orderSummary(order) {
    const sts = items().filter((i) => i.order === order.job).map((i) => itemStatus(i).code);
    const done = sts.filter((c) => c === 'COMPLETE').length;
    if (sts.length && done === sts.length) return { code: 'COMPLETE', label: LABELS.COMPLETE };
    if (sts.every((c) => c === 'NOT_STARTED')) return { code: 'NOT_STARTED', label: LABELS.NOT_STARTED };
    return { code: 'IN_PROGRESS', label: `In progress (${done}/${sts.length} items complete)` };
  }
  const lateBadge = (n) => (n ? badge('overdue', dayWord(n)) : '');
  const link = (job, text) => `<a href="order.html?job=${encodeURIComponent(job)}">${h(text)}</a>`;

  // ---------- shell ----------
  function shell() {
    document.body.insertAdjacentHTML('afterbegin', `
      <div class="demo-banner">Static demo with sample data. Your changes are saved in this browser only.
        <button type="button" id="reset-demo">Reset demo data</button></div>
      <header class="site-header"><span class="brand">Factory Tracker</span>
        <nav class="site-nav"><a href="index.html">Home</a><a href="orders.html">Orders</a><a href="new-order.html">New order</a>
        <a href="tags.html">Tags</a><a href="simulate.html">Simulate scan</a></nav></header>`);
    document.body.insertAdjacentHTML('beforeend', '<footer class="site-footer">Factory Tracker (demo)</footer>');
    $('#reset-demo').addEventListener('click', () => {
      ['ft_events', 'ft_orders', 'ft_items', 'ft_tags'].forEach((k) => { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } });
      location.href = 'index.html';
    });
  }

  // ---------- pages ----------
  function pageHome(app) {
    const rows = items().map((i) => ({ i, o: orderOf(i), st: itemStatus(i) }));
    const complete = {};
    rows.forEach((r) => { complete[r.i.order] = (complete[r.i.order] ?? true) && r.st.code === 'COMPLETE'; });
    const counts = { IN_PROGRESS: 0, WAITING: 0, AT_VENDOR: 0, NOT_STARTED: 0 };
    const overdue = new Set(); const inHouse = []; const atVendor = [];
    for (const r of rows) {
      if (complete[r.i.order]) continue;
      const late = r.o.due < todayStr();
      if (late) overdue.add(r.i.order);
      if (r.st.code in counts) counts[r.st.code]++;
      const cur = r.st.current;
      if (!cur || !['WAITING', 'IN_PROGRESS', 'AT_VENDOR'].includes(r.st.code)) continue;
      Object.assign(r, { cur, late });
      if (r.st.code === 'AT_VENDOR') { r.due = dueBack(cur.started, cur.proc.lead); r.lateDays = daysLate(r.due); atVendor.push(r); } else inHouse.push(r);
    }
    inHouse.sort((a, b) => (a.o.due + a.i.order + a.i.no).localeCompare(b.o.due + b.i.order + b.i.no));
    atVendor.sort((a, b) => b.lateDays - a.lateDays || a.cur.started - b.cur.started);
    const vOver = atVendor.filter((r) => r.lateDays > 0).length;
    const card = (cls, n, text) => `<div class="card counter ${cls}"><strong>${n}</strong> ${text}</div>`;
    app.innerHTML = `<h1>Home</h1><div class="counters">
      ${card('counter-in_progress', counts.IN_PROGRESS, 'In progress')}${card('counter-waiting', counts.WAITING, 'Waiting')}
      ${card('counter-at_vendor', counts.AT_VENDOR, 'At vendor')}${card('counter-not_started', counts.NOT_STARTED, 'Not started')}
      ${card('counter-overdue', overdue.size, 'Overdue orders')}
      ${card('counter-vendor' + (vOver ? ' counter-overdue counter-late' : ''), vOver, 'Vendor overdue')}</div>
      <h2>In-house now</h2>${table(['Item', 'Part', 'Customer', 'Current process', 'Status', 'Due date', 'Last scan (JST)'],
      inHouse.map((r) => `<tr><td>${link(r.i.order, itemCode(r.i))}</td><td>${h(r.i.part)}</td><td>${h(r.o.customer)}</td><td>${h(r.cur.proc.name)}</td>
        <td>${badge(r.st.code.toLowerCase(), r.st.label)}</td><td>${r.o.due} ${r.late ? badge('overdue', 'Overdue') : ''}</td><td>${fmtDT(r.st.last)}</td></tr>`),
      'Nothing in the factory right now.')}
      <h2>At vendors</h2>${table(['Item', 'Part', 'Vendor', 'Process', 'Out since (JST)', 'Days out', 'Due back', 'Late by'],
      atVendor.map((r) => `<tr><td>${link(r.i.order, itemCode(r.i))}</td><td>${h(r.i.part)}</td><td>${h(r.cur.proc.vendor || '-')}</td><td>${h(r.cur.proc.name)}</td>
        <td>${fmtDT(r.cur.started)}</td><td>${Math.floor((Date.now() - r.cur.started) / DAY)}</td><td>${r.due || 'No due date'}</td><td>${lateBadge(r.lateDays)}</td></tr>`),
      'Nothing is out at vendors.')}`;
  }

  function pageOrders(app) {
    const created = new URLSearchParams(location.search).get('created');
    app.innerHTML = `<h1>Orders</h1>${created ? msg('success', `Order ${created} created.`) : ''}
      <form class="filters" id="f">
        <div><label for="q">Search</label><input type="search" id="q"></div>
        <div><label for="status">Status</label><select id="status"><option value="">All</option>
          ${['NOT_STARTED', 'IN_PROGRESS', 'COMPLETE'].map((c) => `<option value="${c}">${LABELS[c]}</option>`).join('')}</select></div>
        <div><label for="sort">Sort by</label><select id="sort"><option value="due">Due date</option><option value="newest">Newest</option><option value="customer">Customer</option></select></div>
        <label class="check"><input type="checkbox" id="late"> Overdue only</label>
        <a href="new-order.html">New order</a></form><div id="list"></div>`;
    const draw = () => {
      const q = $('#q').value.trim().toLowerCase(); const status = $('#status').value; const sort = $('#sort').value; const lateOnly = $('#late').checked;
      let rows = orders().map((o) => {
        const its = items().filter((i) => i.order === o.job);
        const s = orderSummary(o);
        return { o, its, s, late: o.due < todayStr() && s.code !== 'COMPLETE' };
      }).filter((r) => (!q || [r.o.job, r.o.customer, r.o.custNo, ...r.its.map((i) => i.part)].some((x) => String(x || '').toLowerCase().includes(q)))
        && (!status || r.s.code === status) && (!lateOnly || r.late));
      const cmp = { due: (a, b) => (a.o.due + a.o.job).localeCompare(b.o.due + b.o.job), newest: (a, b) => b.o.job.localeCompare(a.o.job),
        customer: (a, b) => a.o.customer.localeCompare(b.o.customer) || a.o.due.localeCompare(b.o.due) };
      rows.sort(cmp[sort]);
      $('#list').innerHTML = table(['Job number', 'Customer', 'Customer order number', 'Due date', 'Items', 'Status'],
        rows.map((r) => `<tr><td>${link(r.o.job, r.o.job)}</td><td>${h(r.o.customer)}</td><td>${h(r.o.custNo)}</td>
          <td>${r.o.due} ${r.late ? badge('overdue', 'Overdue') : ''}</td><td>${r.its.length}</td><td>${badge(r.s.code.toLowerCase(), r.s.label)}</td></tr>`),
        'No orders found.');
    };
    $('#f').addEventListener('input', draw); $('#f').addEventListener('submit', (e) => e.preventDefault()); draw();
  }

  function pageOrder(app) {
    const job = new URLSearchParams(location.search).get('job');
    const o = orders().find((x) => x.job === job);
    if (!o) { app.innerHTML = '<p><a href="orders.html">&larr; Back to Orders</a></p><h1>Order not found</h1>'; return; }
    const s = orderSummary(o); const late = o.due < todayStr() && s.code !== 'COMPLETE';
    const cards = items().filter((i) => i.order === job).map((i) => {
      const st = itemStatus(i);
      const steps = st.rows.map((r, n) => {
        let extra = '';
        if (r.state === 'at_vendor') {
          const due = dueBack(r.started, r.proc.lead); const l = daysLate(due);
          extra = due ? ` <small>due back ${due}</small> ${l ? badge('overdue', 'Late by ' + dayWord(l)) : ''}` : '';
        }
        return `<tr><td>${n + 1}</td><td>${h(r.proc.name)}${r.proc.vendor ? ` (${h(r.proc.vendor)})` : ''}</td>
          <td>${badge(r.state, STEP_LABELS[r.state])}${extra}</td><td>${fmtDT(r.started)}</td><td>${fmtDT(r.finished)}</td></tr>`;
      });
      return `<div class="card card-wide"><h2>${itemCode(i)} - ${h(i.part)} &times; ${i.qty} ${badge(st.code.toLowerCase(), st.label)}</h2>
        <p>${i.tag ? `Box ${h(i.box)}, tag ${h(i.tag)}` : 'No tag assigned.'}</p>
        ${st.warnings.length ? `<ul class="warnings">${st.warnings.map((w) => `<li>${h(w)}</li>`).join('')}</ul>` : ''}
        ${table(['#', 'Process', 'State', 'Started', 'Finished'], steps, '-')}
        <h3>Event history</h3>${table(['Time (JST)', 'Action', 'Process', 'Device'],
          st.events.map((e) => `<tr><td>${jst(e.t)}</td><td>${ACTION_LABELS[e.type]}</td><td>${h(proc(e.proc).name)}</td><td>${h(e.device)}</td></tr>`), 'No events yet.')}</div>`;
    });
    app.innerHTML = `<p><a href="orders.html">&larr; Back to Orders</a></p>
      <h1>${h(o.job)} ${badge(s.code.toLowerCase(), s.label)}</h1>
      <dl class="info"><div><dt>Customer</dt><dd>${h(o.customer)}</dd></div><div><dt>Customer order number</dt><dd>${h(o.custNo || '-')}</dd></div>
      <div><dt>Due date</dt><dd>${o.due} ${late ? badge('overdue', 'Overdue') : ''}</dd></div>
      ${o.notes ? `<div><dt>Notes</dt><dd>${h(o.notes)}</dd></div>` : ''}</dl>${cards.join('')}`;
  }

  function pageNewOrder(app) {
    const options = '<option value="">Choose a process</option>' + PROCESSES.map((p) => `<option value="${p.id}">${h(p.name)} (${p.kind.toLowerCase()})</option>`).join('');
    const step = () => `<div class="route-step"><select>${options}</select><button type="button" class="remove-step">Remove</button></div>`;
    const card = () => `<div class="card item-card"><button type="button" class="remove-item">Remove</button>
      <h2>Item <span class="item-number"></span></h2>
      <div><label>Part name</label><input class="part" required></div>
      <div><label>Quantity</label><input class="qty" type="number" min="1" value="1"></div>
      <div class="route"><label>Route</label><div class="route-steps">${step()}</div><button type="button" class="add-step button-secondary">Add step</button></div></div>`;
    app.innerHTML = `<h1>New order</h1><div id="errors"></div><form id="f">
      <div class="card"><div><label>Customer</label><select id="customer">${CUSTOMERS.map((c) => `<option>${h(c)}</option>`).join('')}</select></div>
      <div><label>Customer order number</label><input id="custNo"></div><div><label>Due date</label><input id="due" type="date"></div>
      <div><label>Notes</label><textarea id="notes" rows="3"></textarea></div></div>
      <div class="form-actions"><button type="button" id="add-item" class="button-secondary">Add another item</button>
      <button type="submit" class="button">Create order</button></div></form>`;
    const actions = $('.form-actions');
    const number = () => $$('.item-card:not(.is-removed) .item-number').forEach((s, n) => { s.textContent = n + 1; });
    const addItem = () => { actions.insertAdjacentHTML('beforebegin', card()); number(); };
    addItem();
    $('#add-item').addEventListener('click', addItem);
    $('#f').addEventListener('click', (e) => {
      const t = e.target;
      if (t.closest('.remove-item')) { t.closest('.item-card').classList.add('is-removed'); number(); }
      else if (t.closest('.add-step')) { t.closest('.route').querySelector('.route-steps').insertAdjacentHTML('beforeend', step()); }
      else if (t.closest('.remove-step')) {
        const row = t.closest('.route-step');
        if (row.parentElement.children.length > 1) row.remove(); else row.querySelector('select').value = '';
      }
    });
    $('#f').addEventListener('submit', (e) => {
      e.preventDefault();
      const errs = []; const its = [];
      if (!$('#due').value) errs.push('Due date is required.');
      $$('.item-card:not(.is-removed)').forEach((c, n) => {
        const route = $$('select', c).map((s) => Number(s.value)).filter(Boolean);
        const part = $('.part', c).value.trim(); const qty = Number($('.qty', c).value);
        if (!part || !(qty > 0)) errs.push(`Item ${n + 1}: part name and quantity are required.`);
        if (!route.length) errs.push(`Item ${n + 1}: add at least one step to the route.`);
        its.push({ part, qty, route });
      });
      if (!its.length) errs.push('Add at least one item.');
      if (errs.length) { $('#errors').innerHTML = `<ul class="messages">${errs.map((x) => `<li class="message error">${h(x)}</li>`).join('')}</ul>`; return; }
      const prefix = todayStr().slice(2, 4) + todayStr().slice(5, 7);
      const seq = Math.max(0, ...orders().filter((o) => o.job.startsWith(prefix + '-')).map((o) => Number(o.job.split('-')[1]))) + 1;
      const job = `${prefix}-${String(seq).padStart(3, '0')}`;
      store.set('ft_orders', [...store.get('ft_orders', []), { job, customer: $('#customer').value, custNo: $('#custNo').value.trim(), due: $('#due').value, notes: $('#notes').value.trim() }]);
      store.set('ft_items', [...store.get('ft_items', []), ...its.map((x, n) => ({ order: job, no: n + 1, ...x, tag: null }))]);
      location.href = `orders.html?created=${job}`;
    });
  }

  function pageTags(app) {
    const all = items(); const used = all.filter((i) => i.tag); const free = all.filter((i) => !i.tag);
    const flash = new URLSearchParams(location.search).get('linked');
    app.innerHTML = `<h1>Tags</h1>${flash ? msg('success', flash) : ''}<div id="errors"></div>
      <form class="card" id="f"><div><label>Item</label><select id="item">${free.map((i) => `<option value="${itemCode(i)}">${itemCode(i)} ${h(i.part)}</option>`).join('')}</select></div>
      <div><label>Box label</label><input id="box" required></div><div><label>Tag UID</label><input id="uid" required></div>
      <button type="submit" class="button" ${free.length ? '' : 'disabled'}>Link tag</button>${free.length ? '' : '<p>All items already have a tag.</p>'}</form>
      <h2>In use</h2>${table(['Tag', 'Box', 'Item', 'Since (JST)'],
        used.map((i) => `<tr><td>${h(i.tag)}</td><td>${h(i.box)}</td><td>${link(i.order, itemCode(i))} ${h(i.part)}</td><td>${fmtDT(i.tagStart)}</td></tr>`), 'No tags in use.')}
      <h2>History</h2>${table(['Tag', 'Box', 'Item', 'From (JST)', 'To (JST)'], [], 'No history yet.')}`;
    $('#f').addEventListener('submit', (e) => {
      e.preventDefault();
      const uid = $('#uid').value.trim().toUpperCase(); const box = $('#box').value.trim(); const code = $('#item').value;
      const clash = used.find((i) => i.tag === uid) ? `Tag ${uid} is already in use.` : used.find((i) => i.box === box) ? `Box ${box} is already in use.` : '';
      if (clash) { $('#errors').innerHTML = `<ul class="messages"><li class="message error">${h(clash)}</li></ul>`; return; }
      store.set('ft_tags', { ...store.get('ft_tags', {}), [code]: { tag: uid, box, tagStart: Date.now() } });
      location.href = `tags.html?linked=${encodeURIComponent(`Tag ${uid} linked to box ${box}, item ${code}.`)}`;
    });
  }

  function pageSimulate(app) {
    const actions = PROCESSES.flatMap((p) => KIND_ACTIONS[p.kind].map((a) => `<option value="${p.id}:${a}">${h(p.name)} - ${ACTION_LABELS[a]}</option>`));
    const tags = items().filter((i) => i.tag).map((i) => `<option value="${h(i.tag)}">`).join('');
    app.innerHTML = `<h1>Simulate scan</h1><div id="flash"></div>
      <form class="card" id="f"><div><label>Tag UID</label><input id="uid" list="uids" autocomplete="off" required></div>
      <div><label>Device</label><select id="device">${DEVICES.map((d) => `<option>${h(d)}</option>`).join('')}</select></div>
      <div><label>What happened</label><select id="action">${actions.join('')}</select></div>
      <datalist id="uids">${tags}</datalist><button type="submit" class="button">Record event</button></form>
      <h2>Last 10 events</h2><div id="log"></div>`;
    const draw = () => {
      const last = [...events()].sort((a, b) => b.t - a.t).slice(0, 10);
      $('#log').innerHTML = table(['Time (JST)', 'Tag', 'Process', 'Action', 'Device'],
        last.map((e) => `<tr><td>${jst(e.t)}</td><td>${h(e.tag)}</td><td>${h(proc(e.proc).name)}</td><td>${ACTION_LABELS[e.type]}</td><td>${h(e.device)}</td></tr>`), 'No events yet.');
    };
    $('#f').addEventListener('submit', (e) => {
      e.preventDefault();
      const uid = $('#uid').value.trim().toUpperCase(); const [pid, type] = $('#action').value.split(':');
      const all = events(); const now = Date.now();
      store.set('ft_events', [...all, { id: Math.max(0, ...all.map((x) => x.id)) + 1, t: now, tag: uid, type, proc: Number(pid), device: $('#device').value }]);
      const item = items().find((i) => i.tag === uid && i.tagStart <= now);
      $('#flash').innerHTML = item
        ? msg('success', `Saved: box ${item.box}, item ${itemCode(item)}, status: ${itemStatus(item).label}`)
        : msg('warning', `Event saved, but tag ${uid} matches no item.`);
      draw();
    });
    draw();
  }

  // ---------- start ----------
  document.addEventListener('DOMContentLoaded', () => {
    shell();
    const app = $('#app');
    ({ home: pageHome, orders: pageOrders, order: pageOrder, new: pageNewOrder, tags: pageTags, simulate: pageSimulate })[document.body.dataset.page](app);
  });
})();
