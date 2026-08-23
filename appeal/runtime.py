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

import collections
import operator
import sys

# Appeal REQUIRES big (ruled 2026-08-06).  In-process code uses
# the installed big directly; standalone EMISSION grabs big's
# snippet regions live from the installed big's source at
# compile time (codegen.snippet_source), so upgrading big
# reaches every subsequently compiled parser.  Generated
# scripts themselves stay dependency-free.
# NB: appeal.runtime imports NOTHING from big--the parse/convert/
# dispatch core is stdlib-only, so `import appeal.runtime` (the
# precompiled fast path) is near bare-Python speed.  All big-backed
# rendering lives in appeal/render.py, imported lazily.


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
    # A precompiled module bakes no help/usage text: when an error
    # that renders help (the DataError family) crosses a command's
    # parse/convert body, that body tags this with the Command it
    # belongs to (whose .callable the shim filled live), so the
    # compiled shim can hand it to full Appeal for rendering
    # (precompile.py; ruled 2026-08-17).  None everywhere else--the
    # in-process paths ignore it.
    command = None
    # set by the set dispatcher's "no command"/"unknown command"
    # errors: render the tagged command's set LISTING, not its own
    # usage line (a class command can be both a constructor and a
    # parent, so the Command alone can't disambiguate).
    want_listing = False


class _CompiledHelp(Exception):
    """
    Not an error--a control-flow signal a COMPILED parser raises
    when the line asks for help (-h/--help, or a bare set line
    wanting the listing).  It carries the Command whose page to show
    (None = the program root: a bare app's page or a set's listing).
    The compiled shim catches it and renders live through full
    Appeal; it never escapes run_main.  In-process parsers never
    raise it.

    code is the exit status to return after rendering: 0 for
    requested help (-h/--help/help), 1 for the listing a BARE set
    line falls through to (orientation, not a diagnostic--v1's
    nonzero exit for "no command ran").
    """
    def __init__(self, command=None, code=0):
        super().__init__()
        self.command = command
        self.code = code


def _tag_errors(fn, command):
    """
    Wrap a compiled parser's scan/run so any help-rendering error
    (the DataError family) crossing it gets tagged with the Command
    it belongs to--the deepest command wins (`if e.command is
    None`).  `command` is the Command OBJECT itself (stable): the
    shim fills its .callable after module exec, so nothing here needs
    a getter--reading e.command.callable later sees the live value.
    Other exceptions pass through untouched.
    """
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DataError as e:
            if e.command is None:
                e.command = command
            raise
    return wrapper


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
    # STAGE 1: recognition now lives once, in tokenize(); it walks
    # the command line into the uniform IR (operand runs interleaved
    # with canonical option tuples).  Every recognition error--
    # unknown option, "requires a value", a value on a flag, a bad
    # '=' boolean--raises THERE, in argv order, before we fold.
    if command_split is not None:
        tokens, rest = tokenize(argv, options, usage, command_split)
    else:
        tokens = tokenize(argv, options, usage)

    # STAGE 1.5: fold the IR into operands + given (the value-shaping
    # and occurrence tangle).  The generated eager walk reuses fold_ir
    # too, to reconstruct `given` for a converter group's inner options.
    operands, given = fold_ir(tokens, options, usage, positions)

    if command_split is not None:
        return operands, given, rest
    return operands, given


def fold_ir(tokens, options, usage=None, positions=None):
    """
    Fold the stage-1 IR into (operands, given): operands flattened from
    the operand runs, given mapping each option KEY to its value(s) per
    kind (a flag's last-wins scalar, a value/multi occurrence list, a
    fold's tuples, a w:/s: option's positional records).  positions, if
    given, is filled with each option's first-appearance operand count
    (and a ('seq', key) clock) for the gate rule.

    A key whose canonical name isn't in `options` is skipped--so a
    caller can fold only a SUBSET of the recognized options (e.g. a
    group's inner options) and leave the rest to the eager on-sight
    walk.  operands are gathered regardless.
    """
    operands = []
    given = {}
    seq = [0]

    # the IR carries only canonical keys; recover each key's kind and
    # arity from the string table (all of an option's strings share
    # one entry shape).
    by_key = {}
    for _s, _entry in options.items():
        by_key.setdefault(_entry[0], _entry)

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
        elif kind == 'value':
            # collect every occurrence, in command-line order:
            # convert_value converts them all (ruled 2026-08-16:
            # every oparg is validated--"it's not called validate
            # for nothing") and the LAST wins for the value (ruled
            # 2026-07-18: a shell alias baking `--mode fast` is
            # overridden by a later `--mode safe`).  Re-add at the
            # end so `given` stays in last-occurrence order, which
            # is how a parameter shared by several option strings
            # picks the one that spoke last.
            occurrences = given.pop(key, [])
            occurrences.append(value)
            given[key] = occurrences
        elif kind in ('flag', 'nullary', 'group'):
            # last one wins.  A bare flag idempotently stores `not
            # default` (its entry's presence value): -v -v is -v.
            # The explicit spellings (--verbose=false) are absolute.
            # Reinsertion keeps `given` in last-occurrence order,
            # which is how a parameter shared by several option
            # strings knows which string spoke last.
            if key in given:
                del given[key]
            given[key] = value
        else:   # 'multi' collects raw strings; 'fold' tuples of them
            given.setdefault(key, []).append(value)

    for token in tokens:
        key = token[0]
        if key == '':
            operands.extend(token[1:])
            continue
        entry = by_key.get(key)
        if entry is None:
            continue   # not in this (sub)table--the caller owns this key
        kind = entry[1]
        base = kind[2:] if kind[:2] in ('w:', 's:') else kind
        if base in ('fold', 'fold1', 'group'):
            maximum = entry[3] if len(entry) > 3 else entry[2]
        else:
            maximum = entry[2] if len(entry) > 2 else 1
        raws = token[1:]
        if base in ('flag', 'nullary') or maximum == 0:
            if raws:
                # a flag's explicit boolean, carried by tokenize as the
                # literal 'true'/'false' (already validated there)
                value = raws[0] == 'true'
            else:
                # presence: a flag stores its entry's value (v1's `not
                # default`; the bare auto-help entry stores True);
                # nullary is True; an options-only group is ()
                value = (entry[2] if base == 'flag' and len(entry) > 2
                         else True if base in ('flag', 'nullary') else ())
        else:
            values = list(raws)
            value = (tuple(values)
                     if (base in ('fold', 'fold1', 'group') or maximum != 1)
                     else values[0])
        record(key, kind, value)

    return operands, given


def tokenize(argv, options, usage=None, command_split=None):
    """
    STAGE 1 of the two-stage parser (Larry's design): recognize the
    command line into a uniform, ordered IR--a list of tuples.  Each
    tuple is `(marker, *raw_strings)`:

      * marker '' is an operand RUN: ('', 'a', 'b')--consecutive
        operands merge into one tuple, so the interleaving with
        options is preserved (that ordering is what makes
        last-wins and window-binding fall out in stage 2).
      * marker is a canonical option KEY otherwise, followed by its
        raw oparg strings: ('--units', 'C'), ('--span', '3', '4'),
        or a bare ('--verbose',) for a flag.  Aliases normalize to
        the key (the option table's entry[0]); `-abc` splits into
        one tuple per flag; '=', attachment, and '--' are resolved
        here.  Nothing is converted--stage 2 dispatches on the
        marker and converts, knowing each key's kind.

    options is the same table parse_tokens uses.  With command_split
    = (minimum, maximum, command_words), returns (tokens, rest) for
    a global command scanned ahead of a subcommand; otherwise returns
    tokens.  This is stage 1 ONLY--no `given`, no occurrence lists,
    no conversion; that tangle moves into the generated stage-2 walk.
    """
    tokens = []
    it = iter(argv)
    force_positional = False

    def operand(text):
        # merge into the current run, or start one
        if tokens and tokens[-1][0] == '':
            tokens[-1] = tokens[-1] + (text,)
        else:
            tokens.append(('', text))

    def option(key, values):
        tokens.append((key,) + tuple(values))

    def n_operands():
        # operands seen so far--the window position an option lands
        # at (the interleaving carries what `positions` used to)
        return sum(len(t) - 1 for t in tokens if t[0] == '')

    def arity(entry, base):
        if base in ('fold', 'fold1', 'group'):
            return entry[2], (entry[3] if len(entry) > 3 else entry[2])
        n = entry[2] if len(entry) > 2 else 1
        return n, n

    def flag_value(name, text):
        if text == 'true':
            return 'true'
        if text == 'false':
            return 'false'
        raise UsageError(
            f"option {name!r}: '=' value must be 'true' or "
            f"'false', not {text!r}", usage)

    def greedy(name, minimum, maximum):
        nonlocal force_positional
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
                f"option {name!r} requires "
                f"{'a value' if minimum == 1 else f'{minimum} values'}",
                usage)
        return values

    def split_here(token):
        # deterministic: a global/precommand era fills its arguments greedily
        # to its maximum, then hands the rest (a command word, or a stray) to
        # the command loop.  It NEVER peeks at the command-word set mid-fill --
        # an optional argument eats whatever's next, a *args runs to the end.
        if command_split is None:
            return None
        _minimum, maximum, _command_words = command_split
        n = n_operands()
        if maximum is not None:
            assert n <= maximum
            if n == maximum:
                return [token] + list(it)
        return None

    for token in it:
        if force_positional or (not token.startswith('-')) or (token == '-'):
            rest = split_here(token)
            if rest is not None:
                return tokens, rest
            operand(token)
            continue

        if token == '--':
            force_positional = True
            continue

        if token.startswith('--'):
            name_part, equals, value_part = token.partition('=')
            entry = options.get(name_part)
            if entry is None:
                rest = split_here(token)    # saturated era: an option we don't
                if rest is not None:        # own ends our boundary -- yield it
                    return tokens, rest
                tail = did_you_mean(
                    name_part,
                    [s for s in options if s.startswith('--')])
                raise UsageError(
                    f"unknown option {name_part!r}{tail}", usage)
            key, kind = entry[0], entry[1]
            base = kind[2:] if kind[:2] in ('w:', 's:') else kind
            minimum, maximum = arity(entry, base)
            if base in ('flag', 'nullary') or maximum == 0:
                if equals:
                    if base != 'flag':
                        raise UsageError(
                            f"option {name_part!r} doesn't take a value",
                            usage)
                    option(key, (flag_value(name_part, value_part),))
                    continue
                option(key, ())
                continue
            if equals:
                if maximum > 1:
                    counts = (f'{maximum} values' if minimum == maximum
                              else f'up to {maximum} values')
                    raise UsageError(
                        f"option {name_part!r} takes {counts} "
                        f"and can't use '='", usage)
                option(key, (value_part,))
                continue
            option(key, greedy(name_part, minimum, maximum))
            continue

        if token[1].isdigit() and ('-' + token[1]) not in options:
            rest = split_here(token)
            if rest is not None:
                return tokens, rest
            operand(token)
            continue

        chars = token[1:]
        for index, c in enumerate(chars):
            entry = options.get('-' + c)
            if entry is None:
                if index == 0:              # whole bundle unowned: a saturated
                    rest = split_here(token)  # era yields it (a later char was
                    if rest is not None:      # ours, so a stray there is an error)
                        return tokens, rest
                raise UsageError(f"unknown option {'-' + c!r}", usage)
            key, kind = entry[0], entry[1]
            base = kind[2:] if kind[:2] in ('w:', 's:') else kind
            minimum, maximum = arity(entry, base)
            if base in ('flag', 'nullary') or maximum == 0:
                rest = chars[index + 1:]
                if rest.startswith('='):
                    if base != 'flag':
                        raise UsageError(
                            f"option {'-' + c!r} doesn't take a value",
                            usage)
                    option(key, (flag_value('-' + c, rest[1:]),))
                    break
                option(key, ())
                continue
            rest = chars[index + 1:]
            if rest:
                if maximum == 1:
                    if rest.startswith('='):
                        rest = rest[1:]
                    option(key, (rest,))
                    break
                counts = (f'{maximum} values' if minimum == maximum
                          else f'up to {maximum} values')
                raise UsageError(
                    f"option {'-' + c!r} takes {counts} and "
                    f"must be last in a bundle", usage)
            option(key, greedy('-' + c, minimum, maximum))

    if command_split is not None:
        return tokens, []
    return tokens


class Command:
    """
    One node of a program's command tree--the single dispatch record
    shared by the in-process and compiled parsers (0.6.4 had this;
    the facade's tree-of-Appeals and the grammar's Plan are heavier
    layers above it).  A leaf has no subcommands; a parent (a set)
    carries its children in `subcommands` (word -> Command).

    `callable` is the live command function.  In-process it's bound
    at construction; in a COMPILED module it starts None and the
    shim fills it at registration--and because everything (the run
    body, the error wrappers, the dispatch frames) holds the SAME
    Command object, filling `callable` once is seen everywhere, with
    no re-lookup.  That is the whole point of the object: it is the
    stable indirection a bare module global can't be.

    `scan`/`run` are the two generated stages.  `fused` is set
    instead for the auto commands (version, help) that scan and run
    in one call.  `usage` is the baked listing pieces in-process, or
    None in a compiled module (which renders live).
    """
    __slots__ = ('name', 'callable', 'scan', 'run', 'subcommands',
                 'words', 'repeat', 'usage', 'default', 'fused',
                 'fingerprint', 'options', 'arguments', 'converters')

    def __init__(self, name=None, *, callable=None, scan=None, run=None,
                 subcommands=None, words=None, repeat=False,
                 usage=None, default=None, fused=None,
                 fingerprint=None, options=None, arguments=None,
                 converters=None):
        self.name = name
        self.callable = callable
        self.scan = scan
        self.run = run
        self.subcommands = subcommands if subcommands is not None else {}
        self.words = words
        self.repeat = repeat
        self.usage = usage
        self.default = default
        self.fused = fused
        # compile-time verification data a COMPILED module carries so
        # its shim can police drift (all None in-process, and None on
        # fused auto commands): the recursive signature fingerprint,
        # the @option and @argument decoration digests, and the
        # reachable converters (path + drift) for live binding.
        self.fingerprint = fingerprint
        self.options = options
        self.arguments = arguments
        self.converters = converters

    def __repr__(self):
        kind = ('fused' if self.fused else
                'set' if self.subcommands else 'command')
        return f'<Command {self.name!r} ({kind})>'


def scan_command_set(argv, parse_globals, commands, usage=None,
                     default=None, repeat=False, words=None):
    """
    Stage 1 of a multi-command program: scan the whole line--global
    portion, command word, command portion--with no user code.  A
    malformed line dies here, before anything runs (Appeal rule).

    parse_globals is the global command's Command (or None); each
    commands value is a Command (leaf, nested set, or fused auto
    command).  With repeat (Appeal's cycling), a command's
    arguments--all of them, optional included--may be followed by
    another command word, resolved against `words`; scanning loops
    until the line runs out.  Returns (invocations, tail):
    invocations is a list of (word, command, operands, given,
    positions)--word None for the global command--and tail is the
    odd trailing job, if any: ('fused', word, callable, tokens) for a
    fused Command, or ('default',) for an empty line with a default
    command.  A compiled command bakes no usage, so any error from
    its scan is tagged HERE with its Command (its own operand error--
    a usage line, not the set listing).
    """
    def do_scan(command, *args):
        try:
            return command.scan(*args)
        except DataError as e:
            if e.command is None:
                e.command = command
            raise

    invocations = []
    rest = list(argv)
    for era in parse_globals:            # an ordered list of head eras (each a
                                         # Command); they run front-to-back
        operands, given, rest, positions = do_scan(era, rest)
        invocations.append((None, era, operands, given, positions))
    if not rest:
        if default is not None:
            return invocations, ('default',)
        # an empty command line isn't a mistake, it's someone who
        # needs orientation (ruled 2026-07-09, git-style): the
        # caller shows the listing and exits 1; nothing runs
        return invocations, ('bare',)

    # the resolution stack, deepest set on top.  Each frame's Command
    # is the set whose subcommands are in scope (a synthetic root
    # Command for the top level, callable None).  Consulting a set
    # for its FIRST command is free--that's descent, how the line got
    # here; re-entering it is repetition, gated on that set's repeat.
    root = Command(subcommands=commands, words=words, repeat=repeat,
                   usage=usage)
    stack = [{'command': root, 'entered': False}]

    def frames_in_order():
        for depth, frame in enumerate(reversed(stack)):
            if depth == 0 and not frame['entered']:
                yield frame          # descent: first consult free
            elif frame['command'].repeat:
                yield frame          # re-entry: gated on repeat

    def resolvable_words():
        out = set()
        for frame in frames_in_order():
            out.update(frame['command'].words or ())
        return frozenset(out)

    def tag(err, frame):
        # a compiled set bakes no usage; the frame's Command (its
        # callable filled live by the shim) tells the shim which
        # LISTING to render.  In-process the baked usage is used and
        # this is ignored.  The root frame's command has no callable,
        # so it renders the program root.
        err.command = frame['command']
        err.want_listing = True
        return err

    while rest:
        word = rest[0]
        entry = target = None
        for frame in frames_in_order():
            entry = frame['command'].subcommands.get(word)
            if entry is not None:
                target = frame
                break
        if entry is None:
            if word.startswith('-') and word not in ('-', '--'):
                # commands never start with a dash: a leading-dash straggler
                # is a mistyped option (--verison), not a mystery command
                raise tag(UsageError(f"unknown option {word}",
                                     stack[-1]['command'].usage), stack[-1])
            tail = did_you_mean(word, resolvable_words())
            raise tag(UsageError(f"unknown command {word!r}{tail}",
                                 stack[-1]['command'].usage), stack[-1])
        while stack[-1] is not target:
            stack.pop()              # re-base at the resolved set
        target['entered'] = True
        if entry.subcommands:
            # a nested set: the parent runs first, like a global
            # command of its own little set
            stack.append({'command': entry, 'entered': False})
            boundary = resolvable_words()
            operands, given, rest, positions = do_scan(
                entry, rest[1:], boundary)
            invocations.append((word, entry, operands, given,
                                positions))
            continue
        if entry.fused is not None:
            # fused: scanned and executed together, last (stage
            # separation inside is its own business)
            return invocations, ('fused', word, entry.fused, rest[1:])
        boundary = resolvable_words()
        operands, given, rest, positions = do_scan(
            entry, rest[1:], boundary or None)
        invocations.append((word, entry, operands, given, positions))

    if len(stack) > 1 and not stack[-1]['entered']:
        # a parent was named but its set never got a command: the
        # set's default command (a Command) fills in, or it's an error
        d = stack[-1]['command'].default
        if d is None:
            raise tag(UsageError("no command specified.",
                                 stack[-1]['command'].usage), stack[-1])
        operands, given, _, positions = do_scan(d, [], None)
        invocations.append((None, d, operands, given, positions))
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
    for word, command, operands, given, positions in invocations:
        # the command runs with its OWN Command in hand (for the live
        # callable and its -h); a compiled command bakes no usage, so
        # its convert-time errors get tagged with it here
        try:
            result = command.run(operands, given, positions, env,
                                  command)
        except DataError as e:
            if e.command is None:
                e.command = command
            raise
        if (isinstance(result, int)
                and not isinstance(result, bool) and result):
            return result
    if tail is not None:
        if tail[0] == 'fused':
            return tail[2](tail[3])
        # ('default',): the root default command (a Command), run
        # with its own Command in hand like any other
        operands, given, rest, positions = default.scan([])
        return default.run(operands, given, positions, {}, default)
    return result


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
             errors=None, version=None, margin=79, fallback=None):
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
        # render is imported lazily--only when an error actually
        # prints, so the success path (and `import appeal.runtime`)
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
    except _CompiledHelp as h:
        # a COMPILED parser asked for help (it bakes none): full
        # Appeal renders it live, from the tagged command function
        if fallback is None:
            raise           # never raised in-process--a real bug
        return fallback.on_help(h)
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
        if fallback is not None:
            # a COMPILED parser: it baked no usage, and tagged the
            # error with the command that owns it--full Appeal
            # renders the message + usage live (never re-running the
            # line: the command may already have had side effects)
            return fallback.on_usage(e)
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


##
## Fingerprints (Larry's design, 2026-08-09): a compiled
## module identifies--and polices--the functions handed to its
## decorators by fingerprint.  Everything the PARSING and
## DISPATCH were derived from is in here: parameter shape,
## defaults, annotations, and the @option/@parameter
## attributes--but NOT the docstring (it feeds only help, which
## a compiled module renders live; ruled 2026-08-17).
## Hand-rolled from the
## function and code objects--inspect.signature knows nothing
## these don't, and it's slow.
##

def _stable_repr(obj):
    "repr with memory addresses masked--id churn isn't drift (no `re`)."
    s = repr(obj)
    if '0x' not in s:
        return s
    hexdigits = '0123456789abcdefABCDEF'
    out = []
    i, n = 0, len(s)
    while i < n:
        # mask 0x + one-or-more hex digits (a memory address) to a fixed
        # token -- the digit count never leaks, so the fingerprint is the
        # same on 32- and 64-bit
        if (s[i] == '0' and i + 2 < n and s[i + 1] == 'x'
                and s[i + 2] in hexdigits):
            out.append('<address>')
            i += 2
            while i < n and s[i] in hexdigits:
                i += 1
        else:
            out.append(s[i])
            i += 1
    return ''.join(out)


def _canonical(obj):
    """
    A hash-seed-stable serialization of a value: sets/dicts sorted (their
    repr order is randomized per run), code objects recursed (a nested
    function's own bytecode + consts).  Feeds _callable_fingerprint.
    """
    if isinstance(obj, (bytes, bytearray)):
        return b'b' + bytes(obj)
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return repr(obj).encode()
    if isinstance(obj, (tuple, list)):
        return b'(' + b','.join(_canonical(x) for x in obj) + b')'
    if isinstance(obj, (set, frozenset)):
        return b'{' + b','.join(sorted(_canonical(x) for x in obj)) + b'}'
    if isinstance(obj, dict):
        return b'{' + b','.join(sorted(
            _canonical(k) + b':' + _canonical(v) for k, v in obj.items())) + b'}'
    code = getattr(obj, 'co_code', None)
    if code is not None:                        # a code object (nested callable)
        return b'code(' + code + _canonical(obj.co_consts) + b')'
    return _stable_repr(obj).encode()


def _callable_fingerprint(fn):
    """
    A hash identifying a policy callable (default_mappings/default_options) by
    its bytecode, constants, and closure -- enough to catch a swapped or
    edited callable at run time (compile-time bake vs run-time check).  It
    cannot see through calls the callable MAKES: change a function it calls
    and this won't notice (accepted -- we can only do so much).
    """
    if fn is None:
        return None
    import _sha1                              # C module: ~0.1ms, vs hashlib ~16ms
    code = fn.__code__
    closure = tuple(cell.cell_contents for cell in (fn.__closure__ or ()))
    material = _canonical((code.co_code, code.co_consts, closure))
    return _sha1.sha1(material).hexdigest()


# the compiled Appeal's default for a policy argument: "use the library
# default".  The facade can't reproduce that default without importing full
# appeal, so for the sentinel it falls back to the fingerprint the emitter
# baked (the compile-time policy's).  An explicit value is fingerprinted live.
_CONFIG_DEFAULT = object()


def config_fingerprint(version, default_mappings_fp, default_options_fp):
    """
    The compiled Appeal's configuration identity: the version plus the two
    policy callables' fingerprints (default_mappings/default_options).  Baked
    at compile time, recomputed in the compiled Appeal's __init__; a mismatch
    means the configuration changed since compile -> regenerate.
    """
    import _sha1
    material = _canonical((version, default_mappings_fp, default_options_fp))
    return _sha1.sha1(material).hexdigest()


def signature_fingerprint(fn):
    """
    A short hash of fingerprint(fn) -- the compiled parser's per-command drift
    check, baked per Converter class and re-verified when the live function is
    wired at @command.  fingerprint() covers the signature shape/defaults/
    annotations the baked register() depends on; the docstring is not identity.
    """
    import _sha1
    return _sha1.sha1(_canonical(fingerprint(fn))).hexdigest()


def _deref_annotated(value):
    # Annotated[T, converter]: the LAST metadata element is the
    # converter (v1's documented rule, kept)
    metadata = getattr(value, '__metadata__', None)
    if metadata:
        return metadata[-1]
    return value


def _annotation_token(value, owner_module=None):
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
    if qualname and owner_module is not None and module == owner_module:
        # defined in the same file as its owner: a LOCAL
        # reference.  The file's own name is volatile--imported
        # at compile time it's `weather`, run directly it's
        # `__main__`--but the two flip together, so locality is
        # the stable fact.
        return f'local.{qualname}'
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


def _converter_fingerprint(value, owner_module, seen):
    """
    An annotation's entry in a fingerprint: a NON-LEAF converter
    recurses into its own fingerprint (so a converter's signature
    change surfaces in its command's fingerprint), while leaves
    (builtins) and vocabulary products stay flat tokens.  Reached
    the same way resolve_fingerprint_path walks, so drift is caught
    everywhere the grammar reaches a converter.
    """
    value = _deref_annotated(value)
    if getattr(value, '__appeal_recipe__', None):
        return _annotation_token(value, owner_module)   # recipe: structural
    if getattr(value, '__module__', None) == 'builtins':
        return _annotation_token(value, owner_module)   # int/str/... : leaf
    if _params_host(value) is None or id(value) in seen:
        # uninspectable, a generic alias, or a cycle (build forbids
        # converter cycles, but guard anyway)
        return _annotation_token(value, owner_module)
    return fingerprint(value, seen)                     # RECURSE


def fingerprint(fn, seen=frozenset()):
    """
    The identity a compiled parser was baked from: a nested tuple
    of plain data, equal iff nothing the grammar or the help
    depends on has changed.  reprs into a script as a literal.
    Mirrors inspect.Signature--the function's NAME is not identity
    (rename freely; only the shape matters).  A converter parameter
    nests its OWN fingerprint, recursively.  A class converter's
    parameters live on __init__ (the host).  The docstring is NOT
    part of identity (ruled 2026-08-17): it feeds help, not parse
    or dispatch, and a compiled module bakes no help.
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
    owner_module = getattr(fn, '__module__', None)
    # the docstring is deliberately NOT here (ruled 2026-08-17): the
    # fingerprint confirms the compiled PARSING and DISPATCH are
    # current, and the docstring feeds neither--a compiled module
    # bakes no help text, rendering it live through full Appeal.
    # (Dropping it also keeps hashlib off the cold path.)
    inner = seen | {id(fn)}
    return (
        code.co_argcount,
        getattr(code, 'co_posonlyargcount', 0),
        code.co_kwonlyargcount,
        varargs, varkw,
        code.co_varnames[:named + varargs + varkw],
        _stable_repr(getattr(host, '__defaults__', None)),
        _stable_repr(getattr(host, '__kwdefaults__', None)),
        tuple(sorted((name, _converter_fingerprint(value, owner_module, inner))
                     for name, value in annotations.items())),
    )


def decoration_fingerprint(fn, option_overrides, parameter_usage):
    """
    A stable rendering of everything @app.option and
    @app.parameter said about fn--recorded in the APP, never on
    the function (ruled 2026-08-09), so it fingerprints
    separately: the function vouches for the function, the app
    vouches for the registration.  option_overrides and
    parameter_usage are callable-keyed dicts (the app registry's,
    or the shim's replay).
    """
    owner_module = getattr(fn, '__module__', None)
    overrides = option_overrides.get(fn) or {}
    usage = parameter_usage.get(fn) or {}
    return (
        tuple(sorted(
            (param,
             tuple((decl['strings'],
                    _annotation_token(decl['annotation'],
                                      owner_module),
                    _stable_repr(decl['default']))
                   for decl in decls))
            for param, decls in overrides.items())),
        tuple(sorted(usage.items())),
    )


def resolve_fingerprint_path(fn, path, option_overrides=None):
    """
    Walk from a live decorated function to one of the callables
    its grammar uses, by the recipe the emitter baked: a tuple of
    ('annotation', param) / ('default_type', param) /
    ('override', (param, index)) steps.  The exact mirror of
    codegen's harvest--the converter arrives LIVE at registration
    time, never by import.  option_overrides is the
    callable-keyed @option registry (needed only for 'override'
    steps).
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
            # in the app's registry, reachable by declaration
            # index
            param, index = name
            declaration = (option_overrides or {})[obj][param][index]
            obj = _deref_annotated(declaration['annotation'])
        else:
            raise AppealConfigurationError(
                f"unknown fingerprint path step {kind!r}")
    return obj



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

_OPTION_UNSET = object()      # option(default=...) omitted marker
_KNOB_UNSET = object()        # constructor knob omitted marker

##
## the compiled module's POLICY VOCABULARY: stand-ins wearing the
## public names, so the same program source spells
## appeal.default_mappings(...) / appeal.default_long_option in
## both worlds.  Their EFFECTS are baked into the compiled
## grammar; these exist to be compared against what was baked
## (and to refuse loudly if anything ever tries to RUN one).
## NOTE: executing inside appeal/runtime.py these define
## runtime-module aliases too--harmless, the real ones live in
## build/__init__ and nothing imports these from here.
##





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

import os
import sys


# the home-module spellings big/markdown.py's regions expect.
# Deferring wrappers, not assignments: snippets emit in source
# order and this appeal-side glue precedes big's regions in the
# combined warehouse--the names resolve at CALL time, by which
# big's stylesheet region has defined them.
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
##
## help and usage rendering
##
## Formatting happens at run time: bake the formatter, not the
## text.  Built on the word-wrap trio above.
##



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
# in a generated script calls format_definition_list, so the
# ~10KB region no longer rides along.  Appeal still imports it
# in-process for the borrowed-trio tests.)
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
## bake-time help machinery: assembles and lays out the page
## on the AUTHOR'S machine (imports big; never streamed into
## a generated script--the script gets baked layout tuples and
## the runtime half above).
##

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
    appeal.runtime imports nothing from big--big.text alone costs
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
    split_converter.__appeal_recipe__ = (
        'call', 'split', tuple(separators),
        {'strip': True} if strip else {})
    split_converter.__appeal_snippet__ = 'appeal split'
    return split_converter
split.__appeal_factory__ = "split(':')"


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

        def __call__(self):
            if ceiling is not None and self.value > ceiling:
                return ceiling
            return self.value
    Counter.__name__ = 'counter'
    return Counter
counter.__appeal_factory__ = "counter()"


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
    __appeal_snippet__ = 'appeal folds'

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
        sub.__appeal_recipe__ = ('subscript', 'accumulator',
                                 tuple(types), {})
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
    __appeal_snippet__ = 'appeal folds'
    # the reader (config layer) hands these a whole dict, not a
    # sequence of occurrences; the two halves' converters live on
    # the parameterized subclass (below), invisible to the fold's
    # single-operand protocol but caught by the recipe fingerprint.
    __appeal_mapping__ = True
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
        sub.__appeal_recipe__ = ('subscript', 'mapping',
                                 (key_type, value_type), {})
        return sub



# ====================================================================
#  The data-driven back-end engine (moved here from processor.py so a
#  precompiled parser imports ONLY appeal.runtime).  The work-item
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
        self.instance.kwargs[self.name] = convert(conv, value, self.name)
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
        args = [convert(leaf, text, self.name)
                for leaf, text in zip(leaves, texts)]
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
            instance.init(_default(self.owner, self.name))
            self.owner.kwargs[self.name] = instance
        opargs = []
        if value is not None:                           # =value / attached
            if not self.converters:                     # a 0-arity fold (counter)
                raise UsageError(
                    f"option {self.name!r} doesn't take a value", None)
            opargs = [convert(self.converters[0], value, self.name)]
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
                opargs.append(convert(converter, processor.advance(), self.name))
        try:
            instance.option(*opargs)
        except (ValueError, TypeError) as e:    # match the interpreter's wrap
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
        obj.kwargs[self.name] = convert(self.converter, value, self.name)


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
    _fingerprint = None                 # signature hash the emitter bakes;
                                        # None in the in-memory build (no drift)
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
        Wire the callable onto the class, verify it hasn't drifted from what
        was compiled, and wire every child converter it reaches (via the
        generated _fixup_children, guarded so a shared child wires once).
        Called from @command; the 1:1 mapping makes the class the callable's
        home.
        """
        cls.converter = converter
        if (cls._fingerprint is not None
                and signature_fingerprint(converter) != cls._fingerprint):
            raise ConfigurationError(
                f"the compiled parser is stale: command "
                f"{converter.__name__!r} changed since it was generated; "
                f"regenerate the compiled module")
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
    def __init__(self, argv, root, commands=()):
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
            arg.owner.kwargs[arg.name] = convert(arg.converter, raw, arg.name)
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
                    arg.owner.args.append(convert(arg.converter, raw, arg.name))
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
                arg.owner.args.append(convert(arg.converter, tok, arg.name))
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
            arg.owner.args.append(convert(arg.converter, tok, arg.name))
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


def appeal_class(baked_fingerprint=None, default_mappings_fp=None,
                 default_options_fp=None):
    """
    Build the Appeal class a compiled parser module wears.  A fresh class
    per module (so each program's registered Converters stay its own): the
    generated module does `Appeal = appeal_class(baked_fingerprint=...)` and
    decorates its Converter subclasses with @Appeal._converter(name).
    `baked_fingerprint` is the compile-time configuration identity;
    default_mappings_fp/default_options_fp are the compile-time policy
    fingerprints the sentinel default falls back to.  Appeal.__init__
    recomputes the identity and raises on drift.
    """
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
        Converters = {}

        def __init__(self, name=None, *, version=None, repeat=False,
                     default_mappings=_CONFIG_DEFAULT,
                     default_options=_CONFIG_DEFAULT,
                     stylesheet=None, margin=79, errors=None,
                     positional_argument_usage_format='<{name.upper()}>',
                     doc=None):
            self.name = name
            self.version = version
            self.repeat = repeat            # cycle commands left to right
            self.commands = {}
            self.precommands = []           # ordered precommand eras (run first)
            # config kept for reconstruct (help/error render through full Appeal)
            self.default_mappings = default_mappings
            self.default_options = default_options
            self.stylesheet = stylesheet
            self.margin = margin
            self.errors = errors
            self.positional_argument_usage_format = positional_argument_usage_format
            self.doc = doc
            if baked_fingerprint is not None:
                # sentinel default -> the compile-time policy's baked fp;
                # an explicit value -> its live fingerprint
                dm_fp = (default_mappings_fp
                         if default_mappings is _CONFIG_DEFAULT
                         else _callable_fingerprint(default_mappings))
                do_fp = (default_options_fp
                         if default_options is _CONFIG_DEFAULT
                         else _callable_fingerprint(default_options))
                current = config_fingerprint(version, dm_fp, do_fp)
                if current != baked_fingerprint:
                    raise ConfigurationError(
                        f"{name or 'this program'}: the compiled parser is "
                        f"stale -- the Appeal configuration changed since it "
                        f"was generated; regenerate the compiled module")

        @classmethod
        def _converter(cls, name):
            def _converter(converter):
                cls.Converters[name] = converter
                return converter
            return _converter

        def command(self, name=None):
            def command(converter):
                # mirror full Appeal: the function name maps _->- (an explicit
                # name is verbatim), and a command word can't start with a dash
                word = (name if name is not None
                        else converter.__name__.replace('_', '-'))
                if word.startswith('-'):
                    raise ConfigurationError(
                        f"a command name can't start with a dash: {word!r}")
                cls = self.Converters[word]
                cls.fixup_converters(converter)     # wire cls + its children
                self.commands[word] = cls
                return converter
            return command

        def precommand(self, *, index=-1):
            def precommand(converter):
                cls = self.Converters[converter.__name__]
                cls.fixup_converters(converter)     # wire cls + its children
                if index == -1:                     # an ordered era; runs first,
                    self.precommands.append(cls)    # before commands
                else:
                    self.precommands.insert(index, cls)
                return converter
            return precommand
        global_command = precommand     # transitional alias for the old name

        def process(self, args):
            return execute(self.commands, list(args),
                           precommands=self.precommands, repeat=self.repeat)

        def main(self, args=None):
            args = sys.argv[1:] if args is None else list(args)
            try:
                result = self.process(args)
            except UsageError as e:
                print(f'{self.name or "error"}: {e}', file=sys.stderr)
                return 2
            return result if isinstance(result, int) else 0

    return Appeal
