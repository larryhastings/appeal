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


class Parameter:
    __slots__ = ('name', 'kind', 'default', 'annotation')

    # mirror inspect.Parameter's class-level constants, so callers can write
    # cheapsig.Parameter.KEYWORD_ONLY / .empty exactly as with inspect
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


class Signature:
    __slots__ = ('parameters',)
    empty = empty
    def __init__(self, parameters):
        self.parameters = parameters


def _resolve(callable):
    """
    Find the __code__-bearing function behind a callable and whether its first
    positional (self/cls) should be dropped -- mirroring what inspect.signature
    does for classes, methods, and callable instances.  Returns (func, skip).
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
        raise ValueError(f'no signature found for builtin type {callable!r}')

    # a callable instance: its __call__ (drop self)
    call = getattr(type(callable), '__call__', None)
    if call is not None and hasattr(call, '__code__'):
        return call, True

    raise ValueError(f'no signature found for {callable!r}')


def signature(callable):
    # exotic callables that publish their own signature (functools.partial,
    # C accelerators, decorators that set it): defer to real inspect -- rare,
    # and it's already paid for if we're here.
    sig = getattr(callable, '__signature__', None)
    if sig is not None:
        import inspect
        return inspect.signature(callable)

    func, skip = _resolve(callable)
    if func is None:
        return Signature({})

    co = func.__code__
    posonly = co.co_posonlyargcount
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
