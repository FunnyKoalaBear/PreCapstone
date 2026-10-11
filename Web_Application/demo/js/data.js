// Sample data of the static demo: the same scenarios as the seed_demo command, all times relative to "now".
const HOUR = 36e5;
const DAY = 24 * HOUR;
const DEVICES = ['DEMO Scanner 1', 'DEMO Scanner 2'];
const CUSTOMERS = ['DEMO 東洋機械', 'DEMO 北斗製作所'];
// lead = vendor lead days; stagnation = per-process idle limit in hours (blank = default 24)
const PROCESSES = [
  { id: 1, name: 'Material', ja: '材料入荷', kind: 'MATERIAL' },
  { id: 2, name: 'Cutting', ja: '切断', kind: 'MACHINING' },
  { id: 3, name: 'Welding', ja: '溶接', kind: 'MACHINING' },
  { id: 4, name: 'Assembly', ja: '組立', kind: 'MACHINING' },
  { id: 5, name: 'Inspection', ja: '検査', kind: 'MACHINING' },
  { id: 6, name: 'Painting', ja: '塗装', kind: 'OUTSOURCED', vendor: 'DEMO 日本塗装', lead: 5 },
  { id: 7, name: 'Plating', ja: 'メッキ', kind: 'OUTSOURCED', vendor: 'DEMO メッキ工業', lead: 7 },
];
const ACTIONS = { MATERIAL: ['ARRIVED'], MACHINING: ['STARTED', 'FINISHED'], OUTSOURCED: ['SENT_OUT', 'RETURNED'] };
const P = { mat: 1, cut: 2, weld: 3, asm: 4, insp: 5, paint: 6, plate: 7 };

// Build the seed world: orders, items, tag assignments and scans (times in ms, relative to now)
function buildSeed(now) {
  const jst = new Date(now + 9 * HOUR).toISOString();
  const prefix = jst.slice(2, 4) + jst.slice(5, 7);
  const today = jst.slice(0, 10);
  const dayStr = (n) => new Date(Date.parse(today + 'T00:00:00Z') + n * DAY).toISOString().slice(0, 10);
  const seed = { orders: [], items: [], assignments: [], events: [], tags: ['DEMO-X1'] };
  const ago = (h) => now - h * HOUR;
  let seq = 0;
  let evId = 0;
  const scan = (tag, proc, type, hoursAgo, dev = 0, corrects = null) => {
    const e = { id: ++evId, t: ago(hoursAgo), tag, type, proc: P[proc] || null, device: DEVICES[dev], corrects };
    seed.events.push(e);
    return e;
  };
  // One order with one item and its first tag/box; extra = further expected-box settings
  const make = (customer, part, route, due, tag, box, o = {}) => {
    const job = `${prefix}-${String(++seq).padStart(3, '0')}`;
    seed.orders.push({ job, customer: CUSTOMERS[customer], custNo: '', due: dayStr(due), notes: '' });
    const id = `${job}-01`;
    seed.items.push({ id, order: job, no: 1, part, qty: 10, boxCount: o.boxCount || 1, route: route.map((r) => P[r]) });
    seed.assignments.push({ id: `s${seed.assignments.length + 1}`, tag, box, item: id, start: ago(o.startH || 21 * 24), end: o.endH == null ? null : ago(o.endH) });
    seed.tags.push(tag);
    return id;
  };
  const link = (item, tag, box, startH = 21 * 24) => {
    seed.assignments.push({ id: `s${seed.assignments.length + 1}`, tag, box, item, start: ago(startH), end: null });
    seed.tags.push(tag);
  };
  const full = ['cut', 'weld', 'asm', 'insp'];

  // 1 Healthy: material arrived, two steps done with a 4h wait, third running
  make(0, 'ブラケット', ['mat', ...full], 10, 'DEMO-01', 'DEMO-01');
  scan('DEMO-01', 'mat', 'ARRIVED', 34);
  [['cut', 30, 26], ['weld', 22, 18]].forEach(([p, s, f]) => { scan('DEMO-01', p, 'STARTED', s); scan('DEMO-01', p, 'FINISHED', f); });
  scan('DEMO-01', 'asm', 'STARTED', 3, 1);
  // 2 Overdue
  make(0, 'フランジ', ['cut', 'weld', 'insp'], -3, 'DEMO-02', 'DEMO-02');
  scan('DEMO-02', 'cut', 'STARTED', 80); scan('DEMO-02', 'cut', 'FINISHED', 70); scan('DEMO-02', 'weld', 'STARTED', 2, 1);
  // 3 Critical: due soon, 4 steps left, nothing scanned yet
  make(1, 'ハウジング', full, 2, 'DEMO-03', 'DEMO-03');
  // 4 Vendor late: Painting out 10 days, lead 5
  make(1, 'カバー', ['cut', 'paint'], 4, 'DEMO-04', 'DEMO-04');
  scan('DEMO-04', 'cut', 'STARTED', 12 * 24); scan('DEMO-04', 'cut', 'FINISHED', 11 * 24); scan('DEMO-04', 'paint', 'SENT_OUT', 10 * 24, 1);
  // 5 Stagnant: finished a step 40h ago, nothing since
  make(0, 'シャフト', ['cut', 'weld', 'insp'], 6, 'DEMO-05', 'DEMO-05');
  scan('DEMO-05', 'cut', 'STARTED', 46); scan('DEMO-05', 'cut', 'FINISHED', 40);
  // 6 Complete, due yesterday
  make(1, 'プレート', ['cut', 'weld', 'plate'], -1, 'DEMO-06', 'DEMO-06');
  scan('DEMO-06', 'cut', 'STARTED', 6 * 24); scan('DEMO-06', 'cut', 'FINISHED', 5 * 24 + 20);
  scan('DEMO-06', 'weld', 'STARTED', 5 * 24); scan('DEMO-06', 'weld', 'FINISHED', 4 * 24);
  scan('DEMO-06', 'plate', 'SENT_OUT', 3 * 24, 1); scan('DEMO-06', 'plate', 'RETURNED', 24, 1);
  // 7 Not started
  make(0, 'ピン', ['cut', 'weld', 'asm'], 20, 'DEMO-07', 'DEMO-07');
  // 8 Messy scans: skipped step, process not in route, unassigned tag, voided wrong scan
  make(1, 'ギア', full, 5, 'DEMO-08', 'DEMO-08');
  scan('DEMO-08', 'cut', 'STARTED', 30); scan('DEMO-08', 'cut', 'FINISHED', 28); scan('DEMO-08', 'asm', 'STARTED', 20);
  scan('DEMO-08', 'plate', 'SENT_OUT', 18, 1);
  const wrong = scan('DEMO-08', 'insp', 'FINISHED', 15);
  scan('DEMO-08', null, 'CORRECTION', 14, 1, wrong.id);
  scan('DEMO-X1', 'cut', 'STARTED', 6, 1);
  // 9 Reused box: DEMO-09 carried a finished order, now carries a new one
  make(1, '旧部品', ['cut'], -10, 'DEMO-09A', 'DEMO-09', { startH: 21 * 24, endH: 9 * 24 });
  scan('DEMO-09A', 'cut', 'STARTED', 19 * 24); scan('DEMO-09A', 'cut', 'FINISHED', 18 * 24);
  make(0, '新部品', ['cut', 'weld'], 7, 'DEMO-09B', 'DEMO-09', { startH: 48 });
  scan('DEMO-09B', 'cut', 'STARTED', 30); scan('DEMO-09B', 'cut', 'FINISHED', 20); scan('DEMO-09B', 'weld', 'STARTED', 2, 1);
  // 10 Split item: 3 boxes expected; one finished, one at step 2, one not linked yet
  const split = make(0, 'フレーム', ['cut', 'weld', 'asm'], 8, 'DEMO-10', 'DEMO-10', { boxCount: 3 });
  link(split, 'DEMO-11', 'DEMO-11');
  [['cut', 40, 36], ['weld', 34, 30], ['asm', 28, 24]].forEach(([p, s, f]) => { scan('DEMO-10', p, 'STARTED', s); scan('DEMO-10', p, 'FINISHED', f); });
  scan('DEMO-11', 'cut', 'STARTED', 26); scan('DEMO-11', 'cut', 'FINISHED', 22); scan('DEMO-11', 'weld', 'STARTED', 1, 1);
  return seed;
}
