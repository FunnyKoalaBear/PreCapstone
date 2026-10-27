# Performance: how status is derived for many items

Status is never stored; it is replayed from Events. To keep pages from running several queries per item, the replay is batched.

## Batching

- `tracking/status.py` `item_statuses(items)` returns `{item id: ItemStatus}`. `item_status(item)` is `item_statuses([item])[item.pk]`; there is one implementation.
- Items are handled in chunks of `STATUS_CHUNK` (1000). Per chunk it runs a fixed set of queries: route steps, assignments, scan events of every tag involved, and the live CANCELLED / SHIPPED events. Voids are an SQL subquery, so a void is found whatever chunk its target is in.
- Events are grouped by tag UID in Python and cut into each assignment's window `[start, end)`, then replayed with the unchanged `_replay` / `_roll_up`.
- Queries per page = a fixed number per chunk. Up to 1000 items that is one chunk; 5000 items is five.
- `iter_statuses(items, size)` yields `(item, status)` in small batches for loops that stop early (matrix, "previous runs").
- A page that needs statuses in two places derives them once and passes them in (`active_boxes(items=, statuses=)`, `matrix(items=, statuses=)`, `order_summary(order, statuses)`).
- The home page skips items with a live CANCELLED event in SQL (`overview.open_items`). Shipped items cannot be skipped: SHIPPED only counts once the boxes replay to COMPLETE.
- No item is dropped: if the number of results differs from the items asked for, or one item fails, `ItemStatusError` names the item.

## The reference implementation

The old per-item code lives in `tracking/tests_reference.py` as `reference_item_status`. The app never calls it. It is the oracle the tests compare against.

Tests (`tracking/tests_scale.py`):
- every state, edge cases (no steps, no assignments, box_count above assignments, reused tags, overlapping windows, blank UIDs, voids outside the chunk, boundary and same-time events, several chunks);
- 30 fixed seeds of random histories (the seed is in the failure message);
- all `seed_demo` and `seed_scale` items;
- query counts that must not change from 3 to 30 items;
- home counters against totals from the full list;
- 1000 items: same query count as 20 items, same rows as the reference, time limit.

## Scale data

`seed_scale` fills a SCRATCH database (the name must start with `scratch` or `test_`; it refuses anything else). It is not demo data.

```
createdb scratch_scale   # or any name starting with scratch
$env:DB_NAME='scratch_scale'; python manage.py migrate
python manage.py seed_scale --items 5000 --events 100000   # running it again adds more
python manage.py seed_scale --verify                         # every item against the reference
```

Scans are bulk inserted (`record_event` costs about 25 ms each); `--slow` uses `record_event`. Tests can run faster with `--settings=config.test_settings` (cheap password hashing).

## Indexes

`Event(tag_uid, occurred_at)` (migration 0007): a lookup of one tag's events drops from a 18 ms seq scan to 0.14 ms at 100,000 events. The batch lookup over a whole chunk is dominated by Python, not SQL.
