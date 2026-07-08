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
# is built into a child Plan.  Child plans are positional-only for
# now: options, *args, and trailing operands inside converters
# await the streaming driver (see the grammar's status table).

import inspect

from .plan import Terminal, NO_DEFAULT, OptionRule, Plan, Slot
from .runtime import AppealConfigurationError, MultiOption, Option


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
        return False
    if getattr(annotation, '__origin__', None) is not None:
        return False
    if hasattr(annotation, '__appeal_factory__'):
        return False
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
    if isinstance(annotation, type) and issubclass(annotation, Option):
        return False
    return _positional_arity(annotation) > 1


def _accepts_no_arguments(fn):
    "Callable with no parameters at all (a value-producing flag)?"
    try:
        signature = inspect.signature(fn)
    except (ValueError, TypeError):
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


def build(callable, name=None):
    """
    Analyze a callable's signature and produce its Plan.
    """
    return _build(callable, name, memo={}, stack=(), top=True)


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
    if isinstance(annotation, type) and issubclass(annotation, Option):
        converters = _fold_converters(
            annotation, f"parameter {parameter.name!r}")
        if len(converters) > 2:
            raise AppealConfigurationError(
                f"parameter {parameter.name!r}: {annotation.__name__} "
                f"takes multiple parameters per occurrence; a positional "
                f"Option class must take one individual object")
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
    kinds = {p.kind for p in signature.parameters.values()}
    if (inspect.Parameter.VAR_POSITIONAL in kinds
            or inspect.Parameter.VAR_KEYWORD in kinds):
        # *args/**kwargs converters consume one operand, like v1's
        # simple converters; multi-operand var-consumption inside
        # converters awaits the streaming driver
        return Terminal(annotation)
    return _build(annotation, None, memo, stack, top=False)


def _operand_converters(callable, context, what, skip_self=False):
    """
    The per-occurrence operand converters of a callable used as an
    option's grammar: one terminal per parameter, per its annotation.
    Staged: every parameter must be a required terminal.
    """
    parameters = list(inspect.signature(callable).parameters.values())
    if skip_self:
        parameters = parameters[1:]
    converters = []
    for parameter in parameters:
        if parameter.kind not in (inspect.Parameter.POSITIONAL_ONLY,
                                  inspect.Parameter.POSITIONAL_OR_KEYWORD):
            raise AppealConfigurationError(
                f"{context}: {what} parameter {parameter.name!r} must be "
                f"positional (fancier signatures await the streaming driver)")
        if parameter.default is not inspect.Parameter.empty:
            raise AppealConfigurationError(
                f"{context}: {what} parameter {parameter.name!r} can't have "
                f"a default (fancier signatures await the streaming driver)")
        annotation = parameter.annotation
        if annotation is inspect.Parameter.empty:
            annotation = str
        converters.append(_leaf_callable(annotation, context))
    return tuple(converters)


def _fold_leaf(cls, converters):
    """
    A positional Option/MultiOption: as a terminal converter it folds
    one occurrence from the command line; mapping readers hand it
    a whole sequence of occurrences (v1's corpus).
    """
    element = converters[1] if len(converters) > 1 else None

    def fold_positional(value):
        if isinstance(value, (list, tuple)):
            occurrences = list(value)
        else:
            occurrences = [value]
        instance = cls()
        instance.init(None)
        for occurrence in occurrences:
            if element is None:
                instance.option()
            else:
                instance.option(element(occurrence))
        return instance.render()
    fold_positional.__name__ = cls.__name__
    return fold_positional


def _fold_converters(cls, context):
    """
    An Option/MultiOption subclass: its option() method's signature
    defines the option's per-occurrence operands.
    """
    return (cls,) + _operand_converters(
        cls.option, context, f'{cls.__name__}.option()', skip_self=True)


def _is_repeat_group(annotation):
    """
    Should this *args converter be a per-instance GROUP (multiple
    operands and/or its own options) rather than a simple terminal?
    """
    if not callable(annotation):
        return False
    if annotation in _blessed_leaves:
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
    return _positional_arity(annotation) > 1


def _repeat_group_plan(annotation, context, memo, stack):
    """
    A converter group on *args: each command-line instance consumes
    the group's (fixed) operand count, and the group's options bind
    to instances by position--the window rule.  Built with a fresh
    memo: windowed-ness is a property of this use site, not of the
    converter.  Staged: every parameter must be a required terminal.
    """
    plan = _build(annotation, None, {}, stack, top=False)
    for slot in plan.slots:
        if not slot.required:
            raise AppealConfigurationError(
                f"{context}: optional parameter {slot.name!r} in a *args "
                f"converter group awaits the streaming driver (each "
                f"instance's operand count must be fixed)")
        if not isinstance(slot.child, Terminal):
            raise AppealConfigurationError(
                f"{context}: converter {slot.child.name!r} on {slot.name!r} "
                f"inside a *args group awaits the streaming driver")
    if plan.minimum < 1:
        raise AppealConfigurationError(
            f"{context}: a *args converter group must consume at least "
            f"one operand per instance")
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
        return rule

    if isinstance(annotation, type) and issubclass(annotation, Option):
        return finish(OptionRule(
            strings, name,
            kind='fold' if issubclass(annotation, MultiOption) else 'fold1',
            converters=_fold_converters(annotation, context),
            default=default))

    if (annotation is bool) or (
            annotation is inspect.Parameter.empty
            and isinstance(grammar_default, bool)):
        if grammar_default is not False:
            raise AppealConfigurationError(
                f"{context}: a flag's default must be False")
        return finish(OptionRule(
            strings, name, kind='flag', converters=(), default=default))

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
        # operands (v1, probed): --where X Y
        converters = ((annotation,)
                      + _operand_converters(
                            annotation, context,
                            f'{getattr(annotation, "__name__", "converter")}()'))
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


OPTION_OVERRIDES_ATTRIBUTE = '_appeal_option_overrides'
PARAMETER_USAGE_ATTRIBUTE = '_appeal_parameter_usage'


def add_parameter_usage(callable, parameter_name, usage):
    """
    The machinery behind @app.parameter: records, on the function
    itself, the usage presentation name for one of its parameters--
    the operand name in usage lines and help tables, or the metavar
    of an option (v1's @app.parameter only reached operands; the
    metavar extension is new).
    """
    if not (isinstance(usage, str) and usage):
        raise AppealConfigurationError(
            f"@parameter for {parameter_name!r}: usage must be a "
            f"nonempty string")
    names = getattr(callable, PARAMETER_USAGE_ATTRIBUTE, None)
    if names is None:
        names = {}
        setattr(callable, PARAMETER_USAGE_ATTRIBUTE, names)
    names[parameter_name] = usage


def add_option_override(callable, parameter_name, strings,
                        annotation=inspect.Parameter.empty,
                        default=inspect.Parameter.empty):
    """
    The machinery behind @app.option: records, on the function
    itself, that parameter_name's option should use these strings
    (replacing the auto-generated long and short), and optionally
    a different annotation and/or default.  Multiple calls for the
    same parameter accumulate strings.
    """
    if not strings:
        raise AppealConfigurationError(
            f"@option for {parameter_name!r}: no option strings specified")
    for s in strings:
        validate_option_string(s)
    overrides = getattr(callable, OPTION_OVERRIDES_ATTRIBUTE, None)
    if overrides is None:
        overrides = {}
        setattr(callable, OPTION_OVERRIDES_ATTRIBUTE, overrides)
    declaration = {'strings': tuple(strings), 'annotation': annotation,
                   'default': default}
    declarations = overrides.setdefault(parameter_name, [])
    if declaration not in declarations:
        # re-binding the same declaration is a no-op: the overrides
        # live on the function, and test harnesses (and REPLs)
        # re-apply decorators to module-level functions freely
        declarations.append(declaration)


def _build(callable, name, memo, stack, top):
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

    signature = inspect.signature(callable)
    stack = stack + (callable,)
    overrides = dict(getattr(callable, OPTION_OVERRIDES_ATTRIBUTE, None) or {})
    usage_names = dict(getattr(callable, PARAMETER_USAGE_ATTRIBUTE, None) or {})

    slots = []
    trailing_slots = []
    options = []
    seen_defaulted_kwonly = False
    has_kwargs = False

    for parameter in signature.parameters.values():
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
            if not top:
                raise AppealConfigurationError(
                    f"converter {name!r}: *{parameter.name} inside a converter "
                    f"is not yet in the grammar")
            annotation = parameter.annotation
            context = f"parameter *{parameter.name}"
            if annotation is inspect.Parameter.empty:
                child = Terminal(str)
            else:
                annotation = dereference_annotated(annotation)
                if isinstance(annotation, type) and issubclass(annotation, Option):
                    raise AppealConfigurationError(
                        f"{context}: {annotation.__name__} is an Option "
                        f"class; those are only meaningful on options")
                if _is_repeat_group(annotation):
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
            if not has_default and not top:
                raise AppealConfigurationError(
                    f"converter {name!r}: trailing operand {parameter.name!r} "
                    f"inside a converter is not yet in the grammar")
            if not has_default:
                # keyword-only with no default: a *required trailing
                # operand* (the `cp SRC... DST` shape).  Must precede
                # any defaulted keyword-only parameter.
                if seen_defaulted_kwonly:
                    raise AppealConfigurationError(
                        f"parameter {parameter.name!r}: required trailing operands "
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
            # @app.option is a FRESH DECLARATION (v1 semantics, by
            # ruling): it blows away all default mappings--that's
            # how you suppress an unwanted short--and each call is
            # its own rule (v1: go2's --north and --south each map
            # `direction` through a different converter).  The
            # parameter's own annotation and default do not leak in.
            for declaration in declarations:
                options.append(_build_option_rule(
                    parameter.name, declaration['strings'], True,
                    declaration['annotation'], declaration['default'],
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
            f"aren't keyword-only-with-default parameters of {name!r} "
            f"(options via **kwargs aren't in the grammar yet)")

    slots = slots + trailing_slots
    plan = Plan(callable, name, slots, options, 0, 0, None)
    plan.var_keyword = has_kwargs or None
    _analyze(plan)

    if not top:
        memo[callable] = plan
    else:
        _finalize_options(plan)
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


def _finalize_options(plan):
    """
    The whole-command view of the options: option strings must be
    globally unique (per-scope shadowing awaits the streaming
    driver), and each option gets a short string--its parameter's
    first letter--if that letter is still free.  First declared,
    first served (walk order: a rule's own options, then its
    children's, depth-first).
    """
    taken = set()
    pairs = all_options(plan)
    for owner, option in pairs:
        for s in option.strings:
            if s in taken:
                raise AppealConfigurationError(
                    f"option {s!r} (parameter {option.name!r} of "
                    f"{owner.name!r}) is already defined; option strings "
                    f"must be unique across a command until scoped "
                    f"options land")
            taken.add(s)
    for owner, option in pairs:
        if getattr(option, 'explicit', False):
            # @app.option strings are the whole story
            continue
        short = '-' + option.name[0]
        if short not in taken:
            taken.add(short)
            option.strings = (short,) + option.strings
    for owner, option in pairs:
        if not option.strings:
            raise AppealConfigurationError(
                f"option {option.name!r} (of {owner.name!r}) has no option "
                f"strings: its name is too short for a long option and its "
                f"letter is already taken")


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

    for slot in non_trailing:
        if slot.repeat:
            slot.count_options = None
            continue
        if isinstance(slot.child, Terminal):
            counts = (1,) if slot.required else (1, 0)
        else:
            child_counts = set(slot.child.valid_counts)
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
        elif counts is None:
            suffix = (None, minimum + min(slot.count_options))
        else:
            suffix = (
                frozenset(c + x for c in slot.count_options for x in counts),
                minimum + min(slot.count_options),
                )

    whole_counts, whole_minimum = suffix
    plan.minimum = whole_minimum + n_trailing
    if whole_counts is None:
        plan.maximum = None
        plan.valid_counts = None
    else:
        shifted = {c + n_trailing for c in whole_counts}
        plan.maximum = max(shifted)
        plan.valid_counts = shifted
