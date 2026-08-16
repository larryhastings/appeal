#!/usr/bin/env python3
#
# appeal/build.py
# Part of Appeal v2.
# Copyright 2021-2026 by Larry Hastings
#
# The compiler front half that CPython already wrote for us:
# inspect.signature hands over the grammar as structured data,
# and we do semantic analysis on it.  build() produces a Plan.
#
# Recursion: a positional parameter annotated with a non-blessed
# callable becomes a *nonterminal*--its converter's own signature
# is built into a child Plan, full grammar: options, *args (an
# absorbing subtree), and trailing arguments (the uniform
# end-reservation rule) all work at depth.

import inspect

from .plan import Terminal, NO_DEFAULT, OptionRule, Plan, Slot
from .runtime import (
    AppealConfigurationError, Option, is_multioption, is_option,
    )


# terminal converters: called with one operand string, never introspected
_blessed_leaves = {str, int, float, bool}

# the terminal converters we bless for annotation-free defaults
_default_type_converters = {str, int, float}


# Annotated[T, converter] -> converter.  T is for mypy; the converter
# (the last metadata element) is for us.  Annotated shipped in 3.9;
# on older Pythons Appeal limps along without it (the annotation
# passes through untouched--and since you can't *write* Annotated
# there, nothing is lost).  This is v1's pattern, kept verbatim.
try:
    from typing import Annotated as _Annotated
    _AnnotatedType = type(_Annotated[int, str])
    del _Annotated

    def dereference_annotated(annotation):
        if isinstance(annotation, _AnnotatedType):
            return annotation.__metadata__[-1]
        return annotation
except ImportError:   # pragma: no cover -- Python 3.8 and earlier
    def dereference_annotated(annotation):
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
    if not callable(annotation) or isinstance(annotation, type):
        return False
    if annotation in _blessed_leaves:
        return False   # pragma: no cover -- the blessed leaves are all
                       # types, and types bailed at the isinstance check
    if getattr(annotation, '__origin__', None) is not None:
        return False
    if hasattr(annotation, '__appeal_factory__'):
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
                        and annotation_ not in _blessed_leaves
                        and not hasattr(annotation_, '__appeal_recipe__')
                        and getattr(annotation_, '__origin__', None) is None
                        and _positional_arity(annotation_) > 0):
                    return True
    return False


def _is_multiparam_converter(annotation):
    "A plain converter whose signature consumes several operands?"
    if not callable(annotation):
        return False
    if annotation in _blessed_leaves:
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
    factory = getattr(annotation, '__appeal_factory__', None)
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
    if annotation in _blessed_leaves:
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


def _validate_completions(plan):
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
        try:
            inspect.signature(completions).bind('')
        except TypeError:
            raise AppealConfigurationError(
                f"{where}.completions must accept one positional "
                f"argument (the prefix)") from None

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

    walk(plan)


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
# at build time only; its output--the strings--is what the
# compiled parser bakes, so a custom policy never rides along into
# a standalone script.

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
    app (None for appless build() calls).
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


def build(callable, name=None, method_of=None,
          default_options=default_options, app=None,
          extra_overrides=None, decorations=None):
    """
    Analyze a callable's signature and produce its Plan.

    method_of compiles a function found in a class body as a
    method command: the first parameter (self) is supplied by the
    execution environment, not the command line--the plan binds to
    the instance stashed under that key.  A class analyzes as its
    constructor (inspect.signature skips self; __new__ works the
    ordinary Python way, because calling the class is all we do)
    and its plan constructs: execution stashes the instance.
    """
    wrapped_class = isinstance(getattr(callable, '__wrapped__', None),
                               type)
    grammar = callable
    if (method_of is not None and wrapped_class
            and hasattr(callable, '__get__')):
        # a wrapped nested class (e.g. big's BoundInnerClass): at
        # runtime it is constructed via attribute access on the
        # parent instance, so the grammar is whatever that
        # attribute accepts--ask the descriptor with a throwaway
        # instance.  (Appeal doesn't know the wrapper; the
        # descriptor protocol answers for it.)
        class _Probe:
            pass
        try:
            grammar = callable.__get__(_Probe(), _Probe)
        except Exception:
            grammar = callable
    memo = {}
    if decorations is not None:
        memo[_DECORATIONS_KEY] = decorations
    plan = _build(grammar, name or getattr(callable, '__name__', None),
                  memo=memo, stack=(), top=True,
                  skip_first=method_of is not None
                  and not isinstance(callable, type)
                  and not wrapped_class,
                  default_options=default_options, app=app,
                  extra_overrides=extra_overrides)
    if isinstance(callable, type) or wrapped_class:
        plan.constructs = callable.__qualname__
    if method_of is not None:
        plan.binds = method_of
    # v1's optionality promotion: a defaulted operand followed by a
    # required one (across group nesting) can never be skipped, so
    # it becomes required.  Re-analyze if anything moved.
    if _promote_optionality(plan):
        _reanalyze(plan)
    _validate_completions(plan)
    return plan


def _child_for(parameter, memo, stack):
    """
    Decide a positional parameter's child: a Terminal, or a child Plan
    (recursion--the heart of the metaphor).
    """
    annotation = parameter.annotation
    if annotation is inspect.Parameter.empty:
        default = parameter.default
        if default is not inspect.Parameter.empty:
            if (isinstance(default, list) and default
                    and all(type(e) in _default_type_converters
                            for e in default)):
                # a list default infers a group from its element
                # types: b=[0, 0.0] takes an int and a float and
                # produces a list (v1's corpus)
                return _elements_plan(list, [type(e) for e in default],
                                      parameter.name)
            t = type(default)
            if t in _default_type_converters or t is bool:
                # bool: v1's corpus reads these from mappings; on a
                # command line the runtime parses true/false/yes/no
                # strictly, never truthiness
                return Terminal(t)
        return Terminal(str)
    annotation = dereference_annotated(annotation)
    _refuse_bare_factory(annotation, f"parameter {parameter.name!r}")
    if (hasattr(annotation, '__appeal_recipe__')
            and not isinstance(annotation, type)):
        # a vocabulary product (validate(...), split(...)): a terminal,
        # so its ValueError becomes a polite UsageError via convert()
        return Terminal(annotation)
    if is_option(annotation):
        converters, _ = _fold_converters(
            annotation, f"parameter {parameter.name!r}")
        return Terminal(_fold_leaf(annotation, converters))
    if getattr(annotation, '__origin__', None) is tuple:
        return _tuple_plan(annotation, parameter.name, memo, stack)
    if _generic_origin(annotation) is not None:
        raise AppealConfigurationError(
            f"parameter {parameter.name!r}: {annotation!r} is only meaningful "
            f"on an option (a keyword-only parameter with a default)")
    if not callable(annotation):
        raise AppealConfigurationError(
            f"parameter {parameter.name!r}: annotation {annotation!r} isn't callable")
    if annotation in _blessed_leaves:
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
    return _build(annotation, None, memo, stack, top=False,
                  allow_trailing=True)


def _operand_converters(callable, context, what, skip_self=False,
                        allow_defaults=False):
    """
    The per-occurrence operand converters of a callable used as an
    option's grammar: one terminal per parameter, per its
    annotation.  Returns (converters, minimum): with
    allow_defaults, trailing parameters may have defaults--those
    operands are optional, consumed greedily when tokens remain
    (v1)--and minimum counts the required ones.
    """
    parameters = list(inspect.signature(callable).parameters.values())
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
            inner, _ = _fold_converters(annotation, context)
            converters.append(_fold_leaf(annotation, inner))
            continue
        converters.append(_leaf_callable(annotation, context))
    return tuple(converters), minimum


def _fold_leaf(cls, converters):
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
        return instance.render()
    fold_positional.__name__ = cls.__name__
    return fold_positional


def _fold_converters(cls, context, allow_defaults=False):
    """
    An Option/MultiOption subclass: its option() method's signature
    defines the option's per-occurrence operands.  Returns
    (converters, minimum): the class plus one converter per
    parameter, and how many of those parameters are required.
    """
    converters, minimum = _operand_converters(
        cls.option, context, f'{cls.__name__}.option()', skip_self=True,
        allow_defaults=allow_defaults)
    return (cls,) + converters, minimum


def _is_repeat_group(annotation):
    """
    Should this *args converter be a per-instance GROUP (multiple
    operands and/or its own options) rather than a simple terminal?
    """
    if not callable(annotation):
        return False
    if annotation in _blessed_leaves:
        return False
    if (hasattr(annotation, '__appeal_recipe__')
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


def _repeat_group_plan(annotation, context, memo, stack):
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
    # a fresh memo (no shared cache), but the decorations registry
    # rides along--windowed-ness is per-use, decorations aren't
    fresh = {}
    if _DECORATIONS_KEY in memo:
        fresh[_DECORATIONS_KEY] = memo[_DECORATIONS_KEY]
    plan = _build(annotation, None, fresh, stack, top=False)
    for slot in plan.slots:
        if not isinstance(slot.child, Terminal):
            raise AppealConfigurationError(
                f"{context}: converter {slot.child.name!r} on {slot.name!r} "
                f"inside a *args group awaits the streaming driver")
    if not plan.maximum:
        raise AppealConfigurationError(
            f"{context}: a *args converter group must be able to consume "
            f"at least one argument per instance")
    plan.windowed = True
    return plan


def _elements_plan(container, element_types, parameter_name):
    """
    A group built from terminal element types, constructing `container`
    (list or tuple)--nothing to call, nothing to render.
    """
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
    plan = Plan(container, parameter_name, slots, [], 0, 0, None)
    _analyze(plan)
    return plan


def _tuple_plan(annotation, parameter_name, memo, stack):
    """
    tuple[T1, T2, ...Tn] on a positional parameter: one slot that
    consumes n operands and builds a tuple.  Modeled as a child Plan
    whose callable is the builtin `tuple`; the interpreter and codegen
    both special-case construction (a tuple display, no call), so
    there's nothing to import--standalone-safe by construction.
    Elements recurse through the ordinary child logic, so converters
    and nested tuples inside a tuple just work.
    """
    args = getattr(annotation, '__args__', ())
    if Ellipsis in args:
        raise AppealConfigurationError(
            f"parameter {parameter_name!r}: {annotation!r}--variable-length "
            f"tuples aren't in the grammar (use *args for zero-or-more)")
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
            _child_for(synthetic, memo, stack),
            required=True,
            default=NO_DEFAULT,
            ))
    plan = Plan(tuple, parameter_name, slots, [], 0, 0, None)
    _analyze(plan)
    return plan



