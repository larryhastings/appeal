"""
appeal_runtime -- the MINIMAL runtime a pre-compiled parser needs.

This is a prototype (2026-08-16): just enough to parse a command
line, convert strings, and dispatch to a command.  NO help, NO
color, NO signature analysis -- none of the machinery that makes
`import appeal` cost ~20ms.  A pre-compiled script does
`from appeal_runtime import *` and gets these few functions; the
per-command scan/run logic lives in the generated script itself.

stdlib-only, so importing it is essentially free (measure below).
"""

import sys

__all__ = ['UsageError', 'parse_tokens', 'check_count', 'multisplit',
           'run_main']


class UsageError(Exception):
    "The command line was wrong; carries optional usage text."
    def __init__(self, message, usage=None):
        super().__init__(message)
        self.usage = usage


def parse_tokens(argv, options):
    """
    Split argv into (operands, given).  `options` maps each option
    string to (name, kind):
        'flag'   present -> True
        'value'  takes one oparg (last wins)
        'count'  repeatable, no oparg -> number of occurrences  (counter)
        'multi'  repeatable, one oparg -> list of opargs        (accumulator)
        'map'    repeatable, TWO opargs -> list of (k, v) pairs (mapping)
    '--' ends option parsing; '-' is a bare operand.  This is the
    whole recognizer -- real Appeal has a counting automaton here,
    but the shape (and the flag/value/count/multi kinds) is the same.
    """
    operands = []
    given = {}
    i = 0
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok == '--':
            operands.extend(argv[i + 1:])
            break
        if len(tok) > 1 and tok[0] == '-':
            key, eq, val = tok.partition('=')
            entry = options.get(key)
            if entry is None:
                raise UsageError(f"unknown option {key!r}")
            name, kind = entry
            if kind in ('flag', 'count'):
                if eq:
                    raise UsageError(f"option {key!r} takes no value")
                if kind == 'flag':
                    given[name] = True
                else:
                    given[name] = given.get(name, 0) + 1
            elif kind == 'map':
                if eq:
                    raise UsageError(f"option {key!r} takes two values")
                if i + 2 >= n:
                    raise UsageError(f"option {key!r} needs two values")
                given.setdefault(name, []).append((argv[i + 1], argv[i + 2]))
                i += 2
            else:
                if not eq:
                    i += 1
                    if i >= n:
                        raise UsageError(f"option {key!r} needs a value")
                    val = argv[i]
                if kind == 'multi':
                    given.setdefault(name, []).append(val)
                else:
                    given[name] = val
        else:
            operands.append(tok)
        i += 1
    return operands, given


def multisplit(text, separators):
    """
    Split `text` on any of `separators` -- the minimal stand-in for
    big.text.toy_multisplit that appeal.split() uses.  This is
    'bucket 2': a converter whose logic is small enough to live in
    the runtime, so the fast path needs it without importing big.
    """
    result = [text]
    for sep in separators:
        pieces = []
        for chunk in result:
            pieces.extend(chunk.split(sep))
        result = pieces
    return result


def check_count(count, minimum, maximum, usage):
    "Operand-count gate; raises UsageError with usage on mismatch."
    if count < minimum or (maximum is not None and count > maximum):
        raise UsageError(
            f"expected {minimum}"
            + (f"-{maximum}" if maximum != minimum else "")
            + f" argument(s), got {count}", usage)


def run_main(dispatch, argv=None):
    """
    The driver: parse+dispatch, print UsageError politely to
    stderr, return the exit code.  (The real run_main also renders
    errors through the help pipeline; a pre-compiled script that
    wants that would import the render module lazily -- out of
    scope for this prototype.)
    """
    if argv is None:
        argv = sys.argv[1:]
    try:
        result = dispatch(argv)
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        if e.usage:
            print(f"usage: {e.usage}", file=sys.stderr)
        return 2
    return result if isinstance(result, int) else 0
