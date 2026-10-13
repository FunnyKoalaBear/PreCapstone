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
const P = { mat: 1, cut: 2, weld: 3, asm: 4, insp: 5, paint: 6, plate: 7 };

// Build the seed world: orders, items, tag assignments and scans (times in ms, relative to now)
function buildSeed(now) {
  const jst = new Date(now + 9 * HOUR).toISOString();
  const prefix = jst.slice(2, 4) + jst.slice(5, 7);
  const today = jst.slice(0, 10);
  const dayStr = (n) => new Date(Date.parse(today + 'T00:00:00Z') + n * DAY).toISOString().slice(0, 10);
  const seed = { orders: [], items: [], assignments: [], events: [], tags: ['B15D6B73'] };
  const ago = (h) => now - h * HOUR;
  let seq = 0;
  let evId = 0;
  const scan = (tag, proc, type, hoursAgo, dev = 0, corrects = null) => {
    const e = { id: ++evId, t: ago(hoursAgo), tag, type, proc: P[proc] || null, device: DEVICES[dev], corrects };
    seed.events.push(e);
    return e;
  };
  // One order with one item and its first tag/box; extra = further expected-box settings
  const make = (customer, part, route, due, tag, box, custNo, o = {}) => {
    const job = `${prefix}-${String(++seq).padStart(3, '0')}`;
    seed.orders.push({ job, customer: CUSTOMERS[customer], custNo, due: dayStr(due), notes: '' });
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
  make(0, 'ブラケット', ['mat', ...full], 10, '0781DD5E', 'DEMO-01', 'TY-2610-014');
  scan('0781DD5E', 'mat', 'ARRIVED', 34);
  [['cut', 30, 26], ['weld', 22, 18]].forEach(([p, s, f]) => { scan('0781DD5E', p, 'STARTED', s); scan('0781DD5E', p, 'FINISHED', f); });
  scan('0781DD5E', 'asm', 'STARTED', 3, 1);
  // 2 Overdue
  make(0, 'フランジ', ['cut', 'weld', 'insp'], -3, 'F8DF3064', 'DEMO-02', 'TY-2610-009');
  scan('F8DF3064', 'cut', 'STARTED', 80); scan('F8DF3064', 'cut', 'FINISHED', 70); scan('F8DF3064', 'weld', 'STARTED', 2, 1);
  // 3 Critical: due soon, 4 steps left, nothing scanned yet
  make(1, 'ハウジング', full, 2, '74BA7571', 'DEMO-03', 'HS-45021');
  // 4 Vendor late: Painting out 10 days, lead 5
  make(1, 'カバー', ['cut', 'paint'], 4, '8EFBD21A', 'DEMO-04', 'HS-45017');
  scan('8EFBD21A', 'cut', 'STARTED', 12 * 24); scan('8EFBD21A', 'cut', 'FINISHED', 11 * 24); scan('8EFBD21A', 'paint', 'SENT_OUT', 10 * 24, 1);
  // 5 Stagnant: finished a step 40h ago, nothing since
  make(0, 'シャフト', ['cut', 'weld', 'insp'], 6, '57E36722', 'DEMO-05', 'TY-2610-021');
  scan('57E36722', 'cut', 'STARTED', 46); scan('57E36722', 'cut', 'FINISHED', 40);
  // 6 Complete, due yesterday
  make(1, 'プレート', ['cut', 'weld', 'plate'], -1, 'FC3F61E6', 'DEMO-06', 'HS-44986');
  scan('FC3F61E6', 'cut', 'STARTED', 6 * 24); scan('FC3F61E6', 'cut', 'FINISHED', 5 * 24 + 20);
  scan('FC3F61E6', 'weld', 'STARTED', 5 * 24); scan('FC3F61E6', 'weld', 'FINISHED', 4 * 24);
  scan('FC3F61E6', 'plate', 'SENT_OUT', 3 * 24, 1); scan('FC3F61E6', 'plate', 'RETURNED', 24, 1);
  // 7 Not started
  make(0, 'ピン', ['cut', 'weld', 'asm'], 20, '092C4B14', 'DEMO-07', 'TY-2610-027');
  // 8 Messy scans: skipped step, process not in route, unassigned tag, voided wrong scan
  make(1, 'ギア', full, 5, '3E180A05', 'DEMO-08', 'HS-45030');
  scan('3E180A05', 'cut', 'STARTED', 30); scan('3E180A05', 'cut', 'FINISHED', 28); scan('3E180A05', 'asm', 'STARTED', 20);
  scan('3E180A05', 'plate', 'SENT_OUT', 18, 1);
  const wrong = scan('3E180A05', 'insp', 'FINISHED', 15);
  scan('3E180A05', null, 'CORRECTION', 14, 1, wrong.id);
  scan('B15D6B73', 'cut', 'STARTED', 6, 1);
  // 9 Reused box: DEMO-09 carried a finished order, now carries a new one
  make(1, '旧部品', ['cut'], -10, '03193FDF', 'DEMO-09', 'HS-44871', { startH: 21 * 24, endH: 9 * 24 });
  scan('03193FDF', 'cut', 'STARTED', 19 * 24); scan('03193FDF', 'cut', 'FINISHED', 18 * 24);
  make(0, '新部品', ['cut', 'weld'], 7, '414D497D', 'DEMO-09', 'TY-2610-025', { startH: 48 });
  scan('414D497D', 'cut', 'STARTED', 30); scan('414D497D', 'cut', 'FINISHED', 20); scan('414D497D', 'weld', 'STARTED', 2, 1);
  // 10 Split item: 3 boxes expected; one finished, one at step 2, one not linked yet
  const split = make(0, 'フレーム', ['cut', 'weld', 'asm'], 8, 'C36F67C6', 'DEMO-10', 'TY-2610-018', { boxCount: 3 });
  link(split, 'D795A680', 'DEMO-11');
  [['cut', 40, 36], ['weld', 34, 30], ['asm', 28, 24]].forEach(([p, s, f]) => { scan('C36F67C6', p, 'STARTED', s); scan('C36F67C6', p, 'FINISHED', f); });
  scan('D795A680', 'cut', 'STARTED', 26); scan('D795A680', 'cut', 'FINISHED', 22); scan('D795A680', 'weld', 'STARTED', 1, 1);
  return seed;
}
