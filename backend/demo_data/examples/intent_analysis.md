
Question: Which suppliers were late more than three times in weeks 1 to 13?
Output: {"disposition": "retrieve", "ambiguous": [{"term": "late", "alternatives": [{"metric": "late_by_delivery"}, {"metric": "late_by_promise"}]}], "readings": [{"label": "late_by_delivery", "asked": "Which suppliers were late more than three times in weeks 1 to 13?", "plan": {"SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"}, {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_count"}], "FROM": "commitments", "WHERE": {"AND": [{"BETWEEN": {"col": "commitments.week", "from": {"value": 1}, "to": {"value": 13}}}, {"EQ": [{"metric": "late_by_delivery"}, {"value": true}]}]}, "GROUP_BY": ["supplier_id"], "HAVING": [{"agg": "late_count", "op": "GT", "value": 3}], "ORDER_BY": [{"by": "late_count", "dir": "DESC"}]}}, {"label": "late_by_promise", "asked": "Which suppliers were late more than three times in weeks 1 to 13?", "plan": {"SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"}, {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_count"}], "FROM": "commitments", "WHERE": {"AND": [{"BETWEEN": {"col": "commitments.week", "from": {"value": 1}, "to": {"value": 13}}}, {"EQ": [{"metric": "late_by_promise"}, {"value": true}]}]}, "GROUP_BY": ["supplier_id"], "HAVING": [{"agg": "late_count", "op": "GT", "value": 3}], "ORDER_BY": [{"by": "late_count", "dir": "DESC"}]}}]}

Question: What is the value of open orders with S-014?
Output: {"disposition": "retrieve", "readings": [{"label": "open_value", "asked": "What is the value of open orders with S-014?", "plan": {"SELECT": [{"expr": {"func": "SUM", "arg": {"col": "commitments.line_value"}}, "as": "open_value"}], "FROM": "commitments", "JOIN": [{"rel": "commitment_order", "type": "INNER"}], "WHERE": {"AND": [{"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-014"}]}, {"EQ": [{"col": "purchase_orders.status"}, {"value": "open"}]}]}}}]}

Question: What is on hand for P-10037, and who is the buyer contact for it?
Output: {"disposition": "retrieve", "readings": [{"label": "on_hand", "asked": "What is on hand for P-10037", "plan": {"SELECT": [{"expr": {"col": "parts.part_id"}, "as": "part_id"}, {"expr": {"col": "parts.on_hand"}, "as": "on_hand"}, {"expr": {"col": "parts.bin_location"}, "as": "bin_location"}], "FROM": "parts", "WHERE": {"EQ": [{"col": "parts.part_id"}, {"value": "P-10037"}]}}}], "unresolved": [{"asked": "who is the buyer contact for it", "needs": {"kind": "no_such_column", "entity": "parts"}}]}

Question: What did S-014 deliver in March?
Output: {"disposition": "retrieve", "readings": [{"label": "delivered_in_march", "asked": "What did S-014 deliver in March?", "plan": {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}, {"expr": {"col": "commitments.part_id"}, "as": "part_id"}, {"expr": {"col": "commitments.delivered_at"}, "as": "delivered_at"}], "FROM": "commitments", "WHERE": {"AND": [{"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-014"}]}, {"BETWEEN": {"col": "commitments.delivered_at", "from": {"value": "2026-03-01"}, "to": {"value": "2026-03-31"}}}]}, "ORDER_BY": [{"by": "delivered_at", "dir": "ASC"}]}}]}

Question: What reason was recorded for the delay on PO-87006?
Output: {"disposition": "retrieve", "readings": [{"label": "recorded_reason", "asked": "What reason was recorded for the delay on PO-87006?", "plan": {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}, {"expr": {"col": "commitments.part_id"}, "as": "part_id"}, {"expr": {"col": "commitments.delivered_at"}, "as": "delivered_at"}, {"expr": {"col": "commitments.delay_reason"}, "as": "delay_reason"}], "FROM": "commitments", "WHERE": {"EQ": [{"col": "commitments.po_id"}, {"value": "PO-87006"}]}}}]}

Question: Which suppliers delivered late in April, and which of them have open orders worth more than 100000 in total?
Output: {"disposition": "needs_clarification"}

