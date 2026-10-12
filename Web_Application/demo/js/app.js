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

  // ---------- customers (orders/services.py) ----------
  const normName = (v) => String(v || '').split(/[\s\u3000]+/).filter(Boolean).join(' ');
  const nameKey = (v) => normName(v).normalize('NFKC').toLowerCase();
  const findCustomer = (name) => customerNames().find((c) => nameKey(c) === nameKey(name));
  const customerSelect = (current) => `<select name="customer"><option value="">${h(t('choose'))}</option>${customerNames().map((c) => `<option${c === current ? ' selected' : ''}>${h(c)}</option>`).join('')}</select>`;
  const newCustomerField = () => `<div><label>${h(t('new_customer'))}</label><input name="newcust" maxlength="200" autocomplete="off"></div>`;
  // The typed name wins over the dropdown; an existing match is reused
  const chosenCustomer = (f) => findCustomer(normName(f.newcust.value)) || normName(f.newcust.value) || f.customer.value;
  const ensureCustomer = (name) => { if (!findCustomer(name)) push('customers', { name, contact: '' }); };

  // ---------- state ----------
  let state = store.get(KEY, {}) || {};
  let seed, world, flash = null;
  const save = () => store.set(KEY, state);
  const refresh = () => { const now = Date.now(); seed = seed || buildSeed(now); world = FT.makeWorld(seed, state, now); };
  const push = (k, v) => { state[k] = [...(state[k] || []), v]; save(); refresh(); };
  const customerNames = () => [...CUSTOMERS, ...(state.customers || []).map((c) => c.name)];
  const tagList = () => world.tags.filter((u) => !world.inactive.has(u));

  // ---------- shell ----------
  const NAV = [['index', 'nav_home'], ['orders', 'nav_orders'], ['customers', 'nav_customers'], ['new-order', 'nav_new'],
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
    app.innerHTML = `<h1>${h(t('home_title'))}</h1><div class="counters">
      ${card('in_progress', 'boxes.html?status=in_progress', c.in_progress, t('IN_PROGRESS'))}
      ${card('waiting', 'boxes.html?status=waiting', c.waiting, t('WAITING'))}
      ${card('at_vendor', 'boxes.html?status=at_vendor', c.at_vendor, t('AT_VENDOR'))}
      ${card('not_started', 'boxes.html?status=not_started', c.not_started, t('NOT_STARTED'))}
      ${card('stagnant', 'boxes.html?stagnant=1', c.stagnant, t('stagnant'))}
      ${card('overdue', 'orders.html?late=1', c.overdue, t('overdue_orders'))}
      ${card('vendor' + (c.vendor_overdue ? ' counter-overdue counter-late' : ''), 'boxes.html?status=at_vendor&vendor_late=1', c.vendor_overdue, t('vendor_overdue'))}
    </div>
    <h2>${h(t('board_title'))}</h2>${boardHtml()}`;
  }

  // ---------- boxes ----------
  const STATUS_PARAMS = { in_progress: IN_PROGRESS, waiting: WAITING, at_vendor: AT_VENDOR, not_started: NOT_STARTED };
  function pageBoxes(app) {
    const status = STATUS_PARAMS[Q.get('status')] || null, stagnant = Q.get('stagnant') === '1', vendorLate = Q.get('vendor_late') === '1';
    const sort = Object.keys(FT.BOX_SORTS).includes(Q.get('sort')) ? Q.get('sort') : 'due';
    const title = [vendorLate ? t('vendor_overdue') : status && t(status), stagnant && t('stagnant')].filter(Boolean).join(', ') || t('active_boxes');
    const rows = FT.boxesPage(world, { status, stagnant, vendorLate, sort }).map((e) => `<tr>
      <td>${orderLink(e.order.job, e.item.id)}</td>
      <td>${e.boxLabel ? h(e.boxLabel) : `${h(t('not_linked_yet'))} <a href="tags.html">${h(t('link_a_tag'))}</a>`}</td>
      <td>${h(e.item.part)}</td><td>${h(e.order.customer)}</td><td>${h(pname(e.proc))}</td><td>${statusBadge(e.code)}</td><td>${e.due}</td><td>${fmtDur(e.idle)}</td>
      <td>${e.late ? badge('overdue', t('overdue')) : ''} ${e.stagnant ? badge('stagnant', t('stagnant')) : ''} ${e.lateDays ? badge('overdue', t('vendor_overdue')) + ' ' + lateBadge(e.lateDays) : ''}</td></tr>`);
    const keep = [['status', status ? Q.get('status') : ''], ['stagnant', stagnant ? '1' : ''], ['vendor_late', vendorLate ? '1' : '']]
      .filter(([, v]) => v).map(([k, v]) => `<input type="hidden" name="${k}" value="${v}">`).join('');
    const opt = (v, text) => `<option value="${v}"${v === sort ? ' selected' : ''}>${h(text)}</option>`;
    app.innerHTML = `<p><a href="index.html">&larr; ${h(t('back_home'))}</a></p>
      <h1>${h(title)}</h1>
      <form method="get" class="filters">${keep}<div><label for="sort">${h(t('sort_by'))}</label><select id="sort" name="sort" onchange="this.form.submit()">${
  opt('process', t('current_process'))}${opt('due', t('due_date'))}${opt('customer', t('customer'))}${opt('waiting', t('idle_for'))}</select></div></form>
      ${rows.length ? table([t('item'), t('box'), t('part'), t('customer'), t('current_process'), t('status'), t('due_date'), t('idle_for'), ''], rows, '') : `<p>${h(t('no_match'))}</p>`}`;
  }

  // ---------- customers ----------
  function pageCustomers(app) {
    const q = (Q.get('q') || '').trim().toLowerCase();
    const list = [...CUSTOMERS.map((name) => ({ name, contact: '' })), ...(state.customers || [])];
    const rows = list.filter((c) => !q || c.name.toLowerCase().includes(q)).sort((a, b) => a.name.localeCompare(b.name))
      .map((c) => `<tr><td>${h(c.name)}</td><td>${h(c.contact || '-')}</td><td>${world.orders.filter((o) => o.customer === c.name).length}</td></tr>`);
    app.innerHTML = `<h1>${h(t('nav_customers'))}</h1><div id="flash">${flashHtml()}</div>
      <form id="addcust" class="card"><div><label>${h(t('cust_name'))}</label><input name="name" maxlength="200" required></div>
      <div><label>${h(t('cust_contact'))}</label><input name="contact" maxlength="200"></div><button type="submit" class="button">${h(t('add_customer'))}</button></form>
      <form method="get" class="filters"><div><label for="q">${h(t('search'))}</label><input type="search" id="q" name="q" value="${h(Q.get('q') || '')}"></div>
      <button type="submit" class="button">${h(t('search'))}</button></form>
      ${table([t('cust_name'), t('cust_contact'), t('num_orders')], rows, t('no_customers_match'))}`;
    $('#addcust').addEventListener('submit', (e) => {
      e.preventDefault();
      const f = e.target, name = normName(f.name.value);
      if (!name) flash = { kind: 'error', text: t('err_required') };
      else if (findCustomer(name)) flash = { kind: 'error', text: t('err_dup_customer') };
      else { push('customers', { name, contact: f.contact.value.trim() }); flash = { kind: 'success', text: t('customer_added', { name }) }; }
      boot();
    });
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
  const FIELD_LABELS = { customer: 'customer', customer_order_number: 'customer_order_number', due_date: 'due_date', notes: 'notes', part_name: 'part_name',
    quantity: 'quantity', box_count: 'boxes_field', drawing: 'drawing', route: 'route', item_added: 'item_added', item_deleted: 'item_deleted' };
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
      return `<div class="card card-wide"><h2>${h(item.id)} - ${h(item.part)} &times; ${item.qty} ${statusBadge(st.code)}</h2>${item.drawing ? `<p>${h(t('drawing'))}: ${h(item.drawing)}</p>` : ''}${body}</div>`;
    });
    const changes = world.changes.filter((c) => c.job === order.job).reverse().map((c) => `<tr><td>${fmtDT(c.t)}</td><td>${h(t('demo_user'))}</td>
      <td>${h((c.item ? c.item + ' ' : '') + t(FIELD_LABELS[c.field] || c.field))}</td><td>${h(c.old || '-')}</td><td>${h(c.new || '-')}</td></tr>`);
    app.innerHTML = `${Q.get('updated') ? `<ul class="messages"><li class="message success">${h(t('order_updated', { job: order.job }))}</li></ul>` : ''}
      <p><a href="orders.html">&larr; ${h(t('back_orders'))}</a></p>
      <h1>${h(order.job)} ${badge(sum.code.toLowerCase(), sum.code === IN_PROGRESS ? t('order_progress', sum) : t(sum.code))}
      <a class="button-secondary" href="edit-order.html?job=${encodeURIComponent(order.job)}">${h(t('edit'))}</a></h1>
      <dl class="info"><div><dt>${h(t('customer'))}</dt><dd>${h(order.customer)}</dd></div>
      <div><dt>${h(t('customer_order_number'))}</dt><dd>${h(order.custNo || '-')}</dd></div>
      <div><dt>${h(t('due_date'))}</dt><dd>${order.due}${order.due < world.today && sum.code !== COMPLETE ? ' ' + badge('overdue', t('overdue')) : ''}</dd></div>
      ${order.notes ? `<div><dt>${h(t('notes'))}</dt><dd>${h(order.notes)}</dd></div>` : ''}</dl>${items.join('')}
      <h2>${h(t('change_history'))}</h2>${table([t('time_jst'), t('changed_by'), t('what'), t('before'), t('after')], changes, t('no_changes'))}`;
  }

  // ---------- new order ----------
  const procOpts = () => PROCESSES.map((p) => `<option value="${p.id}">${h(t('process_opt', { p: pname(p), k: t(p.kind) }))}</option>`).join('');
  // New-order card, or (with an item) the edit card: prefilled, route locked after a scan, no Remove once tags/scans exist
  function itemCard(n, it, edit) {
    const locked = it && FT.itemHasEvents(world, it), keep = it && (locked || world.assignments.some((a) => a.item === it.id));
    const min = it ? Math.max(1, FT.linkedBoxes(world, it)) : 1;
    const steps = (it ? it.route : []).map((id) => stepRow(procOpts(), id)).join('') || stepRow(procOpts());
    return `<div class="card item-card"${it ? ` data-id="${h(it.id)}"` : ''}${locked ? ' data-locked="1"' : ''}>
      ${keep ? `<p class="note">${h(t('cannot_remove'))}</p>` : `<button type="button" class="remove-item">${h(t('remove'))}</button>`}
      <h2>${h(t('item'))} <span class="item-number">${n}</span>${it ? ` <small>${h(it.id)}</small>` : ''}</h2>
      <div><label>${h(t('part_name'))}</label><input name="part" required value="${h(it ? it.part : '')}"></div>
      <div><label>${h(t('quantity'))}</label><input name="qty" type="number" min="1" value="${it ? it.qty : 1}" required></div>
      <div><label>${h(t('boxes_field'))}</label><input name="boxes" type="number" min="${min}" value="${it ? it.boxCount : 1}" required></div>
      ${edit ? `<div><label>${h(t('drawing'))}</label>${it && it.drawing ? `<p class="note">${h(t('current_file', { name: it.drawing }))}</p>` : ''}<input name="drawing" type="file"></div>` : ''}
      ${locked ? `<div class="route"><label>${h(t('route'))}</label><ol class="route-readonly">${it.route.map((id) => `<li>${h(pname(world.proc(id)))}</li>`).join('')}</ol>
        <p class="note">${h(t('route_locked'))}</p></div>`
    : `<div class="route"><label>${h(t('route'))}</label><div class="route-steps">${steps}</div>
      <button type="button" class="add-step button-secondary">${h(t('add_step'))}</button></div>`}</div>`;
  }
  const stepRow = (opts, sel) => `<div class="route-step"><select name="route"><option value="">${h(t('choose_process'))}</option>${
    sel ? opts.replace(`value="${sel}"`, `value="${sel}" selected`) : opts}</select>
    <button type="button" class="remove-step">${h(t('remove'))}</button></div>`;
  function pageNewOrder(app) {
    app.innerHTML = `<h1>${h(t('new_order'))}</h1><form id="f" novalidate><div class="card">
      <div><label>${h(t('customer'))}</label>${customerSelect('')}</div>${newCustomerField()}
      <div><label>${h(t('customer_order_number'))}</label><input name="custNo"></div>
      <div><label>${h(t('due_date'))}</label><input type="date" name="due" required value="${FT.addDays(world.today, 14)}"></div>
      <div><label>${h(t('notes'))}</label><textarea name="notes" rows="3"></textarea></div></div>
      <div id="items">${itemCard(1)}</div><div id="err"></div>
      <div class="form-actions"><button type="button" id="add-item" class="button-secondary">${h(t('add_item'))}</button>
      <button type="submit" class="button">${h(t('create_order'))}</button></div></form>`;
    const f = $('#f'), opts = procOpts();
    wireItemCards(f, itemCard);
    f.addEventListener('submit', (e) => {
      e.preventDefault();
      const errs = [], cards = $$('.item-card:not(.is-removed)', f);
      const items = cards.map((c) => ({
        part: $('[name=part]', c).value.trim(), qty: Number($('[name=qty]', c).value), boxCount: Number($('[name=boxes]', c).value),
        route: $$('[name=route]', c).map((s) => Number(s.value)).filter(Boolean),
      }));
      const customer = chosenCustomer(f);
      if (!customer || !f.due.value || !items.length || items.some((i) => !i.part || !(i.qty >= 1))) errs.push(t('err_required'));
      if (items.some((i) => !(i.boxCount >= 1))) errs.push(t('err_boxes'));
      if (items.some((i) => !i.route.length)) errs.push(t('err_route'));
      if (errs.length) { $('#err').innerHTML = `<ul class="errorlist">${errs.map((x) => `<li>${h(x)}</li>`).join('')}</ul>`; return; }
      const prefix = FT.jstStr(world.now).slice(2, 4) + FT.jstStr(world.now).slice(5, 7);
      const seq = Math.max(0, ...world.orders.filter((o) => o.job.startsWith(prefix + '-')).map((o) => Number(o.job.slice(5)))) + 1;
      const job = `${prefix}-${String(seq).padStart(3, '0')}`;
      ensureCustomer(customer);
      push('orders', { job, customer, custNo: f.custNo.value.trim(), due: f.due.value, notes: f.notes.value.trim() });
      items.forEach((i, n) => push('items', { id: `${job}-${String(n + 1).padStart(2, '0')}`, order: job, no: n + 1, part: i.part, qty: i.qty, boxCount: i.boxCount, route: i.route }));
      location.href = `orders.html?created=${job}`;
    });
  }

  // ---------- edit order ----------
  const FIELD_NAMES = { customer: 'customer', custNo: 'customer_order_number', due: 'due_date', notes: 'notes' };
  const routeNames = (ids) => ids.map((id) => world.proc(id).name).join(' > ');
  function pageEdit(app) {
    const order = world.order(Q.get('job'));
    if (!order) { app.innerHTML = `<p><a href="orders.html">&larr; ${h(t('back_orders'))}</a></p><p>${h(t('order_missing'))}</p>`; return; }
    const its = world.items.filter((i) => i.order === order.job);
    app.innerHTML = `<p><a href="order.html?job=${encodeURIComponent(order.job)}">&larr; ${h(order.job)}</a></p><h1>${h(t('edit_order', { job: order.job }))}</h1>
      <form id="f" novalidate><div class="card"><div><label>${h(t('job_number'))}</label><input value="${h(order.job)}" disabled></div>
      <div><label>${h(t('customer'))}</label>${customerSelect(order.customer)}</div>${newCustomerField()}
      <div><label>${h(t('customer_order_number'))}</label><input name="custNo" value="${h(order.custNo)}"></div>
      <div><label>${h(t('due_date'))}</label><input type="date" name="due" required value="${order.due}"></div>
      <div><label>${h(t('notes'))}</label><textarea name="notes" rows="3">${h(order.notes)}</textarea></div></div>
      <div id="items">${its.map((it, n) => itemCard(n + 1, it, true)).join('')}</div><div id="err"></div>
      <div class="form-actions"><button type="button" id="add-item" class="button-secondary">${h(t('add_item'))}</button>
      <button type="submit" class="button">${h(t('save_changes'))}</button></div></form>`;
    wireItemCards($('#f'), (n) => itemCard(n, null, true));
    $('#f').addEventListener('submit', (e) => {
      e.preventDefault();
      const f = e.target, errs = [], kept = [], added = [], deleted = [];
      const o = { customer: chosenCustomer(f), custNo: f.custNo.value.trim(), due: f.due.value, notes: f.notes.value.trim() };
      if (!o.customer) errs.push(t('err_required'));
      $$('.item-card', f).forEach((c) => {
        const it = c.dataset.id && world.items.find((x) => x.id === c.dataset.id);
        if (c.classList.contains('is-removed')) { if (it) deleted.push(it); return; }
        const file = $('[name=drawing]', c).files[0];
        const v = { part: $('[name=part]', c).value.trim(), qty: Number($('[name=qty]', c).value), boxCount: Number($('[name=boxes]', c).value),
          drawing: file ? file.name : (it && it.drawing) || '', route: c.dataset.locked ? it.route : $$('[name=route]', c).map((x) => Number(x.value)).filter(Boolean) };
        if (!v.part || !(v.qty >= 1)) errs.push(t('err_required'));
        if (!v.route.length) errs.push(t('err_route'));
        if (it && v.boxCount < FT.linkedBoxes(world, it)) errs.push(t('err_box_min', { n: FT.linkedBoxes(world, it) }));
        else if (!(v.boxCount >= 1)) errs.push(t('err_boxes'));
        (it ? kept : added).push({ it, v });
      });
      if (!o.due) errs.push(t('err_required'));
      if (!kept.length && !added.length) errs.push(t('err_one_item'));
      if (deleted.some((it) => FT.itemHasEvents(world, it) || world.assignments.some((a) => a.item === it.id))) errs.push(t('cannot_remove'));
      if (o.custNo && world.orders.some((x) => x.job !== order.job && x.customer === o.customer && x.custNo === o.custNo)) errs.push(t('err_dup_po'));
      if (errs.length) { $('#err').innerHTML = `<ul class="errorlist">${[...new Set(errs)].map((x) => `<li>${h(x)}</li>`).join('')}</ul>`; return; }
      // Append one record per changed field (the log is never edited or deleted)
      const when = Date.now(), log = [];
      const add = (item, field, old, now) => { if (String(old ?? '') !== String(now ?? '')) log.push({ t: when, job: order.job, item, field, old: String(old ?? ''), new: String(now ?? ''), by: 'demo' }); };
      ensureCustomer(o.customer);
      Object.keys(FIELD_NAMES).forEach((k) => add(null, FIELD_NAMES[k], order[k], o[k]));
      kept.forEach(({ it, v }) => {
        add(it.id, 'part_name', it.part, v.part); add(it.id, 'quantity', it.qty, v.qty); add(it.id, 'box_count', it.boxCount, v.boxCount);
        add(it.id, 'drawing', it.drawing || '', v.drawing); add(it.id, 'route', routeNames(it.route), routeNames(v.route));
      });
      let no = Math.max(0, ...its.map((i) => i.no), ...(state.deleted || []).filter((d) => d.startsWith(order.job + '-')).map((d) => Number(d.slice(-2))));
      const fresh = added.map(({ v }) => ({ id: `${order.job}-${String(++no).padStart(2, '0')}`, order: order.job, no, ...v }));
      fresh.forEach((i) => add(i.id, 'item_added', '', `${i.id} ${i.part}`));
      deleted.forEach((i) => add(i.id, 'item_deleted', `${i.id} ${i.part}`, ''));
      state.orderEdits = { ...state.orderEdits, [order.job]: o };
      state.itemEdits = { ...state.itemEdits, ...Object.fromEntries(kept.map(({ it, v }) => [it.id, v])) };
      state.deleted = [...(state.deleted || []), ...deleted.map((i) => i.id)];
      state.items = [...(state.items || []), ...fresh];
      state.changes = [...(state.changes || []), ...log];
      save();
      location.href = `order.html?job=${encodeURIComponent(order.job)}&updated=1`;
    });
  }

  // Remove / add-step / add-item buttons shared by the new-order and edit forms
  function wireItemCards(f, make) {
    const renumber = () => $$('.item-card:not(.is-removed) .item-number', f).forEach((s, i) => { s.textContent = i + 1; });
    $('#add-item').addEventListener('click', () => { $('#items').insertAdjacentHTML('beforeend', make(0)); renumber(); });
    f.addEventListener('click', (e) => {
      if (e.target.closest('.remove-item')) { e.target.closest('.item-card').classList.add('is-removed'); renumber(); }
      if (e.target.closest('.add-step')) e.target.closest('.route').querySelector('.route-steps').insertAdjacentHTML('beforeend', stepRow(procOpts()));
      const rm = e.target.closest('.remove-step');
      if (rm) { const row = rm.closest('.route-step'); if (row.parentElement.children.length > 1) row.remove(); else row.querySelector('select').value = ''; }
    });
  }

  // ---------- tags ----------
  function pageTags(app) {
    const q = (Q.get('q') || '').trim().toLowerCase();
    const open = FT.openOf(world).sort((a, b) => a.item.localeCompare(b.item) || a.start - b.start);
    const seen = {};
    open.forEach((a) => { seen[a.item] = a.nth = (seen[a.item] || 0) + 1; });
    const itemOf = (id) => world.items.find((i) => i.id === id);
    const shown = !q ? [] : open.filter((a) => {
      const it = itemOf(a.item), o = world.order(it.order);
      return [it.id, o.job, it.part, o.customer, a.box, a.tag].some((x) => x.toLowerCase().includes(q));
    });
    const free = world.items.filter((i) => open.filter((a) => a.item === i.id).length < i.boxCount);
    const rows = shown.map((a) => { const it = itemOf(a.item); return `<tr><td>${h(a.tag)}</td><td>${h(t('box_nth', { l: a.box, n: a.nth, t: it.boxCount }))}</td>
      <td>${orderLink(it.order, it.id)} ${h(it.part)}</td><td>${fmtDT(a.start)}</td><td>
      <button type="button" class="remove-step" data-release="${h(a.id)}">${h(t('release'))}</button>
      <details class="replace-tag"><summary>${h(t('replace_tag'))}</summary><form data-replace="${h(a.id)}">
      <input type="text" name="uid" list="tag-uids" autocomplete="off" required placeholder="${h(t('new_tag_uid'))}">
      <label class="check"><input type="checkbox" name="lost" checked> ${h(t('mark_lost'))}</label><button type="submit" class="button">${h(t('replace'))}</button></form></details></td></tr>`; });
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
      <input type="search" id="q" name="q" value="${h(Q.get('q') || '')}" placeholder="${h(t('search_ph'))}"></div><button type="submit" class="button">${h(t('search'))}</button> <a href="tag-history.html">${h(t('tag_history'))}</a></form>
      <p>${h(t('tags_in_use_n', { n: open.length }))}</p>
      ${!q ? '' : rows.length ? table([t('tag'), t('box'), t('item'), t('since_jst'), ''], rows, '') : `<p>${h(t('no_tags_match'))}</p>`}`;
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
      if (!a) step = `<p>${h(t('not_linked_tag', { uid: sim.tag }))} <a href="tags.html">${h(t('link_a_tag'))}</a></p>`;
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
    const last = [...world.events].sort((a, b) => b.t - a.t || b.id - a.id).slice(0, 10).map((e) =>
      `<tr><td>${fmtDT(e.t, true)}</td><td>${h(e.tag)}</td><td>${h(pname(world.proc(e.proc)))}</td><td>${h(t(e.type))}</td><td>${h(e.device)}</td></tr>`);
    const picks = `<datalist id="tag-picks">${open.map((a) => `<option value="${h(a.tag)}">${h(`${a.tag} - ${a.item} - ${t('box_n', { l: a.box })} - ${itemOf(a.item).part}`)}</option>`).join('')}</datalist>`;
    app.innerHTML = `<h1>${h(t('sim_title'))}</h1><div id="flash">${flashHtml()}</div>
      <form id="pick" class="card"><div><label>${h(t('device'))}</label><select name="device">${devOpts}</select></div>
      <div><label>${h(t('tag'))}</label><input name="tag" list="tag-picks" autocomplete="off" placeholder="${h(t('search_or_pick'))}" value="${h(sim.tag)}"></div>${picks}</form>${step}
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
  }

  // ---------- tag history ----------
  function pageTagHistory(app) {
    const p = { q: Q.get('q') || '', range: Q.get('range') || '3m', from: Q.get('from') || '', to: Q.get('to') || '' };
    const d = FT.tagHistory(world, p), r = d.range, custom = r.key === 'custom';
    const itemOf = (id) => world.items.find((i) => i.id === id);
    const rows = d.rows.map((a) => { const it = itemOf(a.item);
      return `<tr><td>${h(a.tag)}</td><td>${h(a.box)}</td><td>${orderLink(it.order, it.id)} ${h(it.part)}</td><td>${fmtDT(a.start)}</td><td>${a.end == null ? h(t('in_use')) : fmtDT(a.end)}</td></tr>`; });
    const opt = (v, text) => `<option value="${v}"${v === r.key ? ' selected' : ''}>${h(text)}</option>`;
    app.innerHTML = `<p><a href="tags.html">&larr; ${h(t('back_tags'))}</a></p><h1>${h(t('tag_history'))}</h1>
      <form method="get" class="filters"><div><label for="q">${h(t('search'))}</label><input type="search" id="q" name="q" value="${h(p.q)}" placeholder="${h(t('tag_or_box'))}"></div>
      <div><label for="range">${h(t('period'))}</label><select id="range" name="range" onchange="this.form.from.disabled = this.form.to.disabled = this.value !== 'custom'">${
  opt('1m', t('range_1m'))}${opt('3m', t('range_3m'))}${opt('6m', t('range_6m'))}${opt('1y', t('range_1y'))}${opt('custom', t('range_custom'))}</select></div>
      <div><label for="from">${h(t('date_from'))}</label><input type="date" id="from" name="from" value="${h(r.from)}"${custom ? '' : ' disabled'}></div>
      <div><label for="to">${h(t('date_to'))}</label><input type="date" id="to" name="to" value="${h(r.to)}"${custom ? '' : ' disabled'}></div>
      <button type="submit" class="button">${h(t('apply'))}</button></form>
      ${rows.length ? table([t('tag'), t('box'), t('item'), t('from_jst'), t('to_jst')], rows, '') + (d.capped ? `<p>${h(t('history_cap', { n: d.cap }))}</p>` : '') : `<p>${h(t('no_history'))}</p>`}`;
  }

  // ---------- process board (on the home page) ----------
  function boardHtml() {
    const flags = (e) => (e.late ? badge('overdue', t('overdue')) : `<small>${e.due}</small>`) + (e.stagnant ? ' ' + badge('stagnant', t('stagnant')) : '');
    const list = (rows, time) => (rows.length ? `<ul class="process-boxes">${rows.map((e) =>
      `<li>${orderLink(e.order.job, itemBox(e))} ${time(e) != null ? `<small>${fmtDur(time(e))}</small>` : ''} ${flags(e)}</li>`).join('')}</ul>` : '');
    const card = (c) => `<section class="card process-card${c.queue ? ' queue-building' : ''}"><h3>${h(pname(c.proc))}
      ${c.outsourced && c.proc.vendor ? `<small>${h(c.proc.vendor)}</small>` : ''} ${c.queue ? badge('queue', t('queue_building')) : ''}</h3>
      <p class="process-counts"><span><strong>${c.running.length}</strong> ${h(t('running'))}</span><span><strong>${c.waiting.length}</strong> ${h(t('waiting'))}</span></p>
      ${list(c.running, (e) => e.runningFor)}${c.waiting.length ? `<h4>${h(t('waiting'))}</h4>${list(c.waiting, (e) => e.idle)}` : ''}</section>`;
    const cards = FT.processBoard(world);
    const grid = (rows, empty) => `<div class="board-grid">${rows.length ? rows.map(card).join('') : `<p>${h(empty)}</p>`}</div>`;
    return `<h3>${h(t('in_house'))}</h3>${grid(cards.filter((c) => !c.outsourced), t('no_in_house'))}
      <h3>${h(t('outsourced'))}</h3>${grid(cards.filter((c) => c.outsourced), t('no_outsourced'))}`;
  }

  // ---------- priorities ----------
  function pagePriorities(app) {
    const sort = Object.keys(FT.PRIORITY_SORTS).includes(Q.get('sort')) ? Q.get('sort') : 'risk';
    const all = FT.prioritiesRows(world, sort), tier = FT.TIERS.includes(Q.get('tier')) ? Q.get('tier') : '';
    const rows = all.filter((r) => !tier || r.tier === tier).map((r) => `<tr><td>${orderLink(r.order.job, r.item.id + (r.multi ? ' - ' + t('box_n', { l: r.box.label }) : ''))}</td>
      <td>${h(r.item.part)}</td><td>${h(r.order.customer)}</td><td>${r.order.due}</td><td>${r.daysLeft}</td><td>${r.slack}</td><td>${h(pname(r.proc))}</td><td>${fmtDur(r.idle)}</td>
      <td>${badge('risk-' + r.tier.toLowerCase(), t(r.tier))}</td><td><ul class="reasons">${r.reasons.map((x) => `<li>${h(reasonText(x))}</li>`).join('')}</ul></td></tr>`);
    const links = FT.TIERS.map((c) => `<a href="?tier=${c}&amp;sort=${sort}" class="badge badge-risk-${c.toLowerCase()}${tier === c ? ' active' : ''}">${h(t(c))} (${all.filter((r) => r.tier === c).length})</a>`).join('');
    const opt = (v, text) => `<option value="${v}"${v === sort ? ' selected' : ''}>${h(text)}</option>`;
    app.innerHTML = `<h1>${h(t('prio_title'))}</h1><nav class="tier-filters"><a href="?sort=${sort}"${tier ? '' : ' class="active"'}>${h(t('all'))} (${all.length})</a>${links}</nav>
      <form method="get" class="filters">${tier ? `<input type="hidden" name="tier" value="${tier}">` : ''}<div><label for="sort">${h(t('sort_by'))}</label><select id="sort" name="sort" onchange="this.form.submit()">${
  opt('risk', t('risk'))}${opt('process', t('current_process'))}${opt('waiting', t('idle_for'))}</select></div></form>
      ${rows.length ? table([t('item'), t('part'), t('customer'), t('due_date'), t('days_left'), t('slack'), t('current_process'), t('idle_for'), t('risk'), t('reasons')], rows, '') : `<p>${h(t('nothing_attention'))}</p>`}`;
  }

  // ---------- start ----------
  const PAGES = { home: pageHome, boxes: pageBoxes, orders: pageOrders, customers: pageCustomers, order: pageOrder, edit: pageEdit, new: pageNewOrder, tags: pageTags, 'tag-history': pageTagHistory, simulate: pageSimulate, priorities: pagePriorities };
  function boot() {
    refresh();
    shell();
    const page = document.body.dataset.page;
    document.title = `${t(`nav_${{ home: 'home', order: 'orders', edit: 'orders', new: 'new', simulate: 'sim', priorities: 'prio', 'tag-history': 'tags' }[page] || page}`)} - ${t('brand')}`;
    PAGES[page]($('#app'));
  }
  document.addEventListener('DOMContentLoaded', () => {
    I18N.lang = Q.get('lang') || store.get(LANG_KEY, 'ja');
    store.set(LANG_KEY, I18N.lang);
    boot();
  });
})();