def _build_option_rule(name, strings, explicit, annotation, grammar_default,
                       default, metavar, stack=()):
    """
    One option's rule, from its (annotation, grammar_default) pair--
    the shared engine behind keyword-only parameters, @app.option
    fresh declarations, and **kwargs-delivered options.
    """
    if annotation is not inspect.Parameter.empty:
        annotation = dereference_annotated(annotation)
        _refuse_bare_factory(annotation, f"option {name!r}")
    context = f"option {name!r}"

    def finish(rule):
        rule.explicit = explicit
        rule.usage_name = metavar
        rule.annotation = annotation
        return rule

    if is_option(annotation):
        converters, fold_minimum = _fold_converters(
            annotation, context, allow_defaults=True)
        rule = OptionRule(
            strings, name,
            kind='fold' if is_multioption(annotation) else 'fold1',
            converters=converters,
            default=default)
        rule.fold_minimum = fold_minimum
        return finish(rule)

    if (annotation is bool) or (
            annotation is inspect.Parameter.empty
            and isinstance(grammar_default, bool)):
        # presence stores `not default` (v1, restored 2026-07-18--
        # Larry's break #1: v2's first cut refused any default but
        # False, wrongly assuming a flag must store True.  v1's
        # rule is better: a default-True flag is how you spell
        # "turn this default-on thing OFF").  The explicit
        # --flag=true/false spellings set the literal value.
        rule = OptionRule(
            strings, name, kind='flag', converters=(), default=default)
        rule.present = not grammar_default
        return finish(rule)

    if (callable(annotation)
            and not isinstance(annotation, type)
            and getattr(annotation, '__origin__', None) is None
            and not hasattr(annotation, '__appeal_factory__')
            and annotation is not inspect.Parameter.empty
            and _positional_arity(annotation) == 0
            and _accepts_no_arguments(annotation)):
        # a zero-argument converter: a flag that produces a value
        # by calling it (v1: @app.option('direction', '--north',
        # annotation=north) where north() returns 'north')
        return finish(OptionRule(
            strings, name, kind='nullary', converters=(annotation,),
            default=default))

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
            return finish(OptionRule(
                strings, name, kind='value', converters=converters,
                default=default))

    if origin is list:
        (t,) = annotation.__args__
        return finish(OptionRule(
            strings, name, kind='accumulate',
            converters=(_leaf_callable(t, context),), default=default))

    if origin is dict:
        k, v = annotation.__args__
        return finish(OptionRule(
            strings, name, kind='mapping',
            converters=(_leaf_callable(k, context),
                        _leaf_callable(v, context)),
            default=default))

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
            return _build_option_rule(name, strings, explicit, t,
                                      inspect.Parameter.empty, default,
                                      metavar, stack)
        else:
            converters = (str,)
    elif _is_option_group(annotation):
        # a converter with its own grammar--optional parameters
        # and/or options: the option consumes the group's MINIMUM
        # operands inline; inner optional groups enter only when
        # their own options force them (v1's -g gloopy ... -i 1 3.0)
        child = _build(annotation, None, {}, stack, top=False)
        rule = OptionRule(
            strings, name, kind='group', converters=(annotation,),
            default=default)
        rule.child = child
        return finish(rule)
    elif _is_multiparam_converter(annotation):
        # a converter with several parameters consumes several
        # operands (v1, probed): --where X Y.  All required--a
        # parameter with a default makes the converter a group.
        operand_converters, _ = _operand_converters(
            annotation, context,
            f'{getattr(annotation, "__name__", "converter")}()')
        converters = (annotation,) + operand_converters
    else:
        converters = (_leaf_callable(annotation, context),)
    return finish(OptionRule(
        strings, name, kind='value', converters=converters, default=default))


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
                   default=inspect.Parameter.empty):
        # zero strings is legal (ruled 2026-07-25): "I'm speaking
        # for this parameter: nothing"--the explicit per-parameter
        # unmap, symmetric with a policy declining.  The parameter
        # stays keyword-only, its default always fills.
        for s in strings:
            validate_option_string(s)
        declaration = {'strings': tuple(strings),
                       'annotation': annotation, 'default': default}
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


# the decorations ride the build's memo dict under this sentinel:
# the memo threads through every recursion site already, and its
# other keys are callables, so the sentinel can't collide
_DECORATIONS_KEY = object()
_NO_DECORATIONS = Decorations()


def _decorations_of(memo):
    return memo.get(_DECORATIONS_KEY) or _NO_DECORATIONS


def _build(callable, name, memo, stack, top, skip_first=False,
           allow_trailing=None, default_options=default_options,
           app=None, extra_overrides=None):
    if callable in stack:
        cycle = ' -> '.join(getattr(c, '__name__', repr(c)) for c in stack)
        raise AppealConfigurationError(
            f"converter cycle: {cycle} -> {getattr(callable, '__name__', repr(callable))}")
    if not top:
        cached = memo.get(callable)
        if cached is not None:
            return cached

    if name is None:
        name = getattr(callable, '__name__', None)
        if not name:
            raise AppealConfigurationError(f"can't determine a name for {callable!r}")

    if allow_trailing is None:
        allow_trailing = top
    signature = inspect.signature(callable)
    stack = stack + (callable,)
    decorations = _decorations_of(memo)
    overrides = {param: list(decls) for param, decls
                 in decorations.overrides_for(callable).items()}
    if extra_overrides:
        # per-app declarations delivered directly (the bound
        # precommand's version/help strings)
        for param, decls in extra_overrides.items():
            merged = list(overrides.get(param, ()))
            merged.extend(d for d in decls if d not in merged)
            overrides[param] = merged
    usage_names = decorations.usage_for(callable)

    slots = []
    trailing_slots = []
    options = []
    seen_defaulted_kwonly = False
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
                _child_for(parameter, memo, stack),
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
                if _is_repeat_group(annotation):
                    if not top:
                        raise AppealConfigurationError(
                            f"converter {name!r}: {context} takes a "
                            f"multi-parameter converter; windowed "
                            f"groups on *args inside a converter "
                            f"aren't in the grammar yet")
                    child = _repeat_group_plan(annotation, context, memo, stack)
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
            if not has_default and not allow_trailing:
                raise AppealConfigurationError(
                    f"converter {name!r}: trailing argument "
                    f"{parameter.name!r} isn't in the grammar here--"
                    f"only positional converters may carry trailing "
                    f"arguments (an option's arguments are consumed "
                    f"inline; there's no end to reserve from)")
            if not has_default:
                # keyword-only with no default: a *required trailing
                # operand* (the `cp SRC... DST` shape).  Must precede
                # any defaulted keyword-only parameter.
                if seen_defaulted_kwonly:
                    raise AppealConfigurationError(
                        f"parameter {parameter.name!r}: required trailing arguments "
                        f"(keyword-only, no default) must come before all options "
                        f"(keyword-only with defaults)")
                annotation = parameter.annotation
                if annotation is inspect.Parameter.empty:
                    child = Terminal(str)
                else:
                    # simple converters only, same rule as *args
                    child = Terminal(_leaf_callable(
                        annotation, f"parameter {parameter.name!r}"))
                trailing_slots.append(Slot(
                    parameter.name,
                    usage_names.pop(parameter.name, parameter.name),
                    child,
                    required=True,
                    default=NO_DEFAULT,
                    trailing=True,
                    ))
                continue

            seen_defaulted_kwonly = True
            # `default` is the value the parameter gets when the
            # option isn't given: ALWAYS the parameter's own default
            # (v1: ungiven kwargs simply aren't passed).
            default = parameter.default
            # the *grammar* pair--what kind of option is this, what
            # converter--normally also comes from the parameter...
            annotation = parameter.annotation
            grammar_default = parameter.default
            declarations = overrides.pop(parameter.name, None)
            metavar = usage_names.pop(parameter.name, None)
            if declarations is None:
                options.append(_build_option_rule(
                    parameter.name, _option_strings(parameter.name),
                    False, annotation, grammar_default, default, metavar,
                    stack))
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
                options.append(_build_option_rule(
                    parameter.name, declaration['strings'], True,
                    decl_annotation, decl_default,
                    default, metavar, stack))
            continue

        if kind is inspect.Parameter.VAR_KEYWORD:
            # legal: it exists to receive @app.option declarations
            # for parameters not in the signature (v1, probed:
            # absent ones simply aren't passed)
            has_kwargs = parameter.name
            continue

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
                rule = _build_option_rule(
                    extra_name, declaration['strings'], True,
                    declaration['annotation'], declaration['default'],
                    None, metavar)
                rule.kwargs_delivered = True
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

    slots = slots + trailing_slots
    plan = Plan(callable, name, slots, options, 0, 0, None)
    plan.var_keyword = has_kwargs or None
    _analyze(plan)

    if not top:
        memo[callable] = plan
    else:
        _finalize_options(plan, default_options, app)
        plan.gated = _mark_barriers(plan, certain=True)
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


def help_option_strings(plan):
    """
    The automatic help strings: ('-h', '--help'), minus anything
    the program claimed for itself.  If the program took '--help',
    there is no automatic help at all--the user wins.  Help strings
    never appear in usage (v1, probed).
    """
    if not plan.auto_help:
        # the app's help= knob is off: no automatic help at all
        return ()
    taken = {s for _, o in all_options(plan) for s in o.strings}
    if '--help' in taken:
        return ()
    return tuple(s for s in ('-h', '--help') if s not in taken)


def all_options(plan, _out=None, _seen=None):
    """
    Every (owner plan, option) pair in the tree: the plan's own
    options first, then each child's, depth-first in slot order.
    """
    if _out is None:
        _out = []
        _seen = set()
    if id(plan) in _seen:
        return _out
    _seen.add(id(plan))
    for option in plan.options:
        _out.append((plan, option))
        if option.child is not None:
            all_options(option.child, _out, _seen)
    for slot in plan.slots:
        if not isinstance(slot.child, Terminal):
            all_options(slot.child, _out, _seen)
    return _out


def subtree_option_keys(plan):
    "The keys of every option declared by plan or its descendants."
    return [option.key for owner, option in all_options(plan)]


def _mark_barriers(plan, certain):
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
    plan.certain = certain
    any_barriers = False
    for slot in plan.slots:
        if isinstance(slot.child, Terminal):
            continue
        slot.barrier = (certain and slot.required and not slot.repeat
                        and not slot.trailing and slot.child.minimum > 0)
        below = _mark_barriers(slot.child,
                               certain and slot.required and not slot.repeat)
        any_barriers = any_barriers or slot.barrier or below
    return any_barriers


def _count_sites(plan):
    """
    How many times each plan instantiates in the tree--a converter
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
    walk(plan, 1)
    return sites


def _finalize_options(plan, default_options=default_options,
                      app=None):
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
    sites = _count_sites(plan)
    pairs = all_options(plan)
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
        pairs = all_options(plan)
        sites = _count_sites(plan)
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
                    if getattr(owner, 'windowed', False)]
        total = sum(sites[id(owner)] for owner, _ in entries
                    if not getattr(owner, 'windowed', False))
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
    plan.scoped_keys = frozenset(scoped)
    taken = set(declared)
    seen_rules = set()
    for owner, option in pairs:
        if getattr(option, 'explicit', False):
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
                    plan.scoped_keys = frozenset(scoped)
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
    option_children = {id(o.child): o.key for o in plan.options
                       if o.kind == 'group' and o.child is not None}
    sibling_keys = set()
    for s in plan.scoped_keys:
        declarers = [owner for owner, o in pairs if s in o.strings]
        if declarers and all(id(d) in option_children
                             for d in declarers):
            sibling_keys.add(s)
    if sibling_keys:
        parents = []
        for o in plan.options:
            if o.kind != 'group' or o.child is None:
                continue
            keys = tuple(k for k in sorted(sibling_keys)
                         if any(owner is o.child and k in opt.strings
                                for owner, opt in pairs))
            if keys:
                parents.append((o.key, keys))
        plan.sibling_parents = tuple(parents)
        plan.sibling_keys = frozenset(sibling_keys)


def _analyze(plan):
    """
    The counting automaton's tables.

    Fills in, for every non-trailing slot:
      * count_options -- the operand counts it can consume, sorted
        descending (None for a repeat slot);
      * suffix_after -- (counts, minimum) completable by the slots
        after it: counts is a frozenset, or None meaning unbounded.
    And on the plan: minimum, maximum, valid_counts (trailing
    operands shift everything by their count).
    """
    non_trailing = [s for s in plan.slots if not s.trailing]
    n_trailing = sum(1 for s in plan.slots if s.trailing)
    plan.tree_trailing = n_trailing + sum(
        s.child.tree_trailing for s in non_trailing
        if not isinstance(s.child, Terminal))

    for slot in non_trailing:
        if slot.repeat:
            slot.count_options = None
            continue
        if isinstance(slot.child, Terminal):
            counts = (1,) if slot.required else (1, 0)
        elif slot.child.valid_counts is None:
            # an absorbing slot: its converter contains *args, so
            # it consumes unboundedly--like a repeat slot, it takes
            # what the slots after it don't need (v1 refused these
            # shapes; v2's completable distribution reads them)
            slot.count_options = None
            continue
        else:
            # distribution runs in body space: a child subtree's
            # trailing arguments come from the end of the whole
            # command's stream, not from this window
            child_counts = set(slot.child.body_valid_counts)
            if not slot.required:
                child_counts.add(0)
            counts = tuple(sorted(child_counts, reverse=True))
        slot.count_options = counts

    # suffix sets, built right to left
    suffix = (frozenset({0}), 0)
    for slot in reversed(non_trailing):
        slot.suffix_after = suffix
        counts, minimum = suffix
        if slot.repeat:
            suffix = (None, minimum)
        elif slot.count_options is None:
            # absorbing: unbounded above its child's minimum (0 if
            # the slot is skippable)
            floor = 0 if not slot.required else slot.child.body_minimum
            suffix = (None, minimum + floor)
        elif counts is None:
            suffix = (None, minimum + min(slot.count_options))
        else:
            suffix = (
                frozenset(c + x for c in slot.count_options for x in counts),
                minimum + min(slot.count_options),
                )

    whole_counts, whole_minimum = suffix
    # the stream footprint: body plus every trailing argument in
    # the subtree (they all come from the end of the stream)
    plan.minimum = whole_minimum + plan.tree_trailing
    if whole_counts is None:
        plan.maximum = None
        plan.valid_counts = None
    else:
        shifted = {c + plan.tree_trailing for c in whole_counts}
        plan.maximum = max(shifted)
        plan.valid_counts = shifted


def _shallow_slots(obj):
    "copy.copy for a __slots__ object (no copy module in standalone)."
    new = obj.__class__.__new__(obj.__class__)
    for klass in type(obj).__mro__:
        for name in getattr(klass, '__slots__', ()):
            if hasattr(obj, name):
                setattr(new, name, getattr(obj, name))
    return new


def _clone_tree(plan):
    """
    A shallow copy of a Plan whose slot/child spine is deep-copied
    (options, Terminals, converters shared).  Used to un-share a
    Plan the build memo handed to more than one slot, so optionality
    promotion--which is context-specific--can't cross-contaminate.
    """
    clone = _shallow_slots(plan)
    slots = []
    for s in plan.slots:
        ns = _shallow_slots(s)
        if isinstance(ns.child, Plan):
            ns.child = _clone_tree(ns.child)
        slots.append(ns)
    clone.slots = slots
    return clone


def _unshare(plan, seen):
    "Give every group slot its own subtree (the memo may share Plans)."
    for s in plan.slots:
        if isinstance(s.child, Plan):
            if id(s.child) in seen:
                s.child = _clone_tree(s.child)
            seen.add(id(s.child))
            _unshare(s.child, seen)


_PROMOTE_INF = 1 << 29


def _promote_walk(plan, parent_opt, lowest_required, mutate, flag):
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
                   for slot in plan.slots]
    lr = lowest_required
    for i in range(len(plan.slots) - 1, -1, -1):
        slot = plan.slots[i]
        if slot.trailing:
            continue
        opt = optionality[i]
        if isinstance(slot.child, Plan):
            returned = _promote_walk(slot.child, opt, lr, mutate, flag)
            if returned == parent_opt:
                lr = returned
        if opt > lr:
            if not slot.required:
                flag[0] = True
                if mutate:
                    slot.required = True
        elif slot.required:
            lr = min(lr, opt)
    return lr


def _promote_optionality(plan):
    """
    Promote defaulted operands that a required operand follows.
    Mutates slot.required in place; returns True if anything moved
    (the caller then re-analyzes the counts).  The tree is un-shared
    first, but only when a promotion is actually needed, so the
    common case (and deliberate converter dedup) is untouched.
    """
    flag = [False]
    _promote_walk(plan, 0, _PROMOTE_INF, False, flag)   # detect
    if not flag[0]:
        return False
    _unshare(plan, set())
    flag = [False]
    _promote_walk(plan, 0, _PROMOTE_INF, True, flag)    # apply
    return flag[0]


def _reanalyze(plan):
    "Re-run the counting automaton bottom-up after promotion."
    for slot in plan.slots:
        if isinstance(slot.child, Plan):
            _reanalyze(slot.child)
    _analyze(plan)
