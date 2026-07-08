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
class AppealConfigurationError(Exception):
    """
    Raised at *build* time: the program's signature (or a request,
    like standalone emission) doesn't make sense.  Always names the
    offender.  Generated parsers never raise it--but the converter
    vocabulary below does, so it travels with the file.
    """


class UsageError(Exception):
    """
    Something is wrong with the *command line* (not the program).
    Printing it shows the message and the command's usage.
    """
    def __init__(self, message, usage=None):
        super().__init__(message)
        self.usage = usage


class AppealError(Exception):
    """
    A runtime failure that should stop the program with a message
    but *without* usage (the user's command line was fine).
    """
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
            usage) from None
# --8<-- end appeal convert --8<--


# --8<-- start appeal parse tokens --8<--
# --8<-- requires appeal exceptions --8<--
def parse_tokens(argv, options, usage=None, command_split=None,
                 positions=None):
    """
    The driver: split argv into plain operands and option values.

    options maps each option string to (key, kind), where key is
    the option's canonical name and kind is 'flag', 'value', or
    'multi'.  Returns (operands, given): operands is a list of
    strings; given maps keys to True (flags), a raw string
    (values), or a list of raw strings (multi).  Options that
    aren't repeatable kinds error when given twice (v1 semantics).

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

    def record(key, kind, value):
        if positions is not None and key not in positions:
            positions[key] = len(operands)
        if kind.startswith('w:'):
            # a *args group's option: binding to an instance
            # happens later, by operand position (window_options)
            given.setdefault(key, []).append((len(operands), kind[2:], value))
            return
        if kind in ('flag', 'value', 'fold1', 'group'):
            # v1 semantics, probed: an option that isn't a
            # repeatable kind may be given at most once
            if key in given:
                raise UsageError(
                    f"option {key} specified more than once", usage)
            given[key] = True if kind == 'flag' else value
        else:   # 'multi' collects raw strings; 'fold' tuples of them
            given.setdefault(key, []).append(value)

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
                raise UsageError(f"unknown option {name_part!r}", usage)
            key, kind = entry[0], entry[1]
            base = kind[2:] if kind.startswith('w:') else kind
            nargs = entry[2] if len(entry) > 2 else 1
            if base == 'flag' or (base in ('fold', 'fold1') and nargs == 0):
                if equals:
                    raise UsageError(
                        f"option {name_part!r} doesn't take a value", usage)
                record(key, kind, True if base == 'flag' else ())
                continue
            if base == 'group' and nargs == 0:
                # a group of all-optional operands: bare, or given
                # one inline operand via '='
                record(key, kind, (value_part,) if equals else ())
                continue
            if equals:
                if nargs != 1:
                    raise UsageError(
                        f"option {name_part!r} takes {nargs} values "
                        f"and can't use '='", usage)
                record(key, kind,
                       (value_part,) if base in ('fold', 'fold1', 'group')
                       else value_part)
                continue
            values = []
            for value in it:
                values.append(value)
                if len(values) == nargs:
                    break
            if len(values) < nargs:
                raise UsageError(
                    f"option {name_part!r} requires "
                    f"{'a value' if nargs == 1 else f'{nargs} values'}", usage)
            record(key, kind,
                   tuple(values)
                   if (base in ('fold', 'fold1', 'group') or nargs != 1)
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
            base = kind[2:] if kind.startswith('w:') else kind
            nargs = entry[2] if len(entry) > 2 else 1
            if base == 'group' and nargs == 0:
                # optional-operand groups allow the concatenated
                # form: -fjoe (and -f=joe) hand 'joe' to the group.
                # But if every remaining character is itself a known
                # short option, this is an ordinary bundle: -me is
                # m-then-e, not m('e')  (v1).
                rest = chars[index + 1:]
                bundled = (not rest
                           or (not rest.startswith('=')
                               and all(('-' + ch) in options for ch in rest)))
                if bundled:
                    record(key, kind, ())
                    continue
                if rest.startswith('='):
                    rest = rest[1:]
                record(key, kind, (rest,))
                break
            if base == 'flag' or (base in ('fold', 'fold1') and nargs == 0):
                record(key, kind, True if base == 'flag' else ())
                continue
            # -p=8: the remainder after '=' is the value
            rest = chars[index + 1:]
            if rest.startswith('=') and nargs == 1:
                value = rest[1:]
                record(key, kind,
                       (value,) if base in ('fold', 'fold1', 'group')
                       else value)
                break
            # a short option that takes values must be last in a
            # bundle; its values are the next tokens
            if index != len(chars) - 1:
                raise UsageError(
                    f"option {'-' + c!r} takes a value and "
                    f"must be last in a bundle", usage)
            values = []
            for value in it:
                values.append(value)
                if len(values) == nargs:
                    break
            if len(values) < nargs:
                raise UsageError(
                    f"option {'-' + c!r} requires "
                    f"{'a value' if nargs == 1 else f'{nargs} values'}", usage)
            record(key, kind,
                   tuple(values)
                   if (base in ('fold', 'fold1', 'group') or nargs != 1)
                   else values[0])

    if command_split is not None:
        return operands, given, []
    return operands, given
# --8<-- end appeal parse tokens --8<--


# --8<-- start appeal command set --8<--
# --8<-- requires appeal exceptions --8<--
def run_command_set(argv, parse_globals, commands, usage=None,
                    default=None):
    """
    Dispatch for a multi-command program: run the global command
    over the tokens before the command word (if there is a global
    command), then the named command over the tokens after it.
    A truthy result from the global command halts dispatch and is
    the program's result (v1's contract: an early exit code).
    `default`, if given, handles an empty line instead of the
    "no command specified." error (v1's default_command).
    """
    if parse_globals is not None:
        result, rest = parse_globals(argv)
        if isinstance(result, int) and not isinstance(result, bool) and result:
            # v1's early-exit contract: a nonzero int halts dispatch
            # (other truthy returns don't--the corpus's parent
            # commands return strings and dispatch proceeds)
            return result
    else:
        rest = list(argv)
    if not rest:
        if default is not None:
            return default([])
        raise UsageError("no command specified.", usage)
    word = rest[0]
    parse = commands.get(word)
    if parse is None:
        raise UsageError(f"unknown command {word!r}", usage)
    return parse(rest[1:])
# --8<-- end appeal command set --8<--


# --8<-- start appeal option protocol --8<--
# --8<-- requires appeal convert --8<--
# --8<-- requires appeal exceptions --8<--
class Option:
    """
    Subclass to define an option with custom behavior.  The
    protocol (v1's, kept):

      * init(default) -- called once, with the parameter's default;
      * option(...)   -- called when the option is given; its
        signature defines the option's operands (each parameter
        one operand, converted per its annotation);
      * render()      -- the final value passed to the command.

    An Option may be given at most once ("specified more than
    once" otherwise); subclass MultiOption to allow repetition.
    If the option is never given, the class is never instantiated:
    the parameter's default passes through untouched.
    """
    def init(self, default):
        pass

    def option(self):
        raise NotImplementedError

    def render(self):
        raise NotImplementedError


class MultiOption(Option):
    """
    An Option that may be given any number of times: option() is
    called once per occurrence, in command-line order.
    """


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
            raise UsageError(f"{name}: {e}", usage) from None
    return instance.render()
# --8<-- end appeal option protocol --8<--


# --8<-- start appeal windows --8<--
# --8<-- requires appeal exceptions --8<--
def window_options(occurrences, first, arity, count, name, usage=None,
                   gate=0):
    """
    Bind a *args group's option occurrences to instances.  An
    occurrence at operand position p configures the instance being
    built or about to be built--instance (p - first) // arity--and
    past the ends of the line it binds to the nearest instance
    (with sizes A B C, the positions 1 A 2 B 3 C 4 map to
    A, B, C, C).  Position selects; it never rejects.  The only
    error left is an option with no instances at all.
    Returns one given-style dict per instance.
    """
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
            j = (position - first) // arity
            if j < 0:
                j = 0
            if j >= count:
                j = count - 1
            given = givens[j]
            if kind in ('flag', 'value', 'fold1', 'group'):
                if key in given:
                    raise UsageError(
                        f"option {key} specified more than once", usage)
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
            f"(not a valid {fn_name})", usage) from None
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
                usage)
        key = convert(key_converter, key_text, name, usage)
        if key in result:
            raise UsageError(
                f"{name}: key {key_text!r} defined more than once", usage)
        result[key] = convert(value_converter, value_text, name, usage)
    return result
# --8<-- end appeal collect mapping --8<--


# --8<-- start appeal check count --8<--
# --8<-- requires appeal exceptions --8<--
def check_count(n, minimum, maximum, valid_counts, usage=None):
    """
    The exact-arity error, phrased as English, computed from the
    valid-count set.
    """
    if valid_counts is not None:
        if n in valid_counts:
            return
        counts = sorted(valid_counts)
        if len(counts) == 1:
            wanted = str(counts[0])
        else:
            wanted = ', '.join(str(c) for c in counts[:-1]) + f' or {counts[-1]}'
        raise UsageError(
            f"wrong number of arguments: got {n}, expected {wanted}", usage)
    if n < minimum:
        raise UsageError(
            f"wrong number of arguments: got {n}, expected at least {minimum}",
            usage)
# --8<-- end appeal check count --8<--


# --8<-- start appeal run main --8<--
# --8<-- requires appeal exceptions --8<--
def run_main(parse, argv=None):
    """
    The main() driver for a generated parser: parse and execute,
    print errors the polite way, return the exit code.
    """
    if argv is None:
        argv = sys.argv[1:]
    try:
        result = parse(list(argv))
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        if e.usage:
            print(f"usage: {e.usage}", file=sys.stderr)
        return 2
    except AppealError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
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
## between the scissors markers, re-synced by tools/sync_text.py.
## The one authoritative copy of this code is big's.
##


# --8<-- start appeal export shim --8<--
def export(fn):
    # big's @export maintains its __all__; here it's a no-op
    return fn
# --8<-- end appeal export shim --8<--


# --8<-- start big _iterate_over_bytes --8<--

def _iterate_over_bytes(b):
    # this may not actually iterate over bytes.
    # for example, we iterate over apostrophes and double_quotes
    # for gently_title, and those might be strings or bytes,
    # or iterables of strings or bytes.
    if isinstance(b, bytes):
        return (b[i:i+1] for i in range(len(b)))
    return iter(b)

# --8<-- end big _iterate_over_bytes --8<--

# --8<-- start big toy multisplit --8<--

def _toy_multisplit(s, separators):
    """
    A toy version of multisplit.  It lives here so the test
    suite can validate multisplit against it--the two must always
    agree--and so I can borrow it in other projects, instead of
    borrowing all of multisplit.  Deliberately not exported;
    the test suite imports it by hand.

    s is a str or bytes.
    separators is a str or iterable of str,
      or bytes or iterable of bytes.

    Returns a list equivalent to
        list(big.multisplit(s, separators, keep=ALTERNATING, separate=True))

    (Doesn't support any other arguments--maxsplit etc.)

    This is my second version of toy_multisplit, a needless
    (but fun to write) optimized improvement over the original.
    (You'll find toy_multisplit_original later in the file,
    lovingly preserved for posterity.)

    toy_multisplit is *usually* faster than toy_multisplit_original,
    and it's *way* faster when there are lots of separators--or exactly
    one separator.  it's only a bit slower than toy_multisplit_original
    when there are only a handful of separators, and even then it's
    only sometimes, and it's not a lot slower.

    And it turns out: toy_multisplit is a lot faster than the real
    multisplit!  I guess that's the price you pay for regular expressions,
    and general-purpose code.  (Though it does make me think... a couple
    of specialized versions of multisplit we dispatch to for the most
    common use cases might speed things up quite a bit!)
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
        return segments

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

    return segments

# --8<-- end big toy multisplit --8<--


# --8<-- start big linebreaks --8<--
export('str_linebreaks')
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
export('str_linebreaks_without_crlf')
str_linebreaks_without_crlf = tuple(s for s in str_linebreaks if s != '\r\n')

export('linebreaks')
linebreaks = str_linebreaks
export('linebreaks_without_crlf')
linebreaks_without_crlf = str_linebreaks_without_crlf

# Whitespace as defined by Unicode.  The same as Python's definition,
# except we again remove the four ASCII separator characters.
export('unicode_linebreaks')
unicode_linebreaks = tuple(s for s in str_linebreaks if not ('\x1c' <= s <= '\x1f'))
export('unicode_linebreaks_without_crlf')
unicode_linebreaks_without_crlf = tuple(s for s in unicode_linebreaks if s != '\r\n')

# Linebreaks as defined by ASCII.  The same as Unicode,
# but only within the first 128 code points.
# Note: these are still *str* objects.
export('ascii_linebreaks')
ascii_linebreaks = tuple(s for s in unicode_linebreaks if s < '\x80')
export('ascii_linebreaks_without_crlf')
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

export('bytes_linebreaks')
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

export('bytes_linebreaks_without_crlf')
bytes_linebreaks_without_crlf = tuple(s for s in bytes_linebreaks if s != b'\r\n')
# --8<-- end big linebreaks --8<--

# --8<-- start big word wrap trio --8<--
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


@export
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


def _normalize_indents(indent, name, margin, tab_width, left_column, indent_type):
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
        column = len(i)
        if column >= margin:
            raise ValueError(f"{name} {i!r} leaves no room for words inside margin {margin}")
        append(column)

    return tuple(expanded), columns


@export
def wrap_words(words, margin=79, *, code_indent=None, indent='', left_column=1, tab_width=8, two_spaces=True):
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

    def next_tab_stops(col, tabs):
        # the 0-based line offset of the next word, after
        # advancing 'tabs' tab stops from the offset 'col'.
        # tab stops live at fixed 1-based columns of the page:
        # 1 + (k * tab_width).
        absolute = left_column + col
        for _ in range(tabs):
            absolute += tab_width - ((absolute - 1) % tab_width)
        return absolute - left_column

    for word in words:
        if first_word:
            first_word = False
            if isinstance(word, bytes):
                empty = lastword = b''
                sentence_ending_punctuation = (b'.', b'?', b'!')
                space1 = b' '
                space2 = b'  '
                linebreak = b'\n'
                tab = b'\t'
            else:
                empty = lastword = ''
                sentence_ending_punctuation = ('.', '?', '!')
                space1 = ' '
                space2 = '  '
                linebreak = '\n'
                tab = '\t'
            if indent or (code_indent is not None):
                indent_type = bytes if isinstance(word, bytes) else str
                if indent:
                    indents, widths = _normalize_indents(
                        indent, 'indent', margin, tab_width, left_column, indent_type)
                else:
                    indents, widths = (empty,), (0,)
                last_indent = len(indents) - 1

                if code_indent is None:
                    code_indents = indents
                    code_widths = widths
                    last_code_indent = last_indent
                else:
                    code_indents, code_widths = _normalize_indents(
                        code_indent, 'code_indent', margin, tab_width, left_column, indent_type)
                    last_code_indent = len(code_indents) - 1

        if word.isspace():
            if word == tab:
                # a tab word: not a line break, not a paragraph
                # break--column advancement, resolved when we
                # place the next word.
                pending_tabs += 1
                continue
            lastword = word
            append(word)

            pending_tabs = 0
            new_line = True
            col = 0

            new_paragraph = len(word) > 1
            if not new_paragraph:
                line_number += 1
            continue

        if new_paragraph:
            new_paragraph = False
            code_paragraph = word[:1].isspace()
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
        l = len(word)
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
                    append(linebreak)
                    new_line = True
                    line_number += 1
                    tabs = 0
                else:
                    append(space1 * (target - col))
                    col = target
            elif col:
                if two_spaces and lastword.endswith(sentence_ending_punctuation):
                    space = space2
                    len_space = 2
                else:
                    space = space1
                    len_space = 1

                wrap = (col + len_space + l) > margin
                if wrap:
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
        col += len(word)
        lastword = word

    if first_word:
        raise ValueError("no words to wrap")

    s = empty.join(text)
    return s


@export
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


@export
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

@export
def merge_columns(*columns, column_separator=None,
    overflow_strategy=OverflowStrategy.RAISE,
    overflow_before=0,
    overflow_after=0,
    tab_width=8,
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
    """
    assert overflow_strategy in (OverflowStrategy.INTRUDE_ALL, OverflowStrategy.DELAY_ALL, OverflowStrategy.RAISE)
    raise_overflow_error = overflow_strategy == OverflowStrategy.RAISE
    delay_all = overflow_strategy == OverflowStrategy.DELAY_ALL

    assert columns
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

            length = len(line)
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
                line = line.ljust(max_width)
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


# --8<-- start big format definition list --8<--
# --8<-- requires big word wrap trio --8<--

_default_definition_list_indent = '  '
_default_definition_list_spacer = '  '

@export
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

# --8<-- end big format definition list --8<--


##
## help and usage rendering
##
## Formatting happens at run time: bake the formatter, not the
## text.  Built on the word-wrap trio above.
##


# --8<-- start appeal help --8<--
# --8<-- requires appeal export shim --8<--
# --8<-- requires big word wrap trio --8<--
# --8<-- requires big format definition list --8<--
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
## Templates (proposal §8.7.1).  A template is a name and a value,
## both strings.  Master templates are ordinary format strings,
## rendered line by line so the empty-line rule can apply.  Section
## templates are read structurally: the literal text around their
## two {argument}/{documentation} pairs configures the definition
## list--and any literal lines before the first pair are the
## section's heading, so an empty section takes its heading with
## it by construction.
##

default_templates = {
    'help': (
        '{summary}\n'
        '\n'
        '{usage}\n'
        '\n'
        '{documentation}\n'
        '\n'
        '{arguments}\n'
        '\n'
        '{options}\n'
    ),
    'help commands': (
        '{summary}\n'
        '\n'
        '{usage}\n'
        '\n'
        '{documentation}\n'
        '\n'
        '{commands}\n'
    ),
    'arguments': (
        'Arguments:\n'
        '    {argument}  {documentation}\n'
        '    {argument}  {documentation}\n'
    ),
    'options': (
        'Options:\n'
        '    {argument}  {documentation}\n'
        '    {argument}  {documentation}\n'
    ),
    'commands': (
        'Commands:\n'
        '    {argument}  {documentation}\n'
        '    {argument}  {documentation}\n'
    ),
}


def parse_section_template(name, template):
    """
    Reads a section template's structure.  The template contains
    exactly two {argument}/{documentation} pairs, alternating,
    {argument} first.  A pair whose placeholders share a line is
    the definition-list form: the literal text before {argument}
    is the indent, the text between them is the spacer.  Literal
    lines before the first pair are the heading.  The text between
    the first {documentation} and the second {argument} is the
    separator between items.

    Returns (heading, indent, spacer, separator).  Malformed
    templates raise AppealConfigurationError naming the template.
    """
    def fail(why):
        raise AppealConfigurationError(f"template {name!r}: {why}")

    heading, found, rest = template.partition('{argument}')
    if not found:
        fail("no {argument} placeholder")
    heading, _, indent = heading.rpartition('\n')
    if heading:
        heading += '\n'
    spacer, found, rest = rest.partition('{documentation}')
    if not found:
        fail("{argument} without a {documentation} after it")
    if '\n' in spacer:
        fail("the hanging form isn't supported yet; put {argument} "
             "and {documentation} on one line")
    separator, found, rest = rest.partition('{argument}')
    if not found:
        fail("a section template contains exactly two "
             "{argument}/{documentation} pairs; found one")
    spacer2, found, rest = rest.partition('{documentation}')
    if not found or spacer2 != spacer:
        fail("the second pair must match the first")
    if separator != '\n' + indent:
        fail("items render adjacently for now: the second pair "
             "must start on the very next line, indented like "
             "the first")
    if '{argument}' in rest or '{documentation}' in rest:
        fail("a section template contains exactly two "
             "{argument}/{documentation} pairs; found more")
    return heading, indent, spacer, separator


def render_section(name, template, rows, margin=79):
    """
    Renders one section--rows of (display, documentation-lines)--
    through its template, laying out the definition list with
    format_definition_list.  No rows renders as the empty string,
    heading and all.
    """
    if not rows:
        return ''
    heading, indent, spacer, separator = parse_section_template(name, template)
    pairs = [(display, '\n'.join(lines)) for display, lines in rows]
    body = format_definition_list(pairs, margin, indent=indent, spacer=spacer)
    return heading + body


def render_page(template_name, template, values, margin=79):
    """
    Renders a master template: an ordinary format string, rendered
    line by line.  A line containing at least one placeholder, ALL
    of which rendered empty, is dropped; runs of three-plus
    newlines collapse to two; the result is rstripped and given a
    final newline.
    """
    import string as _string
    formatter = _string.Formatter()
    lines = []
    for line in template.split('\n'):
        try:
            fields = [f for _, f, _, _ in formatter.parse(line) if f is not None]
            rendered = line.format_map(values)
        except KeyError as e:
            raise AppealConfigurationError(
                f"template {template_name!r} names an unknown "
                f"field: {e}")
        except ValueError as e:
            raise AppealConfigurationError(
                f"template {template_name!r}: {e}")
        if fields and all(not values.get(f) for f in fields):
            continue
        lines.append(rendered)
    text = '\n'.join(lines)
    while '\n\n\n' in text:
        text = text.replace('\n\n\n', '\n\n')
    return text.lstrip('\n').rstrip() + '\n'


def render_help_page(usage, corpus, templates, margin=79):
    """
    The --help page: assembles the master template's values from a
    predigested corpus (see help.merge_docs) and renders it.  The
    'help commands' master serves when the corpus has command
    rows; 'help' otherwise.
    """
    def prose(lines):
        if not lines:
            return ''
        return wrap_words(split_text_with_code('\n'.join(lines)), margin)

    values = {
        'summary': prose(corpus['summary']),
        'usage': render_usage(usage, margin),
        'documentation': prose(corpus['documentation']),
        'arguments': render_section('arguments', templates['arguments'],
                                    corpus['arguments'], margin),
        'options': render_section('options', templates['options'],
                                  corpus['options'], margin),
        'commands': render_section('commands', templates['commands'],
                                   corpus['commands'], margin),
    }
    name = 'help commands' if corpus['commands'] else 'help'
    return render_page(name, templates[name], values, margin)


def render_command_listing(usage, corpus, templates, margin=79):
    """
    The compact command listing: the usage line plus the commands
    table, no prose.  This is the `usage` string attached to
    dispatch-level UsageErrors--helpful enough to name the valid
    commands, terse enough for an error.
    """
    listing = render_section('commands', templates['commands'],
                             corpus['commands'], margin)
    return usage + '\n\n' + listing.rstrip('\n')
# --8<-- end appeal help --8<--


##
## the converter vocabulary
##
## v1's converter vocabulary: split, validate, validate_range,
## counter, accumulator, mapping.  All semantics probed against
## shipping v1 0.6.4.  Factory *products* carry a recipe string
## (__appeal_recipe__): a standalone script re-runs the factory,
## so closures and dynamic classes survive emission--the north
## star holds without importing appeal.
##


# --8<-- start appeal recipe repr --8<--
def _recipe_repr(*args, **kwargs):
    bits = [repr(a) for a in args]
    bits.extend(f'{k}={v!r}' for k, v in kwargs.items())
    return ', '.join(bits)
# --8<-- end appeal recipe repr --8<--


# --8<-- start appeal split --8<--
# --8<-- requires appeal exceptions --8<--
# --8<-- requires appeal recipe repr --8<--
# --8<-- requires big toy multisplit --8<--
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
        # _toy_multisplit returns the raw alternating form:
        # text, separator, text, ... with empty texts between
        # adjacent separators.  keep the texts; drop the interior
        # empties (adjacent separators count as one); and with
        # strip, drop the boundary empties too (leading and
        # trailing separators).
        texts = _toy_multisplit(value, list(separators))[::2]
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
        f'split({_recipe_repr(*separators, strip=strip)})'
        if strip else f'split({_recipe_repr(*separators)})')
    split_converter.__appeal_snippet__ = 'appeal split'
    return split_converter
split.__appeal_factory__ = "split(':')"
# --8<-- end appeal split --8<--


# --8<-- start appeal validate --8<--
# --8<-- requires appeal exceptions --8<--
# --8<-- requires appeal recipe repr --8<--
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
    validate_converter.__appeal_recipe__ = f'validate({_recipe_repr(*values)})'
    validate_converter.__appeal_snippet__ = 'appeal validate'
    return validate_converter
validate.__appeal_factory__ = "validate('red', 'green')"
# --8<-- end appeal validate --8<--


# --8<-- start appeal validate range --8<--
# --8<-- requires appeal recipe repr --8<--
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
    recipe_args = _recipe_repr(start, stop)
    if clamp:
        recipe_args += ', clamp=True'
    validate_range_converter.__appeal_recipe__ = (
        f'validate_range({recipe_args})')
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
        __appeal_recipe__ = (f'counter(max={ceiling!r}, step={step!r})')
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


# --8<-- start appeal folds --8<--
# --8<-- requires appeal option protocol --8<--
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


class accumulator(MultiOption):
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

    def __class_getitem__(cls, types):
        if not isinstance(types, tuple):
            types = (types,)
        sub = type('accumulator', (cls,),
                   {'option': _folder('accumulator', types)})
        sub.__appeal_recipe__ = (
            f'accumulator[{", ".join(t.__name__ for t in types)}]')
        return sub


class mapping(MultiOption):
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

    def __class_getitem__(cls, types):
        if not isinstance(types, tuple) or len(types) < 2:
            raise TypeError("mapping[...] needs at least a key type "
                            "and one value type")
        sub = type('mapping', (cls,),
                   {'option': _folder('mapping', types)})
        sub.__appeal_recipe__ = (
            f'mapping[{", ".join(t.__name__ for t in types)}]')
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
