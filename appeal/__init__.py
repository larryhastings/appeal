#!/usr/bin/env python3
#
# appeal -- Appeal 1.0, the ground-up rewrite of the 0.6 line.
# Copyright 2021-2026 by Larry Hastings
#
# (In the engineering docs the rewrite is nicknamed "v2" and the
# 0.6 line "v1".)  The 0.6 source--and the old argument_grouping.py
# parameter grouper it shipped with--lives on this branch's history.
#
# The spec of record is appeal.grammar.md; the design rationale
# is appeal.proposal.md.

"""
Appeal: give Appeal your function's signature, get a command-line
interface.
"""

__version__ = '1.0'

# build and render are imported LAZILY (see the module __getattr__ below
# and the local imports in the methods that use them): `import appeal`
# pulls in only the stdlib-only core, so it's near bare-Python speed;
# big and inspect load only when you actually build or render (Larry's
# ruling 2026-08-16).
from .plan import Terminal, NO_DEFAULT, OptionRule, Plan, Slot
from .plan import _validate_arg_format

# ============================================================
# The parse / convert / dispatch / execute core.  Everything
# needed to convert, dispatch, and execute a command line lives
# here; rendering, building, and completion stay lazy.
# ============================================================

import collections
import operator
import sys

# Appeal REQUIRES big (ruled 2026-08-06) for its help/usage rendering.
# NB: this core imports NOTHING from big--the parse/convert/dispatch
# core is stdlib-only, so plain `import appeal` is near bare-Python
# speed.  All big-backed rendering lives in appeal/render.py, imported
# lazily.


def did_you_mean(word, candidates):
    """
    The suggestion tail for an unknown-name error: " (did you
    mean 'x'?)" when something in candidates is close, '' when
    nothing is.  difflib's gestalt matching--the stdlib's public
    answer (git hand-rolls Damerau-Levenshtein, clap uses
    Jaro-Winkler; on names this short they all agree).
    """
    import difflib
    matches = difflib.get_close_matches(word, list(candidates), n=2)
    if not matches:
        return ''
    if len(matches) == 1:
        return f" (did you mean {matches[0]!r}?)"
    return f" (did you mean {matches[0]!r} or {matches[1]!r}?)"


class AppealError(Exception):
    """
    The umbrella: every exception Appeal raises derives from it
    (v1's AppealBaseException role), so `except AppealError` means
    "anything Appeal raised".  It also has one job of its own:
    raise it FROM YOUR COMMAND for a runtime failure that should
    stop the program with a polite message but *without* usage
    (the command line was fine)--main() prints `error: ...` and
    exits 1.
    """


class ConfigurationError(AppealError):
    """
    Raised at *build* time: the program's signature doesn't make
    sense.  Always names the offender.  A bug, so main() lets it
    raise.
    """


class DataError(AppealError):
    """
    The data handed to the program is wrong--whatever its
    provenance: a config mapping, a mapping or CSV row being read,
    or (the most common data of all) the command line.  Carries
    the command's usage text when there is one to show.
    """
    def __init__(self, message, usage=None, param=None):
        super().__init__(message)
        self.usage = usage
        # the parameter/option name the error is ABOUT, when one
        # is knowable--structural provenance, so callers (the
        # config layer) can attribute errors by identity instead
        # of grepping the message
        self.param = param


class UsageError(DataError):
    """
    The command line specifically is wrong.  Printing it shows
    the message and the command's usage.
    """


class CommandError(AppealError):
    """
    Raise FROM YOUR COMMAND to fail with a message and a chosen
    exit code--parsing succeeded, the work itself went wrong.
    main() prints `error: message` (no usage: the command line
    was fine) and exits with .exit_code; process() lets it
    propagate.  (v1 documented exactly this contract for
    AppealCommandError but never wired the catch; restored and
    wired, ruled 2026-08-05.)
    """
    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


# the old prefixed names (0.6.4 called them exactly that--"old
# names"--and kept them as aliases; ruled again 2026-07-25:
# the unprefixed spellings are canonical, appeal.UsageError)
AppealUsageError = UsageError
AppealDataError = DataError
AppealConfigurationError = ConfigurationError
AppealCommandError = CommandError
AppealBaseException = AppealError


def convert(converter, text, name, usage=None):
    """
    Run a terminal converter over one operand.  A ValueError or
    TypeError from the converter becomes a UsageError naming
    the parameter and the offending text.
    """
    try:
        return converter(text)
    except (ValueError, TypeError) as e:
        converter_name = getattr(converter, '__name__', 'converter')
        detail = str(e) or f'not a valid {converter_name}'
        raise UsageError(
            f"invalid value for {name!r}: {text!r} ({detail})",
            usage, param=name) from None


class Option:
    """
    Subclass to define an option with custom behavior.  The
    protocol (v1's, kept):

      * init(default) -- called once, with the parameter's default;
      * option(...)   -- called once per occurrence, in
        command-line order; its signature defines the option's
        operands (each parameter one operand, converted per its
        annotation);
      * __call__()    -- (zero args) the final value passed to the
        command; renders the accumulated occurrences.  (1.0 renamed
        this from v1's render(); everything Appeal defers is rendered
        by calling it.)

    An Option may be given any number of times (ruled 2026-07-09:
    repetition is the norm--getopt, argparse, click).  If the option
    is never given, the class is never instantiated: the parameter's
    default passes through untouched.
    """
    def init(self, default):
        pass

    def option(self):
        raise NotImplementedError

    def __call__(self):
        raise NotImplementedError


# v1's name for a repeatable Option--which is now every Option
MultiOption = Option

# NOTE: StrictOption (an Option given AT MOST ONCE, else "specified more than
# once") was removed 2026-08-22 -- no demonstrable demand, no precedent in
# argparse/click (both last-wins by default).  To restore: re-add the
# `class StrictOption(Option)` + `is_strict_option`, make is_multioption exclude
# it, and set kind='fold1' in build for a strict Option (the fold1 handling
# downstream is still in place).


def is_option(annotation):
    "Is this annotation an Option subclass?"
    return isinstance(annotation, type) and issubclass(annotation, Option)


def is_multioption(annotation):
    "A repeatable option class -- now every Option (StrictOption removed)."
    return is_option(annotation)


def fold(cls, converters, occurrences, default, name, usage=None):
    """
    The collector behind MultiOption: init once, option() per
    occurrence, render() at the end.
    """
    instance = cls()
    instance.init(default)
    for occurrence in occurrences:
        arguments = [convert(converter, text, name, usage)
                     for converter, text in zip(converters, occurrence)]
        try:
            instance.option(*arguments)
        except (ValueError, TypeError) as e:
            raise UsageError(f"{name}: {e}", usage,
                             param=name) from None
    return instance()


def greedy_sizes(take, minimum, maximum, name, usage=None):
    """
    Split `take` operands into per-instance sizes for a *args
    converter group, v1's way (RULED, Larry, 2026-08-05): each
    instance takes up to `maximum`, greedily, no lookahead--a
    leftover shortfall below `minimum` is an error, never
    redistributed backward.
    """
    sizes = []
    remaining = take
    while remaining:
        size = maximum if remaining > maximum else remaining
        if size < minimum:
            each = (f"{minimum}" if minimum == maximum
                    else f"{minimum} to {maximum}")
            raise UsageError(
                f"wrong number of arguments for {name!r}: "
                f"each takes {each}, {size} left over", usage)
        sizes.append(size)
        remaining -= size
    return sizes


def window_options(occurrences, first, sizes, name, usage=None,
                   gate=0):
    """
    Bind a *args group's option occurrences to instances.  sizes
    is the per-instance operand count list (variable--greedy
    fill).  An occurrence at operand position p configures the
    instance being built or about to be built, and past the ends
    of the line it binds to the nearest instance (with sizes
    A B C, the positions 1 A 2 B 3 C 4 map to A, B, C, C).
    Position selects; it never rejects.  The only error left is
    an option with no instances at all.
    Returns one given-style dict per instance.
    """
    count = len(sizes)
    ends = []
    acc = first
    for s in sizes:
        acc += s
        ends.append(acc)
    givens = [{} for _ in range(count)]
    for key, entries in occurrences.items():
        for position, kind, value in entries:
            if position < gate:
                raise UsageError(
                    f"option {key} appears too early on the command line",
                    usage)
            if not count:
                raise UsageError(
                    f"option {key} needs at least one {name!r} "
                    f"on the command line", usage)
            # the first instance whose end lies past the
            # occurrence--"being built or about to be built"
            j = 0
            while j < count - 1 and ends[j] <= position:
                j += 1
            given = givens[j]
            if kind == 'fold1':
                if key in given:
                    raise UsageError(
                        f"option {key} specified more than once", usage)
                given[key] = value
            elif kind == 'value':
                # collect every occurrence in this instance's window;
                # convert_value validates them all and the last wins,
                # exactly as at the top level.  Re-add at the end to
                # keep last-occurrence order.
                occ = given.pop(key, [])
                occ.append(value)
                given[key] = occ
            elif kind in ('flag', 'nullary', 'group'):
                if key in given:
                    del given[key]      # last one wins, per instance
                given[key] = value
            else:
                given.setdefault(key, []).append(value)
    return givens


def call_converter(fn, converters, values, name, usage=None):
    """
    A multi-operand option converter: convert each operand per
    fn's parameter annotations, then call fn with the results.
    """
    args = [convert(converter, text, name, usage)
            for converter, text in zip(converters, values)]
    if fn is tuple:
        # a tuple[...] option: construct, don't call
        return tuple(args)
    try:
        return fn(*args)
    except (ValueError, TypeError):
        fn_name = getattr(fn, '__name__', 'converter')
        raise UsageError(
            f"invalid value for {name!r}: {values!r} "
            f"(not a valid {fn_name})", usage, param=name) from None


def convert_value(converters, occurrences, name, usage=None):
    """
    A value option's value: convert EVERY occurrence, in
    command-line order, and return the last.  Last wins (ruled
    2026-07-18), but every oparg is converted, so an invalid one
    is caught even when a later occurrence overrides it (ruled
    2026-08-16: "it's not called validate for nothing").
    occurrences is the option's list of opargs--one raw string per
    occurrence, or a tuple of strings for a multi-parameter
    converter.  A length-1 converter tuple is a simple leaf.
    """
    result = None
    for text in occurrences:
        if len(converters) == 1:
            result = convert(converters[0], text, name, usage)
        else:
            result = call_converter(converters[0], converters[1:],
                                    text, name, usage)
    return result


def absorb_take(remaining, suffix_counts, suffix_minimum, floor,
                skippable):
    """
    How many arguments an absorbing slot consumes--one whose
    converter contains *args, so it takes the most the slots after
    it can spare (leftmost-maximal-completable): the largest take
    the child accepts (>= floor), or a clean skip.  Returns the
    take, or None when only check_count-guaranteed-unreachable
    states remain.
    """
    if suffix_counts is None:
        candidates = (remaining - suffix_minimum,)
    else:
        candidates = tuple(remaining - x for x in sorted(suffix_counts))
    for c in candidates:
        if c >= floor or (c == 0 and skippable):
            return c
    return None


class ScopedQueue:
    """
    One scoped option string's occurrences, and the interval model
    that binds them (decoded from v1's pinned behavior, July 2026):

      * a window's interval runs from its first token to the next
        window's first token;
      * the last window opened extends to the end of the line
        ("the stuff in the final group sticks around forever");
      * a window nested inside an open window of the same key
        closes strictly--the enclosing window absorbs the rest;
      * an occurrence forces a skippable window only when no
        window could claim it (none open, and the latest is
        full); the forced window claims its forcer immediately--
        and forcing happens AT MOST ONCE per key per line (ruled
        2026-07-09: chain-conjuring could only ever produce
        all-defaults husks, and cost an inconsistency).

    Two phases, like everything since the dispatcher rework: the
    structural (dry) walk records the windows and every forcing
    decision; resolve() assigns the remaining occurrences offline;
    the live walk replays the recorded decisions and pops each
    window's values in order.
    """
    __slots__ = ('occurrences', 'mode', 'repeatable', 'taken',
                 'records', 'stack', 'close_order', 'force_answers',
                 'phase', 'live_window', 'live_force')

    def __init__(self, occurrences, mode):
        # mode: 'multi' (every occurrence collects), 'last'
        # (occurrences in one window overwrite--last wins), or
        # 'strict' (a StrictOption: at most once per window)
        self.occurrences = occurrences      # [(position, kind, value)]
        self.mode = mode
        self.repeatable = mode == 'multi'
        self.taken = [False] * len(occurrences)
        # one record per entered window, in walk order:
        # [start, end, strict, forced, values]
        self.records = []
        self.stack = []                     # open record indexes
        self.close_order = []               # indexes, by close time
        self.force_answers = []             # dry's decisions, replayed
        self.phase = 'dry'
        self.live_window = 0
        self.live_force = 0

    def forces(self):
        "Would a pending occurrence force a window right now?"
        if self.phase == 'live':
            answer = self.force_answers[self.live_force]
            self.live_force += 1
            return answer
        if self.stack:
            answer = False      # something enclosing will claim it
        elif not any(not t for t in self.taken):
            answer = False
        elif any(record[3] for record in self.records):
            answer = False      # one conjured window per key per line
        elif self.records:
            last = self.records[-1][4]
            # the latest window claims trailing occurrences unless
            # it's already full
            answer = bool(last) and not self.repeatable
        else:
            answer = True
        self.force_answers.append(answer)
        return answer

    def open_window(self, start, forced=False):
        if self.phase == 'live':
            return
        record = [start, None, False, forced, []]
        if forced:
            # the forced window claims its forcer immediately
            for index, done in enumerate(self.taken):
                if not done:
                    self.taken[index] = True
                    record[4].append(self.occurrences[index][2])
                    break
        self.records.append(record)
        self.stack.append(len(self.records) - 1)

    def close_window(self, end):
        if self.phase == 'live':
            return
        index = self.stack.pop()
        record = self.records[index]
        record[1] = end
        # nested inside an open window of the same key: strict
        record[2] = bool(self.stack)
        self.close_order.append(index)

    def resolve(self, key, usage=None):
        "Assign the unforced occurrences per the interval model."
        if self.phase == 'live':
            return
        opens = [i for i, r in enumerate(self.records) if not r[2]]
        ends = {}
        starts = {}
        for j, index in enumerate(opens):
            # announce-first: the first window's interval reaches
            # back to the start of the line; the last one extends
            # to its end--and a boundary occurrence (an option
            # between two windows' operands) ANNOUNCES the window
            # that follows, exactly as usage renders it (options
            # first inside each bracket; v1's pinned behavior)
            starts[index] = 0 if j == 0 else self.records[index][0]
            ends[index] = (self.records[opens[j + 1]][0]
                           if j + 1 < len(opens) else None)
        for occurrence, done in zip(self.occurrences, list(self.taken)):
            if done:
                continue
            position, kind, value = occurrence
            target = None
            for index, record in enumerate(self.records):
                if record[2]:
                    start = record[0]
                    end = record[1]
                else:
                    start = starts[index]
                    end = ends[index]
                if position < start:
                    continue
                if end is not None and position >= end:
                    continue
                if record[4] and self.mode == 'strict':
                    continue                # full: at most once
                target = record             # deepest/latest wins
            if target is None:
                if any(r[4] for r in self.records):
                    raise UsageError(
                        f"option {key} specified more than once",
                        usage)
                raise UsageError(   # pragma: no cover -- announce-first
                    # and the last window extending to the end leave
                    # no occurrence unclaimed; belt and braces
                    f"option {key} has nothing to bind to", usage)
            if target[4] and self.mode == 'last':
                target[4][-1] = value       # last one wins
            else:
                target[4].append(value)
        self.taken = [True] * len(self.taken)

    def rewind(self):
        "Switch to the live phase: replay decisions, pop values."
        self.phase = 'live'
        self.live_window = 0
        self.live_force = 0

    def next_values(self):
        """
        The live walk's pop: this window's assigned values.  Pops
        happen as windows close--descendants before ancestors--so
        they follow the recorded close order.
        """
        record = self.records[self.close_order[self.live_window]]
        self.live_window += 1
        return record[4]


def scopes_for(specs, given):
    """
    One ScopedQueue per scoped key that actually appeared.  specs
    maps each key to its mode--'multi', 'last', or 'strict' (same
    grammar in every window, so one answer per key).
    """
    scopes = {}
    for key, mode in specs.items():
        occurrences = given.get(key)
        if isinstance(occurrences, list) and occurrences:
            scopes[key] = ScopedQueue(occurrences, mode)
    return scopes


def scoped_forces(scopes, keys):
    "Does a pending occurrence force this window?  (Or replay it.)"
    if scopes is None:
        return False
    answer = False
    for key in keys:
        queue = scopes.get(key)
        if queue is not None and queue.forces():
            answer = True
    return answer


def scoped_window(scopes, keys, edge, i, forced=False):
    "Open ('in') or close ('out') a window for each declared key."
    if scopes is None:
        return
    for key in keys:
        queue = scopes.get(key)
        if queue is None:
            continue
        if edge == 'in':
            queue.open_window(i, forced)
        else:
            queue.close_window(i)


def scoped_resolve(scopes, usage=None):
    "After the structural walk: bind every occurrence, loudly."
    if scopes is None:
        return
    for key, queue in scopes.items():
        queue.resolve(key, usage)


def scoped_rewind(scopes):
    if scopes is None:
        return
    for queue in scopes.values():
        queue.rewind()


def scoped_next(scopes, key):
    "The live walk's pop for one key (None: no occurrences at all)."
    if scopes is None:
        return None
    queue = scopes.get(key)
    if queue is None:
        return None
    return queue.next_values()


def sibling_scopes(parents, specs, given, positions, usage=None,
                   summonable=True):
    """
    Sibling option groups (`e1: extras, e2: extras`): occurrences
    of the shared child options bind by ANNOUNCEMENT--each belongs
    to the announced parent (--e1/--e2) nearest before it, and
    occurrences before every announcement reach back to the first.
    With no announcement at all they SUMMON the first declared
    sibling into existence (Larry's ruling, 2026-07-18); the later
    siblings exist only when announced--there is no way to say
    which sibling an unannounced option means, so it never
    cascades.  summonable=False (the first sibling takes required
    arguments) turns the summon into a loud refusal.

    parents: ((parent key, (child keys...)), ...) in declaration
    order.  Returns (scopes, summon): scopes ready for the fills'
    live pops, summon the parent key to conjure (or None).
    """
    scopes = scopes_for(specs, given)
    if not scopes:
        return None, None
    announced = [(positions.get(('seq', pk), 0), pk, keys)
                 for pk, keys in parents if pk in given]
    seqs = [s for s, _, _ in announced]
    if seqs != sorted(seqs):
        spoken = ' '.join(pk for _, pk, _ in
                          sorted(announced, key=lambda a: a[0]))
        order = ', '.join(pk for pk, _ in parents)
        raise UsageError(
            f"sibling option groups share options and bind in "
            f"declaration order ({order}); they were given as "
            f"{spoken}", usage)
    summon = None
    if not announced:
        pk, keys = parents[0]
        if not summonable:
            shared = ', '.join(sorted(scopes))
            raise UsageError(
                f"option {shared} requires one of " +
                ', '.join(pk for pk, _ in parents), usage)
        summon = pk
        announced = [(0, pk, keys)]
    for s, pk, keys in announced:
        scoped_window(scopes, keys, 'in', s)
        scoped_window(scopes, keys, 'out', s)
    scoped_resolve(scopes, usage)
    scoped_rewind(scopes)
    return scopes, summon


def check_count(n, minimum, maximum, valid_counts, usage=None, what=None,
                param=None):
    """
    The exact-arity error, phrased as English, computed from the
    valid-count set.  what, if given, names the offender (e.g.
    "option -g") in the message.
    """
    where = f' for {what}' if what else ''
    if valid_counts is not None:
        if n in valid_counts:
            return
        counts = sorted(valid_counts)
        if len(counts) == 1:
            wanted = str(counts[0])
        else:
            wanted = ', '.join(str(c) for c in counts[:-1]) + f' or {counts[-1]}'
        raise UsageError(
            f"wrong number of arguments{where}: got {n}, expected {wanted}",
            usage, param=param)
    if n < minimum:
        raise UsageError(
            f"wrong number of arguments{where}: got {n}, "
            f"expected at least {minimum}",
            usage, param=param)


def run_main(parse, args=None, stylesheet=None, completion=None,
             errors=None, version=None, margin=79):
    """
    The main() driver: parse and execute, print errors the polite
    way, return the exit code.  stylesheet
    (a spec: None for auto, False for never-color, or a complete
    composed StyleSheet used verbatim) paints the 'error:' prefix
    and any attached usage/listing when the error stream wants
    color; the environment always wins (resolve_stylesheet via
    can_colorize).  completion, if
    given, is (table, prog): with an empty args and
    _APPEAL_COMPLETE in the environment, the invocation is a
    shell-completion reentry and is answered instead of parsed.

    errors is the file object error messages print to, default
    sys.stderr (the POSIX diagnostic convention, so pipelines
    reading this program's stdout stay clean; sys.stdout is v1's
    behavior).  Like print(file=None), the default is resolved
    at error time, so redirecting sys.stderr works.  Requested
    help always prints to stdout; this knob moves only the
    errors.

    version, if given, is the program's version string:
    `--version` as the first token prints it bare and exits 0,
    like -h/--help--program metadata outranks parsing.
    """
    if args is None:
        args = sys.argv[1:]
    if completion is not None and not args:
        table, prog = completion
        if 'commands' in table:
            completer = lambda w, p: complete_command_set(table, w, p)
        else:
            completer = lambda w, p: complete_command(table, w, p)
        code = completion_reentry(completer, prog)
        if code is not None:
            return code
    # (--version/-V are ordinary precommand options now--Larry's
    # design, 2026-07-19--scanned in the pre-command-word era and
    # yielding to user declarations; no first-token special case)

    def error_stream():
        # None resolves at error time, not at call time (tests
        # and callers redirect sys.stderr)
        return errors if errors is not None else sys.stderr

    def error_prefix():
        # render is imported lazily--only when an error actually
        # prints, so the success path (and `import appeal`)
        # never pays big's ~40ms
        from .render import resolve_stylesheet, style
        sheet = resolve_stylesheet(stylesheet, error_stream())
        return sheet.render(style('error', 'error:'))

    def print_usage(usage):
        # a STRING is a usage line; a TUPLE is a baked listing
        # (pieces), finished here--at the real margin, styled for
        # the error stream (errors ride the pipeline too, ruled
        # 2026-08-06)
        from .render import render_baked_help, help_margin
        if isinstance(usage, tuple):
            print(render_baked_help(usage, margin=help_margin(margin),
                                    file=error_stream(),
                                    stylesheet=stylesheet),
                  end='', file=error_stream())
        else:
            print(f"usage: {usage}", file=error_stream())

    try:
        result = parse(list(args))
    except SystemExit as e:
        # the precommand exits (program metadata: -V, ...);
        # main()'s contract is to RETURN the exit code.  A non-int,
        # non-None code is a message -- Python prints it to stderr
        # and exits 1; reproduce that half of the contract too.
        code = e.code
        if isinstance(code, int):
            return code
        if code is None:
            return 0
        print(code, file=error_stream())
        return 1
    except KeyboardInterrupt:
        # a process ended by SIGINT dies quietly with 128+SIGINT
        # (the shell already echoed ^C).  ONLY here (ruled
        # 2026-07-09): run_main is the whole-program driver;
        # process()/parse() stay raw--Appeal is an argument
        # processor, not an environment
        return 130
    except AppealDataError as e:
        print(f"{error_prefix()} {e}", file=error_stream())
        if e.usage:
            print_usage(e.usage)
        return 2
    except AppealConfigurationError:
        raise               # a bug in the program: traceback
    except CommandError as e:
        # the command failed on purpose: message, its chosen
        # code, no usage (the command line was fine)
        print(f"{error_prefix()} {e}", file=error_stream())
        return e.exit_code
    except AppealError as e:
        print(f"{error_prefix()} {e}", file=error_stream())
        return 1
    if result is None:
        return 0
    if isinstance(result, int):
        return result
    return 0


# the compiled Appeal's default for a policy argument: "use the library
# default".  The facade can't reproduce that default without importing full
# appeal, so for the sentinel it falls back to the fingerprint the emitter
# baked (the compile-time policy's).  An explicit value is fingerprinted live.
_CONFIG_DEFAULT = object()


def _params_host(obj):
    "Where a callable keeps its parameters: itself, or __init__."
    if hasattr(obj, '__code__'):
        return obj
    init = getattr(obj, '__init__', None)
    if init is not None and hasattr(init, '__code__'):
        return init
    return None


import os
import sys


##
## Shell completion (the completion rulings): the engine answers
## "what could legally come next?" from tables--plain data plus
## converter references.  A converter may carry a
## `completions` attribute: always a callable, (prefix) -> tuple
## of str; the engine always calls it, always passing the prefix
## (empty string when nothing's typed), and always re-filters.
## Silence means "no opinion", which the shell treats as
## "complete filenames".
##

def _completion_scan(options, words, maximum=None, boundary=None,
                     minimum=None):
    """
    A forgiving pass over the words already typed.  options maps
    each option string to (key, nargs, repeatable).  Returns
    (used, operands, pending, force_positional, leftover):
    pending is (key, nargs, remaining) when the cursor sits where
    an option's value belongs; leftover is the index where a new
    command word begins--at saturation (operands == maximum,
    cycling's boundary), or, for a set parent or the global
    command (minimum given), the first operand naming a boundary
    word once the minimum is met.  Options after saturation still
    belong to this command (the window stays open).  Never raises;
    completion must work on half-typed nonsense.
    """
    used = set()
    operands = 0
    pending = None
    force_positional = False
    for index, word in enumerate(words):
        if pending:
            key, nargs, remaining = pending
            remaining -= 1
            pending = (key, nargs, remaining) if remaining else None
            continue
        if force_positional or not word.startswith('-') or word == '-':
            if maximum is not None and operands >= maximum:
                return used, operands, pending, force_positional, index
            if (minimum is not None and boundary
                    and operands >= minimum and word in boundary):
                return used, operands, pending, force_positional, index
            operands += 1
            continue
        if word == '--':
            force_positional = True
            continue
        name = word.partition('=')[0] if word.startswith('--') else word[:2]
        entry = options.get(name)
        if entry is None:
            continue
        key, nargs, repeatable = entry
        if not repeatable:
            used.add(name)
            used.add(key)
        if nargs and '=' not in word:
            pending = (key, nargs, nargs)
    return used, operands, pending, force_positional, None


def _value_candidates(converter, prefix):
    """
    A value position's candidates: the expecting converter's
    completions callable, called with the prefix (a hint--the
    filter here is the belt).  No converter, or no completions
    attribute, means no opinion.  A wrong return type raises:
    garbage on TAB is a loud signal; silently dropping candidates
    hides the bug forever.
    """
    completions = getattr(converter, 'completions', None)
    if completions is None:
        return []
    values = completions(prefix)
    where = getattr(converter, '__name__', repr(converter))
    if not isinstance(values, tuple):
        raise AppealConfigurationError(
            f"completions for {where!r} returned "
            f"{type(values).__name__}, must return a tuple of str")
    out = []
    for value in values:
        if not isinstance(value, str):
            raise AppealConfigurationError(
                f"completions for {where!r} returned a "
                f"{type(value).__name__}, must return a tuple of str")
        if value.startswith(prefix):
            out.append(value)
    return sorted(out)


def _pending_candidates(table, pending, prefix):
    key, nargs, remaining = pending
    converters = table['values'].get(key) or ()
    index = nargs - remaining
    converter = converters[index] if index < len(converters) else None
    return _value_candidates(converter, prefix)


def _option_candidates(table, used, prefix):
    candidates = [s for s, (key, nargs, repeatable)
                  in table['options'].items()
                  if s.startswith(prefix)
                  and s not in used and key not in used]
    candidates.extend(s for s in table.get('help', ())
                      if s.startswith(prefix))
    return sorted(set(candidates))


def _operand_candidates(table, operands, prefix):
    slots = table['operands']
    converter = (slots[operands] if operands < len(slots)
                 else table['repeat'])
    return _value_candidates(converter, prefix)


def complete_command(table, words, prefix=''):
    """
    Candidate completions for one command.  table:

        options    {option string: (key, nargs, repeatable)}
        help       the automatic help option strings, or ()
        values     {key: (converter, ...)}, one per option operand
        operands   (converter-or-None, ...) in flat operand order
        repeat     the *args converter, or None
        minimum,   the argument-count arity (maximum None when
        maximum    unbounded)

    Options complete when the prefix starts with '-'; value
    positions ask the expecting converter; anything else is the
    shell's business (empty list = no opinion = filenames).
    """
    used, operands, pending, force_positional, _ = _completion_scan(
        table['options'], words)
    if pending:
        return _pending_candidates(table, pending, prefix)
    if prefix.startswith('-') and not force_positional:
        return _option_candidates(table, used, prefix)
    maximum = table.get('maximum')
    if maximum is not None and operands >= maximum:
        return []       # saturated: nothing more to say here
    return _operand_candidates(table, operands, prefix)


def complete_command_set(table, words, prefix=''):
    """
    Completion for a multi-command program.  table:

        commands   {word: command table, or a nested set entry
                    {'parent': table, 'commands': {...},
                     'repeat': bool}}
        global     the global command's table, or None
        minimum    the global command's minimum argument count
        auto_help  True if the automatic help command is live
        repeat     the root set cycles

    The walk mirrors the dispatcher: the global command's portion,
    then commands--and under cycling, a saturated command's
    boundary offers the resolution chain's words, while its open
    window keeps offering its options.
    """
    commands = table['commands']
    auto_help = table['auto_help']
    words = list(words)

    def is_set(entry):
        return 'options' not in entry

    root_words = set(commands) | ({'help'} if auto_help else set())

    if words and auto_help and words[0] == 'help' and 'help' not in commands:
        remaining = words[1:]
        if not remaining and not prefix.startswith('-'):
            return sorted(w for w in root_words if w.startswith(prefix))
        if remaining and remaining[0] in commands:
            entry = commands[remaining[0]]
            if not is_set(entry):
                return complete_command(entry, remaining[1:], prefix)
        return []

    stack = [{'commands': commands, 'repeat': table.get('repeat', False),
              'entered': False}]

    def resolvable():
        out = set()
        for depth, frame in enumerate(reversed(stack)):
            if (depth == 0 and not frame['entered']) or frame['repeat']:
                out.update(frame['commands'])
                if auto_help and frame is stack[0]:
                    out.add('help')
        return out

    index = 0
    g = table['global']
    if g is not None:
        used, operands, pending, forced, leftover = _completion_scan(
            g['options'], words, maximum=g.get('maximum'),
            boundary=root_words, minimum=table['minimum'])
        if leftover is None:
            if pending:
                return _pending_candidates(g, pending, prefix)
            if prefix.startswith('-') and not forced:
                return _option_candidates(g, used, prefix)
            if operands >= table['minimum']:
                out = sorted(w for w in root_words
                             if w.startswith(prefix))
                return out or _operand_candidates(g, operands, prefix)
            return _operand_candidates(g, operands, prefix)
        index = leftover

    while True:
        if index >= len(words):
            # the cursor sits at a command-word boundary
            if prefix.startswith('-'):
                return []
            return sorted(w for w in resolvable()
                          if w.startswith(prefix))
        word = words[index]
        entry = target = None
        for depth, frame in enumerate(reversed(stack)):
            if (depth == 0 and not frame['entered']) or frame['repeat']:
                if word in frame['commands']:
                    entry, target = frame['commands'][word], frame
                    break
        if entry is None:
            return []           # half-typed nonsense: no opinion
        while stack[-1] is not target:
            stack.pop()
        target['entered'] = True
        index += 1
        if is_set(entry):
            # a nested parent: a global command of its own little
            # set, flexible boundary
            stack.append({'commands': entry['commands'],
                          'repeat': entry.get('repeat', False),
                          'entered': False})
            parent = entry['parent']
            sub_words = resolvable()
            used, operands, pending, forced, leftover = (
                _completion_scan(parent['options'], words[index:],
                                 maximum=parent.get('maximum'),
                                 boundary=sub_words,
                                 minimum=parent.get('minimum', 0)))
            if leftover is None:
                if pending:
                    return _pending_candidates(parent, pending, prefix)
                if prefix.startswith('-') and not forced:
                    return _option_candidates(parent, used, prefix)
                if operands >= parent.get('minimum', 0):
                    out = sorted(w for w in sub_words
                                 if w.startswith(prefix))
                    return out or _operand_candidates(parent, operands,
                                                      prefix)
                return _operand_candidates(parent, operands, prefix)
            index += leftover
            continue
        # a leaf command: greedy saturation is its boundary when
        # anything could follow
        chain = resolvable()
        used, operands, pending, forced, leftover = _completion_scan(
            entry['options'], words[index:],
            maximum=entry.get('maximum') if chain else None)
        if leftover is None:
            if pending:
                return _pending_candidates(entry, pending, prefix)
            if prefix.startswith('-') and not forced:
                return _option_candidates(entry, used, prefix)
            maximum = entry.get('maximum')
            if (chain and maximum is not None
                    and operands >= maximum):
                # saturated: the resolution chain's words complete
                # here (the open window's options handled above)
                return sorted(w for w in chain
                              if w.startswith(prefix))
            return _operand_candidates(entry, operands, prefix)
        index += leftover


_BASH_COMPLETION_SCRIPT = """\
_appeal_{ident}_completion() {{
    local IFS=$'\\n'
    COMPREPLY=( $( COMP_WORDS="${{COMP_WORDS[*]}}" \\
                   COMP_CWORD=$COMP_CWORD \\
                   _APPEAL_COMPLETE=bash {prog} ) )
    return 0
}}
complete -o default -F _appeal_{ident}_completion {prog}
"""


_ZSH_COMPLETION_SCRIPT = """\
_appeal_{ident}_completion() {{
    local -a completions
    completions=("${{(@f)$(COMP_WORDS="${{(pj:\\n:)words}}" \\
                          COMP_CWORD=$((CURRENT-1)) \\
                          _APPEAL_COMPLETE=zsh {prog})}}")
    if (( ${{#completions}} )) && [ -n "${{completions[1]}}" ]; then
        compadd -a completions
    else
        _files
    fi
}}
compdef _appeal_{ident}_completion {prog}
"""


_FISH_COMPLETION_SCRIPT = """\
function _appeal_{ident}_completion
    set -lx _APPEAL_COMPLETE fish
    set -lx COMP_WORDS (commandline -co | string collect)
    set -lx COMP_CWORD (commandline -ct)
    set -l response ({prog})
    if set -q response[1]
        printf '%s\\n' $response
    else
        __fish_complete_path (commandline -ct)
    end
end
complete --command {prog} --no-files \\
    --arguments "(_appeal_{ident}_completion)"
"""


def completion_script(shell, prog):
    """
    The shell function that wires `prog <TAB>` to the reentry
    protocol.  Source its output (or install it in the shell's
    completion directory).  The shell's own filename completion is
    the fallback when the program has no opinion: bash via
    `-o default`, zsh via `_files`, fish via __fish_complete_path.
    (zsh needs compsys loaded--the standard
    `autoload -U compinit && compinit`.)

    Every courier serializes the words the shell is completing as a
    NEWLINE-joined string (a newline can't occur inside a single
    command-line word), so arguments that contain spaces survive
    intact--the reentry splits on newlines, not whitespace.  prog
    is shell-quoted so an odd or hostile program name can't break
    (or inject into) the sourced script.
    """
    import shlex
    ident = ''.join(c if c.isalnum() else '_' for c in prog)
    prog = shlex.quote(prog)
    if shell == 'bash':
        return _BASH_COMPLETION_SCRIPT.format(ident=ident, prog=prog)
    if shell == 'zsh':
        return _ZSH_COMPLETION_SCRIPT.format(ident=ident, prog=prog)
    if shell == 'fish':
        return _FISH_COMPLETION_SCRIPT.format(ident=ident, prog=prog)
    raise AppealConfigurationError(
        f"unsupported completion shell {shell!r} "
        f"(supported: 'bash', 'zsh', 'fish')")


def _split_arg_string(string):
    """
    Split a command line into words the way a shell would, tolerating
    an unterminated quote or escape at the very end--the word the
    user is mid-typing (`prog "New Yo<TAB>`), which a strict parse
    would reject.  The partial token is kept as-is.

    (This is Click's split_arg_string, adopted: the shell hands the
    reentry its word array with the quote CHARACTERS still attached,
    so a plain split would leave `"New York"` quoted; a shell lexer
    recovers the logical value.  shlex is stdlib.)
    """
    import shlex
    lex = shlex.shlex(string, posix=True)
    lex.whitespace_split = True
    lex.commenters = ''
    out = []
    try:
        out.extend(lex)
    except ValueError:
        # end-of-string mid-quote/escape: keep the partial token
        # (it's in lex.token, not yet emitted)
        out.append(lex.token)
    return out


def completion_reentry(completer, prog):
    """
    Answer an _APPEAL_COMPLETE reentry, if this invocation is one:
    prints the candidates (or the sourcing script) and returns an
    exit code.  Returns None if this isn't a reentry.  The caller
    gates on empty argv--the shell's data travels in COMP_WORDS /
    COMP_CWORD, never argv, so a real reentry is always a bare
    invocation.
    """
    import os
    mode = os.environ.get('_APPEAL_COMPLETE')
    if not mode:
        return None
    if mode.startswith('source'):
        shell = mode.partition('_')[2] or 'bash'
        print(completion_script(shell, prog))
        return 0
    # The couriers NEWLINE-join the shell's words so each stays its
    # own line; but the shell hands them over with the QUOTE
    # CHARACTERS still attached ("New York" arrives as one word,
    # literally quoted), so we run the reconstructed line back
    # through a shell lexer to recover the logical value (New York,
    # unquoted).  _split_arg_string tolerates the half-typed current
    # word's unterminated quote.  Because each shell word is on its
    # own line, the lexer's tokens line up with the shell's own word
    # count, so COMP_CWORD still indexes them.
    raw = os.environ.get('COMP_WORDS', '')
    words = _split_arg_string(raw)
    if mode == 'fish':
        # fish can't cheaply produce a word INDEX; its courier
        # sends the current token's TEXT in COMP_CWORD instead
        # (commandline -ct: empty when the cursor follows a space).
        prefix = os.environ.get('COMP_CWORD', '')
        if prefix:
            prefix = _split_arg_string(prefix)[0]
        before = words[1:]
        if prefix and before and before[-1] == prefix:
            before = before[:-1]
    else:
        # bash and zsh answer identically: the courier normalizes
        # the shell's own variables into COMP_WORDS/COMP_CWORD
        # (zsh's 1-based CURRENT becomes 0-based COMP_CWORD in
        # the courier).  A cword past the last token--the cursor
        # sits at a fresh, empty word--yields an empty prefix.
        try:
            cword = int(os.environ.get('COMP_CWORD', '0') or 0)
        except ValueError:
            cword = 0
        before = words[1:cword]
        prefix = words[cword] if 0 <= cword < len(words) else ''
    for candidate in completer(before, prefix):
        print(candidate)
    return 0


def run_mcp(tools, name, version='0'):
    """
    Serve this program's commands as MCP tools: JSON-RPC 2.0 over
    stdio, newline-delimited--the Model Context Protocol's stdio
    transport, stdlib only.  tools maps a tool name to
    (description, input_schema, call) where call takes the
    arguments mapping and returns the result.

    Runs until stdin closes.  Returns 0.
    """
    import json
    import sys

    def reply(id, result=None, error=None):
        message = {'jsonrpc': '2.0', 'id': id}
        if error is not None:
            message['error'] = error
        else:
            message['result'] = result
        sys.stdout.write(json.dumps(message) + '\n')
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        method = request.get('method', '')
        id = request.get('id')
        if method == 'initialize':
            reply(id, {
                'protocolVersion':
                    request.get('params', {}).get('protocolVersion',
                                                  '2024-11-05'),
                'capabilities': {'tools': {}},
                'serverInfo': {'name': name, 'version': version},
            })
        elif method == 'notifications/initialized':
            pass
        elif method == 'ping':
            reply(id, {})
        elif method == 'tools/list':
            reply(id, {'tools': [
                {'name': tool, 'description': description,
                 'inputSchema': schema}
                for tool, (description, schema, call)
                in sorted(tools.items())]})
        elif method == 'tools/call':
            params = request.get('params', {})
            tool = tools.get(params.get('name'))
            if tool is None:
                reply(id, error={'code': -32602,
                                 'message': f"unknown tool "
                                            f"{params.get('name')!r}"})
                continue
            description, schema, call = tool
            try:
                result = call(params.get('arguments') or {})
            except AppealDataError as e:
                reply(id, {'content': [{'type': 'text',
                                        'text': str(e)}],
                           'isError': True})
                continue
            reply(id, {'content': [{'type': 'text',
                                    'text': '' if result is None
                                            else str(result)}]})
        elif id is not None:
            reply(id, error={'code': -32601,
                             'message': f'unknown method {method!r}'})
    return 0


##
## Themes (Larry's design, 2026-08-06): a theme is DATA--a dict
## of StyleSheet entries covering the Markdown concepts and
## appeal's role vocabulary (program, command, option, argument,
## oparg, summary, error).  Colors are palette-independent NAMES
## (red, dark_red, light_red, orange, ...); the palette maps
## them to escapes, so every theme works over every palette.  A
## COMPLETE help stylesheet is the composition
##
##     markdown_defaults | transforms | palette | StyleSheet(theme)
##
## (resolve_stylesheet builds exactly that), and
## Appeal(stylesheet=) takes such a composition and uses it
## VERBATIM.  Painting happens strictly AFTER layout: roles ride
## the baked pieces as style markup, measured styles-stripped,
## resolved by whichever sheet the destination stream deserves.
##
## The colors below are the theme lab's strawmen--Larry's red
## pen has the last word (tools/theme_lab.py is the bench).
##

# appeal_theme: the default.  Designed against the ANSI 16
# (terminal light/dark modes remap those for legibility, so it
# looks right on both).
# the four corners: warm = red/orange/yellow, cool =
# blue/green/cyan, purple in both.  light_* themes use dark_
# colors (dark ink on a light page); dark_* themes use light_.

# (big format_definition_list requires removed 2026-08-15: the
# runtime deflist path is wrap_words' own render_deflist; nothing
# calls format_definition_list any more.  Appeal still imports it
# for the borrowed-trio tests.)
##
## Templates (Larry's single-template model, ruled 2026-08-01).
## ONE template string defines the help page: six {sections}--
## usage, summary, doc, options, arguments, commands--all
## required.  The template establishes the ORDER of the page
## (the Markdown pivot, ruled 2026-08-05); the text between
## placeholders is each section's header, written in MARKDOWN
## ('## Options' by default)--the docstring's own heading
## decoration is input spelling, stripped by the scanner; the
## template dresses the page.  usage is special: not Markdown,
## rendered and wrapped separately.  Empty sections are
## suppressed, header and all.
##

##
## help machinery: assembles and lays out the page (imports big).
##

##
## the converter vocabulary
##
## v1's converter vocabulary: split, validate, validate_range,
## counter, accumulator, mapping.  All semantics probed against
## shipping v1 0.6.4.  Factory *products* carry a `recipe = True`
## marker so build.py recognizes them as vocabulary terminals.
##


def _toy_multisplit_as_pairs(segments, empty):
    # segments alternates non-separator and separator strings,
    # always starting and ending with a (possibly empty)
    # non-separator string.  pair each non-separator string with its
    # subsequent separator--appending the always-empty trailing
    # separator--to make the keep=True 2-tuple form.
    segments.append(empty)
    return list(zip(segments[::2], segments[1::2]))


def _toy_multisplit(s, separators):
    """
    A stdlib-only copy of big.text.toy_multisplit (snipped in so
    the core imports nothing from big--big.text alone costs
    ~14ms to import).  All separators split in one pass, longest
    match wins; returns (text, separator) pairs, keep=True form.
    """
    if not isinstance(separators, (list, tuple)):
        separators = [separators[i:i + 1] for i in range(len(separators))]
    empty = b'' if isinstance(s, bytes) else ''
    if len(separators) == 1:
        segments = []
        sep = separators[0]
        length = len(sep)
        while s:
            index = s.find(sep)
            if index == -1:
                segments.append(s)
                s = None
                break
            segments.append(s[:index])
            segments.append(sep)
            s = s[index + length:]
        if s is not None:
            segments.append(s)
        return _toy_multisplit_as_pairs(segments, empty)

    longest_separator = max(len(sep) for sep in separators)
    separators_by_length = []
    for i in range(longest_separator, -1, -1):
        separators_by_length.append((i, set()))
    for sep in separators:
        separators_by_length[longest_separator - len(sep)][1].add(sep)
    separators_by_length = [t for t in separators_by_length if t[1]]

    segments = []
    word = []

    def flush_word():
        if not word:
            segments.append(empty)
            return
        segments.append(empty.join(word))
        word.clear()

    while s:
        substring = s
        for length, separators_set in separators_by_length:
            substring = substring[:length]
            if substring in separators_set:
                flush_word()
                segments.append(substring)
                s = s[length:]
                break
        else:
            word.append(s[:1])
            s = s[1:]
    flush_word()
    return _toy_multisplit_as_pairs(segments, empty)


def split(*separators, strip=False):
    """
    Creates a converter that splits a string on the separators,
    with big.multisplit's semantics: all separators split in one
    pass (longest match wins), and adjacent separators count as
    one.  With no separators, splits on whitespace.  strip=True
    also strips leading and trailing separators from the string.
    """
    for separator in separators:
        if not (isinstance(separator, str) and separator):
            raise AppealConfigurationError(
                f"split() separators must be nonempty strings, "
                f"not {separator!r}")

    def split_converter(value):
        if not separators:
            # whitespace, like str.split().  (v1 0.6.4 crashes
            # here--multisplit rejects an empty separator tuple--
            # so its docstring's promise is the spec.)
            return value.split()
        # toy_multisplit returns (text, separator) pairs (big
        # 0.14's keep=True form), with empty texts between
        # adjacent separators.  keep the texts; drop the interior
        # empties (adjacent separators count as one); and with
        # strip, drop the boundary empties too (leading and
        # trailing separators).
        texts = [text for text, _ in
                 _toy_multisplit(value, list(separators))]
        last = len(texts) - 1
        values = [text for i, text in enumerate(texts)
                  if text or i == 0 or i == last]
        if strip:
            if values and not values[0]:
                del values[0]
            if values and not values[-1]:
                del values[-1]
        return values
    split_converter.__name__ = 'split'
    split_converter.recipe = True
    return split_converter
split.factory = "split(':')"


def validate(*values, type=None):
    """
    Creates a converter that only accepts the given values.  The
    operand is converted with `type` (default: the type of the
    values, which must be homogeneous) and must equal one of them.
    """
    if not values:
        raise AppealConfigurationError("validate() requires at least one value")
    if type is None:
        types = {v.__class__ for v in values}
        if len(types) > 1:
            raise AppealConfigurationError(
                f"validate() called with non-homogeneous values "
                f"{values!r}; pass type= to disambiguate")
        type = values[0].__class__

    def validate_converter(value):
        value = type(value)
        if value not in values:
            allowed = ', '.join(repr(v) for v in values)
            raise ValueError(f"must be one of {allowed}")
        return value
    validate_converter.__name__ = 'validate'
    validate_converter.recipe = True
    return validate_converter
validate.factory = "validate('red', 'green')"


def validate_range(start, stop=None, *, type=None, clamp=False):
    """
    Creates a converter that checks start <= value <= stop.  With
    one argument, the range is 0..start.  clamp=True pins
    out-of-range values to the nearest bound instead of erroring.
    """
    if stop is None:
        start, stop = start.__class__(0), start
    if type is None:
        type = start.__class__

    def validate_range_converter(value):
        value = type(value)
        if value < start:
            if clamp:
                return start
            raise ValueError(f"must be in range {start}..{stop}")
        if value > stop:
            if clamp:
                return stop
            raise ValueError(f"must be in range {start}..{stop}")
        return value
    validate_range_converter.__name__ = 'validate_range'
    validate_range_converter.recipe = True
    return validate_range_converter
validate_range.factory = "validate_range(0, 10)"


def counter(*, max=None, step=1):
    """
    Creates a repeatable flag-like option that counts occurrences:
    -v -v -v with step=2 gives 6, capped at max.
    """
    ceiling = max

    class Counter(MultiOption):
        recipe = True

        def init(self, default):
            self.value = default if isinstance(default, int) else 0

        def option(self):
            self.value += step

        def __call__(self):
            if ceiling is not None and self.value > ceiling:
                return ceiling
            return self.value
    Counter.__name__ = 'counter'
    return Counter
counter.factory = "counter()"


class _ProcessStream:
    """
    The safe wrapper file() puts around a process-standard stream
    (sys.stdin or sys.stdout, spelled '-' on the command line).
    Everything delegates to the stream, so it reads and writes
    like the file it stands in for--but close() FLUSHES and goes
    inert, and so does leaving a `with` block.  The stream
    belongs to the process, not to one command line; your
    function gets one uniform contract: whatever file() hands
    you, you may close.
    """
    def __init__(self, stream):
        self._stream = stream
        self._closed = False

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def __iter__(self):
        return iter(self._stream)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        # close() promises a flush--an inert close that skipped
        # it would silently lose buffered output
        if not self._closed:
            self._closed = True
            try:
                self._stream.flush()
            except (OSError, ValueError):
                pass

    @property
    def closed(self):
        return self._closed

    def __repr__(self):
        return f'<_ProcessStream {self._stream!r}>'


def file(mode='r', *, buffering=-1, encoding=None, errors=None,
         newline=None, opener=None):
    """
    Creates a converter that opens its argument with open(),
    passing these arguments along (open()'s parameters, spelled
    out; closefd is omitted because it's only legal for file
    descriptors, and this converter always opens a path).

    '-' means the process-standard stream instead: sys.stdin for
    reading modes, sys.stdout for writing modes ('b' modes get
    the .buffer layer), wrapped in _ProcessStream so close() is
    safe.  '-' with a '+' mode refuses--the standard streams
    aren't read-write.  (And /dev/stderr needs no convention:
    it's a path, so the ordinary open() branch handles it.)
    """
    reads = 'r' in mode and '+' not in mode
    writes = (('w' in mode or 'a' in mode or 'x' in mode)
              and '+' not in mode)

    def file_converter(value):
        if value == '-':
            if reads:
                stream = sys.stdin
            elif writes:
                stream = sys.stdout
            else:
                raise ValueError(
                    f"can't open '-' with mode {mode!r} (the "
                    f"standard streams aren't read-write)")
            if 'b' in mode:
                stream = stream.buffer
            return _ProcessStream(stream)
        try:
            return open(value, mode, buffering=buffering,
                        encoding=encoding, errors=errors,
                        newline=newline, opener=opener)
        except OSError as e:
            raise ValueError(
                f"can't open {value!r}: {e.strerror or e}") from None
    file_converter.__name__ = 'file'
    if opener is None:
        file_converter.recipe = True
    return file_converter
file.factory = "file()"


class _OptionalMeta(type):
    "optional[T] parameterizes via metaclass __getitem__ (3.6-safe)."
    def __getitem__(cls, T):
        return cls._parameterize(T)


class optional(metaclass=_OptionalMeta):
    """
    Marks an option's oparg as OPTIONAL, make-style (`-j [jobs]`):
    annotate the parameter with optional[T].  Absent: the
    parameter's own default.  Bare: T() -- int gives 0, str gives
    ''.  With a value: T(value).  Opargs are REQUIRED by default
    (ruled 2026-08-03, the make precedent: `-f file` is the
    common case, `-j [jobs]` is the marked one).
    """
    factory = "optional[str]"

    @classmethod
    def _parameterize(cls, T):
        if isinstance(T, tuple):
            raise AppealConfigurationError(
                "optional[...] takes exactly one converter")
        if not callable(T):
            raise AppealConfigurationError(
                f"optional[...]: {T!r} isn't callable")
        # None is the no-oparg sentinel: an operand that WAS
        # given always arrives as a str, never the None object
        def option_value(value: str = None):
            if value is None:
                return T()
            try:
                return T(value)
            except (ValueError, TypeError):
                # a conversion failure is the USER's error, not a
                # crash (the greedy oparg ate the wrong token)
                name = getattr(T, '__name__', 'value')
                raise UsageError(
                    f"invalid value {value!r} "
                    f"(not a valid {name})") from None
        # usage metavar: show the OPTION'S parameter name, not
        # this closure's ('[-j|--jobs [jobs]]', not '[value]')
        option_value.borrows_name = True
        option_value.recipe = True
        return option_value


class _Subscriptable(type):
    """
    v1's crazy science magic, restored for Python 3.6:
    accumulator[int] parameterizes via a metaclass __getitem__,
    because __class_getitem__ (PEP 560) only exists from 3.7.
    The metaclass serves every version, so it's the ONLY spelling.
    """
    def __getitem__(cls, types):
        return cls._parameterize(types)


def _accumulator_option(types):
    "accumulator[...]'s option(): one operand per type, appended."
    names = [f'v{i}' for i in range(len(types))]
    params = ', '.join(f'{n}: _t{i}' for i, n in enumerate(names))
    namespace = {f'_t{i}': t for i, t in enumerate(types)}
    payload = names[0] if len(names) == 1 else f'({", ".join(names)})'
    exec(f'def option(self, {params}):\n'
         f'    self.values.append({payload})', namespace)
    return namespace['option']


class accumulator(MultiOption, metaclass=_Subscriptable):
    """
    A repeatable option collecting values into a list.  Subscript
    for types: accumulator[int] collects ints; accumulator[int, str]
    collects (int, str) tuples, two operands per occurrence.

    list[T] is sugar for accumulator[T]--one repeatable-list
    mechanism, spelled either way.
    """

    def init(self, default):
        self.values = list(default) if default else []

    def option(self, value):
        self.values.append(value)

    def __call__(self):
        return self.values

    @classmethod
    def _parameterize(cls, types):
        if not isinstance(types, tuple):
            types = (types,)
        sub = _Subscriptable('accumulator', (cls,),
                             {'option': _accumulator_option(types)})
        sub.recipe = True
        return sub


class mapping(MultiOption, metaclass=_Subscriptable):
    """
    A repeatable option collecting KEY=VALUE pairs into a dict:
    --define KEY=VALUE.  ONE operand per occurrence, split on the
    first '='.  Subscript for the halves' types: mapping[str, int]
    maps strs to ints.

    dict[K, V] is sugar for mapping[K, V]--one repeatable-dict
    mechanism, spelled either way; the config layer reads it from a
    dict, the command line from KEY=VALUE tokens.
    """
    # the reader (config layer) hands these a whole dict, not a
    # sequence of occurrences; the two halves' converters live on
    # the parameterized subclass (below), invisible to the fold's
    # single-operand protocol but caught by the recipe fingerprint.
    mapping = True
    _key_converter = staticmethod(str)
    _value_converter = staticmethod(str)

    def init(self, default):
        self.values = dict(default) if default else {}

    def option(self, item):
        key_text, equals, value_text = item.partition('=')
        if not equals:
            raise ValueError(f"{item!r}: expected KEY=VALUE")
        key = self._key_converter(key_text)
        if key in self.values:
            raise ValueError(f"key {key_text!r} defined more than once")
        self.values[key] = self._value_converter(value_text)

    def __call__(self):
        return self.values

    @classmethod
    def _parameterize(cls, types):
        if not isinstance(types, tuple) or len(types) != 2:
            raise TypeError("mapping[...] needs exactly a key type and "
                            "a value type (one KEY=VALUE per occurrence)")
        key_type, value_type = types
        sub = _Subscriptable('mapping', (cls,),
                             {'_key_converter': staticmethod(key_type),
                              '_value_converter': staticmethod(value_type)})
        sub.recipe = True
        return sub


# ====================================================================
#  The data-driven back-end engine.  The work-item
#  classes wear an `Instruction` suffix; the self.X factory methods on
#  Converter keep the bare names.
# ====================================================================
class ArgumentInstruction:
    """
    A positional operand.  A leading-dash token here is an OPTION.  A
    trailing Argument (keyword-only-no-default parameter) instead draws
    from its owner's end-pocket and is delivered as a keyword argument.
    """
    __slots__ = ('owner', 'name', 'converter', 'required', 'slot', 'trailing')
    def __init__(self, owner, name, converter, *, required, trailing=False):
        self.owner = owner
        self.name = name
        self.converter = converter
        self.required = required
        self.slot = name
        self.trailing = trailing

class OpargInstruction:
    "An option's value: a raw grab (a leading-dash token is a literal)."
    __slots__ = ('owner', 'name', 'converter')
    def __init__(self, owner, name, converter):
        self.owner = owner
        self.name = name
        self.converter = converter

class OptionInstruction:
    """
    Registers all of an option's `strings` against an already-built owner.
    The converter is always explicit (pulled from the callable's annotation)
    and tells the runtime how the option behaves: `bool` -> a flag (presence,
    stores `not default`, no oparg); a MultiOption class -> accumulate; a
    Converter class -> a converter-group option; anything else -> a value
    option (`--units F`: raw-grab one oparg, convert).  One binding is shared
    across the strings (so -v and --verbose feed the same MultiOption).
    """
    __slots__ = ('owner', 'name', 'converter', 'strings')
    def __init__(self, owner, name, converter, strings):
        self.owner = owner
        self.name = name
        self.converter = converter
        self.strings = strings
    def register(self, processor):
        conv = self.converter
        if conv is bool:
            binding = LiveBinding(self.owner, self.name)
        elif isinstance(conv, type) and issubclass(conv, Converter):
            binding = GroupBinding(self.owner, self.name, conv, self.strings)
        elif isinstance(conv, type) and issubclass(conv, MultiOption):
            binding = MultiBinding(self.owner, self.name, conv)
        else:
            binding = ValueBinding(self.owner, self.name, conv)
        for string in self.strings:
            processor.handlers[string] = binding

class PreOptionInstruction:
    "Registers a conjure: fire before the converter exists to summon one."
    __slots__ = ('owner', 'string', 'name', 'slot', 'converter_cls',
                 'converter')
    def __init__(self, owner, string, name, slot, converter_cls,
                 converter=None):
        self.owner = owner
        self.string = string
        self.name = name
        self.slot = slot
        self.converter_cls = converter_cls
        self.converter = converter          # set -> a value option (forward
                                            # oparg); None -> a flag (conjure)
    def register(self, processor):
        if self.converter is not None:
            processor.handlers[self.string] = ConjureValueBinding(
                self.name, self.slot, self.converter_cls, self.converter)
        else:
            processor.handlers[self.string] = ConjureBinding(
                self.name, self.slot, self.converter_cls)

class RepeatInstruction:
    "A *args template ([PreOption?, Argument]) re-laid before each element."
    __slots__ = ('items',)
    def __init__(self, items):
        self.items = items


class _Collector:
    "A stand-in processor that just records a converter's laid instructions."
    __slots__ = ('items',)
    def __init__(self):
        self.items = []
    def prepend(self, items):
        self.items.extend(items)


def _own_shape(converter_cls):
    """
    A converter's own options and required leaf operands, read by dry-running
    its register (the SAME instructions the parser runs -- no plan needed).
    Returns (name -> option strings, [required operand name, ...]).
    """
    coll = _Collector()
    converter_cls().register(coll)
    options = {}
    operands = []
    for item in coll.items:
        if isinstance(item, RepeatInstruction):     # a *args template: its lone
            continue                                # element isn't THIS converter
        if isinstance(item, OptionInstruction):
            options[item.name] = list(item.strings)
        elif isinstance(item, PreOptionInstruction):
            options.setdefault(item.name, []).append(item.string)
        elif isinstance(item, ArgumentInstruction) and item.required:
            operands.append(item.name)
    return options, operands


_capacity_cache = {}

def _group_capacity(converter_cls):
    "How many operands a group option's converter takes (dry-run its register)."
    n = _capacity_cache.get(converter_cls)
    if n is None:
        coll = _Collector()
        converter_cls().register(coll)
        n = sum(1 for it in coll.items if isinstance(it, ArgumentInstruction))
        _capacity_cache[converter_cls] = n
    return n


def _valid_counts(converter_cls):
    """
    The set of valid TOTAL operand counts for a group option's converter --
    v1's "judge the count after grabbing".  A required leaf adds exactly 1; an
    optional leaf adds 0 or 1; a required group adds its own valid counts; an
    optional group adds those or 0.  grp2(a, i: inner=(p, q)) -> {1, 3}.
    """
    coll = _Collector()
    converter_cls().register(coll)
    counts = {0}
    for item in coll.items:
        if not isinstance(item, ArgumentInstruction):
            continue
        if isinstance(item.converter, type) and issubclass(item.converter,
                                                            Converter):
            sub = _valid_counts(item.converter)
            if not item.required:
                sub = sub | {0}
        elif item.required:
            sub = {1}
        else:
            sub = {0, 1}
        counts = {c + s for c in counts for s in sub}
    return counts


def _count_list(counts):
    "Render valid counts as '1 or 3' / '1, 2, or 4'."
    nums = sorted(counts)
    if len(nums) == 1:
        return str(nums[0])
    if len(nums) == 2:
        return f"{nums[0]} or {nums[1]}"
    return ', '.join(map(str, nums[:-1])) + f", or {nums[-1]}"


def _takes_many(binding):
    "Does this option take more than one oparg (so it can't be attached)?"
    if isinstance(binding, GroupBinding):
        return _group_capacity(binding.converter_cls) > 1
    if isinstance(binding, ValueBinding) and isinstance(binding.converter, tuple):
        return len(binding.converter) - 1 > 1       # (constructor, *leaves)
    return False


def _operand_list(names):
    "Render required operand names as '<A> <B> and <C>' (usage's default form)."
    toks = [f'<{n.upper()}>' for n in names]
    if len(toks) <= 1:
        return ''.join(toks)
    return ' '.join(toks[:-1]) + ' and ' + toks[-1]


def _availability_message(owner):
    """
    A summoned converter starved for operands: the option that summoned it
    isn't available until its group's required operands are supplied.  Built
    from the starved converter itself -- it knows its own options and operands.
    """
    options, operands = _own_shape(type(owner))
    spoken = None                                   # the option string the user
    for name in owner.kwargs:                       # actually typed (set a kwarg)
        strings = options.get(name)
        if strings:
            spoken = next((s for s in strings if s.startswith('--')), strings[0])
            break
    ops = _operand_list(operands)
    if spoken and ops:
        return f"{spoken} only becomes available if you specify {ops}"
    if spoken:
        return f"{spoken} isn't available here"
    return f"expected {ops}" if ops else "expected an argument"


def _presence(instance, name):
    "A flag's presence value: `not default`, read live off the callable."
    host = _params_host(type(instance).converter)   # a class: its __init__
    default = (getattr(host, '__kwdefaults__', None) or {}).get(name, False)
    return not default

def _default(instance, name):
    "The parameter's default, read live off the callable (for init)."
    host = _params_host(type(instance).converter)
    return (getattr(host, '__kwdefaults__', None) or {}).get(name)

