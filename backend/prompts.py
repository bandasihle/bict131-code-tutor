"""System prompt construction for the BICT131 Socratic tutor.

This module owns the single decision that matters: how much help the tutor is
allowed to give. It is deliberately separate from app.py because the escalation
logic (Level 1 / 2 / 3) grows here, and it must stay server-side — the frontend
sends an attempt count, but it never decides what level of help comes back.
"""

# Shared across every level. Whatever the escalation logic decides, these rules
# hold: the tutor is a debugging partner for a first-year, not an autocomplete.
#
# Reply length is deliberately NOT here — Level 3 has to spend words explaining
# and showing a fix, so each level sets its own limit.
BASE_RULES = """You are a Socratic programming tutor for BICT131, a first-year \
university course in programming fundamentals. You are helping a student debug \
their own code.

Hard rules:
- Talk directly to the student, in plain language. Assume they are new to programming.
- Do not use jargon without explaining it in the same sentence.
- Never rewrite their whole program for them.
"""

# Level 1 — the student has just asked. Point at the neighbourhood of the bug,
# name nothing.
#
# Tightened 17 Aug 2026 after live testing. The previous wording ("ONE guiding
# question... without naming the bug") was satisfied by compound sentences whose
# second clause handed over the diagnosis: "What operator are you using on the if
# line, AND is that the correct one for testing equality?" All three probe bugs
# failed the same way, so the trailing-clause ban below is quoted from the real
# failures — an abstract "do not be too specific" did not bind.
LEVEL_1_INSTRUCTION = """Respond with ONE short question — a single sentence, one \
question mark — that aims the student at the part of their code where the problem \
lives, or at what they saw when they ran it.

Do not include the diagnosis in any form:
- Do not name or quote the specific line, variable, operator, or keyword involved.
- Do not name the concept behind the bug.
- Do not add a trailing clause that leads or evaluates — nothing of the form \
"...and is that the correct one?", "...and how does that behave?", "...and what \
does that become?". One clause, then stop.
- Do not state what is wrong, show corrected code, or list things to check.

The student should learn WHERE to look and still have to work out WHAT is wrong. \
If your question would let them fix it without thinking, make it broader.

Keep your reply under 40 words."""

# Level 2 — one failed attempt behind them. Still a question, but the search
# space gets much smaller: the line, the variable, or the concept is now fair game.
LEVEL_2_INSTRUCTION = """The student has already tried once and is still stuck. \
Narrow the search for them.

Respond with ONE more specific question. You may now name the line, the \
variable, or the programming concept involved — for example, that this is about \
how a loop counts, or about where a variable can be seen. Say what to look at \
and what to compare it against.

Still do not state the fix, and do not show corrected code. The student must be \
the one who spots it. Keep your reply under 80 words."""

# Level 3 — three attempts in. Being Socratic past this point stops being
# teaching and starts being an obstacle, so the tutor answers properly.
LEVEL_3_INSTRUCTION = """The student has tried three times and is still stuck. \
Stop asking questions and teach them directly.

Do all three, in this order:
1. Say plainly what the bug is and which line it is on.
2. Show ONLY the changed line or lines. Never output the full rewritten program, \
even if it is short — the student keeps their own program and edits those lines \
into it.
3. Explain why the original was wrong, in terms of what the computer actually \
did when it ran.

Be encouraging, not blunt — they worked for this. Keep the prose under 150 \
words; the code snippet does not count toward that."""

# Level -> instruction block. Keyed by the output of level_for_attempt(), so the
# two can never drift apart.
LEVEL_INSTRUCTIONS = {
    1: LEVEL_1_INSTRUCTION,
    2: LEVEL_2_INSTRUCTION,
    3: LEVEL_3_INSTRUCTION,
}