Question: Which parts are below their reorder point, and which open lines are for them?
Output: {"disposition": "retrieve", "readings": [{"label": "below_reorder", "asked": "Which parts are below their reorder point", "plan": {"SELECT": [{"expr": {"col": "parts.part_id"}, "as": "part_id"}], "FROM": "parts", "WHERE": {"EQ": [{"metric": "below_reorder_point"}, {"value": true}]}}}, {"label": "open_lines", "asked": "which open lines are for them", "plan": {"SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "commitment_id"}, {"expr": {"col": "commitments.part_id"}, "as": "part_id"}], "FROM": "commitments", "WHERE": {"AND": [{"IS_NULL": {"col": "commitments.delivered_at"}}, {"IN_READING": {"col": "commitments.part_id", "reading": "below_reorder", "as": "part_id"}}]}, "ORDER_BY": [{"by": "part_id", "dir": "ASC"}]}}]}

Question: Which parts had lines in weeks 14 to 26, none of them delivered after the requested date?
Output: {"disposition": "retrieve", "readings": [{"label": "delivered_after", "asked": "delivered after the requested date", "plan": {"SELECT": [{"expr": {"col": "commitments.part_id"}, "as": "part_id"}], "FROM": "commitments", "WHERE": {"AND": [{"BETWEEN": {"col": "commitments.week", "from": {"value": 14}, "to": {"value": 26}}}, {"EQ": [{"metric": "late_by_delivery"}, {"value": true}]}]}}}, {"label": "parts_none_after", "asked": "Which parts had lines in weeks 14 to 26, none of them", "plan": {"SELECT": [{"expr": {"col": "commitments.part_id"}, "as": "part_id"}, {"expr": {"func": "COUNT", "arg": "*"}, "as": "lines"}], "FROM": "commitments", "WHERE": {"AND": [{"BETWEEN": {"col": "commitments.week", "from": {"value": 14}, "to": {"value": 26}}}, {"NOT_IN_READING": {"col": "commitments.part_id", "reading": "delivered_after", "as": "part_id"}}]}, "GROUP_BY": ["part_id"], "ORDER_BY": [{"by": "part_id", "dir": "ASC"}]}}]}

Question: Which of my open lines are due in October?
Output: {"disposition": "retrieve", "readings": [{"label": "my_open_lines", "asked": "Which of my open lines are due in October?", "plan": {"SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "commitment_id"}, {"expr": {"col": "commitments.po_id"}, "as": "po_id"}, {"expr": {"col": "commitments.due_date"}, "as": "due_date"}], "FROM": "commitments", "WHERE": {"AND": [{"EQ": [{"col": "commitments.planner_id"}, {"me": true}]}, {"IS_NULL": {"col": "commitments.delivered_at"}}, {"BETWEEN": {"col": "commitments.due_date", "from": {"value": "2026-10-01"}, "to": {"value": "2026-10-31"}}}]}, "ORDER_BY": [{"by": "due_date", "dir": "ASC"}]}}]}

Question: What did Apex deliver in March?
Output: {"disposition": "retrieve", "readings": [{"label": "delivered_in_march", "asked": "What did Apex deliver in March?", "plan": {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}, {"expr": {"col": "commitments.part_id"}, "as": "part_id"}, {"expr": {"col": "commitments.delivered_at"}, "as": "delivered_at"}], "FROM": "commitments", "JOIN": [{"rel": "commitment_supplier", "type": "INNER"}], "WHERE": {"AND": [{"MATCHES": {"col": "suppliers.name", "term": "Apex"}}, {"BETWEEN": {"col": "commitments.delivered_at", "from": {"value": "2026-03-01"}, "to": {"value": "2026-03-31"}}}]}, "ORDER_BY": [{"by": "delivered_at", "dir": "ASC"}]}}]}

Question: Why is S-014 late so often?
Output: {"disposition": "refer_to_investigation"}

Question: What caused PO-87006 to arrive late?
Output: {"disposition": "refer_to_investigation"}

Question: Update the promised date of PO-87006 to 20 October.
Output: {"disposition": "out_of_scope_action"}

Question: And for the other one?
Output: {"disposition": "needs_clarification"}