def _positional_default(instance, name):
    """
    A positional parameter's default, read live off the callable.  A skipped
    optional positional slot appends this so later slots (e.g. a conjured group)
    stay positionally aligned -- "signature default fills the tail" is false once
    conjuring can fill a slot to the right of a skipped one.
    """
    host = _params_host(type(instance).converter)
    code = host.__code__
    names = code.co_varnames[:code.co_argcount]
    defaults = host.__defaults__ or ()
    return defaults[names.index(name) - (code.co_argcount - len(defaults))]


class LiveBinding:
    "Invoke -> set the flag; `--flag=false` gives an explicit boolean."
    __slots__ = ('instance', 'name')
    def __init__(self, instance, name):
        self.instance = instance
        self.name = name
    def invoke(self, processor, value=None):
        if value is None:
            self.instance.kwargs[self.name] = _presence(self.instance, self.name)
            return
        if value not in ('true', 'false'):          # ruled: only these two
            raise UsageError(
                f"option {self.name!r} expected 'true' or 'false'", None)
        self.instance.kwargs[self.name] = (value == 'true')

class ValueBinding:
    """
    Invoke -> one leaf oparg (`--units F`), or a multi-oparg option whose
    converter is a `(constructor, leaf1, leaf2, ...)` tuple (`--where X Y`,
    `--coord 3 4`): raw-grab one oparg per leaf, convert each, then build the
    value -- `tuple(args)` for a tuple option, else `constructor(*args)`.
    """
    __slots__ = ('instance', 'name', 'converter')
    def __init__(self, instance, name, converter):
        self.instance = instance
        self.name = name
        self.converter = converter
    def invoke(self, processor, value=None):
        conv = self.converter
        if isinstance(conv, tuple):
            self.instance.kwargs[self.name] = self._multi(processor, conv, value)
            return
        if value is None:
            if processor.peek() is None:
                raise UsageError(f"option {self.name!r} requires a value", None)
            value = processor.advance()                 # raw: no option check
        # value options convert eagerly, per occurrence: a repeated option
        # validates EVERY value (ruled 2026-08-16, "not called validate for
        # nothing"), last wins.  (Positional leaves defer; options don't.)
        self.instance.kwargs[self.name] = processor._cv(conv, value, self.name)
    def _multi(self, processor, conv, value):
        constructor, leaves = conv[0], conv[1:]
        if not leaves:                                  # a nullary converter
            if value is not None:                       # (--north): presence IS
                raise UsageError(                       # the value; '=' is refused
                    f"option {self.name!r} doesn't take a value", None)
            return constructor()
        texts = [value] if value is not None else []    # =value/attached is first
        while len(texts) < len(leaves):
            if processor.peek() is None:
                raise UsageError(
                    f"option {self.name!r} requires {len(leaves)} values", None)
            texts.append(processor.advance())           # raw grab
        args = [processor._cv(leaf, text, self.name)
                for leaf, text in zip(leaves, texts)]
        if processor.dry:                           # structural pre-scan: opargs
            return None                             # counted, converter deferred
        if constructor is tuple:
            return tuple(args)
        try:                                        # the converter body's own
            return constructor(*args)               # ValueError/TypeError is a
        except (ValueError, TypeError) as e:        # polite usage error
            name = getattr(constructor, '__name__', 'converter')
            raise UsageError(f"not a valid {name}: {str(e) or name}",
                             None) from None

_oparg_converters_cache = {}

def _oparg_converters(factory):
    """
    A MultiOption's per-occurrence oparg converters, from its option()
    parameters -- read off __code__/__annotations__ (no `inspect`, which
    costs ~7ms to import) and cached ONCE per type, not every parse.
    """
    cached = _oparg_converters_cache.get(factory)
    if cached is None:
        option = factory.option
        code = option.__code__
        names = code.co_varnames[1:code.co_argcount]    # skip self
        annotations = option.__annotations__
        converters = tuple(annotations.get(name, str) for name in names)
        minimum = len(names) - len(option.__defaults__ or ())   # optional tail
        cached = (converters, minimum)
        _oparg_converters_cache[factory] = cached
    return cached


class MultiBinding:
    """
    Invoke -> feed a persistent MultiOption (counter/accumulator/mapping).
    The instance is created lazily on first occurrence (so an unused
    option leaves the parameter's default untouched), init()'d with that
    default, and fed once per occurrence.  render() happens at finalize.
    """
    __slots__ = ('owner', 'name', 'factory', 'converters', 'minimum')
    def __init__(self, owner, name, factory):
        self.owner = owner
        self.name = name
        self.factory = factory
        self.converters, self.minimum = _oparg_converters(factory)
    def invoke(self, processor, value=None):
        instance = self.owner.kwargs.get(self.name)     # MultiOptions live in
        if instance is None:                            # kwargs now, rendered
            instance = self.factory()                   # like any deferred value
            if not processor.dry:                       # init is user code
                instance.init(_default(self.owner, self.name))
            self.owner.kwargs[self.name] = instance
        opargs = []
        if value is not None:                           # =value / attached
            if not self.converters:                     # a 0-arity fold (counter)
                raise UsageError(
                    f"option {self.name!r} doesn't take a value", None)
            opargs = [processor._cv(self.converters[0], value, self.name)]
        else:
            # grab the required opargs; then any OPTIONAL ones greedily, so long
            # as a token exists (an Option subclass's `option(x, y='Y')` -- y is
            # taken if present, defaulted if not).  -- and end-of-line decline.
            for k, converter in enumerate(self.converters):
                tok = processor.peek()
                if tok is None or tok == '--':
                    if k < self.minimum:
                        need = self.minimum
                        raise UsageError(
                            f"option {self.name!r} requires "
                            f"{'a value' if need == 1 else f'{need} values'}",
                            None)
                    break                               # optional tail: stop
                opargs.append(processor._cv(converter, processor.advance(),
                                            self.name))
        if processor.dry:                       # opargs counted; folding deferred
            return
        try:
            instance.option(*opargs)
        except (ValueError, TypeError) as e:    # wrap the option body's error
            raise UsageError(f"{self.name}: {e}", None)

class GroupBinding:
    """
    Invoke -> a converter-group option (`--e1: extras`).  Build the child
    converter, store it as the option's value, and register ITS options so
    the shared child options (--verbose/--label) now bind to this instance.
    Siblings fall out of the flat table: --e2 re-registers them at e2.  No
    summon -- a shared option before any --e1/--e2 has no handler (error).
    """
    __slots__ = ('owner', 'name', 'converter_cls', 'strings')
    def __init__(self, owner, name, converter_cls, strings=()):
        self.owner = owner
        self.name = name
        self.converter_cls = converter_cls
        self.strings = strings
    def invoke(self, processor, value=None):
        instance = self.converter_cls()
        instance._optarg = True                     # its operands are option
        if value is not None:                       # opargs (grab greedily); an
            instance._attached = value              # attached -j5/--jobs=5 feeds
        instance._optarg_root = self.converter_cls  # the first operand directly
        instance._opt_display = next(               # name the short form for the
            (s for s in self.strings if not s.startswith('--')),
            self.strings[0] if self.strings else self.name)
        self.owner.kwargs[self.name] = instance
        processor.enter(instance)                   # register its options

class ConjureBinding:
    "Invoke -> conjure a fresh converter on its defaults, stashed by slot."
    __slots__ = ('name', 'slot', 'converter_cls')
    def __init__(self, name, slot, converter_cls):
        self.name = name
        self.slot = slot
        self.converter_cls = converter_cls
    def invoke(self, processor, value=None):
        obj = processor.conjured.get(self.slot)
        if obj is None:
            obj = self.converter_cls()
            obj._summoned = True
            processor.conjured[self.slot] = obj
        obj.kwargs[self.name] = (_presence(obj, self.name) if value is None
                                 else value == 'true')

class ConjureValueBinding:
    """
    A windowed *args group's VALUE option (`--label up`) at a window boundary:
    grab one oparg, convert it, and set it on a conjured FORWARD instance that
    the next operand's Argument will pick up.  The mid-instance ValueBinding
    (registered when a window is entered) is overwritten by this at the
    boundary, exactly as a flag's ConjureBinding overwrites its LiveBinding.
    """
    __slots__ = ('name', 'slot', 'converter_cls', 'converter')
    def __init__(self, name, slot, converter_cls, converter):
        self.name = name
        self.slot = slot
        self.converter_cls = converter_cls
        self.converter = converter
    def invoke(self, processor, value=None):
        obj = processor.conjured.get(self.slot)
        if obj is None:
            obj = self.converter_cls()
            obj._summoned = True
            processor.conjured[self.slot] = obj
        if value is None:
            if processor.peek() is None:
                raise UsageError(f"option {self.name!r} requires a value", None)
            value = processor.advance()             # raw: no option check
        obj.kwargs[self.name] = processor._cv(self.converter, value, self.name)


# ---- the converter base --------------------------------------------
class Converter:
    """
    Base for a generated command or converter.  There is a 1:1 mapping
    between a user callable and its Converter subclass, so the callable is a
    class attribute `converter`, wired ONCE by fixup_converters at
    @command time (never at parse time).  It's reached via type(self).
    converter -- a bare function on a class binds as a method, so never
    self.converter.  A subclass adds register(self, processor), which pushes
    its work via processor.prepend([...]) using the self.X factories.
    register is called by the Argument that enters this converter -- never by
    a PreOption (conjuring builds the object but defers its work).
    """
    trailing = 0                        # count of this converter's own
                                        # trailing operands (emitter sets it)
    _iterable = False                   # tuple[...]/list[...] group: build from
                                        # the args iterable, don't splat them
    converter = None                    # the user's callable, wired by
                                        # fixup_converters at @command time
    binds = None                        # a method command: the env key of the
                                        # instance to pass as self (class-as-app)
    constructs = None                   # a class command: the env key to stash
                                        # the instance it builds under
    _bound_inner = False                # a BoundInnerClass: construct THROUGH
                                        # the bound parent instance, not plainly
    _window = False                     # set on an instance built as one element
                                        # of a *args window; a starved required
                                        # operand of a window is "left over", not
                                        # a plain missing argument
    _summoned = False                   # set on an instance CONJURED by naming one
                                        # of its options (not started by an
                                        # operand); if it then starves for lack of
                                        # operands, the option wasn't yet available
    _optarg = False                     # set on a group entered as an OPTION's
                                        # oparg (make -j): its operands grab the
                                        # next token unconditionally (even -5/-v),
                                        # only end-of-line or `--` declining
    _attached = None                    # an attached oparg value (-j5 / --jobs=5):
                                        # feeds the group's first operand directly
    _opt_display = None                 # the option string that summoned an oparg
                                        # group ('-g'), for its count-error message
    _optarg_root = None                 # the top oparg group's class, for computing
                                        # the valid operand counts (nested groups)

    @classmethod
    def fixup_converters(cls, converter):
        """
        Wire the callable onto the class, and wire every child converter it
        reaches (via the generated _fixup_children, guarded so a shared child
        wires once).  Called from @command; the 1:1 mapping makes the class the
        callable's home.
        """
        cls.converter = converter
        cls._fixup_children(converter)

    @classmethod
    def _fixup_children(cls, converter):
        "Wire this converter's child converters (generated override; base no-op)."

    def __init__(self):
        self.args = []                  # positional operands (eagerly converted),
                                        # plus child Converters for group slots;
                                        # groups/folds finalized in __call__
        self.kwargs = {}                # options by name -- the SOLE memory for
                                        # options (a MultiOption lives here too,
                                        # rendered like everything else)
        self.reserve = []               # this converter's end-pocket (trailing)
        self.bound = None               # a method command's instance (self)

    # work-item factories -- owner is self, bound implicitly.  A group
    # operand/option passes its child Converter subclass as its converter;
    # a leaf passes a plain callable.
    def Argument(self, name, converter, *, required, trailing=False):
        return ArgumentInstruction(self, name, converter, required=required,
                        trailing=trailing)
    def Oparg(self, name, converter):
        return OpargInstruction(self, name, converter)
    def Option(self, name, converter, *strings):
        return OptionInstruction(self, name, converter, strings)
    def PreOption(self, string, name, slot, converter_cls, converter=None):
        return PreOptionInstruction(self, string, name, slot, converter_cls,
                                    converter)
    def Repeat(self, items):
        return RepeatInstruction(items)

    def __call__(self):
        "Render (phase 2): finalize every deferred value, then call the callable."
        def render(v):
            # the deferred values are zero-arg callables: a child Converter (a
            # group) or a MultiOption (a fold, living in kwargs); leaves were
            # converted eagerly and are already final.  A group's constructor
            # body raising ValueError/TypeError is a POLITE usage error ('not a
            # valid spot'); anything else passes through.
            if isinstance(v, Converter):
                try:
                    return v()
                except (ValueError, TypeError) as e:
                    name = getattr(type(v).converter, '__name__', 'converter')
                    raise UsageError(
                        f"not a valid {name}: {e or name}", None) from None
            if isinstance(v, Option):
                return v()
            return v
        for name in list(self.kwargs):
            self.kwargs[name] = render(self.kwargs[name])
        args = [render(a) for a in self.args]
        conv = type(self).converter
        if type(self)._iterable:            # tuple[...]/list[...]: build from the iterable
            return conv(args)
        if type(self)._bound_inner:
            # a BoundInnerClass: the compiled converter is bound to a throwaway
            # probe (build only needed its grammar).  Construct through the REAL
            # parent instance's attribute, which re-binds the descriptor.
            inner = type(self).constructs.rpartition('.')[2]
            return getattr(self.bound, inner)(*args, **self.kwargs)
        if type(self).binds is not None and type(self).constructs is None:
            # a method command: self is the instance a parent constructed.  A
            # nested CLASS command also has binds (it's a subcommand) but must
            # construct plainly -- it doesn't take the outer instance as self.
            # (A BoundInnerClass, which DOES construct through the outer, is a
            # known-unported edge -- build hands the compiler a _Probe-bound
            # grammar, not the real descriptor.)
            return conv(self.bound, *args, **self.kwargs)
        return conv(*args, **self.kwargs)


# ---- the engine ----------------------------------------------------
class Processor:
    def __init__(self, argv, root, commands=(), dry=False):
        self.argv = list(argv)
        self.pos = 0
        self.end = len(self.argv)       # exclusive: trailing pockets shrink it
        self.queue = collections.deque()
        self.handlers = {}
        self.conjured = {}
        self.force_positional = False
        self.reserved = 0               # trailing operands lifted out of the
                                        # stream (option-aware pocket); counted
                                        # in `consumed` since they never hit pos
        self.root = root
        self.commands = commands        # command words: the saturation boundary
        self.dry = dry                  # the whole-line STRUCTURAL pre-scan:
                                        # parcel + validate arity, running NO
                                        # converter/fold/callable (so a bad value
                                        # or a converter side effect is deferred
                                        # to the live pass).  Structural errors
                                        # -- counts, unknown options, an option
                                        # missing its oparg -- still raise here.

    def _cv(self, converter, text, name):
        "convert(), or a raw passthrough during the dry structural pre-scan."
        return text if self.dry else convert(converter, text, name)

    def prepend(self, items):
        "Push work onto the FRONT, preserving order (a la rextend)."
        # flat recognition (Larry's v2 ruling): an option is recognized anywhere
        # on the line, even before its converter is entered.  Register a batch's
        # options into the handlers table eagerly, not only when they reach the
        # queue front, so `--dashed dot 2.5` knows --dashed before `dot` fills.
        for item in items:
            if isinstance(item, (OptionInstruction, PreOptionInstruction)):
                item.register(self)
            elif isinstance(item, RepeatInstruction):
                # a *args window's options live inside its Repeat; register them
                # too so `--dashed 1 2 3` knows --dashed before the window lays.
                for inner in item.items:
                    if isinstance(inner, (OptionInstruction,
                                          PreOptionInstruction)):
                        inner.register(self)
        self.queue.extendleft(reversed(items))

    def peek(self):
        return self.argv[self.pos] if self.pos < self.end else None

    def advance(self):
        tok = self.argv[self.pos]; self.pos += 1; return tok

    def _is_option(self, tok):
        if not tok.startswith('-') or tok in ('-', '--'):
            return False
        # a negative number ('-2', '-2.5') is an OPERAND, not an option --
        # unless a matching short option is actually registered (v1's rule,
        # parse_tokens)
        if tok[1].isdigit() and ('-' + tok[1]) not in self.handlers:
            return False
        return True

    def _owns_option(self, tok):
        "Does this option token name one of the converter's registered options?"
        if tok.startswith('--'):
            return tok.partition('=')[0] in self.handlers
        return len(tok) > 1 and ('-' + tok[1]) in self.handlers

    def _span_arity(self, binding):
        "How many space-separated opargs an option consumes (for the pocket scan)."
        if binding is None:
            return None
        if isinstance(binding, (LiveBinding, ConjureBinding)):
            return 0
        if isinstance(binding, MultiBinding):
            return len(binding.converters)
        if isinstance(binding, GroupBinding):
            return _group_capacity(binding.converter_cls)
        if isinstance(binding, ValueBinding) and isinstance(binding.converter,
                                                            tuple):
            return len(binding.converter) - 1
        return 1                        # ValueBinding leaf / ConjureValueBinding

    def _option_span(self, i):
        """
        How many argv tokens the option at index i occupies (itself plus its
        space-separated opargs), or None when it's an option we don't own (a
        boundary).  Used only by the trailing-reservation scan to tell operands
        apart from option machinery, so an attached/bundled approximation is
        fine -- the live loop still does the real parse.
        """
        tok = self.argv[i]
        if tok.startswith('--'):
            name, eq, _ = tok.partition('=')
            binding = self.handlers.get(name)
            if binding is None:
                return None
            if eq:
                return 1                # --opt=value: no following opargs
            return 1 + self._span_arity(binding)
        binding = self.handlers.get('-' + tok[1])
        if binding is None:
            return None
        arity = self._span_arity(binding)
        if arity == 0 or len(tok) > 2:
            return 1                    # a flag bundle (-vd) or attached (-j5)
        return 1 + arity

    def enter(self, converter):
        """
        Register the converter's options, then reserve its trailing operands:
        the LAST N OPERANDS still in the stream, skipping options and their
        opargs.  Operand-aware (unlike a blind end-pocket), so `cp a b dst
        --verbose` reserves `dst`, not `--verbose`.  Reserved operands are
        lifted out of the [pos:end) window (the front/*args fill skips them)
        and delivered to the trailing Arguments by keyword.
        """
        converter.register(self)
        if not converter.trailing:
            return
        operand_indices = []
        i = self.pos
        forced = self.force_positional
        while i < self.end:
            tok = self.argv[i]
            if not forced and tok == '--':
                forced = True; i += 1; continue
            if forced or not self._is_option(tok):
                operand_indices.append(i); i += 1; continue
            span = self._option_span(i)
            if span is None:
                break                   # an option we don't own: our boundary
            i += span
        take = operand_indices[-converter.trailing:]
        if not take:
            return
        reserved = set(take)
        converter.reserve.extend(self.argv[j] for j in take)
        middle = [self.argv[j] for j in range(self.pos, self.end)
                  if j not in reserved]
        self.argv = self.argv[:self.pos] + middle + self.argv[self.end:]
        self.end = self.pos + len(middle)
        self.reserved += len(take)

    @property
    def consumed(self):
        "Tokens this processor claimed: the front it advanced through, the"
        " untouched tail past `end`, and the trailing operands it lifted out."
        return self.pos + (len(self.argv) - self.end) + self.reserved

    def run(self):
        self.enter(self.root)
        self._loop()
        if self.dry:                    # structural pre-scan: no render, no call
            return None
        return self.root()

    def _loop(self):
        while True:
            # advance past non-Argument items, registering their options
            while self.queue and isinstance(self.queue[0], (OptionInstruction, PreOptionInstruction)):
                self.queue.popleft().register(self)

            front = self.queue[0] if self.queue else None
            if isinstance(front, RepeatInstruction):
                self.queue.popleft()
                self.prepend(front.items + [front])       # re-lay for one element
                continue

            # an option's greedy oparg (make -j): fill it directly, so a
            # dash-looking token (-5, -v) becomes its value instead of being
            # parsed as an option.  _fill_argument declines on `--`/end-of-line.
            if (isinstance(front, ArgumentInstruction) and front.owner._optarg
                    and not (isinstance(front.converter, type)
                             and issubclass(front.converter, Converter))):
                self._fill_argument(front)
                continue

            tok = self.peek()
            if tok == '--' and not self.force_positional:
                if not self.queue:
                    # saturated: a `--` is only meaningful to a consumer with
                    # operands left to force positional.  An options-only era
                    # (a help/version precommand) must leave it for the command
                    # that follows, or the `--` is lost and its operands parse
                    # as options (test_double_dash_state_never_leaks).
                    return
                self.advance(); self.force_positional = True; continue

            if (tok is not None and not self.force_positional
                    and self._is_option(tok)):
                if not self.queue and not self._owns_option(tok):
                    return          # saturated + an option we don't own: this
                                    # era/command's boundary is over -- yield it
                self._invoke_option(); continue

            if front is None:
                # saturated: this era/command owns nothing more here.  Yield
                # whatever's next (a command word, a later era's option or
                # argument, or a stray) to execute, which diagnoses it --
                # commands never start with a dash.
                return

            self._fill_argument(front)

    def _invoke_option(self):
        tok = self.advance()
        if tok.startswith('--'):                        # long: --foo or --foo=bar
            value = None
            if '=' in tok:
                tok, _, value = tok.partition('=')
            binding = self.handlers.get(tok)
            if binding is None:
                longs = [k for k in self.handlers if k.startswith('--')]
                raise UsageError(
                    f"unknown option {tok!r}{did_you_mean(tok, longs)}", None)
            if value is not None and _takes_many(binding):
                raise UsageError(
                    f"option {tok!r} takes several values; separate them with "
                    f"spaces, not '='", None)
            binding.invoke(self, value)
            return
        # short: -x, a flag bundle -vd, or an attached value -uF
        chars = tok[1:]
        i = 0
        while i < len(chars):
            opt = '-' + chars[i]
            binding = self.handlers.get(opt)
            if binding is None:
                raise UsageError(f"unknown option {opt!r}", None)
            if chars[i + 1:i + 2] == '=':               # -v=false / -n=5: explicit
                if _takes_many(binding):
                    raise UsageError(
                        f"option {opt!r} takes several values; separate them "
                        f"with spaces, not '='", None)
                binding.invoke(self, chars[i + 2:])      # value for THIS option
                return
            if self._nullary(binding):                  # no oparg: keep bundling
                binding.invoke(self)
                i += 1
            elif chars[i + 1:] and _takes_many(binding):  # -gp: an attached value,
                raise UsageError(                         # but this option needs
                    f"option {opt!r} takes several values; it must be last in "  # several -- it must
                    f"a bundle with its values as separate words", None)         # be last, words apart
            else:                                       # takes a value: rest is it
                binding.invoke(self, chars[i + 1:] or None)
                return

    @staticmethod
    def _nullary(binding):
        "Does this option take no oparg (so it can bundle: -vd)?"
        if isinstance(binding, (LiveBinding, ConjureBinding)):
            return True
        if isinstance(binding, MultiBinding):
            return not binding.converters
        return False

    def _fill_argument(self, arg):
        if arg.trailing:                            # a required trailing operand
            self.queue.popleft()                    # reserved off the end, keyword
            if not arg.owner.reserve:               # too few operands
                if arg.required:
                    raise UsageError(f"missing argument {arg.name!r}", None)
                return
            raw = arg.owner.reserve.pop(0)
            arg.owner.kwargs[arg.name] = self._cv(arg.converter, raw, arg.name)
            return
        tok = self.peek()
        # a leaf converter is any one-string-in callable (str, int, split(':'),
        # ...); a group is a Converter subclass (a nested command tree)
        if not (isinstance(arg.converter, type)
                and issubclass(arg.converter, Converter)):
            if arg.owner._optarg:
                # an option's operand (make -j): an attached value feeds it; else
                # grab the next token unconditionally -- only end-of-line or `--`
                # declines it ("optional" just means running out is legal).
                if arg.owner._attached is not None:
                    raw = arg.owner._attached
                    arg.owner._attached = None
                    self.queue.popleft()
                    arg.owner.args.append(self._cv(arg.converter, raw, arg.name))
                    return
                if tok is None or tok == '--':
                    if arg.required:
                        # a required oparg starved mid-group: the grabbed count
                        # isn't a valid one -- name the option and its counts
                        raise UsageError(
                            f"option {arg.owner._opt_display} takes "
                            f"{_count_list(_valid_counts(arg.owner._optarg_root))}",
                            None)
                    self.queue.popleft()
                    arg.owner.args.append(
                        _positional_default(arg.owner, arg.name))
                    return
                self.advance()
                self.queue.popleft()
                arg.owner.args.append(self._cv(arg.converter, tok, arg.name))
                return
            if tok is None or (not self.force_positional and self._is_option(tok)):
                if arg.required:
                    if arg.owner._summoned and not arg.owner.args:
                        # an option summoned this converter but no operand ever
                        # arrived: the option wasn't available yet (Case A/B)
                        raise UsageError(_availability_message(arg.owner), None)
                    if arg.owner._window and arg.owner.args:
                        raise UsageError(
                            f"wrong number of arguments: "
                            f"{len(arg.owner.args)} left over", None)
                    raise UsageError(f"missing argument {arg.name!r}", None)
                self.queue.popleft()
                if self.queue and isinstance(self.queue[0], RepeatInstruction):
                    self.queue.popleft()             # end a *args of leaves -- no
                else:                                # phantom element; a plain
                    arg.owner.args.append(           # optional keeps its position
                        _positional_default(arg.owner, arg.name))
                return
            self.advance()
            arg.owner.args.append(self._cv(arg.converter, tok, arg.name))
            self.queue.popleft()
            return
        # a converter slot: a conjured instance, or a fresh one from an operand
        obj = self.conjured.pop(arg.slot, None)
        if obj is not None and (tok is None or self._is_option(tok)):
            # an option summoned a group but no operand arrived to start a fresh
            # element.  queue[0] is THIS Argument; a *args window has the Repeat
            # behind it.
            if len(self.queue) > 1 and isinstance(self.queue[1], RepeatInstruction):
                # a *args window: an option past the last operand binds to the
                # NEAREST built instance, not a new window (never-rejects rule).
                for built in reversed(arg.owner.args):
                    if isinstance(built, arg.converter):
                        built.kwargs.update(obj.kwargs)
                        self.queue.popleft()        # the Argument
                        self.queue.popleft()        # the Repeat -- end the *args
                        return
            # no instance to bind to: the summoned shell falls through and becomes
            # the element.  If it needs operands and none arrive, its own fill
            # reports it (invoke-always; no conjurable flag).
        if obj is None and (tok is None or self._is_option(tok)):
            if tok is None and arg.required:
                obj = arg.converter()                # invoke-always (ruled): a
                                                     # required slot builds its
                                                     # shell; a starved required
                                                     # operand surfaces in its fill
            else:
                self.queue.popleft()                 # nothing to build here
                if arg.required:
                    raise UsageError(f"missing argument {arg.name!r}", None)
                if self.queue and isinstance(self.queue[0], RepeatInstruction):
                    self.queue.popleft()             # end the *args -- no phantom
                else:                                # element; a plain optional
                    arg.owner.args.append(           # group keeps its position
                        _positional_default(arg.owner, arg.name))
                return
        if obj is None:
            obj = arg.converter()
        if len(self.queue) > 1 and isinstance(self.queue[1], RepeatInstruction):
            obj._window = True                      # a *args window element: a
                                                    # starved required operand of
                                                    # it is a leftover shortfall
        if arg.owner._optarg:                       # a nested group under an
            obj._optarg = True                      # option's oparg is part of the
            obj._optarg_root = arg.owner._optarg_root   # same greedy grab; a starve
            obj._opt_display = arg.owner._opt_display   # names the top option/counts
        arg.owner.args.append(obj)
        self.queue.popleft()
        self.enter(obj)                             # pocket + front-splice


def _halts(result):
    "The early-exit contract: a nonzero non-bool int result halts dispatch."
    return isinstance(result, int) and not isinstance(result, bool) and result


def _unexpected(token, candidates=()):
    """
    Diagnose a token nobody claimed.  Commands never start with a dash, so a
    leading-dash leftover is a mistyped option (--verison), not a mystery
    command -- a friendlier, truthful error than "unknown command".
    `candidates` is the pool to suggest from: option strings for a dash token
    (long ones only), command words otherwise.
    """
    if token.startswith('-') and token not in ('-', '--'):
        longs = [c for c in candidates if c.startswith('--')]
        return UsageError(
            f"unknown option {token!r}{did_you_mean(token, longs)}", None)
    return UsageError(
        f"unknown command {token!r}{did_you_mean(token, candidates)}", None)


def execute(commands, argv, *, precommands=(), repeat=False):
    """
    Run a program left to right.  Any precommand ERAS run first, in order --
    each saturates its own arguments and binds its own options from the head of
    the line, then yields at the first token it doesn't own (a truthy int
    return from any era halts).  Then each command word names a command whose
    arguments follow; with `repeat`, that cycles until the line runs out.  A
    converter runs greedily to saturation, keeps binding its options (the
    window), and yields at the next command word or an option it doesn't own.
    commands maps command-word -> Converter class.
    """
    result = None
    pos = 0
    for era_cls in precommands:                 # the head eras, in order
        processor = Processor(argv[pos:], era_cls(), commands)
        result = processor.run()
        if _halts(result):
            return result
        pos += processor.consumed
    if not precommands and not argv:
        raise UsageError("no command given", None)
    while pos < len(argv):
        word = argv[pos]
        converter_cls = commands.get(word)
        if converter_cls is None:
            raise _unexpected(word, commands)
        pos += 1
        processor = Processor(argv[pos:], converter_cls(), commands)
        result = processor.run()
        pos += processor.consumed
        # validate leftover BEFORE the early-exit contract: a command that
        # returns a truthy int (an exit code -- or just an int result, like a
        # verbosity level) must not mask an unclaimed trailing token.
        if not repeat and pos < len(argv):
            # a leftover option suggests from this command's options; a leftover
            # word from the command table
            tok = argv[pos]
            pool = processor.handlers if tok.startswith('-') else commands
            raise _unexpected(tok, pool)
        if _halts(result):
            return result
    return result


# theming (themes are DATA--resolve_stylesheet composes them) and
# the build surface is re-exported LAZILY via __getattr__
# below, so accessing appeal.appeal_theme / appeal.build / etc. still
# works but doesn't cost anything until you touch it.

# every exception, both spellings (the prefixed forms are the
# real names--they're what tracebacks show, v1's rendering kept)


import os as _os
import sys as _sys


# LAZY RE-EXPORTS: appeal.build / appeal.compile_plan / appeal.appeal_theme
# / ... still work, but import their (heavy) home module only on first
# access, so plain `import appeal` stays stdlib-only.  (Internal uses
# take a local import at the call site.)
_LAZY_REEXPORTS = {
    'Decorations': 'build', 'build_plan': 'build',
    'default_options': 'build', 'default_long_option': 'build',
    'default_short_option': 'build',
    'strip_first_argument_from_signature': 'build',
    'strip_self_from_signature': 'build',
    'appeal_markdown_defaults': 'render', 'appeal_theme': 'render',
    'uncolored_theme': 'render', 'plain_theme': 'render',
    'dark_cool_theme': 'render', 'dark_warm_theme': 'render',
    'light_cool_theme': 'render', 'light_warm_theme': 'render',
    'resolve_stylesheet': 'render', 'help_stylesheet': 'render',
    'completions': 'complete', 'completions_set': 'complete',
    'read_csv': 'read', 'read_iterable': 'read', 'read_mapping': 'read',
    'describe': 'schema', 'describe_set': 'schema',
}


def __getattr__(name):
    spec = _LAZY_REEXPORTS.get(name)
    if spec is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}")
    import importlib
    modname, attr = spec if isinstance(spec, tuple) else (spec, name)
    value = getattr(importlib.import_module('.' + modname, __name__), attr)
    globals()[name] = value    # cache: future access is a plain global
    return value


# "not supplied" sentinel for Appeal(default_options=...)--distinct
# from an explicit None (which means "no default options at all"),
# and lets the signature default stay lazy (no build import at load)
_DEFAULT_OPTIONS = object()

# @app.option(default=...) "not supplied" sentinel--stands in for
# inspect.Parameter.empty in the SIGNATURE default, so `import appeal`
# needn't import inspect (~7ms); option() converts it back at call
# time (build's convention is inspect.Parameter.empty).
_UNSET = object()


class _LazyInspect:
    "inspect, imported on first attribute access--keeps it off `import appeal`."
    def __getattr__(self, name):
        import inspect
        globals()['_inspect'] = inspect     # replace the proxy: real from now on
        return getattr(inspect, name)


_inspect = _LazyInspect()

# featherweight stand-ins for the fast path: cheapsig (microsecond signature)
# and MethodType (types is always already loaded), so registration + dispatch
# never trip the lazy real-inspect proxy.  getdoc etc. stay on _inspect (help).
from . import cheapsig as _cheapsig
from types import MethodType as _MethodType


def _config_vet(plan, table_words, config, command_plan_for=None):
    """
    Config layering's stage 1 (strict keys--"either this is ours,
    or it isn't"): every key must name a global-command option.
    Returns {key: the OptionRule}, or raises naming the offender--
    a config file is end-user input, so loudness is UsageError.
    """
    from .build import all_options
    from .plan import Terminal
    options = {}
    scoped = set()
    for owner, o in all_options(plan):
        if o.name in options and options[o.name] is not o:
            scoped.add(o.name)
        options.setdefault(o.name, o)
    positionals = set()
    def gather(p):
        for s in p.slots:
            positionals.add(s.name)
            if not isinstance(s.child, Terminal):
                gather(s.child)
    gather(plan)
    vetted = {}
    for key, value in config.items():
        if key in scoped or (key in options
                             and options[key].key in plan.scoped_keys):
            # refused BY DESIGN (ruled 2026-07-09): position is
            # the essence of a scoped option, and a mapping has no
            # position--the two transports don't compose.  (The
            # relax-later shape, should a real need appear, is
            # nested addressing through the window's parameter:
            # {'b': {'flavor': ...}}.)
            raise AppealConfigurationError(
                f"config: {key!r} names a scoped option (several "
                f"windows declare it, and position decides which--"
                f"a mapping has no position).  Set it on the "
                f"command line, or give the uses distinct "
                f"parameter names (@app.option)")
        rule = options.get(key)
        if rule is not None:
            vetted[key] = rule
            continue
        if key in table_words:
            raise AppealDataError(
                f"config: {key!r} is a command; config supplies "
                f"only global-command options")
        if key in positionals:
            raise AppealDataError(
                f"config: {key!r} is a positional argument; config "
                f"supplies only options")
        # say where the key actually lives, if anywhere
        if command_plan_for is not None:
            for word in table_words:
                try:
                    p = command_plan_for(word)
                except Exception:
                    continue
                if any(s.name == key for s in p.slots):
                    raise AppealDataError(
                        f"config: {key!r} is a positional argument "
                        f"of {word!r}; config supplies only "
                        f"global-command options")
                if any(o.name == key
                       for owner, o in all_options(p)):
                    raise AppealDataError(
                        f"config: {key!r} is an option of {word!r}; "
                        f"config supplies only global-command "
                        f"options (no per-command sections)")
        raise AppealDataError(
            f"config: {key!r} isn't an option of this program")
    return vetted


def _config_inject(vetted, config, given, usage, scoped_keys=frozenset()):
    """
    The merge, atomic per option: an option argv mentioned wins
    whole; otherwise the config value enters `given` shaped like
    the command line would have shaped it, and stage 2 converts it
    through the ordinary pipeline.  A group's mapping value reads
    read_mapping style--its parameters by name AND its own options,
    recursively.  Returns the injected keys.
    """
    from .read import _read_bool
    injected = {}

    def shape(name, rule, value):
        key = rule.key
        if key in given:
            return          # argv wins, whole
        kind = rule.kind
        if kind in ('flag', 'nullary'):
            try:
                wanted = _read_bool(value, f'config: {name}')
            except AppealError as e:
                # a config file is end-user input
                raise AppealDataError(str(e), usage) from None
            if wanted:
                given[key] = True if kind == 'flag' else ()
                injected[name] = key
            return
        if kind == 'fold' and getattr(rule.converters[0],
                                      'mapping', False):
            # the mapping MultiOption (dict[K, V]'s mechanism): config
            # gives a whole dict; each pair becomes one KEY=VALUE
            # occurrence, exactly as the command line spells it
            if not isinstance(value, dict):
                raise AppealDataError(
                    f"config: {name!r} collects KEY=VALUE pairs; "
                    f"give it a mapping", usage)
            given[key] = [(f'{k}={v}',) for k, v in value.items()]
            injected[name] = key
            return
        if kind == 'fold':
            if not isinstance(value, (list, tuple)):
                raise AppealDataError(
                    f"config: {name!r} repeats; give it a sequence "
                    f"(one entry per occurrence)", usage)
            given[key] = ([tuple(v) if isinstance(v, (list, tuple))
                           else (v,) for v in value]
                          if kind == 'fold' else list(value))
            injected[name] = key
            return
        if kind == 'group':
            if isinstance(value, dict):
                # by name, read_mapping style: the child's
                # parameters in order, and its own options
                # recursively (each still atomic vs argv)
                option_rules = {o.name: o for o in rule.child.options}
                ordered = []
                for s in rule.child.slots:
                    if s.name in value:
                        ordered.append(value[s.name])
                        injected[f'{name}.{s.name}'] = key
                    else:
                        break
                extra = (set(value)
                         - {s.name for s in rule.child.slots}
                         - set(option_rules))
                if extra:
                    raise AppealDataError(
                        f"config: {name!r}: unknown group "
                        f"argument(s) {sorted(extra)}", usage)
                given[key] = tuple(ordered)
                injected[name] = key
                for inner_name, inner_rule in option_rules.items():
                    if inner_name not in value:
                        continue
                    if inner_rule.key in scoped_keys:
                        # same ruling as the top level: a scoped
                        # option has no position in a mapping
                        raise AppealConfigurationError(
                            f"config: {name!r}.{inner_name!r} names "
                            f"a scoped option; set it on the "
                            f"command line")
                    shape(f'{name}.{inner_name}', inner_rule,
                          value[inner_name])
            elif isinstance(value, (list, tuple)):
                given[key] = tuple(value)
                injected[name] = key
            else:
                given[key] = (value,)
                injected[name] = key
            return
        # a value option's `given` entry is the list of occurrences
        # (last wins, all validated); config supplies one occurrence
        if kind == 'value' and len(rule.converters) > 1:
            if not isinstance(value, (list, tuple)):
                raise AppealDataError(
                    f"config: {name!r} takes "
                    f"{len(rule.converters) - 1} values; give it a "
                    f"sequence", usage)
            given[key] = [tuple(value)]
            injected[name] = key
            return
        given[key] = [value]
        injected[name] = key

    for name, rule in vetted.items():
        shape(name, rule, config[name])
    return injected


def _config_apply(conv, table, global_plan, config, plan_for):
    """
    Layer a config mapping onto the global command's already-parsed converter
    (the one engine): defaults < config < argv, atomic per option.  Keys are
    vetted strictly (reuse _config_vet).  Each vetted option argv did NOT set
    is replayed as the synthetic command-line tokens it would have produced,
    run through a fresh converter of the same class and merged in -- so config
    rides the ordinary conversion pipeline.  Conversion failures carry
    'config:' provenance (an option argv already gave wins whole).
    """
    from .read import _read_bool
    vetted = _config_vet(global_plan, frozenset(table), config, plan_for)
    given = set(conv.kwargs)            # options (folds included) all live here now
    usage = global_plan.usage()

    def tokens_for(rule, value, provenance):
        kind = rule.kind
        spelling = rule.key                     # the long option string
        if kind in ('flag', 'nullary'):
            try:
                wanted = _read_bool(value, f'config: {provenance}')
            except AppealError as e:            # config is end-user input
                raise AppealDataError(str(e), usage) from None
            return [spelling] if wanted else []
        if kind == 'group':
            if not isinstance(value, dict):
                seq = value if isinstance(value, (list, tuple)) else (value,)
                return [spelling] + [str(v) for v in seq]
            slot_names = {s.name for s in rule.child.slots}
            option_rules = {o.name: o for o in rule.child.options}
            extra = set(value) - slot_names - set(option_rules)
            if extra:
                raise AppealDataError(
                    f"config: {provenance!r}: unknown group argument(s) "
                    f"{sorted(extra)}", usage)
            toks = [spelling]
            for s in rule.child.slots:          # child operands, by name, in order
                if s.name not in value:
                    break
                toks.append(str(value[s.name]))
            for oname, orule in option_rules.items():   # child options, recursively
                if oname not in value:
                    continue
                if orule.key in global_plan.scoped_keys:
                    # a scoped option's essence is position; a mapping has none
                    # (same ruling as a scoped top-level key, _config_vet)
                    raise AppealConfigurationError(
                        f"config: {provenance!r}.{oname!r} names a scoped "
                        f"option (several windows declare it, and position "
                        f"decides which); set it on the command line")
                toks += tokens_for(orule, value[oname], f'{provenance}.{oname}')
            return toks
        if kind == 'fold':
            if getattr(rule.converters[0], 'mapping', False):
                if not isinstance(value, dict):
                    raise AppealDataError(
                        f"config: {provenance!r} collects KEY=VALUE pairs; "
                        f"give it a mapping", usage)
                toks = []
                for k, v in value.items():
                    toks += [spelling, f'{k}={v}']
                return toks
            if not isinstance(value, (list, tuple)):
                raise AppealDataError(
                    f"config: {provenance!r} repeats; give it a sequence", usage)
            toks = []
            for v in value:                     # one occurrence per element; a
                if isinstance(v, (list, tuple)): # sequence element is a multi-arg
                    toks.append(spelling)        # occurrence (--adds 2 3)
                    toks += [str(x) for x in v]
                else:
                    toks += [spelling, str(v)]
            return toks
        # value: one occurrence, single- or multi-oparg
        if len(rule.converters) > 1:
            if not isinstance(value, (list, tuple)):
                raise AppealDataError(
                    f"config: {provenance!r} takes {len(rule.converters) - 1} "
                    f"values; give it a sequence", usage)
            return [spelling] + [str(v) for v in value]
        return [spelling, str(value)]

    synth = []
    for name, rule in vetted.items():
        if name in given:                       # argv wins, whole
            continue
        synth += tokens_for(rule, config[name], name)
    if not synth:
        return
    _Conv = Converter; _Opt = Option
    cfg_conv = type(conv)()
    proc = Processor(synth, cfg_conv, table)
    proc.enter(cfg_conv)
    try:
        proc._loop()
    except UsageError as e:
        # config supplies only options (vetted); its synth carries no operands,
        # so once the option tokens are consumed cfg_conv's required POSITIONALS
        # report "missing argument" -- expected and irrelevant.  Any OTHER error
        # is about a config value (options validate eagerly) -> config: provenance.
        if not str(e).startswith('missing argument'):
            raise AppealDataError(f"config: {e}", getattr(e, 'usage', None) or usage,
                                  param=getattr(e, 'param', None)) from None
    try:
        # render the config VALUES here (not at conv()) so a conversion failure
        # carries 'config:' provenance; then merge the finished values in
        for k in list(cfg_conv.kwargs):
            v = cfg_conv.kwargs[k]
            if isinstance(v, (_Conv, _Opt)):
                cfg_conv.kwargs[k] = v()
    except UsageError as e:                     # provenance: it came from config
        raise AppealDataError(f"config: {e}", getattr(e, 'usage', None) or usage,
                              param=getattr(e, 'param', None)) from None
    for k, v in cfg_conv.kwargs.items():
        conv.kwargs.setdefault(k, v)


def _is_class_command(obj):
    """
    A class, or a wrapped class (e.g. big's BoundInnerClass): the
    __wrapped__ convention is functools' and wrapt's, not any one
    library's.
    """
    return isinstance(obj, type) or isinstance(
        getattr(obj, '__wrapped__', None), type)


def _refuse_orphan_method(callable):
    """
    A registered command that looks like an undecorated class's
    method: first parameter self, dotted qualname, and no class
    claimed it.  Refuse by name--parsing a string into self helps
    nobody.
    """
    if _is_class_command(callable):
        return
    try:
        parameters = list(_cheapsig.signature(callable).parameters)
    except (ValueError, TypeError):
        return
    qualname = getattr(callable, '__qualname__', '')
    if parameters and parameters[0] == 'self' and '.' in qualname:
        raise AppealConfigurationError(
            f"{qualname}: first parameter is 'self' but no class "
            f"claims this command--did you forget to decorate the "
            f"class?")


class _RunLog:
    """
    One trip through one command line--v1's Processor returns,
    leaner.  app.parse(argv) builds one having run stage 1 only
    (the structural scan: zero user code, a malformed line dies
    there); execute() runs stage 2, the conversions and the
    commands themselves, left to right.  app.process() is both
    stages, fused.

    instances is the run's execution log, appended mechanically in
    execution order: one (command, instance) pair per command run.
    command is the registered callable--None for the global
    command--and instance is the object it constructed (None until
    class-based commands land).

    v1 compat: app.processor() returns an unparsed Processor;
    calling it with an argv runs both stages and returns the
    result (v1's callable execution object).
    """
    def __init__(self, app):
        self.app = app
        self.invocations = None    # stage 1's artifact: what would run
        self._tail = None
        self.instances = []
        self.result = None
        self._config = None        # vetted config layer, if any

    def __repr__(self):
        if self.invocations is None:
            return '<Processor (unparsed)>'
        parts = []
        for word, run, operands, handoff, positions in self.invocations:
            name = '(global)' if word is None else word
            text = f'{name} {" ".join(operands)}'.rstrip()
            # the handoff is either the mature `given` dict or the
            # eager IR (a token list); show the option keys either way
            if isinstance(handoff, dict):
                keys = sorted(handoff)
            else:
                keys = sorted({t[0] for t in handoff if t and t[0]})
            if keys:
                text += ' [' + ' '.join(keys) + ']'
            parts.append(text)
        if self._tail is not None:
            parts.append(f'({self._tail[0]})')
        return '<Processor: ' + '; '.join(parts) + '>'

    def _command_for(self, word):
        """
        The registered callable behind a word, for the instances
        log (None: the global).  A bare word naming DIFFERENT
        callables under different parents is ambiguous from here
        (invocations don't carry their parent), so the log
        answers None rather than guess wrong.
        """
        if word is None:
            return None
        command = self.app._table().get(word)
        if command is not None:
            return command
        matches = {id(fn): fn for subs in self.app._subs.values()
                   for name, fn in subs if name == word}
        if len(matches) == 1:
            (fn,) = matches.values()
            return fn
        return None

# the default_mappings menu, importable (spell your subset with
# these: default_mappings(*default_mappings_help))
default_mappings_help = ('-h', '--help', 'help')
default_mappings_version = ('-V', '--version', 'version')


def default_mappings(*options):
    """
    The FACTORY for the stock program-level defaults policy
    (Larry's design, 2026-07-25).  List the mappings you want:
    '-h', '--help', '-V', '--version' (precommand options),
    'help', 'version' (commands); empty means all of them.
    Returns the policy callable--the constructor default is
    default_mappings=default_mappings().  Pass
    default_mappings=None for no default mappings at all.

    Order is insignificant (the listing keeps v1's order, version
    before help).  Version mappings apply only when the app has a
    version string.  Each mapping lands only if not already
    mapped--user declarations always win.
    """
    if not options:
        options = default_mappings_help + default_mappings_version
    valid = set(default_mappings_help + default_mappings_version)
    for o in options:
        if o in valid:
            continue
        if isinstance(o, str):
            near = {'-v': '-V', '--h': '--help', '-help': '--help',
                    '-version': '--version'}.get(o)
            hint = f" (did you mean {near!r}?)" if near else ''
            raise AppealConfigurationError(
                f"default_mappings: unknown mapping {o!r}{hint}; "
                f"the menu is {sorted(valid)}")
        raise AppealConfigurationError(
            f"default_mappings: {o!r} isn't a mapping name.  "
            f"default_mappings is a factory--pass the constructor "
            f"default_mappings=default_mappings(), not the "
            f"factory itself")
    requested = frozenset(options)

    def default_mappings_policy(app):
        # snapshot FIRST: the version/help COMMANDS only make
        # sense for a program that has commands (v1's rule; an
        # ls-style global-only program gets the options, never
        # command words)
        has_commands = bool(app.commands)
        if app.version is not None:
            if ('version' in requested and has_commands
                    and 'version' not in app.commands):
                app.command('version')(app.print_version)
            free = [s for s in ('-V', '--version')
                    if s in requested and s not in app.options]
            if free:
                app.option('version', *free)(app.help_and_version_precommand)
        if has_commands:
            if 'help' in requested and 'help' not in app.commands:
                app.command('help')(app.help)
                # help()'s usage=/summary=/doc= knobs are API,
                # not command-line surface: the zero-string
                # option() is the explicit unmap (ruled
                # 2026-08-05)
                app.option('usage')(app.help)
                app.option('summary')(app.help)
                app.option('doc')(app.help)
        # the -h/--help OPTION rides for EVERY app, global-only included
        # (like --version above): a program with no commands still answers
        # -h/--help through its precommand.
        free = [s for s in ('-h', '--help')
                if s in requested and s not in app.options]
        if free:
            app.option('help', *free)(app.help_and_version_precommand)

    # _finalize reads this to drive the legacy help machinery
    # (per-command --help, bare-app -h) until the era unification
    # retires it: the requested tokens are the truth
    default_mappings_policy.requested = requested
    return default_mappings_policy


