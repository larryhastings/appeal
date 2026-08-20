#!/usr/bin/env python3
#
# appeal/codegen3.py
# Part of Appeal -- the emitter for the data-driven processor
# (2026-08-20).  Turns a build.Plan into the Converter subclasses the
# appeal.processor engine runs.  See appeal/processor.py for the runtime
# and appeal/codegen3_processor_prototype.py for the validated shapes.
#
# STATUS: first cut.  Handles leaf/group/*args operands, flag options, and
# conjuring (a PreOption per conjurable converter -- child.minimum == 0 --
# placed after the last required positional before its slot; none for a
# non-conjurable one).  NOT YET: value opargs, MultiOption, sibling
# option-groups, trailing pockets, command dispatch.

from .plan import Terminal
from .processor import Converter, Repeat


def _conjurable(child_plan):
    "A converter with no required positional parameters can be conjured."
    return child_plan.minimum == 0


def build_converter(plan):
    "Build (recursively) the Converter subclass for one Plan."
    callable_ = plan.callable
    # child converter classes, one per group/*args slot (built once, shared)
    children = {slot.name: build_converter(slot.child)
                for slot in plan.slots
                if not isinstance(slot.child, Terminal)}
    # converter classes for group options (--e1: extras) -- their own
    # options register when the group option fires (siblings, no summon)
    option_children = {o.name: build_converter(o.child)
                       for o in plan.options if o.kind == 'group'}
    # (string, name, kind, fallback): the converter is read LIVE off the
    # callable's annotations at register time (reuse the user's own type);
    # `fallback` covers a builtin derived from a default (no annotation) and
    # a group option (whose converter is the emitted child Converter class).
    option_specs = []
    for o in plan.options:
        if o.kind == 'flag':
            fallback = bool                     # the flag marker
        elif o.kind == 'value' and len(o.converters) == 1:
            fallback = o.converters[0]
        elif o.kind in ('fold', 'fold1'):       # counter/accumulator/mapping
            fallback = o.converters[0]
        elif o.kind == 'group':                 # converter-group option (sibling)
            fallback = option_children[o.name]
        else:                                   # multi-oparg value: later
            continue
        for s in o.strings:
            option_specs.append((s, o.name, o.kind, fallback))

    def register(self, processor):
        # this plan's own options register FIRST, so an option before the
        # positionals (or a child option mid-fill) is already known
        annotations = type(self).converter.__annotations__
        items = []
        for s, name, kind, fallback in option_specs:
            conv = (fallback if kind == 'group'
                    else annotations.get(name, fallback))
            items.append(self.Option(s, name, conv))
        boundary = len(items)   # insertion point for a conjurable slot's
                                # PreOption: after the last required-or-group
                                # slot (so it leaps over optional leaves)
        for slot in plan.slots:
            if isinstance(slot.child, Terminal):
                # the leaf converter, read live off the callable's annotation
                # (else the builtin derived from the default)
                conv = annotations.get(slot.name, slot.child.converter)
                if slot.repeat:                         # *args of a leaf
                    items.append(Repeat([
                        self.Argument(slot.name, conv, required=False)]))
                    boundary = len(items)
                    continue
                items.append(self.Argument(slot.name, conv,
                                           required=slot.required,
                                           trailing=slot.trailing))
                if slot.required:
                    boundary = len(items)
                continue
            # a converter group
            childcls = children[slot.name]
            conjurable = _conjurable(slot.child)
            preopts = []
            if conjurable:
                for o in slot.child.options:
                    if o.kind != 'flag':        # value-option conjure: later
                        continue
                    for s in o.strings:
                        preopts.append(
                            self.PreOption(s, o.name, slot.name, childcls))
            if slot.repeat:                             # windowed *args
                items.append(Repeat(
                    preopts + [self.Argument(slot.name, childcls, required=False)]))
                boundary = len(items)
                continue
            for k, pre in enumerate(preopts):           # leap over optionals
                items.insert(boundary + k, pre)
            items.append(self.Argument(slot.name, childcls,
                                       required=slot.required))
            boundary = len(items)

        processor.prepend(items)

    n_trailing = sum(1 for slot in plan.slots if slot.trailing)
    return type(f'Converter_{plan.name}', (Converter,),
                {'register': register, 'converter': callable_,
                 'trailing': n_trailing, '__module__': __name__})


# ====================================================================
#  source emission -- the same shapes as build_converter, as text
# ====================================================================
def _conv_ref(plan, param, converter):
    """
    A source expression for a converter.  A user-supplied annotation is
    reused verbatim -- referenced through the register-local `annotations`
    (bound to the callable's __annotations__), exactly as Larry asked; only
    a bare builtin derived from a default (no annotation) is baked.
    """
    if param in plan.callable.__annotations__:
        return f'annotations[{param!r}]'
    return converter.__name__       # derived from the default (str/int/...)


def emit_source(plan):
    """
    Emit the Converter subclass for a flat command as source text (options,
    leaf/*args/trailing operands).  Groups/scoped/windowed/conjuring follow
    the same shapes build_converter produces; this covers the sample.
    """
    body = []                                   # the prepended work items
    for o in plan.options:                      # options first
        if o.kind == 'flag':
            ref = 'bool'
        elif o.kind == 'value' and len(o.converters) == 1:
            ref = _conv_ref(plan, o.name, o.converters[0])
        elif o.kind in ('fold', 'fold1'):       # counter/accumulator: annotated
            ref = _conv_ref(plan, o.name, o.converters[0])
        else:
            continue
        for s in o.strings:
            body.append(f'            self.Option({s!r}, {o.name!r}, {ref}),')
    for slot in plan.slots:
        if not isinstance(slot.child, Terminal):
            body.append(f'            # (converter-group slot {slot.name!r} '
                        f'-- see build_converter)')
            continue
        ref = _conv_ref(plan, slot.name, slot.child.converter)
        if slot.repeat:
            body.append(f'            Repeat([self.Argument({slot.name!r}, '
                        f'{ref}, required=False)]),')
        else:
            extra = ', trailing=True' if slot.trailing else ''
            body.append(f'            self.Argument({slot.name!r}, {ref}, '
                        f'required={slot.required!r}{extra}),')

    lines = [f'@command({plan.name!r})',
             f'class Converter_{plan.name}(Converter):']
    n_trailing = sum(1 for slot in plan.slots if slot.trailing)
    if n_trailing:
        lines.append(f'    trailing = {n_trailing}')
    lines.append('    def register(self, processor):')
    if any('annotations[' in line for line in body):
        lines.append('        annotations = type(self).converter.__annotations__')
    lines.append('        processor.prepend([')
    lines.extend(body)
    lines.append('        ])')
    return '\n'.join(lines)


_MODULE_HEADER = '''\
#
# Generated by Appeal's processor emitter (appeal/codegen3.py).
# A compiled parser MODULE that wears the Appeal API: import it AS appeal
# and your program is unchanged.  Imports only appeal.processor and
# appeal.runtime (the stdlib-only core).  Regenerate rather than edit.

import sys

from appeal.processor import Converter, Repeat, Processor, dispatch
from appeal.runtime import (
    UsageError, split, validate, validate_range, counter, accumulator,
    mapping, file, optional,
    )

commands = {}


def command(name):
    def command(cls):
        commands[name] = cls
        return cls
    return command
'''

_SHIM = '''\
class Appeal:
    "The compiled parser wearing the Appeal API (parse + dispatch only)."
    def __init__(self, name=None, *, version=None):
        self.name = name
        self.version = version
        self.commands = {}

    def command(self, name=None):
        def command(converter):
            nonlocal name
            name = name or converter.__name__
            cls = commands[name]
            cls.converter = converter
            self.commands[name] = cls
            return converter
        return command

    def process(self, args):
        return dispatch(self.commands, list(args))

    def main(self, args=None):
        args = sys.argv[1:] if args is None else list(args)
        try:
            result = self.process(args)
        except UsageError as e:
            print(f'{self.name or "error"}: {e}', file=sys.stderr)
            return 2
        return result if isinstance(result, int) else 0
'''


def emit_module(plans):
    "Emit a complete, runnable compiled parser module for a list of plans."
    parts = [_MODULE_HEADER, '']
    for plan in plans:                          # each @command(name) registers
        parts.append(emit_source(plan))         # itself into `commands`
        parts.append('')
    parts.append(_SHIM)
    return '\n'.join(parts)
