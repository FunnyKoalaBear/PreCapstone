// Static demo of Factory Tracker: pages and UI. All data lives in this browser (localStorage); nothing is sent anywhere.
(() => {
  const KEY = 'ft_demo_v2', LANG_KEY = 'ft_lang';
  const { NOT_STARTED, WAITING, IN_PROGRESS, AT_VENDOR, COMPLETE } = FT;

  // ---------- helpers ----------
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const h = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const store = {
    get(k, d) { try { const v = JSON.parse(localStorage.getItem(k)); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage blocked: keep going in memory */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* storage blocked */ } },
  };
  const Q = new URLSearchParams(location.search);
  const badge = (cls, text) => `<span class="badge badge-${cls}">${h(text)}</span>`;
  const statusBadge = (code) => badge(code.toLowerCase(), t(code));
  const pname = (p) => (p ? (I18N.lang === 'ja' && p.ja ? p.ja : p.name) : '-');
  const fmtDT = (ms, sec) => (ms == null ? '-' : FT.jstStr(ms).slice(0, sec ? 19 : 16));
  const orderLink = (job, text) => `<a href="order.html?job=${encodeURIComponent(job)}">${h(text)}</a>`;
  // templatetags/duration.py
  const fmtDur = (ms) => {
    if (ms == null) return '-';
    const mins = Math.floor(ms / 60000), d = Math.floor(mins / 1440), hh = Math.floor(mins / 60) % 24, m = mins % 60;
    return d ? t('dur_dh', { d, h: hh }) : hh ? t('dur_hm', { h: hh, m }) : m ? t('dur_m', { m }) : t('dur_lt1');
  };
  const itemBox = (e) => `${e.item.id}${e.multi ? ' - ' + t('box_n', { l: e.boxLabel }) : ''}`;
  const lateBadge = (n) => badge('overdue', t('n_days', { n }));
  const table = (head, rows, empty) =>
    `<div class="table-wrap"><table class="table"><thead><tr>${head.map((x) => `<th>${h(x)}</th>`).join('')}</tr></thead><tbody>${
      rows.length ? rows.join('') : `<tr><td colspan="${head.length}">${h(empty)}</td></tr>`}</tbody></table></div>`;
  const warnText = (x) => t(x.k, { p: pname(world.proc(x.proc)), e: x.type ? t(x.type) : '' });
  const reasonText = (r) => (r.k === 'r_due_in' || r.k === 'r_overdue_by'
    ? t(r.k, { days: t('n_days', { n: r.d }), steps: t('n_steps_left', { n: r.s }) }) : t(r.k, { n: r.n }));
  const flashHtml = () => (flash ? `<ul class="messages"><li class="message ${flash.kind}">${h(flash.text)}</li></ul>` : '');
  const errText = (e) => t(e.k, e);

  // ---------- state ----------
  let state = store.get(KEY, {}) || {};
  let seed, world, flash = null;
  const save = () => store.set(KEY, state);
  const refresh = () => { const now = Date.now(); seed = seed || buildSeed(now); world = FT.makeWorld(seed, state, now); };
  const push = (k, v) => { state[k] = [...(state[k] || []), v]; save(); refresh(); };
  const tagList = () => world.tags.filter((u) => !world.inactive.has(u));

  // ---------- shell ----------
  const NAV = [['index', 'nav_home'], ['orders', 'nav_orders'], ['new-order', 'nav_new'], ['boxes', 'nav_boxes'], ['board', 'nav_board'],
    ['priorities', 'nav_prio'], ['tags', 'nav_tags'], ['simulate', 'nav_sim']];
  function shell() {
    document.documentElement.lang = I18N.lang;
    $('#banner').innerHTML = `${h(t('banner'))} <button type="button" id="reset-demo">${h(t('reset_demo'))}</button>`;
    $('#hdr').innerHTML = `<span class="brand">${h(t('brand'))}</span><nav class="site-nav">${
      NAV.map(([p, k]) => `<a href="${p}.html">${h(t(k))}</a>`).join('')}</nav>
      <form class="lang-switch" id="lang-switch">${['ja', 'en'].map((l) =>
    `<button type="button" data-lang="${l}"${I18N.lang === l ? ' class="active" aria-current="true"' : ''}>${l === 'ja' ? '日本語' : 'English'}</button>`).join('')}</form>`;
    $('#ftr').textContent = t('footer');
    $('#reset-demo').addEventListener('click', () => { store.del(KEY); state = {}; seed = null; flash = null; boot(); });
    $$('#lang-switch button').forEach((b) => b.addEventListener('click', () => {
      I18N.lang = b.dataset.lang; store.set(LANG_KEY, I18N.lang); flash = null; boot();
    }));
  }

  // ---------- home ----------
  function pageHome(app) {
    const d = FT.homeData(world), c = d.counters;
    const card = (cls, href, n, label) => `<a class="card counter counter-${cls}" href="${href}"><strong>${n}</strong> ${h(label)}</a>`;
    const inRows = d.inHouse.map((e) => `<tr><td>${orderLink(e.order.job, itemBox(e))}</td><td>${h(e.item.part)}</td><td>${h(e.order.customer)}</td>
      <td>${h(pname(e.proc))}</td><td>${statusBadge(e.code)}</td>
      <td>${e.due}${e.late ? ' ' + badge('overdue', t('overdue')) : ''}</td><td>${fmtDT(e.lastEvent)}</td>
      <td>${fmtDur(e.idle)}${e.stagnant ? ' ' + badge('stagnant', t('stagnant')) : ''}</td></tr>`);
    const vRows = d.atVendor.map((e) => `<tr><td>${orderLink(e.order.job, itemBox(e))}</td><td>${h(e.item.part)}</td><td>${h(e.proc.vendor || '-')}</td>
      <td>${h(pname(e.proc))}</td><td>${fmtDT(e.started)}</td><td>${e.daysOut ?? '-'}</td>
      <td>${e.dueBack || h(t('no_due_date'))}</td><td>${e.lateDays ? lateBadge(e.lateDays) : ''}</td></tr>`);
    app.innerHTML = `<h1>${h(t('home_title'))}</h1><div class="counters">
      ${card('in_progress', 'boxes.html?status=in_progress', c.in_progress, t('IN_PROGRESS'))}
      ${card('waiting', 'boxes.html?status=waiting', c.waiting, t('WAITING'))}
      ${card('at_vendor', 'boxes.html?status=at_vendor', c.at_vendor, t('AT_VENDOR'))}
      ${card('not_started', 'boxes.html?status=not_started', c.not_started, t('NOT_STARTED'))}
      ${card('stagnant', 'boxes.html?stagnant=1', c.stagnant, t('stagnant'))}
      ${card('overdue', 'orders.html?late=1', c.overdue, t('overdue_orders'))}
      ${card('vendor' + (c.vendor_overdue ? ' counter-overdue counter-late' : ''), 'boxes.html?status=at_vendor&vendor_late=1', c.vendor_overdue, t('vendor_overdue'))}
    </div>
    <h2>${h(t('in_house_now'))}</h2>${table([t('item'), t('part'), t('customer'), t('current_process'), t('status'), t('due_date'), t('last_scan'), t('idle_for')], inRows, t('nothing_factory'))}
    <h2>${h(t('at_vendors'))}</h2>${table([t('item'), t('part'), t('vendor'), t('process'), t('out_since'), t('days_out'), t('due_back'), t('late_by')], vRows, t('nothing_vendor'))}`;
  }

  // ---------- boxes ----------
  const STATUS_PARAMS = { in_progress: IN_PROGRESS, waiting: WAITING, at_vendor: AT_VENDOR, not_started: NOT_STARTED };
  function pageBoxes(app) {
    const status = STATUS_PARAMS[Q.get('status')] || null, stagnant = Q.get('stagnant') === '1', vendorLate = Q.get('vendor_late') === '1';
    const parts = [status && t(status), stagnant && t('stagnant'), vendorLate && t('vendor_late')].filter(Boolean);
    const rows = FT.boxesPage(world, { status, stagnant, vendorLate }).map((e) => `<tr>
      <td>${orderLink(e.order.job, e.item.id)}</td>
      <td>${e.boxLabel ? h(e.boxLabel) : `${h(t('not_linked_yet'))} <a href="tags.html">${h(t('link_a_tag'))}</a>`}</td>
      <td>${h(e.item.part)}</td><td>${h(e.order.customer)}</td><td>${h(pname(e.proc))}</td><td>${statusBadge(e.code)}</td><td>${e.due}</td><td>${fmtDur(e.idle)}</td>
      <td>${e.late ? badge('overdue', t('overdue')) : ''} ${e.stagnant ? badge('stagnant', t('stagnant')) : ''} ${e.lateDays ? badge('overdue', t('vendor_late')) + ' ' + lateBadge(e.lateDays) : ''}</td></tr>`);
    app.innerHTML = `<p><a href="index.html">&larr; ${h(t('back_home'))}</a></p>
      <h1>${h(t('boxes_title'))}${parts.length ? ` <small>- ${h(parts.join(', '))}</small>` : ''}</h1>
      ${rows.length ? table([t('item'), t('box'), t('part'), t('customer'), t('current_process'), t('status'), t('due_date'), t('idle_for'), ''], rows, '') : `<p>${h(t('no_boxes_match'))}</p>`}`;
  }

  // ---------- orders ----------
  function pageOrders(app) {
    const q = (Q.get('q') || '').trim().toLowerCase(), status = Q.get('status') || '', sort = ['due', 'newest', 'customer'].includes(Q.get('sort')) ? Q.get('sort') : 'due';
    const late = Q.get('late') === '1';
    const SORTS = { due: (a, b) => a.due.localeCompare(b.due) || a.job.localeCompare(b.job), newest: (a, b) => b.job.localeCompare(a.job),
      customer: (a, b) => a.customer.localeCompare(b.customer) || a.due.localeCompare(b.due) };
    const rows = [...world.orders].sort(SORTS[sort]).map((o) => {
      const its = world.items.filter((i) => i.order === o.job);
      return { o, its, sum: FT.orderSummary(world, o), late: o.due < world.today };
    }).filter((r) => {
      const hay = [r.o.job, r.o.customer, r.o.custNo, ...r.its.map((i) => i.part)].join(' ').toLowerCase();
      return (!q || hay.includes(q)) && (!status || r.sum.code === status) && (!late || (r.late && r.sum.code !== COMPLETE));
    });
    const lateOf = (r) => r.late && r.sum.code !== COMPLETE;
    const body = rows.map((r) => `<tr><td>${orderLink(r.o.job, r.o.job)}</td><td>${h(r.o.customer)}</td><td>${h(r.o.custNo)}</td>
      <td>${r.o.due}${lateOf(r) ? ' ' + badge('overdue', t('overdue')) : ''}</td>
      <td>${r.its.length}${r.its.some((i) => i.boxCount > 1) ? ` <small>${h(t('n_boxes', { n: r.its.reduce((s, i) => s + i.boxCount, 0) }))}</small>` : ''}</td>
      <td>${badge(r.sum.code.toLowerCase(), r.sum.code === IN_PROGRESS ? t('order_progress', r.sum) : t(r.sum.code))}</td></tr>`);
    const opt = (v, text, cur) => `<option value="${v}"${v === cur ? ' selected' : ''}>${h(text)}</option>`;
    app.innerHTML = `${Q.get('created') ? `<ul class="messages"><li class="message success">${h(t('order_created', { job: Q.get('created') }))}</li></ul>` : ''}
      <h1>${h(t('orders_title'))}</h1>
      <form method="get" class="filters">
        <div><label for="q">${h(t('search'))}</label><input type="search" id="q" name="q" value="${h(Q.get('q') || '')}"></div>
        <div><label for="status">${h(t('status'))}</label><select id="status" name="status">${opt('', t('all'), status)}${[NOT_STARTED, IN_PROGRESS, COMPLETE].map((c) => opt(c, t(c), status)).join('')}</select></div>
        <div><label for="sort">${h(t('sort_by'))}</label><select id="sort" name="sort">${opt('due', t('due_date'), sort)}${opt('newest', t('newest'), sort)}${opt('customer', t('customer'), sort)}</select></div>
        <label class="check"><input type="checkbox" name="late" value="1"${late ? ' checked' : ''}> ${h(t('overdue_only'))}</label>
        <button type="submit" class="button">${h(t('apply'))}</button><a href="new-order.html">${h(t('new_order'))}</a></form>
      ${table([t('job_number'), t('customer'), t('customer_order_number'), t('due_date'), t('items'), t('status')], body, t('no_orders'))}`;
  }

  // ---------- order detail ----------
  function timelineHtml(box) {
    return `<ol class="timeline">${FT.timeline(box.rows, world.now).map((s) => {
      const r = s.row, back = r.state === 'at_vendor' ? FT.dueBack(r.started, r.proc.lead) : null, lateDays = FT.daysLate(back, world.today);
      return `${s.wait ? `<li class="timeline-wait">${h(t('waited', { d: fmtDur(s.wait) }))}</li>` : ''}
        <li class="timeline-step timeline-${r.state}"><span class="timeline-marker">${box.rows.indexOf(r) + 1}</span><div class="timeline-body">
        <strong>${h(pname(r.proc))}${r.proc.vendor ? ` (${h(r.proc.vendor)})` : ''}</strong> ${badge(r.state, t(r.state))}
        ${back ? `<small>${h(t('due_back_d', { d: back }))}</small> ${lateDays ? badge('overdue', t('late_by_n', { n: lateDays })) : ''}` : ''}
        <div class="timeline-times">${h(t('started'))}: ${fmtDT(r.started)} &middot; ${h(t('finished'))}: ${fmtDT(r.finished)}
        ${s.duration ? `&middot; ${fmtDur(s.duration)}${s.running ? ' ' + h(t('so_far')) : ''}` : ''}</div></div></li>`;
    }).join('')}</ol>`;
  }
  function eventsHtml(box, single) {
    const evs = box.assignments.length ? FT.eventsForBox(world, box.assignments) : [];
    const rows = evs.map((e) => `<tr><td>${fmtDT(e.t, true)}</td><td>${h(t(e.type))}</td><td>${h(pname(world.proc(e.proc)))}</td><td>${h(e.device)}</td></tr>`);
    return `${single ? '<h3>' : '<h4>'}${h(t('event_history'))}${single ? '</h3>' : '</h4>'}${table([t('time_jst'), t('action'), t('process'), t('device')], rows, t('no_events'))}`;
  }
  function pageOrder(app) {
    const order = world.order(Q.get('job'));
    if (!order) { app.innerHTML = `<p><a href="orders.html">&larr; ${h(t('back_orders'))}</a></p><p>${h(t('order_missing'))}</p>`; return; }
    const sum = FT.orderSummary(world, order);
    const items = world.items.filter((i) => i.order === order.job).map((item) => {
      const st = FT.itemStatus(world, item);
      const single = item.boxCount === 1 && st.boxes.length === 1;
      const open = st.boxes.filter((b) => b.assignment && b.assignment.end == null);
      const warns = st.warnings.length ? `<ul class="warnings">${st.warnings.map((x) => `<li>${h(warnText(x))}</li>`).join('')}</ul>` : '';
      let body;
      if (single) {
        const b = st.boxes[0];
        body = `<p>${open.length ? h(t('box_tag', { box: open[0].label, tag: open[0].assignment.tag })) : h(t('no_tag_assigned'))}</p>${warns}${timelineHtml(b)}${eventsHtml(b, true)}`;
      } else {
        body = `<p>${h(t('boxes_linked', { n: open.length, t: item.boxCount }))}</p>${warns}${st.boxes.map((b) => (b.assignment
          ? `<section class="box-section"><h3>${h(t('box_title', { l: b.label }))}${b.assignment.end != null ? ` <small>${h(t('released'))}</small>` : ''} ${statusBadge(b.code)}</h3>${timelineHtml(b)}${eventsHtml(b, false)}</section>`
          : `<section class="box-section"><h3>${h(t('box_not_linked'))}</h3><p><a href="tags.html">${h(t('link_a_tag'))}</a></p></section>`)).join('')}`;
      }
      return `<div class="card card-wide"><h2>${h(item.id)} - ${h(item.part)} &times; ${item.qty} ${statusBadge(st.code)}</h2>${body}</div>`;
    });
    app.innerHTML = `<p><a href="orders.html">&larr; ${h(t('back_orders'))}</a></p>
      <h1>${h(order.job)} ${badge(sum.code.toLowerCase(), sum.code === IN_PROGRESS ? t('order_progress', sum) : t(sum.code))}</h1>
      <dl class="info"><div><dt>${h(t('customer'))}</dt><dd>${h(order.customer)}</dd></div>
      <div><dt>${h(t('customer_order_number'))}</dt><dd>${h(order.custNo || '-')}</dd></div>
      <div><dt>${h(t('due_date'))}</dt><dd>${order.due}${order.due < world.today && sum.code !== COMPLETE ? ' ' + badge('overdue', t('overdue')) : ''}</dd></div>
      ${order.notes ? `<div><dt>${h(t('notes'))}</dt><dd>${h(order.notes)}</dd></div>` : ''}</dl>${items.join('')}`;
  }

  // ---------- new order ----------
  function itemCard(n) {
    const opts = PROCESSES.map((p) => `<option value="${p.id}">${h(t('process_opt', { p: pname(p), k: t(p.kind) }))}</option>`).join('');
    return `<div class="card item-card"><button type="button" class="remove-item">${h(t('remove'))}</button>
      <h2>${h(t('item'))} <span class="item-number">${n}</span></h2>
      <div><label>${h(t('part_name'))}</label><input name="part" required></div>
      <div><label>${h(t('quantity'))}</label><input name="qty" type="number" min="1" value="1" required></div>
      <div><label>${h(t('boxes_field'))}</label><input name="boxes" type="number" min="1" value="1" required></div>
      <div class="route"><label>${h(t('route'))}</label><div class="route-steps">${stepRow(opts)}</div>
      <button type="button" class="add-step button-secondary">${h(t('add_step'))}</button></div></div>`;
  }
  const stepRow = (opts) => `<div class="route-step"><select name="route"><option value="">${h(t('choose_process'))}</option>${opts}</select>
    <button type="button" class="remove-step">${h(t('remove'))}</button></div>`;
  function pageNewOrder(app) {
    app.innerHTML = `<h1>${h(t('new_order'))}</h1><form id="f" novalidate><div class="card">
      <div><label>${h(t('customer'))}</label><select name="customer">${CUSTOMERS.map((c) => `<option>${h(c)}</option>`).join('')}</select></div>
      <div><label>${h(t('customer_order_number'))}</label><input name="custNo"></div>
      <div><label>${h(t('due_date'))}</label><input type="date" name="due" required value="${FT.addDays(world.today, 14)}"></div>
      <div><label>${h(t('notes'))}</label><textarea name="notes" rows="3"></textarea></div></div>
      <div id="items">${itemCard(1)}</div><div id="err"></div>
      <div class="form-actions"><button type="button" id="add-item" class="button-secondary">${h(t('add_item'))}</button>
      <button type="submit" class="button">${h(t('create_order'))}</button></div></form>`;
    const f = $('#f'), opts = PROCESSES.map((p) => `<option value="${p.id}">${h(t('process_opt', { p: pname(p), k: t(p.kind) }))}</option>`).join('');
    const renumber = () => $$('.item-card:not(.is-removed) .item-number', f).forEach((s, i) => { s.textContent = i + 1; });
    $('#add-item').addEventListener('click', () => { $('#items').insertAdjacentHTML('beforeend', itemCard(0)); renumber(); });
    f.addEventListener('click', (e) => {
      if (e.target.closest('.remove-item')) { e.target.closest('.item-card').classList.add('is-removed'); renumber(); }
      if (e.target.closest('.add-step')) e.target.closest('.route').querySelector('.route-steps').insertAdjacentHTML('beforeend', stepRow(opts));
      const rm = e.target.closest('.remove-step');
      if (rm) { const row = rm.closest('.route-step'); if (row.parentElement.children.length > 1) row.remove(); else row.querySelector('select').value = ''; }
    });
    f.addEventListener('submit', (e) => {
      e.preventDefault();
      const errs = [], cards = $$('.item-card:not(.is-removed)', f);
      const items = cards.map((c) => ({
        part: $('[name=part]', c).value.trim(), qty: Number($('[name=qty]', c).value), boxCount: Number($('[name=boxes]', c).value),
        route: $$('[name=route]', c).map((s) => Number(s.value)).filter(Boolean),
      }));
      if (!f.due.value || !items.length || items.some((i) => !i.part || !(i.qty >= 1))) errs.push(t('err_required'));
      if (items.some((i) => !(i.boxCount >= 1))) errs.push(t('err_boxes'));
      if (items.some((i) => !i.route.length)) errs.push(t('err_route'));
      if (errs.length) { $('#err').innerHTML = `<ul class="errorlist">${errs.map((x) => `<li>${h(x)}</li>`).join('')}</ul>`; return; }
      const prefix = FT.jstStr(world.now).slice(2, 4) + FT.jstStr(world.now).slice(5, 7);
      const seq = Math.max(0, ...world.orders.filter((o) => o.job.startsWith(prefix + '-')).map((o) => Number(o.job.slice(5)))) + 1;
      const job = `${prefix}-${String(seq).padStart(3, '0')}`;
      push('orders', { job, customer: f.customer.value, custNo: f.custNo.value.trim(), due: f.due.value, notes: f.notes.value.trim() });
      items.forEach((i, n) => push('items', { id: `${job}-${String(n + 1).padStart(2, '0')}`, order: job, no: n + 1, part: i.part, qty: i.qty, boxCount: i.boxCount, route: i.route }));
      location.href = `orders.html?created=${job}`;
    });
  }

  // ---------- tags ----------
  function pageTags(app) {
    const q = (Q.get('q') || '').trim().toLowerCase();
    const open = FT.openOf(world).sort((a, b) => a.item.localeCompare(b.item) || a.start - b.start);
    const seen = {};
    open.forEach((a) => { seen[a.item] = a.nth = (seen[a.item] || 0) + 1; });
    const itemOf = (id) => world.items.find((i) => i.id === id);
    const shown = open.filter((a) => {
      const it = itemOf(a.item), o = world.order(it.order);
      return !q || [it.id, o.job, it.part, o.customer, a.box, a.tag].some((x) => x.toLowerCase().includes(q));
    });
    const free = world.items.filter((i) => open.filter((a) => a.item === i.id).length < i.boxCount);
    const rows = shown.map((a) => { const it = itemOf(a.item); return `<tr><td>${h(a.tag)}</td><td>${h(t('box_nth', { l: a.box, n: a.nth, t: it.boxCount }))}</td>
      <td>${orderLink(it.order, it.id)} ${h(it.part)}</td><td>${fmtDT(a.start)}</td><td>
      <button type="button" class="remove-step" data-release="${h(a.id)}">${h(t('release'))}</button>
      <details class="replace-tag"><summary>${h(t('replace_tag'))}</summary><form data-replace="${h(a.id)}">
      <input type="text" name="uid" list="tag-uids" autocomplete="off" required placeholder="${h(t('new_tag_uid'))}">
      <label class="check"><input type="checkbox" name="lost" checked> ${h(t('mark_lost'))}</label><button type="submit" class="button">${h(t('replace'))}</button></form></details></td></tr>`; });
    const hist = world.assignments.filter((a) => a.end != null).sort((a, b) => b.end - a.end).slice(0, 20).map((a) => { const it = itemOf(a.item);
      return `<tr><td>${h(a.tag)}</td><td>${h(a.box)}</td><td>${orderLink(it.order, it.id)} ${h(it.part)}</td><td>${fmtDT(a.start)}</td><td>${fmtDT(a.end)}</td></tr>`; });
    const boxes = [...new Set(world.assignments.map((a) => a.box))];
    app.innerHTML = `<h1>${h(t('tags_title'))}</h1><div id="flash">${flashHtml()}</div>
      <form id="assign" class="card"><div><label>${h(t('item'))}</label><select name="item"><option value="">${h(t('choose'))}</option>${free.map((i) => {
    const n = open.filter((a) => a.item === i.id).length;
    return `<option value="${h(i.id)}">${h(t('item_opt', { code: i.id, part: i.part, n, total: i.boxCount }))}</option>`; }).join('')}</select></div>
      <div><label>${h(t('box_label'))}</label><input name="box" list="box-labels" autocomplete="off"></div>
      <div><label>${h(t('tag_uid'))}</label><input name="uid" list="tag-uids" autocomplete="off"></div>
      <datalist id="box-labels">${boxes.map((b) => `<option value="${h(b)}">`).join('')}</datalist>
      <datalist id="tag-uids">${tagList().map((u) => `<option value="${h(u)}">`).join('')}</datalist>
      <button type="submit" class="button">${h(t('link_tag'))}</button></form>
      <h2>${h(t('in_use'))}</h2><form method="get" class="filters"><div><label for="q">${h(t('search'))}</label>
      <input type="search" id="q" name="q" value="${h(Q.get('q') || '')}" placeholder="${h(t('search_ph'))}"></div><button type="submit" class="button">${h(t('search'))}</button></form>
      ${table([t('tag'), t('box'), t('item'), t('since_jst'), ''], rows, t('no_tags_in_use'))}
      <h2>${h(t('history'))}</h2>${table([t('tag'), t('box'), t('item'), t('from_jst'), t('to_jst')], hist, t('no_history'))}`;
    const done = (msg) => { flash = msg; refresh(); boot(); };
    $('#assign').addEventListener('submit', (e) => {
      e.preventDefault();
      const f = e.target;
      if (!f.item.value) return done({ kind: 'error', text: t('pick_item') });
      const r = FT.assignTag(world, f.item.value, f.box.value, f.uid.value);
      if (r.error) return done({ kind: 'error', text: errText(r.error) });
      push('assignments', r.assignment);
      done({ kind: 'success', text: t('m_linked', { tag: r.assignment.tag, box: r.assignment.box, item: r.assignment.item }) });
    });
    $$('[data-release]', app).forEach((b) => b.addEventListener('click', () => {
      const a = world.assignments.find((x) => x.id === b.dataset.release);
      state.ended = { ...(state.ended || {}), [a.id]: Date.now() }; save(); refresh();
      done({ kind: 'success', text: t('m_released', { tag: a.tag, box: a.box }) });
    }));
    $$('[data-replace]', app).forEach((f) => f.addEventListener('submit', (e) => {
      e.preventDefault();
      const old = world.assignments.find((x) => x.id === f.dataset.replace);
      const r = FT.replaceTag(world, old, f.uid.value, f.lost.checked);
      if (r.error) return done({ kind: 'error', text: errText(r.error) });
      state.ended = { ...(state.ended || {}), [r.endId]: r.assignment.start };
      if (r.lostTag) state.inactive = [...(state.inactive || []), r.lostTag];
      push('assignments', r.assignment);
      done({ kind: 'success', text: t('m_replaced', { old: old.tag, new: r.assignment.tag, box: old.box }) });
    }));
  }

  // ---------- simulate ----------
  const sim = { device: DEVICES[0], tag: '' };
  function recordEvent(device, tag, proc, type) {
    const now = Date.now();
    push('events', { id: Math.max(0, ...world.events.map((x) => x.id)) + 1, t: now, tag, type, proc, device, corrects: null });
    const a = FT.resolveTag(world, tag, now);
    if (!a) return { kind: 'warning', text: t('m_nomatch', { uid: tag }) };
    const item = world.items.find((i) => i.id === a.item);
    return { kind: 'success', text: t('m_saved', { box: a.box, item: item.id, status: t(FT.itemStatus(world, item).code) }) };
  }
  function pageSimulate(app) {
    const open = FT.openOf(world).sort((a, b) => a.tag.localeCompare(b.tag));
    const itemOf = (id) => world.items.find((i) => i.id === id);
    let step = '';
    if (sim.tag) {
      const a = FT.resolveTag(world, sim.tag, world.now);
      if (!a) step = `<p>${h(t('not_linked_tag', { uid: sim.tag }))}</p>`;
      else {
        const it = itemOf(a.item), box = FT.itemStatus(world, it).boxes.find((b) => b.assignment && b.assignment.box === a.box), next = FT.nextAction(box);
        step = `<p>${h(t('tag_info', { uid: sim.tag, item: it.id, box: a.box, part: it.part }))} ${statusBadge(box.code)}</p>${next
          ? `<form id="confirm"><label for="next-action">${h(t('what_happened'))}</label>
            <select id="next-action"><option selected>${h(t(next.type) + ' - ' + pname(next.proc))}</option></select> <button type="submit" class="button">${h(t('confirm'))}</button></form>`
          : `<p>${h(t('box_finished'))}</p>`}`;
      }
      step = `<div class="card">${step}</div>`;
    }
    const devOpts = DEVICES.map((d) => `<option${d === sim.device ? ' selected' : ''}>${h(d)}</option>`).join('');
    const actions = PROCESSES.flatMap((p) => ACTIONS[p.kind].map((ty) => `<option value="${p.id}:${ty}">${h(pname(p) + ' - ' + t(ty))}</option>`)).join('');
    const last = [...world.events].sort((a, b) => b.t - a.t || b.id - a.id).slice(0, 10).map((e) =>
      `<tr><td>${fmtDT(e.t, true)}</td><td>${h(e.tag)}</td><td>${h(pname(world.proc(e.proc)))}</td><td>${h(t(e.type))}</td><td>${h(e.device)}</td></tr>`);
    const picks = `<datalist id="tag-picks">${open.map((a) => `<option value="${h(a.tag)}">${h(`${a.tag} - ${a.item} - ${t('box_n', { l: a.box })} - ${itemOf(a.item).part}`)}</option>`).join('')}</datalist>`;
    app.innerHTML = `<h1>${h(t('sim_title'))}</h1><div id="flash">${flashHtml()}</div>
      <form id="pick" class="card"><div><label>${h(t('device'))}</label><select name="device">${devOpts}</select></div>
      <div><label>${h(t('tag'))}</label><input name="tag" list="tag-picks" autocomplete="off" placeholder="${h(t('search_or_pick'))}" value="${h(sim.tag)}"></div>${picks}</form>${step}
      <details class="card"><summary>${h(t('other_action'))}</summary><form id="adv"><div><label>${h(t('tag_uid'))}</label><input name="tag" list="tag-picks" autocomplete="off" required value="${h(sim.tag)}"></div>
      <div><label>${h(t('device'))}</label><select name="device">${devOpts}</select></div>
      <div><label>${h(t('what_happened'))}</label><select name="action">${actions}</select></div><button type="submit" class="button">${h(t('record_event'))}</button></form></details>
      <h2>${h(t('last_10'))}</h2>${table([t('time_jst'), t('tag'), t('process'), t('action'), t('device')], last, t('no_events'))}`;
    const redraw = () => { refresh(); pageSimulate(app); };
    $('#pick').addEventListener('change', (e) => { sim.device = e.currentTarget.device.value; sim.tag = e.currentTarget.tag.value.trim().toUpperCase(); flash = null; redraw(); });
    $('#pick').addEventListener('submit', (e) => e.preventDefault());
    const send = (device, tag, proc, type) => { flash = recordEvent(device, tag, proc, type); sim.device = device; sim.tag = ''; redraw(); };
    const cf = $('#confirm');
    if (cf) cf.addEventListener('submit', (e) => {
      e.preventDefault();
      const a = FT.resolveTag(world, sim.tag, world.now), it = itemOf(a.item);
      const next = FT.nextAction(FT.itemStatus(world, it).boxes.find((b) => b.assignment && b.assignment.box === a.box));
      send(sim.device, sim.tag, next.proc.id, next.type);
    });
    $('#adv').addEventListener('submit', (e) => {
      e.preventDefault();
      const f = e.target, [pid, ty] = f.action.value.split(':');
      send(f.device.value, f.tag.value.trim().toUpperCase(), Number(pid), ty);
    });
  }

  // ---------- process board ----------
  function pageBoard(app) {
    const flags = (e) => (e.late ? badge('overdue', t('overdue')) : `<small>${e.due}</small>`) + (e.stagnant ? ' ' + badge('stagnant', t('stagnant')) : '');
    const list = (rows, time) => (rows.length ? `<ul class="process-boxes">${rows.map((e) =>
      `<li>${orderLink(e.order.job, itemBox(e))} ${time(e) != null ? `<small>${fmtDur(time(e))}</small>` : ''} ${flags(e)}</li>`).join('')}</ul>` : '');
    const card = (c) => `<section class="card process-card${c.queue ? ' queue-building' : ''}"><h3>${h(pname(c.proc))}
      ${c.outsourced && c.proc.vendor ? `<small>${h(c.proc.vendor)}</small>` : ''} ${c.queue ? badge('queue', t('queue_building')) : ''}</h3>
      <p class="process-counts"><span><strong>${c.running.length}</strong> ${h(t('running'))}</span><span><strong>${c.waiting.length}</strong> ${h(t('waiting'))}</span></p>
      ${list(c.running, (e) => e.runningFor)}${c.waiting.length ? `<h4>${h(t('waiting'))}</h4>${list(c.waiting, (e) => e.idle)}` : ''}</section>`;
    const cards = FT.processBoard(world);
    const grid = (rows, empty) => `<div class="board-grid">${rows.length ? rows.map(card).join('') : `<p>${h(empty)}</p>`}</div>`;
    app.innerHTML = `<h1>${h(t('board_title'))}</h1><h2>${h(t('in_house'))}</h2>${grid(cards.filter((c) => !c.outsourced), t('no_in_house'))}
      <h2>${h(t('outsourced'))}</h2>${grid(cards.filter((c) => c.outsourced), t('no_outsourced'))}`;
  }

  // ---------- priorities ----------
  function pagePriorities(app) {
    const all = FT.prioritiesRows(world), tier = FT.TIERS.includes(Q.get('tier')) ? Q.get('tier') : '';
    const rows = all.filter((r) => !tier || r.tier === tier).map((r) => `<tr><td>${orderLink(r.order.job, r.item.id + (r.multi ? ' - ' + t('box_n', { l: r.box.label }) : ''))}</td>
      <td>${h(r.item.part)}</td><td>${h(r.order.customer)}</td><td>${r.order.due}</td><td>${r.daysLeft}</td><td>${r.slack}</td><td>${h(pname(r.proc))}</td>
      <td>${badge('risk-' + r.tier.toLowerCase(), t(r.tier))}</td><td><ul class="reasons">${r.reasons.map((x) => `<li>${h(reasonText(x))}</li>`).join('')}</ul></td></tr>`);
    const links = FT.TIERS.map((c) => `<a href="?tier=${c}" class="badge badge-risk-${c.toLowerCase()}${tier === c ? ' active' : ''}">${h(t(c))} (${all.filter((r) => r.tier === c).length})</a>`).join('');
    app.innerHTML = `<h1>${h(t('prio_title'))}</h1><nav class="tier-filters"><a href="priorities.html"${tier ? '' : ' class="active"'}>${h(t('all'))} (${all.length})</a>${links}</nav>
      ${rows.length ? table([t('item'), t('part'), t('customer'), t('due_date'), t('days_left'), t('slack'), t('current_process'), t('risk'), t('reasons')], rows, '') : `<p>${h(t('nothing_attention'))}</p>`}`;
  }

  // ---------- start ----------
  const PAGES = { home: pageHome, boxes: pageBoxes, orders: pageOrders, order: pageOrder, new: pageNewOrder, tags: pageTags, simulate: pageSimulate, board: pageBoard, priorities: pagePriorities };
  function boot() {
    refresh();
    shell();
    const page = document.body.dataset.page;
    document.title = `${t(`nav_${{ home: 'home', order: 'orders', new: 'new', simulate: 'sim', priorities: 'prio' }[page] || page}`)} - ${t('brand')}`;
    PAGES[page]($('#app'));
  }
  document.addEventListener('DOMContentLoaded', () => {
    I18N.lang = Q.get('lang') || store.get(LANG_KEY, 'ja');
    store.set(LANG_KEY, I18N.lang);
    boot();
  });
})();
