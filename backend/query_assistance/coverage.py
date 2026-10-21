"""
Task 4 (R-4), the coverage check (docs/notes/91_COVERAGE_PLAN.md; chat 20.10, 22:30, 22:40, 22:50)
[proposal, not in the book; Test 47 is a proposal].

Three components, so that no part of a question disappears unsaid and no recorded value is presented as a cause:

  A  in code (compiler.uncovered): every reading quotes the part of the question it answers (asked), and every
     content word of the question is quoted by a reading or by unresolved. No model call.
  C  classify(): what each part of the question asks (recorded, cause, judgement, action), from the question
     alone. Runs on every question that retrieves, beside retrieval and synthesis (CoVe: it never sees the plan
     or the answer).
  B  check(): after the last gate, whether each part was answered, and how each sentence relates values (Yu et
     al., EMNLP 2019). Skipped only where every condition of COVERAGE_SKIP holds. Sees no record: the sentences
     as references, the parts, what each reading examined.

The model words what it is good at wording; code gives every fact (chat 22:40). A message about a part that was
not answered is the model's sentence with slots code fills, {part}, {reason} and {entity:<table>}, and it is
shown only where it passes the checks of message(); else the fixed template. A follow-up question is the
model's, shown nowhere: the button shows the person's own words, and a click sends the follow-up only where
follow_up() accepts it, else the part as written (chat 22:50).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator

from model_client.client import ModelGateway

from . import calls as model_calls
from . import prompts
from .calls import MODEL_CALL_ATTEMPTS, Call, OutputRejected
from .compiler import FUNCTION_WORDS, HERE, Catalog, CompileError, Conversation, _check_span, _mentioned, _words
from .synthesis import Draft, Sentence

_BUNDLE = json.loads((HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))["schemas"]
CLASSIFY_V = Draft202012Validator(_BUNDLE["question_classification"]["schema"])
CHECK_V = Draft202012Validator(_BUNDLE["coverage_check"]["schema"])

# The conditions under which B is skipped: only where every one holds (chat 20.10, 21:01, decision 2). A case
# that is not here is checked. Each is decided from facts code holds, never from a record's content (NFR 21)
COVERAGE_SKIP = (
    "S1_one_part",            # every reading answers the same part (alternative readings of a term are one part)
    "S2_nothing_unresolved",  # no unresolved entry, and nothing A found uncovered
    "S3_no_recorded_reason",  # no sentence cites a column marked records_reason
    "S4_not_from_history",    # the rewrite carried nothing from earlier exchanges
)

# What code says about a part, by kind (chat 22:40, 22:50). The model's message carries one of them in {reason}
REASONS = {
    "uncovered": "was not covered by the records retrieved for this answer",
    "declared": "was declared as data this assistant does not hold",
    "cause": "asks for a cause, which an investigation determines",
    # task 3, GAP-07 (92 section 7; chat 21.10, 12:50): a part the plan language cannot express, by the limit
    "not_expressible:reading_ref_depth": "depends on the result of another result. Ask it in two steps",
    "not_expressible:in_reading_under_or": "joins a condition on another result with or, which cannot be asked "
                                           "in one question yet. Ask each on its own",
    "not_expressible:duplicate_table": "compares records of one kind with each other, which cannot be asked yet",
    "not_expressible:in_values_limit": "names more than 30 values. Ask about up to 30 at a time, or by something "
                                       "they share",
    "not_expressible:readings_limit": "needs more than 3 retrievals, more than one question holds. Ask about part "
                                      "of it",
}
# Task 3: the limits where asking the part on its own can work, so its line carries a button
ASKABLE_LIMITS = frozenset({"in_values_limit", "readings_limit"})

# Closed classes of the language (chat 22:40, 22:50): a message about what is missing explains nothing and
# negates nothing outside {reason}, where code writes the negation. Lists over model text: allowed here because
# they constrain the shape of a sentence code shows in its own frame, a class and not a case (L2)
NEGATION = frozenset({"no", "not", "none", "never", "nothing", "neither", "nor", "without", "cannot"})
CAUSAL = frozenset({"because", "cause", "caused", "causes", "causing", "due", "reason", "reasons", "why"})
# F-C (chat 21.10, 11:10): absence said without a negation word is still a claim about the data (Test 35). A
# sentence about a part not answered needs none of these: {reason} says what is missing
ABSENCE = frozenset({"lack", "lacks", "lacking", "missing", "absent", "unavailable", "empty", "zero", "exist",
                     "exists", "existing", "nobody", "nowhere"})
MAX_MESSAGE = 240
MAX_FOLLOW_UP = 200
_SLOT = re.compile(r"\{(part|reason|entity:[a-z][a-z0-9_]*)\}")


class ClassificationOutputError(OutputRejected):
    """C's output names a part that is not a span of the question."""


class CoverageOutputError(OutputRejected):
    """B's output leaves out a part or a sentence, or adds one."""


# --------------------------------------------------------------------------
# the parts of a question (from A)
# --------------------------------------------------------------------------

@dataclass
class Part:
    asked: str
    labels: list[str] = field(default_factory=list)   # the readings that answer it
    declared: bool = False                             # an unresolved entry: the records do not hold it
    uncovered: bool = False                            # A: no reading or unresolved quotes it
    limit: str | None = None                           # task 3: the language limit its reading met (LANGUAGE_LIMITS)


def _key(text: str) -> str:
    return " ".join(m.group().casefold() for m in _words(text))


def parts_of(intent: dict, uncovered: list[str], dropped: list[dict] | None = None) -> list[Part]:
    """One part per distinct asked of the readings, in order; one per unresolved entry; one per part A found
    uncovered; one per reading left out for a limit of the language (task 3), as not answered, with the limit.
    Readings that are alternatives of an ambiguous term share their asked, so they are one part."""
    parts: dict[str, Part] = {}
    for r in intent.get("readings", []):
        parts.setdefault(_key(r["asked"]), Part(r["asked"])).labels.append(r["label"])
    out = list(parts.values())
    out += [Part(u["asked"], declared=True) for u in intent.get("unresolved", [])]
    out += [Part(span, uncovered=True) for span in uncovered]
    seen = {_key(p.asked) for p in out}
    for d in dropped or []:
        if _key(d["asked"]) not in seen:
            seen.add(_key(d["asked"]))
            out.append(Part(d["asked"], uncovered=True, limit=d["code"]))
    return out


def reason_of(part: Part) -> str:
    """The reason code writes in the line about a part not answered."""
    return f"not_expressible:{part.limit}" if part.limit else "uncovered"


def reason_aliases(draft: Draft, cat: Catalog) -> set[tuple[str, str]]:
    """(label, alias) of every field the draft cites from a column marked records_reason."""
    out = set()
    for ss in draft.sentences.values():
        for s in ss:
            for lab, alias, _ in s.refs:
                col = draft.materials[lab].columns.get(alias)
                if col and (cat.column(col) or {}).get("records_reason"):
                    out.add((lab, alias))
    return out


def skip_conditions(parts: list[Part], draft: Draft, cat: Catalog, carried: bool) -> dict[str, bool]:
    """Each condition of COVERAGE_SKIP, held or not. B is skipped only where all hold."""
    held = {
        "S1_one_part": len([p for p in parts if p.labels]) <= 1,
        "S2_nothing_unresolved": not any(p.declared or p.uncovered for p in parts),
        "S3_no_recorded_reason": not reason_aliases(draft, cat),
        "S4_not_from_history": not carried,
    }
    if tuple(held) != COVERAGE_SKIP:   # a condition checked here and not listed, or listed and not checked
        raise RuntimeError("skip_conditions and COVERAGE_SKIP differ")
    return held


# --------------------------------------------------------------------------
# C: what each part asks
# --------------------------------------------------------------------------

def render_classify(question: str, rewritten: str | None, prompt: prompts.Prompt,
                    feedback: str | None = None) -> str:
    return prompts.fill(prompt, question_block=prompts.question_block(question, rewritten),
                        feedback=prompts.feedback_block(feedback))


def check_classification(out: dict, conv: Conversation) -> list[dict]:
    for p in out["parts"]:
        try:
            _check_span(p["asked"], conv, "part")
        except CompileError as e:
            raise ClassificationOutputError(str(e), "Each asked must be copied exactly from the question.") from e
    return out["parts"]


def classify(gateway: ModelGateway, model: str, question: str, rewritten: str | None, conv: Conversation,
             prompt: prompts.Prompt, attempts: int = MODEL_CALL_ATTEMPTS) -> tuple[list[dict], list[Call]]:
    return model_calls.ask(gateway, model, prompt.identity,
                           lambda fb: render_classify(question, rewritten, prompt, fb),
                           lambda out: check_classification(out, conv), attempts, CLASSIFY_V.schema, CLASSIFY_V)


