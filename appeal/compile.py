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

from .plan import Terminal, NO_DEFAULT
from .runtime import Converter, signature_fingerprint, _params_host


# builtins we hard-code as a literal (the fingerprint guarantees the shape,
# so there's no need to read them off the callable at runtime)
_BUILTIN_CONVERTERS = (str, bool, int, float, complex)


def _conjurable(child_plan):
    """
    Can this converter be invoked with ZERO operands (so a required slot summons
    it from its defaults when the line runs out)?  Every slot must be skippable:
    optional (has a default), a *args (contributes nothing), or a required slot
    whose own converter is RECURSIVELY conjurable (a required g1 whose g0 needs
    no operands still builds from nothing).  Read from slot.default, NOT
    plan.minimum: v1's promotion inflates minimum for nested groups, but the
    compiled engine fills greedily, so it wants the intrinsic shape.
    """
    for slot in child_plan.slots:
        if slot.repeat or slot.default is not NO_DEFAULT:
            continue                            # *args / optional: skippable
        if isinstance(slot.child, Terminal) or not _conjurable(slot.child):
            return False                        # a required leaf, or a required
    return True                                 # non-conjurable group: needs input


def _converter_key(plan):
    """
    A converter's identity for deduplication.  Normally the callable (one class
    per callable, per Larry's rule).  But a builtin iterable-constructor group --
    tuple[int, str] -- has callable `tuple` for EVERY parameterization, so those
    collide onto one class unless keyed by their element shape too.  Without this,
    tuple[int, str] and tuple[str, int] would share a class and mis-convert.
    """
    if plan.callable in (tuple, list):
        return (plan.callable, _group_shape(plan))
    return plan.callable


def _group_shape(plan):
    "A hashable signature of a builtin group's element slots (converter + shape)."
    return tuple(
        ((slot.child.converter if isinstance(slot.child, Terminal)
          else _converter_key(slot.child)),
         slot.required, slot.repeat, slot.trailing)
        for slot in plan.slots)


def _converters(plans):
    """
    The distinct converters reachable from the command Plans, as an ordered
    map callable -> representative Plan (each converter's own children before
    it).  A converter is identified by its callable, so a callable reached
    from several slots/options appears once -- one converter, one entry.
    """
    found = {}

    def visit(plan):
        key = _converter_key(plan)
        if key in found:
            return
        for slot in plan.slots:                 # its own converters first
            if not isinstance(slot.child, Terminal):
                visit(slot.child)
        for option in plan.options:
            if option.kind == 'group':
                visit(option.child)
        found[key] = plan
    for plan in plans:
        visit(plan)
    return found


def _class_names(converters):
    "Assign each converter its class name (Converter_<name>), unique per program."
    names, used = {}, set()
    for key, plan in converters.items():
        # a plan name can carry dots/dashes (a precommand named for the program,
        # a dashed command word) -- sanitize into a valid Python identifier.
        ident = ''.join(c if c.isalnum() or c == '_' else '_' for c in plan.name)
        base = name = f'Converter_{ident}'
        n = 2
        while name in used:                     # two distinct converters, one name
            name, n = f'{base}_{n}', n + 1
        names[key] = name
        used.add(name)
    return names


def _child_converters(plan):
    """
    The distinct child converters this converter references (group operands
    and group options), as callable -> a representative parameter name.  Feeds
    fixup_converters: one wire-up per child, its callable read off `annotations`.
    """
    annotations = getattr(_params_host(plan.callable), '__annotations__', {}) or {}
    children = {}
    for slot in plan.slots:
        if not isinstance(slot.child, Terminal):
            children.setdefault(_converter_key(slot.child), slot.name)
    for option in plan.options:
        # fixup wires a child off annotations[name]; a decoration-supplied group
        # option (e.g. the help precommand's -h/--help) isn't a signature
        # parameter, so it can't be wired that way -- skip it here.
        if option.kind == 'group' and option.name in annotations:
            children.setdefault(_converter_key(option.child), option.name)
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
    for key, plan in converters.items():
        classes[key] = _build_class(plan, classes)
    for plan in plans:                          # wire each command's reachable tree
        classes[_converter_key(plan)].fixup_converters(plan.callable)
    return classes


def build_converter(plan):
    "The single command's live Converter subclass (convenience over build_converters)."
    return build_converters([plan])[_converter_key(plan)]


def _build_class(plan, classes):
    "One Converter subclass; group refs and fixup resolve through `classes`."
    option_specs = []
    for o in plan.options:
        if o.kind == 'flag':
            kind, extra = 'flag', None
        elif o.kind == 'value' and len(o.converters) == 1:
            kind, extra = 'value', o.converters[0]
        elif o.kind == 'value':                 # multi-oparg: (constructor, *leaves)
            kind, extra = 'multi', o.converters
        elif o.kind in ('fold', 'fold1'):       # counter/accumulator/mapping
            kind, extra = 'fold', o.converters[0]
        elif o.kind == 'group':                 # sibling converter-group option
            kind, extra = 'group', _converter_key(o.child)
        else:                                   # nullary and the rest: later
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
            elif kind == 'multi':
                conv = extra                            # (constructor, *leaves)
            elif kind == 'fold':
                conv = extra                            # the MultiOption (build's rewrite)
            else:                                       # value(single)
                conv = annotations.get(name, extra)
            items.append(self.Option(name, conv, *strings))
        boundary = len(items)   # a conjurable slot's PreOption inserts here:
                                # after the last required-or-group slot (so it
                                # leaps over optional leaves to reach its slot)
        for slot in plan.slots:
            req = slot.default is NO_DEFAULT        # intrinsic, not promoted
            if isinstance(slot.child, Terminal):
                conv = annotations.get(slot.name, slot.child.converter)
                if slot.repeat:                         # *args of a leaf
                    items.append(self.Repeat([
                        self.Argument(slot.name, conv, required=False)]))
                    boundary = len(items)
                    continue
                items.append(self.Argument(slot.name, conv,
                                           required=req,
                                           trailing=slot.trailing))
                if req:
                    boundary = len(items)
                continue
            # a converter group -- reference the child class
            childcls = classes[_converter_key(slot.child)]
            preopts = []
            for o in slot.child.options:        # flat recognition (see emit_source)
                if o.kind == 'flag':
                    for s in o.strings:
                        preopts.append(
                            self.PreOption(s, o.name, slot.name, childcls))
                elif slot.repeat and o.kind == 'value' and len(o.converters) == 1:
                    # a windowed group's VALUE option binds forward at a window
                    # boundary (--label up): carry its oparg converter
                    for s in o.strings:
                        preopts.append(self.PreOption(
                            s, o.name, slot.name, childcls, o.converters[0]))
            if slot.repeat:                             # windowed *args
                items.append(self.Repeat(
                    preopts + [self.Argument(slot.name, childcls, required=False)]))
                boundary = len(items)
                continue
            for k, pre in enumerate(preopts):           # leap over optionals
                items.insert(boundary + k, pre)
            items.append(self.Argument(slot.name, childcls, required=req))
            boundary = len(items)

        processor.prepend(items)

    children = _child_converters(plan)

    def fixup_children(cls, converter):
        annotations = converter.__annotations__
        for child_key, param in children.items():
            child_cls = classes[child_key]
            if not child_cls.converter:
                child_cls.fixup_converters(annotations[param])

    dct = {'register': register, 'trailing': _n_trailing(plan),
           'binds': plan.binds,
           'constructs': plan.constructs, '__module__': __name__}
    if children:                                # else the base no-op suffices
        dct['_fixup_children'] = classmethod(fixup_children)
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


def _fold_expr(plan, o):
    """
    Source for a counter/accumulator/mapping option.  The explicit spellings
    (counter(), accumulator[T], mapping[K, V]) ARE the annotation, reused as
    annotations['name'].  The dict[K, V]/list[T] sugar is canonicalized to
    mapping[...]/accumulator[...] -- both subscript portably (they're Appeal's
    own _Subscriptable, not the builtins) -- since the raw generic isn't a
    usable converter.
    """
    annotation = plan.callable.__annotations__.get(o.name)
    origin = getattr(annotation, '__origin__', None)
    if origin is list:
        return f'accumulator[{_arg_expr(o, annotation, 0)}]'
    if origin is dict:
        return (f'mapping[{_arg_expr(o, annotation, 0)}, '
                f'{_arg_expr(o, annotation, 1)}]')
    if o.name in plan.callable.__annotations__:
        return _converter_expr(plan, o.name, o.converters[0])
    # a decoration-supplied fold (delivered via **kwargs, so the parameter isn't
    # in the signature): reconstruct it from o.converters -- the fold class plus
    # its value converters -- instead of reading annotations['name'].
    fold = o.converters[0]
    values = o.converters[1:]
    if not values:                              # counter(): a nullary fold
        return f'{fold.__name__}()'
    return f'{fold.__name__}[{", ".join(_type_literal(v) for v in values)}]'


def _type_literal(converter):
    "A converter as a bare literal (builtins by name); the reconstruction case."
    if converter in _BUILTIN_CONVERTERS:
        return converter.__name__
    return converter.__name__                   # a named class, imported or global


def _arg_expr(o, annotation, i):
    "The i-th type argument of a sugar generic, hard-coded per the rule."
    arg = annotation.__args__[i]
    if arg in _BUILTIN_CONVERTERS:
        return arg.__name__
    return f'annotations[{o.name!r}].__args__[{i}]'


def _value_option_expr(plan, o):
    """
    Source for a value option's converter: one leaf (`--units F`), or a
    multi-oparg `(constructor, leaf, ...)` tuple (`--where X Y`, `--coord 3 4`).
    The constructor is `tuple` for a tuple[...] option, else the inner
    callable (annotations['name']); each operand leaf follows the usual rule.
    """
    if len(o.converters) == 1:
        return _converter_expr(plan, o.name, o.converters[0])
    constructor = o.converters[0]
    head = 'tuple' if constructor is tuple else _converter_expr(
        plan, o.name, constructor)
    parts = [head] + [_leaf_expr(o, constructor, i, leaf)
                      for i, leaf in enumerate(o.converters[1:])]
    return '(' + ', '.join(parts) + ')'


def _leaf_expr(o, constructor, i, leaf):
    """
    Source for a multi-oparg option's i-th operand converter: a builtin as a
    literal, else read live off the inner callable -- a tuple[...] element from
    its __args__, a multi-param converter's operand from its __annotations__.
    """
    if leaf in _BUILTIN_CONVERTERS:
        return leaf.__name__
    if constructor is tuple:                    # tuple[...] element type
        return f'annotations[{o.name!r}].__args__[{i}]'
    inner = constructor.__code__.co_varnames[i]  # the converter's own parameter
    return f'annotations[{o.name!r}].__annotations__[{inner!r}]'


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
        elif o.kind == 'value':                 # one leaf, or a multi-oparg tuple
            conv = _value_option_expr(plan, o)
        elif o.kind in ('fold', 'fold1'):       # counter/accumulator/mapping
            conv = _fold_expr(plan, o)
        elif o.kind == 'group':                 # sibling converter-group option
            conv = names[_converter_key(o.child)]   # the child Converter class
        else:                                   # nullary and the rest: later
            continue
        strings = ', '.join(repr(s) for s in o.strings)
        items.append(f'self.Option({o.name!r}, {conv}, {strings})')

    boundary = len(items)
    for slot in plan.slots:
        # intrinsic requiredness (has no default), NOT slot.required -- v1's
        # promotion inflates the latter, but the compiled engine fills greedily
        # (minimum governs operands), so a defaulted operand stays optional.
        req = slot.default is NO_DEFAULT
        if isinstance(slot.child, Terminal):
            conv = _converter_expr(plan, slot.name, slot.child.converter)
            if slot.repeat:                         # *args of a leaf
                items.append(f'self.Repeat([self.Argument({slot.name!r}, '
                             f'{conv}, required=False)])')
                boundary = len(items)
                continue
            extra = ', trailing=True' if slot.trailing else ''
            items.append(f'self.Argument({slot.name!r}, {conv}, '
                         f'required={req!r}{extra})')
            if req:
                boundary = len(items)
            continue
        # a converter group -- reference the child class
        childcls = names[_converter_key(slot.child)]
        preopts = []
        for o in slot.child.options:            # flat recognition: a group's flag
            if o.kind == 'flag':                # is known anywhere on the line;
                for s in o.strings:             # firing it forces the group (a
                    preopts.append(f'self.PreOption({s!r}, {o.name!r}, '  # conjurable
                                   f'{slot.name!r}, {childcls})')      # from defaults,
                continue                        # non-conjurable must get operands)
            if slot.repeat and o.kind == 'value' and len(o.converters) == 1:
                # a windowed group's VALUE option binds forward at a boundary
                # (--label up): carry its oparg converter as a source expr
                c = o.converters[0]
                conv = (c.__name__ if c in _BUILTIN_CONVERTERS else
                        f'{childcls}.converter.__annotations__'
                        f'.get({o.name!r}, str)')
                for s in o.strings:
                    preopts.append(f'self.PreOption({s!r}, {o.name!r}, '
                                   f'{slot.name!r}, {childcls}, {conv})')
        if slot.repeat:                             # windowed *args
            inner = ', '.join(preopts +
                              [f'self.Argument({slot.name!r}, {childcls}, required=False)'])
            items.append(f'self.Repeat([{inner}])')
            boundary = len(items)
            continue
        for k, pre in enumerate(preopts):           # leap over optionals
            items.insert(boundary + k, pre)
        items.append(f'self.Argument({slot.name!r}, {childcls}, '
                     f'required={req!r})')
        boundary = len(items)

    lines = []
    if is_command:
        # the registry key is the command word (function name, _->-); the
        # class name keeps underscores (a dash is an invalid identifier)
        lines.append(f'@Appeal._converter({plan.name.replace("_", "-")!r})')
    lines.append(f'class {names[_converter_key(plan)]}(Converter):')
    # a group built on a code-less builtin (tuple/list/...) has no signature of
    # its own to drift; its shape is already covered structurally by the parent
    # command's fingerprint (the tuple[int, int] annotation token).  Bake None so
    # fixup skips verification rather than fingerprinting a callable with no code.
    fp = (signature_fingerprint(plan.callable)
          if _params_host(plan.callable) is not None else None)
    lines.append(f'    _fingerprint = {fp!r}')
    if plan.callable in (tuple, list):      # iterable constructor: build, don't splat
        lines.append('    _iterable = True')
    if plan.binds is not None:              # class-as-app: method binds to an instance
        lines.append(f'    binds = {plan.binds!r}')
    if plan.constructs is not None:         # class command: stash the instance built
        lines.append(f'    constructs = {plan.constructs!r}')
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
        lines.append('    def _fixup_children(cls, converter):')
        lines.append('        annotations = converter.__annotations__')
        for child_key, param in children.items():
            cname = names[child_key]
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
'''


def emit_module(plans, baked_fingerprint=None, default_mappings_fp=None,
                default_options_fp=None):
    """
    Emit a complete, runnable compiled parser module for a list of plans.
    baked_fingerprint (from runtime.config_fingerprint at compile time) is the
    configuration identity the compiled Appeal verifies against at __init__;
    default_mappings_fp/default_options_fp are the compile-time policy
    fingerprints the sentinel default falls back to.
    """
    converters = _converters(plans)
    names = _class_names(converters)
    commands = {_converter_key(plan) for plan in plans}
    parts = [_MODULE_HEADER,
             f'Appeal = appeal_class(baked_fingerprint={baked_fingerprint!r},\n'
             f'                      default_mappings_fp={default_mappings_fp!r},\n'
             f'                      default_options_fp={default_options_fp!r})',
             '']
    for key, plan in converters.items():        # children first; commands self-register
        parts.append(emit_source(plan, names, is_command=key in commands))
        parts.append('')
    return '\n'.join(parts)
