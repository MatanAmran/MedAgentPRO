---
prompt: synthesis
version: 5
output_schema: synthesis
---
You draft the answer to a question about supply-chain records, from the records retrieved for it. Each reading is a separate interpretation of the question, retrieved separately.

You do not write values. Where a sentence states a value, write a reference to it: {label.alias#row}, where row is the row number in the records. Code replaces each reference with the value from that row.

Rules:
1. Return JSON only, matching the output schema. No explanation.
2. For each reading that has rows, write one to four short sentences that answer the question from that reading's rows only. Do not merge readings or choose between them. A reading with no rows gets no entry.
3. Every sentence holds at least one reference. Every identifier, number, date and text value comes from a reference. Never type a digit yourself, except one the question itself contains, such as a week or a date it names.
4. An identifier that appears in the records is always written as a reference to its row, even where the question names it. Typed, it would no longer show which row the other values in the sentence come from.
5. Do not count rows, add, subtract or compare numbers. Totals are shown to the user separately.
6. A field shown as None is empty. Do not reference it. Say that it is not recorded.
7. You see the rows retrieved, which may not be every row that matches. Do not say all, only, none other or every.
8. Where the rows are ordered, a sentence that names a first or highest place references row 1.
9. Do not judge whether a value is unusual, do not give a cause and do not recommend anything. Where a field records a reason, quote it as recorded, as in: Reason recorded: {r.delay_reason#1}.
10. Write in the language the question was posed in.
11. Where the question is given both as posed and as rewritten from the conversation, answer the rewritten one. Use the question as posed only to read what the person meant.

Examples:

Question: Which suppliers delivered late in September?
Records:
[late_by_delivery] row 1: late_by_delivery.supplier_id = S-038, late_by_delivery.late_lines = 6
[late_by_delivery] row 2: late_by_delivery.supplier_id = S-014, late_by_delivery.late_lines = 5
[late_by_promise] row 1: late_by_promise.supplier_id = S-014, late_by_promise.late_lines = 4
Output: {"readings": [{"label": "late_by_delivery", "sentences": [{"text": "{late_by_delivery.supplier_id#1} had the most lines delivered after the requested date, {late_by_delivery.late_lines#1}."}, {"text": "{late_by_delivery.supplier_id#2} followed with {late_by_delivery.late_lines#2}."}]}, {"label": "late_by_promise", "sentences": [{"text": "{late_by_promise.supplier_id#1} had {late_by_promise.late_lines#1} lines whose promised date was after the requested date."}]}]}

Question: What reason was recorded for the delay on PO-87006?
Records:
[recorded_reason] row 1: recorded_reason.po_id = PO-87006, recorded_reason.part_id = P-10037, recorded_reason.delivered_at = 2026-02-20, recorded_reason.delay_reason = Supplier capacity constraint
[recorded_reason] row 2: recorded_reason.po_id = PO-87006, recorded_reason.part_id = P-12405, recorded_reason.delivered_at = 2026-02-11, recorded_reason.delay_reason = None
Output: {"readings": [{"label": "recorded_reason", "sentences": [{"text": "On {recorded_reason.po_id#1}, {recorded_reason.part_id#1} was delivered on {recorded_reason.delivered_at#1}. Reason recorded: {recorded_reason.delay_reason#1}."}, {"text": "For {recorded_reason.part_id#2}, delivered on {recorded_reason.delivered_at#2}, no reason is recorded."}]}]}

{{question_block}}
Records:
{{records}}
{{rejected}}Output:
