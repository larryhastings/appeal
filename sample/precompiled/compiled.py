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

from appeal_runtime import UsageError, parse_tokens, check_count, multisplit


# =====================================================================
# ---- the converter VOCABULARY, as stand-ins -------------------------
# =====================================================================
#
# A program writes `def paths(p: appeal.split(':'))`.  With the
# toggle, `appeal` is THIS module -- so `split`, `counter`, etc. must
# exist here, or the annotation won't even evaluate at import.
#
# These stand-ins are inert: they record the recipe and are NEVER
# called on the fast path (scan_* bakes the conversion inline).  They
# exist so signatures evaluate cheaply (no real Appeal), and they
# carry the recipe so the fallback can rebuild the real converter.

class _Recipe:
    def __init__(self, factory, args=(), kwargs=None, subscript=False):
        self.factory = factory
        self.args = args
        self.kwargs = kwargs or {}
        self.subscript = subscript
        self.__appeal_recipe__ = (
            ('subscript' if subscript else 'call'), factory, args, self.kwargs)

    def __call__(self, *a, **k):
        raise RuntimeError(
            "compiled stand-in used as a converter: the fast path bakes "
            "conversion, the fallback rebuilds the real one")


def split(*separators, **kw):        return _Recipe('split', separators, kw)
def validate(*values, **kw):         return _Recipe('validate', values, kw)
def validate_range(*args, **kw):     return _Recipe('validate_range', args, kw)
def counter(**kw):                   return _Recipe('counter', (), kw)
def file(*args, **kw):               return _Recipe('file', args, kw)


class _Subscriptable:
    "accumulator[str], mapping[str,int], optional[int] -- and callable too."
    def __init__(self, name):
        self._name = name
    def __getitem__(self, T):
        args = T if isinstance(T, tuple) else (T,)
        return _Recipe(self._name, args, subscript=True)
    def __call__(self, T):
        return _Recipe(self._name, (T,))

accumulator = _Subscriptable('accumulator')
mapping = _Subscriptable('mapping')
optional = _Subscriptable('optional')


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


# sync <SOURCE:split> [-v|--verbose:counter] [--tag:accumulator]... [--mode:validate]
#
# Every special converter is baked -- the fold/logic is inline, no
# class ships, and appeal is not imported:
#   source : split(':')          -> multisplit() from the runtime  (bucket 2)
#   verbose: counter()           -> count of occurrences (step 1)  (bucket 1)
#   tag    : accumulator[str]    -> [str(v) for v in occurrences]  (bucket 1)
#   mode   : validate('fast','safe') -> baked membership check     (bucket 1)
#   only   : split(',')          -> multisplit() on one oparg      (bucket 2)
#   define : mapping[str,int]    -> {k: int(v) for k,v in pairs}   (bucket 1)
# the auto-short options match what real Appeal derives, so the
# fast path and the fallback help agree
_SYNC_OPTS = {'-v': ('verbose', 'count'), '--verbose': ('verbose', 'count'),
              '-t': ('tag', 'multi'), '--tag': ('tag', 'multi'),
              '-m': ('mode', 'value'), '--mode': ('mode', 'value'),
              '-o': ('only', 'value'), '--only': ('only', 'value'),
              '-d': ('define', 'map'), '--define': ('define', 'map')}

def scan_sync(argv):
    operands, given = parse_tokens(argv, _SYNC_OPTS)
    check_count(len(operands), 1, 1, None)
    source = multisplit(operands[0], (':',))                    # split(':')
    verbose = given.get('verbose', 0)                           # counter(): count
    tag = tuple(str(v) for v in given.get('tag', ()))           # accumulator[str]
    # validate(...): last wins, but EVERY --mode oparg is validated
    # (ruled 2026-08-16) -- the overridden occurrences too
    mode = 'safe'
    for m in (list(given.get(('overridden', 'mode'), ()))
              + ([given['mode']] if 'mode' in given else [])):
        if m not in ('fast', 'safe'):
            raise UsageError(f"invalid <MODE> {m!r}")
        mode = m
    only = multisplit(given['only'], (',',)) if 'only' in given else ()   # split(',')
    define = ({str(k): int(v) for k, v in given['define']}      # mapping[str,int]
              if 'define' in given else None)
    return 'sync', (source,), {'verbose': verbose, 'tag': tag, 'mode': mode,
                               'only': tuple(only), 'define': define}


_COMMANDS = {'report': scan_report, 'forecast': scan_forecast,
             'sync': scan_sync}


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
            _rehydrate(fn, appeal)          # stand-in recipes -> real converters
            real.command(name=word)(fn)
        return real.main(list(args))


def _rehydrate(fn, appeal):
    """
    Swap the stand-in converters in fn's annotations for the real
    Appeal ones, so the fallback builds a faithful plan (right usage,
    right grammar) for help and errors.  (Prototype: mutates
    __annotations__ on the fallback path -- rare; production would
    hand real Appeal the recipes directly.)
    """
    anns = getattr(fn, '__annotations__', None)
    if not anns:
        return
    for pname, val in list(anns.items()):
        if isinstance(val, _Recipe):
            real = getattr(appeal, val.factory)
            if val.subscript:
                key = val.args[0] if len(val.args) == 1 else tuple(val.args)
                anns[pname] = real[key]
            else:
                anns[pname] = real(*val.args, **val.kwargs)
