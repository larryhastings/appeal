#!/usr/bin/env python3
#
# appeal/load.py
# Part of Appeal 1.0.
# Copyright 2021-2026 by Larry Hastings
#
# read_mapping and read_iterable: the third and fourth consumers of
# the Plan tree.  The same signature that defines a command-line
# grammar reads a config file: parameters pull values by name from a
# mapping (or by position from a sequence), converters apply, groups
# recurse into sub-mappings/sub-sequences, defaults fill absences.
#
# In-process API only: these aren't part of the command-line grammar.
#
# v1 parity where v1 0.6.4 works (probed): values pulled by name,
# converters always applied (already-typed values included), nested
# groups readable BOTH as a sub-mapping under
# the group's name AND as flat keys at the same level.  Where v1 is
# broken (defaults didn't fill; flags crashed; *args refused;
# read_iterable raised TypeError), v2 does the obvious thing.

from collections.abc import Mapping, Sequence

from .frontend import build_plan
from .frontend import Terminal, NO_DEFAULT, Plan, Nullary, Value
from .converters import boolean
from . import (
    AppealConfigurationError, AppealDataError, is_option,
    )





def _fail(message, path):
    # the error carries the innermost parameter's name as its param
    # (structural provenance: the config layer names the key it
    # blames from this, never from the message text)
    where = f" (at {path})" if path else ""
    param = path.rpartition('.')[2] if path else None
    raise AppealDataError(f"{message}{where}", param=param)


def _sub(path, name):
    return f'{path}.{name}' if path else name


def _is_sequence(value):
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _read_bool(value, path):
    """
    A flag read from a mapping (a config file, read_mapping): a bool
    constant, True or False, and nothing else (Larry, 2026-09-11)--
    the boolean LANGUAGE is for text sources.
    """
    if isinstance(value, bool):
        return value
    _not_a_constant(value, path)


def _not_a_constant(value, path):
    # Larry's wording (2026-09-11): the key first, no "(at ...)" suffix
    param = path.rpartition('.')[2] if path else None
    raise AppealDataError(f"{path!r} expected True or False, not {value!r}",
                          param=param)


def _convert(converter, value, path, text=False):
    """
    One value through its converter.  `text`: the value came from a
    row of text (read_iterable, read_csv), where a boolean is spelled
    in the boolean language; from a mapping it's a bool constant.
    """
    if converter is bool or converter is boolean:
        if isinstance(value, bool):
            return value
        if text and isinstance(value, str):
            try:
                return boolean(value)
            except ValueError as e:
                _fail(f"can't convert {value!r}, {e}", path)
        _not_a_constant(value, path)
    if converter is str and not isinstance(value, str):
        # the identity terminal: an unannotated parameter reading
        # typed data keeps the type (v1--str() would mangle a TOML
        # float into its repr)
        return value
    try:
        return converter(value)
    except (ValueError, TypeError) as e:
        name = getattr(converter, '__name__', 'converter')
        why = f': {e}' if str(e) else ''
        _fail(f"can't convert {value!r} (not a valid {name}{why})",
              path)


def _read_child(child, value, path, strict, text=False):
    "A group's value: a mapping or a sequence, its choice."
    if isinstance(value, Mapping):
        return _read_group(child, value, path, strict)
    if _is_sequence(value):
        return _read_sequence(child, list(value), path, strict, text=text)
    if child.minimum <= 1 and (child.maximum is None
                               or child.maximum >= 1):
        # a scalar, for a group that can take exactly one: the
        # single-parameter converter (datestamp(text)) reads its
        # one value in place--v1 applied converters, always
        return _read_sequence(child, [value], path, strict, text=text)
    _fail(f"expected a mapping or a sequence, got {value!r}", path)


def _option_value(o, value, path, strict, text=False):
    if o.is_flag:
        return _read_bool(value, path)
    if isinstance(o, Nullary):
        return o.converters[0]() if _read_bool(value, path) else o.default
    if o.child is not None:
        return _read_child(o.child, value, path, strict, text)
    if isinstance(o, Value):
        if len(o.converters) == 1:
            return _convert(o.converters[0], value, path, text)
        if not _is_sequence(value) or len(value) != len(o.converters) - 1:
            _fail(f"expected a sequence of {len(o.converters) - 1}", path)
        converted = [_convert(c, v, path, text)
                     for c, v in zip(o.converters[1:], value)]
        if o.converters[0] is tuple:
            return tuple(converted)
        return o.converters[0](*converted)
    if getattr(o.converters[0], 'mapping', False):
        # the mapping MultiOption (dict[K, V]'s mechanism): config
        # supplies a whole dict, not KEY=VALUE tokens, so read it as
        # one--the K/V converters live on the parameterized class
        if not isinstance(value, Mapping):
            _fail(f"expected a mapping, got {value!r}", path)
        cls = o.converters[0]
        return {_convert(cls._key_converter, k, path, text):
                _convert(cls._value_converter, v, path, text)
                for k, v in value.items()}
    # Option classes: init/option/render, driven by the mapping's
    # shapes.  arity 1: a scalar per occurrence; arity k: a
    # k-sequence; arity 0: a count.  An Option reads a sequence of
    # occurrences.
    cls = o.converters[0]
    element_converters = o.converters[1:]
    arity = len(element_converters)

    def occurrence_args(occurrence, where):
        if arity == 0:
            return ()
        if arity == 1:
            # an occurrence is a sequence of its arguments; for one
            # argument the bare value is accepted too
            if _is_sequence(occurrence) and len(occurrence) == 1:
                occurrence = occurrence[0]
            return (_convert(element_converters[0], occurrence, where, text),)
        if not _is_sequence(occurrence) or len(occurrence) != arity:
            _fail(f"expected a sequence of {arity}", where)
        return tuple(_convert(c, v, where, text)
                     for c, v in zip(element_converters, occurrence))

    if arity == 0:
        if not isinstance(value, int) or value < 0:
            _fail(f"expected a count, got {value!r}", path)
        occurrences = [()] * value
    elif _is_sequence(value):
        occurrences = list(value)
    else:
        _fail(f"expected a sequence of occurrences, got {value!r}", path)

    instance = cls()
    instance.init(o.default)
    for occurrence in occurrences:
        instance.option(*occurrence_args(occurrence, path))
    return instance()


def _subtree_names(plan):
    "Every parameter name in the tree, for the flat-spelling check."
    names = set()
    for o in plan.options:
        names.add(o.name)
    for s in plan.slots:
        names.add(s.name)
        if not isinstance(s.child, Terminal):
            names.update(_subtree_names(s.child))
    return names


def _vet_keys(plan, mapping, path):
    "strict: every key must be a parameter name somewhere in the tree."
    claimed = _subtree_names(plan)
    for key in mapping:
        if key not in claimed:
            _fail(f"unrecognized key {key!r}", path)


def _read_group(plan, mapping, path, strict, boundary=True, text=False):
    # the strict check runs at MAPPING-OBJECT boundaries only: a flat
    # read (v1's other spelling) re-reads the PARENT's dict, whose
    # other keys are the parent's business, not ours.  text: the
    # mapping's values are text (read_csv by heading)
    if strict and boundary:
        _vet_keys(plan, mapping, path)
    args, kwargs = _read_group_args(plan, mapping, path, strict, text)
    if plan.callable is tuple:
        return tuple(args)
    return plan.callable(*args, **kwargs)


def _read_group_args(plan, mapping, path, strict, text=False):
    args = []
    kwargs = {}
    for slot in plan.slots:
        here = _sub(path, slot.name)
        child = slot.child

        if slot.repeat:
            value = mapping.get(slot.name, ())
            if not _is_sequence(value):
                _fail(f"expected a sequence, got {value!r}", here)
            if isinstance(child, Terminal):
                args.extend(_convert(child.converter, v, here, text)
                            for v in value)
            else:
                args.extend(_read_child(child, v, here, strict, text)
                            for v in value)
            continue

        if isinstance(child, Terminal):
            if slot.name in mapping:
                value = _convert(child.converter, mapping[slot.name], here,
                                 text)
            elif slot.required:
                _fail(f"required key {slot.name!r} is missing", path)
            else:
                value = slot.default
            if slot.trailing:
                kwargs[slot.name] = value
            else:
                args.append(value)
            continue

        # a group: a sub-mapping/sequence under its own name, or
        # (v1's other spelling) flat keys at this level
        if slot.name in mapping:
            args.append(_read_child(child, mapping[slot.name], here, strict,
                                    text))
        elif _subtree_names(child) & mapping.keys():
            args.append(_read_group(child, mapping, path, strict,
                                    boundary=False, text=text))
        elif slot.required:
            # nothing of its present: defaults throughout (its own
            # required parameters will complain by path)
            args.append(_read_group(child, {}, here, strict))
        else:
            args.append(slot.default)

    option_defaults = {}
    for o in plan.options:
        if o.name in mapping:
            kwargs[o.name] = _option_value(o, mapping[o.name],
                                           _sub(path, o.name), strict, text)
        elif o.required:
            _fail(f"missing option {o.name!r}", path)
        elif not o.kwargs_delivered:
            option_defaults.setdefault(o.name, o.default)
    for name, default in option_defaults.items():
        kwargs.setdefault(name, default)

    return args, kwargs


def _read_sequence(plan, items, path, strict, call=True, text=False):
    n = len(items)
    trailing = [s for s in plan.slots if s.trailing]
    reserved = items[n - len(trailing):] if trailing else []
    body = items[:n - len(trailing)] if trailing else items

    # counting, items-space: every non-repeat slot costs one item
    slots = [s for s in plan.slots if not s.trailing]
    required_after = [0] * (len(slots) + 1)
    for index in range(len(slots) - 1, -1, -1):
        required_after[index] = (required_after[index + 1]
                                 + (1 if slots[index].required else 0))
    args = []
    i = 0
    for index, slot in enumerate(slots):
        here = _sub(path, slot.name)
        child = slot.child
        remaining = len(body) - i

        if slot.repeat:
            take = remaining - required_after[index + 1]
            for value in body[i:i + take]:
                if isinstance(child, Terminal):
                    args.append(_convert(child.converter, value, here, text))
                else:
                    args.append(_read_child(child, value, here,
                                            strict, text))
            i += take
            continue

        if not slot.required and remaining <= required_after[index + 1]:
            args.append(slot.default)
            continue
        if i >= len(body):
            _fail(f"ran out of values ({slot.name!r} is next)", path)
        value = body[i]
        i += 1
        if isinstance(child, Terminal):
            args.append(_convert(child.converter, value, here, text))
        else:
            args.append(_read_child(child, value, here, strict, text))

    if i < len(body):
        _fail(f"{len(body) - i} leftover value(s)", path)

    kwargs = {}
    for slot, value in zip(trailing, reserved):
        kwargs[slot.name] = _convert(slot.child.converter, value,
                                     _sub(path, slot.name), text)
    for o in plan.options:
        if o.required:
            _fail(f"missing option {o.name!r}", path)
        if not o.kwargs_delivered:
            kwargs[o.name] = o.default

    if not call:
        return args, kwargs
    if plan.callable is tuple:
        return tuple(args)
    return plan.callable(*args, **kwargs)


def _read_fold(cls, data, strict, text=False):
    """
    An Option subclass as the callable: the protocol, read-side--
    init(default) once, option() per occurrence (an Option reads
    a sequence of occurrences), render() produces the value.
    """
    plan = build_plan(cls.option, name=cls.__name__, method_of=cls.__name__)
    # an Option reads a sequence of occurrences
    if not _is_sequence(data):
        raise AppealDataError(
            f"{cls.__name__} repeats; read it from a sequence "
            f"of occurrences, got {type(data).__name__}")
    occurrences = data
    instance = cls()
    instance.init(None)
    for index, occurrence in enumerate(occurrences):
        path = f'{cls.__name__}[{index}]'
        if isinstance(occurrence, Mapping):
            if strict:
                _vet_keys(plan, occurrence, path)
            args, kwargs = _read_group_args(plan, occurrence, path, strict)
        elif _is_sequence(occurrence):
            args, kwargs = _read_sequence(plan, list(occurrence),
                                          path, strict, call=False, text=text)
        else:
            _fail(f"expected a mapping or a sequence, got "
                  f"{occurrence!r}", path)
        instance.option(*args, **kwargs)
    return instance()


def read_mapping(callable, mapping, *, strict=True):
    """
    Call `callable` with values pulled from `mapping` by parameter
    name, converted per its signature--the command-line metaphor
    pointed at a config file.  Groups (converter annotations) read
    a sub-mapping under their parameter's name, or flat keys at the
    same level; defaults fill absent keys.  A key nothing in the
    tree claims raises (ruled 2026-08-29: fail loud by default);
    strict=False ignores such keys instead--for reading a slice of
    somebody else's document.  An Option subclass folds: it reads
    a sequence of occurrences, option() per element.
    """
    if is_option(callable):
        return _read_fold(callable, mapping, strict)
    plan = callable if isinstance(callable, Plan) else build_plan(callable)
    if not isinstance(mapping, Mapping):
        raise AppealDataError(
            f"read_mapping needs a mapping, got {type(mapping).__name__}")
    return _read_group(plan, mapping, '', strict)


def _reject_unfeedable(plan):
    "read_iterable/read_csv can't position-feed names (v1's corpus)."
    for slot in plan.slots:
        if slot.trailing:
            raise AppealConfigurationError(
                f"read_iterable can't position-feed keyword-only "
                f"parameter {slot.name!r}")
    for o in plan.options:
        raise AppealConfigurationError(
            f"read_iterable can't position-feed keyword-only "
            f"parameter {o.name!r}")
    if plan.var_keyword:
        raise AppealConfigurationError(
            f"read_iterable can't position-feed keyword-only "
            f"parameter {plan.var_keyword!r}")


def read_iterable(callable, iterable, *, strict=True):
    """
    Call `callable` once per row of `iterable`, values pulled by
    position within each row; returns the list of results.  Empty
    rows are skipped.  Keyword-only parameters (and **kwargs) can't
    be position-fed and are configuration errors (v1's corpus).
    strict= governs nested mappings inside rows, as in read_mapping.
    """
    plan = callable if isinstance(callable, Plan) else build_plan(callable)
    _reject_unfeedable(plan)
    results = []
    for row in iterable:
        if not row:
            continue
        results.append(_read_sequence(plan, list(row), '', strict, text=True))
    return results


def read_csv(callable, reader, *, first_row_map=None, strict=True):
    """
    read_iterable for csv.reader-style input: the first row is the
    headings.  Without first_row_map the headings are discarded and
    rows feed positionally; with it, each heading maps to a
    parameter name and rows feed read_mapping-style (strict= as in
    read_mapping).
    """
    plan = callable if isinstance(callable, Plan) else build_plan(callable)
    rows = iter(reader)
    try:
        headings = next(rows)
    except StopIteration:
        return []
    if first_row_map is None:
        _reject_unfeedable(plan)
        return [_read_sequence(plan, list(row), '', strict, text=True)
                for row in rows if row]
    try:
        names = [first_row_map[h] for h in headings]
    except KeyError as e:
        raise AppealDataError(
            f"read_csv: heading {e.args[0]!r} isn't in first_row_map")
    return [_read_group(plan, dict(zip(names, row)), '', strict, text=True)
            for row in rows if row]
