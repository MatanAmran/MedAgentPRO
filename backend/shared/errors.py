"""
MedAgentPro - the one base class for the project's own exceptions (code rules, Evyatar, 21.10 21:25).

What it is for
    An exception the project raises says what failed, where and in which case, so that one line in the operational
    log (the breaker's stdout channel, NFR 11, never the system store) is enough to find a fault without
    reproducing it. Three fields carry that:

      code     stable and dotted, "<stage>.<what>.<detail>", for example "intent.rejected.unknown_name". A test may
               assert on it and a dashboard may count it; its wording never changes once written
      stage    the pipeline stage it happened in, by the names pipeline.STAGE_NAMES uses ("intent", "retrieval",
               ...), or the component outside the pipeline ("catalog", "server", "tool")
      context  identifiers only: an exchange id, a reading label, a column name, an attempt number. Never the text of
               a record, a question or a model output (NFR 21), so the log can be read by whoever runs the system

What it never does
    It never carries the sentence a person reads. What a person sees is chosen from a table of fixed constants by
    the code (89_GAPS section 4.6), never taken from str(exception): exception text is for the log only.

Where it fits
    New exceptions subclass ProjectError. Older exception classes (compiler.CompileError, calls.OutputRejected, ...)
    already carry a code of their own and are moved onto this base when their file is retrofitted
    (docs/notes/96R_RETROFIT_LOG.md). tests/test_code_rules.py refuses a new raise of a built-in exception in
    backend/ outside tests/.
"""

from __future__ import annotations

import re
from typing import Any

# "<stage>.<what>[.<detail>...]": lowercase words, digits and underscores, at least two dotted parts
CODE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
CONTEXT_VALUE_TYPES = (str, int, float, bool, type(None))


class ProjectError(Exception):
    """A failure the project raises on purpose, with a stable code, the stage and the identifiers of the case.

    Args:
        code: stable dotted code, "<stage>.<what>[.<detail>]" (CODE_RE). Checked here, so a malformed code fails
            where it is written, in a test, and never reaches the log.
        stage: the pipeline stage or component it happened in.
        **context: identifiers only (str, int, float, bool or None). A nested structure or an object is refused,
            since it could carry record content (NFR 21).

    Raises:
        ValueError: the code does not match CODE_RE, or a context value is not a plain identifier. Both are faults of
            the code that raises, found by its tests.
    """

    def __init__(self, code: str, stage: str, **context: Any):
        if not CODE_RE.match(code):
            raise ValueError(f"ProjectError code {code!r} is not '<stage>.<what>[.<detail>]'")
        bad = sorted(k for k, v in context.items() if not isinstance(v, CONTEXT_VALUE_TYPES))
        if bad:
            raise ValueError(f"ProjectError context holds a non-identifier value under {bad}")
        super().__init__(code)
        self.code = code
        self.stage = stage
        self.context = dict(context)

    def log_line(self) -> str:
        """The one line for the operational log: code, stage and every context field, sorted, as key=value."""
        fields = " ".join(f"{k}={v}" for k, v in sorted(self.context.items()))
        return f"error code={self.code} stage={self.stage}" + (f" {fields}" if fields else "")

    def __str__(self) -> str:
        return self.log_line()
