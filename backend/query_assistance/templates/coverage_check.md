---
prompt: coverage_check
version: 1
output_schema: coverage_check
---
You check an answer given from business records. You see the question, its parts, what each reading of the records examined, and the answer's sentences, numbered. A value in a sentence appears as a reference, {label.field#row} or {label.total}: you never see the values, and you do not need them. You judge two things: whether each part of the question was answered, and how each sentence relates values.

1. For each part under Parts, in the same order and with asked copied exactly, give status:
   - answered: one or more sentences answer this part. List their numbers in sentences.
   - declared: the part is marked (declared). The answer already says the records do not hold it. sentences is empty.
   - not_answered: no sentence answers this part. sentences is empty.
2. For each part that is not_answered, also give:
   - message: one sentence that tells the person this part was not answered. Write {part} where the part goes and {reason} where the reason goes, once each. Code puts the part in quotation marks and writes the reason. You may write {entity:<table>} for a table listed under Tables. Write no digit, no identifier, and no name of a column or a table. Write no word of negation (no, not, none, never, nothing, neither, nor, without, cannot, or a word ending in n't): the reason carries it. Do not explain why the part was not answered, and write no "because", "cause", "due to" or "reason".
   - follow_up: the part as a question that stands on its own, so that it can be asked alone. End it with a question mark. Name only identifiers the question names.
3. For each sentence, by its number, give kind:
   - no_relation: it states values, and no relation between them.
   - correlational: it says values go together, without saying one brings about the other.
   - conditional_causal: it says one thing may bring about, or contribute to, another.
   - direct_causal: it says one thing brought about another, as with because, caused, due to, led to, or explains why.
   A sentence that states a reason a record holds, as recorded, is no_relation: "the reason recorded for the loan is {loans.reason#1}" states a value.

Examples:
Question: Which loans were returned after their due date in May, and which members have unpaid fines?
Parts:
1. Which loans were returned after their due date in May
2. which members have unpaid fines
Tables: loans (loans), members (members)
Examined:
[late_returns] examined: loans where returned after the due date and return date between '2026-05-01' and '2026-05-31'
Answer:
1. [late_returns] {late_returns.total} loans were returned after their due date in May, among them {late_returns.loan_id#1}.
Output: {"parts": [{"asked": "Which loans were returned after their due date in May", "status": "answered", "sentences": [1]}, {"asked": "which members have unpaid fines", "status": "not_answered", "sentences": [], "message": "Your question also asked {part}; that part {reason}.", "follow_up": "Which members have unpaid fines?"}], "sentences": [{"n": 1, "kind": "no_relation"}]}

Question: Which loans were cancelled in May, and with what reason?
Parts:
1. Which loans were cancelled in May, and with what reason?
Tables: loans (loans)
Examined:
[cancelled] examined: loans where status = 'cancelled' and loan date between '2026-05-01' and '2026-05-31'
Answer:
1. [cancelled] {cancelled.loan_id#1} was cancelled, with the reason recorded as {cancelled.reason#1}.
2. [cancelled] {cancelled.loan_id#2} was cancelled because {cancelled.reason#2}.
Output: {"parts": [{"asked": "Which loans were cancelled in May, and with what reason?", "status": "answered", "sentences": [1, 2]}], "sentences": [{"n": 1, "kind": "no_relation"}, {"n": 2, "kind": "direct_causal"}]}

{{feedback}}
{{question_block}}
Parts:
{{parts}}
Tables: {{tables}}
Examined:
{{examined}}
Answer:
{{answer}}
Output:
