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
# converter x.  A group operand/option references its child by class; the
# callable is wired onto each class once, by a generated fixup_converters
# classmethod called from @command (never at parse time).  Per-parameter
# leaf/option converters are hard-coded per the rule (builtin literal, else
# annotations['x'], else the default's type).  NOT YET: multi-oparg value
# options, mapping KEY=VALUE, global command + cycling.

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


def _child_converters(plan):
    """
    The distinct child converters this converter references (group operands
    and group options), as callable -> a representative parameter name.  Feeds
    fixup_converters: one wire-up per child, its callable read off `annotations`.
    """
    children = {}
    for slot in plan.slots:
        if not isinstance(slot.child, Terminal):
            children.setdefault(slot.child.callable, slot.name)
    for option in plan.options:
        if option.kind == 'group':
            children.setdefault(option.child.callable, option.name)
    return children


# ====================================================================
#  in-memory build -- one live Converter subclass per converter
# ====================================================================
def build_converters(plans):
    """
    The compiled Converter subclasses, in memory, as a map callable -> class.
    One class per converter; group operands/options reference their child
    class.  Callables are wired by fixup_converters, exactly as @command does
    for a generated module.
    """
    converters = _converters(plans)
    classes = {}
    for callable_, plan in converters.items():
        classes[callable_] = _build_class(plan, classes)
    for plan in plans:                          # wire each command's reachable tree
        classes[plan.callable].fixup_converters(plan.callable)
    return classes


def build_converter(plan):
    "The single command's live Converter subclass (convenience over build_converters)."
    return build_converters([plan])[plan.callable]


def _build_class(plan, classes):
    "One Converter subclass; group refs and fixup resolve through `classes`."
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
        annotations = type(self).converter.__annotations__
        items = []
        for name, kind, extra, strings in option_specs:
            if kind == 'flag':
                conv = bool
            elif kind == 'group':
                conv = classes[extra]                   # the child Converter class
            else:                                       # value / fold
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
            # a converter group -- reference the child class
            childcls = classes[slot.child.callable]
            preopts = []
            if _conjurable(slot.child):
                for o in slot.child.options:
                    if o.kind != 'flag':        # value-option conjure: later
                        continue
                    for s in o.strings:
                        preopts.append(
                            self.PreOption(s, o.name, slot.name, childcls))
            if slot.repeat:                             # windowed *args
                items.append(self.Repeat(
                    preopts + [self.Argument(slot.name, childcls, required=False)]))
                boundary = len(items)
                continue
            for k, pre in enumerate(preopts):           # leap over optionals
                items.insert(boundary + k, pre)
            items.append(self.Argument(slot.name, childcls, required=slot.required))
            boundary = len(items)

        processor.prepend(items)

    children = _child_converters(plan)

    def fixup_converters(cls, converter):
        cls.converter = converter
        if children:
            annotations = converter.__annotations__
            for child_callable, param in children.items():
                child_cls = classes[child_callable]
                if not child_cls.converter:
                    child_cls.fixup_converters(annotations[param])

    dct = {'register': register, 'trailing': _n_trailing(plan),
           '__module__': __name__}
    if children:                                # else the base default suffices
        dct['fixup_converters'] = classmethod(fixup_converters)
    return type(f'Converter_{plan.name}', (Converter,), dct)


def _n_trailing(plan):
    return sum(1 for slot in plan.slots if slot.trailing)


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


def emit_source(plan, names, is_command):
    """
    Emit one Converter subclass as source text, mirroring _build_class:
    options first, then leaf/group operands with conjuring PreOptions, *args
    windows, and trailing pockets, plus a fixup_converters classmethod that
    wires child callables.  `names` maps a converter's callable to its class
    name; `is_command` decorates the class with @Appeal._converter.
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
            conv = names[o.child.callable]      # the child Converter class
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
        # a converter group -- reference the child class
        childcls = names[slot.child.callable]
        preopts = []
        if _conjurable(slot.child):
            for o in slot.child.options:
                if o.kind != 'flag':            # value-option conjure: later
                    continue
                for s in o.strings:
                    preopts.append(f'self.PreOption({s!r}, {o.name!r}, '
                                   f'{slot.name!r}, {childcls})')
        if slot.repeat:                             # windowed *args
            inner = ', '.join(preopts +
                              [f'self.Argument({slot.name!r}, {childcls}, required=False)'])
            items.append(f'self.Repeat([{inner}])')
            boundary = len(items)
            continue
        for k, pre in enumerate(preopts):           # leap over optionals
            items.insert(boundary + k, pre)
        items.append(f'self.Argument({slot.name!r}, {childcls}, '
                     f'required={slot.required!r})')
        boundary = len(items)

    lines = []
    if is_command:
        lines.append(f'@Appeal._converter({plan.name!r})')
    lines.append(f'class {names[plan.callable]}(Converter):')
    if _n_trailing(plan):
        lines.append(f'    trailing = {_n_trailing(plan)}')
    lines.append('    def register(self, processor):')
    joined = '\n'.join(items)
    need_converter = ('converter.__defaults__' in joined
                      or 'converter.__kwdefaults__' in joined)
    need_annotations = 'annotations[' in joined
    if need_converter:
        lines.append('        converter = type(self).converter')
    if need_annotations:
        source = 'converter' if need_converter else 'type(self).converter'
        lines.append(f'        annotations = {source}.__annotations__')
    lines.append('        processor.prepend([')
    for it in items:
        lines.append(f'            {it},')
    lines.append('        ])')

    children = _child_converters(plan)
    if children:                                # wire child callables at @command
        lines.append('    @classmethod')
        lines.append('    def fixup_converters(cls, converter):')
        lines.append('        cls.converter = converter')
        lines.append('        annotations = converter.__annotations__')
        for child_callable, param in children.items():
            cname = names[child_callable]
            lines.append(f'        if not {cname}.converter:')
            lines.append(f'            {cname}.fixup_converters(annotations[{param!r}])')
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
