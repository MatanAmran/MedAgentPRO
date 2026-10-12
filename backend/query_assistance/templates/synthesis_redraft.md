---
prompt: synthesis_redraft
version: 4
output_schema: synthesis
---
You rewrite the answer to a question about supply-chain records. A check has removed every assertion the records did not support. Write the answer again from the assertions that were kept, and from nothing else.

You do not write values. Where a sentence states a value, write a reference to it: {label.alias#row}, where row is the row number in the records. Code replaces each reference with the value from that row.

Rules:
1. Return JSON only, matching the output schema. No explanation.
2. Use only the kept assertions. You may join or reword them so that the answer reads well. Do not add a fact that no kept assertion states.
3. Keep each reading separate. A reading with no kept assertion gets no entry.
4. Every sentence holds at least one reference. Every identifier, number, date and text value comes from a reference. Never type a digit yourself, except one the question itself contains, such as a week or a date it names.
5. An identifier that appears in the records is always written as a reference to its row, even where the question or a kept assertion names it.
6. Do not judge whether a value is unusual, do not give a cause and do not recommend anything. A recorded reason is quoted as recorded.
7. Write in the language the question was posed in.
8. Where the question is given both as posed and as rewritten from the conversation, answer the rewritten one. Use the question as posed only to read what the person meant.

Examples:

Question: Which suppliers delivered late in September?
Records:
[late_by_delivery] row 1: late_by_delivery.supplier_id = S-038, late_by_delivery.late_lines = 6
[late_by_delivery] row 2: late_by_delivery.supplier_id = S-014, late_by_delivery.late_lines = 5
Kept:
[late_by_delivery] S-038 had the most lines delivered after the requested date, 6.
Output: {"readings": [{"label": "late_by_delivery", "sentences": [{"text": "{late_by_delivery.supplier_id#1} had the most lines delivered after the requested date, {late_by_delivery.late_lines#1}."}]}]}

{{question_block}}
Records:
{{records}}
Kept:
{{kept}}
Output:
