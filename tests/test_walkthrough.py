"""WALKTHROUGH.md cites code by line number. This keeps those citations true.

The document names an exact file and line for every stage of the pipeline, which
is what makes it readable next to the code rather than instead of it. Line
numbers rot: between Week 4 and Week 5 eleven of seventeen citations drifted —
`builder.build()` moved from 51 to 71 on a docstring, `conditions.fires()` from
51 to 65 — and every one of them still rendered as a confident link to the wrong
place. Nothing noticed, because nothing was looking.

So the citations are a checked claim, in the same habit as the DDL hook in
`test_extension_*.py`, the cursor hook in `test_explain.py` and the `bar-seg`
scan in the console's own suite: the document states something about the code,
and a test fails when the code stops agreeing.

**The convention this enforces**, and it is the reason the check can be strict:

    [`plan_evaluations()`](src/glassbox/engine/evaluation.py#L103)

*The link text is the symbol that must be defined at that line.* Backticks and
a trailing `()` are cosmetic and stripped. A citation whose text is prose
instead — `[the planner](…#L103)` — cannot be checked, so it is refused rather
than skipped: an exemption nobody can see is how the drift got in.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from glassbox.config import REPO_ROOT

DOC = REPO_ROOT / "WALKTHROUGH.md"

# [text](target) — the target stops at the first ')' , which is enough here
# because no path in this document contains one.
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

# `def f(`, `async def f(`, `class C(`, `class C:` — or a module-level binding
# (`_SUBJECT_SQL: dict[...] = {`, `ALLOWED_RELATIONS = (`), which is how the
# document cites the tables that are themselves the design.
def _defines(line: str, symbol: str) -> bool:
    body = line.strip()
    escaped = re.escape(symbol)
    return bool(
        re.match(rf"^(async\s+def|def|class)\s+{escaped}\b", body)
        or re.match(rf"^{escaped}\s*[:=]", body)
    )


def _links() -> list[tuple[str, str]]:
    text = DOC.read_text(encoding="utf-8")
    out = []
    for match in LINK.finditer(text):
        target = match.group(2)
        # Section anchors and anything off this machine are somebody else's
        # problem; this test is about the repository.
        if target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        out.append((match.group(1), target))
    return out


LINKS = _links()
CODE_CITATIONS = [(t, p) for t, p in LINKS if re.match(r"^[^#]+\.py#L\d+$", p)]


def test_the_document_still_cites_code():
    """A guard on the guard: if the citations vanish, so does the point."""
    assert len(CODE_CITATIONS) >= 20, (
        "WALKTHROUGH.md's value is that Part 2 names the exact code for each "
        f"stage; only {len(CODE_CITATIONS)} line-anchored citations were found")


@pytest.mark.parametrize("text,target", LINKS, ids=lambda v: str(v)[:60])
def test_every_link_points_at_something_that_exists(text, target):
    """Covers the .sql seeds, the test modules and the directories too.

    A renamed seed file is the same defect as a moved line number: the document
    keeps rendering, and the link keeps looking authoritative.
    """
    path = REPO_ROOT / target.split("#", 1)[0]
    assert path.exists(), f"[{text}]({target}) points at nothing"


@pytest.mark.parametrize("text,target", CODE_CITATIONS, ids=lambda v: str(v)[:60])
def test_every_code_citation_lands_on_the_symbol_it_names(text, target):
    rel, _, anchor = target.partition("#")
    lineno = int(anchor[1:])
    symbol = text.strip().strip("`").removesuffix("()").strip()

    assert re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", symbol), (
        f"[{text}]({target}) — the link text of a line citation must be the "
        "symbol defined there, so that this test can check it. Prose link text "
        "is unverifiable, and unverifiable is how the numbers drifted in the "
        "first place.")

    lines = (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines()
    assert 1 <= lineno <= len(lines), (
        f"[{text}]({target}) — {rel} has {len(lines)} lines")

    found = lines[lineno - 1]
    if _defines(found, symbol):
        return

    # Say where it went, not just that it moved. A failure that hands over the
    # corrected number is a thirty-second fix; one that says "mismatch" is an
    # afternoon of grepping, and this test will fire most often on somebody
    # else's refactor.
    actual = [i + 1 for i, line in enumerate(lines) if _defines(line, symbol)]
    where = f"it is now at line {actual[0]}" if len(actual) == 1 else (
        f"candidates: {actual}" if actual else "the symbol is not in this file at all")
    pytest.fail(
        f"[{text}]({target}) — line {lineno} of {rel} is {found.strip()!r}, "
        f"which does not define {symbol!r}; {where}")
