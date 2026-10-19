
Question: Which suppliers delivered late in September?
Records:
[late_by_delivery] examined: purchase order lines where delivery date between '2026-09-01' and '2026-09-30' and delivered after the requested date · grouped by supplier · computed the number of records
[late_by_delivery] late_by_delivery.total = 2 rows matched, 2 shown
[late_by_delivery] row 1: late_by_delivery.supplier_id = S-038, late_by_delivery.late_lines = 6
[late_by_delivery] row 2: late_by_delivery.supplier_id = S-014, late_by_delivery.late_lines = 5
[late_by_promise] examined: purchase order lines where delivery date between '2026-09-01' and '2026-09-30' and the supplier committed to a date after the requested date · grouped by supplier · computed the number of records
[late_by_promise] late_by_promise.total = 1 rows matched, 1 shown
[late_by_promise] row 1: late_by_promise.supplier_id = S-014, late_by_promise.late_lines = 4
Output: {"readings": [{"label": "late_by_delivery", "sentences": [{"text": "{late_by_delivery.supplier_id#1} had the most lines delivered after the requested date in September, {late_by_delivery.late_lines#1}."}, {"text": "{late_by_delivery.supplier_id#2} followed with {late_by_delivery.late_lines#2}."}]}, {"label": "late_by_promise", "sentences": [{"text": "{late_by_promise.supplier_id#1} had {late_by_promise.late_lines#1} lines delivered in September whose promised date was after the requested date."}]}]}

Question: Which lines were delivered after the requested date in August?
Records:
[late_lines] examined: purchase order lines where delivery date between '2026-08-01' and '2026-08-31' and delivered after the requested date
[late_lines] late_lines.total = 64 rows matched, 30 shown
[late_lines] row 1: late_lines.commitment_id = C-100512, late_lines.delivered_at = 2026-08-02
[late_lines] row 2: late_lines.commitment_id = C-100530, late_lines.delivered_at = 2026-08-03
[late_lines] row 3: late_lines.commitment_id = C-100547, late_lines.delivered_at = 2026-08-03
Output: {"readings": [{"label": "late_lines", "sentences": [{"text": "{late_lines.total} lines were delivered after the requested date in August, among them {late_lines.commitment_id#1}, {late_lines.commitment_id#2} and {late_lines.commitment_id#3}."}]}]}

Question: Which suppliers were late in weeks 1 to 4?
Records:
[late_by_delivery] examined: purchase order lines where week between 1 and 4 and delivered after the requested date · grouped by supplier · computed the number of records
[late_by_delivery] late_by_delivery.total = 3 rows matched, 3 shown
[late_by_delivery] row 1: late_by_delivery.supplier_id = S-021, late_by_delivery.late_lines = 9
[late_by_delivery] row 2: late_by_delivery.supplier_id = S-007, late_by_delivery.late_lines = 9 (same late_by_delivery.late_lines as row 1)
[late_by_delivery] row 3: late_by_delivery.supplier_id = S-030, late_by_delivery.late_lines = 4
Output: {"readings": [{"label": "late_by_delivery", "sentences": [{"text": "{late_by_delivery.supplier_id#1} and {late_by_delivery.supplier_id#2} each had {late_by_delivery.late_lines#1} lines delivered after the requested date in weeks 1 to 4."}, {"text": "{late_by_delivery.supplier_id#3} had {late_by_delivery.late_lines#3}."}]}]}

Question: What reason was recorded for the delay on PO-87006?
Records:
[recorded_reason] examined: purchase order lines where purchase order = 'PO-87006'
[recorded_reason] recorded_reason.total = 2 rows matched, 2 shown
[recorded_reason] row 1: recorded_reason.po_id = PO-87006, recorded_reason.part_id = P-10037, recorded_reason.delivered_at = 2026-02-20, recorded_reason.delay_reason = Supplier capacity constraint
[recorded_reason] row 2: recorded_reason.po_id = PO-87006, recorded_reason.part_id = P-12405, recorded_reason.delivered_at = 2026-02-11, recorded_reason.delay_reason = None
Output: {"readings": [{"label": "recorded_reason", "sentences": [{"text": "On {recorded_reason.po_id#1}, {recorded_reason.part_id#1} was delivered on {recorded_reason.delivered_at#1}. Reason recorded: {recorded_reason.delay_reason#1}."}, {"text": "For {recorded_reason.part_id#2}, delivered on {recorded_reason.delivered_at#2}, no reason is recorded."}]}]}