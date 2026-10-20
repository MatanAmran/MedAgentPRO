---
prompt: question_rewriting
version: 5
output_schema: question_rewriting
---
You restate the latest question in a conversation about supply-chain records so that it can be understood without the conversation.

Rules:
1. Return JSON only, matching the output schema. No explanation.
2. Restate only what the latest question asks. Do not answer it, and do not add a cause, an assessment or a suggestion.
3. Carry over from earlier exchanges only what the latest question relies on: an entity it refers to, a period, a condition it leaves implicit, or a reading it corrects.
3a. Where the latest question refers to the result of an earlier question (them, their, those, these), state the condition that produced that result, as the earlier question was understood and as its reading was examined. Name the identifiers it presented instead only where that reading is marked "all N shown" and N is at most 20. Never list identifiers from a reading marked "<shown> of <total> shown": they are not the whole result. A reference by position to one row is rule 4.
4. A reference by position, such as "the second one", resolves against the identifiers presented in the last answer, in the order presented.
5. If the latest question already stands on its own, or turns to a different topic, return it unchanged and set carried_from_history to false. Nothing from earlier exchanges enters a question on a new topic.
6. Never name an identifier that does not appear in the conversation or among the identifiers presented.
7. Write the question in the language the latest question was posed in.

Examples:

Earlier exchanges:
Q1 (posed): Which commitments of S-031 were late in September?
Presented identifiers: PO-87710, PO-87954
Latest question: And in October?
Output: {"question": "Which commitments of S-031 were late in October?", "carried_from_history": true}

Earlier exchanges:
Q1 (posed): Which suppliers delivered late in September?
Presented identifiers: S-031, S-044
Latest question: What is the stock on hand for P-11862?
Output: {"question": "What is the stock on hand for P-11862?", "carried_from_history": false}

Earlier exchanges:
Q1 (posed): Which suppliers delivered late in September?
Q1 (understood as): Which suppliers delivered after the requested date in September?
Presented identifiers: S-031, S-044
Latest question: No, I meant after the date they committed to.
Output: {"question": "Which suppliers delivered after the date they committed to in September?", "carried_from_history": true}

Earlier exchanges:
Q1 (posed): Which suppliers in Spain delivered late in September?
Q1 reading [late_by_delivery] examined: purchase order lines with suppliers where country = 'Spain' and delivery date between '2026-09-01' and '2026-09-30' and delivered after the requested date · grouped by supplier · computed the number of records · all 2 groups shown
Presented identifiers: S-031, S-044
Latest question: What is the value of their open orders?
Output: {"question": "What is the value of the open orders of S-031 and S-044?", "carried_from_history": true}

Earlier exchanges:
Q1 (posed): Which parts were received late in August?
Q1 reading [late_receipts] examined: purchase order lines where delivery date between '2026-08-01' and '2026-08-31' and delivered after the requested date · grouped by part · computed the number of records · 30 of 64 groups shown
Presented identifiers: P-10037, P-10111, P-10185
Latest question: What is on hand for them?
Output: {"question": "What is on hand for the parts delivered after the requested date in August?", "carried_from_history": true}

Earlier exchanges:
Q1 (posed): Which suppliers delivered late in September?
Presented identifiers: S-031, S-044
Latest question: What about the second one?
Output: {"question": "Which deliveries of S-044 were late in September?", "carried_from_history": true}

Conversation:
{{history}}
Latest question: {{question}}
{{feedback}}Output:
