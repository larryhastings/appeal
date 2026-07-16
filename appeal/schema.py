#!/usr/bin/env python3
#
# appeal/schema.py
# Part of Appeal v2.
# Copyright 2021-2026 by Larry Hastings
#
# The JSON schema: the plan tree described as plain data, for
# machine consumers--MCP tool definitions, shell completion
# generators, documentation tooling.  Everything is JSON-safe
# (strings, numbers, bools, lists, dicts, None).
#
# In-process API only; not part of the command-line grammar.
# The natural round trip: schema() tells a machine what a command
# accepts; read_mapping() runs the command from the JSON object
# the machine sends back.

import inspect as _inspect

from .build import build
from .help import parse_docstring
from .plan import Terminal, NO_DEFAULT, Plan


def _converter_name(converter):
    return getattr(converter, '__name__', str(converter))


def _default(value):
    "Defaults appear in the schema only when JSON can carry them."
    if value is NO_DEFAULT or value is _inspect.Parameter.empty:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _docs_for(plan):
    doc = _inspect.getdoc(plan.callable) if callable(plan.callable) else None
    where = getattr(plan.callable, '__name__', repr(plan.callable))
    parsed = parse_docstring(doc, where)
    summary = parsed['summary'][0] if parsed['summary'] else ''
    docs = {}
    for kind in ('arguments', 'options', 'commands'):
        for name, lines in parsed[kind].items():
            docs[name] = ' '.join(lines)
    return summary, docs


def _option_schema(o, docs):
    entry = {
        'name': o.name,
        'strings': list(o.strings),
        'kind': o.kind,
        'repeatable': o.kind in ('accumulate', 'mapping', 'fold'),
    }
    if o.kind == 'group':
        entry['group'] = _plan_schema(o.child)
    elif o.kind not in ('flag', 'nullary'):
        converters = o.converters[1:] if len(o.converters) > 1 else o.converters
        entry['operands'] = [_converter_name(c) for c in converters]
    entry['default'] = _default(o.default)
    if o.usage_name and o.usage_name != o.name:
        entry['usage'] = o.usage_name
    if o.name in docs:
        entry['doc'] = docs[o.name]
    return entry


def _slot_schema(slot, docs):
    # 'name' is the IDENTITY name--the Python parameter, the key
    # read_mapping pulls by.  A presentation rename
    # (@app.parameter usage=) rides along as 'usage'; it must
    # never leak into the property keys machines send back.
    entry = {
        'name': slot.name,
        'required': bool(slot.required),
        'repeat': bool(slot.repeat),
        'trailing': bool(slot.trailing),
    }
    if slot.usage_name and slot.usage_name != slot.name:
        entry['usage'] = slot.usage_name
    if isinstance(slot.child, Terminal):
        entry['converter'] = _converter_name(slot.child.converter)
    else:
        entry['group'] = _plan_schema(slot.child)
    if not slot.required:
        entry['default'] = _default(slot.default)
    if slot.name in docs:
        entry['doc'] = docs[slot.name]
    return entry


def _plan_schema(plan):
    summary, docs = _docs_for(plan)
    entry = {
        'name': plan.name,
        'summary': summary,
        'usage': plan.usage(),
        'operands': [_slot_schema(s, docs) for s in plan.slots],
        'options': [_option_schema(o, docs) for o in plan.options],
        'operand_counts': {
            'minimum': plan.minimum,
            'maximum': plan.maximum,
            'valid': (sorted(plan.valid_counts)
                      if plan.valid_counts is not None else None),
        },
    }
    return entry


def schema(callable):
    """
    Describe a command as plain JSON-safe data: its usage line,
    operands (groups recursing), options, arities, and any
    `name: description` docstring entries.  The machine-readable
    twin of --help; pairs with read_mapping() to run the command
    from a JSON object.
    """
    plan = callable if isinstance(callable, Plan) else build(callable)
    return _plan_schema(plan)


def schema_set(commands, global_plan=None, prog=None):
    "The schema of a multi-command program."
    entry = {
        'name': prog or 'program',
        'commands': {word: _plan_schema(plan)
                     for word, plan in commands.items()},
    }
    if global_plan is not None:
        entry['global'] = _plan_schema(global_plan)
    return entry


_JSON_TYPES = {'str': 'string', 'int': 'integer', 'float': 'number',
               'bool': 'boolean'}


def _mcp_object_schema(described):
    """
    One plan description as a JSON-Schema object--recursive, so a
    converter group advertises its real structure (the properties
    read_mapping accepts), not an opaque string.  (read_mapping
    also accepts a group as a positional sequence, and its keys
    flat at the parent level; the schema advertises the mapping
    shape, the roomiest to generate against.)
    """
    def group_entry(group):
        # mirror read_mapping's shapes exactly: a group always
        # reads a mapping; a group that can take exactly one
        # operand (minimum <= 1, maximum allows 1) also reads a
        # bare scalar in place--Path('/tmp/x')--so the schema
        # offers both
        obj = _mcp_object_schema(group)
        counts = group.get('operand_counts') or {}
        minimum = counts.get('minimum') or 0
        maximum = counts.get('maximum')
        if minimum <= 1 and (maximum is None or maximum >= 1):
            first = next(iter(group.get('operands') or ()), None)
            kind = (_JSON_TYPES.get(first.get('converter'))
                    if first else None)
            return {'anyOf': [{'type': kind or 'string'}, obj]}
        return obj

    properties = {}
    required = []
    for operand in described['operands']:
        if 'group' in operand:
            entry = group_entry(operand['group'])
            if operand.get('repeat'):
                entry = {'type': 'array', 'items': entry}
        else:
            kind = _JSON_TYPES.get(operand.get('converter'))
            if operand.get('repeat'):
                entry = {'type': 'array'}
                if kind:
                    entry['items'] = {'type': kind}
            else:
                entry = {'type': kind or 'string'}
        if operand.get('doc'):
            entry['description'] = operand['doc']
        properties[operand['name']] = entry
        if operand.get('required') or operand.get('trailing'):
            required.append(operand['name'])
    for option in described['options']:
        kind = option.get('kind')
        if kind == 'group':
            entry = group_entry(option['group'])
        elif kind == 'flag':
            entry = {'type': 'boolean'}
        elif kind in ('accumulate', 'fold'):
            entry = {'type': 'array'}
        elif kind == 'mapping':
            entry = {'type': 'object'}
        else:
            # value options: type from the converter when there's
            # exactly one operand (coverage found this read a key
            # the corpus never wrote--int options schema'd as
            # strings); multi-operand values are arrays
            operands = option.get('operands') or ()
            if len(operands) > 1:
                entry = {'type': 'array'}
            else:
                converter = _JSON_TYPES.get(
                    operands[0] if operands else None)
                entry = {'type': converter or 'string'}
        if option.get('doc'):
            entry['description'] = option['doc']
        properties[option['name']] = entry
    return {'type': 'object', 'properties': properties,
            'required': required}


def mcp_input_schema(plan):
    """
    The MCP inputSchema (plain JSON Schema) for one command:
    arguments and options become properties--keyed by PARAMETER
    name, exactly what read_mapping() accepts--with required
    listing the required arguments, and converter groups
    recursing into nested object schemas.  Converter types map
    where they're knowable; everything else is a string (the
    read driver converts anyway).
    """
    return _mcp_object_schema(schema(plan))
