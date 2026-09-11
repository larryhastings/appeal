#!/usr/bin/env python3
#
# appeal/schema.py
# Part of Appeal 1.0.
# Copyright 2021-2026 by Larry Hastings
#
# Generic schema generation: the plan tree described as plain data
# for machine consumers.  Two formats: describe()/describe_set()
# produce Appeal's own description--every command's usage, operands,
# options with their spellings, arities, and docs; the machine-
# readable twin of --help--and mcp_input_schema() projects one plan
# down to standard JSON Schema (type/properties/required), the form
# MCP clients validate tool arguments against.  Everything is
# JSON-safe (strings, numbers, bools, lists, dicts, None).
#
# In-process API only; not part of the command-line grammar, and
# LAZY--nothing on the fast path imports this module.  The natural
# round trip: a schema tells a machine what a command accepts;
# read_mapping() runs the command from the object the machine sends
# back.  (This module was briefly folded into mcp.py; split back out
# 2026-09-03--the machinery is generic, MCP is one consumer.)

import inspect as _inspect

# the schema versions this module can render.  APPEAL versions are
# ours (breaking changes to the description bump it); MCP versions
# are the protocol's own date-stamped revisions--the same inventory
# run_mcp() negotiates with, so supporting a new revision is one
# entry here plus whatever the new shape needs.
_APPEAL_VERSIONS = ('1.0',)
_MCP_VERSIONS = ('2024-11-05',)

from .frontend import build_plan
from .presentation import parse_docstring
from .frontend import Terminal, NO_DEFAULT, Plan


def _converter_name(converter):
    return getattr(converter, '__name__', str(converter))


def _default(value):
    "Defaults appear in the schema only when JSON can carry them."
    from .frontend import empty
    if value is NO_DEFAULT or value is empty:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _docs_for(plan):
    doc = _inspect.getdoc(plan.callable) if callable(plan.callable) else None
    where = getattr(plan.callable, '__name__', repr(plan.callable))
    from .presentation import markdown
    parsed = parse_docstring(doc, where)
    summary = ' '.join(markdown(parsed['summary']).split())
    docs = {}
    for kind in ('arguments', 'options', 'commands'):
        for name, blocks in parsed[kind].items():
            docs[name] = ' '.join(markdown(blocks).split())
    return summary, docs


def _option_schema(o, docs):
    entry = {
        'name': o.name,
        'strings': list(o.strings),
        'kind': o.kind,
        'repeatable': o.kind == 'fold',
        # the mapping MultiOption (dict[K, V]) schemas as a JSON object
        'mapping': bool(o.converters) and getattr(
            o.converters[0], 'mapping', False),
    }
    if o.child is not None:
        entry['group'] = _plan_schema(o.child)
    elif o.consumes_operands:
        entry['operands'] = [_converter_name(c) for c in o.operand_converters]
    if o.required:
        entry['required'] = True
    else:
        entry['default'] = _default(o.default)
    if o.restriction == 'deprecated':
        entry['deprecated'] = True
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


def _operand_counts(plan):
    """
    The exact valid counts.  An unbounded plan lists the valid
    counts below 'unbounded_from', from which every count is valid.
    """
    counts = {'minimum': plan.minimum, 'maximum': plan.maximum,
              'valid': sorted(plan.valid_counts)}
    if plan.unbounded_from is not None:
        counts['unbounded_from'] = plan.unbounded_from
    return counts


def _plan_schema(plan):
    from big.stylesheet import strip_styles
    summary, docs = _docs_for(plan)
    entry = {
        'name': plan.name,
        'summary': summary,
        # plan.usage() is role-tagged for the terminal; the schema
        # carries the plain visible line
        'usage': strip_styles(plan.usage()),
        'operands': [_slot_schema(s, docs) for s in plan.slots],
        'options': [_option_schema(o, docs) for o in plan.options
                    if o.restriction != 'hidden'],
        'operand_counts': _operand_counts(plan),
    }
    return entry


def describe(callable):
    """
    Describe a command as plain JSON-safe data: its usage line,
    operands (groups recursing), options, arities, and any
    `name: description` docstring entries.  The machine-readable
    twin of --help; pairs with read_mapping() to run the command
    from a JSON object.
    """
    plan = callable if isinstance(callable, Plan) else build_plan(callable)
    return _plan_schema(plan)


def describe_set(commands, global_plan=None, prog=None):
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
               'bool': 'boolean', 'boolean': 'boolean'}


def _degenerate_leaf_type(group):
    """
    A DEGENERATE converter group consumes exactly one operand
    through a single chain (no options, no repeat)--the command line
    passes it one value and the group just wraps it.  0.6.4 collapsed
    such chains to the innermost leaf (its `collapse_degenerate`); we
    do the same for the schema, so `temp: celsius` with
    `celsius(degrees: int)` reads TRANSPARENTLY as an integer, not an
    object {degrees}.  Returns the leaf's JSON type, or None if the
    group isn't degenerate.  (Ruled by Larry 2026-08-16.)
    """
    if group.get('options'):
        return None
    counts = group.get('operand_counts') or {}
    if counts.get('minimum') != 1 or counts.get('maximum') != 1:
        return None
    operands = group.get('operands') or ()
    if len(operands) != 1:
        return None
    op = operands[0]
    if op.get('repeat'):
        return None
    if 'group' in op:
        return _degenerate_leaf_type(op['group'])
    return _JSON_TYPES.get(op.get('converter')) or 'string'


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
        # a degenerate single-operand group is TRANSPARENT: schema it
        # as the leaf type it collapses to (0.6.4's degenerate tree)
        leaf = _degenerate_leaf_type(group)
        if leaf is not None:
            return {'type': leaf}
        # otherwise mirror read_mapping's shapes: a group always
        # reads a mapping; a group that can take exactly one operand
        # (minimum <= 1, maximum allows 1) also reads a bare scalar
        # in place--Path('/tmp/x')--so the schema offers both
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
        elif kind in ('flag', 'nullary'):
            # nullary is a value-producing flag: the reader reads it as a
            # bool (True calls the zero-arg converter, False takes the
            # default), so the schema must say boolean, not string
            entry = {'type': 'boolean'}
        elif option.get('mapping'):
            entry = {'type': 'object'}
        elif kind == 'fold':
            entry = {'type': 'array'}
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
        if option.get('required'):
            required.append(option['name'])
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
    return _mcp_object_schema(describe(plan))
