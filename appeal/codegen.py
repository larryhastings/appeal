#!/usr/bin/env python3
#
# appeal/codegen.py
# Part of Appeal v2.
# Copyright 2021-2026 by Larry Hastings
#
# Rung 3: emit Python source from a Plan.  The same generated source
# serves two delivery modes:
#
#   * in-process: exec the source in a namespace holding the refs
#     (converters, the command, defaults) *by reference*;
#   * standalone: write a script where every ref is *rendered*--
#     as an import, a literal, or the embedded runtime.
#
# The north star: anything the in-process parser accepts must either
# emit as a standalone script or refuse *by name* at emission time.
# No silent divergence between the two modes.
#
# The counting automaton's decisions compile to if/elif chains whose
# conditions test membership in *constant* suffix-count sets--the
# generated source is the disassembly, and the disassembly is
# ordinary Python.

import linecache
import re

import inspect as _inspect

from .build import (
    all_options, help_option_strings, subtree_option_keys,
    )
from .help import command_set_corpus, merge_docs, summary
from .plan import Terminal, NO_DEFAULT, command_set_usage
from .runtime import (
    AppealConfigurationError, UsageError, accumulate, call_converter,
    collect_mapping, convert, fold, parse_tokens, check_count,
    default_templates, render_command_listing, render_help_page,
    run_command_set, run_main, runtime_source, window_options,
    )


class Refs:
    """
    The names the generated source expects to find in its namespace,
    and the objects they stand for.  In-process, these bind directly;
    standalone, each must be rendered (see render_ref).

    Names are meaningful--"_pair", "_a_default"--because the
    generated source is the disassembly, and disassembly you can't
    read is just bytecode with extra steps.  A counter is appended
    only on a genuine collision.
    """
    def __init__(self):
        self.objects = {}      # name -> object

    def add(self, preferred, obj, dedupe=True):
        import re
        preferred = re.sub(r'\W', '_', preferred)
        if dedupe:
            for name, existing in self.objects.items():
                if existing is obj:
                    return name
        name = preferred
        counter = 1
        while name in self.objects and self.objects[name] is not obj:
            counter += 1
            name = f'{preferred}{counter}'
        self.objects[name] = obj
        return name


_builtin_converters = {str: 'str', int: 'int', float: 'float', bool: 'bool',
                       tuple: 'tuple'}


_RESERVED = frozenset((
    'argv', 'given', 'operands', 'i', 'n', 'remaining', 'gate',
    'positions', 'rest', 'parse_tokens', 'convert', 'accumulate',
    'mapping', 'fold', 'call_converter', 'check_count', 'window_options',
    'UsageError',
    ))


def _local(name):
    "Parameters named like the emitter's own locals get a suffix."
    return name + '_' if name in _RESERVED else name


class _Emitter:
    def __init__(self, plan, refs=None, fill_names=None, templates=None):
        # refs and fill_names may be shared across the emitters of
        # a command set, so converters common to several commands
        # keep one name (and one rendering) in the combined source
        self.plan = plan
        self.templates = default_templates if templates is None else templates
        self.refs = Refs() if refs is None else refs
        self.lines = []
        self.constants = []           # (name, literal-source) pairs
        self.fill_names = {} if fill_names is None else fill_names
        self.gated = getattr(plan, 'gated', False)
        self.usage_const = f'_USAGE_{plan.name}'

    def line(self, s=''):
        self.lines.append(s)

    def constant(self, name, literal):
        self.constants.append(f'{name} = {literal}')

    def leaf_expr(self, converter):
        builtin = _builtin_converters.get(converter)
        if builtin:
            return builtin
        preferred = '_' + getattr(converter, '__name__', 'converter').lstrip('_')
        return self.refs.add(preferred, converter)

    def converter_expr(self, child):
        converter = child.converter
        builtin = _builtin_converters.get(converter)
        if builtin:
            return builtin
        preferred = '_' + getattr(converter, '__name__', 'converter').lstrip('_')
        return self.refs.add(preferred, converter)

    # ---- plan collection ----

    def collect_children(self, plan, out):
        children = [slot.child for slot in plan.slots]
        children.extend(o.child for o in plan.options)
        for child in children:
            if isinstance(child, Terminal) or child is None:
                continue
            if id(child) in self.fill_names:
                continue
            base = f'_fill_{child.name}'
            fname = base
            k = 1
            while fname in self.fill_names.values():
                k += 1
                fname = f'{base}{k}'
            self.fill_names[id(child)] = fname
            self.collect_children(child, out)
            out.append(child)

    # ---- the automaton, compiled ----

    def feasibility(self, c, suffix):
        # "can the slots after this one consume what's left?"
        counts, minimum = suffix
        take = 'remaining' if c == 0 else f'remaining - {c}'
        if counts is None:
            return f'{take} >= {minimum}'
        if len(counts) == 1:
            only = next(iter(counts))
            return f'{take} == {only}'
        literal = ', '.join(str(x) for x in sorted(counts))
        return f'{take} in ({literal})'

    @staticmethod
    def describe_counts(suffix):
        counts, minimum = suffix
        if counts is None:
            return f'{minimum} or more'
        ordered = sorted(counts)
        if len(ordered) == 1:
            return str(ordered[0])
        return ', '.join(str(c) for c in ordered[:-1]) + f' or {ordered[-1]}'

    def emit_slots(self, plan, indent):
        """
        Emit the slot-filling code for a plan\'s non-trailing slots.
        Uses/updates the local variables operands, i, remaining.
        Binds each slot\'s value to a local named after the parameter.
        """
        pad = ' ' * indent
        for slot_index, slot in enumerate(plan.slots):
            if slot.trailing:
                break

            if slot.repeat:
                m = slot.suffix_after[1]
                if isinstance(slot.child, Terminal):
                    conv = self.converter_expr(slot.child)
                    self.line(f'{pad}_take = remaining - {m}')
                    self.line(f'{pad}{_local(slot.name)} = tuple(convert({conv}, _x, '
                              f'{slot.name!r}, {self.usage_const}) '
                              f'for _x in operands[i:i + _take])')
                    self.line(f'{pad}i += _take')
                    self.line(f'{pad}remaining -= _take')
                    continue
                # a converter group on *args: fixed-size instances,
                # options bound to instances by window
                child = slot.child
                k = child.minimum
                fill = self.fill_names[id(child)]
                occ = ', '.join(f'{o.key!r}: given.get({o.key!r}, ())'
                                for o in child.options)
                self.line(f'{pad}# {slot.name}: zero or more {child.name!r} '
                          f'groups, {k} operand{"s" if k != 1 else ""} each')
                self.line(f'{pad}_take = remaining - {m}')
                self.line(f'{pad}if _take % {k}:')
                self.line(f'{pad}    raise UsageError(f"wrong number of arguments '
                          f'for {slot.name!r}: each takes {k}, '
                          f'{{_take % {k}}} left over", {self.usage_const})')
                self.line(f'{pad}_count = _take // {k}')
                gate_arg = ', gate=gate' if self.gated else ''
                extra = ', gate, positions' if self.gated else ''
                self.line(f'{pad}_givens = window_options({{{occ}}}, i, {k}, '
                          f'_count, {slot.name!r}, {self.usage_const}{gate_arg})')
                self.line(f'{pad}{_local(slot.name)} = []')
                self.line(f'{pad}for _j in range(_count):')
                self.line(f'{pad}    _item, i = {fill}(operands, i, {k}, _givens[_j]{extra})')
                self.line(f'{pad}    {_local(slot.name)}.append(_item)')
                self.line(f'{pad}remaining -= _take')
                continue

            options = slot.count_options

            def emit_take(c, pad2):
                if c == 0 and not slot.required:
                    child = slot.child
                    default_name = self.refs.add(f'_{slot.name}_default', slot.default, dedupe=False)
                    if isinstance(child, Terminal) or not subtree_option_keys(child):
                        self.line(f'{pad2}{_local(slot.name)} = {default_name}')
                        return
                    keys = subtree_option_keys(child)
                    if child.minimum == 0:
                        # v1 semantics: giving one of the group's
                        # options forces it, defaults and all
                        literal = ', '.join(repr(k) for k in keys)
                        fill = self.fill_names[id(child)]
                        extra = ', gate, positions' if self.gated else ''
                        self.line(f'{pad2}if given.keys() & {{{literal}}}:')
                        self.line(f'{pad2}    # an option of {child.name!r}: the group is')
                        self.line(f'{pad2}    # forced, entered with zero operands')
                        self.line(f'{pad2}    {_local(slot.name)}, i = {fill}(operands, i, 0, given{extra})')
                        self.line(f'{pad2}else:')
                        self.line(f'{pad2}    {_local(slot.name)} = {default_name}')
                        return
                    # the group needs operands it can't have; its
                    # options are therefore errors
                    for key in keys:
                        self.line(f'{pad2}if {key!r} in given:')
                        self.line(f"{pad2}    raise UsageError(f\"option {key} "
                                  f"requires {slot.name!r}\", {self.usage_const})")
                    self.line(f'{pad2}{_local(slot.name)} = {default_name}')
                    return
                if isinstance(slot.child, Terminal):
                    conv = self.converter_expr(slot.child)
                    self.line(f'{pad2}{_local(slot.name)} = convert({conv}, operands[i], '
                              f'{slot.name!r}, {self.usage_const})')
                    self.line(f'{pad2}i += 1')
                    self.line(f'{pad2}remaining -= 1')
                    return
                fill = self.fill_names[id(slot.child)]
                extra = ', gate, positions' if self.gated else ''
                self.line(f'{pad2}{_local(slot.name)}, i = {fill}(operands, i, {c}, given{extra})')
                self.line(f'{pad2}remaining -= {c}')

            if len(options) == 1:
                emit_take(options[0], pad)
                if self.gated and slot.barrier:
                    self.line(f'{pad}gate = i  # {slot.name!r} is fed; '
                              f'options behind it are now legal')
                continue

            # the counting decision, with its reasoning attached:
            # take the most operands this slot can, as long as the
            # slots after it can still consume the rest.
            takes = ' or '.join(str(c) for c in options if c)
            skippable = (0 in options) and (not slot.required)
            what = f'takes {takes}' + (', or is skipped' if skippable else '')
            self.line(f'{pad}# {slot.name}: {what}; '
                      f'the slots after it need {self.describe_counts(slot.suffix_after)}')
            for k, c in enumerate(options):
                if k == 0:
                    self.line(f'{pad}if {self.feasibility(c, slot.suffix_after)}:')
                elif k < len(options) - 1:
                    self.line(f'{pad}elif {self.feasibility(c, slot.suffix_after)}:')
                else:
                    # check_count guarantees some branch fires;
                    # the last one needs no test
                    self.line(f'{pad}else:')
                emit_take(c, pad + '    ')
            if self.gated and slot.barrier:
                self.line(f'{pad}gate = i  # {slot.name!r} is fed; '
                          f'options behind it are now legal')

    def emit_options(self, plan, indent):
        """
        Emit the code resolving a rule\'s own options from `given`,
        binding each option\'s value to a local named after its
        parameter.
        """
        pad = ' ' * indent
        if any(o.kwargs_delivered for o in plan.options):
            self.line(f'{pad}_extra = {{}}')
        seen = set()
        for o in plan.options:
            key = o.key
            if o.kwargs_delivered:
                # a **kwargs option: absent means not passed (v1)
                target = f"_extra[{o.name!r}]"
                if o.kind == 'flag':
                    self.line(f'{pad}if {key!r} in given:')
                    self.line(f'{pad}    {target} = True')
                    continue
                expr = self.option_value_expr(o, key)
                self.line(f'{pad}if {key!r} in given:')
                self.line(f'{pad}    {target} = {expr}')
                continue
            first = o.name not in seen
            seen.add(o.name)
            if not first:
                # several rules share this parameter: giving two of
                # them is "more than once" (v1: --north --south)
                earlier = [r.key for r in plan.options
                           if r.name == o.name and r.key != o.key]
                condition = ' or '.join(f'{k!r} in given' for k in earlier)
                self.line(f'{pad}if {o.key!r} in given and ({condition}):')
                self.line(f'{pad}    raise UsageError(f"option {o.key} '
                          f'specified more than once", {self.usage_const})')
            if o.child is not None:
                # an absent option group: its inner options have
                # nothing to attach to
                for inner in subtree_option_keys(o.child):
                    self.line(f'{pad}if {inner!r} in given and {o.key!r} not in given:')
                    self.line(f'{pad}    raise UsageError(f"option {inner} '
                              f'requires {o.key}", {self.usage_const})')
            if first and o.kind == 'flag':
                if o.default is False:
                    absent = 'False'
                else:
                    # @app.option can declare a flag on a parameter
                    # whose own default isn't False; that default
                    # still fills when the flag is absent
                    absent = self.refs.add(f'_{o.name}_default', o.default, dedupe=False)
                self.line(f'{pad}{_local(o.name)} = given.get({key!r}, {absent})')
                continue
            expr = ('True' if o.kind == 'flag'
                    else self.option_value_expr(o, key))
            if first:
                # several rules may share a parameter (per-
                # declaration @app.option): the first initializes
                # the default, each rule overwrites when given
                default_name = self.refs.add(f'_{o.name}_default', o.default,
                                             dedupe=False)
                self.line(f'{pad}{_local(o.name)} = {default_name}')
            self.line(f'{pad}if {key!r} in given:')
            self.line(f'{pad}    {_local(o.name)} = {expr}')

    def option_value_expr(self, o, key, default_name=None):
        "The expression converting given[key] per the option's kind."
        if o.kind == 'nullary':
            # a value-producing flag: presence calls the converter
            return f'{self.leaf_expr(o.converters[0])}()'
        if o.kind == 'group':
            # the option's inline operands fill the converter group;
            # inner options resolve from the same `given`
            fill = self.fill_names[id(o.child)]
            extra = ', gate, positions' if self.gated else ''
            return (f'{fill}(list(given[{key!r}]), 0, '
                    f'len(given[{key!r}]), given{extra})[0]')
        if o.kind == 'value' and len(o.converters) == 1:
            conv = self.leaf_expr(o.converters[0])
            return (f'convert({conv}, given[{key!r}], '
                    f'{o.name!r}, {self.usage_const})')
        if o.kind == 'value':
            fn_name = self.leaf_expr(o.converters[0])
            convs = ', '.join(self.leaf_expr(c) for c in o.converters[1:])
            return (f'call_converter({fn_name}, ({convs}), given[{key!r}], '
                    f'{o.name!r}, {self.usage_const})')
        if o.kind in ('fold', 'fold1'):
            cls_name = self.leaf_expr(o.converters[0])
            convs = ', '.join(self.leaf_expr(c) for c in o.converters[1:])
            comma = ',' if len(o.converters) == 2 else ''
            occurrences = (f'given[{key!r}]' if o.kind == 'fold'
                           else f'(given[{key!r}],)')
            if default_name is None:
                default_name = self.refs.add(f'_{o.name}_default', o.default,
                                             dedupe=False)
            return (f'fold({cls_name}, ({convs}{comma}), {occurrences}, '
                    f'{default_name}, {o.name!r}, {self.usage_const})')
        if o.kind == 'accumulate':
            conv = self.leaf_expr(o.converters[0])
            return (f'accumulate({conv}, given[{key!r}], '
                    f'{o.name!r}, {self.usage_const})')
        kconv = self.leaf_expr(o.converters[0])
        vconv = self.leaf_expr(o.converters[1])
        return (f'collect_mapping({kconv}, {vconv}, given[{key!r}], '
                f'{o.name!r}, {self.usage_const})')

    def emit_fill_function(self, plan):
        fname = self.fill_names[id(plan)]
        params = ', '.join(s.name for s in plan.slots)
        extra = ', gate, positions' if self.gated else ''
        self.line(f'def {fname}(operands, i, remaining, given{extra}):')
        if (self.gated and plan.options
                and not getattr(plan, 'certain', True)
                and not getattr(plan, 'windowed', False)):
            # the gate rule: this group's options may not appear
            # before every required group to its left was fed
            for o in plan.options:
                self.line(f'    if {o.key!r} in given and '
                          f'positions.get({o.key!r}, 0) < gate:')
                self.line(f'        raise UsageError(f"option {o.key} '
                          f'appears too early on the command line", '
                          f'{self.usage_const})')
        self.line(f'    # reads exactly `remaining` operands and builds')
        if plan.callable in (tuple, list):
            self.line(f'    # the {plan.callable.__name__} ({params})')
        else:
            self.line(f'    # {plan.name}({params})')
        self.emit_slots(plan, 4)
        self.emit_options(plan, 4)
        args = [_local(s.name) for s in plan.slots]
        args.extend(f'{name}={_local(name)}' for name in dict.fromkeys(
            o.name for o in plan.options if not o.kwargs_delivered))
        if any(o.kwargs_delivered for o in plan.options):
            args.append('**_extra')
        if plan.callable is tuple:
            # a tuple display, not a call: nothing to import
            comma = ',' if len(args) == 1 else ''
            self.line(f'    return ({", ".join(args)}{comma}), i')
        elif plan.callable is list:
            self.line(f'    return [{", ".join(args)}], i')
        else:
            converter_name = self.refs.add('_' + plan.name.lstrip('_'), plan.callable)
            self.line(f'    return {converter_name}({", ".join(args)}), i')
        self.line()

    def emit_parse_function(self, command_split=None):
        plan = self.plan
        fname = f'parse_{plan.name}'
        options_const = f'_OPTIONS_{plan.name}'

        table_items = []
        for owner, o in all_options(plan):
            windowed = getattr(owner, 'windowed', False)
            for s in o.strings:
                table_items.append(f'{s!r}: {o.table_entry(windowed=windowed)!r}')
        help_keys = help_option_strings(plan) if command_split is None else ()
        for s in help_keys:
            table_items.append(f"{s!r}: ('--help', 'flag')")
        self.constant(self.usage_const, repr(plan.usage()))
        self.constant(options_const, '{%s}' % ', '.join(table_items))

        command_name = self.refs.add('_' + plan.name.lstrip('_'), plan.callable)
        trailing = [s for s in plan.slots if s.trailing]

        self.line(f'def {fname}(argv):')
        if self.gated:
            self.line(f'    positions = {{}}')
        gate_kwarg = ', positions=positions' if self.gated else ''
        if command_split is None:
            self.line(f'    operands, given = parse_tokens(argv, {options_const}, '
                      f'{self.usage_const}{gate_kwarg})')
        else:
            self.line(f'    # the global command: its operands end at the first')
            self.line(f'    # operand naming a command (or at the maximum)')
            self.line(f'    operands, given, _rest = parse_tokens(argv, {options_const}, '
                      f'{self.usage_const}, command_split={command_split!r}{gate_kwarg})')
        if help_keys:
            # compiled means the documentation too: the corpus is
            # predigested at build time, formatted at runtime
            corpus = merge_docs(plan)
            corpus_name = self.refs.add(f'_HELP_{plan.name}', corpus, dedupe=False)
            templates_name = self.refs.add(f'_TEMPLATES_{plan.name}', self.templates, dedupe=False)
            self.line(f"    if given.pop('--help', False):")
            self.line(f'        print(render_help_page({self.usage_const}, '
                      f"{corpus_name}, {templates_name}), end='')")
            self.line(f'        return')
        self.line(f'    n = len(operands)')
        if plan.valid_counts is None:
            self.line(f'    check_count(n, {plan.minimum}, None, None, {self.usage_const})')
        else:
            self.line(f'    check_count(n, {plan.minimum}, {plan.maximum}, '
                      f'{set(sorted(plan.valid_counts))!r}, {self.usage_const})')

        if trailing:
            k = len(trailing)
            for j, slot in enumerate(trailing):
                index = -(k - j)
                conv = self.converter_expr(slot.child)
                self.line(f'    {_local(slot.name)} = convert({conv}, operands[{index}], '
                          f'{slot.name!r}, {self.usage_const})')
            self.line(f'    operands = operands[:-{k}]')
            self.line(f'    n -= {k}')

        self.line(f'    i = 0')
        self.line(f'    remaining = n')
        if self.gated:
            self.line(f'    gate = 0')
        self.emit_slots(plan, 4)

        self.emit_options(plan, 4)

        args = []
        for slot in plan.slots:
            if slot.trailing:
                continue
            if slot.repeat:
                args.append(f'*{_local(slot.name)}')
            else:
                args.append(_local(slot.name))
        for slot in trailing:
            args.append(f'{slot.name}={_local(slot.name)}')
        for name in dict.fromkeys(o.name for o in plan.options
                                   if not o.kwargs_delivered):
            args.append(f'{name}={_local(name)}')
        if any(o.kwargs_delivered for o in plan.options):
            args.append('**_extra')
        if command_split is None:
            self.line(f'    return {command_name}({", ".join(args)})')
        else:
            self.line(f'    return {command_name}({", ".join(args)}), _rest')
        self.line()

    def emit(self, command_split=None):
        children = []
        self.collect_children(self.plan, children)
        for child in children:
            self.emit_fill_function(child)
        self.emit_parse_function(command_split)
        source = '\n'.join(self.constants) + '\n\n' + '\n'.join(self.lines) + '\n'
        return source, self.refs


def emit(plan, templates=None):
    """
    Generate the parser source for a plan.  Returns (source, refs).
    """
    return _Emitter(plan, templates=templates).emit()


def compile_plan(plan, command_split=None, templates=None):
    """
    In-process mode: exec the generated source, binding the refs
    by reference.  Returns the parse function.  The source is
    registered with linecache, so tracebacks and pdb show the
    generated lines.

    command_split compiles the plan as a global command (see
    parse_tokens): the parse function returns (result, rest).
    """
    source, refs = _Emitter(plan, templates=templates).emit(command_split)
    filename = f'<appeal generated: {plan.name}>'
    linecache.cache[filename] = (
        len(source), None, source.splitlines(keepends=True), filename)
    namespace = {
        'parse_tokens': parse_tokens,
        'convert': convert,
        'accumulate': accumulate,
        'collect_mapping': collect_mapping,
        'fold': fold,
        'call_converter': call_converter,
        'window_options': window_options,
        'render_help_page': render_help_page,
        'check_count': check_count,
        'UsageError': UsageError,
        }
    namespace.update(refs.objects)
    code = compile(source, filename, 'exec')
    exec(code, namespace)
    parse = namespace[f'parse_{plan.name}']
    parse.source = source
    return parse


def emit_command_set(commands, global_plan=None, prog=None, templates=None):
    """
    Generate the source for a multi-command program: one parse
    function per command, an optional global-command parse function
    (command mode), and a dispatcher named parse_command_set.
    commands maps command-word -> Plan.  Returns (source, refs).
    """
    templates = default_templates if templates is None else templates
    refs = Refs()
    fill_names = {}
    chunks = []
    auto_help = 'help' not in commands
    command_words = frozenset(commands) | ({'help'} if auto_help else set())
    if global_plan is not None:
        emitter = _Emitter(global_plan, refs, fill_names)
        split = (global_plan.minimum, global_plan.maximum, command_words)
        chunks.append(emitter.emit(command_split=split)[0])
    for plan in commands.values():
        emitter = _Emitter(plan, refs, fill_names, templates=templates)
        chunks.append(emitter.emit()[0])

    entries = [(word, summary(plan.callable)) for word, plan in commands.items()]
    usage_line = command_set_usage(prog or 'program', global_plan)
    corpus = command_set_corpus(global_plan, entries, auto_help)
    usage = render_command_listing(usage_line, corpus, templates)
    corpus_name = refs.add('_HELP_command_set', corpus, dedupe=False)
    templates_name = refs.add('_TEMPLATES_command_set', templates, dedupe=False)
    globals_name = f'parse_{global_plan.name}' if global_plan is not None else 'None'
    table = ', '.join(
        f'{word!r}: parse_{plan.name}' for word, plan in commands.items())
    lines = [f'_USAGE_command_set = {usage!r}',
             f'_USAGE_LINE_command_set = {usage_line!r}', '']
    listing_stmt = (f"print(render_help_page(_USAGE_LINE_command_set, "
                    f"{corpus_name}, {templates_name}), end='')")
    if auto_help:
        table += ", 'help': parse_help"
        lines.extend([
            'def parse_help(argv):',
            "    # `help` alone: the command listing; `help CMD`: CMD's --help",
            "    if argv and argv[0] != 'help':",
            '        parse = _COMMANDS.get(argv[0])',
            '        if parse is None:',
            '            raise UsageError(f"unknown command {argv[0]!r}", _USAGE_command_set)',
            "        return parse(['--help'])",
            f"    {listing_stmt}",
            '',
            ])
    body = ['def parse_command_set(argv):']
    if auto_help:
        body.extend([
            "    if argv and argv[0] in ('-h', '--help'):",
            f"        {listing_stmt}",
            '        return',
            ])
    body.append(f'    return run_command_set(argv, {globals_name}, '
                f'_COMMANDS, _USAGE_command_set)')
    lines.extend([f'_COMMANDS = {{{table}}}', ''])
    lines.extend(body)
    chunks.append('\n'.join(lines) + '\n')
    return '\n\n'.join(chunks), refs


def compile_command_set(commands, global_plan=None, prog=None, templates=None):
    """
    In-process mode for a multi-command program.  Returns the
    dispatching parse function.
    """
    source, refs = emit_command_set(commands, global_plan, prog, templates)
    filename = '<appeal generated: command set>'
    linecache.cache[filename] = (
        len(source), None, source.splitlines(keepends=True), filename)
    namespace = {
        'parse_tokens': parse_tokens,
        'convert': convert,
        'accumulate': accumulate,
        'collect_mapping': collect_mapping,
        'fold': fold,
        'call_converter': call_converter,
        'window_options': window_options,
        'render_help_page': render_help_page,
        'check_count': check_count,
        'run_command_set': run_command_set,
        'UsageError': UsageError,
        }
    namespace.update(refs.objects)
    code = compile(source, filename, 'exec')
    exec(code, namespace)
    parse = namespace['parse_command_set']
    parse.source = source
    return parse


def render_ref(name, obj):
    recipe = getattr(obj, '__appeal_recipe__', None)
    if recipe:
        # a vocabulary product (split(':'), counter(), ...): the
        # standalone script re-runs the factory; the vocabulary
        # travels with the streamed runtime
        return f'{name} = {recipe}'
    return _render_ref(name, obj)


def _render_ref(name, obj):
    """
    Standalone mode: render one ref as Python source--an import
    line or a literal assignment.  Raises AppealConfigurationError,
    naming the offender, for anything unrenderable.  This refusal
    is the north star\'s teeth.
    """
    # literals round-trip through repr
    if obj is None or isinstance(obj, (bool, int, float, str, bytes)):
        return f'{name} = {obj!r}'
    if isinstance(obj, (tuple, list, dict, set, frozenset)):
        try:
            from ast import literal_eval
            if literal_eval(repr(obj)) == obj:
                return f'{name} = {obj!r}'
        except (ValueError, SyntaxError):
            pass
        raise AppealConfigurationError(
            f"can't emit a standalone script: default value {obj!r} "
            f"doesn't round-trip through repr()")

    # callables import from the user\'s module
    module = getattr(obj, '__module__', None)
    qualname = getattr(obj, '__qualname__', None)
    if callable(obj) and module and qualname:
        if module == '__main__':
            raise AppealConfigurationError(
                f"can't emit a standalone script: {qualname!r} is defined "
                f"in __main__, so the generated script can't import it; "
                f"move it into an importable module")
        if ('<' in qualname) or ('.' in qualname):
            raise AppealConfigurationError(
                f"can't emit a standalone script: {module}.{qualname} "
                f"isn't importable by name (lambdas, closures, and nested "
                f"callables can't be imported)")
        real_name = qualname
        if real_name == name:
            return f'from {module} import {real_name}'
        return f'from {module} import {real_name} as {name}'

    raise AppealConfigurationError(
        f"can't emit a standalone script: don't know how to render "
        f"{name} = {obj!r}")


# every standalone script gets these; everything else is plucked
# only if the generated parser actually uses it.  help (and so
# big's word-wrap trio, via requires) is a permanent fixture:
# every Appeal parser supports --help.
_BASE_SNIPPETS = (
    'appeal exceptions',
    'appeal parse tokens',
    'appeal check count',
    'appeal run main',
    'appeal help',
    )

# plumbing the generated source calls by name, and the snippet
# that delivers each.
_SOURCE_SNIPPETS = (
    ('convert', 'appeal convert'),
    ('call_converter', 'appeal call converter'),
    ('accumulate', 'appeal collect list'),
    ('collect_mapping', 'appeal collect mapping'),
    ('fold', 'appeal option protocol'),
    ('window_options', 'appeal windows'),
    ('run_command_set', 'appeal command set'),
    )


def _extract_snippets():
    # standalone emission plucks snippets out of the runtime
    # warehouse with big's own machinery.  the import lives here,
    # not at module top: parsing in-process needs no big at all;
    # only emitting a standalone script does.  (the emitted script
    # itself still imports nothing but the stdlib and your module.)
    try:
        from big.snip import extract_snippets
    except ImportError:
        raise AppealConfigurationError(
            "can't emit a standalone script: standalone emission "
            "requires big (it uses big.snip to assemble the "
            "runtime), and big isn't importable here")
    return extract_snippets


