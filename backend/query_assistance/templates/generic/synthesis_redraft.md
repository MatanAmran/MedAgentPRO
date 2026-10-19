---
prompt: synthesis_redraft
version: 9
output_schema: synthesis
---
You rewrite the answer to a question about [[subject_records]]. A check has removed every assertion the records did not support. Write the answer again from the assertions that were kept, and from nothing else.

You do not write values. Where a sentence states a value, write a reference to it: {label.alias#row}, where row is the row number in the records. Code replaces each reference with the value from that row. How many rows a reading matched is {label.total}, with no row number.

Rules:
1. Return JSON only, matching the output schema. No explanation.
2. Use only the kept assertions. You may join or reword them so that the answer reads well. Do not add a fact that no kept assertion states.
3. Keep each reading separate. A reading with no kept assertion gets no entry.
4. Every sentence holds at least one reference. Every identifier, number, date and text value comes from a reference. Never type a digit yourself, except one the question itself contains, such as a week or a date it names.
5. An identifier that appears in the records is always written as a reference to its row, even where the question or a kept assertion names it.
6. Do not judge whether a value is unusual, do not give a cause and do not recommend anything. A recorded reason is quoted as recorded.
7. Write the answer in English, whatever language the question is in.
8. Where the question is given both as posed and as rewritten from the conversation, answer the rewritten one. Use the question as posed only to read what the person meant.
9. A row marked (same label.alias as row n) holds the same value in the field the rows are ordered by as row n. Do not add a place (most, highest, first, top, followed, then, next) between rows that hold the same value, even to join two kept assertions.
10. A sentence that refers to a reading's rows keeps the condition its kept assertion states: the measure, the filter and the period. A sentence that continues the previous one about the same reading need not repeat it. Never describe the rows by anything other than their condition, such as "are in the records", "are listed" or "also include".

Examples:
[[examples]]

{{question_block}}
Records:
{{records}}
Kept:
{{kept}}
{{feedback}}Output:
