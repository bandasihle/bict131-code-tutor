"""Rule-based bug detectors for common first-year Python mistakes.

These run BEFORE the Groq call. Each one returns a short, plain-English tag that
gets appended to the system prompt as extra context, so the model has a head
start on cases we see constantly in BICT131.

Three rules govern everything in this module:

1. A tag is a LEAD, not a verdict. It informs the hint; it never replaces it,
   and it never reaches the student. The escalation ladder in prompts.py is
   untouched by anything here — a detector firing does not skip a level.
2. Silence is the default. Every detector returns None unless it is confident.
   A missed bug costs us nothing (the model still reads the code); a false tag
   aims the tutor at a problem the student does not have. We would rather be
   quiet than wrong.
3. Tags name the CONCEPT, never the identifier or the line number. Level 1 has
   to ask a broad question, and handing the model the variable name right
   before it writes that question is how Level 1 starts running hot.

The tag wordings below are prompt text. Treat changes to them the same way you
would treat changes to prompts.py.
"""

import ast
import builtins
import re

# --- Tag wordings (prompt text — concept only, no identifiers, no line numbers)

TAG_ASSIGN_IN_CONDITION = (
    "The program does not parse. Inside a conditional test, a single `=` "
    "appears to be used where `==` was meant."
)

TAG_INDENTATION = (
    "The program does not parse because of its indentation: a block is "
    "indented where none was expected, or is missing indentation where a "
    "block was required."
)

TAG_TABS_AND_SPACES = (
    "The program does not parse because it mixes tabs and spaces for "
    "indentation."
)

TAG_MISSING_RETURN = (
    "A function's result is used at a call site, but that function never "
    "returns a value."
)

TAG_SCOPE = (
    "A variable is assigned only inside a function, but is read outside that "
    "function, where it does not exist."
)

TAG_INPUT_NO_CAST = (
    "A value read from `input()` is used in arithmetic or a numeric "
    "comparison without being converted to a number first. `input()` always "
    "produces text."
)

TAG_OFF_BY_ONE_RANGE = (
    "A loop's range boundary appears to be one larger than the sequence it "
    "indexes, so the final pass reads past the end."
)

TAG_OFF_BY_ONE_INDEX = (
    "A loop indexes one position ahead of its counter without shortening its "
    "range, so the final pass reads past the end."
)


def detect_bugs(code: str) -> list:
    """Return the tags that fired for this code. Empty list means say nothing.

    Parse once. If the code does not parse we cannot walk a tree, so only the
    syntax-shaped detectors run and every structural detector stays silent —
    we do not guess at the shape of broken code. If it does parse, the
    syntax-shaped detectors cannot fire by construction.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return _detect_from_syntax_error(code, exc)
    except (ValueError, MemoryError, RecursionError):
        # Null bytes, absurd nesting. Not our problem — let the model read it.
        return []

    tags = []
    for detector in (
        _detect_scope,
        _detect_missing_return,
        _detect_input_without_cast,
        _detect_off_by_one_range,
        _detect_off_by_one_index,
    ):
        try:
            tag = detector(tree)
        except Exception:
            # A detector bug must never take the tutor down with it. The whole
            # layer is optional context; failing means falling back to silence.
            tag = None
        if tag:
            tags.append(tag)
    return tags


# --- Detectors that only ever see code which failed to parse ----------------


def _detect_from_syntax_error(code, exc):
    """Classify a parse failure. Correct code never reaches this function."""
    # TabError is a subclass of IndentationError, which is a subclass of
    # SyntaxError, so the order of these checks is the whole logic.
    if isinstance(exc, TabError):
        return [TAG_TABS_AND_SPACES]
    if isinstance(exc, IndentationError):
        return [TAG_INDENTATION]

    line = _line_of(code, exc.lineno)
    if line is not None and _bare_equals_in_condition(line):
        return [TAG_ASSIGN_IN_CONDITION]
    return []


def _line_of(code, lineno):
    if not isinstance(lineno, int) or lineno < 1:
        return None
    lines = code.split("\n")
    if lineno > len(lines):
        return None
    return lines[lineno - 1]


_CONDITION_HEADER = re.compile(r"^\s*(if|elif|while)\b")

# != <= >= += -= *= /= etc, and the walrus := — an `=` after any of these
# characters is part of a legitimate two-character operator.
_OPERATOR_TAILS = "=!<>+-*/%&|^~:"


def _bare_equals_in_condition(line):
    """True if this `if`/`elif`/`while` header uses `=` where `==` belongs.

    Anchored to the line the interpreter itself blamed, so this can only ever
    mislabel one parse failure as another — it cannot fire on code that runs.

    The paren-depth check matters: `if print(end='x')` is a syntax error for an
    unrelated reason (no colon), and the `=` in it is a keyword argument. Only
    a bare `=` at depth zero is the mistake we mean.
    """
    if not _CONDITION_HEADER.match(line):
        return False

    text = _blank_out_strings(line)
    depth = 0
    i = 0
    while i < len(text):
        char = text[i]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "=":
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt == "=":
                i += 2
                continue
            prev = text[i - 1] if i else ""
            if prev and prev in _OPERATOR_TAILS:
                i += 1
                continue
            if depth == 0:
                return True
        i += 1
    return False


def _blank_out_strings(line):
    """Replace quoted spans with spaces so an `=` inside a string is invisible."""
    out = []
    quote = None
    escaped = False
    for char in line:
        if quote:
            out.append(" ")
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char == '"' or char == "'":
            quote = char
            out.append(" ")
        else:
            out.append(char)
    return "".join(out)


# --- Scope walking helpers --------------------------------------------------

_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _walk_shallow(scope_node):
    """Yield descendants of a node without entering nested function/class bodies.

    Nested scopes are yielded (a `def` binds its own name in the enclosing
    scope) but not descended into, which is what makes "bound in this scope"
    mean something.
    """
    for child in ast.iter_child_nodes(scope_node):
        yield child
        if not isinstance(child, _SCOPE_NODES):
            for grandchild in _walk_shallow(child):
                yield grandchild


def _bindings(scope_node):
    """Every name this one scope binds."""
    names = set()
    for node in _walk_shallow(scope_node):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def _functions_in(scope_node):
    """Function definitions directly inside this scope, not nested deeper."""
    return [
        node
        for node in _walk_shallow(scope_node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _all_functions(tree):
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _has_star_import(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                return True
    return False


def _loads_in(scope_node):
    for node in _walk_shallow(scope_node):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            yield node


# --- 1. Scope: a name that only exists inside a function ---------------------


def _detect_scope(tree):
    """Fire when a function-local name is read somewhere it cannot be seen.

    Deliberately conservative. Every guard below exists because it is a real
    way for correct code to look like this bug:

    - `global` / `nonlocal`: a legal way to bind from inside a function, so any
      name declared either way is off the table entirely.
    - `from x import *`: we no longer know what is bound at module level, so
      the whole detector goes quiet.
    - class bodies: their bindings are folded into the "safe" set rather than
      reasoned about, because `self.total` is not a bare name and a class
      attribute is not a NameError.
    - closures: a nested function legitimately sees its parents' locals, so
      visibility accumulates down the nesting chain rather than per-function.
    """
    if _has_star_import(tree):
        return None

    declared_global = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared_global.update(node.names)

    module_bound = _bindings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            module_bound |= _bindings(node)

    function_bound = set()
    for fn in _all_functions(tree):
        function_bound |= _bindings(fn)

    candidates = function_bound - module_bound - declared_global - set(dir(builtins))
    if not candidates:
        return None

    # Read at module level, where a function local simply does not exist.
    for name_node in _loads_in(tree):
        if name_node.id in candidates:
            return TAG_SCOPE

    # Read inside a different function that cannot see it either.
    def visit(fn, visible):
        visible = visible | _bindings(fn)
        for name_node in _loads_in(fn):
            if name_node.id in candidates and name_node.id not in visible:
                return True
        return any(visit(child, visible) for child in _functions_in(fn))

    if any(visit(fn, module_bound) for fn in _functions_in(tree)):
        return TAG_SCOPE
    return None


# --- 2. Missing return -------------------------------------------------------


def _detect_missing_return(tree):
    """Fire when a function that computes something never returns it AND the
    caller uses the result.

    The second half is the whole detector. "Function with no return" on its own
    is not a bug — most first-year functions just print — and a detector built
    on that alone would fire constantly on correct code. Requiring the result
    to be used in a value context is what makes it safe.

    Only module-level `def`s, called by bare name. Resolving `obj.method()` back
    to a class is guesswork, and `__init__` returns nothing quite legally.
    """
    parents = _parent_map(tree)

    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _returns_a_value(fn) or _is_generator(fn):
            continue
        if _is_stub(fn) or _ends_with_raise(fn):
            continue
        if not _computes_something(fn):
            continue
        if _result_used_as_a_value(tree, parents, fn.name):
            return TAG_MISSING_RETURN
    return None


def _parent_map(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _returns_a_value(fn):
    return any(
        isinstance(node, ast.Return) and node.value is not None
        for node in _walk_shallow(fn)
    )


def _is_generator(fn):
    return any(
        isinstance(node, (ast.Yield, ast.YieldFrom)) for node in _walk_shallow(fn)
    )


def _body_without_docstring(fn):
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            return body[1:]
    return body


def _is_stub(fn):
    body = _body_without_docstring(fn)
    if not body:
        return True
    return all(
        isinstance(stmt, ast.Pass)
        or (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and stmt.value.value is Ellipsis
        )
        for stmt in body
    )


def _ends_with_raise(fn):
    body = _body_without_docstring(fn)
    return bool(body) and isinstance(body[-1], ast.Raise)


def _computes_something(fn):
    """True if the body produces a value rather than only causing side effects.

    An assignment, a loop, a branch — or a bare expression that is not a call,
    which is the `def double(n): n * 2` mistake. A body of nothing but calls
    (`print(...)`) is a procedure, and procedures are allowed to return nothing.
    """
    for stmt in _body_without_docstring(fn):
        if isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            return True
        if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While, ast.If)):
            return True
        if isinstance(stmt, ast.Expr) and not isinstance(stmt.value, ast.Call):
            return True
    return False


def _result_used_as_a_value(tree, parents, name):
    """True if this function is called somewhere its result is consumed.

    A call whose parent is an `Expr` statement is a bare `f()` — the caller
    threw the result away on purpose, so there is nothing to complain about.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == name):
            continue
        if not isinstance(parents.get(node), ast.Expr):
            return True
    return False


# --- 3. input() used as a number ---------------------------------------------

_NUMERIC_BUILTINS = {"range", "sum", "abs", "round"}
_ALWAYS_NUMERIC_OPS = (ast.Sub, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_ORDERING_OPS = (ast.Lt, ast.Gt, ast.LtE, ast.GtE)


def _detect_input_without_cast(tree):
    """Fire when text from input() is used where a number is required.

    Un-cast `input()` is not itself a bug — `name = input("Name: ")` is exactly
    right, and a detector that fired on every un-cast input() would be wrong
    most of the time. The numeric-use requirement is what keeps this off
    correct code.
    """
    from_input = set()
    cast_somewhere = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if _is_bare_input_call(node.value):
            from_input.update(targets)
        elif _is_numeric_cast(node.value):
            cast_somewhere.update(targets)

    # `age = input()` then `age = int(age)` is the correct pattern, not the bug.
    names = from_input - cast_somewhere
    if not names:
        return None

    for node in ast.walk(tree):
        if _is_numeric_use(node, names):
            return TAG_INPUT_NO_CAST
    return None


def _is_bare_input_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "input"
    )


def _is_numeric_cast(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("int", "float")
    )


def _is_numeric_literal(node):
    # bool is a subclass of int; `x == True` is not evidence of numeric intent.
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    )


def _is_string_literal(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _named(node, names):
    return isinstance(node, ast.Name) and node.id in names


def _is_numeric_use(node, names):
    if isinstance(node, ast.BinOp):
        return _binop_is_numeric_use(node, names)
    if isinstance(node, ast.Compare):
        return _compare_is_numeric_use(node, names)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in _NUMERIC_BUILTINS:
            return any(_named(arg, names) for arg in node.args)
    return False


def _binop_is_numeric_use(node, names):
    left, right = node.left, node.right
    if not (_named(left, names) or _named(right, names)):
        return False
    # These are a TypeError on str, full stop.
    if isinstance(node.op, _ALWAYS_NUMERIC_OPS):
        return True
    # `+` concatenates and `*` repeats, so both are legal on text. A numeric
    # literal on the other side is what makes the intent unambiguous.
    if isinstance(node.op, (ast.Add, ast.Mult)):
        other = right if _named(left, names) else left
        return _is_numeric_literal(other)
    return False


def _compare_is_numeric_use(node, names):
    """Walk each comparison pair, so `a < b < c` is handled a link at a time."""
    left = node.left
    for op, right in zip(node.ops, node.comparators):
        pair = (left, right)
        if any(_named(operand, names) for operand in pair):
            other = next((n for n in pair if not _named(n, names)), None)
            if isinstance(op, _ORDERING_OPS):
                # `if answer > "m":` is legal alphabetical ordering on text.
                if other is None or not _is_string_literal(other):
                    return True
            elif isinstance(op, (ast.Eq, ast.NotEq)):
                if other is not None and _is_numeric_literal(other):
                    return True
        left = right
    return False


# --- 4/5. Off-by-one, in exactly two shapes ----------------------------------


def _detect_off_by_one_range(tree):
    """`for i in range(len(seq) + 1): ... seq[i] ...` — always an IndexError.

    Narrow on purpose. Off-by-one in general is undecidable: `range(1, n)` and
    `range(n - 1)` are correct in some programs and wrong in others, so a broad
    check would be a coin flip. This shape is not — it raises every time.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
            continue
        seq = _range_over_len_plus_one(node.iter)
        if seq and _indexes_with(node.body, seq, _plain_index(node.target.id)):
            return TAG_OFF_BY_ONE_RANGE
    return None


def _detect_off_by_one_index(tree):
    """`for i in range(len(seq)): ... seq[i + 1] ...` — IndexError on the last pass.

    The correct version of this loop is written `range(len(seq) - 1)`, which
    `_range_over_len` refuses to match, so pairwise-comparison code stays clear.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
            continue
        seq = _range_over_len(node.iter)
        if seq and _indexes_with(node.body, seq, _index_plus_one(node.target.id)):
            return TAG_OFF_BY_ONE_INDEX
    return None


def _len_of_name(node):
    """Return the name inside `len(x)`, or None."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "len"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
    ):
        return node.args[0].id
    return None


def _sole_range_arg(node):
    """The single bound of `range(n)`, also accepting the `range(0, n)` spelling."""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "range"
        and not node.keywords
    ):
        return None
    args = node.args
    if len(args) == 1:
        return args[0]
    if len(args) == 2 and isinstance(args[0], ast.Constant) and args[0].value == 0:
        return args[1]
    return None


def _range_over_len(node):
    arg = _sole_range_arg(node)
    return _len_of_name(arg) if arg is not None else None


def _range_over_len_plus_one(node):
    arg = _sole_range_arg(node)
    if not isinstance(arg, ast.BinOp) or not isinstance(arg.op, ast.Add):
        return None
    for side, other in ((arg.left, arg.right), (arg.right, arg.left)):
        name = _len_of_name(side)
        if name and isinstance(other, ast.Constant) and other.value == 1:
            return name
    return None


def _plain_index(var):
    def matches(slice_node):
        return isinstance(slice_node, ast.Name) and slice_node.id == var

    return matches


def _index_plus_one(var):
    def matches(slice_node):
        return (
            isinstance(slice_node, ast.BinOp)
            and isinstance(slice_node.op, ast.Add)
            and isinstance(slice_node.left, ast.Name)
            and slice_node.left.id == var
            and isinstance(slice_node.right, ast.Constant)
            and slice_node.right.value == 1
        )

    return matches


def _indexes_with(body, seq, slice_matches):
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Subscript):
                continue
            if not (isinstance(node.value, ast.Name) and node.value.id == seq):
                continue
            if slice_matches(node.slice):
                return True
    return False