def _overlap(a: str, b: str) -> float:
    """The share of a's content words that b holds."""
    wa = {w for w in _key(a).split() if w not in FUNCTION_WORDS}
    wb = {w for w in _key(b).split() if w not in FUNCTION_WORDS}
    return len(wa & wb) / len(wa) if wa else 0.0


def referred(classified: list[dict], parts: list[Part]) -> list[dict]:
    """Each part C says asks for a cause, a judgement or an action, with the parts of A it falls on: the one it
    overlaps most, at least half of its words, all of them where several tie (the cautious side). A part that
    falls on none of them (F-A, chat 21.10, 11:10) falls on every part it overlaps at all, and where it overlaps
    none, on every part: an answer from the records never stands beside a question of cause it could not be
    told apart from. Such a part is marked unmapped, for the record."""
    out = []
    for c in classified:
        if c["asks"] == "recorded":
            continue
        scores = [(_overlap(c["asked"], p.asked), p) for p in parts]
        best = max((s for s, _ in scores), default=0.0)
        hit = [p for s, p in scores if best >= 0.5 and s == best]
        unmapped = not hit
        if unmapped:
            hit = [p for s, p in scores if s > 0] or list(parts)
        out.append({"asked": c["asked"], "asks": c["asks"], "parts": hit,
                    "labels": [lab for p in hit for lab in p.labels], "unmapped": unmapped})
    return out


# --------------------------------------------------------------------------
# B: was each part answered, and how does each sentence relate values
# --------------------------------------------------------------------------

def render_check(question: str, rewritten: str | None, parts: list[Part], draft: Draft, cat: Catalog,
                 prompt: prompts.Prompt, feedback: str | None = None) -> str:
    """L4: each field says what it is: the parts numbered, a declared part marked, each sentence numbered with
    its reading, as references, never a value."""
    plist = "\n".join(f"{n}. {p.asked}" + (" (declared)" if p.declared else "") for n, p in enumerate(parts, 1))
    sentences = [(label, s.template) for label, ss in draft.sentences.items() for s in ss]
    answer = "\n".join(f"{n}. [{label}] {t}" for n, (label, t) in enumerate(sentences, 1))
    examined = "\n".join(f"[{label}] examined: {m.examined}" for label, m in draft.materials.items()
                         if label in draft.sentences and m.examined)
    tables = ", ".join(f"{t} ({e.get('label', t)})" for t, e in cat.entities.items())
    return prompts.fill(prompt, question_block=prompts.question_block(question, rewritten), parts=plist,
                        tables=tables, examined=examined, answer=answer, feedback=prompts.feedback_block(feedback))


def check_output(out: dict, parts: list[Part], n_sentences: int) -> dict:
    """Every part sent, once and in order; every sentence number once (as R4: nothing goes unexamined)."""
    got = [p["asked"] for p in out["parts"]]
    if [_key(a) for a in got] != [_key(p.asked) for p in parts]:
        raise CoverageOutputError("the parts are not the parts sent", "Give every part under Parts, in the same "
                                                                       "order, with asked copied exactly.")
    numbers = sorted(s["n"] for s in out["sentences"])
    if numbers != list(range(1, n_sentences + 1)):
        raise CoverageOutputError("the sentences are not the sentences sent", "Give a kind for every numbered "
                                                                               "sentence, once.")
    for p in out["parts"]:
        if any(n > n_sentences for n in p["sentences"]):
            raise CoverageOutputError("a part cites a sentence that is not there", "Cite only the numbered "
                                                                                   "sentences.")
    return out


def check(gateway: ModelGateway, model: str, question: str, rewritten: str | None, parts: list[Part],
          draft: Draft, cat: Catalog, prompt: prompts.Prompt,
          attempts: int = MODEL_CALL_ATTEMPTS) -> tuple[dict, list[Call]]:
    n = sum(len(ss) for ss in draft.sentences.values())
    return model_calls.ask(gateway, model, prompt.identity,
                           lambda fb: render_check(question, rewritten, parts, draft, cat, prompt, fb),
                           lambda out: check_output(out, parts, n), attempts, CHECK_V.schema, CHECK_V)


# --------------------------------------------------------------------------
# what code shows of the model's wording (chat 22:40, 22:50)
# --------------------------------------------------------------------------

def _plain_words(text: str) -> list[str]:
    return [m.group().casefold() for m in _words(text)]


