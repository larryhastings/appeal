#!/usr/bin/env python3
#
# appeal/converters.py
# Part of Appeal 1.0.
#
# The converter vocabulary: the Option protocol, convert(), and the
# built-in converter factories (split, validate, validate_range, counter,
# file, optional, accumulator, mapping).  Low-level -- depends only on the
# exceptions.

import sys
from . import (AppealConfigurationError, ConfigurationError,
               AppealDataError, DataError, UsageError)


def convert(converter, text, name):
    """
    Run a terminal converter over one operand.  A ValueError or
    TypeError from the converter becomes a UsageError naming
    the parameter and the offending text.  (Appeal-internal: the
    engine's per-operand conversion primitive.  The usage line is
    attached later, at the dispatch boundary--see _run_node.)
    """
    try:
        return converter(text)
    except (ValueError, TypeError) as e:
        converter_name = getattr(converter, '__name__', 'converter')
        detail = str(e) or f'not a valid {converter_name}'
        raise UsageError(
            f"invalid value for {name!r}: {text!r} ({detail})",
            param=name) from None


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


MultiOption = Option


def is_option(annotation):
    "Is this annotation an Option subclass?"
    return isinstance(annotation, type) and issubclass(annotation, Option)


def is_multioption(annotation):
    "A repeatable option class -- now every Option (StrictOption removed)."
    return is_option(annotation)


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


def verbatim(text):
    """
    The operand exactly as the shell handed it over, whatever it looks
    like.  A verbatim slot takes the next token without asking whether
    it's an option, so `count: verbatim` accepts -5 and `*args: verbatim`
    takes the rest of the line for another program (tron run ...):

        @app.command()
        def run(*args: verbatim): ...

    The rule (Larry, 2026-09-09): once a verbatim *args has taken its
    first token, nothing later on the line is an option, and `--` is
    taken like any other token.  Before that, `--` is the usual marker.
    """
    return text


def counter(delta=1, clamp=None):
    """
    Creates a repeatable flag-like option that accumulates: it starts
    at the parameter's own default, and every occurrence adds `delta`.
    The classic is `-v -v -v` with the default `delta=1`, giving 3.

    `delta` needn't be a number--anything the running value supports
    with `+` works, so `counter('ba')` on a parameter defaulting to
    'a' spells 'a', 'aba', 'ababa'.

    `clamp` (default None: no clamping) is a BARRIER the value stops
    on, approached from either side--so it caps a counter that climbs
    (`counter(1, 2)` gives 1, 2, 2, 2...) and floors one that falls
    (`counter(-1, 0)` stops dead at 0).  No direction to declare, and
    nothing numeric assumed when you don't use it.
    """

    class Counter(MultiOption):
        recipe = True

        def init(self, default):
            self.value = default

        def option(self):
            value = self.value
            new_value = value + delta
            if clamp is not None:
                # the barrier: pin the value when this step would
                # carry it ACROSS clamp, whichever way it's heading
                is_greater = (value <= clamp) and (new_value > clamp)
                is_less = (value >= clamp) and (new_value < clamp)
                if is_greater or is_less:
                    new_value = clamp
            self.value = new_value

        def __call__(self):
            return self.value
    Counter.__name__ = 'counter'
    return Counter


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
