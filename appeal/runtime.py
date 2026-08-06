#!/usr/bin/env python3
#
# appeal/runtime.py
# Part of Appeal v2.
# Copyright 2021-2026 by Larry Hastings
#
# Everything a parser needs at run time: the exceptions, the token
# driver, the collectors, the Option/MultiOption protocol, command
# dispatch, help and usage rendering, and the converter vocabulary.
#
# This whole file, unchanged, is streamed into every standalone
# script; the other appeal modules import from it for in-process
# use.  So it must be stdlib-only--the only appeal import is the
# guarded one just below.

import enum
import operator
import sys


# --8<-- start appeal exceptions --8<--
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
    Raised at *build* time: the program's signature (or a request,
    like standalone emission) doesn't make sense.  Always names the
    offender.  A bug, so main() lets it raise.  Generated parsers
    never raise it--but the converter vocabulary below does, so it
    travels with the file.
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


def foreign_appeal_error(e):
    """
    A standalone script's user module imports appeal (to raise
    AppealError, subclass Option, ...), so exceptions it raises
    are the INSTALLED appeal's classes--not this script's
    streamed copies, and `except` can't match them by identity
    (the two-copies problem, exceptions edition; see is_option).
    Recognizes one by name and home (canonical or old-prefixed
    spelling--the raiser controls which is its class's real
    __name__); returns 'configuration', 'data', 'command',
    'error', or None.
    """
    for c in type(e).__mro__:
        if not c.__module__.endswith('appeal.runtime'):
            continue
        if c.__name__ in ('ConfigurationError',
                          'AppealConfigurationError'):
            return 'configuration'
        if c.__name__ in ('DataError', 'AppealDataError'):
            return 'data'
        if c.__name__ in ('CommandError', 'AppealCommandError'):
            return 'command'
        if c.__name__ == 'AppealError':
            return 'error'
    return None
# --8<-- end appeal exceptions --8<--


# --8<-- start appeal convert --8<--
# --8<-- requires appeal exceptions --8<--
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
# --8<-- end appeal convert --8<--


# --8<-- start appeal parse tokens --8<--
# --8<-- requires appeal exceptions --8<--
def parse_tokens(argv, options, usage=None, command_split=None,
                 positions=None):
    """
    The driver: split argv into plain operands and option values.

    options maps each option string to (key, kind), where key is
    the option's canonical name and kind is 'flag', 'value', or
    'multi'.  Folds and groups carry per-occurrence operand
    counts: (key, kind, minimum, maximum); consumption is greedy
    to the maximum--an optional operand takes the next token
    unconditionally (v1).  Returns (operands, given): operands is
    a list of strings; given maps keys to True (flags), a raw
    string (values), or a list of raw strings (multi).  Options
    that aren't repeatable kinds error when given twice (v1
    semantics).

    Handles: '--' (ends option recognition for this parse only--
    it's local state, never program state), '--name=value',
    and single-dash bundling of flags ('-abc').

    command_split, if given, is (minimum, maximum, command_words)
    for a global command parsed ahead of a subcommand: once
    `minimum` operands are consumed, the first operand naming a
    command is the command word; at `maximum` (when bounded) the
    next operand is the word regardless of name.  Parsing stops
    there, and (operands, given, rest) is returned instead, where
    rest begins with the command word (empty if the line ran out).

    positions, if given, is a dict this function fills in: for
    each option, how many operands had been consumed when it first
    appeared.  The gate rule (a required group blocks options
    behind it until it's been fed) is checked against these.
    """
    operands = []
    given = {}
    it = iter(argv)
    force_positional = False

    seq = [0]

    def record(key, kind, value):
        # seq is the token clock: adjacent options share an
        # operand position, so sibling-group announcement order
        # needs a finer tick.  Every key's LAST occurrence seq is
        # stamped under ('seq', key)--tuple keys are invisible to
        # the gate logic, which reads plain string keys.
        seq[0] += 1
        if positions is not None:
            positions[('seq', key)] = seq[0]
            if key not in positions:
                positions[key] = len(operands)
        if kind.startswith('w:'):
            # a *args group's option: binding to an instance
            # happens later, by operand position (window_options)
            given.setdefault(key, []).append((len(operands), kind[2:], value))
            return
        if kind.startswith('s:'):
            # a sibling option group's shared option: binding is
            # by announcement (--e1/--e2), resolved after the
            # scan (sibling_scopes); the seq is the position
            given.setdefault(key, []).append((seq[0], kind[2:], value))
            return
        if kind == 'fold1':
            # a StrictOption: at most once, by declaration
            if key in given:
                raise UsageError(
                    f"option {key} specified more than once", usage)
            given[key] = value
        elif kind in ('flag', 'nullary', 'value', 'group'):
            # last one wins (RULED by Larry 2026-07-18, review
            # item 6: repetition is for overriding defaults--a
            # shell alias baking in `--north` is harmlessly
            # overridden by a later `--south`).  A bare flag
            # idempotently stores `not default` (its entry's
            # presence value): -v -v is -v.  The explicit
            # spellings (--verbose=false) are absolute; a bare
            # occurrence after one simply stores not-default
            # again.  Reinsertion keeps `given` in last-occurrence
            # order, which is how a parameter shared by several
            # option strings knows which string spoke last.
            if key in given:
                del given[key]
            given[key] = value
        else:   # 'multi' collects raw strings; 'fold' tuples of them
            given.setdefault(key, []).append(value)

    def flag_value(name, text):
        # a flag's explicit '=' value: exactly these two
        # spellings (ruled 2026-07-09)--no yes/no/on/off zoo
        if text == 'true':
            return True
        if text == 'false':
            return False
        raise UsageError(
            f"option {name!r}: '=' value must be 'true' or "
            f"'false', not {text!r}", usage)

    for token in it:
        if force_positional or (not token.startswith('-')) or (token == '-'):
            if command_split is not None:
                minimum, maximum, command_words = command_split
                n = len(operands)
                if ((maximum is not None and n >= maximum)
                        or (n >= minimum and token in command_words)):
                    return operands, given, [token] + list(it)
            operands.append(token)
            continue

        if token == '--':
            force_positional = True
            continue

        if token.startswith('--'):
            name_part, equals, value_part = token.partition('=')
            entry = options.get(name_part)
            if entry is None:
                tail = did_you_mean(
                    name_part,
                    [s for s in options if s.startswith('--')])
                raise UsageError(
                    f"unknown option {name_part!r}{tail}", usage)
            key, kind = entry[0], entry[1]
            base = kind[2:] if kind[:2] in ('w:', 's:') else kind
            if base in ('fold', 'fold1', 'group'):
                minimum = entry[2]
                maximum = entry[3] if len(entry) > 3 else entry[2]
            else:
                minimum = maximum = entry[2] if len(entry) > 2 else 1
            if base in ('flag', 'nullary') or maximum == 0:
                # flags, value-producing flags, zero-operand
                # folds, options-only groups
                if equals:
                    if base != 'flag':
                        raise UsageError(
                            f"option {name_part!r} doesn't take a value",
                            usage)
                    # a flag with an explicit boolean: --verbose=false
                    # overrides anything a config layer said
                    record(key, kind, flag_value(name_part, value_part))
                    continue
                # a flag's presence stores the value in its table
                # entry (v1: `not default`; a bare entry--the auto
                # help flag--stores True); nullary presence is
                # just True (the converter supplies the value)
                record(key, kind,
                       entry[2] if base == 'flag' and len(entry) > 2
                       else True if base in ('flag', 'nullary') else ())
                continue
            if equals:
                if maximum > 1:
                    counts = (f'{maximum} values' if minimum == maximum
                              else f'up to {maximum} values')
                    raise UsageError(
                        f"option {name_part!r} takes {counts} "
                        f"and can't use '='", usage)
                record(key, kind,
                       (value_part,) if base in ('fold', 'fold1', 'group')
                       else value_part)
                continue
            # greedy to the maximum: an optional operand takes the
            # next token unconditionally, whatever it looks like
            # (v1, probed: -j -5 works, -j -v is a loud conversion
            # error)--EXCEPT '--', the terminator, which outranks
            # greed once the minimum is satisfied (ruled
            # 2026-07-09, POSIX guideline 10: only a REQUIRED
            # oparg may consume '--', the `grep -e --` idiom)
            values = []
            for value in it:
                if value == '--' and len(values) >= minimum:
                    force_positional = True
                    break
                values.append(value)
                if len(values) == maximum:
                    break
            if len(values) < minimum:
                raise UsageError(
                    f"option {name_part!r} requires "
                    f"{'a value' if minimum == 1 else f'{minimum} values'}",
                    usage)
            record(key, kind,
                   tuple(values)
                   if (base in ('fold', 'fold1', 'group') or maximum != 1)
                   else values[0])
            continue

        # a negative number is an operand, unless the program
        # defines that exact short option (v1)
        if token[1].isdigit() and ('-' + token[1]) not in options:
            if command_split is not None:
                minimum, maximum, command_words = command_split
                n = len(operands)
                if ((maximum is not None and n >= maximum)
                        or (n >= minimum and token in command_words)):
                    return operands, given, [token] + list(it)
            operands.append(token)
            continue

        # single dash: one short option, possibly a bundle of flags
        chars = token[1:]
        for index, c in enumerate(chars):
            entry = options.get('-' + c)
            if entry is None:
                raise UsageError(f"unknown option {'-' + c!r}", usage)
            key, kind = entry[0], entry[1]
            base = kind[2:] if kind[:2] in ('w:', 's:') else kind
            if base in ('fold', 'fold1', 'group'):
                minimum = entry[2]
                maximum = entry[3] if len(entry) > 3 else entry[2]
            else:
                minimum = maximum = entry[2] if len(entry) > 2 else 1
            if base in ('flag', 'nullary') or maximum == 0:
                rest = chars[index + 1:]
                if rest.startswith('='):
                    if base != 'flag':
                        raise UsageError(
                            f"option {'-' + c!r} doesn't take a value",
                            usage)
                    record(key, kind, flag_value('-' + c, rest[1:]))
                    break
                record(key, kind,
                       entry[2] if base == 'flag' and len(entry) > 2
                       else True if base in ('flag', 'nullary') else ())
                continue
            rest = chars[index + 1:]
            if rest:
                # attachment, getopt's rule (adopted 2026-07-09,
                # overturning v1's refusal): for an option taking
                # exactly one value, the rest of the token IS the
                # oparg--`-fjoe` is `-f joe`, `-DX=1` is
                # `-D 'X=1'`.  A leading '=' is the separator
                # spelling and is stripped (`-f=joe` is `joe`).
                if maximum == 1:
                    if rest.startswith('='):
                        rest = rest[1:]
                    record(key, kind,
                           (rest,) if base in ('fold', 'fold1', 'group')
                           else rest)
                    break
                counts = (f'{maximum} values' if minimum == maximum
                          else f'up to {maximum} values')
                raise UsageError(
                    f"option {'-' + c!r} takes {counts} and "
                    f"must be last in a bundle", usage)
            # last in its bundle: greedy to the maximum (see the
            # long-option form above, '--' carve-out included)
            values = []
            for value in it:
                if value == '--' and len(values) >= minimum:
                    force_positional = True
                    break
                values.append(value)
                if len(values) == maximum:
                    break
            if len(values) < minimum:
                raise UsageError(
                    f"option {'-' + c!r} requires "
                    f"{'a value' if minimum == 1 else f'{minimum} values'}",
                    usage)
            record(key, kind,
                   tuple(values)
                   if (base in ('fold', 'fold1', 'group') or maximum != 1)
                   else values[0])

    if command_split is not None:
        return operands, given, []
    return operands, given
# --8<-- end appeal parse tokens --8<--


# --8<-- start appeal command set --8<--
# --8<-- requires appeal exceptions --8<--
def scan_command_set(argv, parse_globals, commands, usage=None,
                     default=None, repeat=False, words=None):
    """
    Stage 1 of a multi-command program: scan the whole line--global
    portion, command word, command portion--with no user code.  A
    malformed line dies here, before anything runs (Appeal rule).

    parse_globals and each commands value are (scan, run) pairs.
    With repeat (Appeal's cycling), a command's arguments--all of
    them, optional included--may be followed by another command
    word, resolved against `words`; scanning loops until the line
    runs out.  Returns (invocations, tail): invocations is a list of
    (word, run, operands, given, positions)--word None for the
    global command--and tail is the odd trailing job, if any:
    ('fused', word, callable, tokens) for a commands value that is
    a plain callable (a nested dispatcher, or the generated help
    command--scanned and executed together, stage separation inside
    is its own business), or ('default',) for an empty line with a
    default command (v1's default_command).
    """
    invocations = []
    if parse_globals is not None:
        scan_globals, run_globals = parse_globals
        operands, given, rest, positions = scan_globals(argv)
        invocations.append((None, run_globals, operands, given, positions))
    else:
        rest = list(argv)
    if not rest:
        if default is not None:
            return invocations, ('default',)
        # an empty command line isn't a mistake, it's someone who
        # needs orientation (ruled 2026-07-09, git-style): the
        # caller shows the listing and exits 1; nothing runs
        return invocations, ('bare',)

    # the resolution stack, deepest set on top.  Consulting a set
    # for its FIRST command is free--that's descent, how the line
    # got here; re-entering a set for a second command is what
    # repetition means, and needs that set's own repeat.  A set
    # without repeat doesn't block resolution in its ancestors.
    stack = [{'commands': commands, 'repeat': repeat, 'words': words,
              'usage': usage, 'entered': False}]

    def frames_in_order():
        for depth, frame in enumerate(reversed(stack)):
            if depth == 0 and not frame['entered']:
                yield frame          # descent: first consult free
            elif frame['repeat']:
                yield frame          # re-entry: gated on repeat

    def resolvable_words():
        out = set()
        for frame in frames_in_order():
            out.update(frame['words'] or ())
        return frozenset(out)

    while rest:
        word = rest[0]
        entry = target = None
        for frame in frames_in_order():
            entry = frame['commands'].get(word)
            if entry is not None:
                target = frame
                break
        if entry is None:
            tail = did_you_mean(word, resolvable_words())
            raise UsageError(f"unknown command {word!r}{tail}",
                             stack[-1]['usage'])
        while stack[-1] is not target:
            stack.pop()              # re-base at the resolved set
        target['entered'] = True
        if isinstance(entry, dict):
            # a nested set: the parent runs first, like a global
            # command of its own little set
            stack.append({'commands': entry['commands'],
                          'repeat': entry['repeat'],
                          'words': entry['words'],
                          'usage': entry['usage'],
                          'default': entry.get('default'),
                          'entered': False})
            boundary = resolvable_words()
            operands, given, rest, positions = entry['scan'](
                rest[1:], boundary)
            invocations.append((word, entry['run'], operands, given,
                                positions))
            continue
        if not isinstance(entry, tuple):
            # fused: scanned and executed together, last (stage
            # separation inside is its own business)
            return invocations, ('fused', word, entry, rest[1:])
        scan_command, run_command = entry
        boundary = resolvable_words()
        operands, given, rest, positions = scan_command(
            rest[1:], boundary or None)
        invocations.append((word, run_command, operands, given,
                            positions))

    if len(stack) > 1 and not stack[-1]['entered']:
        # a parent was named but its set never got a command: the
        # set's default command (a (scan, run) pair) fills in, or
        # the line is an error
        d = stack[-1].get('default')
        if d is None:
            raise UsageError("no command specified.", stack[-1]['usage'])
        scan_d, run_d = d
        operands, given, _, positions = scan_d([], None)
        invocations.append((None, run_d, operands, given, positions))
    return invocations, None


def run_command_set(argv, parse_globals, commands, usage=None,
                    default=None, repeat=False, words=None,
                    listing=None):
    """
    Both stages of a multi-command program: scan the whole line,
    then execute left to right.  A nonzero int return halts
    dispatch and is the result (v1's early-exit contract, extended
    to every command in a cycle; other truthy returns don't halt).
    An empty line (no command named, no default command) prints
    the listing--`listing` if given, else the usage text--to
    stdout and returns 1: orientation, not a diagnostic.
    """
    invocations, tail = scan_command_set(argv, parse_globals, commands,
                                         usage, default, repeat, words)
    if tail == ('bare',):
        if listing is not None:
            listing()
        else:
            print(f"usage: {usage}")
        return 1
    result = None
    env = {}    # class-based commands: instances live here
    for word, run, operands, given, positions in invocations:
        result = run(operands, given, positions, env)
        if (isinstance(result, int)
                and not isinstance(result, bool) and result):
            return result
    if tail is not None:
        if tail[0] == 'fused':
            return tail[2](tail[3])
        return default([])
    return result
# --8<-- end appeal command set --8<--


# --8<-- start appeal option protocol --8<--
# --8<-- requires appeal convert --8<--
# --8<-- requires appeal exceptions --8<--
class Option:
    """
    Subclass to define an option with custom behavior.  The
    protocol (v1's, kept):

      * init(default) -- called once, with the parameter's default;
      * option(...)   -- called once per occurrence, in
        command-line order; its signature defines the option's
        operands (each parameter one operand, converted per its
        annotation);
      * render()      -- the final value passed to the command.

    An Option may be given any number of times (ruled 2026-07-09:
    repetition is the norm--getopt, argparse, click; subclass
    StrictOption to declare "at most once").  If the option is
    never given, the class is never instantiated: the parameter's
    default passes through untouched.
    """
    def init(self, default):
        pass

    def option(self):
        raise NotImplementedError

    def render(self):
        raise NotImplementedError


# v1's name for a repeatable Option--which is now every Option
MultiOption = Option


class StrictOption(Option):
    """
    An Option that may be given AT MOST ONCE: a second occurrence
    is "specified more than once", loudly.  (v1 called this
    Option; strictness is opt-in now, so it gets the louder name.)
    """


def _foreign_option(annotation, protocol):
    # A standalone script imports the user's module; if that module
    # itself imports appeal (it must, to subclass Option), the
    # process holds TWO copies of the protocol classes--the script's
    # and appeal's--and issubclass against ours says no.  Recognize
    # the foreign base by name and home.
    return any(c.__name__ == protocol
               and c.__module__.endswith('appeal.runtime')
               for c in annotation.__mro__)


def is_option(annotation):
    "Is this annotation an Option subclass?  (Either copy of Option.)"
    if not isinstance(annotation, type):
        return False
    return (issubclass(annotation, Option)
            or _foreign_option(annotation, 'Option'))


def is_strict_option(annotation):
    if not isinstance(annotation, type):
        return False
    return (issubclass(annotation, StrictOption)
            or _foreign_option(annotation, 'StrictOption'))


def is_multioption(annotation):
    "A repeatable option class: any Option that isn't strict."
    return is_option(annotation) and not is_strict_option(annotation)


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
    return instance.render()
# --8<-- end appeal option protocol --8<--


# --8<-- start appeal windows --8<--
# --8<-- requires appeal exceptions --8<--
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
            elif kind in ('flag', 'nullary', 'value', 'group'):
                if key in given:
                    del given[key]      # last one wins, per instance
                given[key] = value
            else:
                given.setdefault(key, []).append(value)
    return givens
# --8<-- end appeal windows --8<--


# --8<-- start appeal call converter --8<--
# --8<-- requires appeal convert --8<--
# --8<-- requires appeal exceptions --8<--
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
# --8<-- end appeal call converter --8<--


# --8<-- start appeal collect list --8<--
# --8<-- requires appeal convert --8<--
def accumulate(converter, values, name, usage=None):
    """
    The collector behind list[T] options: convert each collected
    occurrence, in order.
    """
    return [convert(converter, value, name, usage) for value in values]
# --8<-- end appeal collect list --8<--


# --8<-- start appeal collect mapping --8<--
# --8<-- requires appeal convert --8<--
# --8<-- requires appeal exceptions --8<--
def collect_mapping(key_converter, value_converter, values, name, usage=None):
    """
    The collector behind dict[K, V] options: each occurrence is
    KEY=VALUE; convert both halves.
    """
    result = {}
    for text in values:
        key_text, equals, value_text = text.partition('=')
        if not equals:
            raise UsageError(
                f"invalid value for {name!r}: {text!r} (expected KEY=VALUE)",
                usage, param=name)
        key = convert(key_converter, key_text, name, usage)
        if key in result:
            raise UsageError(
                f"{name}: key {key_text!r} defined more than once", usage)
        result[key] = convert(value_converter, value_text, name, usage)
    return result
# --8<-- end appeal collect mapping --8<--


# --8<-- start appeal check count --8<--
# --8<-- requires appeal exceptions --8<--
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
# --8<-- end appeal check count --8<--


# --8<-- start appeal run main --8<--
# --8<-- requires appeal theme --8<--
# --8<-- requires appeal complete --8<--
# --8<-- requires appeal exceptions --8<--
def run_main(parse, args=None, theme=None, completion=None,
             errors=None, version=None):
    """
    The main() driver for a generated parser: parse and execute,
    print errors the polite way, return the exit code.  theme (a
    spec: None, False, a Theme, or a baked dict) paints the
    'error:' prefix when the error stream wants color; the
    environment always wins (resolve_theme).  completion, if
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
        active = resolve_theme(theme, error_stream())
        if active is None:
            return 'error:'
        return active.paint('error', 'error:')

    try:
        result = parse(list(args))
    except SystemExit as e:
        # the precommand exits (program metadata: -V, ...);
        # main()'s contract is to RETURN the exit code
        code = e.code
        return code if isinstance(code, int) else (0 if code is None
                                                   else 1)
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
            print(f"usage: {e.usage}", file=error_stream())
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
    except Exception as e:
        # the installed appeal's exceptions, raised by the user's
        # module inside a standalone script
        kind = foreign_appeal_error(e)
        if kind == 'data':
            print(f"{error_prefix()} {e}", file=error_stream())
            usage = getattr(e, 'usage', None)
            if usage:
                print(f"usage: {usage}", file=error_stream())
            return 2
        if kind == 'command':
            print(f"{error_prefix()} {e}", file=error_stream())
            code = getattr(e, 'exit_code', 1)
            return code if isinstance(code, int) else 1
        if kind == 'error':
            print(f"{error_prefix()} {e}", file=error_stream())
            return 1
        raise               # not appeal's (or a config bug): traceback
    if result is None:
        return 0
    if isinstance(result, int):
        return result
    return 0
# --8<-- end appeal run main --8<--


##
## word wrapping, borrowed out of big
##
## The help renderers below lean on big's word-wrap trio.  These
## two snippets are big's--copied verbatim out of big/big/text.py
## between the scissors markers, re-synced by tools/sync_snippets.py.
## The one authoritative copy of this code is big's.
##


# --8<-- start big license --8<--
_big_license = """
big
Copyright 2022-2026 Larry Hastings
All rights reserved.

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR
THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
# --8<-- end big license --8<--

# --8<-- start big word wrap trio imports --8<--
import enum
import operator
import sys
# --8<-- end big word wrap trio imports --8<--

# --8<-- start big _iterate_over_bytes --8<--
# --8<-- requires big license --8<--

def _iterate_over_bytes(b):
    # this may not actually iterate over bytes.
    # for example, we iterate over apostrophes and double_quotes
    # for gently_title, and those might be strings or bytes,
    # or iterables of strings or bytes.
    if isinstance(b, bytes):
        return (b[i:i+1] for i in range(len(b)))
    return iter(b)

# --8<-- end big _iterate_over_bytes --8<--

# --8<-- start big toy_multisplit --8<--
# --8<-- requires big license --8<--

def _toy_multisplit_as_pairs(segments, empty):
    # segments alternates non-separator and separator strings,
    # always starting and ending with a (possibly empty)
    # non-separator string.  pair each non-separator string
    # with its subsequent separator--appending the always-empty
    # trailing separator--to make the keep=True 2-tuple form.
    segments.append(empty)
    return list(zip(segments[::2], segments[1::2]))


def toy_multisplit(s, separators):
    """
    A toy version of multisplit.

    s should be str or bytes.  separators should be a list or
    tuple of str (or bytes, matching s); if separators is itself
    a single str or bytes, every character (or byte) in it is a
    separator.  separators must be non-empty and must not contain
    the empty string.

    Returns a list of 2-tuples of

        (string, separator)

    where string is a (possibly empty) substring of s containing
    no separators, and separator is the separator that followed
    it.  The final 2-tuple's separator is always the empty string.
    Splitting is greedy: at each position, the longest matching
    separator wins.  The result is identical to

        list(multisplit(s, separators, keep=True, separate=True))

    Why use this instead of multisplit?  It has no startup time.
    It's also available as a snippet.
    """
    if not isinstance(separators, (list, tuple)):
        separators = [separators[i:i+1] for i in range(len(separators))]
    # assert separators
    if isinstance(s, bytes):
        empty = b''
    else:
        empty = ''
    # assert empty not in separators

    # special-cased only one separator,
    # for PEDAL TO THE MEDAL HYPER-SPEED
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
            index2 = index + length
            s = s[index2:]
        if s is not None:
            segments.append(s)
        return _toy_multisplit_as_pairs(segments, empty)

    # separators_by_length is a list of tuples:
    #    (length, bucket_of_separators_of_that_length)
    #
    # we add a bucket for every length, including 0.
    # (makes the algorithm easier.)
    longest_separator = max([len(sep) for sep in separators])
    separators_by_length = []
    for i in range(longest_separator, -1, -1):
        separators_by_length.append((i, set()))

    # store each separator in the correct bucket,
    # for separators of that length.
    for sep in separators:
        separators_by_length[longest_separator - len(sep)][1].add(sep)

    # strip out empty buckets.
    # there may not be any separators in every length bucket.
    # for example, if your separators are
    #     ['X', 'Y', 'ABC', 'XYZ', ]
    # then you don't have any separators of length 2.
    # (also, we should never have any separators of length 0).
    s2 = [t for t in separators_by_length if t[1]]
    separators_by_length = s2

    # confirm: we shouldn't have any separators of length 0.
    # separators_by_length is sorted, with buckets containing
    # longer separators appearing earlier.  so the bucket with
    # the shortest separators is last.  the length of those
    # separators should be > 0.

    # assert separators_by_length[-1][0]

    segments = []
    word = []

    def flush_word():
        if not word:
            segments.append(empty)
            return
        segments.append(empty.join(word))
        word.clear()

    longest_separator_length = separators_by_length[0][0]
    while s:
        substring = s
        for length, separators_set in separators_by_length:
            substring = substring[:length]
            # print(f"substring={substring!r} separators_set={separators_set!r}")
            if substring in separators_set:
                flush_word()
                segments.append(substring)
                s = s[length:]
                break
        else:
            # slice on a bytes object gives you back a bytes object.
            # s[0] on a bytes object gives you back an int.
            word.append(s[:1])
            s = s[1:]
    flush_word()

    return _toy_multisplit_as_pairs(segments, empty)

# --8<-- end big toy_multisplit --8<--


# --8<-- start big linebreaks --8<--
# --8<-- requires big license --8<--
str_linebreaks = (
    # char    decimal   hex      identity
    ##########################################
    '\n'    , #   10 - 0x000a - linebreak
    '\v'    , #   11 - 0x000b - vertical tab
    '\f'    , #   12 - 0x000c - form feed
    '\r'    , #   13 - 0x000d - carriage return
    '\r\n'  , # bonus! the classic DOS linebreak sequence!
    '\x1c'  , #   28 - 0x001c - file separator
    '\x1d'  , #   29 - 0x001d - group separator
    '\x1e'  , #   30 - 0x001e - record separator
    '\x85'  , #  133 - 0x0085 - next line
    '\u2028', # 8232 - 0x2028 - line separator
    '\u2029', # 8233 - 0x2029 - paragraph separator

    # What about '\n\r'?
    # Sorry, Acorn and RISC OS users, you'll have to add this yourselves.
    # I'm worried it would cause bugs with a malformed DOS string,
    # or maybe when operating in reverse mode.
    #
    # Also: welcome to big, Acorn and RISC OS users!
    # What are you doing here?  You can't run Python 3.6+!
    )
str_linebreaks_without_crlf = tuple(s for s in str_linebreaks if s != '\r\n')

linebreaks = str_linebreaks
linebreaks_without_crlf = str_linebreaks_without_crlf

# Whitespace as defined by Unicode.  The same as Python's definition,
# except we again remove the four ASCII separator characters.
unicode_linebreaks = tuple(s for s in str_linebreaks if not ('\x1c' <= s <= '\x1f'))
unicode_linebreaks_without_crlf = tuple(s for s in unicode_linebreaks if s != '\r\n')

# Linebreaks as defined by ASCII.  The same as Unicode,
# but only within the first 128 code points.
# Note: these are still *str* objects.
ascii_linebreaks = tuple(s for s in unicode_linebreaks if s < '\x80')
ascii_linebreaks_without_crlf = tuple(s for s in ascii_linebreaks if s != '\r\n')

# Whitespace as defined by the Python bytes object.
# Bytes objects, using the ASCII encoding.
#
# Notice that bytes_linebreaks doesn't contain \v or \f.
# That's because the Python bytes object doesn't consider those
# to be linebreak characters.
#
#    >>> define is_linebreak_str(c): return len( ("a"+c+"x").splitlines() ) > 1
#    >>> is_linebreak_str('\n')
#    True
#    >>> is_linebreak_str('\r')
#    True
#    >>> is_linebreak_str('\f')
#    True
#    >>> is_linebreak_str('\v')
#    True
#
#    >>> define is_linebreak_byte(c): return len( (b"a"+c+b"x").splitlines() ) > 1
#    >>> is_linebreak_byte(b'\n')
#    True
#    >>> is_linebreak_byte(b'\r')
#    True
#    >>> is_linebreak_byte(b'\f')
#    False
#    >>> is_linebreak_byte(b'\v')
#    False
#
# However! with defensive programming, in case this changes in the future
# (as it should!), big will automatically still agree with Python.
#
# p.s. in the above code examples, you have to put characters around the
# linebreak character, because str.splitlines (and bytes.splitlines)
# rstrips the string of linebreak characters before it splits lines, sigh.

bytes_linebreaks = (
    b'\n'    , #   10 0x000a - linebreak
    )

if len(b'x\vx'.splitlines()) == 2: # pragma: nocover
    bytes_linebreaks += (
        b'\v'    , #   11 - 0x000b - vertical tab
        )

if len(b'x\fx'.splitlines()) == 2: # pragma: nocover
    bytes_linebreaks += (
        b'\f'    , #   12 - 0x000c - form feed
        )

bytes_linebreaks += (
    b'\r'    , #   13 0x000d - carriage return
    b'\r\n'  , # bonus! the classic DOS linebreak sequence!
    )

bytes_linebreaks_without_crlf = tuple(s for s in bytes_linebreaks if s != b'\r\n')
# --8<-- end big linebreaks --8<--

# --8<-- start big word wrap trio --8<--
# --8<-- requires big license --8<--
# --8<-- requires big word wrap trio imports --8<--
# --8<-- requires big _iterate_over_bytes --8<--
# --8<-- requires big linebreaks --8<--

def _expand_tabs(s, column, tab_width, tab, space, first_column=1):
    """
    Expands the tabs in s to spaces and returns the result.
    s is assumed to be a single line, starting at 'column';
    linebreak characters don't reset the column here (that's
    expand_tabs's job).

    Tab stops sit every tab_width columns, counted from
    first_column: with a first_column of 1 and the default
    tab_width of 8, a tab advances to column 9, 17, 25, ...
    (This is the same arithmetic big.string uses for its
    column numbers.)

    tab and space supply the correct type of those two characters
    ('\\t' and ' ', or b'\\t' and b' ').
    """
    segments = s.split(tab)
    if len(segments) == 1:
        return s
    result = []
    append = result.append
    for i, segment in enumerate(segments):
        if i:
            delta = tab_width - ((column - first_column) % tab_width)
            append(space * delta)
            column += delta
        append(segment)
        column += len(segment)
    return s[:0].join(result)


def expand_tabs(s, *, column=1, first_column=1, tab_width=8):
    """
    Expands the tabs in s to spaces and returns the result.
    If s contains no tabs, returns s unchanged.

    s may be str or bytes, and may contain multiple lines.

    'column' is the column of the first character of s.
    'first_column' is the column the count resets to after a
    linebreak--the column your lines start at.  (These follow
    big.string, which uses column_number and first_column_number
    the same way.)  column may not be less than first_column,
    and first_column may not be negative.

    Tab stops sit every tab_width columns, counted from
    first_column: with the default first_column of 1 and the
    default tab_width of 8, a tab advances to column 9, 17, 25...
    This too matches big.string's arithmetic.  A tab's width
    depends on the column where it lands, so if s is going to be
    placed anywhere other than the left edge of the page,
    expanding its tabs correctly requires knowing where it
    starts.

    Lines are separated as str.splitlines splits them
    (for bytes, bytes.splitlines); the linebreak characters
    are preserved in the result.
    """
    if (not isinstance(first_column, int)) or (first_column < 0):
        raise ValueError(f"first_column must be a non-negative int, not {first_column!r}")
    if (not isinstance(column, int)) or (column < first_column):
        raise ValueError(f"column must be an int >= first_column ({first_column}), not {column!r}")
    if isinstance(s, bytes):
        tab = b'\t'
        space = b' '
    else:
        tab = '\t'
        space = ' '
    if tab not in s:
        return s
    result = []
    append = result.append
    for line in s.splitlines(keepends=True):
        append(_expand_tabs(line, column, tab_width, tab, space, first_column))
        column = first_column
    return s[:0].join(result)


def _normalize_indents(indent, name, margin, tab_width, left_column, indent_type, measure=len):
    """
    Validates, normalizes, and measures an "indent" argument for wrap_words.

    indent is an indent argument to wrap_words (either indent or code_indent).
    name is a string identifying which indent argument this is (used for
      error messages).
    margin is the word wrap margin--we confirm the longest indent fits.
    tab_width and left_column govern tab expansion: tabs in an indent
      are expanded to spaces, at the indent's true position (an indent
      always starts its line, at left_column).
    indent_type is the type (str or bytes) indent should be.

    Returns a 2-tuple:
        (indents, columns)
    indents is a tuple of strings: indent, normalized as a tuple,
        with any tabs expanded.
    columns is a list of ints, the same length as indents,
        with the measured width of each one.
    """
    if isinstance(indent, indent_type):
        indents = (indent,)
    elif isinstance(indent, (list, tuple)):
        indents = indent
        for i in indents:
            if not isinstance(i, indent_type):
                raise TypeError(f"{name} must be {indent_type.__name__}, or a list or tuple of {indent_type.__name__}, not {i!r}")
    else:
        raise TypeError(f"{name} must be {indent_type.__name__}, or a list or tuple of {indent_type.__name__}, not {indent!r}")

    # forbidden is a membership-testable object containing
    # characters forbidden to be in the indent--linebreaks.
    # for str, forbidden is a set, as that's cheapest.
    # for bytes it's the joined bytes object--iterating a bytes yields ints, and
    # int-in-bytes membership tests the byte value
    if indent_type is bytes:
        indent_iter = _iterate_over_bytes
        forbidden = set(bytes_linebreaks)
        tab = b'\t'
        space = b' '
    else:
        indent_iter = iter
        forbidden = set(linebreaks)
        tab = '\t'
        space = ' '

    expanded = []
    columns = []
    append = columns.append
    for i in indents:
        characters = set(indent_iter(i))
        intersection = characters & forbidden
        if intersection:
            raise ValueError(f"{name} {i!r} contains linebreak characters {intersection!r}")

        i = _expand_tabs(i, left_column, tab_width, tab, space)
        expanded.append(i)
        column = measure(i)
        if column >= margin:
            raise ValueError(f"{name} {i!r} leaves no room for words inside margin {margin}")
        append(column)

    return tuple(expanded), columns


# the gap between a definition-list term and its details, the details
# indent when a list goes tall, and the narrowest details ribbon worth
# wrapping into.  See deflist.layout.proposal.md.
_DEFLIST_GAP = 2
_DEFLIST_TALL_INDENT = 4
_DEFLIST_MIN_RIBBON = 20


def _deflist_column(term_widths, margin):
    """
    Pick the detail column D for a definition list, and whether to
    render it tall.  term_widths are the terms' visible widths; margin
    is the width available to the list.  Returns (D, tall).

    Fit-everyone if every term clears a third of the width; else put D
    at the 80th-percentile term (the wider outliers take the per-entry
    fallback); give up to tall if D would eat past half the width or
    leave too thin a ribbon for the details.
    """
    longest = max(term_widths)
    if longest + _DEFLIST_GAP <= margin // 3:
        column = longest + _DEFLIST_GAP
    else:
        ordered = sorted(term_widths)
        rank = (4 * len(ordered) + 4) // 5          # ceil(0.8 * n), nearest-rank
        p80 = ordered[min(rank, len(ordered)) - 1]
        column = p80 + _DEFLIST_GAP
    tall = (column > margin // 2) or ((margin - column) < _DEFLIST_MIN_RIBBON)
    return column, tall


def wrap_words(words, margin=79, *, code_indent=None, indent='', left_column=1, tab_width=8, two_spaces=True, raw=None):
    """
    Combines 'words' into lines and returns the result as a string.
    Similar to textwrap.wrap.

    'words' should be an iterator yielding str or bytes strings, and
    these strings should already be split at word boundaries.
    Here's an example of a valid argument for 'words':
        ["this", "is", "an", "example", "of",
         "text", "split", "at", "word", "boundaries"]

    A single '\n' indicates a line break.
    A double '\n\n' indicates a paragraph break.
    Two line breaks in a row ('\n', '\n') doesn't count as
    a paragraph break ('\n\n').
    A single '\t' indicates a tab: the next word is placed at the
    next tab stop, as governed by tab_width and left_column.  A tab
    renders as spaces--wrap_words is the final rendering, and it
    knows what column everything lands at, so its output contains
    no tabs.  No space is added around a tab (the tab IS the
    separation); consecutive tabs advance consecutive stops; if
    the word after a tab doesn't fit on the line, the word wraps
    and the tab dies with the line, just like a space would.
    Any other whitespace-only strings are unsupported, and if
    you pass in a "words" array to wrap_words containing one,
    its behavior is undefined.

    An element of 'words' may also be a tuple, which is an
    instruction rather than a word.  Its first item names the
    instruction:
        ('indent', first, ..., last)
            Switch the active indent context, using the same rules
            as the 'indent' parameter (see below): 'first' prefixes
            the next line, 'last' prefixes every line after the ones
            named.  It resets the line counter, so the next line is
            always a "first line".  (Passing indent=X behaves as if
            ('indent', ...) built from X were prepended to 'words'.)
        ('fill margin',   initial, fill, trailing)
        ('fill previous', initial, fill, trailing)
        ('fill next',     initial, fill, trailing)
            Draw a whole line by filling to a width: the margin, the
            previous line's width, or the next line's width.  The
            line is 'initial', then 'fill' repeated and clipped to
            the remaining width, then 'trailing', all inside the
            active indent.  'initial' and 'trailing' are never
            clipped.  This draws rules and boxes; e.g. a heading
            underlined to its own width is a 'fill previous'.

    Implicitly supports "code lines" as defined by split_text_with_code.
    (A "code line" just shows up in words as one unbroken word surrounded
    by line breaks.)  Tabs inside a code line are expanded to spaces
    when the line is rendered, at the column where they actually land.

    'margin' specifies the maximum length of each line. The length of
    every line will be less than or equal to 'margin', unless the length
    of an individual element inside 'words' is greater than 'margin'.

    'left_column' is the 1-based "virtual left column": the column
    your output will start at, if you're going to place it somewhere
    other than the left edge of the page.  It only affects how tabs
    are rendered--tab stops live at fixed columns of the page
    (9, 17, 25, ... with the default tab_width of 8), so text that
    starts at column 5 reaches its first tab stop after four
    characters, not eight.  'margin' is unaffected: it's the width
    of the block wrap_words produces, wherever you put it.

    If 'two_spaces' is true, elements from 'words' that end in
    sentence-ending punctuation ('.', '?', and '!') will be followed
    by two spaces, not one.

    'raw' is an optional callback mapping a word to its PLAIN TEXT,
    for callers whose words carry in-band styling markup (so the
    word emitted is wider than the text the reader sees).  It
    defaults to the identity.  Every place wrap_words INSPECTS a
    word -- its width for the margin, its trailing punctuation for
    two_spaces, and the whitespace/first-character tests that detect
    line breaks and code lines -- runs on raw(word), while the
    ORIGINAL word (markup and all) is what gets emitted.  wrap_words
    never slices a word, so a plain-text view is all it needs.

    'indent' is a value used to prefix every line in the wrapped
    paragraphs.  It may be a single string, in which case every line
    gets prefixed with 'indent'.  It may also be a list or tuple of
    strings, in which case the first line of a paragraph gets indent[0],
    the second indent[1], etc; once we run out, we use indent[-1] for
    all subsequent lines in the paragraph.  The line counter resets
    every time we start a new paragraph.  Blank lines separating
    paragraphs never get indented.  'indent' strings may not contain
    linebreak characters.

    'code_indent' is an indent used only for paragraphs made up of
    code lines.  If code_indent is None, code lines and normal lines
    both use the 'indent' prefix string(s).  Otherwise, code_indent
    accepts the same sorts of values as 'indent', and uses them the
    same way--but 'code_indent' only applies to paragraphs made up
    of code lines.  ('indent' will still be used for paragraphs made
    up of text.)

    An indent string reduces the space available for the paragraph;
    if your margin is 70, and your indent string is 5 characters,
    you effectively have a margin of only 65.  Tabs inside an indent
    are expanded to spaces at the indent's true position (an indent
    always starts its line, at left_column).  If the length of your
    indent is equal to or greater than your margin, wrap_words raises
    ValueError.

    Elements in 'words' are not modified--except for tab expansion;
    any leading or trailing whitespace will be preserved.  You can
    use this to preserve whitespace where necessary; this is the
    mechanism used to preserve code lines.

    The objects yielded by words can be a subclass of either
    str or bytes, though wrap_words will only return str or bytes.
    All the objects yielded by words must have the same base class
    (str or bytes).  If they're bytes, indent and code_indent must
    be bytes too (or lists or tuples of bytes).

    If 'words' is empty, raises ValueError.
    (Note that split_text_with_code('') returns [''].)
    """
    if (not isinstance(left_column, int)) or (left_column < 1):
        raise ValueError(f"left_column must be a positive int, not {left_column!r}")

    # words may carry in-band styling markup; every INSPECTION below
    # runs on raw(word) -- its plain text -- while the original word
    # is what we emit.  Default: the word is its own plain text.
    _raw = (lambda w: w) if raw is None else raw

    # words (and indent prefixes) may carry in-band markup; _measure
    # is a word's VISIBLE width, past the markup.
    _measure = lambda s: len(_raw(s))

    words = iter(words)
    col = 0
    empty = None
    lastword = None
    text = []
    append = text.append
    first_word = True

    indents = widths = code_indents = None
    last_indent = 0
    line_number = 0
    pending_tabs = 0

    new_paragraph = True
    new_line = True
    code_paragraph = False

    # the visible width of the last completed output line (what a
    # 'fill previous' matches), and a 'fill next' still waiting for the
    # following line to be laid out so it can match ITS width.
    last_line_len = 0
    pending_next = None
    pending_next_armed = False

    def next_tab_stops(col, tabs):
        # the 0-based line offset of the next word, after
        # advancing 'tabs' tab stops from the offset 'col'.
        # tab stops live at fixed 1-based columns of the page:
        # 1 + (k * tab_width).
        absolute = left_column + col
        for _ in range(tabs):
            absolute += tab_width - ((absolute - 1) % tab_width)
        return absolute - left_column

    class _FillNext:
        # placeholder for a 'fill next' line: its width isn't known
        # until the following line is laid out, so we emit this and
        # back-patch its .value at join time.
        __slots__ = ('value',)
        def __init__(self):
            self.value = empty

    def fill_body(initial, fillchar, trailing, target, prefix_w):
        # a fill line's content, after its indent prefix: initial, then
        # fillchar repeated to the leftover VISIBLE width and clipped,
        # then trailing.  initial and trailing are never clipped.
        body = target - prefix_w - _measure(initial) - _measure(trailing)
        fill_width = _measure(fillchar) if fillchar else 0
        if (body > 0) and fill_width:
            filled = fillchar * (body // fill_width)
            remainder = body - (body // fill_width) * fill_width
            if remainder and (len(fillchar) == fill_width):
                # a plain fillchar can be sliced to finish the last
                # partial repeat; a styled one just under-fills.
                filled = filled + fillchar[:remainder]
        else:
            filled = empty
        return initial + filled + trailing

    def complete_line(line_len):
        # the current output line just ended, this wide: record it (for
        # 'fill previous'), and arm then resolve a deferred 'fill next'.
        nonlocal last_line_len, pending_next, pending_next_armed
        last_line_len = line_len
        if pending_next is not None:
            if not pending_next_armed:
                pending_next_armed = True    # that was the fill line's own line
            else:
                placeholder, prefix_w, initial, fillchar, trailing = pending_next
                placeholder.value = fill_body(initial, fillchar, trailing, line_len, prefix_w)
                pending_next = None
                pending_next_armed = False

    def render_stream(stream, width):
        # lay a sub-stream out to `width`, returning its lines; an empty
        # or content-free stream gives [].
        try:
            return wrap_words(stream, margin=max(1, width), raw=raw,
                              two_spaces=two_spaces, tab_width=tab_width).split(linebreak)
        except ValueError:
            return []

    def render_deflist(items, base_first, base_rest, base_w):
        # Split the collected list into entries, measure the terms, pick
        # the detail column, and lay each entry out -- compact (term and
        # its details share a line when the term fits) or tall (term on
        # its own line, details indented, a blank line between entries).
        # Details are laid out recursively, so nested lists / code / even
        # nested definition lists compose, indented to the detail column.
        # Returns lines already carrying the outer indent.
        entries = []
        term_words = None
        details = []
        for item in items:
            if (type(item) is tuple) and (item[0] == def_markers[2]):    # 'term'
                if term_words is not None:
                    entries.append((term_words, details))
                term_words = list(item[1:])
                details = []
            elif term_words is not None:
                details.append(item)
        if term_words is not None:
            entries.append((term_words, details))
        if not entries:
            return []

        inner_width = max(1, margin - base_w)

        terms = []
        measures = []
        for term_words, _ in entries:
            lines = render_stream(term_words, 10 ** 9) if term_words else []
            term_text = lines[0] if lines else empty
            terms.append(term_text)
            measures.append(_measure(term_text))

        column, tall = _deflist_column(measures, inner_width)
        details_width = inner_width - (_DEFLIST_TALL_INDENT if tall else column)
        full_pad = space1 * column
        tall_pad = space1 * _DEFLIST_TALL_INDENT

        inner_lines = []
        for index, (term_words, details) in enumerate(entries):
            term_text = terms[index]
            details_lines = render_stream(details, details_width)
            if tall:
                if index:
                    inner_lines.append(empty)              # blank between entries
                inner_lines.append(term_text)
                inner_lines.extend(tall_pad + line for line in details_lines)
            elif not details_lines:
                inner_lines.append(term_text)              # empty details: bare term
            elif (measures[index] + 1) <= column:          # term fits: share the line
                gap = space1 * (column - measures[index])
                inner_lines.append(term_text + gap + details_lines[0])
                inner_lines.extend(full_pad + line for line in details_lines[1:])
            else:                                          # term too wide: own line
                inner_lines.append(term_text)
                inner_lines.extend(full_pad + line for line in details_lines)

        return [(base_first if (index == 0) else base_rest) + line
                for index, line in enumerate(inner_lines)]

    for word in words:
        is_instruction = type(word) is tuple

        if first_word:
            first_word = False
            if is_instruction:
                sample = next((a for a in word[1:] if isinstance(a, (str, bytes))), '')
            else:
                sample = word
            if isinstance(sample, bytes):
                empty = lastword = b''
                sentence_ending_punctuation = (b'.', b'?', b'!')
                space1 = b' '
                space2 = b'  '
                linebreak = b'\n'
                tab = b'\t'
                indent_marker = b'indent'
                fill_markers = (b'fill margin', b'fill previous', b'fill next')
                def_markers = (b'def start', b'def end', b'term')
            else:
                empty = lastword = ''
                sentence_ending_punctuation = ('.', '?', '!')
                space1 = ' '
                space2 = '  '
                linebreak = '\n'
                tab = '\t'
                indent_marker = 'indent'
                fill_markers = ('fill margin', 'fill previous', 'fill next')
                def_markers = ('def start', 'def end', 'term')
            indent_type = bytes if isinstance(sample, bytes) else str
            if indent or (code_indent is not None):
                if indent:
                    indents, widths = _normalize_indents(
                        indent, 'indent', margin, tab_width, left_column, indent_type, _measure)
                else:
                    indents, widths = (empty,), (0,)
                last_indent = len(indents) - 1

                if code_indent is None:
                    code_indents = indents
                    code_widths = widths
                    last_code_indent = last_indent
                else:
                    code_indents, code_widths = _normalize_indents(
                        code_indent, 'code_indent', margin, tab_width, left_column, indent_type, _measure)
                    last_code_indent = len(code_indents) - 1

        if is_instruction:
            instruction = word[0]
            if instruction == indent_marker:
                # switch the active indent context mid-stream; the next
                # line starts fresh on the first-line prefix.
                indents, widths = _normalize_indents(
                    word[1:], 'indent', margin, tab_width, left_column, indent_type, _measure)
                last_indent = len(indents) - 1
                if code_indent is None:
                    # no separate code indent: code lines follow the
                    # active indent too (as they do at the front).
                    code_indents = indents
                    code_widths = widths
                    last_code_indent = last_indent
                line_number = 0
                new_line = True
                col = 0
                continue
            if instruction in fill_markers:
                initial, fillchar, trailing = word[1], word[2], word[3]
                if indents:
                    index = min(line_number, last_indent)
                    prefix = indents[index]
                    prefix_w = widths[index]
                else:
                    prefix = empty
                    prefix_w = 0
                append(prefix)
                if instruction == fill_markers[2]:               # fill next
                    placeholder = _FillNext()
                    append(placeholder)
                    pending_next = (placeholder, prefix_w, initial, fillchar, trailing)
                    pending_next_armed = False
                    col = 0
                else:
                    target = margin if (instruction == fill_markers[0]) else last_line_len
                    body = fill_body(initial, fillchar, trailing, target, prefix_w)
                    append(body)
                    col = prefix_w + _measure(body)
                lastword = empty
                new_line = False
                continue
            if instruction == def_markers[0]:                # 'def start'
                # collect the whole list (balancing nested lists), then
                # lay it out against the active indent.
                items = []
                depth = 1
                for item in words:
                    if type(item) is tuple:
                        if item[0] == def_markers[0]:
                            depth += 1
                        elif item[0] == def_markers[1]:      # 'def end'
                            depth -= 1
                            if depth == 0:
                                break
                    items.append(item)
                if indents:
                    base_first, base_rest, base_w = indents[0], indents[-1], widths[0]
                else:
                    base_first = base_rest = empty
                    base_w = 0
                lines = render_deflist(items, base_first, base_rest, base_w)
                for index, line in enumerate(lines):
                    if index:
                        append(linebreak)
                    append(line)
                col = _measure(lines[-1]) if lines else 0
                last_line_len = col
                lastword = empty
                new_line = False
                continue
            if instruction in (def_markers[1], def_markers[2]):
                continue                                     # stray 'def end'/'term'
            raise ValueError(f"unknown wrap_words instruction {instruction!r}")

        rawword = _raw(word)

        if rawword.isspace():
            if rawword == tab:
                # a tab word: not a line break, not a paragraph
                # break--column advancement, resolved when we
                # place the next word.
                pending_tabs += 1
                continue
            complete_line(col)
            lastword = word
            append(word)

            pending_tabs = 0
            new_line = True
            col = 0

            new_paragraph = len(rawword) > 1
            if not new_paragraph:
                line_number += 1
            continue

        if new_paragraph:
            new_paragraph = False
            code_paragraph = rawword[:1].isspace()
            line_number = 0
            lastword = empty

        if code_paragraph:
            pending_tabs = 0
            if code_indents:
                index = min(line_number, last_code_indent)
                append(code_indents[index])
                col = code_widths[index]
            else:
                col = 0
            if tab in word:
                # a code line's tabs expand at render time, at the
                # columns where they actually land.
                word = _expand_tabs(word, left_column + col, tab_width, tab, space1)
            append(word)
            continue

        # text paragraph
        l = len(rawword)
        if not l:
            continue

        tabs = pending_tabs
        pending_tabs = 0

        if not new_line:
            if tabs:
                # tab(s) glue this word to the line at a tab
                # stop--if it fits.  if it doesn't, the word
                # wraps, and the tabs die with the line, just
                # like a space would.
                target = next_tab_stops(col, tabs)
                if (target + l) > margin:
                    complete_line(col)
                    append(linebreak)
                    new_line = True
                    line_number += 1
                    tabs = 0
                else:
                    append(space1 * (target - col))
                    col = target
            elif col:
                if two_spaces and _raw(lastword).endswith(sentence_ending_punctuation):
                    space = space2
                    len_space = 2
                else:
                    space = space1
                    len_space = 1

                wrap = (col + len_space + l) > margin
                if wrap:
                    complete_line(col)
                    append(linebreak)
                    new_line = True
                    line_number += 1
                else:
                    append(space)
                    col += len_space

        if new_line:
            new_line = False
            if not indents:
                col = 0
            else:
                index = min(line_number, last_indent)
                prefix = indents[index]
                append(prefix)
                col = widths[index]
            if tabs:
                # tabs at the start of a line (only a hand-built
                # stream gets here): advance from the line's
                # start.  no wrap check--like an over-long word,
                # an over-margin stop just overflows.
                target = next_tab_stops(col, tabs)
                append(space1 * (target - col))
                col = target

        append(word)
        col += l                       # display width, not markup length
        lastword = word

    if first_word:
        raise ValueError("no words to wrap")

    # resolve any 'fill next' placeholders (an unresolved one -- a fill
    # next with no following line -- renders as its bare caps).
    s = empty.join(p.value if (type(p) is _FillNext) else p for p in text)
    return s


def split_text_with_code(s, *, code_indent=4, tab_width=8):
    """
    Splits the string s into individual words,
    suitable for feeding into wrap_words.

    s may be either str or bytes.

    Paragraphs indented by less than code_indent will be
    broken up into individual words.

    code_indent must be an int.  If it's nonzero, lines indented
    by at least code_indent columns are "code lines": paragraphs
    of them preserve their whitespace, internal and leading, and
    their linebreaks.  (This preserves the formatting of code
    examples, when these words are rejoined into lines by
    wrap_words.)  Code lines are emitted verbatim, tabs included;
    wrap_words expands their tabs at render time, when it knows
    what column the line lands at.  If code_indent is 0, there
    are no code lines: everything is just text.

    In text, a tab survives as its own '\\t' word: a run of
    whitespace containing k tabs becomes exactly k '\\t' words,
    in order--the rest of the whitespace is just separation,
    and is thrown away as usual.  wrap_words renders a '\\t'
    word by placing the next word at the next tab stop.

    s can be str, bytes, or a subclass of either, though
    split_text_with_code will only return str or bytes.

    The only whitespace-only words split_text_with_code will
    ever emit are '\\n' (line break), '\\n\\n' (paragraph break),
    and '\\t' (tab).

    split_text_with_code is inflexible about line endings;
    it only recognizes '\\n' (or b'\\n') as ending a line.

    if s is empty, returns a list containing an empty string.
    """

    if isinstance(code_indent, bool):
        # bool sneaks through the index protocol; "we must
        # remain strong."
        raise TypeError(f"code_indent must be an int, not {code_indent!r}")
    code_indent = operator.index(code_indent)
    if code_indent < 0:
        raise ValueError(f"code_indent must be non-negative, not {code_indent!r}")

    if isinstance(s, bytes):
        empty = b''
        tab = b'\t'
        linebreak = b'\n'
        paragraph_break = b'\n\n'
        iterate_over_characters = _iterate_over_bytes
    else:
        empty = ''
        tab = '\t'
        linebreak = '\n'
        paragraph_break = '\n\n'
        iterate_over_characters = iter

    linebreak_tuple = (linebreak,)
    code_paragraph = "code paragraph"
    text_paragraph = "text paragraph"

    # the kind of paragraph the previous nonblank line belonged to:
    # code_paragraph, text_paragraph, or None (haven't seen one yet).
    previous_paragraph = None

    # how many blank lines we've seen since the previous nonblank line.
    blank_lines = 0

    words = []

    # only '\n' ends a line.  every other whitespace character--
    # including '\r'--is just whitespace, width 1.
    for line in s.split(linebreak):
        line = line.rstrip()
        if not line:
            blank_lines += 1
            continue

        if code_indent:
            # measure the line's indent, expanding tabs.
            stripped = line.lstrip()
            col = 0
            len_indent = len(line) - len(stripped)
            for i, c in enumerate(iterate_over_characters(line)):
                if i == len_indent:
                    break
                if c == tab:
                    col += tab_width - (col % tab_width)
                else:
                    # space, and any unusual whitespace
                    # (\r, \v, \f, nbsp...), counts as width 1.
                    col += 1

            if col >= code_indent:
                # it's a code line.
                if previous_paragraph is text_paragraph:
                    words.append(paragraph_break)
                elif previous_paragraph is code_paragraph:
                    # in a code paragraph, linebreaks are just that--
                    # line breaks.  if you want to finish the current
                    # code line, emit a line break.  if you want an
                    # empty line in the middle of a code paragraph,
                    # emit two linebreaks in a row.  (if you want two
                    # empty lines, emit three linebreaks.)
                    #
                    # as a rule, code paragraphs start with a code line,
                    # then have one or more line breaks before the next
                    # code line.  you never have two code lines in a row.
                    # the paragraph break that ends a code paragraph can
                    # come after either a code line or a linebreak.
                    words.extend(linebreak_tuple * (blank_lines + 1))

                # reproduce the line as a single word, verbatim--
                # tabs included.  wrap_words expands them at render
                # time, when it knows what column the line lands at.
                words.append(empty + line)
                previous_paragraph = code_paragraph
                blank_lines = 0
                continue

        # not a code line.
        if ((previous_paragraph is code_paragraph)
            or ((previous_paragraph is text_paragraph) and blank_lines)):
            words.append(paragraph_break)
        # split the line into words.  each tab survives as its own
        # '\t' word, in order: a run of whitespace containing k tabs
        # becomes exactly k '\t' words.
        first_segment = True
        for segment in line.split(tab):
            if not first_segment:
                words.append(tab)
            first_segment = False
            words.extend(segment.split())
        previous_paragraph = text_paragraph
        blank_lines = 0

    if not words:
        words.append(empty)
    return words


class OverflowStrategy(enum.Enum):
    """
    Enum providing constants to specify how merge_columns
    handles overflow in columns.
    """
    INVALID = enum.auto()
    RAISE = enum.auto()
    INTRUDE_ALL = enum.auto()
    # INTRUDE_MINIMUM = enum.auto()  # not implemented yet
    DELAY_ALL = enum.auto()
    # DELAY_MINIMUM = enum.auto()  # not implemented yet

def merge_columns(*columns, column_separator=None,
    overflow_strategy=OverflowStrategy.RAISE,
    overflow_before=0,
    overflow_after=0,
    tab_width=8,
    raw=None,
    ):
    """
    Merge n column tuples, with each column tuple being
    formatted into its own column in the resulting string.
    Returns a string.

    columns should be an iterable of column tuples.
    Each column tuple should contain three items:
        (text, min_width, max_width)
    text should be a single string, either str or bytes,
    with linebreak characters separating lines. min_width
    and max_width are the minimum and maximum permissible
    widths for that column, not including the column
    separator (if any).

    A column tuple may carry an optional fourth member,
    relative_tabs, governing how tabs in that column's text are
    expanded (they're always expanded to spaces, using tab_width).
    If true (the default), each line's tabs expand in the column's
    own coordinates--as if the line started at column 1--and the
    expanded text shifts rigidly into place, so the column's
    internal alignment survives wherever the column lands.  If
    false, tabs expand at the column's position on the page (its
    nominal position: an overflow strategy that shifts lines
    doesn't move their tab stops).

    Note that this function doesn't text-wrap the lines.

    column_separator is printed between every column.

    overflow_strategy tells merge_columns how to handle a column
    with one or more lines that are wider than that column's max_width.
    The supported values are:

        OverflowStrategy.RAISE

            Raise an OverflowError.  The default: overflow is
            an error, and it shouldn't pass silently unless you
            explicitly silence it by picking another strategy.

        OverflowStrategy.INTRUDE_ALL

           Intrude into all subsequent columns on all lines
           where the overflowed column is wider than its max_width.

        OverflowStrategy.DELAY_ALL

           Delay all columns after the overflowed column,
           not beginning any until after the last overflowed line
           in the overflowed column.  (Help-style tables with an
           occasional overlong label usually want this one.)

    When overflow_strategy is INTRUDE_ALL or DELAY_ALL, and
    either overflow_before or overflow_after is nonzero, these
    specify the number of extra lines before or after
    the overflowed lines in a column.

    text and column_separator can be str, bytes, or a subclass
    of either, though merge_columns will only return str or bytes.
    All these objects (text and column_separator) must have the
    same baseclass, str or bytes.

    'raw' is an optional callback mapping a line to its PLAIN TEXT,
    for columns whose lines carry in-band styling markup.  It
    defaults to the identity.  Line WIDTHS -- for overflow detection
    and for padding a column to its max_width -- are measured on
    raw(line), while the original line (markup and all) is emitted.
    (Tab expansion still counts a line's literal characters, so
    don't mix tabs and markup within a single column's line.)
    """
    # real raises, not asserts: these guard user input, and
    # asserts vanish under python -O.  (OverflowStrategy.INVALID
    # used to silently behave as INTRUDE_ALL under -O!)
    if overflow_strategy not in (OverflowStrategy.INTRUDE_ALL, OverflowStrategy.DELAY_ALL, OverflowStrategy.RAISE):
        raise ValueError(f"invalid overflow_strategy {overflow_strategy!r}")
    raise_overflow_error = overflow_strategy == OverflowStrategy.RAISE
    delay_all = overflow_strategy == OverflowStrategy.DELAY_ALL

    if not columns:
        raise ValueError("no columns")
    is_bytes = isinstance(columns[0][0], bytes)

    if is_bytes:
        empty = b''
        space = b' '
        linebreak = b'\n'
        tab = b'\t'
    else:
        empty = ''
        space = ' '
        linebreak = '\n'
        tab = '\t'

    if column_separator is None:
        column_separator = space

    # lines may carry in-band styling markup; widths (overflow and
    # padding) are measured on raw(line), the plain text, while the
    # original line is emitted.  Default: a line is its own plain text.
    _raw = (lambda w: w) if raw is None else raw

    _columns = columns
    columns = []
    empty_columns = []
    last_too_wide_lines = []
    max_lines = -1

    column_spacing = len(column_separator)

    overflows = []
    in_overflow = False
    def add_overflow():
        nonlocal in_overflow
        in_overflow = False
        if overflows:
            last_overflow = overflows[-1]
            if last_overflow[1] >= (overflow_start - 1):
                overflows.pop()
                overflows.append((last_overflow[0], overflow_end))
                return
        overflows.append((overflow_start, overflow_end))

    overflow_start = overflow_end = None
    def next_overflow():
        nonlocal overflow_start
        nonlocal overflow_end
        if overflows:
            overflow_start, overflow_end = overflows.pop()
        else:
            overflow_start = overflow_end = sys.maxsize

    # the 1-based column each column starts at, nominally
    # (as if nothing overflowed)--the phase for expanding
    # tabs with relative_tabs=False.
    nominal_left = 1

    for column_number, column in enumerate(_columns):
        s, min_width, max_width = column[:3]
        relative_tabs = column[3] if len(column) > 3 else True

        # check types, let them raise exceptions as needed
        operator.index(min_width)
        operator.index(max_width)

        empty_columns.append(max_width * space)

        if isinstance(s, (str, bytes)):
            lines = s.rstrip().split(linebreak)
        else:
            lines = s
        max_lines = max(max_lines, len(lines))

        tab_phase = 1 if relative_tabs else nominal_left
        nominal_left += max_width + column_spacing

        # loop 1:
        # measure each line length, determining
        #  * maximum line length, and
        #  * all overflow lines
        rstripped_lines = []
        overflows = []
        max_line_length = -1
        in_overflow = False

        for line_number, line in enumerate(lines):
            if tab in line:
                line = _expand_tabs(line, tab_phase, tab_width, tab, space)
            line = line.rstrip()
            assert not linebreak in line
            rstripped_lines.append(line)

            length = len(_raw(line))
            max_line_length = max(max_line_length, length)

            line_overflowed = length > max_width
            if (not in_overflow) and line_overflowed:
                # starting new overflow
                if raise_overflow_error:
                    raise OverflowError(f"overflow in column {column_number}: {line!r} is {length} characters, column max_width is {max_width}")
                overflow_start = max(line_number - overflow_before, 0)
                in_overflow = True
            elif in_overflow and (not line_overflowed):
                # ending current overflow
                overflow_end = line_number - 1 + overflow_after
                add_overflow()

        if in_overflow:
            overflow_end = line_number + overflow_after
            add_overflow()
            for i in range(overflow_after):
                rstripped_lines.append(empty)

        # loop 2 must consume the rstripped lines--both so that
        # per-line trailing whitespace can't fool the padding math,
        # and so the overflow_after padding lines appended above
        # actually make it into the output.
        lines = rstripped_lines

        if delay_all and overflows:
            overflows.clear()
            overflows.append((0, overflow_end))

        # loop 2:
        # compute padded lines and in_overflow for every line
        padded_lines = []
        overflows.reverse()
        overflow_start = overflow_end = None

        in_overflow = False
        next_overflow()
        for line_number, line in enumerate(lines):
            if line_number > overflow_end:
                in_overflow = False
                next_overflow()
            if line_number >= overflow_start:
                in_overflow = True
            if not in_overflow:
                # pad by DISPLAY width, so markup doesn't skew the column
                pad = max_width - len(_raw(line))
                if pad > 0:
                    line = line + (space * pad)
            padded_lines.append((line, in_overflow))

        columns.append(padded_lines)


    column_iterators = [iter(c) for c in columns]
    lines = []

    while True:
        line = []
        all_iterators_are_exhausted = True
        add_separator = False
        in_overflow = False
        for column_iterator, empty_column in zip(column_iterators, empty_columns):
            if add_separator:
                line.append(column_separator)
            else:
                add_separator = True

            try:
                column, in_overflow = next(column_iterator)
                all_iterators_are_exhausted = False
            except StopIteration:
                column = empty_column
            line.append(column)
            if in_overflow:
                break
        if all_iterators_are_exhausted:
            break
        line = empty.join(line).rstrip()
        lines.append(line)

    text = linebreak.join(lines)
    return text.rstrip()

# --8<-- end big word wrap trio --8<--


# --8<-- start big format_definition_list --8<--
# --8<-- requires big license --8<--
# --8<-- requires big word wrap trio --8<--

_default_definition_list_indent = '  '
_default_definition_list_spacer = '  '

def format_definition_list(pairs, margin=79, *,
        definition_left_column=None,
        definition_relative_tabs=True,
        indent=_default_definition_list_indent,
        spacer=_default_definition_list_spacer,
        tab_width=8,
        term_relative_tabs=True):
    """
    Formats a "definition list" and returns it as a string:
    terms on the left, definitions on the right, definitions
    wrapped to fit and aligned in a column.  Sample output:

        -v, --verbose  Print more output.  Repeat
                       for even more.
        --color <red|green|blue>
                       Sets the output color.

    'pairs' is an iterable of (term, definition) pairs of strings.
    Terms are never wrapped, and may not contain linebreak
    characters.  Definitions are text: each one is split with
    split_text_with_code and wrapped with wrap_words, so paragraph
    breaks, code lines, and tabs work as they do there.
    A pair with an empty definition is just the term on a line
    by itself.

    'margin' is the target width, as per wrap_words.

    'indent' is a string prefixed to every line.  Counts towards
    the margin (if your margin is 70, and your indent is 5 characters,
    your "effective margin" is 65).

    'spacer' is the fill material between a term and its definition,
    in the manner of TeX's leaders: conceptually the spacer repeats,
    phase-locked to the start of the term column, from the end of
    each term to the definition column--so the repeats line up
    vertically from line to line, and a line with no term (a wrapped
    continuation line, or a code line) fills the whole span.  The
    default spacer is two spaces, which renders as the classic help
    table above.  A visible spacer renders leaders, like a table of
    contents: spacer=':' gives you

        x:::::::::abcde
        y so long:this is the text
        ::::::::::for y no fooling

    A multi-character spacer tiles, clipped at the front so the
    columns still line up.  The spacer may not be empty: it's the
    fill material, and there's no such thing as filling with
    nothing.

    The indent for the definitions on the right (the "definition
    column") is computed dynamically.  It's one 'spacer' past the
    widest "term" that's no wider than a third of the effective
    margin.  A term wider than that "hangs": it gets the line
    to itself, and its definition starts on the next line, in the
    definition column.  Either way, a term on the same line as its
    definition always has at least one full spacer after it.

    'definition_left_column' overrides the computed column: it's
    the 1-based column (indent included) where the first character
    of every definition goes.  Fussy users may know exactly what
    they want.  The hang rule still applies, now purely geometric:
    a term hangs iff it can't fit on the line with a full spacer
    after it.  A definition_left_column that leaves no room for
    the spacer, or no room for definitions inside the margin,
    raises ValueError.

    Tabs are permitted in the terms and the indent; they're
    expanded to spaces, using 'tab_width'.  Tabs in the indent
    expand at the indent's true position (it starts every line,
    at column 1).  Tabs in a term expand in the term's own
    coordinates--as if the term started at column 1--and the
    expanded term shifts rigidly into place, so the term's
    internal alignment survives wherever the term lands; pass
    term_relative_tabs=False to expand them at the term's true
    position on the page instead.  definition_relative_tabs works
    the same way for the definitions: by default (True) each
    definition is laid out in its author's own coordinates--tab
    stops counted from the definition column, exactly as the
    author saw them--and shifts rigidly into place; pass False
    to land the definition's tabs on the tab stops of the page.
    Tabs are disallowed in the spacer: the spacer repeats and
    shifts around, and a tab's width depends on where it lands.

    You may use either str or bytes, but all arguments must be
    consistently either str or bytes.

    If pairs is empty, returns an empty str.
    """
    pairs = list(pairs)
    if not pairs:
        return ''

    if isinstance(pairs[0][0], bytes):
        str_type = bytes
        empty = b''
        tab = b'\t'
        linebreak = b'\n'
        forbidden = set(bytes_linebreaks)
        characters = _iterate_over_bytes
        if indent is _default_definition_list_indent:
            indent = b'  '
        if spacer is _default_definition_list_spacer:
            spacer = b'  '
    else:
        str_type = str
        empty = ''
        tab = '\t'
        linebreak = '\n'
        forbidden = set(linebreaks)
        characters = iter

    for name, s in (('indent', indent), ('spacer', spacer)):
        if not isinstance(s, str_type):
            raise TypeError(f"{name} must be {str_type.__name__}, not {s!r}")
        if set(characters(s)) & forbidden:
            raise ValueError(f"{name} {s!r} contains linebreak characters")
    if not spacer:
        raise ValueError("spacer must not be empty")
    if tab in spacer:
        raise ValueError(f"spacer {spacer!r} contains tabs")

    space = b' ' if str_type is bytes else ' '

    # an indent starts every line, at column 1: expand its
    # tabs there.
    indent = _expand_tabs(indent, 1, tab_width, tab, space)
    indent_width = len(indent)

    # validate the terms, and expand their tabs: in their own
    # coordinates (term_relative_tabs, the default), or at the
    # column where they actually land, right after the indent.
    term_column = 1 if term_relative_tabs else 1 + indent_width
    expanded = []
    widths = []
    for term, definition in pairs:
        if not isinstance(term, str_type):
            raise TypeError(f"terms must be {str_type.__name__}, not {term!r}")
        if set(characters(term)) & forbidden:
            raise ValueError(f"term {term!r} contains linebreak characters")
        term = _expand_tabs(term, term_column, tab_width, tab, space)
        expanded.append((term, definition))
        widths.append(len(term))
    pairs = expanded

    if definition_left_column is None:
        # the widest term no wider than a third of the effective
        # margin decides the definition column; wider terms hang.
        hang_threshold = (margin - indent_width) // 3
        column = 0
        for width in widths:
            if (width <= hang_threshold) and (width > column):
                column = width
        ribbon_width = column + len(spacer)
    else:
        # the fussy user knows exactly where they want the
        # definitions.  1-based, indent included.
        if (not isinstance(definition_left_column, int)) or (definition_left_column < 1):
            raise ValueError(f"definition_left_column must be a positive int, not {definition_left_column!r}")
        ribbon_width = definition_left_column - 1 - indent_width
        if ribbon_width < len(spacer):
            raise ValueError(f"definition_left_column {definition_left_column} leaves no room for the spacer")

    # a term fits on the same line as its definition iff at least
    # one full spacer separates them; wider terms hang.
    fit_limit = ribbon_width - len(spacer)

    definition_width = margin - indent_width - ribbon_width
    if definition_width < 1:
        raise ValueError(f"the definition column leaves no room for definitions inside margin {margin}")

    # the ribbon: the spacer tiled across the span from the start
    # of the term column to the definition column.  a term line
    # shows the ribbon from its own width onward, so the repeats
    # line up vertically no matter how wide the term is.
    # (the spacer contains no tabs, so its len is its width.)
    ribbon = (spacer * ((ribbon_width // len(spacer)) + 1))[:ribbon_width]

    definition_indent = indent + ribbon
    len_definition_indent = len(definition_indent)

    lines = []
    append = lines.append
    for (term, definition), width in zip(pairs, widths):
        if definition:
            if definition_relative_tabs:
                # lay the definition out in its author's own
                # coordinates--virtual column 1--and shift the
                # resulting lines rigidly into the definition
                # column.  (they're tab-free, so the shift is
                # safe.)  the author's tabs line up the way they
                # did when the author wrote them.
                wrapped = wrap_words(
                    split_text_with_code(definition, tab_width=tab_width),
                    definition_width, tab_width=tab_width)
                if wrapped:
                    wrapped = linebreak.join(
                        (definition_indent + line) if line.strip() else line
                        for line in wrapped.split(linebreak))
            else:
                # the definition's tabs land on the tab stops of
                # the *page*: render it in place.
                wrapped = wrap_words(
                    split_text_with_code(definition, tab_width=tab_width),
                    margin, indent=definition_indent, tab_width=tab_width)
        else:
            wrapped = empty
        if not wrapped:
            append(indent + term)
            continue
        if width > fit_limit:
            append(indent + term)
            append(wrapped)
            continue
        # the first wrapped line starts with definition_indent,
        # which renders exactly as wide as indent + term + the
        # rest of the ribbon.  slice those off and put these there
        # instead.
        append(indent + term + ribbon[width:] + wrapped[len_definition_indent:])
    return linebreak.join(lines)

# --8<-- end big format_definition_list --8<--


##
## help and usage rendering
##
## Formatting happens at run time: bake the formatter, not the
## text.  Built on the word-wrap trio above.
##


# --8<-- start appeal complete --8<--
# --8<-- requires appeal exceptions --8<--

##
## Shell completion (the completion rulings): the engine answers
## "what could legally come next?" from tables--plain data plus
## converter references--so it runs identically in-process and
## inside a generated script.  A converter may carry a
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
    recovers the logical value.  shlex is stdlib, so it rides into a
    standalone script for free.)
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
# --8<-- end appeal complete --8<--




# --8<-- start appeal mcp --8<--
# --8<-- requires appeal exceptions --8<--
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
# --8<-- end appeal mcp --8<--


# --8<-- start appeal theme --8<--
# --8<-- requires appeal exceptions --8<--

##
## Colorization (proposal §8.8, plus the completion/colorization
## rulings): a Theme styles semantic roles, not spans of text, and
## painting happens strictly AFTER layout--the trio only ever
## measures uncolored text.
##

_SGR_WORDS = {
    'bold': '1', 'dim': '2', 'italic': '3', 'underline': '4',
    'black': '30', 'red': '31', 'green': '32', 'yellow': '33',
    'blue': '34', 'magenta': '35', 'cyan': '36', 'white': '37',
    'bright-black': '90', 'bright-red': '91', 'bright-green': '92',
    'bright-yellow': '93', 'bright-blue': '94', 'bright-magenta': '95',
    'bright-cyan': '96', 'bright-white': '97',
}

_SGR_RESET = '\x1b[0m'


class Theme:
    """
    Colors for appeal's own output--help and error messages.
    Each slot's value is a little symbolic language: space-
    separated words from {bold, dim, italic, underline}, the eight
    colors {black, red, green, yellow, blue, magenta, cyan,
    white}, their bright-* variants, and the names of other slots,
    which expand to that slot's resolved words.  Resolved once,
    here; reference cycles and unknown words are refused by name.
    The empty string means "leave that role alone".  A value that
    starts with an escape character (\x1b) passes through
    verbatim--the escape hatch for styling we don't model (it
    can't be referenced by other slots).

    Theme() with no arguments is the stock theme.  Whether a theme
    is USED is a separate, runtime question (resolve_theme):
    NO_COLOR and friends always win.
    """
    def __init__(self, *, program='bold', option='cyan',
                 metavar='dim', operand='', heading='bold',
                 error='bold red', summary=''):
        self.spec = {
            'program': program, 'option': option, 'metavar': metavar,
            'operand': operand, 'heading': heading, 'error': error,
            'summary': summary,
        }
        self.sgr = {}
        resolving = []

        def resolve(slot):
            if slot in self.sgr:
                return self.sgr[slot]
            if slot in resolving:
                raise AppealConfigurationError(
                    f"theme: reference cycle: "
                    f"{' -> '.join(resolving + [slot])}")
            resolving.append(slot)
            value = self.spec[slot]
            if value.startswith('\x1b'):
                codes = None
                on = value
            else:
                codes = []
                for word in value.split():
                    if word in _SGR_WORDS:
                        codes.append(_SGR_WORDS[word])
                        continue
                    if word in self.spec:
                        referenced = resolve(word)
                        if referenced[0] is None:
                            raise AppealConfigurationError(
                                f"theme: {slot!r} references {word!r}, "
                                f"which is a raw escape sequence")
                        codes.extend(referenced[0])
                        continue
                    raise AppealConfigurationError(
                        f"theme: {slot!r}: unknown word {word!r}")
                on = ('\x1b[' + ';'.join(codes) + 'm') if codes else ''
            resolving.pop()
            self.sgr[slot] = (codes, on)
            return self.sgr[slot]

        for slot in self.spec:
            resolve(slot)

    def paint(self, slot, s):
        "Wrap s in the slot's escape codes; a plain slot returns s as-is."
        on = self.sgr[slot][1]
        if not on or not s:
            return s
        return f'{on}{s}{_SGR_RESET}'


def can_colorize(file):
    """
    The established convention, copied from CPython's _colorize
    precedence so appeal programs and stock argparse programs
    respond identically to the same shell: PYTHON_COLORS beats
    NO_COLOR beats FORCE_COLOR, then TERM=dumb, then isatty.
    """
    import os
    python_colors = os.environ.get('PYTHON_COLORS')
    if python_colors == '0':
        return False
    if python_colors == '1':
        return True
    if os.environ.get('NO_COLOR'):
        return False
    if os.environ.get('FORCE_COLOR'):
        return True
    if os.environ.get('TERM') == 'dumb':
        return False
    try:
        return file.isatty()
    except (AttributeError, ValueError):
        return False


def resolve_theme(spec, file):
    """
    The runtime half of the theme decision.  spec is what the
    program was configured with: None (auto: the stock theme),
    False (never), a Theme, or a dict of symbolic strings (a baked
    theme in a generated script).  Returns a Theme to paint with,
    or None for monochrome.  The environment always wins: NO_COLOR
    and friends silence any theme.
    """
    if spec is False:
        return None
    if not can_colorize(file):
        return None
    if spec is None:
        return Theme()
    if isinstance(spec, Theme):
        return spec
    return Theme(**spec)


def _paint_atoms(theme, text):
    """
    Paint the atoms inside a laid-out span: '<metavar>'s and
    '-'-led option strings.  Painting happens inside spans the
    layout already placed, never across them, so the escape codes
    can't perturb any width arithmetic--it already happened.
    """
    out = []
    append = out.append
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '<':
            j = text.find('>', i)
            if j == -1:
                append(text[i:])
                break
            append(theme.paint('metavar', text[i:j + 1]))
            i = j + 1
            continue
        if c == '-' and ((i == 0) or (text[i - 1] in '[|= ')):
            j = i
            while j < n and (text[j].isalnum() or text[j] in '-_'):
                j += 1
            append(theme.paint('option', text[i:j]))
            i = j
            continue
        append(c)
        i += 1
    return ''.join(out)


def _bare_word(token):
    "True if the token is a bare name (an operand in a usage line)."
    return token and all(c.isalnum() or c in '_.' for c in token)


def paint_usage(theme, text):
    """
    Paint a rendered usage block, token by token: the program name,
    option strings, metavars, and bare operand names.  Structural
    characters (brackets, pipes, ellipses) stay plain.  Tokens are
    whole--wrap_words wraps usage at whole units--so every painted
    span survived layout intact.
    """
    painted_program = False
    lines = []
    for line in text.split('\n'):
        stripped = line.lstrip(' ')
        indent = line[:len(line) - len(stripped)]
        tokens = []
        for token in stripped.split(' '):
            if token == 'usage:':
                tokens.append(token)
            elif not painted_program:
                tokens.append(theme.paint('program', token))
                painted_program = True
            elif _bare_word(token.rstrip('.')):
                tokens.append(theme.paint('operand', token))
            else:
                tokens.append(_paint_atoms(theme, token))
        lines.append(indent + ' '.join(tokens))
    return '\n'.join(lines)
# --8<-- end appeal theme --8<--


# --8<-- start appeal help --8<--
# --8<-- requires appeal theme --8<--
# --8<-- requires big word wrap trio --8<--
# --8<-- requires big format_definition_list --8<--
def usage_units(usage):
    """
    Split a usage line into its unbreakable top-level units: the
    program name, bare operands, and complete bracket groups like
    '[-t|--times <int>]'.  Wrapping never splits a unit.
    """
    units = []
    unit = []
    depth = 0
    for ch in usage:
        if ch == ' ' and not depth:
            if unit:
                units.append(''.join(unit))
                unit = []
            continue
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
        unit.append(ch)
    if unit:
        units.append(''.join(unit))
    return units


def render_usage(usage, margin=79):
    """
    The 'usage:' line, wrapped at whole units, continuation lines
    indented to align under the first.
    """
    prefix = 'usage: '
    return wrap_words(usage_units(usage), margin,
        indent=(prefix, ' ' * len(prefix)))


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

default_template = (
    'usage: {usage}\n'
    '\n'
    '{summary}\n'
    '\n'
    '{doc}\n'
    '\n'
    '## Arguments\n'
    '{arguments}\n'
    '\n'
    '## Options\n'
    '{options}\n'
    '\n'
    '## Commands\n'
    '{commands}\n'
)

_TEMPLATE_SECTIONS = ('usage', 'summary', 'doc', 'options',
                      'arguments', 'commands')


def parse_help_template(template):
    """
    Partition a help template at its six {section} placeholders.
    Returns [(name, header, indent), ...] in template order:
    header is the text since the previous placeholder (leading
    newlines included); indent is the header text after its last
    newline--the body's per-line indent.  All six sections must
    appear exactly once; anything else in braces refuses.
    """
    import re as _re
    template = '\n'.join(line.rstrip()
                         for line in template.split('\n'))
    sections = []
    seen = set()
    pos = 0
    for m in _re.finditer(r'\{([A-Za-z_]+)\}', template):
        name = m.group(1)
        if name not in _TEMPLATE_SECTIONS:
            raise AppealConfigurationError(
                f"help template: unknown section {{{name}}}")
        if name in seen:
            raise AppealConfigurationError(
                f"help template: {{{name}}} appears twice")
        seen.add(name)
        header = template[pos:m.start()]
        nl = header.rfind('\n')
        indent = header[nl + 1:] if nl >= 0 else ''
        if not indent.strip() == '':
            indent = ''
        sections.append((name, header, indent))
        pos = m.end()
    missing = [s for s in _TEMPLATE_SECTIONS if s not in seen]
    if missing:
        raise AppealConfigurationError(
            f"help template: missing section(s): "
            f"{', '.join('{' + s + '}' for s in missing)}")
    return sections


_SECTION_TERM_SLOTS = {'arguments': 'operand', 'commands': None,
                       'options': None}


def _render_rows(name, rows, indent, margin=79, theme=None):
    """
    One definition-list section body--no heading.  rows of
    (display, documentation-lines) through format_definition_list
    at the given indent.  With a theme, each row's term is
    painted where it landed (terms never wrap, so the span
    survived layout intact).
    """
    if not rows:
        return ''
    pairs = [(display, '\n'.join(lines)) for display, lines in rows]
    body = format_definition_list(pairs, margin, indent=indent,
                                  spacer='  ')
    if theme is None:
        return body
    slot = _SECTION_TERM_SLOTS.get(name)
    lines = body.split('\n')
    cursor = 0
    for display, _ in pairs:
        prefix = indent + display
        for i in range(cursor, len(lines)):
            if lines[i].startswith(prefix):
                painted = (theme.paint(slot, display) if slot
                           else _paint_atoms(theme, display))
                lines[i] = indent + painted + lines[i][len(prefix):]
                cursor = i + 1
                break
    return '\n'.join(lines)


def rows_markdown(rows):
    """
    Corpus rows [(display, doc-lines), ...] as one Markdown
    definition list, definition order preserved (ruled
    2026-08-05).  An undocumented row is a term with an empty
    definition (': ' with nothing after it--bare ':' wouldn't
    parse as a definition list).  Entry lines are already
    Markdown; continuation lines re-indent under the ':'.

    A nested option's row (its display carries two leading
    spaces per depth level, from the merge) becomes a NESTED
    definition list inside its parent's details (ruled
    2026-08-06)--the rendered table indents sub-options beneath
    the option that declares them.
    """
    def entry_block(display, lines, children):
        body = [l for l in lines] or ['']
        first = f": {body[0]}" if body[0] else ": "
        rest = [("  " + l) if l.strip() else '' for l in body[1:]]
        block = [display, first] + rest
        for child in children:
            block.append('')
            block.extend(("  " + l) if l.strip() else ''
                         for l in entry_block(*child))
        return block

    # rebuild the tree the merge flattened: depth = the display's
    # leading two-space pairs
    roots = []
    stack = []                  # (depth, entry) path to the tip
    for display, lines in rows:
        stripped = display.lstrip(' ')
        depth = (len(display) - len(stripped)) // 2
        entry = (stripped, lines, [])
        while stack and stack[-1][0] >= depth:
            stack.pop()
        (stack[-1][1][2] if stack else roots).append(entry)
        stack.append((depth, entry))
    return '\n\n'.join('\n'.join(entry_block(*e)) for e in roots)


def render_markdown(text, margin=79, theme=None):
    """
    Markdown -> terminal text via big's whole pipeline: parse ->
    style_document -> split_styles_document -> render_terminal ->
    join_styles -> StyleSheet.render.  theme=None renders plain
    (styles resolve to nothing); themed rendering arrives with
    the StyleSheet-based theming rewrite.  Requires big:
    standalone scripts wait on the stage-2 snippets (accepted,
    2026-08-05, heavy development mode).
    """
    from big.markdown import (layout_document, markdown_defaults,
                              parse, split_styles_document,
                              style_document)
    from big.stylesheet import (join_styles, plain_stylesheet,
                                strip_styles)
    document = split_styles_document(style_document(parse(text)))
    layout = layout_document(document)
    rendered = wrap_words(layout, margin=margin, raw=strip_styles)
    sheet = plain_stylesheet | markdown_defaults
    return sheet.render(join_styles(rendered))


def render_help_page(usage, corpus, templates, margin=79, theme=None,
                     suppress=()):
    """
    The --help page, the Markdown pivot's engine (ruled
    2026-08-05): the template establishes the page's ORDER and
    dresses its headings (Markdown, '## Options' by default);
    the corpus fills the slots--summary and doc are Markdown
    prose, the three tables become definition lists whose terms
    are the resolved displays, rows synthesized in definition
    order.  The assembled document renders through big's
    pipeline in one pass.  usage is not Markdown: rendered and
    wrapped separately, at whole units, painted when themed.
    Empty sections are suppressed, header and all; suppress
    names slots to omit entirely (help()'s usage=/summary=/doc=
    knobs).
    """
    parsed = parse_help_template(templates)
    pieces = []        # rendered text pieces, template order
    md = []            # pending markdown, flushed around usage

    def flush():
        text = ''.join(md)
        md.clear()
        if text.strip():
            pieces.append(render_markdown(text, margin, theme)
                          .rstrip('\n'))

    for name, header, indent in parsed:
        if name in suppress:
            continue
        if name == 'usage':
            flush()
            nl = header.rfind('\n')
            lead, prefix = ((header[:nl + 1], header[nl + 1:])
                            if nl >= 0 else ('', header))
            body = wrap_words(usage_units(usage), margin,
                              indent=(prefix, ' ' * len(prefix)))
            if theme is not None:
                body = paint_usage(theme, body)
            pieces.append(body)
            continue
        if name == 'summary':
            content = '\n'.join(corpus['summary'])
        elif name == 'doc':
            content = '\n'.join(corpus['documentation'])
        else:
            content = rows_markdown(corpus[name])
        if not content.strip():
            continue
        md.append(header + content)
    flush()

    text = '\n\n'.join(pieces)
    while '\n\n\n' in text:
        text = text.replace('\n\n\n', '\n\n')
    return text.lstrip('\n').rstrip() + '\n'


def help_margin(max_columns=79):
    """
    The wrap margin for a rendered help page: the terminal's
    width, capped at max_columns (v1's rule--a narrow terminal
    re-wraps, a wide one doesn't stretch lines past the cap).
    Pipes and other non-terminals get the cap itself, so captured
    output is stable.
    """
    import shutil
    return min(shutil.get_terminal_size((max_columns, 24)).columns,
               max_columns)


def render_command_listing(usage, corpus, templates, margin=79):
    """
    The compact command listing: the usage line plus the commands
    table, no prose.  This is the `usage` string attached to
    dispatch-level UsageErrors--helpful enough to name the valid
    commands, terse enough for an error.
    """
    parsed = parse_help_template(templates)
    header = indent = None
    for name, h, ind in parsed:
        if name == 'commands':
            header, indent = h, ind
    body = _render_rows('commands', corpus['commands'], indent,
                        margin)
    # the template's headers are Markdown ('## Commands'); the
    # terse listing wants plain text--strip ATX marks and setext
    # underlines, spell it heading-colon
    words = [l.lstrip('#').strip() for l in header.split('\n')
             if l.strip() and set(l.strip()) - set('=-')]
    heading = (words[0] + ':') if words else 'Commands:'
    return usage + '\n\n' + (heading + '\n' + body).rstrip('\n') \
        if body else usage

# --8<-- end appeal help --8<--


##
## the converter vocabulary
##
## v1's converter vocabulary: split, validate, validate_range,
## counter, accumulator, mapping.  All semantics probed against
## shipping v1 0.6.4.  Factory *products* carry a structured
## recipe (__appeal_recipe__ = (kind, factory, args, kwargs),
## kind 'call' or 'subscript', args/kwargs holding LIVE objects):
## a standalone script re-runs the factory, with literal arguments
## rendered by repr and classes/callables rendered through the
## reference table (so `type=float` and `accumulator[Path]` both
## survive emission)--the north star holds without importing
## appeal.
##


# --8<-- start appeal split --8<--
# --8<-- requires appeal exceptions --8<--
# --8<-- requires big toy_multisplit --8<--
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
                 toy_multisplit(value, list(separators))]
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
    split_converter.__appeal_recipe__ = (
        'call', 'split', tuple(separators),
        {'strip': True} if strip else {})
    split_converter.__appeal_snippet__ = 'appeal split'
    return split_converter
split.__appeal_factory__ = "split(':')"
# --8<-- end appeal split --8<--


# --8<-- start appeal validate --8<--
# --8<-- requires appeal exceptions --8<--
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
    validate_converter.__appeal_recipe__ = (
        'call', 'validate', tuple(values), {'type': type})
    validate_converter.__appeal_snippet__ = 'appeal validate'
    return validate_converter
validate.__appeal_factory__ = "validate('red', 'green')"
# --8<-- end appeal validate --8<--


# --8<-- start appeal validate range --8<--
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
    recipe_kwargs = {'type': type}
    if clamp:
        recipe_kwargs['clamp'] = True
    validate_range_converter.__appeal_recipe__ = (
        'call', 'validate_range', (start, stop), recipe_kwargs)
    validate_range_converter.__appeal_snippet__ = 'appeal validate range'
    return validate_range_converter
validate_range.__appeal_factory__ = "validate_range(0, 10)"
# --8<-- end appeal validate range --8<--


# --8<-- start appeal counter --8<--
# --8<-- requires appeal option protocol --8<--
def counter(*, max=None, step=1):
    """
    Creates a repeatable flag-like option that counts occurrences:
    -v -v -v with step=2 gives 6, capped at max.
    """
    ceiling = max

    class Counter(MultiOption):
        __appeal_recipe__ = ('call', 'counter', (),
                             {'max': ceiling, 'step': step})
        __appeal_snippet__ = 'appeal counter'

        def init(self, default):
            self.value = default if isinstance(default, int) else 0

        def option(self):
            self.value += step

        def render(self):
            if ceiling is not None and self.value > ceiling:
                return ceiling
            return self.value
    Counter.__name__ = 'counter'
    return Counter
counter.__appeal_factory__ = "counter()"
# --8<-- end appeal counter --8<--


# --8<-- start appeal file --8<--
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
        # an opener is a callable: it can't ride a recipe string,
        # so a converter carrying one refuses standalone emission
        # (by name, per the north star)
        settings = {kw: v for kw, v, default in (
                        ('buffering', buffering, -1),
                        ('encoding', encoding, None),
                        ('errors', errors, None),
                        ('newline', newline, None))
                    if v != default}
        file_converter.__appeal_recipe__ = (
            'call', 'file', (mode,), settings)
        file_converter.__appeal_snippet__ = 'appeal file'
    return file_converter
file.__appeal_factory__ = "file()"
# --8<-- end appeal file --8<--


# --8<-- start appeal optional --8<--
# --8<-- requires appeal exceptions --8<--
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
    __appeal_factory__ = "optional[str]"

    @classmethod
    def _parameterize(cls, T):
        if isinstance(T, tuple):
            raise AppealConfigurationError(
                "optional[...] takes exactly one converter")
        if not callable(T):
            raise AppealConfigurationError(
                f"optional[...]: {T!r} isn't callable")
        # None is the no-oparg sentinel: an operand that WAS
        # given always arrives as a str, never the None object--
        # and None renders into standalone scripts, which an
        # anonymous sentinel can't
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
        option_value.__appeal_oparg_borrows_name__ = True
        option_value.__appeal_recipe__ = (
            'subscript', 'optional', (T,), {})
        option_value.__appeal_snippet__ = 'appeal optional'
        return option_value
# --8<-- end appeal optional --8<--


# --8<-- start appeal folds --8<--
# --8<-- requires appeal option protocol --8<--
class _Subscriptable(type):
    """
    v1's crazy science magic, restored for Python 3.6:
    accumulator[int] parameterizes via a metaclass __getitem__,
    because __class_getitem__ (PEP 560) only exists from 3.7.
    The metaclass serves every version, so it's the ONLY spelling.
    """
    def __getitem__(cls, types):
        return cls._parameterize(types)


def _folder(kind, types):
    "The shared engine behind accumulator[...] and mapping[...]."
    names = [f'v{i}' for i in range(len(types))]
    params = ', '.join(f'{n}: _t{i}' for i, n in enumerate(names))
    namespace = {f'_t{i}': t for i, t in enumerate(types)}
    if kind == 'accumulator':
        payload = names[0] if len(names) == 1 else f'({", ".join(names)})'
        body = f'self.values.append({payload})'
    else:
        payload = (names[1] if len(names) == 2
                   else f'({", ".join(names[1:])})')
        body = f'self.values[{names[0]}] = {payload}'
    exec(f'def option(self, {params}):\n    {body}', namespace)
    return namespace['option']


class accumulator(MultiOption, metaclass=_Subscriptable):
    """
    A repeatable option collecting values into a list.  Subscript
    for types: accumulator[int] collects ints; accumulator[int, str]
    collects (int, str) tuples, two operands per occurrence.
    """
    __appeal_snippet__ = 'appeal folds'

    def init(self, default):
        self.values = list(default) if default else []

    def option(self, value):
        self.values.append(value)

    def render(self):
        return self.values

    @classmethod
    def _parameterize(cls, types):
        if not isinstance(types, tuple):
            types = (types,)
        sub = _Subscriptable('accumulator', (cls,),
                             {'option': _folder('accumulator', types)})
        sub.__appeal_recipe__ = ('subscript', 'accumulator',
                                 tuple(types), {})
        return sub


class mapping(MultiOption, metaclass=_Subscriptable):
    """
    A repeatable option collecting key/value pairs into a dict:
    --define KEY VALUE.  Subscript for types: mapping[int, str]
    maps ints to strs; mapping[int, str, float] maps ints to
    (str, float) tuples, three operands per occurrence.
    """
    __appeal_snippet__ = 'appeal folds'

    def init(self, default):
        self.values = dict(default) if default else {}

    def option(self, key, value):
        if key in self.values:
            raise ValueError(f"key {key!r} defined more than once")
        self.values[key] = value

    def render(self):
        return self.values

    @classmethod
    def _parameterize(cls, types):
        if not isinstance(types, tuple) or len(types) < 2:
            raise TypeError("mapping[...] needs at least a key type "
                            "and one value type")
        sub = _Subscriptable('mapping', (cls,),
                             {'option': _folder('mapping', types)})
        sub.__appeal_recipe__ = ('subscript', 'mapping',
                                 tuple(types), {})
        return sub
# --8<-- end appeal folds --8<--


def runtime_source():
    """
    This module's entire source text, for streaming into standalone
    scripts.  Read from the file, so there is exactly one copy of
    the runtime in the world.
    """
    with open(__file__, 'rt', encoding='utf-8') as f:
        return f.read()
