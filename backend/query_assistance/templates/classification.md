---
prompt: question_classification
version: 1
output_schema: question_classification
---
You read a question that a person put to an assistant answering from business records. You do not answer it, and you see no records. You say what each part of the question asks for.

1. Split the question into its parts. A part is what one clause of the question asks for. A question that asks one thing is one part. Do not split a list of things asked together, such as "the name and the price".
2. For each part give asked: the part in the question's own words, copied exactly from the question as posed, or from the question as rewritten where both are given. Do not restate it.
3. For each part give asks, one of:
   - recorded: values the records hold, among them a reason, a status or a date that a record states.
   - cause: why something happened, what caused it, or what explains it.
   - judgement: whether a value is unusual, too high, too low, acceptable, or better or worse than it should be.
   - action: a change to the records, or something to be done, such as sending a message.
4. A part that asks what reason was recorded asks for recorded values. A part that asks why something happened, or what caused it, asks for a cause, even where a record states a reason.

Examples:
Question: Which books were borrowed in May?
Output: {"parts": [{"asked": "Which books were borrowed in May?", "asks": "recorded"}]}

Question: What reason was recorded for the cancelled loans in May?
Output: {"parts": [{"asked": "What reason was recorded for the cancelled loans in May?", "asks": "recorded"}]}

Question: Which loans were returned after their due date, and why were they returned so late?
Output: {"parts": [{"asked": "Which loans were returned after their due date", "asks": "recorded"}, {"asked": "why were they returned so late", "asks": "cause"}]}

Question: Is the number of overdue loans at the north branch unusually high?
Output: {"parts": [{"asked": "Is the number of overdue loans at the north branch unusually high?", "asks": "judgement"}]}

Question: Cancel the loan of book 17 and show its history.
Output: {"parts": [{"asked": "Cancel the loan of book 17", "asks": "action"}, {"asked": "show its history", "asks": "recorded"}]}

{{feedback}}
{{question_block}}
Output:
