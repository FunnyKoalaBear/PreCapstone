// Sample data for the static demo. Dates are relative to today so it always looks alive.
const DAY = 864e5;
const PROCESSES = [
  { id: 1, name: 'Material', kind: 'MATERIAL' },
  { id: 2, name: 'Milling', kind: 'MACHINING' },
  { id: 3, name: 'Lathe', kind: 'MACHINING' },
  { id: 4, name: 'Plating', kind: 'OUTSOURCED', vendor: 'Acme Plating', lead: 7 },
  { id: 5, name: 'Welding', kind: 'OUTSOURCED', vendor: 'Kobe Welding', lead: 5 },
];
const CUSTOMERS = ['Toyo Machinery', 'Sakura Robotics', 'Nippon Gear'];
const DEVICES = ['Gate A (receiving)', 'Machine shop reader', 'Shipping dock'];
const KIND_ACTIONS = {
  MATERIAL: ['ARRIVED'],
  MACHINING: ['STARTED', 'FINISHED'],
  OUTSOURCED: ['SENT_OUT', 'RETURNED'],
};
const ACTION_LABELS = { ARRIVED: 'Arrived', STARTED: 'Started', FINISHED: 'Finished', SENT_OUT: 'Sent out', RETURNED: 'Returned' };

// due: days from today. tagAgo: days ago the tag was assigned.
const BASE_ORDERS = [
  { job: '2610-001', customer: 'Toyo Machinery', custNo: 'PO-5521', due: 3, notes: 'Rush job, call before shipping.' },
  { job: '2610-002', customer: 'Sakura Robotics', custNo: 'SR-88', due: 9, notes: '' },
  { job: '2610-003', customer: 'Nippon Gear', custNo: '', due: 2, notes: '' },
  { job: '2610-004', customer: 'Toyo Machinery', custNo: 'PO-5530', due: -2, notes: 'Customer is waiting.' },
];
const BASE_ITEMS = [
  { order: '2610-001', no: 1, part: 'Drive shaft', qty: 5, route: [1, 2, 4], tag: 'DOG', box: 'BOX-001', tagAgo: 30 },
  { order: '2610-001', no: 2, part: 'Flange', qty: 10, route: [1, 3], tag: 'CAT', box: 'BOX-002', tagAgo: 30 },
  { order: '2610-002', no: 1, part: 'Bracket', qty: 20, route: [1, 2, 5], tag: 'PIG', box: 'BOX-003', tagAgo: 30 },
  { order: '2610-002', no: 2, part: 'Cover plate', qty: 8, route: [1, 2], tag: 'COW', box: 'BOX-004', tagAgo: 30 },
  { order: '2610-003', no: 1, part: 'Spacer', qty: 50, route: [1], tag: 'ELK', box: 'BOX-005', tagAgo: 30 },
  { order: '2610-004', no: 1, part: 'Gear blank', qty: 4, route: [1, 3, 4], tag: 'FOX', box: 'BOX-006', tagAgo: 30 },
];
// hoursAgo, tag, type, process id
const SEED_EVENTS = [
  [288, 'DOG', 'ARRIVED', 1], [264, 'DOG', 'STARTED', 2], [252, 'DOG', 'FINISHED', 2], [240, 'DOG', 'SENT_OUT', 4],
  [48, 'CAT', 'ARRIVED', 1],
  [72, 'PIG', 'ARRIVED', 1], [24, 'PIG', 'STARTED', 2],
  [120, 'ELK', 'ARRIVED', 1],
  [144, 'FOX', 'ARRIVED', 1], [120.5, 'FOX', 'STARTED', 3], [118, 'FOX', 'FINISHED', 3],
];
