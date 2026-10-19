
Answer:
1. [late_suppliers] S-031 had 7 late commitments.
2. [late_suppliers] The delays were caused by a raw material shortage.
Records:
[late_suppliers] row 1: late_suppliers.supplier_id = S-031, late_suppliers.late_count = 7
Output: {"claims": [{"sentence": 1, "text": "S-031 had 7 late commitments.", "verdict": "supported", "evidence": ["late_suppliers.supplier_id", "late_suppliers.late_count"]}, {"sentence": 2, "text": "The delays were caused by a raw material shortage.", "verdict": "uncovered", "evidence": []}]}

Answer:
1. [late_by_delivery] S-021 had the most lines delivered after the requested date in weeks 1 to 4, 9.
2. [late_by_delivery] S-007 also had 9.
Records:
[late_by_delivery] row 1: late_by_delivery.supplier_id = S-021, late_by_delivery.late_lines = 9
[late_by_delivery] row 2: late_by_delivery.supplier_id = S-007, late_by_delivery.late_lines = 9 (same late_by_delivery.late_lines as row 1)
Output: {"claims": [{"sentence": 1, "text": "S-021 had the most lines delivered after the requested date in weeks 1 to 4, 9.", "verdict": "contradicted", "evidence": ["late_by_delivery.late_lines"]}, {"sentence": 2, "text": "S-007 also had 9.", "verdict": "supported", "evidence": ["late_by_delivery.supplier_id", "late_by_delivery.late_lines"]}]}

Answer:
1. [late_lines] 64 lines were delivered after the requested date in August, among them C-100512.
2. [late_lines] C-100530 is also listed in the records.
Records:
[late_lines] examined: purchase order lines where delivery date between '2026-08-01' and '2026-08-31' and delivered after the requested date
[late_lines] late_lines.total = 64 rows matched, 30 shown
[late_lines] row 1: late_lines.commitment_id = C-100512, late_lines.delivered_at = 2026-08-02
[late_lines] row 2: late_lines.commitment_id = C-100530, late_lines.delivered_at = 2026-08-03
Output: {"claims": [{"sentence": 1, "text": "64 lines were delivered after the requested date in August, among them C-100512.", "verdict": "supported", "evidence": ["late_lines.total", "late_lines.commitment_id"]}, {"sentence": 2, "text": "C-100530 is also listed in the records.", "verdict": "uncovered", "evidence": []}]}