class Appeal:
    """
    The v2 API surface, matching v1's shape:

      * @app.command() functions are *subcommands*: the first
        operand on the line names one (literally--the function's
        name, no mangling), even when only one is registered.
      * @app.global_command() is the command with no name: its
        options and operands come before the command word.  With
        no @app.command()s at all, it owns the whole line--that's
        how you spell a program without subcommands.

    Decoration only records; the plans are built and compiled at
    first use (see "Laziness and late binding" in the grammar doc).
    """
    def __init__(self, name=None, *, parent=None,
                 stylesheet=None, version=None, repeat=False,
                 errors=None, script=_sys.argv[0],
                 margin=79,
                 positional_argument_usage_format='<{name.upper()}>',
                 default_options=_DEFAULT_OPTIONS,
                 default_mappings=default_mappings(), doc=None):
        from .build import Decorations
        self.name = name
        # the command tree (v1's model, restored 2026-07-18 by
        # Larry's ruling): a tree of Appeal instances, one per
        # command word, linked by .parent.  A node's _impl is its
        # command function--for a node with children, that
        # function is the global command of its own little set.
        # The flat structures the compiler consumes (_commands,
        # _subs, ...) are read-only views derived from this tree.
        self.parent = parent
        self._children = {}       # command word -> child Appeal
        self._impl = None         # this node's command function
        self._precommands = []    # ordered precommand eras (the head; _impl
                                  # tracks the primary until dispatch runs them all)
        self._auto_impl = None    # synthesized fn for a pure dispatcher
        self._node_default = None # this node's default command
        self._node_repeat = False # this node's set cycles
        if parent is not None:
            # a subcommand node is a full Appeal; the program
            # knobs are the root's, copied as processed values
            for attr in ('_help_enabled', 'default_options',
                         'default_mappings',
                         'positional_argument_usage_format',
                         'script', 'errors', 'repeat', 'stylesheet',
                         'margin', '_templates'):    # the BACKING field, not the
                setattr(self, attr, getattr(parent, attr))  # `templates` property
                                                    # -- copying the property would
                                                    # force render's lazy import
            self.version = None
            self._finalized = True      # the ROOT runs the pass
            self._precommand_options = {}
            self._decorations = parent.root._decorations
            self._method_owner = parent._method_owner
            self._init_caches()
            if name is not None:
                parent._children[name] = self
                parent._invalidate()
            return
        # whether Appeal supplies automatic help (v1's knob): the
        # per-command -h/--help option AND, for a program with
        # commands, the `help` command.  help=False suppresses all
        # of it--the program answers -h/--help only if it declares
        # them itself.  (A command that defines its own help still
        # wins even when help=True; this is the blanket off switch.)
        # the v1 help= knob is dead (ruled 2026-07-25):
        # default_mappings is the policy switch.  The legacy
        # bare-app help machinery still keys off this flag;
        # approximate it until the era unification lands
        self._help_enabled = default_mappings is not None
        # the option-string policy (v1's knob, restored): a callable
        # (name, annotation, default) -> list of option strings, run
        # at build time on every automatically-mapped keyword-only
        # parameter.  The stock policy adds a long and a short;
        # default_long_option drops the short, default_short_option
        # drops the long, or supply your own.  Its output--the
        # strings--are computed at build time.
        if default_options is _DEFAULT_OPTIONS:
            # not supplied -> the stock policy (lazy: importing build
            # is deferred until an Appeal is actually constructed)
            from .build import default_options as default_options
        if default_options is not None and not callable(default_options):
            raise AppealConfigurationError(
                f"default_options must be callable or None, "
                f"not {default_options!r}")
        self.default_options = default_options
        # the program-level defaults pass (Larry's design,
        # 2026-07-19): default_mappings(app) runs ONCE, at first
        # compile, after all registration--the stock policy maps
        # the `help` and `version` commands and the precommand's
        # -V/--version, each only if not already mapped.  None:
        # no default semantics at all.  Rhymes with
        # default_options: that one derives a parameter's strings,
        # this one decides the program's default mappings.
        if default_mappings is not None and not callable(default_mappings):
            raise AppealConfigurationError(
                f"default_mappings must be callable or None, "
                f"not {default_mappings!r}")
        self.default_mappings = default_mappings
        self._finalized = False
        self._precommand_options = {}   # param -> (strings...)
        # EVERYTHING @app.option/@app.parameter expressed, keyed
        # by the decorated callable (ruled 2026-08-09: Appeal
        # never modifies objects the user owns--decoration writes
        # it down HERE and moves on).  One registry per tree.
        self._decorations = Decorations()
        # @app.subcommand('path') declarations, written down at
        # decoration and resolved lazily (ruled 2026-08-10:
        # explicit parentage, registration order free)
        self._pending_subcommands = []
        # how an operand renders in usage lines and help tables:
        # a format string over the parameter NAME (v1's knob,
        # restored).  '{name}' (default) shows the bare name; the
        # only interpolations are {name} and {name.upper()}, so
        # '<{name}>' gives <name> and '{name.upper()}' gives NAME.
        # Applies to positional operands AND option operands
        # (opargs) alike; an explicit @app.parameter usage= wins
        # outright over the format.
        _validate_arg_format(positional_argument_usage_format)
        self.positional_argument_usage_format = \
            positional_argument_usage_format
        # argv[0], captured HERE at the outer edge (its default is
        # read once, when this module is imported) rather than
        # sniffed from sys.argv deep in the machinery--so the
        # program name is a controllable input, not ambient state.
        # _prog() derives the displayed name from its basename;
        # name=, if given, overrides it outright.
        self.script = script
        # the file object main() prints error messages to,
        # default sys.stderr (the POSIX diagnostic convention, so
        # pipelines reading this program's stdout stay clean;
        # sys.stdout is v1's behavior).  Like print(file=None),
        # None resolves at error time.  Requested help always
        # prints to stdout.
        if errors is not None and not hasattr(errors, 'write'):
            raise AppealConfigurationError(
                f"errors= must be a writable file object "
                f"(sys.stderr, sys.stdout, ...), not {errors!r}")
        self.errors = errors
        # cycling is PER NODE (ruled 2026-08-22): `repeat` on a node means its
        # own set may cycle -- run more than one command from it.  The root's
        # set is the top-level commands; a command's set is its subcommands.
        # Not inherited: each node's repeat governs only its own set.  The root
        # seeds its _node_repeat from the program-level repeat= here.
        self.repeat = repeat
        self._node_repeat = repeat
        # None = auto (appeal_theme when the stream wants color),
        # False = never any color, or a complete composed
        # StyleSheet, used VERBATIM (ruled 2026-08-06); the
        # environment always wins (resolve_stylesheet's palette).
        self.stylesheet = stylesheet
        self.version = version
        # the program's documentation, tier 1 of the doc chain
        # (ruled 2026-08-01): doc= beats the global command's
        # docstring beats the shared module's docstring
        self.doc = doc
        # the help formatter's knob (v1's, wired 2026-07-09):
        # margin caps the wrap width (narrow terminals re-wrap
        # below it; pipes get the cap itself).  indent= died
        # unshipped with the Markdown pivot (ruled 2026-08-06):
        # big's renderer owns the definition-list layout
        if not isinstance(margin, int) or margin <= 0:
            raise AppealConfigurationError(
                f"margin must be a positive int, not {margin!r}")
        self.margin = margin
        # the help template: ONE string, six {sections}, its headings
        # Markdown, yours to replace.  Loaded lazily (it lives in render, which
        # pulls big/markdown) so a successful dispatch never imports render.
        self._templates = None
        # NO lock (ruled 2026-08-22): builds are idempotent and cache installs
        # are atomic (setdefault / attribute assignment), so racing first-parses
        # each build and one install wins -- see _init_caches.
        self._method_owner = {}   # id(callable) -> owning class's env key
        self._init_caches()

    @property
    def templates(self):
        "The help template; loaded from render lazily (off the fast path)."
        if self._templates is None:
            from .render import default_template
            self._templates = default_template
        return self._templates

    @templates.setter
    def templates(self, value):
        self._templates = value

    def _init_caches(self):
        # lock-free lazy caches (ruled 2026-08-22: no Lock).  Builds are
        # idempotent (same callable -> equivalent artifact), and dict.setdefault
        # / attribute assignment are atomic (GIL, and PEP 703 free-threaded), so
        # racing first-parses each build and one install wins -- the rest
        # harmlessly discard.  The dict caches are eager-{} so there's no
        # None-then-{} check-and-set to race.
        self._parse = None        # scalar: the single-command parse fn
        self._set_entries = {}    # nested set dicts, built per parent node
        self._last_processor = None   # app.instances reads this
        self._plans = {}          # {id(node): Plan}, filled per word
        self._parses = {}         # {id(node): parse fn}, ditto
        self._global_plan = None  # scalar

    def _invalidate(self):
        # registration under a node changes every ancestor's
        # compiled artifacts (they embed the descendants); a
        # node's own descendants embed nothing of it, so down
        # the tree nothing staling
        node = self
        while node is not None:
            node._parse = None
            node._set_entries = {}
            node._plans = {}
            node._parses = {}
            node._global_plan = None
            node = node.parent

    # ------------------------------------------------------------
    # the command tree: registration
    # ------------------------------------------------------------

    def _child(self, word):
        "Fetch-or-create the child Appeal for a command word."
        node = self._children.get(word)
        if node is None:
            node = Appeal(word, parent=self)
        return node

    def __call__(self, callable):
        """
        Calling an Appeal node with a callable sets the node's
        command function (v1): `@app.command('sync-all')`
        decorates through here, so the command word is the node's
        name and the function's own name is ignored.  On the root
        it sets the global command.  Decorating again replaces.
        """
        self._impl = callable
        self._invalidate()
        return callable

    @property
    def root(self):
        "The tree's root Appeal--the program."
        node = self
        while node.parent is not None:
            node = node.parent
        return node

    # ------------------------------------------------------------
    # the public introspection API (Larry's design, 2026-07-19):
    # queryable by default_mappings callbacks and anyone else
    # ------------------------------------------------------------

    @property
    def commands(self):
        """
        Read-only mapping: command word -> the child Appeal node,
        in definition order.  The node IS the configuration
        object: .callable is its function, .commands its
        subcommands, .options its option table, .default_callable
        its default command.
        """
        import types as _types
        return _types.MappingProxyType(self._children)

    @property
    def callable(self):
        """
        This node's command function (spelled like plan.callable
        one layer down; ruled 2026-07-25).  On the root, the
        global command; on a child, the function bound to its
        word.  None if never bound.
        """
        return self._impl

    @property
    def default_callable(self):
        "The default command's function (None if unset; ruled 2026-08-04)."
        return self._node_default

    @property
    def options(self):
        """
        Read-only mapping: option string -> the OptionRule that
        owns it, declaration order, converters' nested options
        included.  On the root: every string mapped in the
        precommand+global era.  Compiles what it needs, lazily,
        like .plan and .plans.
        """
        import types as _types
        from .build import all_options
        table = {}
        plan = None
        if self._impl is not None:
            plan = (self.global_plan if self.parent is None
                    else self._plan())
        if plan is not None:
            for owner, o in all_options(plan):
                for s in o.strings:
                    table.setdefault(s, o)
        for param, strings in self.root._precommand_options.items():
            for s in strings:
                table.setdefault(s, None)
        return _types.MappingProxyType(table)

    def _plan(self):
        "This node's own Plan (the root: the global plan; ruled private 2026-08-04)."
        if self.parent is None:
            return self.global_plan
        return self.root.plan_for(self.name)

    # ------------------------------------------------------------
    # the default mappings pass and its default implementations
    # ------------------------------------------------------------

    def _finalize(self):
        """
        Run the root's default_mappings pass exactly once, at
        first compile--whatever triggered it.  It never re-runs;
        mappings changed afterward are the changer's business.
        """
        root = self.root
        if root._finalized:
            return
        root._finalized = True      # first: registrations inside
                                    # must not recurse
        if root.default_mappings is not None:
            root.default_mappings(root)
            requested = getattr(root.default_mappings,
                                'requested', None)
            if requested is not None:
                # the stock factory says what was asked for
                root._help_enabled = bool(
                    requested & {'-h', '--help', 'help'})
            else:
                # a custom policy: judge by what it actually mapped
                root._help_enabled = bool(
                    root._precommand_options.get('help')
                    or 'help' in root._children)
        root._resolve_subcommands()
        root._derive_method_owners()

    def print_version(self):
        "Print the program's version."
        print(self.root.version)

    def _help_topic_page(self, topic, suppress=frozenset()):
        "help(topic)'s command-page path, split for readability."
        root = self.root
        table = root._table()
        if topic == 'help':
            print('Print usage documentation on a specific command.')
            return
        fn = table.get(topic)
        if getattr(fn, '__func__', None) is Appeal.print_version:
            # a stock command describes itself with its summary
            print(_inspect.getdoc(fn))
            return
        if topic not in table:
            from .plan import command_set_usage
            raise UsageError(
                f"unknown command {topic!r}"
                f"{did_you_mean(topic, table)}",
                command_set_usage(root._prog(), root._display_global()))
        # render the topic's page directly from plans (the one engine has no
        # baked-help compile step).  A topic that is itself a command SET shows
        # its subcommand listing (like `prog topic --help`); a leaf shows its
        # command page.
        node = root._node_for(topic)
        from .render import help_margin, render_help_page
        if node is not None and node._table():
            from .plan import command_set_usage
            from .help import summary as _summary, command_set_corpus
            node_table = node._table()
            entries = [(w, _summary(c)) for w, c in node_table.items()]
            # add the auto `help` row unless the set already registers one
            # (the bare-root path gets it from the root's own table instead)
            auto_help = node._help_enabled and 'help' not in node_table
            corpus = command_set_corpus(
                node.global_plan, entries, auto_help, auto_version=False,
                doc=node._program_doc_override())
            text = render_help_page(
                command_set_usage(node._prog(), node._display_global()),
                corpus, node.templates, margin=help_margin(node.margin),
                file=_sys.stdout, stylesheet=node.stylesheet,
                suppress=suppress).rstrip('\n')
        else:
            from .help import merge_docs
            plan = root.plan_for(topic)
            text = render_help_page(
                plan.usage(), merge_docs(plan), root.templates,
                margin=help_margin(root.margin),
                file=_sys.stdout, stylesheet=root.stylesheet,
                suppress=suppress).rstrip('\n')
        print(text)

    def help_and_version_precommand(self, *, help: optional[str] = None,
                   version=False):
        """
        The stage ahead of the global command: program metadata.
        Its options live in the precommand+global era and unmap at
        the first command word.  Absent from the grammar entirely
        when default_mappings mapped nothing to it.  Map options
        onto it the ordinary way:
        app.option('help', '-h', '--help')(app.help_and_version_precommand).
        """
        if version:
            _sys.exit(self.print_version())
        if help is not None:
            _sys.exit(self.help(help))

    def _command_callable(self):
        """
        This node's command function--synthesized (a no-op taking
        nothing) for a pure dispatcher, a parent that was only
        ever chained through; None for a word that was named but
        never bound (not a command at all).
        """
        if self._impl is not None:
            return self._impl
        if not self._children:
            return None
        if self._auto_impl is None:
            def dispatcher():
                pass
            dispatcher.__name__ = self.name or 'command'
            dispatcher.__qualname__ = dispatcher.__name__
            dispatcher.__doc__ = None
            self._auto_impl = dispatcher
        return self._auto_impl

    def _iter_set_nodes(self):
        "Every descendant, any depth, that parents a nested set."
        for word, node in self._children.items():
            if node._children:
                yield word, node
                yield from node._iter_set_nodes()

    # -- the flat views the compiler consumes: read-only,
    # -- derived from the tree

    @property
    def _commands(self):
        "(word, callable) for this node's children, decl order."
        out = []
        for word, node in self._children.items():
            impl = node._command_callable()
            if impl is not None:
                out.append((word, impl))
        return out

    @property
    def _global(self):
        return self._impl

    @property
    def _default(self):
        return self._node_default

    @property
    def _subs(self):
        """
        Flat: parent word -> [(word, callable)] for every nested
        set at any depth.  Flatness means parent words must be
        unique tree-wide (restrictive; path-addressed sets can
        relax it later).
        """
        self.root._finalize()   # drain the subcommand ledger
        out = {}
        for word, node in self._iter_set_nodes():
            if word in out:
                raise AppealConfigurationError(
                    f"two nested command sets named {word!r}")
            out[word] = [(w, c._command_callable())
                         for w, c in node._children.items()
                         if c._command_callable() is not None]
        return out

    @property
    def _sub_repeat(self):
        return {word: True for word, node in self._iter_set_nodes()
                if node._node_repeat}

    @property
    def _sub_defaults(self):
        "Parent word -> its set's default command, where set."
        return {word: node._node_default
                for word, node in self._iter_set_nodes()
                if node._node_default is not None}

    @staticmethod
    def _command_word(name, callable=None):
        """
        The command word for a registration: an explicit name verbatim, else
        the function name with underscores turned to dashes (upload_database
        -> upload-database, like git's format-patch/range-diff).  A command
        word can never start with a dash -- that's an option's shape -- so a
        leading dash, whether from name='--foo' or a function named _command
        (-> -command), is a configuration error.
        """
        word = name if name is not None else callable.__name__.replace('_', '-')
        if word.startswith('-'):
            raise AppealConfigurationError(
                f"a command name can't start with a dash: {word!r} "
                f"(commands are words, not options)")
        return word

    def command(self, name=None, *, repeat=False, parent=None):
        """
        @app.command() registers a command under the callable's name with
        underscores turned to dashes (upload_database -> upload-database).
        app.command('db')
        returns the child Appeal for the word 'db', creating it
        if needed--the command tree is a tree of Appeal instances
        (v1).  Use the child as a decorator to set the command's
        function while saying the word out loud
        (`@app.command('sync-all')`: dashes welcome, the
        function's name is ignored), or keep going: `.command()`
        attaches subcommands (the parent runs first, like a
        global command of its own little set),
        `.default_command()` picks what runs when the line stops
        at the parent--and every other Appeal method is there,
        because the child IS an Appeal.  repeat=True makes the
        node's set cycle: after a subcommand's arguments, the
        next token may name another one.  parent= is the older
        v2 spelling of the same fetch: command(parent='db') ==
        command('db').
        """
        if parent is not None:
            if name is not None:
                raise AppealConfigurationError(
                    "command(): give a name or parent=, not both")
            name = parent
        return self.subcommand(None, name, repeat=repeat)

    def default(self):
        """
        v1's API: the command run when the line stops at this
        node--for the root, a line naming no command; for a
        subcommand node (`@app.command('db').default_command()`),
        a line ending at the parent.
        """
        def decorator(callable):
            self._node_default = callable
            self._invalidate()
            return callable
        return decorator
    default_command = default           # transitional alias for the old name

    def precommand(self, *, index=-1):
        def decorator(callable):
            # a class here is class-as-app (§8.6): its __init__
            # is the global command's grammar; its methods
            # register themselves explicitly and membership
            # derivation binds them (ruled 2026-08-10).  precommand is
            # REPEATABLE (Larry, 2026-08-21): each call inserts an era into
            # the ordered list (index -1 = append, 0 = head); they run
            # front-to-back before the commands, each its own era.
            if index == -1:
                self._precommands.append(callable)
            else:
                self._precommands.insert(index, callable)
            self._impl = self._precommands[-1]
            self._invalidate()
            return callable
        return decorator
    global_command = precommand         # transitional alias for the old name

    def subcommand(self, parent, name=None, *, repeat=False):
        """
        Register a command under `parent`--a command word PATH
        string, root-relative: subcommand('db') for a child of
        db, subcommand('db migrate') for depth.  EXPLICIT by
        ruling (2026-08-10): Appeal never infers subcommand-ness;
        you say what the thing is a subcommand of, or it's a
        top-level command.  parent=None IS the top level--
        command() is exactly subcommand(None).

        The decoration writes down what was said and moves on;
        the path resolves at first use, so registration order is
        free (declare the child before the parent, fine).  A path
        nothing ever registers is a loud error naming it.  The
        returned decorator is REUSABLE--a tear-off:

            dbcmd = app.subcommand('db')
            @dbcmd
            def add(...): ...
            @dbcmd
            def remove(...): ...

        A method command may mount only at its class's own mount
        or under another method of the same class (the same-world
        rule, ruled 2026-08-10: commands are sentences about the
        object; once a path leaves the object's world it doesn't
        come back).
        """
        if parent is None:
            # the top level: the tree registration, eager
            # (nothing to resolve).  With a name, the node comes
            # back--decorator AND chaining handle, v1's shape.
            if name is not None:
                if not isinstance(name, str):
                    raise AppealConfigurationError(
                        f"command(): the command word must be a "
                        f"string, not {name!r}")
                node = self._child(self._command_word(name))
                if repeat and not node._node_repeat:
                    node._node_repeat = True
                    self._invalidate()
                return node
            def decorator(callable):
                node = self._child(self._command_word(None, callable))
                node._node_repeat = node._node_repeat or repeat
                return node(callable)
            return decorator
        if not isinstance(parent, str):
            raise AppealConfigurationError(
                f"subcommand: the parent is a command word path "
                f"(a string) or None, not {parent!r}")
        root = self.root
        def decorator(callable):
            if root._finalized:
                # late registration: the tree exists, attach now
                root._attach_subcommand(parent, name, repeat,
                                        callable)
            else:
                root._pending_subcommands.append(
                    (parent, name, repeat, callable))
            root._invalidate()
            return callable
        return decorator

    def _node_at_path(self, path):
        "The COMMAND node at a word path, or None while unresolved."
        node = self.root
        for word in path.split():
            child = node._children.get(word)
            if child is None or child._command_callable() is None:
                return None
            node = child
        return node

    def _attach_subcommand(self, parent, name, repeat, callable):
        node = self._node_at_path(parent)
        if node is None:
            raise AppealConfigurationError(
                f"subcommand: no command at path {parent!r} (for "
                f"{getattr(callable, '__name__', callable)!r})")
        child = node._child(self._command_word(name, callable))
        child._node_repeat = child._node_repeat or repeat
        child(callable)

    def _resolve_subcommands(self):
        """
        Drain the subcommand ledger to a fixpoint--a parent may
        itself arrive by subcommand--and refuse, naming paths,
        anything left unresolvable.
        """
        pending = self._pending_subcommands
        while pending:
            remaining = []
            progressed = False
            for item in pending:
                if self._node_at_path(item[0]) is None:
                    remaining.append(item)
                    continue
                self._attach_subcommand(*item)
                progressed = True
            if not progressed:
                paths = sorted({item[0] for item in remaining})
                raise AppealConfigurationError(
                    f"subcommand: no command was ever registered "
                    f"at path{'s' if len(paths) > 1 else ''} "
                    f"{', '.join(map(repr, paths))}")
            pending[:] = remaining

    def _derive_method_owners(self):
        """
        Membership derivation (ruled 2026-08-10): a registered
        command function found--by IDENTITY--in the __dict__ of a
        mounted class is that class's method command; self binds
        to the instance constructed at the class's mount.  Not
        signature-sniffing: two explicit declarations (the class
        mounted, the function registered) plus membership,
        deterministically combined.  Enforces SAME-WORLD (Larry's
        formulation, 2026-08-10): a method is either (a) a
        top-level command, its class being the GLOBAL command, or
        (b) a direct subcommand of its class's own mount, the
        class being a command (or subcommand) itself.  Nowhere
        else--methods don't hang off each other.
        """
        owners = self._method_owner
        classes = []                    # (cls, mount node)
        if self._impl is not None and _is_class_command(self._impl):
            classes.append((self._impl, self))
        def find(node):
            for child in node._children.values():
                impl = child._impl
                if impl is not None and _is_class_command(impl):
                    classes.append((impl, child))
                find(child)
        find(self)
        for cls, mount in classes:
            target = getattr(cls, '__wrapped__', cls)
            members = {id(m) for m in target.__dict__.values()}
            key = cls.__qualname__
            def claim(node):
                for child in node._children.values():
                    fn = child._impl
                    # class members too: a nested (or bound
                    # inner) class found in the parent's dict
                    # constructs through the parent instance's
                    # attribute--BIC composes without Appeal
                    # knowing
                    if fn is not None and id(fn) in members:
                        owners[id(fn)] = key
                        # class members too: a nested class
                        # constructs from its owner's instance,
                        # which exists only at the owner's mount
                        if child.parent is not mount:
                            where = (child.parent.name
                                     or '<the top level>')
                            place = ('the top level'
                                     if mount is self else
                                     f"{key!r}'s own mount")
                            raise AppealConfigurationError(
                                f"{fn.__name__!r} is a method of "
                                f"{key!r}, but it's mounted under "
                                f"{where!r}; a method mounts only "
                                f"at {place} (the same-world "
                                f"rule)")
                    claim(child)
            claim(self)

    def option(self, name, *options, annotation=None,
               default=_UNSET):
        """
        Additional decorator for @command functions: maps only the
        strings you specify for one keyword-only parameter,
        blowing away the default mappings (so naming just the long
        suppresses the auto short).  The option's grammar--
        converter, flag-ness--comes from the PARAMETER (ruled
        2026-07-25, arglet style): its annotation, else
        type(default), else str.  annotation=/default= override
        that when the option should genuinely differ from the
        parameter.  Stack several to accumulate strings; each call
        is its own rule.
        """
        # build's "not specified" marker is cheapsig.empty (the same singleton
        # build compares against); convert here at call time.
        from . import cheapsig
        if default is _UNSET:
            default = cheapsig.empty
        if annotation is None:
            annotation = cheapsig.empty
        def decorator(callable):
            if (isinstance(callable, _MethodType)
                    and isinstance(callable.__self__, Appeal)
                    and callable.__func__
                        is type(callable.__self__).help_and_version_precommand):
                # the bound precommand: Python mints a fresh bound
                # object per attribute access, so attribute-marking
                # can't stick--record in the app's own table
                if name not in ('version', 'help'):
                    raise AppealConfigurationError(
                        f"option: the precommand has no parameter "
                        f"{name!r} (only 'help' and 'version')")
                callable.__self__.root._precommand_options[name] = \
                    tuple(options)
                callable.__self__.root._invalidate()
                return callable
            if (isinstance(callable, _MethodType)
                    and isinstance(callable.__self__, Appeal)):
                # a bound app method registered as a command
                # (help's knobs, ruled 2026-08-05): bound methods
                # mint a fresh object per attribute access, but
                # they hash by (instance, function), so the
                # registry's key still finds them.  Cheap
                # validation off the code object (rule 2: no
                # inspect.signature at decoration time).
                code = callable.__func__.__code__
                named = code.co_argcount + code.co_kwonlyargcount
                if name not in code.co_varnames[1:named]:
                    raise AppealConfigurationError(
                        f"option: {callable.__func__.__name__} has "
                        f"no parameter {name!r}")
                root = callable.__self__.root
                root._decorations.add_option(
                    callable, name, options,
                    annotation=annotation, default=default)
                root._invalidate()
                return callable
            self.root._decorations.add_option(
                callable, name, options,
                annotation=annotation, default=default)
            self._invalidate()
            return callable
        return decorator

    def complete(self, words, prefix=''):
        """
        Candidate completions for the partial word `prefix`, given
        the `words` already typed.  Shell integration scripts call
        this; an empty list means "no opinion" (operand values are
        the shell's business).
        """
        from .complete import completions, completions_set
        table = self._table()
        if not table:
            return completions(self.plan, words, prefix)
        sets = {}
        for parent, entries in self._subs.items():
            sets[parent] = {
                'commands': {
                    name: self._build(fn, name=name,
                                method_of=self._method_owner.get(id(fn)))
                    for name, fn in entries},
                'repeat': self._sub_repeat.get(parent, False),
            }
        # the real help/version commands ride the table; no
        # legacy synthesis (banishment must banish)
        return completions_set(self.plans, self.global_plan, words, prefix,
                            auto_version=False,
                            repeat=self.repeat, sets=sets or None,
                            help=False)

    def help(self, topic='', *, usage=True, summary=True, doc=True):
        """
        Print usage documentation on a specific command.
        (That summary line doubles as the help command's listing
        row.)  Bare: the --help text (bare apps) or the command
        listing (sets), v1-style--also returned.  With a topic:
        that command's help page.  This method IS the help
        command (and -h/--help, via the precommand); subclass and
        override to customize every spelling at once.

        The knobs (Larry's design, 2026-08-05; v1's usage()
        folded in): usage=False suppresses the usage line,
        summary=False the summary line, doc=False the doc AND the
        arguments/options/commands sections--each with the
        template text before it.  help(summary=False, doc=False)
        is just the usage line.  As the help command the knobs
        stay API-only: default_mappings unmaps them (zero-string
        app.option()).
        """
        suppress = set()
        if not usage:
            suppress.add('usage')
        if not summary:
            suppress.add('summary')
        if not doc:
            suppress.update(('doc', 'arguments', 'options',
                             'commands'))
        suppress = frozenset(suppress)
        if topic:
            return self._help_topic_page(topic, suppress)
        table = self._table()
        if table:
            from .plan import command_set_usage
            from .help import summary, command_set_corpus
            from .render import render_help_page
            entries = [(w, summary(c)) for w, c in table.items()]
            corpus = command_set_corpus(
                self.global_plan, entries, False, auto_version=False,
                doc=self._program_doc_override())
            from .render import help_margin
            text = render_help_page(
                command_set_usage(self._prog(), self._display_global()),
                corpus, self.templates,
                margin=help_margin(self.margin),
                file=_sys.stdout, stylesheet=self.stylesheet,
                suppress=suppress).rstrip('\n')
        else:
            from .help import merge_docs, parse_docstring
            from .render import help_margin, render_help_page
            plan = self.plan
            corpus = merge_docs(plan)
            override = self.root.doc
            if override is not None:
                # tier 1 overrides a bare app's prose too; the
                # signature-bound sections stay with the command
                parsed = parse_docstring(override, '<program documentation>')
                corpus['summary'] = parsed['summary']
                corpus['documentation'] = parsed['documentation']
            text = render_help_page(
                plan.usage(), corpus, self.templates,
                margin=help_margin(self.margin),
                file=_sys.stdout, stylesheet=self.stylesheet,
                suppress=suppress).rstrip('\n')
        print(text)
        # returns None: help is a COMMAND implementation now
        # (ruled 2026-07-25), and a command's return value is its
        # exit status--text would sys.exit(text).  Capture stdout
        # for the text.

    def documentation(self, format):
        """
        The program's documentation rendered in the named format--
        the grammar describing itself in one more dialect, like
        completion(shell).  Formats (ruled 2026-08-05): 'gfm'
        (GitHub-flavored Markdown: definition lists as
        inline-HTML <dl>, everything else GitHub renders
        natively), 'commonmark' (pure CommonMark: definition
        lists as bold term + blockquote, strikethrough stripped,
        alerts as bold-labelled blockquotes), 'troff' (a man(1)
        page).  Unknown formats refuse by name.  Returns the
        text; where it goes is the caller's business--there is
        deliberately NO command-line switch for this: wire it up
        yourself if you want one.
        """
        if format in ('gfm', 'commonmark'):
            from .markdown import to_commonmark, to_github
            transform = (to_github if format == 'gfm'
                         else to_commonmark)
            prog = self._prog()
            table = self._table()
            doc = self._program_doc()
            if not table:
                return transform(doc or '')
            parts = [f'# {prog}']
            if doc:
                parts.append(doc)
            for word, fn in table.items():
                f = getattr(fn, '__func__', fn)
                if f in (Appeal.help, Appeal.print_version):
                    continue        # stock commands document
                                    # themselves in help, not READMEs
                parts.append(f'## {prog} {word}')
                d = _inspect.getdoc(fn)
                if d and d.strip():
                    parts.append(d)
            return transform('\n\n'.join(parts))
        if format != 'troff':
            raise AppealConfigurationError(
                f"documentation format {format!r} isn't supported "
                f"(only 'gfm', 'commonmark', and 'troff', for now)")
        from .help import command_set_corpus, man_page, merge_docs, summary
        from .plan import command_set_usage
        prog = self._prog()
        version = str(self.version) if self.version is not None else None
        table = self._table()
        if not table:
            plan = self.plan
            return man_page(prog, merge_docs(plan), plan.usage(prog),
                            version=version)
        entries = [(w, summary(c)) for w, c in table.items()]
        corpus = command_set_corpus(
            self.global_plan, entries, False, auto_version=False,
            doc=self._program_doc_override(), listing=False)
        pages = [(word,
                  self.plan_for(word).usage(f'{prog} {word}'),
                  merge_docs(self.plan_for(word)))
                 for word in table]
        return man_page(prog, corpus,
                        command_set_usage(prog, self._display_global()),
                        command_pages=pages, version=version)

    def schema(self):
        """
        The program described as JSON-safe data--the machine-
        readable twin of --help.  Pairs with read_mapping() to run
        a command from a JSON object.
        """
        from .schema import describe, describe_set
        table = self._table()
        if not table:
            return describe(self.plan)
        return describe_set(self.plans, self.global_plan, self._prog())

    def read_mapping(self, callable, mapping):
        "v1's API: call `callable` with values pulled from `mapping`."
        from .read import read_mapping
        self._finalize()
        return read_mapping(callable, mapping)

    def read_iterable(self, callable, iterable):
        "v1's API: call `callable` once per row; returns the results."
        from .read import read_iterable
        self._finalize()
        return read_iterable(callable, iterable)

    def read_csv(self, callable, reader, *, first_row_map=None):
        "v1's API: read_iterable for csv.reader input (see read_csv)."
        from .read import read_csv
        self._finalize()
        return read_csv(callable, reader, first_row_map=first_row_map)

    def unnested(self):
        """
        v1 compat marker: the decorated converter reads its keys
        from the enclosing mapping level.  v2 reads both the nested
        and flat spellings anyway, so this is a no-op.
        """
        def decorator(callable):
            return callable
        return decorator

    def argument(self, parameter_name, *, usage):
        """
        Additional decorator for @command functions: renames one
        parameter in usage lines and help tables.  On an operand,
        the shown name; on an option, the metavar
        (`[-t|--times <COUNT>]`).  Reaches both, despite the name.
        """
        def decorator(callable):
            self.root._decorations.add_usage(callable,
                                             parameter_name, usage)
            self._invalidate()
            return callable
        return decorator

    parameter = argument    # the older spelling, kept as an alias

    def app_class(self):
        """
        v1's class-based-commands API, as a compatibility layer
        over class-as-app: returns (app_class, command_method).
        Decorate the class with @app_class() and its methods with
        @command_method(); the class's __init__ is the global
        command, Appeal constructs the instance, and the methods
        late-bind to it--v1's documented contract, new machinery.
        """
        def app_class_decorator():
            def decorator(cls):
                self.precommand()(cls)
                return cls
            return decorator
        def command_method(name=None):
            # a method registers exactly like @app.command() in a
            # class body; adoption claims it when the class runs
            # through @app_class()
            return self.command(name)
        return app_class_decorator, command_method

    # ---- first use: build and compile, one command at a time ----
    #
    # Laziness extends *per command*: dispatching (or examining)
    # one command never builds the others.  A config error in
    # command B surfaces when B is first used, not before.

    def _table(self):
        "The {command word: callable} table.  Cheap: no inspection."
        self._finalize()
        table = {}
        for name, callable in self._commands:
            if name in table:
                raise AppealConfigurationError(
                    f"two commands named {name!r}")
            table[name] = callable
        if self._global is not None and self._global.__name__ in table:
            raise AppealConfigurationError(
                f"the global command {self._global.__name__!r} has the "
                f"same name as a command")
        if not table and self._global is None:
            raise AppealConfigurationError(
                "no commands: use @app.command() or @app.precommand()")
        return table

    def _build(self, callable, **kwargs):
        """
        build_plan() a top plan and stamp it with the app's operand
        usage format (positional_argument_usage_format).  Every
        top plan the app renders funnels through here; child plans
        read the format off their root at render time.
        """
        from .build import build_plan
        # the policy registers via the registrar-proxy's
        # app.option() (arglet style, Larry's design 2026-07-22);
        # build constructs the proxy around the real app
        plan = build_plan(callable,
                     default_options=self.root.default_options,
                     app=self.root,
                     decorations=self.root._decorations, **kwargs)
        plan.arg_format = self.positional_argument_usage_format
        plan.auto_help = self._help_enabled
        return plan

    def _node_for(self, word):
        """
        The tree node a bare word means: a direct child, or the
        UNIQUE descendant with that word.  Ambiguous bare words
        refuse by name (path addressing--walk .commands--is the
        unambiguous spelling; dispatch itself resolves per-parent,
        deepest set first, and never comes through here).
        """
        node = self._children.get(word)
        if node is not None:
            return node
        matches = []
        def walk(parent):
            for w, child in parent._children.items():
                if w == word:
                    matches.append((parent, child))
                walk(child)
        walk(self)
        if len(matches) > 1:
            parents = ', '.join(sorted(repr(p.name or '(root)')
                                       for p, _ in matches))
            raise AppealConfigurationError(
                f"plan_for({word!r}): ambiguous--commands "
                f"named {word!r} exist under {parents}")
        if matches:
            return matches[0][1]
        return None

    def _plan_for_node(self, node, word):
        "The node's Plan, cached by NODE (words can repeat)."
        plan = self._plans.get(id(node))
        if plan is None:
            callable = node._command_callable()
            if callable is None:
                raise AppealConfigurationError(
                    f"no command named {word!r}")
            owner = self._method_owner.get(id(callable))
            if owner is None:
                _refuse_orphan_method(callable)
            plan = self._build(callable, name=word, method_of=owner)
            plan.argv0 = self.root._prog()
            plan = self._plans.setdefault(id(node), plan)
        return plan

    def plan_for(self, word):
        "The named command's Plan, built at first request."
        self._finalize()
        node = self._node_for(word)
        if node is None:
            raise AppealConfigurationError(f"no command named {word!r}")
        return self._plan_for_node(node, word)

    def _program_doc(self):
        """
        The program's documentation, three tiers (ruled
        2026-08-01), highest first: the doc= constructor
        argument; the global command's docstring; and--the
        pleasant magic--the module docstring, when every user
        command lives in one module.  Returns None when nobody
        has anything to say.
        """
        root = self.root
        if root.doc is not None:
            return root.doc
        if root._global is not None:
            d = _inspect.getdoc(root._global)
            if d and d.strip():
                return d
        modules = set()
        for word, node in root._children.items():
            fn = node._command_callable()
            if fn is None:
                continue
            f = getattr(fn, '__func__', fn)
            if f in (Appeal.help, Appeal.print_version):
                continue    # the stock commands live in appeal;
                            # they don't get a vote
            m = getattr(fn, '__module__', None)
            if m is None:
                return None
            modules.add(m)
        if len(modules) == 1:
            module = _sys.modules.get(modules.pop())
            d = getattr(module, '__doc__', None)
            if d and d.strip():
                import textwrap as _textwrap
                return _textwrap.dedent(d).strip('\n')
        return None

    def _program_doc_override(self):
        """
        Tiers 1 and 3 of the doc chain--the sources that
        OVERRIDE what merge_docs would read from the global
        command.  Tier 2 (the global docstring) returns None
        here: the existing merge path already honors it, with
        its fuller validation.
        """
        root = self.root
        if root.doc is not None:
            return root.doc
        if root._global is not None:
            d = _inspect.getdoc(root._global)
            if d and d.strip():
                return None         # tier 2: merge_docs' job
        return self._program_doc() if root.doc is None else root.doc

    def _prog(self):
        return self.name or _os.path.basename(self.script) or 'program'

    @property
    def plan(self):
        "The lone plan of a global-command-only app."
        if self._table():
            raise AppealConfigurationError(
                "this program has subcommands; use .plan_for(name)")
        return self.global_plan

    @property
    def plans(self):
        "Every command's Plan.  Deliberately eager: builds them all."
        return {word: self.plan_for(word) for word in self._table()}


    def _display_global(self):
        """
        The global plan for DISPLAY (usage lines, corpora): None
        when the precommand merely hosts the slot--v1 never
        advertised auto options in usage, and the corpus goldens
        pin those lines.
        """
        plan = self.global_plan
        if plan is not None and getattr(plan.callable,
                                        'precommand', False):
            return None
        return plan

    def _precommand_plan(self):
        """
        The precommand's mini plan, built from what
        default_mappings mapped to it.  None when nothing is.
        The closure is marked stock when the app doesn't override
        the precommand family.
        """
        mapped = self.root._precommand_options
        if not mapped:
            return None
        app = self.root
        # empty strings = the explicit unmap (ruled
        # 2026-07-25): treated as not mapped at all
        want_v = bool(mapped.get('version'))
        want_h = bool(mapped.get('help'))
        # the closures mirror Appeal.precommand's signature:
        # optional[str] marks the topic's oparg optional (bare -h
        # gives ''), version=False is a flag
        if want_v and want_h:
            def precommand(*, help: optional[str] = None,
                           version=False):
                app.help_and_version_precommand(help=help, version=version)
        elif want_v:
            def precommand(*, version=False):
                app.help_and_version_precommand(version=version)
        else:
            def precommand(*, help: optional[str] = None):
                app.help_and_version_precommand(help=help)
        if want_v:
            app.root._decorations.add_option(precommand, 'version',
                                             mapped['version'])
        if want_h:
            app.root._decorations.add_option(precommand, 'help',
                                             mapped['help'])
        cls = type(app)
        precommand.precommand = True
        return self._build(precommand, name=self.root._prog())

    @property
    def global_plan(self):
        self._finalize()
        plan = self._global_plan
        if plan is None:
            pre = self._precommand_plan() if self.parent is None else None
            if self._global is not None:
                plan = self._build(self._global)
                plan.pre_plan = pre
            elif pre is not None:
                # no global command: the precommand IS the global
                # plan--its options scan the pre-word segment and
                # it runs (a no-op when nothing was given) first
                plan = pre
            if plan is not None:
                if self._global_plan is None:
                    self._global_plan = plan
                plan = self._global_plan
        return plan

    def global_plans(self):
        """
        The ordered head eras' plans (Larry's repeatable precommand, 2026-08-21):
        the help/version precommand at the head (when default_mappings mapped
        anything to it), then each precommand the user registered, front-to-back.
        Empty when there's no head at all.  scan_command_set scans them in order.
        """
        self._finalize()
        plans = []
        if self.parent is None:
            pre = self._precommand_plan()
            if pre is not None:
                plans.append(pre)
        for era in self._precommands:
            plans.append(self._build(era))
        return plans

    def process(self, args=None, config=None):
        """
        Parse args (default: sys.argv[1:]) and invoke the command;
        returns its return value.
        """
        argv = _sys.argv[1:] if args is None else list(args)
        return self._compiled_dispatch(argv, config=config)

    def _compiled_dispatch(self, argv, config=None):
        """
        The one path (Larry, 2026-08-21): compile the parser in memory and
        run the Processor.
        Dispatches the head eras then the command words -- recursing into a
        command's subcommand node when it has one -- and logs the instances the
        old two-stage execute() did (app.instances reads _last_processor).
        """
        holder = _RunLog(self)
        self._last_processor = holder
        # whole-line STRUCTURAL pre-scan first (Larry, 2026-08-23): parcel and
        # validate the ENTIRE command set -- every command's arity, oparg counts,
        # unknown options -- running NO converter or command body.  A structural
        # error anywhere aborts here, before the first command runs.  Then the
        # live pass converts and runs left to right (a later CONVERSION error
        # doesn't un-run an earlier command; see [[streaming-dispatch]]).
        # both passes visit the same converters in the same order, so the dry
        # pass records each built converter class into `built` (traversal order)
        # and the live pass pops them instead of rebuilding -- no plan or
        # converter is built twice (Larry's insight, 2026-08-23).  reverse() so
        # a live pop() off the end yields them front-to-back.
        built = []
        self._run_node(list(argv), 0, holder, top=True, config=config,
                       dry=True, built=built)
        built.reverse()
        result, _ = self._run_node(list(argv), 0, holder, top=True,
                                   config=config, built=built)
        holder.result = result
        return result

    def _run_node(self, argv, pos, holder, top, env=None, config=None,
                  dry=False, built=None):
        "Dispatch one set node's eras + command words; recurse for subcommands."
        if env is None:
            env = {}                                # class-as-app instance store
        from .compile import build_converters, _converter_key
        self._finalize()
        table = self._table()
        if config and self._global is None:
            # config layers only the global command's options; a commands-only
            # program has nowhere for it to land (an empty config is a no-op)
            key = next(iter(config))
            raise AppealDataError(
                f"config: {key!r} isn't an option of this program (it has no "
                f"global command)")
        era_plans = self.global_plans()             # carries the global as a head era
        # laziness is per command (build_converters compiles independently): the
        # head eras always run, so build them now; each command word builds ITS
        # OWN converter only when dispatched -- a broken sibling costs nothing
        # until it's used.  The dry pass builds; the live pass pops what the dry
        # pass built (same converters, same order).
        if dry:
            era_classes = build_converters(era_plans) if era_plans else {}
            precommands = [era_classes[_converter_key(p)] for p in era_plans]
            built.extend(precommands)
        else:
            precommands = [built.pop() for _ in era_plans]

        result = None
        for cls, era_plan in zip(precommands, era_plans):   # head eras, in order
            conv = cls()
            # -h/--help/-V/--version is Appeal's own metadata precommand: its
            # body sys.exit()s the help/version page and outranks parsing, so it
            # must run even in the dry pre-scan -- otherwise the pre-scan would
            # validate (and reject) a command portion that help would preempt.
            # It's a no-op unless help/version was actually requested.
            is_meta = getattr(era_plan.callable, 'precommand', False)
            proc = Processor(argv[pos:], conv, table, dry=dry and not is_meta)
            if config is not None and era_plan.callable is self._global and not dry:
                # layer config onto the global command: parse argv, merge config
                # for options argv didn't set, THEN invoke (argv wins, whole).
                # Config only adds OPTION values, never changes structure, so the
                # dry pre-scan validates argv alone (config is a live-only merge).
                proc.enter(conv)
                proc._loop()
                _config_apply(conv, table, self.global_plan, config,
                              self.plan_for)
                result = proc.root()
            else:
                if dry and config is not None and era_plan.callable is self._global:
                    # config KEY vetting is structural -- fire its refusals in the
                    # pre-scan, before the command portion is parsed (the value
                    # merge stays live, above)
                    _config_vet(self.global_plan, frozenset(table), config,
                                self.plan_for)
                result = proc.run()
            if dry:                                 # pre-scan: no instances, no
                pos += proc.consumed                # halt (scan the whole line)
                continue
            if cls.constructs is not None:          # a global class-as-app: its
                env[cls.constructs] = result        # methods bind to this instance
            holder.instances.append(               # eras log (None, instance-or-None)
                (None, result if cls.constructs is not None else None))
            if _halts(result):
                return result, pos
            pos += proc.consumed
        dispatched = False              # did a command word of THIS node run?
        while pos < len(argv):
            word = argv[pos]
            if word not in table:
                if not top:
                    return result, pos          # pop back: a parent may own it
                raise _unexpected(word, table)
            c = table[word]
            if dry:
                owner = self._method_owner.get(id(c))
                if owner is None:                   # a self-method with no class
                    _refuse_orphan_method(c)        # that claimed it: refuse by name
                plan = self._build(c, method_of=owner)  # method_of -> binds
                cls = build_converters([plan])[_converter_key(plan)]  # this cmd
                built.append(cls)
            else:
                cls = built.pop()                   # the dry pass built this
            pos += 1
            conv = cls()
            if cls.binds is not None and not dry:   # a method command: self is the
                conv.bound = env.get(cls.binds)     # instance a parent constructed
            proc = Processor(argv[pos:], conv, table, dry=dry)
            result = proc.run()
            dispatched = True
            if dry:                                 # pre-scan: no instances, no
                pos += proc.consumed                # halt, but keep recursing
            else:
                if cls.constructs is not None:      # a class command: stash instance
                    env[cls.constructs] = result
                instance = result if _is_class_command(c) else None
                holder.instances.append((holder._command_for(word), instance))
                if _halts(result):
                    return result, pos
                pos += proc.consumed
            # recurse into the command's subcommand node: it may dispatch a
            # subcommand OR (the line stops at the parent) run that node's
            # default command -- so recurse even at end-of-line when a default
            # is waiting.  Every command has a child node (lazy registration);
            # only enter one that actually has subcommands or a default.
            child = self._children.get(word)
            if child is not None and (child._commands
                                      or child._default is not None):
                result, pos = child._run_node(argv, pos, holder, top=False,
                                              env=env, dry=dry, built=built)
            if not self._node_repeat and pos < len(argv):
                # this set doesn't cycle: pop the leftover word up to an
                # ancestor whose set does (the parent's loop re-dispatches it);
                # at the top with nothing to claim it, it's unexpected
                if not top:
                    return result, pos
                tok = argv[pos]
                pool = proc.handlers if tok.startswith('-') else table
                raise _unexpected(tok, pool)

        if not dispatched:
            # the line stopped at this node without naming a subcommand of it.
            # Run this node's default command; or, for a top-level set with no
            # default, print the listing for orientation and exit 1 (git-style,
            # ruled 2026-07-09).  A global command runs as a head era regardless
            # -- it processes pre-command options; it doesn't answer a bare line.
            if self._default is not None:
                if dry:
                    d_plan = self._build(self._default)
                    dcls = build_converters([d_plan])[_converter_key(d_plan)]
                    built.append(dcls)
                else:
                    dcls = built.pop()
                dconv = dcls()
                if dcls.binds is not None and not dry:
                    dconv.bound = env.get(dcls.binds)
                dproc = Processor(argv[pos:], dconv, table, dry=dry)
                result = dproc.run()
                if not dry:
                    holder.instances.append((None, None))
                pos += dproc.consumed
            elif top and self._commands and not dry:
                self.help()                         # the set listing, to stdout
                result = 1
        return result, pos

    @property
    def instances(self):
        """
        The most recent run's execution log: (command, instance)
        pairs in execution order (see Processor).
        """
        processor = self._last_processor
        return processor.instances if processor is not None else []

    def main(self, args=None, config=None):
        """
        Parse-and-execute with polite error handling, then EXIT
        the process with the result--0.6.4's contract, restored
        2026-07-19 (Larry's ruling, review item J1): a script
        whose last line is bare `app.main()` reports its exit
        code to the shell.  Usage errors exit 2 (the getopt/
        argparse convention); a command's nonzero int return is
        the exit code; success exits 0.  Want the code returned
        instead?  That's process().
        """
        import os as _os
        if (args is None and '_APPEAL_COMPLETE' in _os.environ
                and not _sys.argv[1:]):
            # a shell-completion reentry: bare args, mode in the
            # environment.  Answer it instead of parsing.
            _sys.exit(completion_reentry(
                lambda words, prefix: self.complete(words, prefix),
                self._prog()))
        # the one engine (2026-08-22): main() drives the same in-memory dispatch
        # process() does (config layering included).  A help/version precommand
        # prints then sys.exit()s; run_main catches that and converts to a code.
        parse = lambda argv: self._compiled_dispatch(list(argv), config=config)
        _sys.exit(run_main(parse, args, stylesheet=self.stylesheet,
                           errors=self.errors, margin=self.margin))

    def _mcp_instance(self, config):
        """
        Construct-once, the class-as-app half of MCP: the global
        class's __init__ runs at server startup, fed by config
        under the layering rules (strict keys, global-command
        options only), and every method tool binds to the one
        instance.  Returns the instance, or None when the global
        command isn't a class.  A required __init__ positional has
        no coverage (config supplies only options), so it refuses
        here--at startup, not mid-call.
        """
        from .read import read_mapping
        table = self._table()
        global_plan = self.global_plan
        if not (table and global_plan is not None
                and global_plan.constructs is not None):
            if config is not None:
                raise AppealConfigurationError(
                    "mcp(): config feeds a class-based program's "
                    "__init__ at server startup; this program has "
                    "no class to construct")
            return None
        _config_vet(global_plan, frozenset(table), config or {},
                    command_plan_for=self.plan_for)
        return read_mapping(global_plan, config or {})

    def _mcp_bound_plan(self, word, plan, instance):
        """
        The plan a tool call reads through.  A method command
        rebuilds from the method bound to the startup instance
        (self is gone from the signature, so read_mapping drives
        it like any function); everything else reads as-is.
        """
        if plan.binds is None:
            return plan
        if instance is None:   # pragma: no cover -- method commands
            # exist only under a class global, whose instance always
            # constructs at startup; belt and braces
            raise AppealConfigurationError(
                f"{word!r}: bound to {plan.binds!r}, and no startup "
                f"instance provides it")
        if plan.constructs is not None:
            # a bound inner class: construction goes through the
            # parent instance's attribute (BIC composes)
            return self._build(getattr(instance, plan.name), name=plan.name)
        return self._build(plan.callable.__get__(instance), name=plan.name)

    def mcp(self, *, config=None, version=None):
        """
        Serve this program's commands as MCP tools--the Model
        Context Protocol's stdio transport, stdlib only.  Each
        command becomes a tool: its docstring summary is the
        description, its signature the input schema, and calls
        arrive as mappings through the read driver (the same
        rules as read_mapping: converters always apply, defaults
        fill absences).  A class-based program constructs its
        instance ONCE, at server startup: config feeds __init__
        (the layering rules), and method tools dispatch bound.
        Runs until stdin closes.
        """
        from .read import read_mapping
        from .schema import mcp_input_schema
        from .help import summary
        table = self._table()
        if self._subs:
            raise AppealConfigurationError(
                "nested subcommands aren't in mcp(); give the "
                "command a flat name instead (name='db add')")
        instance = self._mcp_instance(config)
        if not table:
            commands = {self._prog(): self.global_plan}
        else:
            commands = {word: self.plan_for(word) for word in table}
        tools = {}
        for word, plan in commands.items():
            bound = self._mcp_bound_plan(word, plan, instance)
            tools[word] = (summary(plan.callable) or '',
                           mcp_input_schema(bound),
                           lambda arguments, p=bound:
                               read_mapping(p, arguments))
        return run_mcp(tools, self._prog(),
                       str(version or self.version or '0'))

    def repl(self, *, prompt=None, banner=None):
        """
        §8.9: an interactive mode for any Appeal program--read a
        line, split it, feed it through the parser exactly as a
        command line, execute, loop.  Tab completion is the same
        machinery the shells use.  EOF (^D) or `quit` leaves.
        Returns None.
        """
        import shlex as _shlex
        prog = self._prog()
        prompt = prompt if prompt is not None else f'{prog}> '
        try:
            import readline as _readline

            def completer(text, state):
                buffer = _readline.get_line_buffer()
                try:
                    words = _shlex.split(buffer[:_readline.get_begidx()])
                except ValueError:
                    words = buffer[:_readline.get_begidx()].split()
                try:
                    candidates = self.complete(words, text)
                except Exception:
                    candidates = []
                return candidates[state] if state < len(candidates) else None

            _readline.set_completer(completer)
            _readline.set_completer_delims(' \t')
            _readline.parse_and_bind('tab: complete')
        except ImportError:      # pragma: no cover -- no readline
            pass
        if banner is not None:
            print(banner)
        while True:
            try:
                line = input(prompt)
            except EOFError:
                print()
                return
            except KeyboardInterrupt:
                print()
                continue
            try:
                words = _shlex.split(line)
            except ValueError as e:
                print(f'error: {e}')
                continue
            if not words:
                continue
            if words == ['quit'] or words == ['exit']:
                return
            try:
                result = self.process(words)
            except AppealDataError as e:
                print(f'error: {e}')
                usage = getattr(e, 'usage', None)
                if usage:
                    print(f'usage: {usage}')
            except AppealConfigurationError as e:
                print(f'configuration error: {e}')
            else:
                if result is not None:
                    print(result)

    def completion(self, shell):
        """
        The shell function text that wires this program's name to
        tab completion--source it, or install it in the shell's
        completion directory.  The zero-effort spelling sources it
        directly:

            eval "$(env _APPEAL_COMPLETE=source_bash mytool)"
        """
        return completion_script(shell, self._prog())
