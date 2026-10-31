# Demo data (all invented)

Our own names and codes. None of the company's real codes, names or customers are used. Files are UTF-8 with BOM (Excel opens them correctly) except `orders_demo_sjis.csv`.

| File | Used by |
|---|---|
| `codes.csv` | F73 / F78: 5-digit codes. INTERNAL 00000-00099, SUPPLIER and VENDOR 00100-00429 |
| `processes.csv` | F78: the processes and their kind (MATERIAL, MACHINING, OUTSOURCED) |
| `orders_demo.csv` | F42: 10 items in 7 orders. Should import with no errors |
| `orders_demo_sjis.csv` | F42: same content in Shift-JIS (cp932). The importer must read it |
| `orders_demo_errors.csv` | F42: one row per error type (table below) |
| `workers_machines.csv` | F65 / F78: demo workers, machines and defect causes (`kind` = WORKER, MACHINE or CAUSE; `active` = yes or no, one inactive entry of each) |

## Order CSV format
Header row required, comma separated. One row = one item. Rows with the same customer and customer_order_number become one order. An empty customer_order_number makes its own order.

`customer, customer_order_number, due_date, part_name, quantity, material, notes, route`

Optional columns (anywhere after the header, files without them import as before): `part_number` (50 characters, stored upper-case), `drawing_number` (50), `revision` (10).

- `due_date`: YYYY-MM-DD or YYYY/MM/DD.
- `route`: steps separated by `|`, each `process:code` or `process:code:due_date`, e.g. `Material:00101|Lathe:00012|Plating:00131:2026-11-02|Inspection:00020`.
- Process is matched by name or Japanese name. The code must exist, and its kind must fit the process: MATERIAL needs SUPPLIER, MACHINING needs INTERNAL, OUTSOURCED needs VENDOR.
- Import all-or-nothing: show a preview with every row's result, and import only when there are no errors.

## Expected results of `orders_demo_errors.csv`
Import `orders_demo.csv` first (row 10 duplicates PO-7101).

| Row | Customer order no. | Expected result |
|---|---|---|
| 2 | PO-9001 | Unknown customer (OK if "create new customers" is ticked) |
| 3 | PO-9002 | Invalid due date |
| 4 | PO-9003 | Quantity must be at least 1 |
| 5 | PO-9004 | Unknown code 00999 |
| 6 | PO-9005 | Code kind does not fit: Plating needs a VENDOR code |
| 7 | PO-9006 | Part name missing |
| 8 | PO-9007 | Route required |
| 9 | PO-9008 | Malformed route step |
| 10 | PO-7101 | Duplicate customer order number |
| 11 | PO-9010 | Unknown process |
| 12 | PO-9011 | OK (the only valid row, still not imported because of the others) |

## F65 seed guidance
Record results for about 15 finished steps. Quantity done equals the item quantity. About one step in ten has 1-2 defects, so the defect summary is not flat. Spread the work across the workers and machines in `workers_machines.csv`.

## seed_demo (history data)
`python manage.py seed_demo --reset` (DEBUG only) builds the 12 scenario orders, then about 100 more from `_demo_bulk.py`: 75 shipped orders spread over 12 months (on-time rate improves with a dip), 3 cancelled, and open orders in every state (not started, waiting, in progress, at vendor, paused, ready to ship), with due dates from 6 days overdue to 40 days ahead. Routes have 3 to 8 steps. Plating, Grinding, Inspection and Heat treatment run slower than planned, so Plan vs actual shows bottlenecks. Results carry defects per process. A fixed random seed makes it repeatable; all times are relative to now.
Defect investigation: part SPC-220 fails far more on Machining centre 2, and the newer worker Nakamura Mio fails far more on part ARM-510 (Assembly). Some defective results carry a note, and 8 carry a small generated photo in `MEDIA_ROOT/defects/demo/` (`media/` is git-ignored; `seed_demo --reset` removes that folder and writes it again).
