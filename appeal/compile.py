#!/usr/bin/env python3
#
# appeal/compile.py
# Part of Appeal -- the emitter for the data-driven processor
# (2026-08-20).  Turns a program's command Plans into the Converter
# subclasses the runtime engine runs.  The engine lives in
# appeal/runtime.py; see appeal/codegen3_processor_prototype.py for the
# validated shapes.
#
# The unit of compilation is the CONVERTER (identified by its callable):
# `_converters()` enumerates the distinct converters reachable from the
# commands, and everything downstream produces exactly one Converter_x per
# converter x.  References between converters (a group operand/option, a
# conjure) resolve through a shared registry -- a (Converter subclass,
# callable) 2-tuple whose callable is read live off the parent's annotations.
# Handles options (flag / value / counter-accumulator / sibling group), leaf
# & group operands, *args windows, trailing pockets, and conjuring.  NOT YET:
# multi-oparg value options, mapping KEY=VALUE, global command + cycling.

from .plan import Terminal
from .runtime import Converter


# builtins we hard-code as a literal (the fingerprint guarantees the shape,
# so there's no need to read them off the callable at runtime)
_BUILTIN_CONVERTERS = (str, bool, int, float, complex)


def _conjurable(child_plan):
    "A converter with no required positional parameters can be conjured."
    return child_plan.minimum == 0


def _converters(plans):
    """
    The distinct converters reachable from the command Plans, as an ordered
    map callable -> representative Plan (each converter's own children before
    it).  A converter is identified by its callable, so a callable reached
    from several slots/options appears once -- one converter, one entry.
    """
    found = {}

    def visit(plan):
        if plan.callable in found:
            return
        for slot in plan.slots:                 # its own converters first
            if not isinstance(slot.child, Terminal):
                visit(slot.child)
        for option in plan.options:
            if option.kind == 'group':
                visit(option.child)
        found[plan.callable] = plan
    for plan in plans:
        visit(plan)
    return found


def _class_names(converters):
    "Assign each converter its class name (Converter_<name>), unique per program."
    names, used = {}, set()
    for callable_, plan in converters.items():
        base = name = f'Converter_{plan.name}'
        n = 2
        while name in used:                     # two distinct converters, one name
            name, n = f'{base}_{n}', n + 1
        names[callable_] = name
        used.add(name)
    return names


# ====================================================================
#  in-memory build -- one live Converter subclass per converter
# ====================================================================
def build_converters(plans):
    """
    The compiled Converter subclasses, in memory, as a map callable -> class.
    One class per converter; a group operand/option resolves its child class
    through the shared registry at parse time.  The callable is NOT baked onto
    the class -- the runtime passes it to the constructor.
    """
    classes = {}
    for callable_, plan in _converters(plans).items():
        classes[callable_] = _build_class(plan, classes)
    return classes


def build_converter(plan):
    "The single command's live Converter subclass (convenience over build_converters)."
    return build_converters([plan])[plan.callable]


def _build_class(plan, classes):
    "One Converter subclass; group refs resolve through `classes` at parse time."
    option_specs = []
    for o in plan.options:
        if o.kind == 'flag':
            kind, extra = 'flag', None
        elif o.kind == 'value' and len(o.converters) == 1:
            kind, extra = 'value', o.converters[0]
        elif o.kind in ('fold', 'fold1'):       # counter/accumulator/mapping
            kind, extra = 'fold', o.converters[0]
        elif o.kind == 'group':                 # sibling converter-group option
            kind, extra = 'group', o.child.callable
        else:                                   # multi-oparg value: later
            continue
        option_specs.append((o.name, kind, extra, o.strings))

    def register(self, processor):
        annotations = self.converter.__annotations__
        items = []
        for name, kind, extra, strings in option_specs:
            if kind == 'flag':
                conv = bool
            elif kind == 'group':
                conv = (classes[extra], annotations[name])      # (child cls, callable)
            else:                                               # value / fold
                conv = annotations.get(name, extra)
            items.append(self.Option(name, conv, *strings))
        boundary = len(items)   # a conjurable slot's PreOption inserts here:
                                # after the last required-or-group slot (so it
                                # leaps over optional leaves to reach its slot)
        for slot in plan.slots:
            if isinstance(slot.child, Terminal):
                conv = annotations.get(slot.name, slot.child.converter)
                if slot.repeat:                         # *args of a leaf
                    items.append(self.Repeat([
                        self.Argument(slot.name, conv, required=False)]))
                    boundary = len(items)
                    continue
                items.append(self.Argument(slot.name, conv,
                                           required=slot.required,
                                           trailing=slot.trailing))
                if slot.required:
                    boundary = len(items)
                continue
            # a converter group -- (child class, callable) tuple
            group = (classes[slot.child.callable], annotations[slot.name])
            preopts = []
            if _conjurable(slot.child):
                for o in slot.child.options:
                    if o.kind != 'flag':        # value-option conjure: later
                        continue
                    for s in o.strings:
                        preopts.append(
                            self.PreOption(s, o.name, slot.name, group))
            if slot.repeat:                             # windowed *args
                items.append(self.Repeat(
                    preopts + [self.Argument(slot.name, group, required=False)]))
                boundary = len(items)
                continue
            for k, pre in enumerate(preopts):           # leap over optionals
                items.insert(boundary + k, pre)
            items.append(self.Argument(slot.name, group, required=slot.required))
            boundary = len(items)

        processor.prepend(items)

    n_trailing = sum(1 for slot in plan.slots if slot.trailing)
    return type(f'Converter_{plan.name}', (Converter,),
                {'register': register, 'trailing': n_trailing,
                 '__module__': __name__})


# ====================================================================
#  source emission -- the same shapes as build, as text
# ====================================================================
def _converter_expr(plan, param, converter):
    """
    Source for parameter `param`'s converter, hard-coded per Larry's rule:
    a builtin (str/bool/int/float/complex) as its literal name; otherwise the
    value the user passed -- annotations['param'] for an annotation, or
    type(converter.__defaults__[i]) for a default's type.  The fingerprint
    guarantees the shape, so hard-coding here is safe.
    """
    if converter in _BUILTIN_CONVERTERS:
        return converter.__name__               # str, int, float, ...
    if param in plan.callable.__annotations__:
        return f'annotations[{param!r}]'        # the user's own supplied type
    return _default_type_expr(plan.callable, param)


def _default_type_expr(fn, param):
    "type(converter.__defaults__[i]) / __kwdefaults__['p'] -- the default's type."
    code = fn.__code__
    positional = code.co_varnames[:code.co_argcount]
    if param in positional:
        i = positional.index(param) - (code.co_argcount - len(fn.__defaults__ or ()))
        return f'type(converter.__defaults__[{i}])'
    return f'type(converter.__kwdefaults__[{param!r}])'


def _group_expr(plan, slot_or_option, names):
    "A group converter as source: (Converter_child, <callable>)."
    child = slot_or_option.child
    callable_expr = _converter_expr(plan, slot_or_option.name, child.callable)
    return f'({names[child.callable]}, {callable_expr})'


def emit_source(plan, names, is_command):
    """
    Emit one Converter subclass as source text, mirroring _build_class:
    options first, then leaf/group operands with conjuring PreOptions, *args
    windows, and trailing pockets.  `names` maps a converter's callable to its
    class name; `is_command` decorates the class with @Appeal._converter.
    """
    items = []                                  # source for each work item

    for o in plan.options:                      # options first
        if o.kind == 'flag':
            conv = 'bool'
        elif o.kind == 'value' and len(o.converters) == 1:
            conv = _converter_expr(plan, o.name, o.converters[0])
        elif o.kind in ('fold', 'fold1'):       # counter/accumulator/mapping
            conv = _converter_expr(plan, o.name, o.converters[0])
        elif o.kind == 'group':                 # sibling converter-group option
            conv = _group_expr(plan, o, names)
        else:                                   # multi-oparg value: later
            continue
        strings = ', '.join(repr(s) for s in o.strings)
        items.append(f'self.Option({o.name!r}, {conv}, {strings})')

    boundary = len(items)
    for slot in plan.slots:
        if isinstance(slot.child, Terminal):
            conv = _converter_expr(plan, slot.name, slot.child.converter)
            if slot.repeat:                         # *args of a leaf
                items.append(f'self.Repeat([self.Argument({slot.name!r}, '
                             f'{conv}, required=False)])')
                boundary = len(items)
                continue
            extra = ', trailing=True' if slot.trailing else ''
            items.append(f'self.Argument({slot.name!r}, {conv}, '
                         f'required={slot.required!r}{extra})')
            if slot.required:
                boundary = len(items)
            continue
        # a converter group
        group = _group_expr(plan, slot, names)
        preopts = []
        if _conjurable(slot.child):
            for o in slot.child.options:
                if o.kind != 'flag':            # value-option conjure: later
                    continue
                for s in o.strings:
                    preopts.append(f'self.PreOption({s!r}, {o.name!r}, '
                                   f'{slot.name!r}, {group})')
        if slot.repeat:                             # windowed *args
            inner = ', '.join(preopts +
                              [f'self.Argument({slot.name!r}, {group}, required=False)'])
            items.append(f'self.Repeat([{inner}])')
            boundary = len(items)
            continue
        for k, pre in enumerate(preopts):           # leap over optionals
            items.insert(boundary + k, pre)
        items.append(f'self.Argument({slot.name!r}, {group}, '
                     f'required={slot.required!r})')
        boundary = len(items)

    lines = []
    if is_command:
        lines.append(f'@Appeal._converter({plan.name!r})')
    lines.append(f'class {names[plan.callable]}(Converter):')
    n_trailing = sum(1 for slot in plan.slots if slot.trailing)
    if n_trailing:
        lines.append(f'    trailing = {n_trailing}')
    lines.append('    def register(self, processor):')
    joined = '\n'.join(items)
    need_converter = ('converter.__defaults__' in joined
                      or 'converter.__kwdefaults__' in joined)
    need_annotations = 'annotations[' in joined
    if need_converter:
        lines.append('        converter = self.converter')
    if need_annotations:
        source = 'converter' if need_converter else 'self.converter'
        lines.append(f'        annotations = {source}.__annotations__')
    lines.append('        processor.prepend([')
    for it in items:
        lines.append(f'            {it},')
    lines.append('        ])')
    return '\n'.join(lines)


_MODULE_HEADER = '''\
#
# Generated by Appeal's emitter (appeal/compile.py).
# A compiled parser MODULE that wears the Appeal API: import it AS appeal
# and your program is unchanged.  Imports only appeal.runtime (the
# stdlib-only core).  Regenerate rather than edit.

from appeal.runtime import (
    Converter, appeal_class, split, validate, validate_range, counter,
    accumulator, mapping, file, optional,
    )

Appeal = appeal_class()
'''


def emit_module(plans):
    "Emit a complete, runnable compiled parser module for a list of plans."
    converters = _converters(plans)
    names = _class_names(converters)
    commands = {plan.callable for plan in plans}
    parts = [_MODULE_HEADER, '']
    for callable_, plan in converters.items():  # children first; commands self-register
        parts.append(emit_source(plan, names, is_command=callable_ in commands))
        parts.append('')
    return '\n'.join(parts)
