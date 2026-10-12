// The Django app's status, priority and overview rules ported to plain JS. Each function names the Python one it mirrors.
const FT = (() => {
  const NOT_STARTED = 'NOT_STARTED', WAITING = 'WAITING', IN_PROGRESS = 'IN_PROGRESS', AT_VENDOR = 'AT_VENDOR', COMPLETE = 'COMPLETE';
  const PENDING = 'pending', STEP_IN_PROGRESS = 'in_progress', STEP_AT_VENDOR = 'at_vendor', DONE = 'done';
  const TIERS = ['OVERDUE', 'CRITICAL', 'WATCH', 'ON_TRACK'];
  const STAGNATION_DEFAULT_HOURS = 24, QUEUE_WAITING = 3, DAYS_PER_STEP = 1, WATCH_SLACK = 2;

  // ---------- dates (JST) ----------
  const jstStr = (ms) => new Date(ms).toLocaleString('sv-SE', { timeZone: 'Asia/Tokyo' }); // 'YYYY-MM-DD HH:MM:SS'
  const jstDate = (ms) => jstStr(ms).slice(0, 10);
  const dayNum = (d) => Date.parse(d + 'T00:00:00Z') / DAY;
  const addDays = (d, n) => new Date((dayNum(d) + n) * DAY).toISOString().slice(0, 10);

  // status.due_back
  const dueBack = (startedMs, lead) => (startedMs == null || lead == null ? null : addDays(jstDate(startedMs), lead));
  // status.days_late
  const daysLate = (due, today) => (due ? Math.max(dayNum(today) - dayNum(due), 0) : 0);

  // ---------- world ----------
  // Seed + the visitor's saved changes -> one world object that every function below reads
  function makeWorld(seed, st, now) {
    const today = jstDate(now);
    const ended = st.ended || {};
    const assignments = [...seed.assignments, ...(st.assignments || [])].map((a) => (ended[a.id] != null ? { ...a, end: ended[a.id] } : a));
    const w = {
      now, today, procs: PROCESSES, assignments,
      orders: [...seed.orders, ...(st.orders || [])].map((o) => ({ ...o, ...(st.orderEdits || {})[o.job] })),
      items: [...seed.items, ...(st.items || [])].filter((i) => !(st.deleted || []).includes(i.id)).map((i) => ({ ...i, ...(st.itemEdits || {})[i.id] })),
      changes: st.changes || [],
      events: [...seed.events, ...(st.events || [])], inactive: new Set(st.inactive || []),
      tags: [...new Set([...seed.tags, ...(st.tags || []), ...assignments.map((a) => a.tag)])].sort(),
    };
    w.proc = (id) => w.procs.find((p) => p.id === id);
    w.order = (job) => w.orders.find((o) => o.job === job);
    w.statusCache = new Map();
    return w;
  }

  // ---------- per-box status ----------
  // status.events_for_box: every tag of the (item, box) pair within its own window, minus voided events, in time order
  function eventsForBox(w, group) {
    const voided = new Set(w.events.filter((e) => e.type === 'CORRECTION').map((e) => e.corrects));
    return w.events
      .filter((e) => !voided.has(e.id) && group.some((a) => e.tag === a.tag && e.t >= a.start && (a.end == null || e.t < a.end)))
      .sort((a, b) => a.t - b.t || a.id - b.id);
  }

  // status._apply: move the matching route step to its next state; warn if nothing fits
  function apply(e, rows, warnings) {
    const cands = rows.filter((r) => r.proc.id === e.proc);
    const pick = (...states) => { for (const s of states) { const r = cands.find((x) => x.state === s); if (r) return r; } return null; };
    let r = null;
    if (e.type === 'ARRIVED') { r = pick(PENDING); if (r) { r.state = DONE; r.started = r.finished = e.t; } }
    else if (e.type === 'STARTED') { r = pick(PENDING); if (r) { r.state = STEP_IN_PROGRESS; r.started = e.t; } }
    else if (e.type === 'FINISHED') { r = pick(STEP_IN_PROGRESS, PENDING); if (r) { r.state = DONE; r.finished = e.t; } }
    else if (e.type === 'SENT_OUT') { r = pick(PENDING); if (r) { r.state = STEP_AT_VENDOR; r.started = e.t; } }
    else if (e.type === 'RETURNED') { r = pick(STEP_AT_VENDOR, PENDING); if (r) { r.state = DONE; r.finished = e.t; } }
    else return;
    if (!cands.length) warnings.push({ k: 'warn_notinroute', proc: e.proc });
    else if (!r) warnings.push({ k: 'warn_unexpected', type: e.type, proc: e.proc });
  }

  // status._current: the latest active step, else the first step not done
  function current(rows) {
    const active = rows.filter((r) => r.state === STEP_IN_PROGRESS || r.state === STEP_AT_VENDOR);
    if (active.length) return active.reduce((a, b) => (b.started > a.started ? b : a));
    return rows.find((r) => r.state !== DONE) || null;
  }

  // status._replay: status of one (item, box) pair
  function replay(w, steps, group, events) {
    const rows = steps.map((proc) => ({ proc, state: PENDING, started: null, finished: null }));
    const warnings = [];
    events.forEach((e) => { if (e.proc) apply(e, rows, warnings); });
    rows.forEach((r, i) => {
      if (r.state !== PENDING) rows.slice(0, i).filter((p) => p.state === PENDING).forEach((p) => warnings.push({ k: 'warn_skipped', proc: p.proc.id }));
    });
    const cur = current(rows);
    let code;
    if (!rows.length || rows.every((r) => r.state === PENDING)) code = NOT_STARTED;
    else if (!cur) code = COMPLETE;
    else code = { [STEP_IN_PROGRESS]: IN_PROGRESS, [STEP_AT_VENDOR]: AT_VENDOR }[cur.state] || WAITING;
    const assignment = group.length ? group[group.length - 1] : null;
    return {
      assignment, assignments: group, label: assignment ? assignment.box : '', code, current: cur ? cur.proc : null, rows,
      warnings: [...new Map(warnings.map((x) => [JSON.stringify(x), x])).values()],
      lastEvent: events.length ? events[events.length - 1].t : null,
    };
  }

  // status._roll_up: item status from its boxes; the deciding box supplies step, rows and last scan
  function rollUp(boxes) {
    const codes = new Set(boxes.map((b) => b.code));
    let code, pick, last;
    if (codes.size === 1 && (codes.has(COMPLETE) || codes.has(NOT_STARTED))) {
      code = boxes[0].code; pick = boxes[0]; last = pick.lastEvent;
    } else {
      code = [IN_PROGRESS, WAITING, AT_VENDOR].find((c) => codes.has(c));
      if (code) {
        pick = boxes.filter((b) => b.code === code).reduce((a, b) => ((b.lastEvent ?? -Infinity) > (a.lastEvent ?? -Infinity) ? b : a));
        last = pick.lastEvent;
      } else {
        code = WAITING; pick = boxes.find((b) => b.code === NOT_STARTED);
        last = Math.max(-Infinity, ...boxes.map((b) => b.lastEvent ?? -Infinity)); if (last === -Infinity) last = null;
      }
    }
    const warnings = [...new Map(boxes.flatMap((b) => b.warnings).map((x) => [JSON.stringify(x), x])).values()];
    return { code, current: pick.current, rows: pick.rows, warnings, lastEvent: last, boxes };
  }

  // status.item_status: one box per (item, box) pair plus placeholders for unlinked expected boxes
  function itemStatus(w, item) {
    if (w.statusCache.has(item.id)) return w.statusCache.get(item.id);
    const steps = item.route.map((id) => w.proc(id));
    const groups = new Map();
    w.assignments.filter((a) => a.item === item.id).sort((a, b) => a.start - b.start).forEach((a) => {
      if (!groups.has(a.box)) groups.set(a.box, []);
      groups.get(a.box).push(a);
    });
    const boxes = [...groups.values()].map((g) => replay(w, steps, g, eventsForBox(w, g)));
    for (let i = Math.max(item.boxCount - boxes.length, boxes.length ? 0 : 1); i > 0; i--) boxes.push(replay(w, steps, [], []));
    const st = rollUp(boxes);
    w.statusCache.set(item.id, st);
    return st;
  }

  // status.order_summary: NOT_STARTED, IN_PROGRESS or COMPLETE for the whole order
  function orderSummary(w, order) {
    const codes = w.items.filter((i) => i.order === order.job).map((i) => itemStatus(w, i).code);
    const done = codes.filter((c) => c === COMPLETE).length;
    if (codes.length && done === codes.length) return { code: COMPLETE, done, total: codes.length };
    if (codes.every((c) => c === NOT_STARTED)) return { code: NOT_STARTED, done, total: codes.length };
    return { code: IN_PROGRESS, done, total: codes.length };
  }

  // status.idle_for / status.is_stagnant
  const idleFor = (box, now) => (box.lastEvent == null ? null : now - box.lastEvent);
  function isStagnant(box, now) {
    const idle = idleFor(box, now);
    if ((box.code !== WAITING && box.code !== IN_PROGRESS) || idle == null || !box.current) return false;
    return idle > (box.current.stagnation || STAGNATION_DEFAULT_HOURS) * HOUR;
  }

  // status.next_action: [event type, process] that moves a box one step forward, or null
  function nextAction(box) {
    const row = current(box.rows);
    if (!row || box.code === COMPLETE) return null;
    if (row.state === STEP_IN_PROGRESS) return { type: 'FINISHED', proc: row.proc };
    if (row.state === STEP_AT_VENDOR) return { type: 'RETURNED', proc: row.proc };
    return { type: { MATERIAL: 'ARRIVED', MACHINING: 'STARTED', OUTSOURCED: 'SENT_OUT' }[row.proc.kind], proc: row.proc };
  }

  // status.timeline: duration, running flag and wait before each step
  function timeline(rows, now) {
    let prevEnd = null;
    return rows.map((r) => {
      const running = (r.state === STEP_IN_PROGRESS || r.state === STEP_AT_VENDOR) && r.started != null;
      const end = running ? now : r.finished;
      const wait = r.started != null && prevEnd != null ? r.started - prevEnd : null;
      if (r.finished != null) prevEnd = r.finished;
      return { row: r, duration: r.started != null && end != null ? end - r.started : null, running, wait: wait != null && wait >= 0 && wait > 0 ? wait : null };
    });
  }

  // ---------- priority ----------
  // priority.priority: tier, days left/needed, slack and reasons (as {k, ...} for the UI to translate)
  function priority(w, order, box) {
    const left = box.rows.filter((r) => r.state !== DONE);
    let needed = 0;
    const reasons = [];
    left.forEach((r) => {
      const lead = r.proc.lead;
      if (r.state === STEP_AT_VENDOR) {
        const back = dueBack(r.started, lead);
        const late = daysLate(back, w.today);
        needed += back ? Math.max(dayNum(back) - dayNum(w.today), 0) : DAYS_PER_STEP;
        if (late) reasons.push({ k: 'r_vendor_late', n: late });
      } else needed += lead || DAYS_PER_STEP;
    });
    const daysLeft = dayNum(order.due) - dayNum(w.today);
    const slack = daysLeft - needed;
    const tier = daysLeft < 0 ? 'OVERDUE' : slack < 0 ? 'CRITICAL' : slack <= WATCH_SLACK ? 'WATCH' : 'ON_TRACK';
    reasons.unshift({ k: daysLeft >= 0 ? 'r_due_in' : 'r_overdue_by', d: Math.abs(daysLeft), s: left.length });
    if (box.warnings.length) reasons.push({ k: 'r_warnings', n: box.warnings.length });
    return { tier, daysLeft, daysNeeded: needed, slack, reasons };
  }

  // ---------- overview ----------
  // overview.active_boxes: one entry per unfinished box (placeholders included)
  function activeBoxes(w) {
    const out = [];
    w.items.forEach((item) => {
      const st = itemStatus(w, item);
      const order = w.order(item.order);
      st.boxes.forEach((box) => {
        if (box.code === COMPLETE) return;
        const row = current(box.rows);
        const proc = row ? row.proc : null;
        const started = row ? row.started : null;
        const e = {
          item, order, box, boxLabel: box.assignment ? box.label : null, multi: st.boxes.length > 1, code: box.code, proc,
          started, lastEvent: box.lastEvent, idle: idleFor(box, w.now), stagnant: isStagnant(box, w.now),
          due: order.due, late: order.due < w.today, dueBack: null, lateDays: 0,
          daysOut: started != null ? Math.floor((w.now - started) / DAY) : null,
        };
        if (box.code === AT_VENDOR) { e.dueBack = dueBack(started, proc.lead); e.lateDays = daysLate(e.dueBack, w.today); }
        out.push(e);
      });
    });
    return out.sort((a, b) => (a.due < b.due ? -1 : a.due > b.due ? 1 : 0) || a.item.id.localeCompare(b.item.id) || (a.boxLabel || '').localeCompare(b.boxLabel || ''));
  }

  // overview.process_board: per in-house/outsourced process, running (longest first) and waiting (longest idle first) boxes
  function processBoard(w) {
    const boxes = activeBoxes(w);
    return w.procs.filter((p) => p.kind !== 'MATERIAL').map((p) => {
      const running = boxes.filter((e) => e.proc === p && (e.code === IN_PROGRESS || e.code === AT_VENDOR))
        .map((e) => ({ ...e, runningFor: e.started != null ? w.now - e.started : null }))
        .sort((a, b) => (a.runningFor == null) - (b.runningFor == null) || b.runningFor - a.runningFor);
      const waiting = boxes.filter((e) => e.proc === p && e.code === WAITING)
        .sort((a, b) => (a.idle == null) - (b.idle == null) || b.idle - a.idle);
      return { proc: p, outsourced: p.kind === 'OUTSOURCED', running, waiting, queue: waiting.length >= QUEUE_WAITING };
    });
  }

  // core.views.home: counters, in-house and at-vendor rows (boxes of unfinished orders)
  function homeData(w) {
    const boxes = activeBoxes(w);
    const count = (c) => boxes.filter((e) => e.code === c).length;
    return {
      counters: {
        in_progress: count(IN_PROGRESS), waiting: count(WAITING), at_vendor: count(AT_VENDOR), not_started: count(NOT_STARTED),
        stagnant: boxes.filter((e) => e.stagnant).length, overdue: new Set(boxes.filter((e) => e.late).map((e) => e.order.job)).size,
        vendor_overdue: boxes.filter((e) => e.lateDays).length,
      },
    };
  }

  // views.boxes: the result-list rows for the given filters and sort (the same rows the home counters count)
  const BOX_SORTS = {
    process: (a, b) => (a.proc ? 0 : 1) - (b.proc ? 0 : 1) || (a.proc ? a.proc.name : '').localeCompare(b.proc ? b.proc.name : '') || a.due.localeCompare(b.due),
    due: (a, b) => a.due.localeCompare(b.due) || a.order.job.localeCompare(b.order.job) || a.item.id.localeCompare(b.item.id) || (a.boxLabel || '').localeCompare(b.boxLabel || ''),
    customer: (a, b) => a.order.customer.localeCompare(b.order.customer) || a.due.localeCompare(b.due) || a.order.job.localeCompare(b.order.job),
    waiting: (a, b) => (a.idle == null) - (b.idle == null) || (b.idle || 0) - (a.idle || 0) || a.due.localeCompare(b.due),
  };
  function boxesPage(w, f) {
    let rows = activeBoxes(w);
    if (f.status) rows = rows.filter((e) => e.code === f.status);
    if (f.stagnant) rows = rows.filter((e) => e.stagnant);
    if (f.vendorLate) rows = rows.filter((e) => e.lateDays);
    return rows.sort(BOX_SORTS[f.sort] || BOX_SORTS.due);
  }

  // views.priorities: one row per started or linked unfinished box, most urgent first
  const PRIORITY_SORTS = {
    risk: (a, b) => TIERS.indexOf(a.tier) - TIERS.indexOf(b.tier) || a.slack - b.slack,
    process: (a, b) => (a.proc ? 0 : 1) - (b.proc ? 0 : 1) || (a.proc ? a.proc.name : '').localeCompare(b.proc ? b.proc.name : '') || PRIORITY_SORTS.risk(a, b),
    waiting: (a, b) => (a.idle == null) - (b.idle == null) || (b.idle || 0) - (a.idle || 0) || PRIORITY_SORTS.risk(a, b),
  };
  function prioritiesRows(w, sort) {
    const idle = new Map(activeBoxes(w).map((e) => [`${e.item.id}|${e.boxLabel}`, e.idle]));
    const rows = [];
    w.items.forEach((item) => {
      const st = itemStatus(w, item);
      const order = w.order(item.order);
      st.boxes.forEach((box) => {
        const a = box.assignment;
        if (box.code === COMPLETE || !a || (a.end != null && box.code === NOT_STARTED)) return;
        rows.push({ item, order, box, multi: st.boxes.length > 1, proc: box.current, idle: idle.get(`${item.id}|${box.label}`), ...priority(w, order, box) });
      });
    });
    return rows.sort(PRIORITY_SORTS[sort] || PRIORITY_SORTS.risk);
  }

  // forms.OrderItemForm: boxes already linked (open or ended) and whether the item has any scan
  const linkedBoxes = (w, item) => new Set(w.assignments.filter((a) => a.item === item.id).map((a) => a.box)).size;
  const itemHasEvents = (w, item) => {
    const group = w.assignments.filter((a) => a.item === item.id);
    return group.length > 0 && eventsForBox(w, group).length > 0;
  };

  // ---------- tag services (tracking/services.py) ----------
  // services.resolve_tag: the assignment active for a tag at a moment
  const resolveTag = (w, uid, when) => w.assignments.find((a) => a.tag === uid && a.start <= when && (a.end == null || a.end > when)) || null;
  const openOf = (w) => w.assignments.filter((a) => a.end == null);

  // services.assign_tag -> {ok, assignment} or {error: {k, ...}}
  function assignTag(w, itemId, boxLabel, uidRaw) {
    const box = boxLabel.trim(), uid = uidRaw.trim().toUpperCase();
    if (!box || !uid) return { error: { k: 'e_required' } };
    const item = w.items.find((i) => i.id === itemId);
    if (w.inactive.has(uid)) return { error: { k: 'e_inactive', tag: uid } };
    const open = openOf(w);
    if (open.filter((a) => a.item === itemId).length >= item.boxCount) return { error: { k: 'e_all_linked', item: item.id, n: item.boxCount } };
    let f = open.find((a) => a.tag === uid);
    if (f) return { error: { k: 'e_tag_in_use', tag: uid, item: f.item } };
    f = open.find((a) => a.box === box);
    if (f) return { error: { k: 'e_box_in_use', box, item: f.item } };
    return { ok: true, assignment: { id: `u${w.assignments.length + 1}-${w.now}`, tag: uid, box, item: itemId, start: w.now, end: null } };
  }

  // services.replace_tag -> {ok, assignment, endId, lostTag} or {error}
  function replaceTag(w, old, uidRaw, markLost) {
    const uid = uidRaw.trim().toUpperCase();
    if (old.end != null || !uid) return { error: { k: 'e_required' } };
    if (uid === old.tag) return { error: { k: 'e_same_tag' } };
    if (w.inactive.has(uid)) return { error: { k: 'e_inactive', tag: uid } };
    const f = openOf(w).find((a) => a.tag === uid);
    if (f) return { error: { k: 'e_tag_in_use', tag: uid, item: f.item } };
    return { ok: true, endId: old.id, lostTag: markLost ? old.tag : null, assignment: { id: `u${w.assignments.length + 1}-${w.now}`, tag: uid, box: old.box, item: old.item, start: w.now, end: null } };
  }

  return {
    NOT_STARTED, WAITING, IN_PROGRESS, AT_VENDOR, COMPLETE, TIERS, jstStr, jstDate, dayNum, addDays, dueBack, daysLate, makeWorld,
    eventsForBox, itemStatus, orderSummary, idleFor, isStagnant, nextAction, timeline, priority, activeBoxes, processBoard, homeData, boxesPage, BOX_SORTS, PRIORITY_SORTS,
    prioritiesRows, linkedBoxes, itemHasEvents, resolveTag, openOf, assignTag, replaceTag, current,
  };
})();
