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
    # (string, name, converter): converter None -> flag, else value option.
    # A multi-oparg value option (len(converters) > 1) awaits a later pass.
    option_specs = []
    for o in plan.options:
        if o.kind == 'flag':
            conv = bool                         # the flag marker
        elif o.kind == 'value' and len(o.converters) == 1:
            conv = o.converters[0]
        elif o.kind in ('fold', 'fold1'):       # counter/accumulator/mapping
            base = o.converters[0]              # the MultiOption class; re-
            conv = base if len(o.converters) == 1 else base[o.converters[1:]]
        elif o.kind == 'group':                 # converter-group option (sibling)
            conv = option_children[o.name]
        else:                                   # multi-oparg value: later
            continue
        for s in o.strings:
            option_specs.append((s, o.name, conv))

    def register(self, processor):
        # this plan's own options register FIRST, so an option before the
        # positionals (or a child option mid-fill) is already known
        items = []
        for s, name, conv in option_specs:
            items.append(self.Option(s, name, conv))
        boundary = len(items)   # insertion point for a conjurable slot's
                                # PreOption: after the last required-or-group
                                # slot (so it leaps over optional leaves)
        for slot in plan.slots:
            if isinstance(slot.child, Terminal):
                if slot.repeat:                         # *args of a leaf
                    items.append(Repeat([
                        self.Argument(slot.name, slot.child.converter, False)]))
                    boundary = len(items)
                    continue
                items.append(self.Argument(slot.name, slot.child.converter,
                                           slot.required, trailing=slot.trailing))
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
                    preopts + [self.Argument(slot.name, childcls, False)]))
                boundary = len(items)
                continue
            for k, pre in enumerate(preopts):           # leap over optionals
                items.insert(boundary + k, pre)
            items.append(self.Argument(slot.name, childcls, slot.required))
            boundary = len(items)

        processor.prepend(items)

    def __init__(self):
        Converter.__init__(self, callable_)

    n_trailing = sum(1 for slot in plan.slots if slot.trailing)
    return type(f'Converter_{plan.name}', (Converter,),
                {'__init__': __init__, 'register': register,
                 'trailing': n_trailing, '__module__': __name__})


# ====================================================================
#  source emission -- the same shapes as build_converter, as text
# ====================================================================
def _ref(converter):
    "A source expression for a converter (builtin, recipe, class, or name)."
    if converter in (str, int, float, bool):
        return converter.__name__
    recipe = getattr(converter, '__appeal_recipe__', None)
    if recipe:
        kind, name, args, kwargs = recipe
        show = lambda v: v.__name__ if isinstance(v, type) else repr(v)
        if kind == 'call':
            parts = [repr(a) for a in args]
            parts += [f'{k}={show(v)}' for k, v in kwargs.items()]
            return f'{name}({", ".join(parts)})'
        if kind == 'subscript':
            return f'{name}[{", ".join(show(t) for t in args)}]'
    if isinstance(converter, type):
        return converter.__name__
    return getattr(converter, '__name__', repr(converter))


def emit_source(plan):
    """
    Emit the Converter subclass for a flat command as source text (options,
    leaf/*args/trailing operands).  Groups/scoped/windowed/conjuring follow
    the same shapes build_converter produces; this covers the sample.
    """
    lines = [f'class Converter_{plan.name}(Converter):']
    n_trailing = sum(1 for slot in plan.slots if slot.trailing)
    if n_trailing:
        lines.append(f'    trailing = {n_trailing}')
    lines.append('    def __init__(self):')
    lines.append(f'        Converter.__init__(self, {plan.callable.__name__})')
    lines.append('    def register(self, processor):')
    lines.append('        processor.prepend([')
    for o in plan.options:                      # options first
        if o.kind == 'flag':
            ref = 'bool'
        elif o.kind == 'value' and len(o.converters) == 1:
            ref = _ref(o.converters[0])
        elif o.kind in ('fold', 'fold1'):
            base = o.converters[0]
            ref = _ref(base if len(o.converters) == 1 else base[o.converters[1:]])
        else:
            continue
        for s in o.strings:
            lines.append(f'            self.Option({s!r}, {o.name!r}, {ref}),')
    for slot in plan.slots:
        if not isinstance(slot.child, Terminal):
            lines.append(f'            # (converter-group slot {slot.name!r} '
                         f'-- see build_converter)')
            continue
        ref = _ref(slot.child.converter)
        if slot.repeat:
            lines.append(f'            Repeat([self.Argument({slot.name!r}, '
                         f'{ref}, False)]),')
        else:
            extra = ', trailing=True' if slot.trailing else ''
            lines.append(f'            self.Argument({slot.name!r}, {ref}, '
                         f'{slot.required!r}{extra}),')
    lines.append('        ])')
    return '\n'.join(lines)
