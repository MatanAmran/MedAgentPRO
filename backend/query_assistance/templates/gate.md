---
prompt: support_scoring
version: 4
output_schema: support_scoring
---
You check a drafted answer against the records retrieved for it. You do not see the question, and you do not judge whether the answer is relevant or complete.

The answer is given as numbered sentences. Each sentence carries, in brackets, the label of the records it was drafted from.

Rules:
1. Return JSON only, matching the output schema. No explanation.
2. Give one assertion per sentence, in order, and copy the whole sentence exactly as the answer words it. Split a sentence into parts only where the parts would get different verdicts, and then copy each part exactly. Do not shorten or rephrase.
3. Give each assertion the number of the sentence it comes from. Every sentence makes at least one assertion, so every sentence number appears at least once.
4. Give each assertion one verdict:
   supported: the records state it. Cite every field it rests on.
   contradicted: the records state otherwise. Cite the field that contradicts it.
   uncovered: the records neither state nor deny it. Cite nothing.
5. Only the records count. What you know about suppliers, parts or the world is not support.
6. A number the answer derived itself, such as a total of two fields or a difference between dates, is uncovered unless a field holds that number.
7. Cite a field as label.alias, exactly as it appears in the records.
8. Words of place or order (most, highest, first, top, followed, then, next) are assertions about the values. They are supported only where the cited values show that order. A row marked (same label.alias as row n) holds the same value in that field as row n: a place named between rows that hold the same value is contradicted. Cite the field that holds the equal values.

Examples:

Answer:
1. [late_suppliers] S-031 had 7 late commitments.
2. [late_suppliers] The delays were caused by a raw material shortage.
Records:
[late_suppliers] row 1: late_suppliers.supplier_id = S-031, late_suppliers.late_count = 7
Output: {"claims": [{"sentence": 1, "text": "S-031 had 7 late commitments.", "verdict": "supported", "evidence": ["late_suppliers.supplier_id", "late_suppliers.late_count"]}, {"sentence": 2, "text": "The delays were caused by a raw material shortage.", "verdict": "uncovered", "evidence": []}]}

Answer:
1. [late_by_delivery] S-021 had the most lines delivered after the requested date, 9.
2. [late_by_delivery] S-007 also had 9.
Records:
[late_by_delivery] row 1: late_by_delivery.supplier_id = S-021, late_by_delivery.late_lines = 9
[late_by_delivery] row 2: late_by_delivery.supplier_id = S-007, late_by_delivery.late_lines = 9 (same late_by_delivery.late_lines as row 1)
Output: {"claims": [{"sentence": 1, "text": "S-021 had the most lines delivered after the requested date, 9.", "verdict": "contradicted", "evidence": ["late_by_delivery.late_lines"]}, {"sentence": 2, "text": "S-007 also had 9.", "verdict": "supported", "evidence": ["late_by_delivery.supplier_id", "late_by_delivery.late_lines"]}]}

Answer:
{{answer}}
Records:
{{records}}
{{feedback}}Output:
