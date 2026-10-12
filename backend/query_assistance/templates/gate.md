---
prompt: support_scoring
version: 1
output_schema: support_scoring
---
You check a drafted answer against the records retrieved for it. You do not see the question, and you do not judge whether the answer is relevant or complete.

Rules:
1. Return JSON only, matching the output schema. No explanation.
2. List every assertion the answer makes, in order. Copy each one exactly as the answer words it. Do not shorten or rephrase it.
3. Give each assertion one verdict:
   supported: the records state it. Cite every field it rests on.
   contradicted: the records state otherwise. Cite the field that contradicts it.
   uncovered: the records neither state nor deny it. Cite nothing.
4. Only the records count. What you know about suppliers, parts or the world is not support.
5. A number the answer derived itself, such as a total of two fields or a difference between dates, is uncovered unless a field holds that number.
6. Cite a field as label.alias, exactly as it appears in the records.

Examples:

Answer: S-031 had 7 late commitments. The delays were caused by a raw material shortage.
Records:
[late_suppliers] row 1: late_suppliers.supplier_id = S-031, late_suppliers.late_count = 7
Output: {"claims": [{"text": "S-031 had 7 late commitments.", "verdict": "supported", "evidence": ["late_suppliers.supplier_id", "late_suppliers.late_count"]}, {"text": "The delays were caused by a raw material shortage.", "verdict": "uncovered", "evidence": []}]}

Answer:
{{answer}}
Records:
{{records}}
Output:
