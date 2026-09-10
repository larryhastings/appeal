#!/usr/bin/env python3
#
# appeal/frontend.py
# Part of Appeal 1.0.
#
# The front end: a callable's signature -> the Plan (the grammar IR).
# Featherweight signature reading, the Plan tree, and build_plan's
# semantic analysis.  The back end (appeal/backend.py) consumes the Plan.

"""
A featherweight stand-in for inspect.signature.

Importing `inspect` costs ~7ms and drags in ~38 modules (dis, tokenize, ast,
annotationlib, ...).  Appeal only needs a callable's parameters -- their names,
kinds, defaults, and annotations -- which live right on the code object.  This
module reads them directly, imports in microseconds, and pulls in nothing.

The surface mirrors the slice of inspect Appeal uses: `signature(callable)`
returns a Signature whose `.parameters` is an ordered name->Parameter dict, and
`Parameter` carries the kind singletons (POSITIONAL_ONLY, ...) and `empty`.
"""


class _Empty:
    __slots__ = ()
    def __repr__(self):
        return '<empty>'

empty = _Empty()


class _Kind:
    __slots__ = ('name', 'ordinal')
    def __init__(self, name, ordinal):
        self.name = name
        self.ordinal = ordinal
    def __repr__(self):
        return self.name
    # singletons -> identity is equality; ordering by ordinal for completeness
    def __lt__(self, other):
        return self.ordinal < other.ordinal if isinstance(other, _Kind) else NotImplemented


POSITIONAL_ONLY       = _Kind('POSITIONAL_ONLY', 0)
POSITIONAL_OR_KEYWORD = _Kind('POSITIONAL_OR_KEYWORD', 1)
VAR_POSITIONAL        = _Kind('VAR_POSITIONAL', 2)
KEYWORD_ONLY          = _Kind('KEYWORD_ONLY', 3)
VAR_KEYWORD           = _Kind('VAR_KEYWORD', 4)

# ordinal-indexed, to translate a real inspect._ParameterKind (an IntEnum)
# back to ours when we delegate to the real inspect (see _adopt).
_KINDS = (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD, VAR_POSITIONAL,
          KEYWORD_ONLY, VAR_KEYWORD)


class Parameter:
    __slots__ = ('name', 'kind', 'default', 'annotation')

    # mirror inspect.Parameter's class-level constants, so callers can write
    # Parameter.KEYWORD_ONLY / .empty exactly as with the real inspect
    empty                 = empty
    POSITIONAL_ONLY       = POSITIONAL_ONLY
    POSITIONAL_OR_KEYWORD = POSITIONAL_OR_KEYWORD
    VAR_POSITIONAL        = VAR_POSITIONAL
    KEYWORD_ONLY          = KEYWORD_ONLY
    VAR_KEYWORD           = VAR_KEYWORD

    def __init__(self, name, kind, *, default=empty, annotation=empty):
        self.name = name
        self.kind = kind
        self.default = default
        self.annotation = annotation
    def __repr__(self):
        return f'<Parameter {self.name!r} {self.kind}>'


_CO_VARARGS     = 0x04
_CO_VARKEYWORDS = 0x08
_CO_COROUTINE   = 0x80      # async def: a coroutine function
_CO_ASYNC_GEN   = 0x200     # async def with yield


def _is_coroutine_function(callable):
    "An `async def` (function, or bound method of one)--never a class."
    fn = getattr(callable, '__func__', callable)
    co = getattr(fn, '__code__', None)
    return co is not None and bool(co.co_flags & (_CO_COROUTINE | _CO_ASYNC_GEN))


class Signature:
    __slots__ = ('parameters',)
    empty = empty
    def __init__(self, parameters):
        self.parameters = parameters


    def operand_converters(self, context, what, skip_self=False,
                           allow_defaults=False):
        """
        The per-occurrence operand converters of a callable used as an
        option's grammar: one terminal per parameter, per its
        annotation.  Returns (converters, minimum): with
        allow_defaults, trailing parameters may have defaults--those
        operands are optional, consumed greedily when tokens remain
        (v1)--and minimum counts the required ones.
        """
        parameters = list(self.parameters.values())
        if skip_self:
            parameters = parameters[1:]
        converters = []
        minimum = 0
        for parameter in parameters:
            if parameter.kind not in (inspect.Parameter.POSITIONAL_ONLY,
                                      inspect.Parameter.POSITIONAL_OR_KEYWORD):
                raise AppealConfigurationError(
                    f"{context}: {what} parameter {parameter.name!r} must be "
                    f"positional (fancier signatures await the streaming driver)")
            if parameter.default is inspect.Parameter.empty:
                minimum += 1
            elif not allow_defaults:
                raise AppealConfigurationError(
                    f"{context}: {what} parameter {parameter.name!r} can't have "
                    f"a default here")
            annotation = parameter.annotation
            if annotation is inspect.Parameter.empty:
                annotation = str
            annotation = dereference_annotated(annotation)
            if is_option(annotation):
                # a nested fold: mapping readers hand it sequences
                inner, _ = Fold.converters_of(annotation, context)
                converters.append(Fold.leaf(annotation, inner))
                continue
            converters.append(_leaf_callable(annotation, context))
        return tuple(converters), minimum


class _Uninspectable(Exception):
    "No __code__ to read -- signature() falls back to the real inspect."


def _adopt(sig, inspect):
    """
    Translate a real inspect.Signature into ours, so callers only ever see our
    sentinels.  Real inspect uses its own `empty` (compared with `is`) and its
    own IntEnum kinds; a delegated signature carrying those would slip past our
    `annotation is empty` / `kind is KEYWORD_ONLY` tests and corrupt the build.
    """
    real_empty = inspect.Parameter.empty
    params = {}
    for name, p in sig.parameters.items():
        params[name] = Parameter(
            name, _KINDS[int(p.kind)],
            default=empty if p.default is real_empty else p.default,
            annotation=empty if p.annotation is real_empty else p.annotation)
    return Signature(params)


def _resolve(callable):
    """
    Find the __code__-bearing function behind a callable and whether its first
    positional (self/cls) should be dropped -- mirroring what inspect.signature
    does for classes, methods, and callable instances.  Returns (func, skip).
    Raises _Uninspectable for code-less callables (builtins), which signature()
    hands to the real inspect (Argument Clinic gives most builtins signatures).
    """
    # a plain function / lambda
    if hasattr(callable, '__code__'):
        # an unbound function whose owner binds it: no skip.  A bound method
        # arrives via __func__ below; here __self__ marks a builtin-ish bound
        # callable we treat by dropping the first arg.
        return callable, hasattr(callable, '__self__')

    # a bound (or unbound-descriptor) method
    func = getattr(callable, '__func__', None)
    if func is not None and hasattr(func, '__code__'):
        return func, True

    # a class: __init__ (drop self), else __new__ (drop cls), else no params
    if isinstance(callable, type):
        init = getattr(callable, '__init__', None)
        if init is not object.__init__ and hasattr(init, '__code__'):
            return init, True
        new = getattr(callable, '__new__', None)
        if new is not object.__new__ and hasattr(new, '__code__'):
            return new, True
        if init is object.__init__ and new is object.__new__:
            return None, False                  # a plain class: no parameters
        raise _Uninspectable                    # a builtin type: real inspect

    # a callable instance: its __call__ (drop self)
    call = getattr(type(callable), '__call__', None)
    if call is not None and hasattr(call, '__code__'):
        return call, True

    raise _Uninspectable                        # a builtin/uninspectable callable


def signature(callable):
    # exotic callables that publish their own signature (functools.partial,
    # C accelerators, decorators that set it): defer to real inspect -- rare,
    # and it's already paid for if we're here.
    sig = getattr(callable, '__signature__', None)
    if sig is not None:
        import inspect
        return _adopt(inspect.signature(callable), inspect)

    # a wrapper wearing the face of the function it wraps
    # (functools.wraps sets __wrapped__): the PUBLIC signature is the
    # wrapped function's, so the command's grammar doesn't silently
    # collapse to (*args, **kwargs).  Follow the chain--exactly
    # inspect.signature's rule: recursion walks nested wrappers, and
    # a level's own explicit __signature__ (checked above) wins.
    wrapped = getattr(callable, '__wrapped__', None)
    if wrapped is not None:
        if hasattr(callable, '__self__') and hasattr(callable, '__func__'):
            # a BOUND method whose function is a wrapper: the bound
            # object proxies __wrapped__ to the bare function, which
            # still carries self.  Read the function's public signature
            # and drop self--the binding is the method's, not the
            # wrapper's (Astra R01)
            sig = signature(callable.__func__)
            params = dict(sig.parameters)
            del params[next(iter(params))]      # self: a bound method has one
            return Signature(params)
        return signature(wrapped)

    try:
        func, skip = _resolve(callable)
    except _Uninspectable:
        # a builtin / C callable: hand it to the real inspect, which knows the
        # Argument Clinic signatures most builtins now carry (and raises the
        # same ValueError we would when there genuinely isn't one).
        import inspect
        return _adopt(inspect.signature(callable), inspect)
    if func is None:
        return Signature({})

    co = func.__code__
    # co_posonlyargcount is 3.8+; on 3.6/3.7 there are no positional-only
    # parameters, so it's 0
    posonly = getattr(co, 'co_posonlyargcount', 0)
    pos_or_kw = co.co_argcount - posonly
    has_varargs = bool(co.co_flags & _CO_VARARGS)
    kwonly = co.co_kwonlyargcount
    has_varkw = bool(co.co_flags & _CO_VARKEYWORDS)

    n_positional = posonly + pos_or_kw
    n_params = n_positional + has_varargs + kwonly + has_varkw

    defaults = func.__defaults__ or ()
    n_required_pos = n_positional - len(defaults)
    kwdefaults = func.__kwdefaults__ or {}
    annotations = func.__annotations__
    if any(type(v) is str for v in annotations.values()):
        # a string where an object belongs--`from __future__ import
        # annotations` (PEP 563), or hand-stringized.  Ruled (Larry,
        # 2026-09-03): refused, not evaluated--stringized annotations
        # are going away (PEP 649), and Appeal reads objects.
        raise NotImplementedError(
            "Appeal doesn't support stringized annotations")

    names = co.co_varnames
    params = []

    # positional block (posonly then pos-or-kw), in source order
    for i in range(n_positional):
        name = names[i]
        kind = POSITIONAL_ONLY if i < posonly else POSITIONAL_OR_KEYWORD
        default = empty if i < n_required_pos else defaults[i - n_required_pos]
        params.append(Parameter(name, kind, default=default,
                                annotation=annotations.get(name, empty)))

    # co_varnames orders: positionals, keyword-only, *args, **kwargs
    kw_start = n_positional
    star = kw_start + kwonly
    for j in range(kwonly):
        name = names[kw_start + j]
        params.append(Parameter(name, KEYWORD_ONLY,
                                default=kwdefaults.get(name, empty),
                                annotation=annotations.get(name, empty)))
    idx = star
    if has_varargs:
        name = names[idx]; idx += 1
        # *args sits after keyword-only in co_varnames; place it before them
        params.insert(n_positional,
                      Parameter(name, VAR_POSITIONAL,
                                annotation=annotations.get(name, empty)))
    if has_varkw:
        name = names[idx]
        params.append(Parameter(name, VAR_KEYWORD,
                                annotation=annotations.get(name, empty)))

    if skip and params and params[0].kind in (POSITIONAL_ONLY,
                                              POSITIONAL_OR_KEYWORD):
        params = params[1:]                      # drop self / cls

    return Signature({p.name: p for p in params})

# a microsecond stand-in for the real `inspect` (which costs ~7ms):
# the signature/Parameter slice this module reads, exposed as `inspect`
# so the analysis below reads naturally.
import sys as _sys

# the builtin union spelling (X | None), feature-detected: 3.10+ has it
try:
    _UnionType = type(int | str)
except TypeError:      # pragma: no cover -- 3.6..3.9 only; the 3.6 test
    _UnionType = None  # runs exercise it, where coverage doesn't gate
inspect = _sys.modules[__name__]

class Terminal:
    """
    Marker for a terminal slot's converter: consumes one command-line
    string, converts it with a single callable (str, int, float,
    or any one-string-in callable).
    """
    __slots__ = ('converter',)

    def __init__(self, converter):
        self.converter = converter

    def __repr__(self):
        name = getattr(self.converter, '__name__', repr(self.converter))
        return f'<Terminal {name}>'


class Slot:
    """
    One positional parameter of a rule: an edge to a child.

    name          the Python parameter name
    usage_name    the name shown in usage (dashes, case, etc.)
    child         a Terminal, or another Plan (a nonterminal--not yet
                  implemented in the builder)
    required      computed by analysis (a defaulted parameter may
                  still be promoted to required by grouping)
    default       the default value, or NO_DEFAULT
    repeat        True for *args
    trailing      True for a required leaf operand reserved from the
                  END--one that follows an absorbing (*args) converter,
                  so the absorber leaves room for it (filled last, by
                  keyword)

    """

    __slots__ = ('name', 'usage_name', 'child', 'required', 'default',
                 'repeat', 'trailing', 'barrier')

    def __init__(self, name, usage_name, child, required, default,
                 repeat=False, trailing=False):
        self.name = name
        self.usage_name = usage_name
        self.child = child
        self.required = required
        self.default = default
        self.repeat = repeat
        self.trailing = trailing
        self.barrier = False    # a group CERTAIN to consume operands:
                                # options behind it wait for it (gate rule)

    @property
    def needs_operand(self):
        """
        Does filling this slot consume at least one operand?  A leaf
        always does; a converter GROUP does only if it has a required
        operand of its own (intrinsic minimum >= 1).  A required slot
        whose converter is conjurable (minimum 0) needs nothing from
        the command line, so it must NOT force earlier optionals to
        fill up ahead of it (Larry, 2026-08-21): promotion is for a
        required operand that follows, and a conjurable slot isn't one.
        """
        if isinstance(self.child, Terminal):
            return True                 # a Terminal leaf consumes exactly one
        return self.child.minimum >= 1

    def __repr__(self):
        flags = []
        if self.required: flags.append('required')
        if self.repeat: flags.append('repeat')
        if self.trailing: flags.append('trailing')
        return f'<Slot {self.name} {self.child!r} {" ".join(flags)}>'


def _shallow_copy(obj):
    # copy.copy for a __slots__ object, without importing copy.  Both
    # copied classes (Plan, Slot) assign every slot in __init__, so each
    # attribute is simply present--no hasattr guard.
    new = obj.__class__.__new__(obj.__class__)
    for klass in type(obj).__mro__:
        for name in getattr(klass, '__slots__', ()):
            setattr(new, name, getattr(obj, name))
    return new


NO_DEFAULT = object()


class OptionRule:
    """
    One option of a rule--one keyword-only parameter, or one
    @app.option declaration.  The varieties are subclasses (Larry's
    design, 2026-09-08), each differing in what its converters mean
    and what it consumes:

        Flag         presence stores a value, consumes nothing
        Value        consumes one oparg (last one wins), or several
                     for a multi-parameter converter / tuple[...]
        Fold         a MultiOption--repeatable, each occurrence folded
                     in (counter/accumulator/mapping; list[T] and
                     dict[K, V] compile to it); FoldOnce: an Option,
                     at most once
        Nullary      a zero-argument converter: presence CALLS it
        GroupOption  the option opens a nested Plan (its converter
                     has a grammar of its own)

    strings       the option strings, e.g. ('-v', '--verbose').  The
                  long string is the *key* (option strings are unique
                  within one command, checked at build time).
    name          the Python parameter it fills
    converters    () for a Flag; (T,) for a Value, or (constructor,
                  *leaves) for a multi-oparg one; (cls, *leaves) for
                  a fold; (fn,) for a Nullary; (annotation,) for a
                  GroupOption, whose Plan is .child
    default       the value when the option never appears
    required      the option must appear (a keyword-only parameter
                  with no default; Larry, 2026-09-10): the engine
                  refuses the line without it, usage shows it
                  unbracketed
    config_key    the key config looks this option up under: the
                  parameter name, unless @app.option(config=) says
                  otherwise (Larry, 2026-09-10: a command word wins a
                  collision, so the option gets another key)
    kind          the shape's name, as the schema and the option
                  tables spell it
    """
    __slots__ = ('strings', 'name', 'converters', 'default', 'explicit',
                 'usage_name', 'kwargs_delivered', 'annotation', 'auto_shorts',
                 'required', 'config_key')
    kind = None
    child = None                # a GroupOption's Plan; None elsewhere
    is_flag = False
    consumes_operands = True    # False: presence is the whole story

    def __init__(self, strings, name, converters, default):
        self.strings = strings
        self.name = name
        self.converters = converters
        self.default = default
        self.explicit = False    # True when @app.option supplied the strings
        self.usage_name = None   # @app.parameter metavar override
        self.kwargs_delivered = False   # @app.option into **kwargs:
                                        # omitted from the call when absent
        self.annotation = None   # the parameter's annotation, kept so
                                 # default_options (the option-string
                                 # policy) can see it at finalize time
        self.auto_shorts = ()    # short strings the policy proposes;
                                 # the finalize pass claims each if free
        self.required = False
        self.config_key = name

    @property
    def key(self):
        return self.strings[-1]

    # Each variety answers, where it applies:
    #   operand_converters  the converters for its operands, one per position
    #   oparg_names()       the bare operand names its value(s) render as,
    #                       before the format string decorates them
    #   engine_converter    what the backend binds its strings to
    #   _entry()            its option-table value, before the windowed prefix
    conjure = None              # a converter group's option that can be named
                                # before the group is entered: the PreOption
                                # keyword(s) to pass; None means it can't

    @staticmethod
    def build(name, strings, explicit, annotation, grammar_default,
              default, metavar, build):
        """
        One option's rule, from its (annotation, grammar_default) pair--
        the shared engine behind keyword-only parameters, @app.option
        fresh declarations, and **kwargs-delivered options.
        """
        if annotation is not inspect.Parameter.empty:
            annotation = dereference_annotated(annotation)
            _refuse_bare_factory(annotation, f"option {name!r}")
            # list[T] and dict[K, V] are the modern sugar for the
            # accumulator and mapping MultiOptions--repeatable options
            # collecting into a list or a dict (one KEY=VALUE token each).
            # The MultiOptions work on every Python Appeal supports; the
            # [T] subscript on builtin list/dict is comparatively new.
            # Rewrite the sugar to the MultiOption so each repeatable kind
            # has ONE mechanism (the fold path below), never two.
            _context = f"option {name!r}"
            _origin = _generic_origin(annotation)
            if _origin is list:
                (_t,) = annotation.__args__
                annotation = accumulator[_leaf_callable(_t, _context)]
            elif _origin is dict:
                _k, _v = annotation.__args__
                annotation = mapping[_leaf_callable(_k, _context),
                                     _leaf_callable(_v, _context)]
        context = f"option {name!r}"

        def finish(rule):
            rule.explicit = explicit
            rule.usage_name = metavar
            rule.annotation = annotation
            return rule

        if is_option(annotation):
            converters, fold_minimum = Fold.converters_of(
                annotation, context, allow_defaults=True)
            variety = Fold if is_multioption(annotation) else FoldOnce
            return finish(variety(strings, name, converters, default,
                                  fold_minimum))

        if (annotation is bool) or (
                annotation is inspect.Parameter.empty
                and isinstance(grammar_default, bool)):
            # presence stores `not default` (v1, restored 2026-07-18--
            # Larry's break #1: v2's first cut refused any default but
            # False, wrongly assuming a flag must store True.  v1's
            # rule is better: a default-True flag is how you spell
            # "turn this default-on thing OFF").  The explicit
            # --flag=true/false spellings set the literal value.
            return finish(Flag(strings, name, default,
                               present=not grammar_default))

        if (callable(annotation)
                and not _is_leaf(annotation)
                and getattr(annotation, '__origin__', None) is None
                and not hasattr(annotation, 'factory')
                and annotation is not inspect.Parameter.empty
                and _positional_arity(annotation) == 0
                and _accepts_no_arguments(annotation)):
            # a zero-argument converter: a flag that produces a value
            # by calling it (v1: @app.option('direction', '--north',
            # annotation=north) where north() returns 'north')
            return finish(Nullary(strings, name, (annotation,), default))

        origin = None
        if annotation is not inspect.Parameter.empty:
            origin = _generic_origin(annotation)
            if getattr(annotation, '__origin__', None) is tuple:
                # a multi-operand value option that builds a tuple; same
                # machinery as a multi-param converter, nothing to call
                args = annotation.__args__
                if Ellipsis in args:
                    raise AppealConfigurationError(
                        f"{context}: {annotation!r}--variable-length "
                        f"tuples aren't in the grammar (use list[T] "
                        f"for a repeatable option)")
                if not args or args == ((),):
                    raise AppealConfigurationError(
                        f"{context}: {annotation!r} has no elements")
                converters = (tuple,) + tuple(
                    _leaf_callable(element, context) for element in args)
                return finish(Value(strings, name, converters, default))

        # list[T]/dict[K, V] were rewritten to accumulator/mapping up top,
        # so they never reach here as bare generics--the fold path handled
        # them (one mechanism per repeatable kind, not two).

        if annotation is inspect.Parameter.empty:
            t = type(grammar_default)
            if t in _default_type_converters:
                converters = (t,)
            elif (grammar_default is not None
                  and grammar_default is not inspect.Parameter.empty
                  and not isinstance(grammar_default,
                                     (bool, list, dict, tuple, set, bytes))):
                # v1: a custom-class default infers ITS TYPE as the
                # converter (position=IntAndFloat(0, 0.0) makes
                # --position take an int and a float)
                return OptionRule.build(name, strings, explicit, t,
                                        inspect.Parameter.empty, default,
                                        metavar, build)
            else:
                converters = (str,)
        elif _is_option_group(annotation):
            # a converter with its own grammar--optional parameters
            # and/or options: the option consumes the group's MINIMUM
            # operands inline; inner optional groups enter only when
            # their own options force them (v1's -g gloopy ... -i 1 3.0)
            child = build.bare().plan(annotation)   # its own memo, undecorated
            return finish(GroupOption(strings, name, (annotation,), default,
                                      child))
        elif _is_multiparam_converter(annotation):
            # a converter with several parameters consumes several
            # operands (v1, probed): --where X Y.  All required--a
            # parameter with a default makes the converter a group.
            operand_converters, _ = inspect.signature(annotation).operand_converters(
                context, f'{getattr(annotation, "__name__", "converter")}()')
            converters = (annotation,) + operand_converters
        else:
            converters = (_leaf_callable(annotation, context),)
        return finish(Value(strings, name, converters, default))

    def table_entry(self, windowed=False):
        """
        This option's option-table value.  Folds and groups
        carry their per-occurrence (minimum, maximum) operand
        counts--consumption is greedy to the maximum (v1: an
        optional operand takes the next token unconditionally);
        everything else is a pair or an exact count.
        A windowed option (owned by a *args group) gets a 'w:'-
        prefixed kind and an explicit operand count: occurrences
        are collected with positions and bound to instances later.
        """
        entry = self._entry()
        if not windowed:
            return entry
        kind = entry[1]
        if len(entry) > 2:
            return (entry[0], 'w:' + kind) + tuple(entry[2:])
        return (entry[0], 'w:' + kind, 0 if not self.consumes_operands else 1)

    def __repr__(self):
        return f'<{type(self).__name__} {"/".join(self.strings)} -> {self.name}>'


class Flag(OptionRule):
    "Presence stores `present` (v1: `not default`); --flag=true/false sets it."
    __slots__ = ('present',)
    kind = 'flag'
    is_flag = True
    consumes_operands = False

    def __init__(self, strings, name, default, present):
        super().__init__(strings, name, (), default)
        self.present = present   # v1: `not default`, so a default-True flag
                                 # turns its thing OFF

    @property
    def engine_converter(self):
        return bool

    @property
    def conjure(self):
        return {}

    def _entry(self):
        # a flag entry always carries the value presence stores--never
        # an operand count, flags consume nothing
        return (self.key, 'flag', self.present)


class Value(OptionRule):
    """
    Consumes oparg(s) and converts: (T,) for one; (constructor,
    *leaves) for a multi-parameter converter or a tuple[...].
    """
    __slots__ = ()
    kind = 'value'

    @property
    def operand_converters(self):
        if len(self.converters) > 1:
            return tuple(self.converters[1:])
        return tuple(self.converters)

    def oparg_names(self):
        # a single-operand option echoes its own parameter name
        # (--width -> 'width'); a tuple option falls back to element
        # type names; a multi-parameter converter borrows its
        # parameters' names
        if len(self.converters) <= 1:
            return [self.name]
        if self.converters[0] is tuple:
            return [getattr(c, '__name__', self.name)
                    for c in self.converters[1:]]
        params = inspect.signature(self.converters[0]).parameters.values()
        return [p.name for p in params]

    @property
    def engine_converter(self):
        if len(self.converters) == 1:
            return self.converters[0]
        return self.converters           # multi-oparg: (constructor, *leaves)

    @property
    def conjure(self):
        # a single-oparg value option conjures its group (the mix-in
        # pattern: --log-level debug on a converter that consumes no
        # operands); the PreOption carries the oparg converter
        if len(self.converters) == 1:
            return {'converter': self.converters[0]}
        return None

    def _entry(self):
        if len(self.converters) > 1:
            return (self.key, 'value', len(self.converters) - 1)
        return (self.key, 'value')


class Fold(OptionRule):
    """
    A MultiOption: (cls, *leaves), each occurrence's opargs folded in
    by cls.option(); fold_minimum is how many of them are required.
    """
    __slots__ = ('fold_minimum',)
    kind = 'fold'

    def __init__(self, strings, name, converters, default, fold_minimum):
        super().__init__(strings, name, converters, default)
        self.fold_minimum = fold_minimum

    @property
    def operand_converters(self):
        return tuple(self.converters[1:])

    def oparg_names(self):
        # a fold's per-occurrence opargs are converters[1:]: a counter
        # has NONE (a counting flag -- no metavar), an accumulator one,
        # a mapping two.  One oparg is named after the option; several
        # after their converter types.
        opargs = self.converters[1:]
        if len(opargs) == 1:
            return [self.name]
        return [getattr(c, '__name__', self.name) for c in opargs]

    @property
    def engine_converter(self):
        return self.converters[0]        # the MultiOption class

    @property
    def conjure(self):
        # -v on a Logging mixin: conjure the group and fold the
        # occurrence into its MultiOption
        return {'factory': self.converters[0]}

    def _entry(self):
        n = len(self.converters) - 1
        m = self.fold_minimum if self.fold_minimum is not None else n
        return (self.key, self.kind, m, n)


    @staticmethod
    def converters_of(cls, context, allow_defaults=False):
        """
        An Option/MultiOption subclass: its option() method's signature
        defines the option's per-occurrence operands.  Returns
        (converters, minimum): the class plus one converter per
        parameter, and how many of those parameters are required.
        """
        converters, minimum = inspect.signature(cls.option).operand_converters(
            context, f'{cls.__name__}.option()', skip_self=True,
            allow_defaults=allow_defaults)
        return (cls,) + converters, minimum

    @staticmethod
    def leaf(cls, converters):
        """
        A positional Option/MultiOption: as a terminal converter it folds
        one occurrence from the command line; mapping readers hand it a
        whole sequence of occurrences (v1's corpus)--each a sequence in
        option()'s parameter order, or a mapping keyed by its parameter
        names.  Only mapping readers can supply multi-parameter
        occurrences (one command-line token can't); a token that
        reaches one fails loudly.
        """
        elements = converters[1:]
        parameters = [p for p in
                      inspect.signature(cls.option).parameters][1:]

        def fold_positional(value):
            from collections.abc import Mapping as _Mapping
            if isinstance(value, (list, tuple)):
                occurrences = list(value)
            else:
                occurrences = [value]
            instance = cls()
            instance.init(None)
            for occurrence in occurrences:
                if not elements:
                    instance.option()
                    continue
                if isinstance(occurrence, _Mapping):
                    try:
                        row = [occurrence[name] for name in parameters]
                    except KeyError as e:
                        raise ValueError(
                            f"{cls.__name__} occurrence is missing "
                            f"{e.args[0]!r}") from None
                elif len(elements) == 1:
                    row = [occurrence]
                else:
                    row = list(occurrence) if isinstance(
                        occurrence, (list, tuple)) else None
                    if row is None or len(row) != len(elements):
                        raise ValueError(
                            f"each {cls.__name__} occurrence takes "
                            f"{len(elements)} values")
                instance.option(*[convert(v) for convert, v
                                  in zip(elements, row)])
            return instance()
        fold_positional.__name__ = cls.__name__
        return fold_positional


class FoldOnce(Fold):
    "An Option (not a MultiOption): folds at most once."
    __slots__ = ()
    kind = 'fold1'
    conjure = None


class Nullary(OptionRule):
    "A zero-argument converter: presence CALLS it (--north -> north())."
    __slots__ = ()
    kind = 'nullary'
    consumes_operands = False

    @property
    def engine_converter(self):
        # spelled as a value option with a (constructor,) tuple and no
        # leaves: ValueBinding._multi grabs zero opargs and returns
        # constructor()
        return (self.converters[0],)

    def _entry(self):
        # consumes nothing, like a flag--but refuses '=' (there's no
        # boolean to set; presence IS the value)
        return (self.key, 'nullary')


class GroupOption(OptionRule):
    "The option's converter has a grammar of its own: `child` is its Plan."
    __slots__ = ('child',)
    kind = 'group'

    def __init__(self, strings, name, converters, default, child):
        super().__init__(strings, name, converters, default)
        self.child = child

    @property
    def operand_converters(self):
        return tuple(self.converters)    # the group's callable stands for its
                                         # operands (completion asks it)

    def _entry(self):
        return (self.key, 'group', self.child.minimum, self.child.maximum)


class Plan:
    """
    One grammar rule: one callable, analyzed.

    callable      the thing we'll invoke
    name          its name (for usage and generated code)
    slots         positional Slots, in call order.  Trailing required
                  operands (kwonly-no-default) come after any *args
                  slot, mirroring the signature.
    options       OptionRules, in declaration order
    minimum,      operand-count arity, folded over the slots
    maximum       (maximum is None for unbounded)
    valid_counts  the EXACT set of operand counts the fill rule
                  accepts (simulated, so it can't disagree with the
                  engine).  Unbounded plans (a *args somewhere) list
                  the valid counts BELOW unbounded_from; from there
                  up, every count is valid.
    unbounded_from
                  that threshold, or None for a bounded plan
    """
    __slots__ = ('callable', 'name', 'slots', 'options',
                 'minimum', 'maximum', 'valid_counts', 'unbounded_from',
                 'gated', 'certain', 'var_keyword',
                 'tree_trailing', 'scoped_keys', 'auto_help',
                 'sibling_parents', 'sibling_keys', 'pre_plan', 'argv0',
                 'decoration', 'compiled', 'share', 'immediate')

    # the varieties differ here (Larry's design, 2026-09-08):
    windowed = False        # True: a *args group; options bind by window
    binds = None            # a method/nested-class command: the env key of
                            # the instance it needs (its self, or its parent)
    constructs = None       # a class command: the env key to stash the
                            # instance it builds under

    def __init__(self, callable, name, slots, options):
        # whether this command answers -h/--help (the app's help=
        # knob, stamped at build time; False suppresses the
        # automatic help option entirely).
        self.auto_help = True
        # the operand-placeholder SHAPE: the app stylesheet's
        # argument_decoration entry, stamped by the app at build
        # (None: the stock <NAME>)
        self.decoration = None
        self.gated = False      # True on the top plan: barriers exist somewhere
        self.certain = True     # False: this group might never be entered
        self.var_keyword = None # the **kwargs parameter's name, if any
        # trailing arguments in this whole subtree: they reserve
        # from the END of the command's argument stream, wherever
        # they sit in the tree (the uniform end-reservation rule)
        self.tree_trailing = 0
        # option strings declared by more than one window (a
        # converter reused across sibling slots, or two converters
        # agreeing on the grammar): occurrences record positionally
        # and bind by stream order.  Set on the top plan.
        self.scoped_keys = frozenset()
        # SIBLING OPTION GROUPS (Larry's ruling, 2026-07-18): when
        # every declarer of a scoped key is the direct child of a
        # top-level group OPTION (e1: extras, e2: extras), the
        # windows are announced by the parent options, not by
        # operand position.  sibling_parents: ((parent key,
        # (its scoped child keys...)), ...) in declaration order;
        # sibling_keys: the union.  Child options may summon the
        # FIRST declared sibling into existence; later siblings
        # exist only when announced.  Set on the top plan.
        self.sibling_parents = ()
        self.sibling_keys = frozenset()
        # the precommand's mini plan (program metadata: -V,
        # --version, ...), attached to the global-era plan at
        # compile when default_mappings mapped anything to it.
        self.pre_plan = None
        # the string the program was invoked as, stamped on
        # COMMAND plans at build so the usage line reads
        # `usage: prog go ...` (0.6.4's shape, ruled 2026-07-19)
        # --pasteable into a shell.  Named argv0 (ruled
        # 2026-08-04).
        self.argv0 = None
        self.callable = callable
        self.name = name
        self.slots = slots
        self.options = options
        self.minimum = self.maximum = None      # analyze() fills these
        self.valid_counts = None
        self.unbounded_from = None
        self.compiled = None    # the backend's class, built once (converter_for)
        # the era flags (Larry, 2026-09-08), stamped by the app: share
        # says which neighboring eras recognize this era's options
        # (FORWARDS into the next, BACKWARDS into the previous, True
        # both, PRECOMMAND both but only among precommands, False
        # neither); immediate executes the era before the line is judged
        self.share = False
        self.immediate = False

    def __call__(self, args, kwargs, bound):
        """
        Invoke the callable with the parsed values--the variety's
        calling convention (a method takes its instance, an iterable
        group builds its container...).  `bound` is the instance a
        class-as-app constructed, or None.
        """
        return self.callable(*args, **kwargs)

    @property
    def key(self):
        "The converter's identity for deduplication: one class per callable."
        return self.callable

    def __repr__(self):
        return (f'<{type(self).__name__} {self.name} slots={len(self.slots)} '
                f'options={len(self.options)} '
                f'arity=({self.minimum}, {self.maximum})>')

    def usage(self):
        """
        A one-line usage string, read straight off the tree: the
        program span (the plan's argv0 and command word, else the
        plan's name), then usage_body().
        """
        from big.stylesheet import style, escape_styles
        if self.argv0:
            head = (style('program', escape_styles(self.argv0)) + ' '
                    + style('command', escape_styles(self.name)))
        else:
            head = style('program', escape_styles(self.name))
        return f'{head} {self.usage_body()}'.rstrip()

    def usage_body(self):
        """
        The usage line's body--options and operands, no program span.
        v1's shape, kept: options first (all strings, pipe-joined,
        converter names as metavars), then operands; inside a
        group's brackets, the group's options come first--so every
        bracket reads left-to-right as something you can type
        (announce-first, truth in advertising).  A line that spans
        several eras (the program's, a command's) composes bodies.
        """
        from big.stylesheet import style, escape_styles
        from .presentation import decorate_argument

        def _arg(name):
            # a positional operand or an oparg: the name decorated
            # EARLY into its placeholder (host -> <HOST>, via the
            # argument_decoration transform) and tagged 'argument'
            # (the color role).  An explicit @app.parameter rename
            # rides the same decoration (ruled 2026-08-24: one uniform
            # decoration; the SHAPE follows the app's stylesheet,
            # ruled 2026-09-03).
            return style('argument',
                         decorate_argument(name, self.decoration))

        def name_text(slot):
            return _arg(slot.usage_name)

        def transparent_name(slot):
            # the outer slot's name flows through iff the child
            # consumes exactly one operand AND that terminal wasn't
            # explicitly renamed (usage_name != name means
            # @app.parameter spoke; explicit wins).  Returns the
            # FINAL display span, or None to keep the child's own.
            inner = slot.child.sole_terminal_slot()
            if inner is None:
                return None
            if inner.usage_name != inner.name:
                return None
            return name_text(slot)

        def option_text(o):
            text = _option_body(o)
            return text if o.required else '[' + text + ']'

        def _option_body(o):
            bits = ['|'.join(style('option', escape_styles(s))
                             for s in o.strings)]
            if o.child is not None:
                if getattr(o.child.callable,
                           'borrows_name', False):
                    # an optional[T]-style wrapper: its own
                    # parameter name is plumbing; the metavar is
                    # the OPTION's parameter (or its rename)
                    name = (o.usage_name if o.usage_name is not None
                            else o.name)
                    bits.append(f'[{_arg(name)}]')
                else:
                    bits.append(body_text(o.child))
            elif o.consumes_operands:
                if o.usage_name is not None:
                    bits.append(_arg(o.usage_name))
                else:
                    # an option operand shows the NAME (the
                    # parameter's, or the converter parameters' for a
                    # multi-operand option), through the 'argument' role
                    for name in o.oparg_names():
                        bits.append(_arg(name))
            return ' '.join(bits)

        def slot_text(slot, rename=None):
            child = slot.child
            if isinstance(child, Terminal):
                # rename, when present, is already final display text
                name = rename if rename is not None else name_text(slot)
                if slot.repeat:
                    return f'[{name}]...'
                if slot.required:
                    return name
                return f'[{name}]'
            # the transparency rule: a converter that consumes
            # exactly one operand is transparent to naming--the
            # annotated parameter's name flows through to its
            # terminal.  (An explicit rename on the inner
            # parameter still wins.)  Applied at render time:
            # plans are memoized and shared, so `flavor` used
            # under two different outer names must render
            # differently per use.
            body = body_text(child, rename=transparent_name(slot))
            if slot.repeat:
                return f'[{body}]...'
            if slot.required:
                return body
            return f'[{body}]'

        def body_text(plan, rename=None):
            # options that feed one parameter are alternatives: the
            # last given wins, so they read as a choice, [--json | --yaml]
            # (Larry, 2026-09-09: exclusivity is one parameter, one value)
            groups = {}
            for o in plan.options:
                groups.setdefault(o.name, []).append(o)
            bits = []
            for rules in groups.values():
                if len(rules) == 1:
                    bits.append(option_text(rules[0]))
                    continue
                text = ' | '.join(_option_body(o) for o in rules)
                bits.append(text if rules[0].required else '[' + text + ']')
            bits.extend(slot_text(s, rename) for s in plan.slots)
            return ' '.join(bits)

        return body_text(self)

    def sole_terminal_slot(self):
        """
        The transparency rule's eligibility test: if this plan
        consumes exactly one operand, returns that Terminal's
        slot; otherwise None.
        """
        found = None
        for s in self.slots:
            if isinstance(s.child, Terminal):
                if found is not None:
                    return None
                found = s
            else:
                inner = s.child.sole_terminal_slot()
                if inner is None and s.child.count_terminals():
                    return None
                if inner is not None:
                    if found is not None:
                        return None
                    found = inner
        return found

    def count_terminals(self):
        n = 0
        for s in self.slots:
            if isinstance(s.child, Terminal):
                n += 1
            else:
                n += s.child.count_terminals()
        return n


    def greedy_body(self, remaining):
        """
        The engine's fill rule, on counts: how many of `remaining`
        operands this self's body (its non-trailing slots) consumes,
        or None if some slot starves.  Slots fill strictly left to
        right, each taking while operands remain; a group is entered
        whenever operands remain, and takes what ITS slots take.
        Trailing operands aren't in play here: the whole tree's are
        pocketed from the end of the stream before any body fills.
        """
        used = 0
        for slot in self.slots:
            if slot.trailing:
                continue
            if slot.repeat:
                used += remaining
                remaining = 0
            elif isinstance(slot.child, Terminal):
                if remaining:
                    used += 1
                    remaining -= 1
                elif slot.required:
                    return None
            elif remaining or slot.required:
                took = slot.child.greedy_body(remaining)
                if took is None:
                    return None
                used += took
                remaining -= took
        return used

    @property
    def absorbs(self):
        "Does this self's body consume unboundedly (a *args somewhere)?"
        return any(s.repeat or (not s.trailing
                                and not isinstance(s.child, Terminal)
                                and s.child.absorbs)
                   for s in self.slots)

    @property
    def saturation(self):
        """
        For an absorbing self: the body count at which every slot
        before the absorber has taken its maximum, so everything from
        there up is valid (the absorber takes the rest).
        """
        # (no trailing check: a trailing slot always FOLLOWS the absorber,
        # and the walk returns there)
        total = 0
        for slot in self.slots:
            if slot.repeat:
                return total
            if isinstance(slot.child, Terminal):
                total += 1
            elif slot.child.absorbs:
                return total + slot.child.saturation
            else:
                total += slot.child.maximum
        assert False, 'an absorbing self has an absorber'    # pragma: no cover

    def analyze(self):
        """
        The self's operand-count footprint--minimum, maximum,
        valid_counts, unbounded_from--by SIMULATING the fill rule on
        counts, so the set can't disagree with the engine (the retired
        completable-distribution fold was a superset: it called two
        operands valid for f(a='A', p: pair='P'), which fill a and
        starve pair; ruled exact 2026-09-07).  Trailing operands are
        pocketed from the end of the stream first, shifting everything
        by the tree's trailing count.
        """
        self.tree_trailing = sum(
            1 if s.trailing else
            (s.child.tree_trailing if not isinstance(s.child, Terminal) else 0)
            for s in self.slots)
        trailing = self.tree_trailing

        def valid(n):
            return self.greedy_body(n) == n

        if self.absorbs:
            threshold = self.saturation
            counts = {n + trailing for n in range(threshold) if valid(n)}
            self.unbounded_from = threshold + trailing
            self.maximum = None
            self.minimum = min(counts) if counts else self.unbounded_from
        else:
            ceiling = self.greedy_body(10 ** 9)       # every slot saturated
            counts = {n + trailing for n in range(ceiling + 1) if valid(n)}
            self.unbounded_from = None
            self.maximum = max(counts)
            self.minimum = min(counts)
        self.valid_counts = counts
        self.check_option_reachability()

    def check_option_reachability(self):
        """
        An option a later same-string option would STOMP is unreachable, and
        that's a ConfigurationError (Larry, 2026-08-24).  It happens with two
        ZERO-OPERAND converter groups on the same command: they stack at the same
        spot (no operands to tell them apart), so the second's options overwrite
        the first's -- the first's are unreachable.  Windowed groups (that DO
        consume operands, like rip's a/b/c) sit at different positions and scope
        the option by window, so they're fine.
        """
        own = set()
        for o in self.options:
            own.update(o.strings)
        claimed = {}                        # option string -> the slot that owns it
        for slot in self.slots:
            child = slot.child
            if isinstance(child, Terminal) or child.maximum != 0:
                continue                    # only zero-width groups stack at a spot
            for o in child.options:
                for s in o.strings:
                    if s in own:
                        continue            # the command's own option shadows it
                    if s in claimed and claimed[s] != slot.name:
                        raise AppealConfigurationError(
                            f"option {s!r} of {claimed[s]!r} is unreachable: "
                            f"{slot.name!r} stomps on it (two zero-operand groups "
                            f"can't be told apart by position)")
                    claimed[s] = slot.name

    def all_options(self, _out=None, _seen=None):
        """
        Every (owner self, option) pair in the tree: the self's own
        options first, then each child's, depth-first in slot order.
        """
        if _out is None:
            _out = []
            _seen = set()
        if id(self) in _seen:
            return _out
        _seen.add(id(self))
        for option in self.options:
            _out.append((self, option))
            if option.child is not None:
                option.child.all_options(_out, _seen)
        for slot in self.slots:
            if not isinstance(slot.child, Terminal):
                slot.child.all_options(_out, _seen)
        return _out

    def subtree_option_keys(self):
        "The keys of every option declared by self or its descendants."
        return [option.key for owner, option in self.all_options()]

    def help_option_strings(self):
        """
        The automatic help strings: ('-h', '--help'), minus anything
        the program claimed for itself.  If the program took '--help',
        there is no automatic help at all--the user wins.  Help strings
        never appear in usage (v1, probed).
        """
        if not self.auto_help:
            # the app's help= knob is off: no automatic help at all
            return ()
        taken = {s for _, o in self.all_options() for s in o.strings}
        if '--help' in taken:
            return ()
        return tuple(s for s in ('-h', '--help') if s not in taken)

    def verbatim_sites(self):
        """
        (singles, run): the operand positions of this plan's single
        verbatim slots, and the position its verbatim *args begins
        (None if it has none)--counted through nested converters.
        Positions are exact because the fill is greedy left to right:
        every slot before takes its maximum before a later one takes
        anything.  Trailing operands aren't in play (pocketed from the
        end, and always after the absorber the walk stops at).  The
        engine's reserve scan and completion read this, so neither can
        classify a verbatim token as an option.
        """
        singles = set()
        total = 0
        for slot in self.slots:
            child = slot.child
            if isinstance(child, Terminal):
                if child.converter is verbatim:
                    if slot.repeat:
                        return singles, total
                    singles.add(total)
                if slot.repeat:                 # a plain *args takes the rest
                    return singles, None
                total += 1
                continue
            inner_singles, inner_run = child.verbatim_sites()
            singles.update(total + i for i in inner_singles)
            if inner_run is not None:
                return singles, total + inner_run
            if child.absorbs:
                return singles, None
            total += child.maximum
        return singles, None

    def count_sites(self):
        """
        How many times each self instantiates in the tree--a converter
        reused across sibling slots is one Plan object with several
        sites, and each site is a window for its options.  (Cycles
        can't happen; build refuses them.)
        """
        sites = {}
        def walk(p, count):
            sites[id(p)] = sites.get(id(p), 0) + count
            for option in p.options:
                if option.child is not None:
                    walk(option.child, count)
            for slot in p.slots:
                if not isinstance(slot.child, Terminal):
                    walk(slot.child, count)
        walk(self, 1)
        return sites

    def mark_barriers(self, certain):
        """
        The gate rule (Larry's ruling, refined against shipping v1):
        a converter group CERTAIN to consume operands--required all the
        way up from the top, needing at least one--is a barrier, and
        options of *skippable* groups behind it may not appear until it
        has been fed.  Options of CERTAIN groups float free (v1,
        probed: they announce something that's coming no matter what).
        Skippable groups and plain terminal operands never gate anything.
        Returns True if any barrier exists.
        """
        self.certain = certain
        any_barriers = False
        for slot in self.slots:
            if isinstance(slot.child, Terminal):
                continue
            slot.barrier = (certain and slot.required and not slot.repeat
                            and not slot.trailing and slot.child.minimum > 0)
            below = slot.child.mark_barriers(
                                   certain and slot.required and not slot.repeat)
            any_barriers = any_barriers or slot.barrier or below
        return any_barriers

    def finalize_options(self, default_options, app=None):
        """
        The whole-command view of the options.  A string declared by
        several windows is *scoped*: legal when every declaration
        agrees on the grammar (kind and oparg counts--the consumption
        must be binding-independent; defaults, converters, and
        docstrings may differ per window), and occurrences bind by
        position.  A string declared twice in ONE window is refused
        (no position can distinguish them), as is a grammar mismatch.

        First, the option-string policy (default_options) runs over
        every automatically-mapped option: it returns that option's
        proposed strings from its (name, annotation, default).  Longs
        are claimed here; shorts are proposed, and each is claimed
        below if its letter is still free.  First declared, first
        served (walk order: a rule's own options, then its children's,
        depth-first).  @app.option declarations are explicit and skip
        the policy--their strings are the whole story.
        """
        sites = self.count_sites()
        pairs = self.all_options()
        doomed = []
        registrar = _PolicyRegistrar(app)
        for owner, option in pairs:
            if option.explicit:
                continue
            proposed = ()
            if default_options is not None:
                # the policy registers through the registrar's
                # app.option() (arglet style, Larry's design
                # 2026-07-22); declining is not calling
                default_options(registrar, owner.callable, option.name)
                proposed = registrar.claims.pop(
                    (id(owner.callable), option.name), ())
            longs, shorts = [], []
            for s in proposed:
                validate_option_string(s)
                (longs if s.startswith('--') else shorts).append(s)
            option.strings = tuple(longs)
            option.auto_shorts = tuple(shorts)
            if not proposed:
                # the policy declined (None, or an empty iterable--
                # e.g. the stock policy's _underscore rule): this
                # parameter simply isn't an option, its default always
                # fills.  The "has no option strings" refusal below is
                # reserved for STARVATION: strings proposed, none
                # available.
                doomed.append((owner, option))
        for owner, option in doomed:
            owner.options = [o for o in owner.options if o is not option]
        if doomed:
            pairs = self.all_options()
            sites = self.count_sites()
        declared = {}     # string -> [(owner, option)]
        for owner, option in pairs:
            for s in option.strings:
                declared.setdefault(s, []).append((owner, option))
        scoped = set()
        for s, entries in declared.items():
            owners = [id(owner) for owner, _ in entries]
            if len(set(owners)) != len(owners):
                raise AppealConfigurationError(
                    f"option {s!r} is declared twice by one converter; "
                    f"no position can tell the declarations apart")
            windowed = [owner for owner, _ in entries
                        if owner.windowed]
            total = sum(sites[id(owner)] for owner, _ in entries
                        if not owner.windowed)
            if windowed and (total or len(entries) > 1):
                raise AppealConfigurationError(
                    f"option {s!r} is declared both by a *args group "
                    f"and elsewhere; that mix isn't in the grammar yet")
            if total <= 1:
                continue
            # scoped: every declaration must agree on the grammar
            grammars = {(o.table_entry()[1],) + tuple(o.table_entry()[2:])
                        for _, o in entries}
            if len(grammars) > 1:
                names = ' and '.join(sorted(
                    repr(owner.name) for owner, _ in entries))
                raise AppealConfigurationError(
                    f"option {s!r} is declared with different grammars "
                    f"by {names}; scoped options with differing "
                    f"grammars aren't in the grammar yet (the parser "
                    f"couldn't know how many arguments to consume "
                    f"before knowing which window wins)")
            scoped.add(s)
        self.scoped_keys = frozenset(scoped)
        taken = set(declared)
        seen_rules = set()
        for owner, option in pairs:
            if option.explicit:
                # @app.option strings are the whole story
                continue
            if id(option) in seen_rules:
                # a shared rule revisited (all_options dedupes plans,
                # but belt and braces)
                continue   # pragma: no cover
            seen_rules.add(id(option))
            for short in option.auto_shorts:
                if short not in taken:
                    taken.add(short)
                    option.strings = (short,) + option.strings
                    if option.strings[-1] in scoped:
                        # the short rides its long's scopedness
                        scoped.add(short)
                        self.scoped_keys = frozenset(scoped)
        for owner, option in pairs:
            if not option.strings:
                raise AppealConfigurationError(
                    f"option {option.name!r} (of {owner.name!r}) has no option "
                    f"strings: its name is too short for a long option and its "
                    f"letter is already taken")

        # sibling option groups (Larry's ruling, 2026-07-18): a scoped
        # key whose EVERY declarer is the direct child of a top-level
        # group OPTION (e1: extras, e2: extras) binds by announcement
        # (--e1/--e2), not operand position.  Child options may summon
        # the first declared sibling; later siblings exist only when
        # announced.
        option_children = {id(o.child): o.key for o in self.options
                           if o.child is not None}
        sibling_keys = set()
        for s in self.scoped_keys:
            declarers = [owner for owner, o in pairs if s in o.strings]
            if declarers and all(id(d) in option_children
                                 for d in declarers):
                sibling_keys.add(s)
        if sibling_keys:
            parents = []
            for o in self.options:
                if o.child is None:
                    continue
                keys = tuple(k for k in sorted(sibling_keys)
                             if any(owner is o.child and k in opt.strings
                                    for owner, opt in pairs))
                if keys:
                    parents.append((o.key, keys))
            self.sibling_parents = tuple(parents)
            self.sibling_keys = frozenset(sibling_keys)

    def clone(self):
        """
        A shallow copy of a Plan whose slot/child spine is deep-copied
        (options, Terminals, converters shared).  Used to un-share a
        Plan the build memo handed to more than one slot, so optionality
        promotion--which is context-specific--can't cross-contaminate.
        """
        clone = _shallow_copy(self)
        slots = []
        for s in self.slots:
            ns = _shallow_copy(s)
            if isinstance(ns.child, Plan):
                ns.child = ns.child.clone()
            slots.append(ns)
        clone.slots = slots
        return clone

    def unshare(self, seen):
        "Give every group slot its own subtree (the memo may share Plans)."
        for s in self.slots:
            if isinstance(s.child, Plan):
                if id(s.child) in seen:
                    s.child = s.child.clone()
                seen.add(id(s.child))
                s.child.unshare(seen)

    def promote(self, parent_opt, lowest_required, mutate, flag):
        """
        v1's optionality promotion (argument_grouping.py's first_pass/
        second_pass), folded into one right-to-left pass.

        optionality is how deeply optional a slot is: the parent's plus
        one for each default level.  Walking right to left,
        lowest_required tracks the shallowest optionality at which a
        required slot sits to the RIGHT; a slot deeper than that can't
        be skipped (a required operand follows it, across group
        nesting), so it's promoted to required.  Because Python forces a
        signature's defaults to its tail, this only fires on a converter
        GROUP in a non-final required slot: `f(first: opt2, x, y)`
        promotes opt2's trailing default, so f demands four operands
        (matching 0.6.4).

        TRAILING slots (keyword-only, filled from the end) don't
        participate--reservation isn't promotion, and the oracle
        likewise ignores keyword-only parameters.  When mutate is False
        the walk only reports (via flag[0]) whether anything WOULD be
        promoted, so the caller can skip un-sharing when nothing moves.
        Returns this level's lowest_required.
        """
        optionality = [None if slot.trailing
                       else parent_opt + (0 if slot.required else 1)
                       for slot in self.slots]
        lr = lowest_required
        for i in range(len(self.slots) - 1, -1, -1):
            slot = self.slots[i]
            if slot.trailing:
                continue
            opt = optionality[i]
            if isinstance(slot.child, Plan):
                returned = slot.child.promote(opt, lr, mutate, flag)
                if returned == parent_opt:
                    lr = returned
            if opt > lr:
                if not slot.required:
                    flag[0] = True
                    if mutate:
                        slot.required = True
            elif slot.required and slot.needs_operand:
                lr = min(lr, opt)
        return lr

    def promote_optionality(self):
        """
        Promote defaulted operands that a required operand follows.
        Mutates slot.required in place; returns True if anything moved
        (the caller then re-analyzes the counts).  The tree is un-shared
        first, but only when a promotion is actually needed, so the
        common case (and deliberate converter dedup) is untouched.
        """
        flag = [False]
        self.promote(0, _PROMOTE_INF, False, flag)   # detect
        if not flag[0]:
            return False
        self.unshare(set())
        flag = [False]
        self.promote(0, _PROMOTE_INF, True, flag)    # apply
        return flag[0]

    def reanalyze(self):
        "Re-run the counting automaton bottom-up after promotion."
        for slot in self.slots:
            if isinstance(slot.child, Plan):
                slot.child.reanalyze()
        self.analyze()

    def validate_completions(self):
        """
        Build-time validation of the completion protocol: a converter's
        `completions` attribute, if present, must be a callable
        accepting one positional argument (the prefix).  The tuple
        return type is checked at query time--it can't be checked
        before the call--but the signature is structure, and structure
        bugs fail loudly, here.
        """
        def check(converter):
            completions = getattr(converter, 'completions', None)
            if completions is None:
                return
            where = getattr(converter, '__name__', repr(converter))
            if not callable(completions):
                raise AppealConfigurationError(
                    f"{where}.completions must be callable "
                    f"(a (prefix) -> tuple of str), not "
                    f"{completions!r}")
            # completions must accept exactly the one positional prefix: at least
            # one positional slot must exist (or *args), and none beyond the first
            # may be required.
            try:
                params = list(inspect.signature(completions).parameters.values())
            except (ValueError, TypeError):
                params = None
            if params is not None:
                positional = [p for p in params
                              if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                            inspect.Parameter.POSITIONAL_OR_KEYWORD)]
                has_varargs = any(p.kind is inspect.Parameter.VAR_POSITIONAL
                                  for p in params)
                required = sum(1 for p in positional
                               if p.default is inspect.Parameter.empty)
                fits = required <= 1 and (has_varargs or len(positional) >= 1)
                if not fits:
                    raise AppealConfigurationError(
                        f"{where}.completions must accept one positional "
                        f"argument (the prefix)")

        def terminal_count(p):
            n = 0
            for s in p.slots:
                n += 1 if isinstance(s.child, Terminal) else terminal_count(s.child)
            return n

        def walk(p):
            check(p.callable)
            if (getattr(p.callable, 'completions', None) is not None
                    and terminal_count(p) != 1):
                where = getattr(p.callable, '__name__', repr(p.callable))
                raise AppealConfigurationError(
                    f"{where}.completions: {where!r} consumes "
                    f"{terminal_count(p)} arguments, so its completions "
                    f"are ambiguous; put completions on the individual "
                    f"converters")
            for o in p.options:
                for converter in o.converters:
                    check(converter)
            for s in p.slots:
                if isinstance(s.child, Terminal):
                    check(s.child.converter)
                else:
                    walk(s.child)

        walk(self)

    def child_for(self, parameter, build):
        """
        Decide a positional parameter's child: a Terminal, or a child Plan
        (recursion--the heart of the metaphor).
        """
        annotation = parameter.annotation
        if annotation is inspect.Parameter.empty:
            default = parameter.default
            if default is not inspect.Parameter.empty:
                if (isinstance(default, (list, tuple)) and default
                        and all(type(e) in _default_type_converters
                                for e in default)):
                    # a list or tuple default infers a group from its
                    # element types: b=[0, 0.0] takes an int and a float
                    # and produces that sequence type (v1's corpus; the
                    # tuple half restored 2026-09-07, review item I2)
                    return ElementsPlan(type(default),
                                          [type(e) for e in default],
                                          parameter.name)
                t = type(default)
                if t in _default_type_converters or t is bool:
                    # bool: v1's corpus reads these from mappings; on a
                    # command line the runtime parses true/false/yes/no
                    # strictly, never truthiness
                    return Terminal(t)
                if default is not None and not isinstance(
                        default, (list, tuple, dict, set, frozenset, bytes)):
                    # v1: a custom-class default infers ITS TYPE as the
                    # converter, recursing exactly like an annotation--
                    # build(target=Path('out')) reads the operand through
                    # Path (restored 2026-09-07, review item I1; the
                    # option side always kept this rule)
                    annotation = t
            if annotation is inspect.Parameter.empty:
                return Terminal(str)
        annotation = dereference_annotated(annotation)
        _refuse_bare_factory(annotation, f"parameter {parameter.name!r}")
        if (hasattr(annotation, 'recipe')
                and not isinstance(annotation, type)):
            # a vocabulary product (validate(...), split(...)): a terminal,
            # so its ValueError becomes a polite UsageError via convert()
            return Terminal(annotation)
        if is_option(annotation):
            converters, _ = Fold.converters_of(
                annotation, f"parameter {parameter.name!r}")
            return Terminal(Fold.leaf(annotation, converters))
        if getattr(annotation, '__origin__', None) is tuple:
            return TuplePlan(annotation, parameter.name, build)
        if _generic_origin(annotation) is not None:
            raise AppealConfigurationError(
                f"parameter {parameter.name!r}: {annotation!r} is only meaningful "
                f"on an option (a keyword-only parameter with a default)")
        if not callable(annotation):
            raise AppealConfigurationError(
                f"parameter {parameter.name!r}: annotation {annotation!r} isn't callable")
        if _is_leaf(annotation):
            return Terminal(annotation)
        # a real converter: introspect it.  uninspectable callables
        # (some builtins, C functions) are treated as terminals.
        try:
            signature = inspect.signature(annotation)
        except (ValueError, TypeError):
            return Terminal(annotation)
        # a *args converter is an ABSORBING nonterminal: it consumes
        # greedily, to what the slots after it don't need.  (v1,
        # probed: pair(a, *rest) fed the whole remaining line--an
        # earlier comment here claimed v1 read these as one-operand
        # terminals; it doesn't.)
        return build.plan(annotation)


from . import (
    AppealConfigurationError, Option, accumulator, is_multioption,
    is_option, mapping, verbatim,
    )


# THE LEAVES of the annotation tree (Larry, 2026-09-09, v1's list): these
# take one operand string and are never introspected--their signatures
# aren't their grammar (complex(real=0, imag=0) is one string, '3j').
# Every other callable, class or function, argument or option, IS
# introspected: its signature is its grammar.  (verbatim is a leaf the
# engine knows by identity.)
_blessed_leaves = {str, int, float, bool, complex, verbatim}


_PATHLIB_LEAVES = ('PurePath', 'PurePosixPath', 'PureWindowsPath',
                   'Path', 'PosixPath', 'WindowsPath')


def _is_leaf(annotation):
    """
    Is this annotation a leaf: the list above, or one of pathlib's
    six classes--exactly those, never a subclass (a subclass may add
    a keyword-only parameter and mean it as an option)?  They take
    one string on a command line--their (*args, **kwargs) is for
    joining segments, not a grammar (Larry, 2026-09-09).  pathlib is
    never imported for this (it costs 7-9ms): if the user annotated
    with a Path class, it's in sys.modules already.
    """
    if annotation in _blessed_leaves:
        return True
    pathlib = _sys.modules.get('pathlib')
    return pathlib is not None and any(
        annotation is getattr(pathlib, name) for name in _PATHLIB_LEAVES)

# the terminal converters we bless for annotation-free defaults
_default_type_converters = {str, int, float}


# Annotated[T, converter] -> converter.  T is for mypy; the converter
# (the last metadata element) is for us.  Annotated shipped in 3.9;
# on older Pythons Appeal limps along without it (the annotation
# passes through untouched--and since you can't *write* Annotated
# there, nothing is lost).  This is v1's pattern, kept verbatim.
def dereference_annotated(annotation):
    # Annotated lives in `typing`.  If the user never imported typing, they
    # could not have written an Annotated annotation -- so a mere sys.modules
    # probe (nanoseconds) settles it without importing typing ourselves.  When
    # typing IS loaded, the local import is free (already in sys.modules).
    import sys
    typing = sys.modules.get('typing')
    if typing is not None:
        if type(annotation) is type(typing.Annotated[int, str]):
            return annotation.__metadata__[-1]
        # Literal['red', 'blue'] means validate('red', 'blue') (Larry,
        # 2026-09-10): Python's own spelling of "one of these"--the same
        # sys.modules probe, never an import of typing (3.5ms)
        if getattr(annotation, '__origin__', None) is typing.Literal:
            from . import validate
            return validate(*annotation.__args__)
    # X | None (PEP 604 builtin unions, 3.10+): the None arm is for the
    # type checker--it's the default's type--and X is the converter.
    # Only the BUILTIN spelling; typing.Optional/typing.Union stay
    # unsupported, per the house typing stance.  A union of two real
    # types is refused by name: which converter would it be?
    if _UnionType is not None and isinstance(annotation, _UnionType):
        arms = [a for a in annotation.__args__ if a is not type(None)]
        if len(arms) != 1:
            from . import AppealConfigurationError
            raise AppealConfigurationError(
                f"annotation {annotation!r}: a union isn't a converter"
                f"--only `X | None` is in the grammar (the None arm is "
                f"for the type checker; X converts)")
        return dereference_annotated(arms[0])
    return annotation


def _generic_origin(annotation):
    "list[int] -> list, dict[str, int] -> dict, everything else -> None."
    origin = getattr(annotation, '__origin__', None)
    if origin in (list, dict):
        return origin
    return None


def _is_option_group(annotation):
    """
    Does this option converter have its own grammar--optional
    parameters, nested converters, or keyword-only options--rather
    than a flat row of required terminals?
    """
    if not callable(annotation):
        return False
    if _is_leaf(annotation):
        return False
    if getattr(annotation, '__origin__', None) is not None:
        return False
    if hasattr(annotation, 'factory'):
        return False   # pragma: no cover -- _refuse_bare_factory fires
                       # before the only caller reaches this predicate
    try:
        signature = inspect.signature(annotation)
    except (ValueError, TypeError):
        return False
    saw_positional = False
    for p in signature.parameters.values():
        if p.kind is inspect.Parameter.KEYWORD_ONLY:
            return True
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                      inspect.Parameter.POSITIONAL_OR_KEYWORD):
            saw_positional = True
            if p.default is not inspect.Parameter.empty:
                return True
            annotation_ = p.annotation
            if annotation_ is not inspect.Parameter.empty:
                annotation_ = dereference_annotated(annotation_)
                if (callable(annotation_)
                        and not _is_leaf(annotation_)
                        and not hasattr(annotation_, 'recipe')
                        and getattr(annotation_, '__origin__', None) is None
                        and _positional_arity(annotation_) > 0):
                    return True
    return False


def _is_multiparam_converter(annotation):
    "A plain converter whose signature consumes several operands?"
    if not callable(annotation):
        return False
    if _is_leaf(annotation):
        return False
    if getattr(annotation, '__origin__', None) is not None:
        return False
    if is_option(annotation):
        return False   # pragma: no cover -- Option classes take the fold
                       # branch before the only caller asks about arity
    return _positional_arity(annotation) > 1


def _accepts_no_arguments(fn):
    "Callable with no parameters at all (a value-producing flag)?"
    try:
        signature = inspect.signature(fn)
    except (ValueError, TypeError):   # pragma: no cover -- only called
        # when _positional_arity() == 0, and uninspectable callables
        # report arity 1
        return False
    return not signature.parameters


def _positional_arity(callable):
    "Required positional parameter count; 1 if uninspectable."
    try:
        signature = inspect.signature(callable)
    except (ValueError, TypeError):
        return 1
    return sum(
        1 for p in signature.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                      inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and p.default is inspect.Parameter.empty)


def _refuse_bare_factory(annotation, context):
    factory = getattr(annotation, 'factory', None)
    if factory:
        raise AppealConfigurationError(
            f"{context}: {annotation.__name__} is a converter factory; "
            f"call it first, e.g. {factory}")


def _leaf_callable(annotation, context):
    """
    A converter position that (so far) only accepts terminals:
    blessed types, or callables taking one operand.  A
    multi-parameter converter here is a named config error.

    Dereferences Annotated itself, so every consumer of an
    element annotation (list[T], dict[K, V]) honors the documented
    contract--"Appeal only ever uses the *last* value"--everywhere.
    """
    annotation = dereference_annotated(annotation)
    _refuse_bare_factory(annotation, context)
    if getattr(annotation, '__origin__', None) is not None:
        raise AppealConfigurationError(
            f"{context}: {annotation!r} isn't usable here (generic "
            f"annotations are only meaningful directly on a parameter)")
    if not callable(annotation):
        raise AppealConfigurationError(
            f"{context}: {annotation!r} isn't callable")
    if _is_leaf(annotation):
        return annotation
    try:
        signature = inspect.signature(annotation)
    except (ValueError, TypeError):
        return annotation
    positional = [
        p for p in signature.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                      inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and p.default is inspect.Parameter.empty
        ]
    if len(positional) > 1:
        raise AppealConfigurationError(
            f"{context}: converter {getattr(annotation, '__name__', annotation)!r} "
            f"takes {len(positional)} required arguments; multi-parameter "
            f"converters aren't supported here yet")
    return annotation


def _long_option(name):
    "The long option string for a parameter name: color -> --color."
    return '--' + name.replace('_', '-')


def _short_option(name):
    "The short option string for a parameter name: color -> -c."
    return '-' + name[0]


# --- the default_options policy (v1's constructor knob, restored) ---
#
# A policy decides the option strings an automatically-mapped
# keyword-only parameter proposes: given (name, annotation,
# default), it returns a list of option strings.  Appeal claims
# every long ('--xxx') unconditionally (they must be unique) and
# every short ('-x') if its letter is still free.  The policy runs
# at build time only.

def default_options(app, callable, name):
    """
    The stock option-string policy, arglet style (Larry's design,
    2026-07-22): the policy REGISTERS its mappings through the
    same app.option() spelling users write--one mechanism.
    Declining is simply not calling.  Both a long (names >= 2
    chars) and a short--v1's default; the short is a wish, claimed
    only if its letter is still free.  A leading underscore means
    "not public surface" in Python and here too: no default
    mapping at all (@app.option is the escape hatch).
    """
    if name.startswith('_'):
        return
    strings = []
    if len(name) >= 2:
        strings.append(_long_option(name))
    strings.append(_short_option(name))
    app.option(name, *strings)(callable)


def default_long_option(app, callable, name):
    "Long only, no short (the common 'suppress all shorts' policy)."
    if len(name) >= 2:
        app.option(name, _long_option(name))(callable)


def default_short_option(app, callable, name):
    "Short only, no long."
    app.option(name, _short_option(name))(callable)


class _PolicyRegistrar:
    """
    The `app` a default_options policy is handed: .option()
    records into THIS build (nothing persists onto callables, so
    shared converters can't poison other apps and rebuilds can't
    double-register); every other attribute forwards to the real
    app (None for appless build_plan() calls).
    """
    def __init__(self, app):
        self._app = app
        self.claims = {}    # (id(callable), parameter) -> strings

    def option(self, parameter_name, *strings,
               annotation=inspect.Parameter.empty,
               default=inspect.Parameter.empty):
        if (annotation is not inspect.Parameter.empty
                or default is not inspect.Parameter.empty):
            raise AppealConfigurationError(
                "a default_options policy registers option "
                "STRINGS; annotation=/default= overrides belong "
                "to an explicit @app.option declaration")
        if not strings:
            # same meaning as everywhere (ruled 2026-07-25):
            # zero strings = this parameter isn't an option.
            # Equivalent to declining by not calling.
            def unmapped(callable):
                return callable
            return unmapped
        def decorator(callable):
            self.claims[(id(callable), parameter_name)] = strings
            return callable
        return decorator

    def __getattr__(self, attr):
        return getattr(self._app, attr)


def _is_repeat_group(annotation):
    """
    Should this *args converter be a per-instance GROUP (multiple
    operands and/or its own options) rather than a simple terminal?
    """
    if not callable(annotation):
        return False
    if _is_leaf(annotation):
        return False
    if (hasattr(annotation, 'recipe')
            and not isinstance(annotation, type)):
        # a vocabulary product (split/validate/...): a terminal,
        # exactly as _child_for treats it off *args (the old param
        # count kept these leaves only by accident)
        return False
    if getattr(annotation, '__origin__', None) is not None:
        return False
    try:
        signature = inspect.signature(annotation)
    except (ValueError, TypeError):
        return False
    kinds = [p.kind for p in signature.parameters.values()]
    if inspect.Parameter.KEYWORD_ONLY in kinds:
        return True
    # ANY positional parameter makes it a converter GROUP, whose
    # operand(s) are converted per its own annotations--matching the
    # non-*args path (_child_for) and 0.6.4.  The old `> 1` threshold
    # treated a single-parameter group as a raw-string leaf, so
    # g0(p0: int) off *args skipped its int() and returned the
    # string (differential-caught 2026-08-16).  pair(a, b='B') is
    # still a two-operand group whose second fills greedily.
    return sum(
        1 for p in signature.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                      inspect.Parameter.POSITIONAL_OR_KEYWORD)) >= 1


def _option_strings(name):
    """
    dry_run -> ('--dry-run',).  A one-character name gets no long
    option (v1, probed: '--n' is unknown; 'n' has only '-n', which
    the auto-short pass assigns).
    """
    if len(name) < 2:
        return ()
    return ('--' + name.replace('_', '-'),)


def validate_option_string(s):
    "v1's rule: '-X' (one alphanumeric) or '--xxx' (at least two chars)."
    ok = (isinstance(s, str)
          and s.startswith('-')
          and (((len(s) == 2) and s[1].isalnum())
               or ((len(s) >= 4) and s.startswith('--'))))
    if not ok:
        raise AppealConfigurationError(f"{s!r} is not a legal option string")
    return s


class Decorations:
    """
    Everything @app.option and @app.parameter EXPRESSED, recorded
    inside the app and keyed by the decorated callable (ruled
    2026-08-09: Appeal never modifies objects the user owns--no
    attributes planted on functions, classes, or anything else;
    and decoration only writes down what was said).  Functions,
    classes, and bound methods are all hashable keys--bound
    methods compare by (instance, function), so the fresh object
    minted per attribute access still finds its entry.
    """
    def __init__(self):
        self.option_overrides = {}   # callable -> {param: [decls]}
        self.parameter_usage = {}    # callable -> {param: usage}

    def add_option(self, callable, parameter_name, strings,
                   annotation=inspect.Parameter.empty,
                   default=inspect.Parameter.empty, config=None):
        # zero strings is legal (ruled 2026-07-25): "I'm speaking
        # for this parameter: nothing"--the explicit per-parameter
        # unmap, symmetric with a policy declining.  The parameter
        # stays keyword-only, its default always fills.
        for s in strings:
            validate_option_string(s)
        declaration = {'strings': tuple(strings),
                       'annotation': annotation, 'default': default,
                       'config': config}
        overrides = self.option_overrides.setdefault(callable, {})
        declarations = overrides.setdefault(parameter_name, [])
        if declaration not in declarations:
            # re-applying the same declaration is a no-op (REPLs
            # and test harnesses re-decorate freely)
            declarations.append(declaration)

    def add_usage(self, callable, parameter_name, usage):
        if not (isinstance(usage, str) and usage):
            raise AppealConfigurationError(
                f"@parameter for {parameter_name!r}: usage must be "
                f"a nonempty string")
        names = self.parameter_usage.setdefault(callable, {})
        names[parameter_name] = usage

    def overrides_for(self, callable):
        return self.option_overrides.get(callable) or {}

    def usage_for(self, callable):
        return dict(self.parameter_usage.get(callable) or {})


_NO_DECORATIONS = Decorations()


class Build:
    """
    One build's context, threaded through the plan constructors
    (Larry's design, 2026-09-08--it replaces the memo/stack pair
    every helper used to pass by hand): the memo of plans already
    built (a converter reached from several slots is ONE plan), the
    cycle stack, the app's decorations (@app.option/@app.parameter),
    its option-string policy, and the top plan's extra declarations.
    """
    __slots__ = ('memo', 'stack', 'decorations', 'default_options', 'app',
                 'extra_overrides')

    def __init__(self, decorations=None, default_options=None, app=None,
                 extra_overrides=None, memo=None, stack=()):
        self.memo = {} if memo is None else memo
        self.stack = stack
        self.decorations = decorations or _NO_DECORATIONS
        self.default_options = default_options
        self.app = app
        self.extra_overrides = extra_overrides

    def within(self, callable):
        "The context one level down, inside `callable`; a cycle refuses."
        if callable in self.stack:
            cycle = ' -> '.join(getattr(c, '__name__', repr(c))
                                for c in self.stack)
            raise AppealConfigurationError(
                f"converter cycle: {cycle} -> "
                f"{getattr(callable, '__name__', repr(callable))}")
        return Build(self.decorations, self.default_options, self.app,
                     self.extra_overrides, self.memo, self.stack + (callable,))

    def fresh(self):
        "The same context with an empty memo: a plan that mustn't be shared."
        return Build(self.decorations, self.default_options, self.app,
                     self.extra_overrides, None, self.stack)

    def bare(self):
        "A fresh memo and no decorations: a group option's own grammar."
        return Build(None, self.default_options, self.app, None, None,
                     self.stack)

    def plan(self, callable):
        "The plan for a converter reached from a slot: memoized, not top."
        plan = self.memo.get(callable)
        if plan is None:
            plan = SignaturePlan(callable, self)
            self.memo[callable] = plan
        return plan


class SignaturePlan(Plan):
    """
    A plan read from a callable's signature: positional parameters
    become slots, keyword-only ones options.  `top` is a command (or
    precommand): its options are finalized against the whole tree and
    its barriers marked; a converter reached from a slot isn't.
    """
    __slots__ = ()

    def __init__(self, callable, build, *, name=None, top=False,
                 skip_first=False):
        if name is None:
            name = getattr(callable, '__name__', None)
            if not name:
                raise AppealConfigurationError(
                    f"can't determine a name for {callable!r}")
        if _is_coroutine_function(callable):
            # refused, never run for them (Larry, 2026-09-10): choosing
            # an event loop is magic, and breaks inside a running one.
            # Called, it would return an unawaited coroutine and the
            # command would silently do nothing.
            fn = getattr(getattr(callable, '__func__', callable),
                         '__name__', name)
            raise AppealConfigurationError(
                f"{fn!r} is a coroutine function; wrap it in a plain "
                f"function that calls asyncio.run()")
        build = build.within(callable)
        signature = inspect.signature(callable)
        decorations = build.decorations
        overrides = {param: list(decls) for param, decls
                     in decorations.overrides_for(callable).items()}
        if build.extra_overrides:
            # per-app declarations delivered directly (the bound
            # precommand's version/help strings)
            for param, decls in build.extra_overrides.items():
                merged = list(overrides.get(param, ()))
                merged.extend(d for d in decls if d not in merged)
                overrides[param] = merged
        usage_names = decorations.usage_for(callable)

        slots = []
        options = []
        has_kwargs = False

        parameters = list(signature.parameters.values())
        if skip_first:
            # a method command: self comes from the environment
            parameters = parameters[1:]
        for parameter in parameters:
            kind = parameter.kind
            has_default = parameter.default is not inspect.Parameter.empty

            if kind in (inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD):
                slots.append(Slot(
                    parameter.name,
                    usage_names.pop(parameter.name, parameter.name),
                    self.child_for(parameter, build),
                    required=not has_default,
                    default=parameter.default if has_default else NO_DEFAULT,
                    ))
                continue

            if kind is inspect.Parameter.VAR_POSITIONAL:
                annotation = parameter.annotation
                context = f"parameter *{parameter.name}"
                if annotation is inspect.Parameter.empty:
                    child = Terminal(str)
                else:
                    annotation = dereference_annotated(annotation)
                    if is_option(annotation):
                        raise AppealConfigurationError(
                            f"{context}: {annotation.__name__} is an Option "
                            f"class; those are only meaningful on options")
                    if getattr(annotation, '__origin__', None) is tuple:
                        # *args: tuple[X, Y, Z] -- each command-line instance
                        # consumes N operands and builds a tuple, so the flat
                        # operand stream chunks by N (six operands -> two
                        # (X, Y, Z) tuples; five -> a "left over" shortfall).
                        # A repeat of the fixed-arity tuple group; converter
                        # and nested-tuple elements ride the ordinary child
                        # logic and just work.
                        child = TuplePlan(annotation, parameter.name, build)
                    elif _is_repeat_group(annotation):
                        if not top:
                            raise AppealConfigurationError(
                                f"converter {name!r}: {context} takes a "
                                f"multi-parameter converter; windowed "
                                f"groups on *args inside a converter "
                                f"aren't in the grammar yet")
                        child = WindowPlan(annotation, context, build)
                    else:
                        child = Terminal(_leaf_callable(annotation, context))
                slots.append(Slot(
                    parameter.name,
                    usage_names.pop(parameter.name, parameter.name),
                    child,
                    required=False,
                    default=NO_DEFAULT,
                    repeat=True,
                    ))
                continue

            if kind is inspect.Parameter.KEYWORD_ONLY:
                # `default` is the value the parameter gets when the
                # option isn't given: ALWAYS the parameter's own default
                # (v1: ungiven kwargs simply aren't passed).  No default
                # (Larry, 2026-09-10, reversing v1's "options are always
                # optional"): the option is REQUIRED--the engine refuses a
                # line without it, before anything runs.
                default = parameter.default
                # the *grammar* pair--what kind of option is this, what
                # converter--normally also comes from the parameter...
                annotation = parameter.annotation
                grammar_default = parameter.default
                declarations = overrides.pop(parameter.name, None)
                metavar = usage_names.pop(parameter.name, None)
                if declarations is None:
                    rule = OptionRule.build(
                        parameter.name, _option_strings(parameter.name),
                        False, annotation, grammar_default, default, metavar,
                        build)
                    rule.required = not has_default
                    options.append(rule)
                    continue
                # @app.option maps STRINGS (ruled 2026-07-25, the
                # arglet style, superseding the July fresh-declaration
                # rule): the grammar comes from the PARAMETER unless
                # the declaration overrides it--annotation=/default=
                # are escape hatches, not obligations.  Blow-away
                # still applies to the strings (that's how you
                # suppress an unwanted short), and each call is still
                # its own rule (v1: go2's --north and --south each map
                # `direction` through a different converter).
                for declaration in declarations:
                    if not declaration['strings']:
                        # the explicit unmap: configured, no rule.
                        # Alongside a real declaration it's just
                        # noise, not a veto.
                        continue
                    decl_annotation = declaration['annotation']
                    if decl_annotation is inspect.Parameter.empty:
                        decl_annotation = annotation
                    decl_default = declaration['default']
                    if decl_default is inspect.Parameter.empty:
                        decl_default = grammar_default
                    rule = OptionRule.build(
                        parameter.name, declaration['strings'], True,
                        decl_annotation, decl_default,
                        default, metavar, build)
                    rule.required = not has_default
                    if declaration['config'] is not None:
                        rule.config_key = declaration['config']
                    options.append(rule)
                continue

            # every other parameter kind was consumed above, so this one IS
            # the **kwargs--legal: it exists to receive @app.option
            # declarations for parameters not in the signature (v1,
            # probed: absent ones simply aren't passed)
            assert kind is inspect.Parameter.VAR_KEYWORD
            has_kwargs = parameter.name

        if overrides and has_kwargs:
            # @app.option declarations for parameters the signature
            # doesn't have: delivered through **kwargs, and omitted
            # from the call entirely when not given (v1, probed)
            for extra_name in list(overrides):
                metavar = usage_names.pop(extra_name, None)
                for declaration in overrides.pop(extra_name):
                    if not declaration['strings']:
                        raise AppealConfigurationError(
                            f"@option for {extra_name!r}: no option "
                            f"strings, and no parameter to unmap--"
                            f"a **kwargs-delivered option IS its "
                            f"strings")
                    rule = OptionRule.build(
                        extra_name, declaration['strings'], True,
                        declaration['annotation'], declaration['default'],
                        None, metavar, build)
                    rule.kwargs_delivered = True
                    if declaration['config'] is not None:
                        rule.config_key = declaration['config']
                    options.append(rule)

        if usage_names:
            leftover = ', '.join(repr(n) for n in usage_names)
            raise AppealConfigurationError(
                f"{name!r}: @parameter names parameter(s) {leftover}, "
                f"which {name!r} doesn't have")
        if overrides:
            leftover = ', '.join(repr(n) for n in overrides)
            raise AppealConfigurationError(
                f"{name!r}: @option names parameter(s) {leftover}, which "
                f"aren't keyword-only-with-default parameters of {name!r}--"
                f"and there's no **kwargs to deliver them into (v1's rule: "
                f"such options need a **kwargs to land in)")

        # uniform end-reservation: a required leaf operand that FOLLOWS an
        # absorbing converter (one that consumes unboundedly -- its own *args)
        # is reserved from the END, so the absorber leaves room for it.  It's
        # a named param, so it's delivered by keyword like any other trailing
        # operand.
        absorbing = False
        for slot in slots:
            if (absorbing and slot.required
                    and isinstance(slot.child, Terminal)):
                slot.trailing = True
                continue
            if slot.repeat or (not isinstance(slot.child, Terminal)
                               and any(s.repeat for s in slot.child.slots)):
                absorbing = True
        super().__init__(callable, name, slots, options)
        self.var_keyword = has_kwargs or None
        self.analyze()
        if top:
            self.finalize_options(build.default_options, build.app)
            self.gated = self.mark_barriers(certain=True)


class ClassPlan(SignaturePlan):
    """
    A class as a command: its __init__ is the grammar, and calling the
    plan constructs the instance, which execution stashes under
    `constructs` (the class's qualname) for its method commands to
    bind to.  `binds`, when set, is the parent instance a nested
    class command is reached through.
    """
    __slots__ = ('constructs', 'binds')

    def __init__(self, callable, build, *, name=None, binds=None):
        super().__init__(callable, build, name=name, top=True)
        self.constructs = callable.__qualname__
        self.binds = binds


class MethodPlan(SignaturePlan):
    """
    A method as a command: `self` comes from the environment (the
    instance stashed under `binds`), not the command line, so the
    signature's first parameter is skipped and the call supplies it.
    """
    __slots__ = ('binds',)

    def __init__(self, callable, build, *, name=None, binds):
        super().__init__(callable, build, name=name, top=True, skip_first=True)
        self.binds = binds

    def __call__(self, args, kwargs, bound):
        return self.callable(bound, *args, **kwargs)


class BoundInnerPlan(ClassPlan):
    """
    A wrapped nested class (big's BoundInnerClass): at run time it is
    constructed via attribute access on the parent instance, so the
    grammar is whatever that attribute accepts--asked of the
    descriptor with a throwaway instance (Appeal doesn't know the
    wrapper; the descriptor protocol answers for it)--and the call
    goes through the real parent instance's attribute, which re-binds
    the descriptor.
    """
    __slots__ = ('attribute',)

    def __init__(self, callable, build, *, name=None, binds):
        class _Probe:
            pass
        try:
            grammar = callable.__get__(_Probe(), _Probe)
        except Exception:
            grammar = callable
        super().__init__(grammar, build,
                         name=name or getattr(callable, '__name__', None),
                         binds=binds)
        self.constructs = callable.__qualname__
        self.attribute = callable.__qualname__.rpartition('.')[2]

    def __call__(self, args, kwargs, bound):
        return getattr(bound, self.attribute)(*args, **kwargs)


class WindowPlan(SignaturePlan):
    """
    A converter group on *args: each command-line instance consumes
    up to the group's operand count--optional parameters fill
    GREEDILY per instance, v1's semantics (RULED, Larry,
    2026-08-05: the fixed-arity staging is dead)--and the group's
    options bind to instances by position, the window rule.  Built
    with a fresh memo: windowed-ness is a property of this use
    site, not of the converter.  Every parameter must be a
    terminal (a converter inside a *args group is still out of
    the grammar).
    """
    __slots__ = ()
    windowed = True

    def __init__(self, callable, context, build):
        super().__init__(callable, build.fresh())
        for slot in self.slots:
            if not isinstance(slot.child, Terminal):
                raise AppealConfigurationError(
                    f"{context}: converter {slot.child.name!r} on "
                    f"{slot.name!r} inside a *args group awaits the "
                    f"streaming driver")
        if not self.maximum:
            raise AppealConfigurationError(
                f"{context}: a *args converter group must be able to "
                f"consume at least one argument per instance")


class IterablePlan(Plan):
    """
    A group whose operands become a container (tuple or list): nothing
    to call, nothing to render--the container is built from the
    converted operands.  Its identity carries its element shape, since
    every tuple[...] shares the callable `tuple`.
    """
    __slots__ = ()

    def __call__(self, args, kwargs, bound):
        return self.callable(args)

    @property
    def key(self):
        return (self.callable, self.shape)

    @property
    def shape(self):
        "A hashable signature of the element slots (converter + shape)."
        return tuple(
            ((slot.child.converter if isinstance(slot.child, Terminal)
              else slot.child.key),
             slot.required, slot.repeat, slot.trailing)
            for slot in self.slots)


class TuplePlan(IterablePlan):
    """
    tuple[T1, T2, ...Tn] on a positional parameter: one slot that
    consumes n operands and builds a tuple.  Elements recurse through
    the ordinary child logic, so converters and nested tuples inside
    a tuple just work.
    """
    __slots__ = ()

    def __init__(self, annotation, parameter_name, build):
        args = getattr(annotation, '__args__', ())
        if Ellipsis in args:
            raise AppealConfigurationError(
                f"parameter {parameter_name!r}: {annotation!r}--variable-"
                f"length tuples aren't in the grammar (use *args for "
                f"zero-or-more)")
        if not args or args == ((),):
            raise AppealConfigurationError(
                f"parameter {parameter_name!r}: {annotation!r} has no elements")
        slots = []
        for index, element in enumerate(args):
            element_name = f'{parameter_name}_{index}'
            synthetic = inspect.Parameter(
                element_name, inspect.Parameter.POSITIONAL_ONLY,
                annotation=element)
            slots.append(Slot(
                element_name,
                element_name,
                self.child_for(synthetic, build),
                required=True,
                default=NO_DEFAULT,
                ))
        super().__init__(tuple, parameter_name, slots, [])
        self.analyze()


class ElementsPlan(IterablePlan):
    """
    A group inferred from a list or tuple DEFAULT's element types
    (b=[0, 0.0] takes an int and a float and produces that sequence
    type; v1's corpus), constructing `container`.
    """
    __slots__ = ()

    def __init__(self, container, element_types, parameter_name):
        slots = []
        for index, element in enumerate(element_types):
            element_name = f'{parameter_name}_{index}'
            slots.append(Slot(
                element_name,
                element_name,
                Terminal(element),
                required=True,
                default=NO_DEFAULT,
                ))
        super().__init__(container, parameter_name, slots, [])
        self.analyze()


def build_plan(callable, name=None, method_of=None,
               default_options=default_options, app=None,
               extra_overrides=None, decorations=None):
    """
    Analyze a callable's signature and produce its Plan--the plan
    variety that fits: a class constructs (ClassPlan), a method binds
    to the instance stashed under `method_of` (MethodPlan), a wrapped
    nested class constructs through that instance (BoundInnerPlan),
    anything else is a plain SignaturePlan.  Then v1's optionality
    promotion (a defaulted operand followed by a required one can
    never be skipped, so it becomes required; re-analyze if anything
    moved) and the completion-protocol check.
    """
    build = Build(decorations, default_options, app, extra_overrides)
    wrapped_class = isinstance(getattr(callable, '__wrapped__', None), type)
    if method_of is not None and wrapped_class and hasattr(callable, '__get__'):
        plan = BoundInnerPlan(callable, build, name=name, binds=method_of)
    elif isinstance(callable, type) or wrapped_class:
        plan = ClassPlan(callable, build, name=name, binds=method_of)
    elif method_of is not None:
        plan = MethodPlan(callable, build, name=name, binds=method_of)
    else:
        plan = SignaturePlan(callable, build, name=name, top=True)
    if plan.promote_optionality():
        plan.reanalyze()
    plan.validate_completions()
    return plan



def strip_first_argument_from_signature(signature):
    "v1 utility: the signature minus its first parameter."
    parameters = list(signature.parameters.values())
    if not parameters:
        raise AppealConfigurationError(
            "signature has no parameters to strip")
    return signature.replace(parameters=parameters[1:])


def strip_self_from_signature(signature):
    "v1 utility: the signature minus a leading 'self', if present."
    parameters = list(signature.parameters.values())
    if parameters and parameters[0].name == 'self':
        return signature.replace(parameters=parameters[1:])
    return signature


_PROMOTE_INF = 1 << 29