def message(text: str | None, span: str, reason: str, cat: Catalog) -> str | None:
    """The model's sentence about a part not answered, with its slots filled, or None where it fails a check:
    {part} and {reason} once each; every {entity:<table>} a table of the catalog; outside the slots no digit, no
    catalog name (table or column), no causal word, no negation and no word of absence; at most MAX_MESSAGE
    characters."""
    if not text or len(text) > MAX_MESSAGE or reason not in REASONS:
        return None
    slots = _SLOT.findall(text)
    if slots.count("part") != 1 or slots.count("reason") != 1:
        return None
    if "{" in _SLOT.sub("", text) or "}" in _SLOT.sub("", text):
        return None   # a slot code does not know
    tables = [s.split(":", 1)[1] for s in slots if s.startswith("entity:")]
    if any(t not in cat.entities for t in tables):
        return None
    outside = _SLOT.sub(" ", text)
    if re.search(r"\d", outside):
        return None
    names = {t.casefold() for t in cat.entities} | {c.casefold() for e in cat.entities.values()
                                                     for c in e.get("columns", {})}
    words = [w.replace("’", "'") for w in _plain_words(outside)]   # F-B: doesn’t is doesn't
    if any(w in NEGATION or w.endswith("n't") or w in CAUSAL or w in ABSENCE or w in names for w in words):
        return None
    if any("_" in w for w in outside.split()):
        return None   # a raw catalog name the word pattern splits
    filled = text.replace("{part}", f"“{span}”").replace("{reason}", REASONS[reason])
    for t in tables:
        filled = filled.replace(f"{{entity:{t}}}", cat.entities[t].get("label", t))
    return filled


def follow_up(text: str | None, question: str, conv: Conversation) -> str | None:
    """The model's standalone question for a part, or None: it ends with a question mark, is at most
    MAX_FOLLOW_UP characters, holds no digit the question does not, and every token holding a digit (an
    identifier) is in the conversation (NFR 27, as the compiler checks)."""
    if not text or len(text) > MAX_FOLLOW_UP or not text.rstrip().endswith("?"):
        return None
    texts = [question] + list(conv.earlier)
    for m in _words(text):
        w = m.group()
        if re.search(r"\d", w) and not _mentioned(w, texts) and w.casefold() not in {
                str(v).casefold() for v in conv.presented}:
            return None
    return text.strip()


# --------------------------------------------------------------------------
# sentences that present a recorded value as a cause (decision 4)
# --------------------------------------------------------------------------

CAUSAL_KINDS = ("conditional_causal", "direct_causal")


def recorded_fact(sentence: Sentence, draft: Draft, cat: Catalog) -> Sentence | None:
    """The sentence code writes in place of a causal one: for each field it cites from a column marked
    records_reason, that the row holds it, by reference, and that why is for an investigation. None where it
    cites no such field: there is no recorded fact to give."""
    parts = []
    refs = []
    for lab, alias, row in sentence.refs:
        m = draft.materials[lab]
        col = m.columns.get(alias)
        spec = cat.column(col) if col else None
        if not spec or not spec.get("records_reason"):
            continue
        ident = next((a for a in m.columns if a in m.id_aliases), None)
        who = f"{{{lab}.{ident}#{row}}}" if ident else "One record"
        parts.append(f"{who} has {spec.get('label', alias)} recorded as {{{lab}.{alias}#{row}}}.")
        refs += ([(lab, ident, row)] if ident else []) + [(lab, alias, row)]
    if not parts:
        return None
    return Sentence(" ".join(parts) + " Why it happened is for an investigation.", refs)


def replace_reason_sentences(draft: Draft, cat: Catalog, which: set[int] | None = None) -> tuple[Draft, int, int]:
    """The draft with each sentence in which (numbers in the order of filled(); None: every sentence that cites
    a records_reason field) replaced by its recorded fact, or removed where it has none. Returns the draft, how
    many were replaced and how many removed."""
    out: dict[str, list[Sentence]] = {}
    replaced = removed = 0
    n = 0
    for label, ss in draft.sentences.items():
        for s in ss:
            n += 1
            cites = any((cat.column(draft.materials[lab].columns.get(alias) or "") or {}).get("records_reason")
                        for lab, alias, _ in s.refs)
            if (which is not None and n in which) or (which is None and cites):
                fact = recorded_fact(s, draft, cat)
                if fact is None:
                    removed += 1
                    continue
                replaced += 1
                out.setdefault(label, []).append(fact)
            else:
                out.setdefault(label, []).append(s)
    return Draft(out, draft.materials, draft.calls), replaced, removed


def without_labels(draft: Draft, labels: set[str]) -> Draft:
    return Draft({lab: ss for lab, ss in draft.sentences.items() if lab not in labels}, draft.materials, draft.calls)