# The rule-based detectors (detectors.py) hand us zero or more plain-English
# tags. They go in the system prompt as a lead, wrapped in this framing.
#
# Two things about this text are load-bearing:
#
# - "lead, not a verdict". The detectors are pattern matchers, not readers. If
#   the model treats a tag as a diagnosis it will answer the tag instead of the
#   student's actual problem.
# - The explicit "does not change how much you are allowed to reveal". Without
#   it, a tag reads as permission to name the bug, which would quietly collapse
#   Level 1 into Level 3 — the one failure mode this whole layer must not have.
#
# The tags themselves name the concept only, never an identifier or a line
# number, for the same reason Level 1's wording was tightened on 17 Aug: give
# the model the name and it puts the name in the question.
DETECTOR_PREFIX = """An automatic scan of the student's code flagged the \
following. It is a lead, not a verdict — it was produced by a simple pattern \
matcher, not by reading the program, and it can be wrong or beside the point.

{tag_lines}

How to use it: let it inform WHERE you aim, and nothing else. It does not \
change how much you are allowed to reveal — the instructions below decide \
that, and they still apply in full. If they say ask one broad question, ask \
one broad question; a flag here is not a reason to name the bug, show the fix, \
or move faster.

Never mention this scan, quote it, or tell the student that anything was \
flagged. If the code does not actually have the problem described, say nothing \
about it and help with the real problem instead."""

MIN_LEVEL = 1
MAX_LEVEL = 3


def level_for_attempt(attempt: int) -> int:
    """Map an attempt count to a help level (1, 2, or 3).

    This is the single place attempts become levels. It lives server-side and
    clamps its own input, so a frontend that sends 99 — or -1, or garbage —
    cannot skip the ladder:

        attempt 0        -> Level 1: guiding question, area of the bug only
        attempt 1 or 2   -> Level 2: narrower hint, the line or the concept
        attempt >= 3     -> Level 3: direct explanation + the fix, with reasoning
    """
    try:
        attempt = int(attempt)
    except (TypeError, ValueError):
        attempt = 0

    # Clamp before mapping: below 0 is a first ask, 3 and above are all "stuck".
    attempt = max(0, min(3, attempt))

    if attempt == 0:
        return 1
    if attempt < 3:
        return 2
    return 3


def build_detector_block(tags) -> str:
    """Wrap detector tags in their framing. No tags means no block at all."""
    if not tags:
        return ""
    tag_lines = "\n".join(f"- {tag}" for tag in tags)
    return DETECTOR_PREFIX.format(tag_lines=tag_lines)


def build_system_prompt(attempt: int, tags=None) -> str:
    """Return the system prompt for a request at the given attempt count.

    `attempt` is the number of tries the student has already made on this bug
    (0 = they have just asked for the first time).

    `tags` is the output of detectors.detect_bugs() — extra context, and
    optional in the strongest sense: with no tags this returns exactly what it
    returned before the detectors existed.

    Ordering is deliberate. The detector block sits BETWEEN the base rules and
    the level instruction, so the level instruction is still the last thing the
    model reads. Putting the tag last would give a diagnosis recency over "one
    short question, under 40 words, do not name the operator" — which is
    precisely the Level 1 leak the 17 Aug wording was written to stop. The
    detectors must not be able to loosen the ladder; keeping them upstream of
    it in the prompt is half of how that is enforced.
    """
    level = level_for_attempt(attempt)
    detector_block = build_detector_block(tags)
    if detector_block:
        return f"{BASE_RULES}\n{detector_block}\n\n{LEVEL_INSTRUCTIONS[level]}"
    return f"{BASE_RULES}\n{LEVEL_INSTRUCTIONS[level]}"


def trim_blank_lines(text: str) -> str:
    """Strip fully-blank leading and trailing lines, preserving indentation.

    Unlike str.strip(), this never touches the leading whitespace of a line that
    has content — so a fragment pasted out of a class keeps line 1 at column 4.
    That matters because indentation IS the bug in a lot of first-year Python:
    re-indenting the student's first line means the tutor is not looking at the
    code the student actually has.

    A line counts as blank only if it is entirely whitespace, and blank lines are
    removed whole, never partially.
    """
    lines = text.split("\n")

    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1

    end = len(lines)
    while end > start and not lines[end - 1].strip():
        end -= 1

    return "\n".join(lines[start:end])


def build_user_message(code: str, issue: str) -> str:
    """Format the student's submission into a single user turn."""
    # Not .strip() — see trim_blank_lines. app.py trims too; this is idempotent,
    # and it keeps the prompt builder safe to call directly from a test.
    code = trim_blank_lines(code)
    # `issue` is prose, not code, so ordinary stripping is right here.
    issue = issue.strip()

    return (
        "Here is my code:\n\n"
        f"```\n{code}\n```\n\n"
        f"What's going wrong: {issue}"
    )
