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

# Appeal REQUIRES big (ruled 2026-08-06).  In-process code uses
# the installed big directly; standalone EMISSION grabs big's
# snippet regions live from the installed big's source at
# compile time (codegen.snippet_source), so upgrading big
# reaches every subsequently compiled parser.  Generated
# scripts themselves stay dependency-free.
from big.builtin import can_colorize
from big.markdown import glyphs_from_stylesheet, markdown_defaults
from big.stylesheet import (StyleSheet, ansi_16_color_palette,
                            escape_styles, join_styles,
                            plain_stylesheet, strip_styles, style,
                            transforms)
from big.text import (OverflowStrategy, _iterate_over_bytes,
                      expand_tabs, format_definition_list,
                      merge_columns, split_text_with_code,
                      toy_multisplit, wrap_words)


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
        elif isinstance(usage, tuple):
            print(render_baked_help(usage, margin=help_margin(79)),
                  end='')
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
# --8<-- requires appeal help --8<--
def run_main(parse, args=None, stylesheet=None, completion=None,
             errors=None, version=None, margin=79):
    """
    The main() driver for a generated parser: parse and execute,
    print errors the polite way, return the exit code.  stylesheet
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
        sheet = resolve_stylesheet(stylesheet, error_stream())
        return sheet.render(style('error', 'error:'))

    def print_usage(usage):
        # a STRING is a usage line; a TUPLE is a baked listing
        # (pieces), finished here--at the real margin, styled for
        # the error stream (errors ride the pipeline too, ruled
        # 2026-08-06)
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
    except Exception as e:
        # the installed appeal's exceptions, raised by the user's
        # module inside a standalone script
        kind = foreign_appeal_error(e)
        if kind == 'data':
            print(f"{error_prefix()} {e}", file=error_stream())
            usage = getattr(e, 'usage', None)
            if usage:
                print_usage(usage)
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


# --8<-- start appeal fingerprint --8<--
##
## Fingerprints (Larry's design, 2026-08-09): a compiled
## standalone module identifies--and polices--the functions
## handed to its decorators by fingerprint.  Everything the
## grammar and the baked help were derived from is in here:
## parameter shape, defaults, annotations, the docstring, and
## the @option/@parameter attributes.  Hand-rolled from the
## function and code objects--inspect.signature knows nothing
## these don't, and it's slow.
##

def _stable_repr(obj):
    "repr with memory addresses masked--id churn isn't drift."
    import re
    return re.sub(r'0x[0-9a-fA-F]+', '0x?', repr(obj))


def _deref_annotated(value):
    # Annotated[T, converter]: the LAST metadata element is the
    # converter (v1's documented rule, kept)
    metadata = getattr(value, '__metadata__', None)
    if metadata:
        return metadata[-1]
    return value


def _annotation_token(value):
    "One annotation, as stable, bakeable text."
    value = _deref_annotated(value)
    recipe = getattr(value, '__appeal_recipe__', None)
    if recipe:
        # a vocabulary product (validate(1, 2), accumulator[Path],
        # ...): its identity IS its recipe.  The product's
        # __module__ differs by world (appeal.runtime in-process,
        # the compiled module standalone)--the recipe doesn't.
        kind, factory, args, kwargs = recipe
        return (f'recipe:{kind}:{factory}:'
                f'{_stable_repr(args)}:{_stable_repr(kwargs)}')
    module = getattr(value, '__module__', None)
    qualname = getattr(value, '__qualname__', None)
    if qualname and '<locals>' in qualname:
        return qualname             # closures: structure, not home
    if qualname:
        return f'{module}.{qualname}'
    return _stable_repr(value)      # list[int], dict[str,int], ...


def _parameter_default(fn, name):
    "The default of fn's parameter `name`, from the raw objects."
    kwdefaults = getattr(fn, '__kwdefaults__', None) or {}
    if name in kwdefaults:
        return kwdefaults[name]
    code = fn.__code__
    defaults = getattr(fn, '__defaults__', None) or ()
    positional = code.co_varnames[:code.co_argcount]
    if name in positional:
        index = positional.index(name) - (code.co_argcount
                                          - len(defaults))
        if index >= 0:
            return defaults[index]
    raise AppealConfigurationError(
        f"parameter {name!r} of {fn.__name__!r} has no default")


def _params_host(obj):
    "Where a callable keeps its parameters: itself, or __init__."
    if hasattr(obj, '__code__'):
        return obj
    init = getattr(obj, '__init__', None)
    if init is not None and hasattr(init, '__code__'):
        return init
    return None


def fingerprint(fn):
    """
    The identity a compiled parser was baked from: a nested tuple
    of plain data, equal iff nothing the grammar or the help
    depends on has changed.  reprs into a script as a literal.
    A class converter's parameters live on __init__ (the host);
    its docstring and decorations stay its own.
    """
    host = _params_host(fn)
    if host is None:
        raise AppealConfigurationError(
            f"can't fingerprint {fn!r}: no code object")
    code = host.__code__
    varargs = bool(code.co_flags & 0x04)
    varkw = bool(code.co_flags & 0x08)
    named = code.co_argcount + code.co_kwonlyargcount
    annotations = getattr(host, '__annotations__', None) or {}
    return (
        fn.__name__,
        code.co_argcount,
        getattr(code, 'co_posonlyargcount', 0),
        code.co_kwonlyargcount,
        varargs, varkw,
        code.co_varnames[:named + varargs + varkw],
        _stable_repr(getattr(host, '__defaults__', None)),
        _stable_repr(getattr(host, '__kwdefaults__', None)),
        tuple(sorted((name, _annotation_token(value))
                     for name, value in annotations.items())),
        getattr(fn, '__doc__', None),
        _stable_repr(getattr(fn, '_appeal_option_overrides', None)),
        _stable_repr(getattr(fn, '_appeal_parameter_usage', None)),
    )


def resolve_fingerprint_path(fn, path):
    """
    Walk from a live decorated function to one of the callables
    its grammar uses, by the recipe the emitter baked: a tuple of
    ('annotation', param) / ('default_type', param) steps.  The
    exact mirror of codegen's harvest--the converter arrives LIVE
    at decoration time, never by import.
    """
    obj = fn
    for kind, name in path:
        host = _params_host(obj)
        if host is None:
            raise AppealConfigurationError(
                f"can't resolve {name!r} on {obj!r}")
        if kind == 'annotation':
            obj = _deref_annotated(host.__annotations__[name])
        elif kind == 'default_type':
            obj = type(_parameter_default(host, name))
        elif kind == 'override':
            # an @app.option(annotation=...) converter: recorded
            # on the function itself, reachable by declaration
            # index
            param, index = name
            declaration = obj._appeal_option_overrides[param][index]
            obj = _deref_annotated(declaration['annotation'])
        else:
            raise AppealConfigurationError(
                f"unknown fingerprint path step {kind!r}")
    return obj
# --8<-- end appeal fingerprint --8<--


# --8<-- start appeal standalone shim --8<--
# --8<-- requires appeal exceptions --8<--
# --8<-- requires appeal fingerprint --8<--
# --8<-- requires appeal run main --8<--

##
## The standalone shim (Larry's design, 2026-08-09): a compiled
## module WEARS THE APPEAL API.  The user's program is the
## documented spelling, unchanged--
##
##     try:
##         from . import standalone as appeal
##     except ImportError:
##         import appeal
##
## --and when the compiled module is the one imported, Appeal()
## and its decorators don't build anything: @app.command() on
## `forecast` says "I have the precompiled bits for that over
## here", fingerprints the live function against what the parser
## was baked from, and binds it as the thing run_forecast calls.
## Converters bind the same way, resolved from the live
## function's annotations--nothing imports the user's code, the
## relationship runs the other way.
##
## Verification is all-or-nothing at main(): EVERY mapped
## function checks, not just the one dispatched (ruled: editing
## foo's signature yells even when you ran bar), plus baked
## configuration.  Any mismatch is a loud, complete list, with
## the remedy (regenerate) in the message.
##

def _standalone_appeal(spec, namespace):
    "Build the Appeal class a compiled module exports."

    class Appeal:
        def __init__(self, name=None, *,
                     stylesheet=None, version=None, repeat=False,
                     errors=None, script=None, margin=79,
                     positional_argument_usage_format=None,
                     default_options=None, default_mappings=None,
                     doc=None):
            # live knobs: these never touched the baked grammar
            # or pieces, so they simply apply, custom values and
            # all (stylesheet compositions and custom error
            # streams are LEGAL here--everything is runtime)
            self.stylesheet = stylesheet
            self.errors = errors
            self.templates = spec['templates']
            # baked knobs: the grammar and help were derived from
            # these; a different value now means a stale parser
            self._staleness = []
            for knob, value in (('name', name),
                                ('version', version),
                                ('repeat', repeat),
                                ('margin', margin),
                                ('positional_argument_usage_format',
                                 positional_argument_usage_format),
                                ('doc', doc)):
                baked = spec['config'][knob]
                if value is None and knob != 'repeat':
                    continue        # unspecified: the baked value
                if _stable_repr(value) != baked:
                    self._staleness.append(
                        f"Appeal({knob}=...): compiled with "
                        f"{baked}, now {_stable_repr(value)}")
            for knob, value in (('default_options', default_options),
                                ('default_mappings', default_mappings)):
                if value is not None:
                    raise AppealConfigurationError(
                        f"a compiled parser can't take {knob}= "
                        f"(its effects are baked in); regenerate "
                        f"the standalone module instead")
            self._bound = {}        # spec key -> live function

        # -- registration: match, don't build --------------------

        def command(self, name=None):
            def register(fn):
                word = name if name is not None else fn.__name__
                if word not in spec['commands']:
                    known = ', '.join(sorted(spec['commands']))
                    raise AppealConfigurationError(
                        f"this compiled parser has no command "
                        f"{word!r} (it knows: {known}); regenerate "
                        f"the standalone module")
                self._bound[('command', word)] = fn
                return fn
            return register

        def global_command(self):
            def register(fn):
                if spec['global'] is None:
                    raise AppealConfigurationError(
                        "this compiled parser has no global "
                        "command; regenerate the standalone module")
                self._bound[('global',)] = fn
                return fn
            return register

        def option(self, parameter_name, *strings,
                   annotation=None, default=None):
            # records on the function, exactly as the real facade
            # does (build.add_option_override)--the fingerprint
            # covers the result, so a changed @option call reads
            # as staleness at main()
            from inspect import Parameter
            annotation = (Parameter.empty if annotation is None
                          else annotation)
            default = (Parameter.empty if default is None
                       else default)
            def register(fn):
                overrides = getattr(fn, '_appeal_option_overrides',
                                    None)
                if overrides is None:
                    overrides = {}
                    fn._appeal_option_overrides = overrides
                declaration = {'strings': tuple(strings),
                               'annotation': annotation,
                               'default': default}
                declarations = overrides.setdefault(parameter_name,
                                                    [])
                if declaration not in declarations:
                    declarations.append(declaration)
                return fn
            return register

        def parameter(self, parameter_name, *, usage=None):
            def register(fn):
                names = getattr(fn, '_appeal_parameter_usage', None)
                if names is None:
                    names = {}
                    fn._appeal_parameter_usage = names
                names[parameter_name] = usage
                return fn
            return register
        argument = parameter            # v1's deprecated alias

        # -- verification: all-or-nothing, at main() -------------

        def _verify_and_bind(self):
            problems = list(self._staleness)
            entries = [(('global',), spec['global'])] if spec['global'] else []
            entries += [(('command', word), entry)
                        for word, entry in spec['commands'].items()]
            for key, entry in entries:
                what = (f"command {key[1]!r}" if key[0] == 'command'
                        else "the global command")
                fn = self._bound.get(key)
                if fn is None:
                    problems.append(
                        f"{what} was compiled in but never "
                        f"registered with @app.command()")
                    continue
                if fingerprint(fn) != entry['fingerprint']:
                    problems.append(
                        f"{what} has changed since this parser "
                        f"was compiled (signature, defaults, "
                        f"annotations, docstring, or @option/"
                        f"@parameter decorations)")
                    continue
                for ref_name, path, ref_fpr in entry['refs']:
                    try:
                        obj = resolve_fingerprint_path(fn, path)
                    except Exception:
                        problems.append(
                            f"{what}: converter for {ref_name!r} "
                            f"can't be resolved from the live "
                            f"function")
                        continue
                    if (ref_fpr is not None
                            and fingerprint(obj) != ref_fpr):
                        problems.append(
                            f"{what}: converter "
                            f"{getattr(obj, '__name__', ref_name)!r} "
                            f"has changed since this parser was "
                            f"compiled")
                        continue
                    namespace[ref_name] = obj
                namespace[entry['impl']] = fn
            if _stable_repr(self.templates) != spec['config']['templates']:
                problems.append(
                    "app.templates has changed since this parser "
                    "was compiled")
            if problems:
                bullets = '\n'.join(f'  - {p}' for p in problems)
                raise AppealConfigurationError(
                    f"stale compiled parser "
                    f"({spec['program']}):\n{bullets}\n"
                    f"Regenerate it: run this program against "
                    f"installed appeal (delete or ignore the "
                    f"compiled module) and call "
                    f"app.standalone(path=...) again.")

        # -- running ---------------------------------------------

        def main(self, args=None):
            self._verify_and_bind()
            parse = namespace[spec['entry']]
            complete = spec.get('complete')
            completion = ((namespace[complete], spec['program'])
                          if complete and complete in namespace
                          else None)
            sys.exit(run_main(parse, args,
                              stylesheet=self.stylesheet,
                              completion=completion,
                              errors=self.errors,
                              version=spec['config'].get('version_value'),
                              margin=spec['config']['margin_value']))

        # -- the honest refusals ---------------------------------

        def standalone(self, path=None, **kwargs):
            raise AppealConfigurationError(
                "this IS the compiled parser; to regenerate, run "
                "the program against installed appeal (the "
                "try/except import falls through when the "
                "compiled module is absent)")

        def __getattr__(self, name):
            raise AppealConfigurationError(
                f"Appeal.{name} isn't part of this compiled "
                f"parser; if the program needs it, regenerate "
                f"with a current appeal (in-process-only APIs "
                f"never compile)")

    return Appeal
# --8<-- end appeal standalone shim --8<--


##
## big's snippet regions--the word-wrap trio, the StyleSheet
## renderer, the ANSI stylesheets, terminal color detection--
## are NOT copied here (the compile-time grab, ruled
## 2026-08-06): in-process code imports big directly (the
## imports at the top), and standalone emission reads big's
## regions live from the installed big's source
## (codegen.snippet_source).  The glue regions below are
## appeal's own: they stream ahead of big's regions in a
## generated script and supply what those regions expect from
## their home modules.
##

# --8<-- start appeal stylesheet preamble --8<--
import os
import sys

style_delimiters = '⦃⦙⦄'

def export(*args, **kwargs):
    """
    big's module-manager hook: identity as a decorator (@export
    on a class or function), no-op on name strings.
    """
    if len(args) == 1 and not kwargs and not isinstance(args[0], str):
        return args[0]

def _multisplit(*args, **kwargs):
    # only split_styles calls it, and splitting is bake-time
    # work--a runtime call is a bug, not a fallback
    raise RuntimeError("split_styles is bake-time only")
# --8<-- end appeal stylesheet preamble --8<--


# --8<-- start appeal stylesheet alias --8<--
# the home-module spellings big/markdown.py's regions expect.
# Deferring wrappers, not assignments: snippets emit in source
# order and this appeal-side glue precedes big's regions in the
# combined warehouse--the names resolve at CALL time, by which
# big's stylesheet region has defined them.
def _StyleSheet(*args, **kwargs):
    return StyleSheet(*args, **kwargs)

def _style_span(*args, **kwargs):
    return style(*args, **kwargs)

def _gently_title(*args, **kwargs):
    return gently_title(*args, **kwargs)
# --8<-- end appeal stylesheet alias --8<--

# --8<-- start appeal markdown defaults --8<--
# Appeal owns the WIDTH-AWARE structure (ruled 2026-08-08: big
# stays width-agnostic by design--its markdown_defaults are the
# neutral look: headings unruled, the thematic break a short
# dash).  These entries compose OVER big's defaults in every
# help stylesheet: h1 between full-length rules, h2 over one,
# the thematic break filled to the margin--all in terms of
# `line`, which the renderer injects.  STRUCTURE only; the look
# (attributes, colors) arrives with the themes.  Alert titles
# say their kind colors directly--referencing heading2 would
# inherit its rule, inside the quote bars.
appeal_markdown_defaults = {
    'heading1': ('T',
        '⦃clip⦙⦃line⦄⦙⦃fill⦙⦃heading1_rule⦄⦙⦃strip⦙T⦄⦄⦄\n'
        '⦃strip⦙T⦄\n'
        '⦃clip⦙⦃line⦄⦙⦃fill⦙⦃heading1_rule⦄⦙⦃strip⦙T⦄⦄⦄'),
    'heading2': ('T',
        '⦃strip⦙T⦄\n'
        '⦃clip⦙⦃line⦄⦙⦃fill⦙⦃heading2_rule⦄⦙⦃strip⦙T⦄⦄⦄'),
    'rule': ('T', '⦃fill⦙T⦙⦃line⦄⦄'),
    'heading_note':      ('T', '⦃blue⦙T⦄'),
    'heading_tip':       ('T', '⦃green⦙T⦄'),
    'heading_important': ('T', '⦃purple⦙T⦄'),
    'heading_warning':   ('T', '⦃orange⦙T⦄'),
    'heading_caution':   ('T', '⦃red⦙T⦄'),
}
# --8<-- end appeal markdown defaults --8<--


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
# --8<-- requires appeal stylesheet preamble --8<--
# --8<-- requires appeal stylesheet alias --8<--
# --8<-- requires appeal markdown defaults --8<--
# --8<-- requires big stylesheet render core --8<--
# --8<-- requires big stylesheet transforms --8<--
# --8<-- requires big ansi stylesheets --8<--
# --8<-- requires big terminal color --8<--
# --8<-- requires big markdown defaults --8<--

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

# uncolored: structure and attributes, no color--the base every
# colored theme extends.  heading_color is ONE slot: a theme
# recolors all six headings (and their rules) with one entry.
uncolored_theme = {
    **appeal_markdown_defaults,
    'heading_color': ('T', 'T'),
    'heading1':   ('T',
        '⦃heading_color⦙⦃clip⦙⦃line⦄⦙⦃fill⦙⦃heading1_rule⦄⦙⦃strip⦙T⦄⦄⦄⦄\n'
        '⦃bold⦙⦃heading_color⦙⦃strip⦙T⦄⦄⦄\n'
        '⦃heading_color⦙⦃clip⦙⦃line⦄⦙⦃fill⦙⦃heading1_rule⦄⦙⦃strip⦙T⦄⦄⦄⦄'),
    'heading2':   ('T',
        '⦃bold⦙⦃heading_color⦙⦃strip⦙T⦄⦄⦄\n'
        '⦃heading_color⦙⦃clip⦙⦃line⦄⦙⦃fill⦙⦃heading2_rule⦄⦙⦃strip⦙T⦄⦄⦄⦄'),
    'heading3':   ('T', '⦃bold⦙⦃heading_color⦙⦃strip⦙T⦄⦄⦄\n'
                       '⦃heading_color⦙⦃fill⦙⦃heading2_rule⦄⦙⦃strip⦙T⦄⦄⦄'),
    'heading4':   ('T', '⦃italic⦙⦃heading_color⦙T⦄⦄'),
    'heading5':   ('T', '⦃italic⦙⦃heading_color⦙T⦄⦄'),
    'heading6':   ('T', '⦃heading_color⦙⦃lower⦙T⦄⦄'),
    # inline structure
    'code':       ('T', 'T'),               # themes color this
    'codeblock':  ('T', '⦃code⦙T⦄'),        # inherits code
    'link':       ('T', '⦃underline⦙T⦄'),
    'marker':     ('T', 'T'),
    'blockquote': ('T', 'T'),
    'term':       ('T', '⦃bold⦙T⦄'),        # deflist terms
    # GitHub alerts: bodies wear their kind's color too
    'note':              ('T', '⦃blue⦙T⦄'),
    'heading_note':      ('T', '⦃bold⦙⦃blue⦙T⦄⦄'),
    'tip':               ('T', '⦃green⦙T⦄'),
    'heading_tip':       ('T', '⦃bold⦙⦃green⦙T⦄⦄'),
    'important':         ('T', '⦃purple⦙T⦄'),
    'heading_important': ('T', '⦃bold⦙⦃purple⦙T⦄⦄'),
    'warning':           ('T', '⦃orange⦙T⦄'),
    'heading_warning':   ('T', '⦃bold⦙⦃orange⦙T⦄⦄'),
    'caution':           ('T', '⦃red⦙T⦄'),
    'heading_caution':   ('T', '⦃bold⦙⦃red⦙T⦄⦄'),
    # the roles, attribute-only defaults
    'program':    ('T', '⦃bold⦙T⦄'),
    'command':    ('T', '⦃bold⦙T⦄'),
    'option':     ('T', '⦃bold⦙T⦄'),
    'argument':   ('T', 'T'),
    'oparg':      ('T', '⦃argument⦙T⦄'),    # ruled: defaults to argument
    'summary':    ('T', '⦃bold⦙T⦄'),
    'error':      ('T', '⦃bold⦙T⦄'),
}

# plain: every span strips to its text.
plain_theme = {name: ('T', 'T') for name in uncolored_theme}
plain_theme['codeblock'] = ('T', '⦃code⦙T⦄')
plain_theme['oparg'] = ('T', '⦃argument⦙T⦄')


def _theme(**overrides):
    "A theme: the uncolored base plus your colors."
    t = dict(uncolored_theme)
    t.update(overrides)
    return t


# appeal_theme: the default.  Designed against the ANSI 16
# (terminal light/dark modes remap those for legibility, so it
# looks right on both).
appeal_theme = _theme(
    command   = ('T', '⦃bold⦙⦃cyan⦙T⦄⦄'),
    option    = ('T', '⦃cyan⦙T⦄'),
    argument  = ('T', '⦃italic⦙T⦄'),
    summary   = ('T', '⦃bold⦙T⦄'),
    error     = ('T', '⦃bold⦙⦃red⦙T⦄⦄'),
    code      = ('T', '⦃green⦙T⦄'),          # ruled: code is green
    marker    = ('T', '⦃yellow⦙T⦄'),
    link      = ('T', '⦃underline⦙⦃blue⦙T⦄⦄'),
    heading_color = ('T', '⦃cyan⦙T⦄'),
)

# the four corners: warm = red/orange/yellow, cool =
# blue/green/cyan, purple in both.  light_* themes use dark_
# colors (dark ink on a light page); dark_* themes use light_.

light_warm_theme = _theme(
    command   = ('T', '⦃bold⦙⦃dark_orange⦙T⦄⦄'),
    option    = ('T', '⦃dark_red⦙T⦄'),
    argument  = ('T', '⦃italic⦙⦃dark_gray⦙T⦄⦄'),
    summary   = ('T', '⦃bold⦙⦃dark_red⦙T⦄⦄'),
    error     = ('T', '⦃bold⦙⦃red⦙T⦄⦄'),
    code      = ('T', '⦃dark_orange⦙T⦄'),
    marker    = ('T', '⦃dark_yellow⦙T⦄'),
    link      = ('T', '⦃underline⦙⦃dark_purple⦙T⦄⦄'),
    heading_color = ('T', '⦃dark_red⦙T⦄'),
)

dark_warm_theme = _theme(
    command   = ('T', '⦃bold⦙⦃light_orange⦙T⦄⦄'),
    option    = ('T', '⦃light_red⦙T⦄'),
    argument  = ('T', '⦃italic⦙⦃light_gray⦙T⦄⦄'),
    summary   = ('T', '⦃bold⦙⦃light_orange⦙T⦄⦄'),
    error     = ('T', '⦃bold⦙⦃light_red⦙T⦄⦄'),
    code      = ('T', '⦃light_orange⦙T⦄'),
    marker    = ('T', '⦃light_yellow⦙T⦄'),
    link      = ('T', '⦃underline⦙⦃light_purple⦙T⦄⦄'),
    heading_color = ('T', '⦃light_red⦙T⦄'),
)

light_cool_theme = _theme(
    command   = ('T', '⦃bold⦙⦃dark_cyan⦙T⦄⦄'),
    option    = ('T', '⦃dark_blue⦙T⦄'),
    argument  = ('T', '⦃italic⦙⦃dark_gray⦙T⦄⦄'),
    summary   = ('T', '⦃bold⦙⦃dark_blue⦙T⦄⦄'),
    error     = ('T', '⦃bold⦙⦃red⦙T⦄⦄'),     # errors stay red, even here
    code      = ('T', '⦃dark_green⦙T⦄'),
    marker    = ('T', '⦃dark_cyan⦙T⦄'),
    link      = ('T', '⦃underline⦙⦃dark_purple⦙T⦄⦄'),
    heading_color = ('T', '⦃dark_blue⦙T⦄'),
)

dark_cool_theme = _theme(
    command   = ('T', '⦃bold⦙⦃light_cyan⦙T⦄⦄'),
    option    = ('T', '⦃light_blue⦙T⦄'),
    argument  = ('T', '⦃italic⦙⦃light_gray⦙T⦄⦄'),
    summary   = ('T', '⦃bold⦙⦃light_cyan⦙T⦄⦄'),
    error     = ('T', '⦃bold⦙⦃light_red⦙T⦄⦄'),
    code      = ('T', '⦃light_green⦙T⦄'),
    marker    = ('T', '⦃light_cyan⦙T⦄'),
    link      = ('T', '⦃underline⦙⦃light_purple⦙T⦄⦄'),
    heading_color = ('T', '⦃light_blue⦙T⦄'),
)


def resolve_stylesheet(spec, file=None):
    """
    The runtime half of the stylesheet decision.  spec is what
    the program was configured with: None (auto), False (never
    any color), or a complete composed StyleSheet, used VERBATIM
    (ruled 2026-08-06).  Auto composes appeal_theme over the
    ANSI 16 when this stream wants color at this moment
    (can_colorize: NO_COLOR and friends always win)--the 16
    because the terminal remaps them to its own scheme, so the
    theme stays legible on light and dark alike (ruled
    2026-08-06); False (and colorless auto) gets the plain
    palette: no escapes of any kind.
    """
    if spec is None or spec is False:
        palette = (ansi_16_color_palette
                   if (spec is None) and can_colorize(file=file)
                   else plain_stylesheet)
        return (markdown_defaults | transforms | palette
                | _StyleSheet(appeal_theme))
    return spec


def usage_markup(usage):
    """
    Dress a usage line in role spans: the first bare word is the
    program, '-'-led words are options, <words> are arguments at
    the top level and opargs inside brackets, other bare words
    at the top level are arguments.  Structural characters
    (brackets, pipes, ellipses) stay bare.  Purely lexical and
    purely additive--the visible text is unchanged, so
    usage_units and the wrap see the same units, and a plain
    sheet strips the spans back to the input.
    """
    out = []
    append = out.append
    depth = 0
    saw_program = False
    i = 0
    n = len(usage)
    while i < n:
        c = usage[i]
        if c == '<':
            j = usage.find('>', i)
            if j == -1:
                append(escape_styles(usage[i:]))
                break
            role = 'oparg' if depth else 'argument'
            append(style(role, escape_styles(usage[i:j + 1])))
            i = j + 1
            continue
        if c == '-' and ((i == 0) or (usage[i - 1] in '[|= ')):
            j = i
            while j < n and (usage[j].isalnum() or usage[j] in '-_'):
                j += 1
            append(style('option', escape_styles(usage[i:j])))
            i = j
            continue
        if c.isalnum() or c in '_.':
            j = i
            while j < n and (usage[j].isalnum() or usage[j] in '_.'):
                j += 1
            word = usage[i:j]
            if not any(ch.isalnum() for ch in word):
                append(escape_styles(word))      # '...' and friends
            elif not saw_program:
                append(style('program', escape_styles(word)))
                saw_program = True
            elif not depth:
                append(style('argument', escape_styles(word)))
            else:
                append(escape_styles(word))
            i = j
            continue
        if c == '[':
            depth += 1
        elif c == ']':
            depth = max(0, depth - 1)
        append(escape_styles(c))
        i += 1
    return ''.join(out)
# --8<-- end appeal theme --8<--


# --8<-- start appeal help --8<--
# --8<-- requires appeal theme --8<--
# --8<-- requires big word wrap trio --8<--
# --8<-- requires big format_definition_list --8<--
# --8<-- requires appeal stylesheet preamble --8<--
# --8<-- requires big terminal color --8<--
# --8<-- requires big stylesheet render core --8<--
# --8<-- requires appeal stylesheet alias --8<--
# --8<-- requires big ansi stylesheets --8<--
# --8<-- requires big markdown defaults --8<--
# --8<-- requires big glyphs from stylesheet --8<--
# --8<-- requires big gently_title --8<--
# --8<-- requires big stylesheet transforms --8<--
# --8<-- requires appeal markdown defaults --8<--
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


def help_stylesheet(file=None):
    """
    The StyleSheet a help page paints with by default, for this
    stream at this moment: appeal_theme over the ANSI 16 when
    the stream wants color, the plain palette (no escapes of any
    kind) when it doesn't--pipes and captures stay clean.
    Exactly resolve_stylesheet(None, file).
    """
    return resolve_stylesheet(None, file)


def render_baked_help(pieces, margin=79, file=None,
                      stylesheet=None):
    """
    The runtime half of a help page.  pieces is the baked,
    template-ordered tuple from help_page_pieces: ('usage',
    prefix, usage-string) entries are dressed in role spans
    (usage_markup) and wrapped at whole units; ('markdown',
    layout) entries carry big's width-independent layout
    tuples--wrap_words lays them out at the real margin
    (strip_styles measuring the words), join_styles fuses
    adjacent spans.  Either way the stylesheet paints last:
    stylesheet is a spec (None auto per stream, False never,
    or a composed StyleSheet used verbatim).
    """
    sheet = resolve_stylesheet(stylesheet, file)
    # the renderer injects `line`--'-' repeated to the margin, a
    # full-width rule bare and a margin-wide model inside
    # clip/fill (ruled 2026-08-08).  Only the renderer knows the
    # margin; injected UNDERNEATH, so a sheet that defines its
    # own `line` wins (the stylesheet= verbatim rule).
    sheet = StyleSheet({'line': ('-' * margin,)}) | sheet
    glyphs = glyphs_from_stylesheet(sheet)
    # ...and measures it: glyphs_from_stylesheet only knows big's
    # markdown glyph roles, so a bare ⦃line⦄ word in a layout
    # would measure zero-wide and wrap_words would drop it.  Same
    # symmetry as the injection--only the renderer knows how wide
    # a line is.
    line_span = _style_span('line')
    line_glyph = sheet.render(line_span)
    measure = lambda w: strip_styles(
        glyphs(w).replace(line_span, line_glyph))
    out = []
    for piece in pieces:
        if piece[0] == 'usage':
            prefix, usage = piece[1], piece[2]
            # roles are lexical and additive, so the units and
            # their widths are those of the bare usage line
            body = wrap_words(usage_units(usage_markup(usage)),
                              margin, raw=measure,
                              indent=(prefix, ' ' * len(prefix)))
            out.append(sheet.render(body))
        else:
            layout = piece[1]
            text = wrap_words(layout, margin=margin, raw=measure)
            # span_linebreaks: a WRAPPED heading is one heading--
            # its structural entry fires once, around the block
            text = join_styles(text, span_linebreaks=True)
            out.append(sheet.render(text).rstrip('\n'))
    text = '\n\n'.join(out)
    while '\n\n\n' in text:
        text = text.replace('\n\n\n', '\n\n')
    return text.lstrip('\n').rstrip() + '\n'
# --8<-- end appeal help --8<--


##
## bake-time help machinery: assembles and lays out the page
## on the AUTHOR'S machine (imports big; never streamed into
## a generated script--the script gets baked layout tuples and
## the runtime half above).
##

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


def listing_pieces(usage, corpus, templates):
    """
    The terse command listing as BAKED PIECES: the usage line and
    the Commands table, no prose.  Attached to dispatch-level
    UsageErrors and printed for a bare command line;
    render_baked_help finishes it at print time--wrapped at the
    real margin, styled for the real stream (the 2026-08-06
    ruling: errors render through the pipeline too).
    """
    return help_page_pieces(usage, corpus, templates,
                            suppress=('summary', 'doc',
                                      'arguments', 'options'))


def render_help_page(usage, corpus, templates, margin=79,
                     file=None, stylesheet=None, suppress=()):
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

    BAKE + RUN in one call: help_page_pieces does the Markdown
    work (this machine), render_baked_help wraps and paints (any
    machine)--the same two halves a generated script uses, so
    in-process help and standalone help cannot drift.
    """
    return render_baked_help(
        help_page_pieces(usage, corpus, templates, suppress),
        margin, file=file, stylesheet=stylesheet)


def term_markup(word, role):
    """
    Dress one laid-out table-term word in its role span: 'option'
    terms lexically (option strings and <oparg>s), 'argument' and
    'command' terms whole.  The word usually wears the Markdown
    'term' wrapper; the role span nests INSIDE it, so a themed
    table term is bold AND role-colored.  The word came out of
    the styled pipeline, so its text is already escaped.
    """
    prefix = suffix = ''
    inner = word
    open_ = style_delimiters[0] + 'term' + style_delimiters[1]
    close = style_delimiters[2]
    if word.startswith(open_) and word.endswith(close):
        inner = word[len(open_):-len(close)]
        prefix, suffix = open_, close
    if not inner:
        return word
    if role != 'option':
        return prefix + style(role, inner) + suffix
    out = []
    append = out.append
    i = 0
    n = len(inner)
    while i < n:
        c = inner[i]
        if c == '<':
            j = inner.find('>', i)
            if j == -1:
                append(inner[i:])
                break
            append(style('oparg', inner[i:j + 1]))
            i = j + 1
            continue
        if c == '-' and ((i == 0) or (inner[i - 1] in '[|= ')):
            j = i
            while j < n and (inner[j].isalnum() or inner[j] in '-_'):
                j += 1
            append(style('option', inner[i:j]))
            i = j
            continue
        append(c)
        i += 1
    return prefix + ''.join(out) + suffix


_SECTION_TERM_ROLES = {'options': 'option', 'arguments': 'argument',
                       'commands': 'command'}


def role_layout(layout, section):
    """
    Dress one laid-out help section in appeal's role spans--the
    bake half of themed help.  Table sections mark their
    definition-list terms (term_markup); the summary marks every
    word (join_styles fuses them back into one span at render).
    Purely additive: a plain sheet strips the spans, so unthemed
    output is unchanged.
    """
    role = _SECTION_TERM_ROLES.get(section)
    out = []
    for item in layout:
        if role and (type(item) is tuple) and item and (item[0] == 'term'):
            out.append(('term',) + tuple(term_markup(w, role)
                                         for w in item[1:]))
        elif ((section == 'summary') and isinstance(item, str)
              and item.strip()):
            out.append(style('summary', item))
        else:
            out.append(item)
    return tuple(out)


def help_page_pieces(usage, corpus, templates, suppress=()):
    """
    The bake half of a help page: assemble the template-ordered
    Markdown, parse/style/lay it out via big, dress the flense's
    sections in role spans (role_layout), and return the
    template-ordered piece tuple render_baked_help consumes at
    runtime--('usage', prefix, usage-string) for the usage line,
    ('markdown', layout) for everything else, where layout is
    big's width-independent flat tuple.  Every value reprs into
    valid Python source: a generated script embeds the pieces as
    a literal.
    """
    from big.markdown import (layout_document, parse,
                              split_styles_document, style_document)
    pieces = []
    md = []            # pending markdown, flushed per role change

    def flush(section=None):
        text = ''.join(md)
        md.clear()
        if text.strip():
            document = split_styles_document(
                style_document(parse(text)))
            layout = layout_document(document)
            if section:
                layout = role_layout(layout, section)
            pieces.append(('markdown', layout))

    for name, header, indent in parse_help_template(templates):
        if name in suppress:
            continue
        if name == 'usage':
            flush()
            nl = header.rfind('\n')
            prefix = header[nl + 1:] if nl >= 0 else header
            pieces.append(('usage', prefix, usage))
            continue
        if name == 'summary':
            content = '\n'.join(corpus['summary'])
        elif name == 'doc':
            content = '\n'.join(corpus['documentation'])
        else:
            content = rows_markdown(corpus[name])
        if not content.strip():
            continue
        if name == 'doc':
            md.append(header + content)
            continue
        # a roled section bakes alone, so role_layout knows whose
        # terms (or words) it is dressing
        flush()
        md.append(header + content)
        flush(name)
    flush()
    return tuple(pieces)


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
