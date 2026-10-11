// Japanese / English texts for the demo (Japanese terms follow docs/glossary.md). key: [ja, en]; an en array is [singular, plural].
const TEXTS = {
  // shell
  brand: ['工程進捗管理', 'Factory Tracker'], footer: ['工程進捗管理（デモ）', 'Factory Tracker (demo)'],
  banner: ['これはサンプルデータによるデモです。変更はこのブラウザにのみ保存されます。', 'This is a demo with sample data. Changes are saved only in this browser.'],
  reset_demo: ['デモデータをリセット', 'Reset demo data'],
  nav_home: ['ホーム', 'Home'], nav_orders: ['受注一覧', 'Orders'], nav_new: ['新規受注', 'New order'], nav_boxes: ['箱一覧', 'Boxes'],
  nav_board: ['工程ボード', 'Process board'], nav_prio: ['優先度', 'Priorities'], nav_tags: ['タグ管理', 'Tags'], nav_sim: ['スキャンのシミュレーション', 'Simulate scan'],
  // statuses, kinds, events, tiers
  NOT_STARTED: ['未着手', 'Not started'], WAITING: ['待機中', 'Waiting'], IN_PROGRESS: ['作業中', 'In progress'], AT_VENDOR: ['外注中', 'At vendor'], COMPLETE: ['完了', 'Complete'],
  pending: ['未処理', 'Pending'], in_progress: ['作業中', 'In progress'], at_vendor: ['外注中', 'At vendor'], done: ['完了', 'Done'],
  MATERIAL: ['材料', 'Material'], MACHINING: ['加工', 'Machining'], OUTSOURCED: ['外注', 'Outsourced'],
  ARRIVED: ['入荷', 'Arrived'], STARTED: ['開始', 'Started'], FINISHED: ['完了', 'Finished'], SENT_OUT: ['外注出し', 'Sent out'], RETURNED: ['外注戻り', 'Returned'], CORRECTION: ['訂正', 'Correction'],
  OVERDUE: ['納期遅れ', 'Overdue'], CRITICAL: ['緊急', 'Critical'], WATCH: ['注意', 'Watch'], ON_TRACK: ['順調', 'On track'],
  // common words
  item: ['品目', 'Item'], part: ['品名', 'Part'], customer: ['得意先', 'Customer'], status: ['状態', 'Status'], due_date: ['納期', 'Due date'], notes: ['備考', 'Notes'],
  process: ['工程', 'Process'], current_process: ['現在の工程', 'Current process'], box: ['箱', 'Box'], tag: ['タグ', 'Tag'], vendor: ['外注先', 'Vendor'],
  overdue: ['納期遅れ', 'Overdue'], stagnant: ['滞留', 'Stagnant'], vendor_late: ['外注遅れ', 'Vendor late'], idle_for: ['滞留時間', 'Idle for'],
  search: ['検索', 'Search'], all: ['すべて', 'All'], apply: ['適用', 'Apply'], remove: ['削除', 'Remove'], confirm: ['確定', 'Confirm'], device: ['読取端末', 'Device'],
  action: ['操作', 'Action'], time_jst: ['時刻（JST）', 'Time (JST)'], job_number: ['製番', 'Job number'], customer_order_number: ['客先注文番号', 'Customer order number'],
  not_linked_yet: ['未紐付け', 'Not linked yet'], link_a_tag: ['タグを紐付ける', 'Link a tag'], back_home: ['ホームに戻る', 'Back to Home'], back_orders: ['受注一覧に戻る', 'Back to Orders'],
  started: ['開始', 'Started'], finished: ['完了', 'Finished'], so_far: ['経過中', 'so far'], box_n: ['箱 {l}', 'box {l}'], box_title: ['箱 {l}', 'Box {l}'],
  n_days: ['{n}日', ['{n} day', '{n} days']], n_steps_left: ['残り{n}工程', ['{n} step left', '{n} steps left']],
  late_by_n: ['{n}日遅れ', ['Late by {n} day', 'Late by {n} days']], n_boxes: ['（{n}箱）', '({n} boxes)'],
  dur_dh: ['{d}日{h}時間', '{d}d {h}h'], dur_hm: ['{h}時間{m}分', '{h}h {m}m'], dur_m: ['{m}分', '{m}m'], dur_lt1: ['1分未満', '<1m'],
  // home
  home_title: ['ホーム', 'Home'], overdue_orders: ['納期遅れの受注', 'Overdue orders'], vendor_overdue: ['外注遅れ', 'Vendor overdue'],
  in_house_now: ['社内の現在状況', 'In-house now'], at_vendors: ['外注中', 'At vendors'], last_scan: ['最終スキャン（JST）', 'Last scan (JST)'],
  nothing_factory: ['現在、社内に作業中の箱はありません。', 'Nothing in the factory right now.'],
  out_since: ['外注出し日（JST）', 'Out since (JST)'], days_out: ['外注日数', 'Days out'], due_back: ['戻り予定日', 'Due back'], late_by: ['遅れ', 'Late by'],
  no_due_date: ['戻り予定なし', 'No due date'], nothing_vendor: ['外注中の箱はありません。', 'Nothing is out at vendors.'],
  // boxes page
  boxes_title: ['箱一覧', 'Boxes'], no_boxes_match: ['条件に一致する箱はありません。', 'No boxes match this filter.'],
  // orders
  orders_title: ['受注一覧', 'Orders'], sort_by: ['並び順', 'Sort by'], newest: ['新しい順', 'Newest'], overdue_only: ['納期遅れのみ', 'Overdue only'],
  items: ['品目', 'Items'], no_orders: ['受注が見つかりません。', 'No orders found.'], order_progress: ['作業中（{total}品目中{done}品目が完了）', 'In progress ({done}/{total} items complete)'],
  order_created: ['受注 {job} を登録しました。', 'Order {job} created.'], order_missing: ['受注が見つかりません。', 'Order not found.'],
  // order detail
  boxes_linked: ['箱: {t}個中{n}個を紐付け済み', 'Boxes: {n} of {t} linked'], box_tag: ['箱 {box}、タグ {tag}', 'Box {box}, tag {tag}'],
  no_tag_assigned: ['タグが割り当てられていません。', 'No tag assigned.'], box_not_linked: ['未紐付けの箱', 'Box not linked yet'], released: ['（解除済み）', '(released)'],
  event_history: ['履歴', 'Event history'], no_events: ['履歴はまだありません。', 'No events yet.'], waited: ['待ち時間 {d}', 'waited {d}'], due_back_d: ['戻り予定 {d}', 'due back {d}'],
  warn_skipped: ['工程を飛ばしています: {p}', 'Step skipped: {p}'], warn_notinroute: ['工程順にない工程のイベントです: {p}', 'Event for a process not in the route: {p}'],
  warn_unexpected: ['想定外の「{e}」イベントです（工程: {p}）', 'Unexpected {e} event for {p}'],
  // new order
  new_order: ['新規受注', 'New order'], part_name: ['品名', 'Part name'], quantity: ['数量', 'Quantity'], boxes_field: ['箱数', 'Boxes'], route: ['工程順', 'Route'],
  choose_process: ['工程を選択', 'Choose a process'], add_step: ['工程を追加', 'Add step'], add_item: ['品目を追加', 'Add another item'], create_order: ['受注を登録', 'Create order'],
  err_route: ['工程順に工程を1つ以上追加してください。', 'Add at least one step to the route.'], err_required: ['この項目は必須です。', 'This field is required.'],
  err_boxes: ['箱数は1以上にしてください。', 'Boxes must be at least 1.'], process_opt: ['{p}（{k}）', '{p} ({k})'], choose: ['選択してください', 'Choose'],
  // tags
  tags_title: ['タグ管理', 'Tags'], box_label: ['箱番号', 'Box label'], tag_uid: ['タグUID', 'Tag UID'], link_tag: ['タグを紐付ける', 'Link tag'], in_use: ['使用中', 'In use'],
  search_ph: ['品目、製番、品名、得意先、箱、タグ', 'Item, job, part, customer, box or tag'], since_jst: ['開始（JST）', 'Since (JST)'], release: ['タグを解除', 'Release'],
  replace_tag: ['タグを交換', 'Replace tag'], new_tag_uid: ['新しいタグUID', 'New tag UID'], mark_lost: ['旧タグを紛失扱いにする', 'Mark the old tag as lost'], replace: ['交換', 'Replace'],
  no_tags_in_use: ['使用中のタグはありません。', 'No tags in use.'], history: ['紐付け履歴', 'History'], from_jst: ['開始（JST）', 'From (JST)'], to_jst: ['終了（JST）', 'To (JST)'],
  no_history: ['履歴はまだありません。', 'No history yet.'], item_opt: ['{code} - {part}（{total}箱中{n}箱を紐付け済み）', '{code} - {part} ({n} of {total} boxes linked)'],
  box_nth: ['箱 {l}（{t}個中{n}個目）', 'Box {l} ({n} of {t})'], pick_item: ['品目を選択してください。', 'Choose an item.'],
  m_linked: ['タグ {tag} を箱 {box}（品目 {item}）に紐付けました。', 'Tag {tag} linked to box {box}, item {item}.'],
  m_released: ['タグ {tag} を箱 {box} から解除しました。', 'Tag {tag} released from box {box}.'],
  m_replaced: ['箱 {box} のタグ {old} を {new} に交換しました。', 'Tag {old} replaced by {new} in box {box}.'],
  e_required: ['箱番号とタグUIDは必須です。', 'Box label and tag UID are required.'], e_inactive: ['タグ {tag} は無効です。', 'Tag {tag} is inactive.'],
  e_all_linked: ['品目 {item} は、すでに{n}個の箱をすべて紐付け済みです。先に箱数を増やしてください。', 'Item {item} already has all {n} boxes linked. Raise its box count first.'],
  e_tag_in_use: ['タグ {tag} は品目 {item} で使用中です。', 'Tag {tag} is already in use for item {item}.'],
  e_box_in_use: ['箱 {box} は品目 {item} で使用中です。', 'Box {box} is already in use for item {item}.'],
  e_same_tag: ['新しいタグは旧タグと異なるものにしてください。', 'The new tag must differ from the old tag.'],
  // simulate
  sim_title: ['スキャンのシミュレーション', 'Simulate scan'], search_or_pick: ['タグを検索または選択', 'Search or pick a tag'],
  not_linked_tag: ['タグ {uid} は品目に紐付いていません。イベントは保存されますが、どの品目にも結び付きません。送信するには下の「その他の操作」を使ってください。',
    'Tag {uid} is not linked to any item: the event would be saved but match no item. Use "Other action" below to send it anyway.'],
  tag_info: ['タグ {uid}: 品目 {item}、箱 {box}、{part}', 'Tag {uid}: item {item}, box {box}, {part}'],
  box_finished: ['この箱は全工程を完了しています。スキャンする操作はありません。', 'This box has finished its whole route. Nothing left to scan.'],
  what_happened: ['実施した操作', 'What happened'], other_action: ['その他の操作（誤操作のテスト）', 'Other action (test a mistake)'], record_event: ['イベントを記録', 'Record event'],
  last_10: ['直近10件のイベント', 'Last 10 events'],
  m_saved: ['保存しました: 箱 {box}、品目 {item}、状態: {status}', 'Saved: box {box}, item {item}, status: {status}'],
  m_nomatch: ['イベントを保存しましたが、タグ {uid} に該当する品目がありません。', 'Event saved, but tag {uid} matches no item.'],
  // board
  board_title: ['工程ボード', 'Process board'], in_house: ['社内', 'In-house'], outsourced: ['外注', 'Outsourced'], running: ['稼働中', 'Running'], waiting: ['待機中', 'Waiting'],
  queue_building: ['待ち増加', 'Queue building'], no_in_house: ['社内工程がありません。', 'No in-house processes.'], no_outsourced: ['外注工程がありません。', 'No outsourced processes.'],
  // priorities
  prio_title: ['優先度', 'Priorities'], days_left: ['残日数', 'Days left'], slack: ['余裕日数', 'Slack'], risk: ['リスク', 'Risk'], reasons: ['理由', 'Reasons'],
  nothing_attention: ['対応が必要なものはありません。', 'Nothing needs attention.'],
  r_due_in: ['納期まであと{days}、{steps}', 'Due in {days}, {steps}'], r_overdue_by: ['納期を{days}超過、{steps}', 'Overdue by {days}, {steps}'],
  r_vendor_late: ['外注 {n}日遅れ', ['Vendor {n} day late', 'Vendor {n} days late']], r_warnings: ['警告 {n}件', ['{n} warning', '{n} warnings']],
};

const I18N = (() => {
  let lang = 'ja';
  const fill = (s, p) => String(s).replace(/\{(\w+)\}/g, (m, k) => (p && p[k] !== undefined ? p[k] : m));
  // Translate a key; params fill {name}; a numeric p.n picks the English plural
  const t = (key, p) => {
    const e = TEXTS[key];
    if (!e) return key;
    let v = lang === 'ja' ? e[0] : e[1];
    if (Array.isArray(v)) v = v[p && p.n === 1 ? 0 : 1];
    return fill(v, p);
  };
  return { t, get lang() { return lang; }, set lang(v) { lang = v === 'en' ? 'en' : 'ja'; } };
})();
const t = I18N.t;
