"""
compiled -- a PRE-COMPILED weather parser that WEARS the Appeal API,
with full help by FALLBACK.

    if 1:
        import compiled as appeal
    else:
        import appeal

The fast path -- parse a valid command line and dispatch -- touches
NOTHING heavy: no help text, no rendering, and it never imports
appeal.  It goes python-start to dispatch at bare-Python speed.

The moment the minimal runtime raises (a usage error, or a `--help`
it doesn't recognize), we fall back: import the real Appeal, build
the app from the SAME live functions, and let it render full
color, word-wrapped help / rich errors from their docstrings.  So
this module carries zero pre-digested help -- the help lives in the
functions, and real Appeal reads it only when actually asked.

Everything above the divider is what codegen would emit.
"""

import sys

from appeal_runtime import UsageError, parse_tokens, check_count


# =====================================================================
# ---- generated: per-command SCAN (parse only) + dispatch ------------
# =====================================================================
#
# scan_* PARSE and return (word, args, kwargs) -- they never execute
# the command, so a parse failure raises before anything runs, and
# main() can fall back cleanly.  No usage strings are baked: on any
# failure, real Appeal produces the message.

_impls = {}          # command word -> the live function, bound at registration


_REPORT_OPTS = {'-u': ('units', 'value'), '--units': ('units', 'value'),
                '-v': ('verbose', 'flag'), '--verbose': ('verbose', 'flag')}

def scan_report(argv):
    operands, given = parse_tokens(argv, _REPORT_OPTS)
    check_count(len(operands), 1, 1, None)
    return 'report', (operands[0],), {'units': given.get('units', 'C'),
                                      'verbose': given.get('verbose', False)}


def scan_forecast(argv):
    operands, given = parse_tokens(argv, {})
    check_count(len(operands), 1, 2, None)
    days = int(operands[1]) if len(operands) == 2 else 3   # int(), baked
    return 'forecast', (operands[0], days), {}


_COMMANDS = {'report': scan_report, 'forecast': scan_forecast}


def _scan(argv):
    "Parse only.  Returns (word, args, kwargs); raises UsageError."
    if not argv:
        raise UsageError('no command given')
    scan = _COMMANDS.get(argv[0])
    if scan is None:
        raise UsageError(f"unknown command {argv[0]!r}")
    return scan(argv[1:])


# =====================================================================
# ---- the Appeal your program imports --------------------------------
# =====================================================================

class ConfigurationError(Exception):
    "The program and this compiled parser disagree; regenerate."


class Appeal:
    """
    Matches, never builds.  The fast path dispatches directly; help
    and errors fall back to the real Appeal, rebuilt from the same
    functions.
    """
    def __init__(self, name=None, **ignored):
        self.name = name
        self._registered = []          # (word, fn) -- for the fallback

    def command(self, name=None):
        def register(fn):
            word = name if name is not None else fn.__name__
            if word not in _COMMANDS:
                known = ', '.join(sorted(_COMMANDS))
                raise ConfigurationError(
                    f"this compiled parser has no command {word!r} "
                    f"(it knows: {known}); regenerate it")
            _impls[word] = fn
            self._registered.append((word, fn))
            return fn
        return register

    def main(self, args=None):
        if args is None:
            args = sys.argv[1:]
        missing = [w for w in _COMMANDS if w not in _impls]
        if missing:
            raise ConfigurationError(
                f"compiled command(s) never registered: "
                f"{', '.join(missing)}; regenerate the parser")
        try:
            word, cargs, ckwargs = _scan(list(args))     # FAST: parse only
        except UsageError:
            return self._fallback(args)                  # help / error: full Appeal
        result = _impls[word](*cargs, **ckwargs)         # FAST: execute
        return result if isinstance(result, int) else 0

    def _fallback(self, args):
        """
        The slow path: real Appeal, built from the live functions,
        renders full color word-wrapped help and rich errors.  Only
        reached on `--help` or a bad command line -- never on the
        hot path -- so paying the import+build here is fine.
        """
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))   # demo: pin the repo
        import appeal
        real = appeal.Appeal(self.name)
        for word, fn in self._registered:
            real.command(name=word)(fn)
        return real.main(list(args))