def _needed_snippets(source, refs):
    """
    The names of every warehouse snippet this generated parser
    needs: the base set, the plumbing the source calls, and the
    snippets the refs' objects declare they're delivered by
    (__appeal_snippet__--vocabulary products carry it, next to
    their recipe).  extract_snippets resolves requires from here,
    so this list needn't be transitive.
    """
    needed = set(_BASE_SNIPPETS)
    for name, snippet in _SOURCE_SNIPPETS:
        if re.search(rf'(?<![.\w]){name}\s*\(', source):
            needed.add(snippet)
    for obj in refs.objects.values():
        snippet = getattr(obj, '__appeal_snippet__', None)
        if snippet:
            needed.add(snippet)
    return sorted(needed)


def _standalone_script(source, refs, prog, description, entry):
    """
    Assemble a standalone script around generated parser source.
    Renders every ref *first*, so refusals happen before we commit
    to anything.
    """
    imports = []
    constants = []
    for name, obj in refs.objects.items():
        rendered = render_ref(name, obj)
        if rendered.startswith('from '):
            imports.append(rendered)
        else:
            constants.append(rendered)

    header = (
        f'#!/usr/bin/env python3\n'
        f'#\n'
        f'# {prog} -- {description}\n'
        f'# Generated by Appeal.  Standalone: no dependency on appeal or big.\n'
        f'# Regenerate rather than edit.\n'
        )

    parts = [header]
    parts.append('\n# ---- the appeal runtime, plucked out of appeal/runtime.py (one copy in the world) ----\n')
    parts.append('import enum\nimport operator\nimport sys\n\n')
    parts.append(_extract_snippets()(runtime_source(), *_needed_snippets(source, refs)))
    parts.append('\n# ---- your program ----\n')
    if imports:
        parts.append('\n'.join(imports) + '\n')
    if constants:
        parts.append('\n'.join(constants) + '\n')
    parts.append('\n# ---- generated parser ----\n')
    parts.append(source)
    parts.append(
        f'\nif __name__ == "__main__":\n'
        f'    sys.exit(run_main({entry}))\n'
        )
    return '\n'.join(parts)


def emit_standalone(plan, *, argv0=None):
    """
    Standalone mode: the complete text of a dependency-free script
    implementing the plan\'s command-line parsing.  stdlib-only:
    Appeal\'s runtime is embedded (scissors), user callables are
    imported, defaults are literals.
    """
    source, refs = emit(plan)
    prog = argv0 or plan.name
    return _standalone_script(
        source, refs, prog,
        f'command-line parsing for {plan.name!r}',
        f'parse_{plan.name}')


def emit_standalone_command_set(commands, global_plan=None, *, argv0=None, templates=None):
    """
    Standalone mode for a multi-command program: every command\'s
    parser plus the dispatcher, in one dependency-free script.
    """
    prog = argv0 or 'program'
    source, refs = emit_command_set(commands, global_plan, prog, templates)
    return _standalone_script(
        source, refs, prog,
        f'command-line parsing ({", ".join(commands)})',
        'parse_command_set')
