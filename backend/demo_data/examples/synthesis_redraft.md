
Question: Which suppliers delivered late in September?
Records:
[late_by_delivery] late_by_delivery.total = 2 groups matched (one per supplier), 2 shown
[late_by_delivery] row 1: late_by_delivery.supplier_id = S-038, late_by_delivery.late_lines = 6
[late_by_delivery] row 2: late_by_delivery.supplier_id = S-014, late_by_delivery.late_lines = 5
Kept:
[late_by_delivery] S-038 had the most lines delivered after the requested date in September, 6.
Output: {"readings": [{"label": "late_by_delivery", "sentences": [{"text": "{late_by_delivery.supplier_id#1} had the most lines delivered after the requested date in September, {late_by_delivery.late_lines#1}."}]}]}