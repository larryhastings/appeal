"""
compiled -- a PRE-COMPILED weather parser that WEARS the Appeal API.

Import it AS appeal and your program is unchanged:

    if 1:
        import compiled as appeal
    else:
        import appeal

Unlike the standalone script, this is NOT self-contained: it
imports appeal_runtime (the minimal, stdlib-only parse/dispatch
core) for speed.  It just doesn't drag in help/color/build -- so
importing it costs about as much as bare Python.

Same idea as the standalone shim: the decorators don't BUILD
anything.  @app.command() matches your live function to the
pre-compiled parse function baked in here (by command word) and
binds it into a slot the parser calls.  (This prototype matches by
name; the real thing fingerprints, and regenerates on drift.)

Everything below the divider is what codegen would emit.
"""

import sys

from appeal_runtime import UsageError, parse_tokens, check_count, run_main


# =====================================================================
# ---- generated: per-command parsers + dispatch (the baked bits) -----
# =====================================================================

_impls = {}          # command word -> the live function, bound at registration


# report [-u|--units <UNITS>] [-v|--verbose] <CITY>
_REPORT_OPTS = {'-u': ('units', 'value'), '--units': ('units', 'value'),
                '-v': ('verbose', 'flag'), '--verbose': ('verbose', 'flag')}
_REPORT_USAGE = 'weather report [-u|--units <UNITS>] [-v|--verbose] <CITY>'

def parse_report(argv):
    operands, given = parse_tokens(argv, _REPORT_OPTS)
    check_count(len(operands), 1, 1, _REPORT_USAGE)
    return _impls['report'](operands[0],
                            units=given.get('units', 'C'),
                            verbose=given.get('verbose', False))


# forecast <CITY> [<DAYS>]
_FORECAST_USAGE = 'weather forecast <CITY> [<DAYS>]'

def parse_forecast(argv):
    operands, given = parse_tokens(argv, {})
    check_count(len(operands), 1, 2, _FORECAST_USAGE)
    if len(operands) == 2:               # the int() converter, baked inline
        try:
            days = int(operands[1])
        except ValueError:
            raise UsageError(f"invalid <DAYS> {operands[1]!r}", _FORECAST_USAGE)
    else:
        days = 3
    return _impls['forecast'](operands[0], days)


_COMMANDS = {'report': parse_report, 'forecast': parse_forecast}
_USAGE = 'weather {report|forecast} ...'

def _dispatch(argv):
    if not argv:
        raise UsageError('no command given', _USAGE)
    parse = _COMMANDS.get(argv[0])
    if parse is None:
        raise UsageError(f"unknown command {argv[0]!r}", _USAGE)
    return parse(argv[1:])


# =====================================================================
# ---- the Appeal your program imports --------------------------------
# =====================================================================

class ConfigurationError(Exception):
    "The program and this compiled parser disagree; regenerate."


class Appeal:
    """
    The compiled parser's public face.  Matches, never builds:
    each decorator binds a live function into the baked parser.
    """
    def __init__(self, name=None, **ignored):
        # baked-in knobs are ignored in this prototype; the real
        # shim verifies them against what was compiled
        self.name = name

    def command(self, name=None):
        def register(fn):
            word = name if name is not None else fn.__name__
            if word not in _COMMANDS:
                known = ', '.join(sorted(_COMMANDS))
                raise ConfigurationError(
                    f"this compiled parser has no command {word!r} "
                    f"(it knows: {known}); regenerate it")
            _impls[word] = fn
            return fn
        return register

    def main(self, args=None):
        # all-or-nothing, like the real shim: every baked command
        # must have been registered
        missing = [w for w in _COMMANDS if w not in _impls]
        if missing:
            raise ConfigurationError(
                f"compiled command(s) never registered: "
                f"{', '.join(missing)}; regenerate the parser")
        return run_main(_dispatch, args)
