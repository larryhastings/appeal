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
import sys

import inspect as _inspect

from .build import (
    all_options, help_option_strings, subtree_option_keys,
    )
from .help import command_set_corpus, merge_docs, summary
from .plan import Terminal, NO_DEFAULT, command_set_usage
from .runtime import (
    AppealConfigurationError, UsageError, Command, absorb_take,
    accumulate, call_converter, collect_mapping, convert,
    convert_value, fold,
    parse_tokens, tokenize, check_count, scoped_forces, scoped_next,
    scoped_resolve, scoped_rewind, scoped_window, scopes_for,
    sibling_scopes,
    did_you_mean,
    greedy_sizes, run_command_set, run_main,
    window_options,
    )
from .render import (
    default_template, help_page_pieces, render_baked_help,
    listing_pieces, render_help_page, help_margin,
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
        self.command_names = {}  # id(command callable) -> _CMD_X global

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
    'mapping', 'fold', 'call_converter', 'convert_value', 'check_count', 'window_options',
    'greedy_sizes', 'UsageError',
    ))


def _inlineable(value):
    """
    True if value is a simple immutable whose repr round-trips as a
    literal, so it's safe to inline instead of binding a named ref--
    there's no identity to preserve (unlike a mutable default, which
    in-process must be the same object every call).  The round-trip
    gate rules out floats like inf/nan (whose repr isn't a literal)
    and containers like frozenset.
    """
    if not isinstance(value, (type(None), bool, int, float, str,
                              bytes, tuple)):
        return False
    try:
        from ast import literal_eval
        return literal_eval(repr(value)) == value
    except (ValueError, SyntaxError):
        return False


def _local(name):
    "Parameters named like the emitter's own locals get a suffix."
    return name + '_' if name in _RESERVED else name


def _ident(name):
    "A command word as a Python identifier (add-item -> add_item)."
    return re.sub(r'\W', '_', name)


class _Emitter:
    def __init__(self, plan, refs=None, fill_names=None, templates=None, stylesheet=None,
                 boundary='saturation', max_columns=79, symbol=None,
                 compiled=False, command_meta=None, is_global=False):
        # refs and fill_names may be shared across the emitters of
        # a command set, so converters common to several commands
        # keep one name (and one rendering) in the combined source.
        # symbol is the base for every emitted top-level name
        # (scan_X, run_X, _USAGE_X, ...): a command set numbers it
        # per plan OBJECT, so two classes each exposing `run` get
        # run_run and run_run2 instead of silently sharing one.
        self.plan = plan
        self.symbol = symbol or _ident(plan.name)
        self.templates = default_template if templates is None else templates
        self.stylesheet = stylesheet
        self.max_columns = max_columns
        self.refs = Refs() if refs is None else refs
        self.lines = []
        self.constants = []           # (name, literal-source) pairs
        self.fill_names = {} if fill_names is None else fill_names
        self.gated = getattr(plan, 'gated', False)
        # how command_words splits this plan's scan: 'saturation'
        # (a cycled command: greedy to the maximum, then the next
        # non-option token is a word) or 'flexible' (a set parent:
        # like the root global, the first operand naming a word
        # once the minimum is met)
        self.boundary = boundary
        # the tree carries trailing arguments: fills thread the
        # shared end-reserve iterator
        self.reserves = getattr(plan, 'tree_trailing', 0) > 0
        # option strings bound by position: fills thread the
        # shared occurrence queues
        self.scoped = frozenset(getattr(plan, 'scoped_keys', ()) or ())
        # sibling option groups (Larry's ruling, 2026-07-18):
        # their shared child keys bind by announcement--the run
        # body builds their scopes (sibling_scopes) and passes
        # them into the group fills; the positional walk's own
        # scopes exclude them
        self.sibling = frozenset(getattr(plan, 'sibling_keys', ()) or ())
        self.sibling_parents = tuple(
            getattr(plan, 'sibling_parents', ()) or ())
        # compiled (precompiled-module) mode bakes NO help/usage
        # text: the usage handed to parse_tokens/check_count/convert
        # is the literal None (the wrapped scan/run tag the error
        # with its command; full Appeal renders it), and -h raises
        # _CompiledHelp instead of printing a baked page.
        self.compiled = compiled
        # the global command hosts config layering (Processor mutates
        # its `given`), the precommand, and global options: it stays on
        # the mature two-pass emitter, never the eager path.
        self.is_global = is_global
        # id(callable) -> (fingerprint, options, arguments), baked into
        # the Command constructor in a compiled module (empty in-process)
        self.command_meta = command_meta or {}
        self.usage_const = ('None' if compiled
                            else f'_USAGE_{self.symbol}')

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

    def default_expr(self, name, value):
        """
        A parameter's default value as source.  A simple immutable
        (str/int/bool/None/tuple-of-those) round-trips through repr
        and has no identity to preserve, so it inlines--`units = 'C'`,
        not a `_units_default` global used once.  Anything else (a
        mutable, an object) keeps a bound ref: in-process it must be
        the SAME object every call.
        """
        if _inlineable(value):
            return repr(value)
        return self.refs.add(f'_{name}_default', value, dedupe=False)

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

    def fill_ref(self, child, dry=False):
        name = self.fill_names[id(child)]
        return ('_scan' + name) if dry else name

    def emit_slots(self, plan, indent, dry=False):
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
                    self.line(f'{pad}_take = remaining - {m}')
                    if not dry:
                        conv = self.converter_expr(slot.child)
                        self.line(f'{pad}{_local(slot.name)} = tuple(convert({conv}, _x, '
                                  f'{slot.name!r}, {self.usage_const}) '
                                  f'for _x in operands[i:i + _take])')
                    self.line(f'{pad}i += _take')
                    self.line(f'{pad}remaining -= _take')
                    continue
                # a converter group on *args: greedily-sized
                # instances (v1's fill, ruled 2026-08-05),
                # options bound to instances by window
                child = slot.child
                k_min, k_max = child.minimum, child.maximum
                fill = self.fill_names[id(child)]
                occ = ', '.join(f'{o.key!r}: given.get({o.key!r}, ())'
                                for o in child.options)
                each = (f'{k_min}' if k_min == k_max
                        else f'{k_min} to {k_max}')
                self.line(f'{pad}# {slot.name}: zero or more {child.name!r} '
                          f'groups, {each} operand'
                          f'{"s" if k_max != 1 else ""} each (greedy)')
                self.line(f'{pad}_take = remaining - {m}')
                self.line(f'{pad}_sizes = greedy_sizes(_take, {k_min}, '
                          f'{k_max}, {slot.name!r}, {self.usage_const})')
                gate_arg = ', gate=gate' if self.gated else ''
                extra = ', gate, positions' if self.gated else ''
                if dry:
                    # the window binding is structural: it raises for
                    # too-early and no-instance occurrences
                    self.line(f'{pad}window_options({{{occ}}}, i, _sizes, '
                              f'{slot.name!r}, {self.usage_const}{gate_arg})')
                    self.line(f'{pad}i += _take')
                    self.line(f'{pad}remaining -= _take')
                    continue
                self.line(f'{pad}_givens = window_options({{{occ}}}, i, _sizes, '
                          f'{slot.name!r}, {self.usage_const}{gate_arg})')
                self.line(f'{pad}{_local(slot.name)} = []')
                self.line(f'{pad}for _j, _size in enumerate(_sizes):')
                self.line(f'{pad}    _item, i = {fill}(operands, i, _size, _givens[_j]{extra})')
                self.line(f'{pad}    {_local(slot.name)}.append(_item)')
                self.line(f'{pad}remaining -= _take')
                continue

            options = slot.count_options

            if options is None:
                # an absorbing slot: its converter contains *args--
                # it takes what the slots after it don't need
                counts, minimum = slot.suffix_after
                counts_lit = ('None' if counts is None else
                              '{' + ', '.join(str(x)
                                              for x in sorted(counts)) + '}')
                floor = slot.child.body_minimum
                fill = self.fill_ref(slot.child, dry)
                extra = ', gate, positions' if self.gated else ''
                if self.reserves and not dry:
                    extra += ', reserved'
                if self.scoped:
                    extra += ', scopes'
                self.line(f'{pad}# {slot.name}: absorbs (its converter '
                          f'contains *args)--the most')
                self.line(f'{pad}# the slots after it can spare')
                self.line(f'{pad}_take = absorb_take(remaining, {counts_lit}, '
                          f'{minimum}, {floor}, {not slot.required!r})')
                if slot.required:
                    if dry:
                        self.line(f'{pad}i = {fill}(operands, i, _take, '
                                  f'given{extra})')
                    else:
                        self.line(f'{pad}{_local(slot.name)}, i = '
                                  f'{fill}(operands, i, _take, given{extra})')
                    self.line(f'{pad}remaining -= _take')
                else:
                    keys = subtree_option_keys(slot.child)
                    self.line(f'{pad}if _take:')
                    if dry:
                        self.line(f'{pad}    i = {fill}(operands, i, _take, '
                                  f'given{extra})')
                    else:
                        self.line(f'{pad}    {_local(slot.name)}, i = '
                                  f'{fill}(operands, i, _take, given{extra})')
                    self.line(f'{pad}    remaining -= _take')
                    if keys and slot.child.body_minimum == 0:
                        self.line(f'{pad}elif '
                                  f'{self.forcing_condition(keys)}:')
                        self.line(f'{pad}    # an option of the group forces '
                                  f'it, zero arguments')
                        if dry:
                            self.line(f'{pad}    i = {fill}(operands, i, 0, '
                                      f'given{extra})')
                        else:
                            self.line(f'{pad}    {_local(slot.name)}, i = '
                                      f'{fill}(operands, i, 0, given{extra})')
                        self.line(f'{pad}else:')
                    else:
                        self.line(f'{pad}else:')
                    if dry:
                        self.line(f'{pad}    pass')
                    else:
                        default_name = self.default_expr(slot.name, slot.default)
                        self.line(f'{pad}    {_local(slot.name)} = '
                                  f'{default_name}')
                if self.gated and slot.barrier:
                    self.line(f'{pad}gate = i  # {slot.name!r} is fed; '
                              f'options behind it are now legal')
                continue

            def emit_take(c, pad2):
                if c == 0 and not slot.required:
                    child = slot.child
                    if not dry:
                        default_name = self.default_expr(slot.name, slot.default)
                    if isinstance(child, Terminal) or not subtree_option_keys(child):
                        self.line(f'{pad2}{_local(slot.name)} = {default_name}'
                                  if not dry else f'{pad2}pass')
                        return
                    keys = subtree_option_keys(child)
                    if child.body_minimum == 0:
                        # v1 semantics: giving one of the group's
                        # options forces it, defaults and all--a
                        # scoped option forces per the interval
                        # model, and the forced window claims it
                        ns = [k for k in keys if k not in self.scoped]
                        sc = [k for k in keys if k in self.scoped]
                        fill = self.fill_ref(child, dry)
                        extra = ', gate, positions' if self.gated else ''
                        if self.reserves and not dry:
                            extra += ', reserved'
                        conds = []
                        if ns:
                            conds.append('given.keys() & {%s}' % ', '.join(
                                repr(k) for k in ns))
                        if self.scoped:
                            self.line(f'{pad2}_force = None')
                            for k in sc:
                                self.line(f'{pad2}if _force is None and '
                                          f'scoped_forces(scopes, '
                                          f'({k!r},)):')
                                self.line(f'{pad2}    _force = {k!r}')
                            extra += ', scopes, _force'
                            conds.append('_force is not None')
                        condition = ' or '.join(conds) or 'False'
                        self.line(f'{pad2}if {condition}:')
                        self.line(f'{pad2}    # an option of {child.name!r}: the group is')
                        self.line(f'{pad2}    # forced, entered with zero operands')
                        if dry:
                            self.line(f'{pad2}    i = {fill}(operands, i, 0, given{extra})')
                        else:
                            self.line(f'{pad2}    {_local(slot.name)}, i = {fill}(operands, i, 0, given{extra})')
                            self.line(f'{pad2}else:')
                            self.line(f'{pad2}    {_local(slot.name)} = {default_name}')
                        return
                    # the group needs operands it can't have; its
                    # options are therefore errors
                    for key in keys:
                        if key in self.scoped:
                            self.line(f'{pad2}if scoped_forces(scopes, '
                                      f'({key!r},)):')
                        else:
                            self.line(f'{pad2}if {key!r} in given:')
                        self.line(f"{pad2}    raise UsageError(f\"option {key} "
                                  f"requires {slot.name!r}\", {self.usage_const})")
                    if not dry:
                        self.line(f'{pad2}{_local(slot.name)} = {default_name}')
                    return
                if isinstance(slot.child, Terminal):
                    if dry:
                        self.line(f'{pad2}i += 1')
                        self.line(f'{pad2}remaining -= 1')
                        return
                    conv = self.converter_expr(slot.child)
                    self.line(f'{pad2}{_local(slot.name)} = convert({conv}, operands[i], '
                              f'{slot.name!r}, {self.usage_const})')
                    self.line(f'{pad2}i += 1')
                    self.line(f'{pad2}remaining -= 1')
                    return
                fill = self.fill_ref(slot.child, dry)
                extra = ', gate, positions' if self.gated else ''
                if self.reserves and not dry:
                    extra += ', reserved'
                if self.scoped:
                    extra += ', scopes'
                if dry:
                    self.line(f'{pad2}i = {fill}(operands, i, {c}, given{extra})')
                else:
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

    def emit_options(self, plan, indent, legality=True):
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
                source = '_overlay' if key in self.scoped else 'given'
                if o.kind == 'flag':
                    self.line(f'{pad}if {key!r} in {source}:')
                    self.line(f'{pad}    {target} = {source}[{key!r}]')
                    continue
                expr = self.option_value_expr(o, key, source=source)
                self.line(f'{pad}if {key!r} in {source}:')
                self.line(f'{pad}    {target} = {expr}')
                continue
            first = o.name not in seen
            seen.add(o.name)
            scoped_key = key in self.scoped
            siblings = [r.key for r in plan.options if r.name == o.name]
            if first and len(siblings) > 1 and not scoped_key:
                # several rules share this parameter: the one whose
                # string spoke LAST on the command line wins (ruled
                # 2026-07-09; `given` is kept in last-occurrence
                # order for exactly this).  Each rule below guards
                # on being the winner.
                sel = f'_sel_{_ident(o.name)}'
                keys_literal = ', '.join(repr(k) for k in siblings)
                self.line(f'{pad}{sel} = None')
                self.line(f'{pad}for _key in given:')
                self.line(f'{pad}    if _key in ({keys_literal},):')
                self.line(f'{pad}        {sel} = _key')
            if legality and o.child is not None and not scoped_key:
                # an absent option group: its inner options have
                # nothing to attach to (sibling keys excepted:
                # another window may claim them, or they summon)
                for inner in subtree_option_keys(o.child):
                    if inner in self.sibling:
                        continue
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
                    absent = self.default_expr(o.name, o.default)
                source = '_overlay' if scoped_key else 'given'
                self.line(f'{pad}{_local(o.name)} = '
                          f'{source}.get({key!r}, {absent})')
                continue
            source = '_overlay' if scoped_key else 'given'
            expr = (f'{source}[{key!r}]' if o.kind == 'flag'
                    else self.option_value_expr(o, key, source=source))
            if first:
                # several rules may share a parameter (per-
                # declaration @app.option): the first initializes
                # the default, the winning rule overwrites
                default_name = self.default_expr(o.name, o.default)
                self.line(f'{pad}{_local(o.name)} = {default_name}')
            if len(siblings) > 1 and not scoped_key:
                sel = f'_sel_{_ident(o.name)}'
                self.line(f'{pad}if {sel} == {key!r}:')
            else:
                self.line(f'{pad}if {key!r} in {source}:')
            self.line(f'{pad}    {_local(o.name)} = {expr}')
            if (self.sibling_parents
                    and key == self.sibling_parents[0][0]):
                # the first declared sibling: child options with
                # no announcement summon it (Larry's ruling,
                # 2026-07-18)--zero operands, defaults throughout
                fill = self.fill_names[id(o.child)]
                self.line(f'{pad}elif _sib_summon == {key!r}:')
                self.line(f'{pad}    {_local(o.name)} = '
                          f'{fill}([], 0, 0, given, scopes=_sib)[0]')

    def emit_group_count_check(self, o, key, pad, guard=False):
        """
        A group option's operands were consumed greedily, up to the
        group's maximum; the count consumed must still be one the
        group can accept.  guard wraps the check in "if given"
        (scan-time: the option may be absent).
        """
        if o.kind != 'group':
            return   # pragma: no cover -- the caller pre-filters
        child = o.child
        counts = ('{' + ', '.join(str(n) for n in sorted(child.valid_counts))
                  + '}' if child.valid_counts is not None else 'None')
        if guard:
            self.line(f'{pad}if {key!r} in given:')
            pad = pad + '    '
        self.line(f'{pad}check_count(len(given[{key!r}]), {child.minimum}, '
                  f'{child.maximum}, {counts}, {self.usage_const}, '
                  f'what="option {key}", param={key!r})')

    def forcing_condition(self, keys):
        ns = [k for k in keys if k not in self.scoped]
        sc = [k for k in keys if k in self.scoped]
        conds = []
        if ns:
            conds.append('given.keys() & {%s}' % ', '.join(
                repr(k) for k in ns))
        if sc:
            conds.append('scoped_forces(scopes, (%s))' % ''.join(
                f'{k!r}, ' for k in sc))
        return ' or '.join(conds) or 'False'

    def scoped_own(self, plan):
        return [o for o in plan.options if o.key in self.scoped]

    def scoped_specs_literal(self):
        specs = {}
        for owner, o in all_options(self.plan):
            if o.key in self.scoped and o.key not in self.sibling:
                specs[o.key] = (
                    'multi' if o.kind in ('accumulate', 'mapping',
                                          'fold')
                    else 'strict' if o.kind == 'fold1'
                    else 'last')
        return ('{' + ', '.join(f'{k!r}: {v!r}'
                                for k, v in sorted(specs.items()))
                + '}')

    def sibling_specs_literal(self):
        specs = {}
        for owner, o in all_options(self.plan):
            if o.key in self.sibling:
                specs[o.key] = (
                    'multi' if o.kind in ('accumulate', 'mapping',
                                          'fold')
                    else 'strict' if o.kind == 'fold1'
                    else 'last')
        return ('{' + ', '.join(f'{k!r}: {v!r}'
                                for k, v in sorted(specs.items()))
                + '}')

    def scoped_own_literal(self, plan):
        return ('(' + ''.join(f'{o.key!r}, '
                              for o in self.scoped_own(plan)) + ')')

    def emit_scoped_pops(self, plan, pad):
        """
        The live walk's pop, as it exits a window: the plan's own
        scoped options' assigned values, shaped per kind into the
        _overlay the option resolution reads.
        """
        own = self.scoped_own(plan)
        if not own:
            return
        self.line(f'{pad}_overlay = {{}}')
        for o in own:
            self.line(f'{pad}_vals = scoped_next(scopes, {o.key!r})')
            self.line(f'{pad}if _vals:')
            if o.kind in ('accumulate', 'mapping', 'fold'):
                self.line(f'{pad}    _overlay[{o.key!r}] = _vals')
            elif o.kind == 'group':
                child = o.child
                counts = ('{' + ', '.join(
                    str(n) for n in sorted(child.valid_counts)) + '}'
                    if child.valid_counts is not None else 'None')
                self.line(f'{pad}    check_count(len(_vals[0]), '
                          f'{child.minimum}, {child.maximum}, {counts}, '
                          f'{self.usage_const}, what="option {o.key}", '
                          f'param={o.key!r})')
                self.line(f'{pad}    _overlay[{o.key!r}] = _vals[0]')
            else:
                self.line(f'{pad}    _overlay[{o.key!r}] = _vals[0]')

    def option_value_expr(self, o, key, default_name=None, source='given'):
        "The expression converting source[key] per the option's kind."
        if o.kind == 'nullary':
            # a value-producing flag: presence calls the converter
            return f'{self.leaf_expr(o.converters[0])}()'
        if o.kind == 'group':
            # the option's inline operands fill the converter group;
            # inner options resolve from the same `given`.  A
            # sibling parent passes its announcement scopes so the
            # fill pops this window's claimed child options.
            fill = self.fill_names[id(o.child)]
            extra = ', gate, positions' if self.gated else ''
            sib = (', scopes=_sib'
                   if any(key == pk for pk, _ in self.sibling_parents)
                   else '')
            return (f'{fill}(list({source}[{key!r}]), 0, '
                    f'len({source}[{key!r}]), given{extra}{sib})[0]')
        if o.kind == 'value':
            # convert every occurrence (all validated), last wins.
            # `given` holds the occurrence list; the scoped overlay
            # holds a single claimed value, so wrap it as one.
            convs = ', '.join(self.leaf_expr(c) for c in o.converters)
            comma = ',' if len(o.converters) == 1 else ''
            operand = (f'[{source}[{key!r}]]' if source == '_overlay'
                       else f'{source}[{key!r}]')
            return (f'convert_value(({convs}{comma}), {operand}, '
                    f'{o.name!r}, {self.usage_const})')
        if o.kind in ('fold', 'fold1'):
            cls_name = self.leaf_expr(o.converters[0])
            convs = ', '.join(self.leaf_expr(c) for c in o.converters[1:])
            comma = ',' if len(o.converters) == 2 else ''
            occurrences = (f'{source}[{key!r}]' if o.kind == 'fold'
                           else f'({source}[{key!r}],)')
            if default_name is None:
                default_name = self.default_expr(o.name, o.default)
            return (f'fold({cls_name}, ({convs}{comma}), {occurrences}, '
                    f'{default_name}, {o.name!r}, {self.usage_const})')
        if o.kind == 'accumulate':
            conv = self.leaf_expr(o.converters[0])
            return (f'accumulate({conv}, {source}[{key!r}], '
                    f'{o.name!r}, {self.usage_const})')
        kconv = self.leaf_expr(o.converters[0])
        vconv = self.leaf_expr(o.converters[1])
        return (f'collect_mapping({kconv}, {vconv}, {source}[{key!r}], '
                f'{o.name!r}, {self.usage_const})')

    def emit_fill_function(self, plan, dry=False):
        fname = self.fill_ref(plan, dry)
        params = ', '.join(s.name for s in plan.slots)
        extra = ', gate, positions' if self.gated else ''
        if self.reserves and not dry:
            extra += ', reserved=None'
        if self.scoped:
            extra += ', scopes=None, force_key=None'
        self.line(f'def {fname}(operands, i, remaining, given{extra}):')
        own_scoped = [o.key for o in self.scoped_own(plan)]
        if self.scoped and dry:
            keys = self.scoped_own_literal(plan)
            self.line(f"    scoped_window(scopes, {keys}, 'in', i, "
                      f"force_key)")
        if (self.gated and plan.options
                and not getattr(plan, 'certain', True)
                and not getattr(plan, 'windowed', False)):
            # the gate rule: this group's options may not appear
            # before every required group to its left was fed--and
            # a scoped option's placement is the interval model's
            # business, not the gate's
            for o in plan.options:
                if o.key in self.scoped:
                    continue
                self.line(f'    if {o.key!r} in given and '
                          f'positions.get({o.key!r}, 0) < gate:')
                self.line(f'        raise UsageError(f"option {o.key} '
                          f'appears too early on the command line", '
                          f'{self.usage_const})')
        if dry:
            # structure only: every decision and structural error
            # of the real fill, none of its conversions or calls
            self.line(f'    # the structural half of {self.fill_names[id(plan)]}')
            self.emit_slots(plan, 4, dry=True)
            if self.scoped:
                keys = self.scoped_own_literal(plan)
                self.line(f"    scoped_window(scopes, {keys}, 'out', i)")
            self.line(f'    return i')
            self.line()
            return
        self.line(f'    # reads exactly `remaining` arguments and builds')
        if plan.callable in (tuple, list):
            self.line(f'    # the {plan.callable.__name__} ({params})')
        else:
            self.line(f'    # {plan.name}({params})')
        self.emit_slots(plan, 4)
        for slot in plan.slots:
            if slot.trailing:
                # this converter's own trailing arguments, from the
                # shared end-reserve, in traversal order
                conv = self.converter_expr(slot.child)
                self.line(f'    {_local(slot.name)} = convert({conv}, '
                          f'next(reserved), {slot.name!r}, '
                          f'{self.usage_const})')
        if self.scoped:
            self.emit_scoped_pops(plan, '    ')
        self.emit_options(plan, 4)
        args = [(f'*{_local(s.name)}' if s.repeat else _local(s.name))
                for s in plan.slots if not s.trailing]
        args.extend(f'{s.name}={_local(s.name)}'
                    for s in plan.slots if s.trailing)
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

    # ---- the eager path (stage-2 walk) ----

    _EAGER_KINDS = frozenset(('flag', 'nullary', 'value', 'accumulate'))

    def _eager_ok(self, plan, command_split):
        """
        True when this plan has the common shape the eager stage-2
        walk handles: no positional option machinery, no converter
        groups, no trailing operands, no precommand/class wrapping.
        Richer plans fall through to the mature two-pass emitter.
        """
        if self.is_global:
            return False
        if (self.gated or self.scoped or self.sibling
                or self.sibling_parents or self.reserves
                or getattr(self, 'need_dry', False)):
            return False
        if plan.pre_plan is not None:
            return False
        c = plan.callable
        if (getattr(c, 'appeal_precommand', False)
                or getattr(c, 'appeal_stock', False)
                or plan.binds is not None or plan.constructs is not None):
            return False
        # operands: every slot a plain leaf or a bare *args (repeat
        # Terminal)--no converter groups, no absorbing slots, no
        # trailing
        for slot in plan.slots:
            if slot.trailing:
                return False
            if not isinstance(slot.child, Terminal):
                return False
            if not slot.repeat and slot.count_options is None:
                return False
        # options: only the flat kinds, one rule per parameter, no
        # **kwargs delivery, value converters simple leaves
        seen = set()
        for o in plan.options:
            if o.kwargs_delivered or o.child is not None:
                return False
            if o.kind not in self._EAGER_KINDS:
                return False
            if o.kind == 'value' and len(o.converters) != 1:
                return False
            if o.name in seen:
                return False
            seen.add(o.name)
        return True

    def _emit_eager(self, plan, command_split, options_const, help_keys,
                    cmd_obj, command_name):
        usage = self.usage_const

        # ---- stage 1: scan yields the IR (no user code) ----
        self.line(f'def scan_{self.symbol}(argv, command_words=None):')
        if command_split is not None:
            self.line(f'    tokens, rest = tokenize(argv, {options_const}, '
                      f'{usage}, command_split={command_split!r})')
        elif self.boundary == 'saturation' and plan.maximum is None:
            self.line(f'    tokens = tokenize(argv, {options_const}, {usage})')
            self.line(f'    rest = []')
        else:
            if self.boundary == 'saturation':
                split = f'({plan.maximum}, {plan.maximum}, command_words)'
            else:
                maximum = 'None' if plan.maximum is None else str(plan.maximum)
                split = f'({plan.minimum}, {maximum}, command_words)'
            self.line(f'    if command_words is None:')
            self.line(f'        tokens = tokenize(argv, {options_const}, '
                      f'{usage})')
            self.line(f'        rest = []')
            self.line(f'    else:')
            self.line(f'        tokens, rest = tokenize(argv, '
                      f'{options_const}, {usage}, command_split={split})')
        self.line(f"    operands = [_s for _t in tokens if _t[0] == '' "
                  f"for _s in _t[1:]]")
        if help_keys:
            self.line(f"    if any(_t[0] == '--help' for _t in tokens):")
            self.line(f'        # help outranks a malformed line '
                      f'(pinned order)')
            self.line(f'        return operands, tokens, rest, None')
        if plan.valid_counts is None:
            self.line(f'    check_count(len(operands), {plan.minimum}, '
                      f'None, None, {usage})')
        else:
            self.line(f'    check_count(len(operands), {plan.minimum}, '
                      f'{plan.maximum}, {set(sorted(plan.valid_counts))!r}, '
                      f'{usage})')
        self.line(f'    return operands, tokens, rest, None')
        self.line()

        # ---- stage 2: run walks the IR, converting on sight ----
        self.line(f'def run_{self.symbol}(operands, tokens, positions=None, '
                  f'env=None, command=None):')
        if help_keys and self.compiled:
            self.line(f"    if any(_t[0] == '--help' for _t in tokens):")
            self.line(f'        raise _CompiledHelp(command)')
        elif help_keys:
            corpus = merge_docs(plan)
            pieces = help_page_pieces(plan.usage(), corpus, self.templates)
            pieces_name = self.refs.add(f'_HELP_{self.symbol}', pieces,
                                        dedupe=False)
            sheet_name = self.refs.add(f'_SHEET_{self.symbol}',
                                       self.stylesheet, dedupe=False)
            self.line(f"    if any(_t[0] == '--help' for _t in tokens):")
            self.line(f'        print(render_baked_help({pieces_name}, '
                      f'margin=help_margin({self.max_columns!r}), '
                      f'file=sys.stdout, '
                      f"stylesheet={sheet_name}), end='')")
            self.line(f'        return')

        # initialize each option's parameter to its default; the
        # collectors (accumulate) also need a running list + a
        # seen-flag, since absence means the default, not [].
        accum = []
        for o in plan.options:
            self.line(f'    {_local(o.name)} = '
                      f'{self.default_expr(o.name, o.default)}')
            if o.kind == 'accumulate':
                self.line(f'    _acc_{_ident(o.name)} = []')
                self.line(f'    _seen_{_ident(o.name)} = False')
                accum.append(o)

        if plan.options:
            self.line(f'    for _t in tokens:')
            self.line(f'        _k = _t[0]')
            first = True
            for o in plan.options:
                head = 'if' if first else 'elif'
                first = False
                self.line(f'        {head} _k == {o.key!r}:')
                self._emit_eager_option(o, usage)
        for o in accum:
            self.line(f'    if _seen_{_ident(o.name)}:')
            self.line(f'        {_local(o.name)} = _acc_{_ident(o.name)}')

        # ---- operands: gather done; size + convert (the automaton) ----
        self.line(f'    n = len(operands)')
        self.line(f'    i = 0')
        self.line(f'    remaining = n')
        self.emit_slots(plan, 4)

        args = []
        for slot in plan.slots:
            if slot.repeat:
                args.append(f'*{_local(slot.name)}')
            else:
                args.append(_local(slot.name))
        for name in dict.fromkeys(o.name for o in plan.options):
            args.append(f'{name}={_local(name)}')
        self.line(f'    return {command_name}({", ".join(args)})')
        self.line()

        # ---- the shared tail: the Command + the fused parse_X ----
        callable_kw = ('' if self.cmd_callable_ref is None
                       else f', callable={self.cmd_callable_ref}')
        meta = self.command_meta.get(id(plan.callable))
        meta_kw = ''
        if meta is not None:
            fp, opts, args_ = meta
            meta_kw = (f', fingerprint={fp!r}, options={opts!r}'
                       f', arguments={args_!r}')
        self.line(f'{cmd_obj} = Command({plan.name!r}{callable_kw}, '
                  f'scan=scan_{self.symbol}, run=run_{self.symbol}'
                  f'{meta_kw})')
        self.line(f'def parse_{self.symbol}(argv):')
        self.line(f'    operands, tokens, rest, positions = '
                  f'scan_{self.symbol}(argv)')
        if command_split is None:
            self.line(f'    return run_{self.symbol}(operands, tokens, '
                      f'positions, command={cmd_obj})')
        else:
            self.line(f'    return run_{self.symbol}(operands, tokens, '
                      f'positions, command={cmd_obj}), rest')
        self.line()

    def _emit_eager_option(self, o, usage):
        "One option's on-sight conversion in the stage-2 walk."
        local = _local(o.name)
        if o.kind == 'flag':
            # presence stores the flag's value (v1's `not default`);
            # an explicit --flag=true/false, carried by tokenize as
            # the literal, overrides
            self.line(f"            {local} = (_t[1] == 'true') "
                      f'if len(_t) > 1 else {o.present!r}')
        elif o.kind == 'nullary':
            conv = self.leaf_expr(o.converters[0])
            self.line(f'            {local} = {conv}()')
        elif o.kind == 'value':
            conv = self.leaf_expr(o.converters[0])
            # every occurrence converts (validate-all); last wins
            self.line(f'            {local} = convert({conv}, _t[1], '
                      f'{o.name!r}, {usage})')
        else:   # accumulate
            conv = self.leaf_expr(o.converters[0])
            self.line(f'            _acc_{_ident(o.name)}.append('
                      f'convert({conv}, _t[1], {o.name!r}, {usage}))')
            self.line(f'            _seen_{_ident(o.name)} = True')

    def emit_parse_function(self, command_split=None):
        plan = self.plan
        fname = f'parse_{self.symbol}'
        options_const = f'_OPTIONS_{self.symbol}'

        table_items = []
        for owner, o in all_options(plan):
            sibling = o.key in self.sibling
            marked = (not sibling
                      and (getattr(owner, 'windowed', False)
                           or o.key in self.scoped))
            for s in o.strings:
                entry = o.table_entry(windowed=marked)
                if sibling:
                    # sibling-group keys record by announcement
                    # seq, not operand position: the 's:' kinds
                    entry = (entry[0], 's:' + entry[1]) + entry[2:]
                table_items.append(f'{s!r}: {entry!r}')
        if plan.pre_plan is not None:
            # the precommand's options scan in the same era
            for owner, o in all_options(plan.pre_plan):
                for s in o.strings:
                    table_items.append(f'{s!r}: {o.table_entry()!r}')
        help_keys = help_option_strings(plan) if command_split is None else ()
        for s in help_keys:
            table_items.append(f"{s!r}: ('--help', 'flag')")
        if not self.compiled:
            # compiled mode: usage_const is the literal None (nothing
            # baked); errors carry the command, not the usage text
            self.constant(self.usage_const, repr(plan.usage()))
        self.constant(options_const, '{%s}' % ', '.join(table_items))

        # every emitted command gets a Command object (the shared
        # dispatch record): _CMD_X.  It is the single stable
        # indirection for the live function--in-process its callable
        # is bound at construction (a ref); in a compiled module the
        # shim fills _CMD_X.callable at registration, and everything
        # (the run body, the error wrappers, the dispatch table) that
        # holds _CMD_X sees it with no re-lookup.
        cmd_obj = f'_CMD_{self.symbol}'
        self.cmd_obj = cmd_obj
        if plan.binds is not None and plan.constructs is not None:
            # a nested class constructs via the parent instance's
            # attribute: no direct callable
            command_name = None
            self.cmd_callable_ref = None
        elif getattr(plan.callable, 'appeal_stock', False):
            # the stock precommand hosting the global plan: its
            # behavior bakes as literals, no callable to carry
            command_name = None
            self.cmd_callable_ref = None
        else:
            # calls go THROUGH the Command the dispatcher passes in
            # (run's `command` param)--live, no per-command global ref
            command_name = 'command.callable'
            self.cmd_callable_ref = (
                None if self.compiled
                else self.refs.add('_' + plan.name.lstrip('_'),
                                   plan.callable))
        # remember the Command's global name for this plan, so a
        # compiled module's spec can point the shim's binding at it
        self.refs.command_names[id(plan.callable)] = cmd_obj
        trailing = [s for s in plan.slots if s.trailing]

        # the two-stage eager path (Larry's design): for the common
        # plan shape--flat options, plain-leaf and *args operands, no
        # positional option machinery--scan yields the IR and run
        # walks it, converting options ON SIGHT (last-wins & validate-
        # all fall out of stream order; no `given`, no convert_value).
        # Everything richer (gates, windows, scoped/sibling options,
        # converter groups, trailing operands, precommand, classes)
        # still rides the mature two-pass emitter below.  Both feed the
        # SAME positional scan/run contract, so the dispatcher--which
        # never inspects the handoff--carries a mix without knowing.
        if self._eager_ok(plan, command_split):
            self._emit_eager(plan, command_split, options_const,
                             help_keys, cmd_obj, command_name)
            return

        # stage 1: the structural parse--no user code.  A malformed
        # line dies here, before anything runs.
        self.line(f'def scan_{self.symbol}(argv, command_words=None):')
        track_positions = self.gated or bool(self.sibling_parents)
        if track_positions:
            self.line(f'    positions = {{}}')
        gate_kwarg = ', positions=positions' if track_positions else ''
        if command_split is None:
            if self.boundary == 'saturation' and plan.maximum is None:
                self.line(f'    # unbounded arguments: never saturates, so '
                          f'under cycling')
                self.line(f'    # this command is a cycle terminator '
                          f'(command_words moot)')
                self.line(f'    operands, given = parse_tokens(argv, {options_const}, '
                          f'{self.usage_const}{gate_kwarg})')
                self.line(f'    rest = []')
            else:
                if self.boundary == 'saturation':
                    split = (f'({plan.maximum}, {plan.maximum}, '
                             f'command_words)')
                    why = [f'        # cycling: the boundary is greedy saturation--the',
                           f'        # first non-option token after {plan.maximum} '
                           f'argument{"s" if plan.maximum != 1 else ""} is',
                           f'        # the next command word, whatever it looks like']
                else:
                    maximum = ('None' if plan.maximum is None
                               else str(plan.maximum))
                    split = (f'({plan.minimum}, {maximum}, '
                             f'command_words)')
                    why = [f'        # a set parent: its arguments end at the first',
                           f'        # operand naming a subcommand (or at the maximum)']
                self.line(f'    if command_words is None:')
                self.line(f'        operands, given = parse_tokens(argv, {options_const}, '
                          f'{self.usage_const}{gate_kwarg})')
                self.line(f'        rest = []')
                self.line(f'    else:')
                for line in why:
                    self.line(line)
                self.line(f'        operands, given, rest = parse_tokens(argv, {options_const}, '
                          f'{self.usage_const}, command_split={split}{gate_kwarg})')
        else:
            self.line(f'    # the global command: its arguments end at the first')
            self.line(f'    # operand naming a command (or at the maximum)')
            self.line(f'    operands, given, rest = parse_tokens(argv, {options_const}, '
                      f'{self.usage_const}, command_split={command_split!r}{gate_kwarg})')
        positions_expr = 'positions' if track_positions else 'None'
        pre = plan.pre_plan
        if pre is None and getattr(plan.callable, 'appeal_precommand',
                                   False):
            pre = plan      # the precommand IS the global plan
        if pre is not None:
            # the precommand acts at SCAN time, pinned like help:
            # program metadata (-V) outranks a malformed line and
            # the empty-line listing.  The stock behavior bakes as
            # literals (standalone-safe); an overridden precommand
            # family rides as a reference (standalone then refuses
            # by name--the north star's teeth).
            fn = pre.callable
            version_rule = next((o for o in pre.options
                                 if o.name == 'version'), None)
            if version_rule is not None:
                pops = ' or '.join(f'given.pop({s!r}, False)'
                                   for s in version_rule.strings)
                if getattr(fn, 'appeal_stock', False):
                    literal = getattr(fn, 'appeal_version', None)
                    self.line(f'    if {pops}:')
                    self.line(f'        print({literal!r})')
                    self.line(f'        raise SystemExit(0)')
                else:
                    ref = self.refs.add('_precommand', fn)
                    self.line(f'    if {pops}:')
                    self.line(f'        {ref}(version=True)')
            help_rule = next((o for o in pre.options
                              if o.name == 'help'), None)
            if help_rule is not None:
                # the help topic is optional[str]: a group whose
                # given value is the consumed operands--bare -h
                # is ()
                helper = getattr(fn, 'appeal_help', None)
                ref = self.refs.add('_default_help',
                                    helper if helper is not None
                                    else fn)
                self.line(f'    _topic = None')
                for s in help_rule.strings:
                    self.line(f'    if _topic is None and '
                              f'{s!r} in given:')
                    self.line(f'        _topic = list(given.pop({s!r}))')
                self.line(f'    if _topic is not None:')
                self.line(f"        {ref}(_topic[0] if _topic else '')")
                self.line(f'        raise SystemExit(0)')
        if help_keys:
            self.line(f"    if given.get('--help'):")
            self.line(f'        # help outranks a malformed line (pinned order)')
            self.line(f'        return operands, given, rest, {positions_expr}')
        if plan.valid_counts is None:
            self.line(f'    check_count(len(operands), {plan.minimum}, None, None, '
                      f'{self.usage_const})')
        else:
            self.line(f'    check_count(len(operands), {plan.minimum}, {plan.maximum}, '
                      f'{set(sorted(plan.valid_counts))!r}, {self.usage_const})')
        by_owner_name = {}
        for owner, o in all_options(plan):
            if (getattr(owner, 'windowed', False)
                    or o.key in self.scoped):
                # windowed and scoped options bind per window--the
                # positional machinery owns their legality
                continue
            if o.kind == 'group':
                self.emit_group_count_check(o, o.key, '    ',
                                            guard=True)
            if o.child is not None:
                # an absent option group: its inner options have
                # nothing to attach to (sibling keys excepted)
                for inner in subtree_option_keys(o.child):
                    if inner in self.sibling:
                        continue
                    self.line(f'    if {inner!r} in given and '
                              f'{o.key!r} not in given:')
                    self.line(f'        raise UsageError(f"option {inner} '
                              f'requires {o.key}", {self.usage_const})')
            by_owner_name.setdefault((id(owner), o.name), []).append(o.key)
        # several rules may share a parameter (per-declaration
        # @app.option): the string that spoke last wins (ruled
        # 2026-07-09)--no scan-stage repetition check anymore
        if self.scoped:
            # the top window is this inline walk (fills open their
            # own)
            self.line(f'    scopes = scopes_for('
                      f'{self.scoped_specs_literal()}, given)')
            self.line(f"    scoped_window(scopes, "
                      f"{self.scoped_own_literal(plan)}, 'in', 0)")
        if getattr(self, 'need_dry', False):
            self.line(f'    # the structural walk: the gate rule and')
            self.line(f'    # window binding raise here, before anything runs')
            self.line(f'    i = 0')
            if plan.tree_trailing:
                self.line(f'    remaining = len(operands) - '
                          f'{plan.tree_trailing}')
            else:
                self.line(f'    remaining = len(operands)')
            if self.gated:
                self.line(f'    gate = 0')
            self.emit_slots(plan, 4, dry=True)
        if self.scoped:
            self.line(f"    scoped_window(scopes, "
                      f"{self.scoped_own_literal(plan)}, 'out', i)")
            self.line(f'    scoped_resolve(scopes, {self.usage_const})')
        self.line(f'    return operands, given, rest, {positions_expr}')
        self.line()

        # stage 2: build bottom-up and call--the conversions (user
        # code) and the command itself
        self.line(f'def run_{self.symbol}(operands, given, positions=None, '
                  f'env=None, command=None):')
        if help_keys and self.compiled:
            # a precompiled module bakes no page: -h raises the help
            # signal tagged with the Command the dispatcher passed in
            # (its callable filled live by the shim); full Appeal
            # renders the page
            self.line(f"    if given.pop('--help', False):")
            self.line(f'        raise _CompiledHelp(command)')
        elif help_keys:
            # compiled means the documentation too: the whole
            # Markdown pipeline runs at BUILD time (parse, style,
            # layout--big's half), and the page bakes as pieces;
            # the script wraps and paints at ITS runtime, at its
            # real width, for its real terminal
            corpus = merge_docs(plan)
            pieces = help_page_pieces(plan.usage(), corpus,
                                      self.templates)
            pieces_name = self.refs.add(f'_HELP_{self.symbol}',
                                        pieces, dedupe=False)
            sheet_name = self.refs.add(f'_SHEET_{self.symbol}', self.stylesheet, dedupe=False)
            self.line(f"    if given.pop('--help', False):")
            self.line(f'        print(render_baked_help({pieces_name}, '
                      f'margin=help_margin({self.max_columns!r}), '
                      f'file=sys.stdout, '
                      f"stylesheet={sheet_name}), end='')")
            self.line(f'        return')
        self.line(f'    n = len(operands)')
        if self.sibling_parents:
            first_key = self.sibling_parents[0][0]
            (first,) = [o for o in plan.options if o.key == first_key]
            self.line(f'    # sibling option groups: bind the shared')
            self.line(f'    # child options by announcement (Larry''s')
            self.line(f'    # ruling, 2026-07-18)')
            self.line(f'    _sib, _sib_summon = sibling_scopes('
                      f'{self.sibling_parents!r}, '
                      f'{self.sibling_specs_literal()}, given, '
                      f'positions or {{}}, {self.usage_const}, '
                      f'summonable={(first.child.minimum == 0)!r})')

        if self.reserves:
            k = plan.tree_trailing
            self.line(f'    # trailing arguments--anywhere in the tree--')
            self.line(f'    # reserve from the end; converters take their')
            self.line(f'    # own in traversal order (= stream order)')
            self.line(f'    reserved = iter(operands[-{k}:])')
            self.line(f'    operands = operands[:-{k}]')
            self.line(f'    n -= {k}')

        self.line(f'    i = 0')
        self.line(f'    remaining = n')
        if self.gated:
            self.line(f'    gate = 0')
        if self.scoped:
            own = self.scoped_own_literal(plan)
            self.line(f'    # the structural walk again, to place the')
            self.line(f'    # scoped options (scan did this too;')
            self.line(f'    # recounting is cheap and self-contained)')
            self.line(f'    scopes = scopes_for('
                      f'{self.scoped_specs_literal()}, given)')
            self.line(f"    scoped_window(scopes, {own}, 'in', 0)")
            if self.gated:
                self.line(f'    gate = 0')
            self.emit_slots(plan, 4, dry=True)
            self.line(f"    scoped_window(scopes, {own}, 'out', i)")
            self.line(f'    scoped_resolve(scopes, {self.usage_const})')
            self.line(f'    scoped_rewind(scopes)')
            self.line(f'    i = 0')
            self.line(f'    remaining = n')
        self.emit_slots(plan, 4)
        for slot in trailing:
            conv = self.converter_expr(slot.child)
            self.line(f'    {_local(slot.name)} = convert({conv}, '
                      f'next(reserved), {slot.name!r}, '
                      f'{self.usage_const})')

        if self.scoped:
            self.emit_scoped_pops(plan, '    ')
        # option legality lives in scan (stage 1); here only
        # resolution
        self.emit_options(plan, 4, legality=False)

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
        call = f'{command_name}({", ".join(args)})'
        if getattr(plan.callable, 'appeal_stock', False):
            # stock precommand-as-global: inline the behavior
            literal = getattr(plan.callable, 'appeal_version', None)
            if any(o.name == 'version' for o in plan.options):
                self.line(f'    if {_local("version")}:')
                self.line(f'        print({literal!r})')
                self.line(f'        raise SystemExit(0)')
            self.line(f'    return None')
        elif plan.binds is not None and plan.constructs is not None:
            # a nested class: constructed via attribute access on
            # the parent instance (bound inner classes compose)
            self.line(f'    _instance = getattr(env[{plan.binds!r}], '
                      f'{plan.name!r})({", ".join(args)})')
            self.line(f'    env[{plan.constructs!r}] = _instance')
            self.line(f'    return _instance')
        elif plan.constructs is not None:
            # calling the class: the instance goes to the
            # environment, never mistaken for an exit status
            self.line(f'    _instance = {call}')
            self.line(f'    if env is not None:')
            self.line(f'        env[{plan.constructs!r}] = _instance')
            self.line(f'    return _instance')
        elif plan.binds is not None:
            # a method command: self is the environment's instance
            self.line(f'    return {command_name}(env[{plan.binds!r}]'
                      f'{", " if args else ""}{", ".join(args)})')
        else:
            self.line(f'    return {call}')
        self.line()

        # the Command object: the shared dispatch record.  In-process
        # its callable is the bound ref; compiled, the shim fills it.
        # Error-tagging lives in the dispatcher now (it holds the
        # Command), so there are no per-command wrappers.  A compiled
        # module bakes the drift fingerprints right into the
        # constructor (converters fold on later--they need ref names).
        callable_kw = ('' if self.cmd_callable_ref is None
                       else f', callable={self.cmd_callable_ref}')
        meta = self.command_meta.get(id(plan.callable))
        meta_kw = ''
        if meta is not None:
            fp, opts, args_ = meta
            meta_kw = (f', fingerprint={fp!r}, options={opts!r}'
                       f', arguments={args_!r}')
        self.line(f'{cmd_obj} = Command({plan.name!r}{callable_kw}, '
                  f'scan=scan_{self.symbol}, run=run_{self.symbol}'
                  f'{meta_kw})')

        # both stages, glued: the fused convenience, handing run its
        # Command (the dispatcher does this for a set; this is the
        # bare-app / default path)
        self.line(f'def {fname}(argv):')
        self.line(f'    operands, given, rest, positions = scan_{self.symbol}(argv)')
        if command_split is None:
            self.line(f'    return run_{self.symbol}(operands, given, '
                      f'positions, command={cmd_obj})')
        else:
            self.line(f'    return run_{self.symbol}(operands, given, '
                      f'positions, command={cmd_obj}), rest')
        self.line()

    def emit_completion_table(self):
        """
        The command's completion table, as a source literal: plain
        data plus converter expressions (imported names, recipes,
        or builtins)--see runtime.complete_command for the shape.
        """
        from .complete import completion_table
        table = completion_table(self.plan)

        def conv_expr(c):
            return 'None' if c is None else self.leaf_expr(c)

        options = ', '.join(f'{s!r}: {entry!r}'
                            for s, entry in table['options'].items())
        values = ', '.join(
            '{!r}: ({}{})'.format(
                key,
                ', '.join(conv_expr(c) for c in converters),
                ',' if len(converters) == 1 else '')
            for key, converters in table['values'].items())
        operands = ', '.join(conv_expr(c) for c in table['operands'])
        comma = ',' if len(table['operands']) == 1 else ''
        src = ("{'options': {%s}, 'help': %r, 'values': {%s}, "
               "'operands': (%s%s), 'repeat': %s, "
               "'minimum': %r, 'maximum': %r}" % (
                   options, table['help'], values,
                   operands, comma, conv_expr(table['repeat']),
                   table['minimum'], table['maximum']))
        # a factory, not a constant: in a compiled module the
        # slot names inside (converters with .completions) bind at
        # REGISTRATION, after module exec--evaluate at use
        self.constants.append(
            f'def _COMPLETE_{self.symbol}():\n    return {src}')

    def emit(self, command_split=None):
        children = []
        self.collect_children(self.plan, children)
        for child in children:
            self.emit_fill_function(child)
        # scan needs the structural walk only when the plan can
        # raise position-dependent errors: the gate rule, windows,
        # or a skipped group whose options would dangle
        self.need_dry = bool(self.gated or self.scoped or any(
            getattr(c, 'windowed', False) or subtree_option_keys(c)
            for c in children))
        if self.need_dry:
            for child in children:
                self.emit_fill_function(child, dry=True)
        self.emit_completion_table()
        self.emit_parse_function(command_split)
        source = '\n'.join(self.constants) + '\n\n' + '\n'.join(self.lines) + '\n'
        return source, self.refs


def emit(plan, templates=None, stylesheet=None, max_columns=79,
         compiled=False, command_meta=None):
    """
    Generate the parser source for a plan.  Returns (source, refs).
    compiled=True mutes all baked help/usage (precompiled modules).
    """
    return _Emitter(plan, templates=templates, stylesheet=stylesheet,
                    max_columns=max_columns, compiled=compiled,
                    command_meta=command_meta).emit()


def compile_plan(plan, command_split=None, templates=None, stylesheet=None,
                 max_columns=79,
                 boundary='saturation', is_global=False):
    """
    In-process mode: exec the generated source, binding the refs
    by reference.  Returns the parse function.  The source is
    registered with linecache, so tracebacks and pdb show the
    generated lines.

    command_split compiles the plan as a global command (see
    parse_tokens): the parse function returns (result, rest).
    """
    source, refs = _Emitter(plan, templates=templates, stylesheet=stylesheet,
                            boundary=boundary, is_global=is_global,
                            max_columns=max_columns).emit(command_split)
    filename = f'<appeal generated: {plan.name}>'
    linecache.cache[filename] = (
        len(source), None, source.splitlines(keepends=True), filename)
    namespace = {
        'parse_tokens': parse_tokens,
        'tokenize': tokenize,
        'convert': convert,
        'accumulate': accumulate,
        'collect_mapping': collect_mapping,
        'fold': fold,
        'call_converter': call_converter,
        'convert_value': convert_value,
        'window_options': window_options,
        'greedy_sizes': greedy_sizes,
        'render_help_page': render_help_page,
        'render_baked_help': render_baked_help,
        'help_margin': help_margin,
        'did_you_mean': did_you_mean,
        'sys': sys,
        'check_count': check_count,
        'absorb_take': absorb_take,
        'scopes_for': scopes_for,
        'sibling_scopes': sibling_scopes,
        'scoped_forces': scoped_forces,
        'scoped_window': scoped_window,
        'scoped_resolve': scoped_resolve,
        'scoped_rewind': scoped_rewind,
        'scoped_next': scoped_next,
        'UsageError': UsageError,
        'Command': Command,
        }
    namespace.update(refs.objects)
    code = compile(source, filename, 'exec')
    exec(code, namespace)
    parse = namespace[f'parse_{_ident(plan.name)}']
    parse.source = source
    # the stages, separately: the Processor drives these
    parse.scan = namespace[f'scan_{_ident(plan.name)}']
    parse.run = namespace[f'run_{_ident(plan.name)}']
    return parse


def emit_command_set(commands, global_plan=None, prog=None, templates=None, stylesheet=None, repeat=False, subs=None, sub_repeat=None, version=None, max_columns=79, help=True, default=None, sub_defaults=None, doc=None, compiled=False, command_meta=None):
    """
    Generate the source for a multi-command program: one parse
    function per command, an optional global-command parse function
    (command mode), and a dispatcher named parse_command_set.
    commands maps command-word -> Plan.  Returns (source, refs).
    help=False suppresses the automatic `help` command (v1's knob).
    default is the root default command's Plan (run when the line
    names no command); sub_defaults maps parent word -> that set's
    default Plan.
    """
    templates = default_template if templates is None else templates
    refs = Refs()
    fill_names = {}
    chunks = []
    auto_help = help and 'help' not in commands
    auto_version = version is not None and 'version' not in commands
    command_words = (frozenset(commands)
                     | ({'help'} if auto_help else set())
                     | ({'version'} if auto_version else set()))
    subs = subs or {}
    sub_repeat = sub_repeat or {}
    sub_defaults = sub_defaults or {}
    # every plan OBJECT gets its own emitted symbol--the base for
    # scan_X/run_X/_SET_X/_COMPLETE_X--numbered on collision, so
    # two classes each exposing `run` emit run_run and run_run2
    # (identity is the plan, never the bare word)
    symbols = {}
    taken = set()
    def sym(plan):
        symbol = symbols.get(id(plan))
        if symbol is None:
            base = symbol = _ident(plan.name)
            counter = 1
            while symbol in taken:
                counter += 1
                symbol = f'{base}{counter}'
            taken.add(symbol)
            symbols[id(plan)] = symbol
        return symbol
    if global_plan is not None:
        # THROUGH the registry, like every reference to it below:
        # a program named for one of its commands (program `serve`,
        # command `serve`) must not emit two _COMPLETE_serve and
        # reference a _COMPLETE_serve2 that exists nowhere.  The
        # global plan emits first, so it keeps the clean name and
        # the same-named command takes the number.
        emitter = _Emitter(global_plan, refs, fill_names,
                           max_columns=max_columns,
                           symbol=sym(global_plan), compiled=compiled,
                           command_meta=command_meta)
        split = (global_plan.minimum, global_plan.maximum, command_words)
        chunks.append(emitter.emit(command_split=split)[0])
    emitted = set()
    def emit_one(plan, boundary='saturation'):
        if id(plan) in emitted:
            return
        emitted.add(id(plan))
        emitter = _Emitter(plan, refs, fill_names, templates=templates,
                           stylesheet=stylesheet, boundary=boundary,
                           max_columns=max_columns, symbol=sym(plan),
                           compiled=compiled, command_meta=command_meta)
        chunks.append(emitter.emit()[0])
    for word, plan in commands.items():
        emit_one(plan, boundary='flexible' if word in subs
                 else 'saturation')
    sub_lines = []
    sub_plan_tables = {}
    # a nested parent's plan lives in ITS parent's children, not
    # in the top-level table
    all_plans = dict(commands)
    for sub_plans in subs.values():
        all_plans.update(sub_plans)
    # children before parents: _SET_child must be defined before
    # _SET_parent's literal references it
    pending = dict(subs)
    ordered = []
    emitted_sets = set()
    while pending:
        progressed = False
        for parent_word in list(pending):
            if all(w not in subs or w in emitted_sets
                   for w in pending[parent_word]):
                ordered.append(parent_word)
                emitted_sets.add(parent_word)
                del pending[parent_word]
                progressed = True
        if not progressed:
            raise AppealConfigurationError(
                f"subcommand sets form a cycle: {sorted(pending)}")
    for parent_word in ordered:
        sub_plans = subs[parent_word]
        parent_plan = all_plans[parent_word]
        sub_plan_tables[parent_word] = sub_plans
        for w, plan in sub_plans.items():
            emit_one(plan, boundary='flexible' if w in subs
                     else 'saturation')
        if compiled:
            # a precompiled module bakes no listing (docstrings and
            # all): full Appeal renders it when the nested set is
            # asked for help
            sub_usage = None
        else:
            sub_entries = [(w, summary(p.callable))
                           for w, p in sub_plans.items()]
            # auto_help=True: the facade's nested listing shows the
            # help row (cycling pops `help` up to the root handler)
            sub_corpus = command_set_corpus(parent_plan, sub_entries,
                                            help)
            sub_usage = listing_pieces(
                command_set_usage(parent_word, parent_plan), sub_corpus,
                templates or default_template)
        # a nested set is just its parent's Command with its children
        # hung on it (word -> child Command, leaf or deeper set).  The
        # Command is the tag target too, so a "no command"/"unknown
        # command" error under it renders that parent's listing live--
        # no lambda, no separate _SET dict.
        sub_table = ', '.join(f'{w!r}: _CMD_{sym(p)}'
                              for w, p in sub_plans.items())
        sub_words = ', '.join(repr(w) for w in sorted(sub_plans))
        d_plan = sub_defaults.get(parent_word)
        if d_plan is not None:
            emit_one(d_plan)
            d_literal = f'_CMD_{sym(d_plan)}'
        else:
            d_literal = 'None'
        p = sym(parent_plan)
        sub_lines.append(
            f"_CMD_{p}.subcommands = {{{sub_table}}}\n"
            f"_CMD_{p}.words = frozenset(({sub_words},))\n"
            f"_CMD_{p}.repeat = {sub_repeat.get(parent_word, False)!r}\n"
            f"_CMD_{p}.usage = {sub_usage!r}\n"
            f"_CMD_{p}.default = {d_literal}")

    if compiled:
        # a precompiled module bakes no set page/listing/usage
        # (docstrings and all): full Appeal renders them live
        usage = usage_line = None
        pieces_name = sheet_name = None
    else:
        entries = [(word, summary(plan.callable)) for word, plan in commands.items()]
        display_global = (None if global_plan is not None
                          and getattr(global_plan.callable,
                                      'appeal_precommand', False)
                          else global_plan)
        usage_line = command_set_usage(prog or 'program', display_global)
        corpus = command_set_corpus(global_plan, entries, auto_help, doc=doc,
                                    auto_version=auto_version)
        usage = listing_pieces(usage_line, corpus, templates)
        page_pieces = help_page_pieces(usage_line, corpus, templates)
        pieces_name = refs.add('_HELP_command_set', page_pieces,
                               dedupe=False)
        sheet_name = refs.add('_SHEET_command_set', stylesheet, dedupe=False)
    globals_name = (f'_CMD_{sym(global_plan)}'
                    if global_plan is not None else 'None')
    # every entry is a Command now--leaf or nested set alike
    table = ', '.join(f'{word!r}: _CMD_{sym(plan)}'
                      for word, plan in commands.items())
    def complete_entry(word, plan):
        # recursive: a child that is itself a parent nests its own
        # {'parent', 'commands', 'repeat'} entry (the completion
        # walker pushes on any such dict, at any depth)
        if word not in subs:
            return f'_COMPLETE_{sym(plan)}()'
        inner = ', '.join(
            f'{w!r}: {complete_entry(w, p)}'
            for w, p in sub_plan_tables[word].items())
        return ("{'parent': _COMPLETE_%s(), 'commands': {%s}, "
                "'repeat': %r}" % (
                    sym(plan), inner,
                    sub_repeat.get(word, False)))
    version_complete = (
        ", 'version': {'options': {}, 'help': (), 'values': {}, "
        "'operands': (), 'repeat': None, 'minimum': 0, 'maximum': 0}"
        if auto_version else '')
    set_table = (
        "{'commands': {%s%s}, 'global': %s, 'minimum': %d, "
        "'auto_help': %r, 'repeat': %r}" % (
            ', '.join(f'{word!r}: {complete_entry(word, plan)}'
                      for word, plan in commands.items()),
            version_complete,
            ("dict(_COMPLETE_%s(), help=())" % sym(global_plan)
             if global_plan is not None else 'None'),
            global_plan.minimum if global_plan is not None else 0,
            auto_help, repeat))
    # the usage argument the dispatcher/errors carry: baked pieces
    # in-process, the literal None in a compiled module (which bakes
    # no usage--the tagged command drives full Appeal instead)
    set_usage = 'None' if compiled else '_USAGE_command_set'
    lines = list(sub_lines)
    if not compiled:
        lines += [f'_USAGE_command_set = {usage!r}',
                  f'_USAGE_LINE_command_set = {usage_line!r}']
    lines += [f'def _COMPLETE_command_set():\n'
              f'    return {set_table}', '']
    # two listing surfaces (matching the in-process facade):
    # set-level --help and bare `help` print the FULL set page;
    # a bare command line prints the TERSE listing--usage and
    # the Commands table, no prose (orientation, not a manual).
    # compiled mode bakes neither: both raise the help signal
    # (tagged root--None) and full Appeal renders the listing.
    if compiled:
        # requested help exits 0; the terse listing a bare line
        # falls through to exits 1 (v1: no command ran)
        listing_stmt = 'raise _CompiledHelp(None, 0)'
        terse_stmt = 'raise _CompiledHelp(None, 1)'
    else:
        listing_stmt = (f"print(render_baked_help({pieces_name}, "
                        f"margin=help_margin({max_columns!r}), "
                        f"file=sys.stdout, "
                        f"stylesheet={sheet_name}), end='')")
        terse_stmt = (f"print(render_baked_help(_USAGE_command_set, "
                      f"margin=help_margin({max_columns!r}), "
                      f"file=sys.stdout, "
                      f"stylesheet={sheet_name}), end='')")
    if auto_version:
        table += ", 'version': Command('version', fused=parse_version)"
        lines.extend([
            f'_VERSION = {str(version)!r}',
            'def parse_version(argv):',
            '    if argv:',
            '        raise UsageError("version takes no arguments", '
            f'{set_usage})',
            '    print(_VERSION)',
            '',
            ])
    if auto_help:
        table += ", 'help': Command('help', fused=parse_help)"
        version_topic = ([
            "    if argv[0] == 'version':",
            '        print("Print the program\'s version.")',
            '        return',
            ] if auto_version else [])
        lines.extend([
            'def parse_help(argv):',
            "    # `help` alone: the listing; `help CMD`: CMD's",
            '    # --help; the auto commands and nested parents',
            '    # describe themselves',
            '    if not argv:',
            f'        {listing_stmt}',
            '        return',
            "    if argv[0] == 'help':",
            "        print('Print usage documentation on a specific command.')",
            '        return',
            ] + version_topic + [
            '    entry = _COMMANDS.get(argv[0])',
            '    if entry is None:',
            '        raise UsageError(f"unknown command {argv[0]!r}"',
            '                         f"{did_you_mean(argv[0], _COMMANDS)}",',
            f'                         {set_usage})',
            '    if entry.subcommands:',
            ] + ([
            '        # a nested set: drive its parent -h, which raises',
            '        # the help signal tagged with the parent command',
            "        operands, given, rest, positions = entry.scan(['--help'])",
            "        return entry.run(operands, given, positions, command=entry)",
            ] if compiled else [
            '        # a nested set: its listing, baked pieces,',
            '        # finished at the real margin',
            f"        print(render_baked_help(entry.usage, "
            f"margin=help_margin({max_columns!r})), end='')",
            '        return',
            ]) + [
            "    operands, given, rest, positions = entry.scan(['--help'])",
            '    return entry.run(operands, given, positions, command=entry)',
            '',
            ])
    lines.extend([
        'def _print_listing():',
        f'    {listing_stmt}',
        '',
        'def _print_terse_listing():',
        f'    {terse_stmt}',
        '',
        ])
    body = ['def parse_command_set(argv):']
    pre_src = None
    if global_plan is not None:
        pre_src = global_plan.pre_plan
        if pre_src is None and getattr(global_plan.callable,
                                       'appeal_precommand', False):
            pre_src = global_plan
    pre_help = (pre_src is not None
                and any(o.name == 'help' for o in pre_src.options))
    if help and not pre_help:
        # gated on help= (the knob), not auto_help: a REAL `help`
        # command owns the word, but -h/--help at the set level
        # still shows the listing.  When the precommand maps the
        # help option, IT handles every form (-h, -h TOPIC)--no
        # first-token shortcut, it would shadow the topic.
        body.extend([
            "    if argv and argv[0] in ('-h', '--help'):",
            '        _print_listing()',
            '        return',
            ])
    if default is not None:
        # the root default command: an empty line runs it.  Its
        # Command (emitted by emit_one) goes straight to
        # run_command_set, which scans+runs it with itself in hand
        emit_one(default)
        default_arg = f'default=_CMD_{sym(default)}, '
    else:
        default_arg = ''
    body.append(f'    return run_command_set(argv, {globals_name}, '
                f'_COMMANDS, {set_usage}, '
                f'{default_arg}'
                f'repeat={repeat!r}, words=_COMMAND_WORDS, '
                f'listing=_print_terse_listing)')
    words_literal = ', '.join(repr(w) for w in sorted(command_words))
    lines.extend([f'_COMMANDS = {{{table}}}',
                  f'_COMMAND_WORDS = frozenset(({words_literal},))', ''])
    lines.extend(body)
    chunks.append('\n'.join(lines) + '\n')
    return '\n\n'.join(chunks), refs


def compile_command_set(commands, global_plan=None, prog=None, templates=None, stylesheet=None, max_columns=79, help=True, default=None, sub_defaults=None):
    """
    In-process mode for a multi-command program.  Returns the
    dispatching parse function.
    """
    source, refs = emit_command_set(commands, global_plan, prog, templates,
                                    stylesheet, max_columns=max_columns, help=help,
                                    default=default,
                                    sub_defaults=sub_defaults)
    filename = '<appeal generated: command set>'
    linecache.cache[filename] = (
        len(source), None, source.splitlines(keepends=True), filename)
    namespace = {
        'parse_tokens': parse_tokens,
        'tokenize': tokenize,
        'convert': convert,
        'accumulate': accumulate,
        'collect_mapping': collect_mapping,
        'fold': fold,
        'call_converter': call_converter,
        'convert_value': convert_value,
        'window_options': window_options,
        'greedy_sizes': greedy_sizes,
        'render_help_page': render_help_page,
        'render_baked_help': render_baked_help,
        'help_margin': help_margin,
        'did_you_mean': did_you_mean,
        'sys': sys,
        'check_count': check_count,
        'run_command_set': run_command_set,
        'UsageError': UsageError,
        'scopes_for': scopes_for,
        'scoped_forces': scoped_forces,
        'scoped_next': scoped_next,
        'scoped_resolve': scoped_resolve,
        'scoped_rewind': scoped_rewind,
        'scoped_window': scoped_window,
        'sibling_scopes': sibling_scopes,
        'absorb_take': absorb_take,
        'Command': Command,
        }
    namespace.update(refs.objects)
    code = compile(source, filename, 'exec')
    exec(code, namespace)
    parse = namespace['parse_command_set']
    parse.source = source
    return parse


def _recipe_arg(value, refs):
    """
    One recipe argument as source.  Literals render by repr;
    builtin converters render bare; any other class or callable
    goes through the reference table, which supplies the import
    under a collision-safe name (this is what lets
    `validate(..., type=float)` and `accumulator[Path]` survive
    emission with their semantics intact).
    """
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return repr(value)
    builtin = _builtin_converters.get(value)
    if builtin:
        return builtin
    preferred = '_' + getattr(value, '__name__', 'ref')
    return refs.add(preferred, value)


def render_ref(name, obj, refs=None):
    recipe = getattr(obj, '__appeal_recipe__', None)
    if recipe:
        # a vocabulary product (split(':'), counter(), ...): the
        # compiled module re-runs the factory--the vocabulary
        # travels with appeal.runtime--with each argument rendered
        # from the structured recipe (kind, factory, args, kwargs);
        # class arguments add refs of their own
        kind, factory, args, kwargs = recipe
        bits = [_recipe_arg(a, refs) for a in args]
        bits.extend(f'{k}={_recipe_arg(v, refs)}'
                    for k, v in kwargs.items())
        if kind == 'subscript':
            return f'{name} = {factory}[{", ".join(bits)}]'
        return f'{name} = {factory}({", ".join(bits)})'
    return _render_ref(name, obj)


def _render_ref(name, obj):
    """
    Compiled-module mode: render one ref as Python source--an
    import line or a literal assignment.  Raises
    AppealConfigurationError, naming the offender, for anything
    unrenderable.  This refusal is the north star's teeth.

    Note the compiled module imports NONE of the user's grammar:
    every converter reachable from a command function arrives LIVE
    at registration (see _classify_refs).  This renders only the
    leftovers--constants, recipes' class arguments, the process
    streams, and the stock help shim.
    """
    # the STOCK default_help rides into a compiled module as a
    # shim over its generated parse_help (an overridden
    # default_help refuses below, by name--the north star)
    func = getattr(obj, '__func__', None)
    if (func is not None
            and getattr(func, '__qualname__', '')
            in ('Appeal.default_help', 'Appeal.help')):
        return (f'def {name}(topic):\n'
                f'    parse_help([topic] if topic else [])')
    # the three process streams render by identity: `sys` is in
    # every compiled module's import footprint (the canonical
    # appeal.file() usage has `= sys.stdout` as a default)
    if obj is sys.stdin:
        return f'{name} = sys.stdin'
    if obj is sys.stdout:
        return f'{name} = sys.stdout'
    if obj is sys.stderr:
        return f'{name} = sys.stderr'
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
            f"can't emit a compiled module: default value {obj!r} "
            f"doesn't round-trip through repr()")

    # callables import from the user's module
    module = getattr(obj, '__module__', None)
    qualname = getattr(obj, '__qualname__', None)
    if callable(obj) and module and qualname:
        if module == '__main__':
            raise AppealConfigurationError(
                f"can't emit a compiled module: {qualname!r} is "
                f"defined in __main__, so the generated module "
                f"can't import it; move it into an importable "
                f"module")
        if '<' in qualname:
            raise AppealConfigurationError(
                f"can't emit a compiled module: {module}.{qualname} "
                f"isn't importable by name (lambdas, closures, and "
                f"nested callables can't be imported)")
        # past the refusals, `head` IS importable from `module`, so
        # canonicalize away a private-submodule __module__
        module = _public_module(module, qualname.split('.')[0])
        if '.' in qualname:
            # a class's method (or a nested class): import the
            # outermost class, reach the rest by attribute
            head = qualname.split('.')[0]
            return (f'from {module} import {head}\n'
                    f'{name} = {qualname}')
        real_name = qualname
        if real_name == name:
            return f'from {module} import {real_name}'
        return f'from {module} import {real_name} as {name}'

    raise AppealConfigurationError(
        f"can't emit a compiled module: don't know how to render "
        f"{name} = {obj!r}")


def render_refs(refs):
    """
    Render every ref, sorted into (imports, constants) for the
    script header.  A worklist, not a plain loop: rendering a
    recipe may ADD refs (its class arguments), and those must
    be rendered too.
    """
    imports = []
    constants = []
    done = set()
    while True:
        pending = [(n, o) for n, o in refs.objects.items()
                   if n not in done]
        if not pending:
            return imports, constants
        for name, obj in pending:
            done.add(name)
            rendered = render_ref(name, obj, refs)
            if rendered.startswith('from ') and '\n' in rendered:
                # a dotted ref: the import line and the
                # attribute reach
                line, _, assignment = rendered.partition('\n')
                if line not in imports:
                    imports.append(line)
                constants.append(assignment)
            elif rendered.startswith('from '):
                if rendered not in imports:
                    imports.append(rendered)
            else:
                constants.append(rendered)


def _public_module(module, head):
    """
    The public module to import `head` from.  An object's __module__
    can name a private implementation submodule instead of its public
    home (CPython 3.13 reports `pathlib.Path.__module__` as
    'pathlib._local'; 3.14 reverted it).  Returns the shortest
    already-imported ancestor module that re-exports the SAME object
    (identity-checked); falls back to `module` when none does, which
    is the ordinary case.  Called only for a `head` already known
    importable from `module` (the caller refused lambdas etc. first).
    """
    target = getattr(sys.modules.get(module), head, None)
    assert target is not None
    parts = module.split('.')
    for depth in range(1, len(parts)):       # ancestors, shortest first
        ancestor = '.'.join(parts[:depth])
        mod = sys.modules.get(ancestor)
        if mod is not None and getattr(mod, head, None) is target:
            return ancestor
    return module


# ------------------------------------------------------------------
# Precompiled modules (Larry's design, 2026-08-09; speed rework
# 2026-08-16).  An importable file WEARING THE APPEAL API that does
# `import appeal` for the runtime and bakes its parser tables and
# fingerprints.  The program that uses it is unchanged:
#
#     try:
#         import compiled as appeal
#     except ImportError:
#         import appeal
#
# The module imports NONE of the user's grammar: every converter
# reachable from a command function arrives LIVE at registration,
# walked off the decorated function's annotations (ruled 2026-08-16,
# the relationship runs the other way--see _harvest_paths).  Only
# appeal.runtime (the stdlib-only core) is imported eagerly; render
# rides behind lazy wrappers, so importing the compiled module and
# parsing a line never touch big/inspect/build.
# ------------------------------------------------------------------

# the runtime functions a generated parser calls by name, PLUS the
# converter vocabulary a recipe re-runs by factory name (optional[T],
# split(':'), counter(), ...); all live in appeal.runtime (the cheap
# core), so the import is fast and stdlib-only.  A superset--unused
# names cost nothing.
_RUNTIME_IMPORTS = (
    'parse_tokens', 'tokenize', 'convert', 'accumulate', 'collect_mapping',
    'fold', 'call_converter', 'convert_value', 'window_options',
    'greedy_sizes', 'did_you_mean', 'check_count', 'absorb_take',
    'scopes_for', 'sibling_scopes', 'scoped_forces', 'scoped_window',
    'scoped_resolve', 'scoped_rewind', 'scoped_next',
    'run_command_set', 'UsageError', 'Command',
    # compiled modules bake no help/usage: -h routes to full Appeal
    # through this signal (error-tagging lives in the dispatcher)
    '_CompiledHelp',
    # the converter vocabulary (recipe factories, rendered bare)
    'optional', 'split', 'validate', 'validate_range', 'counter',
    'file', 'accumulator', 'mapping',
    )

def _harvest_paths(fn, decorations=None):
    """
    {id(callable): (obj, path)} for every callable reachable from
    fn the way build reaches converters: parameter annotations
    (Annotated dereferenced), @option override annotations (from
    the app's decorations registry--never function attributes,
    ruled 2026-08-09), and type-of-default inference,
    recursively.  The paths are the recipes a compiled module's
    shim walks at registration time
    (runtime.resolve_fingerprint_path is the exact mirror), so a
    converter arrives LIVE from the decorated function--never by
    import.
    """
    from .runtime import (_deref_annotated, _parameter_default,
                          _params_host)
    out = {}

    def note(child, path, depth):
        if callable(child) and id(child) not in out:
            out[id(child)] = (child, path)
            walk(child, path, depth + 1)

    def walk(obj, path, depth):
        if depth > 8:
            return
        host = _params_host(obj)
        if host is None or not hasattr(host, '__code__'):
            return
        code = host.__code__
        varargs = bool(code.co_flags & 0x04)
        varkw = bool(code.co_flags & 0x08)
        named = code.co_argcount + code.co_kwonlyargcount
        annotations = getattr(host, '__annotations__', None) or {}
        kwdefaults = getattr(host, '__kwdefaults__', None) or {}
        defaults = getattr(host, '__defaults__', None) or ()
        positional = code.co_varnames[:code.co_argcount]
        # named params, PLUS *args/**kwargs--their annotations
        # carry converters too (path(start, *segs: seg))
        for pname in code.co_varnames[:named + varargs + varkw]:
            annotation = annotations.get(pname)
            if annotation is not None:
                note(_deref_annotated(annotation),
                     path + (('annotation', pname),), depth)
                continue
            has_default = (pname in kwdefaults
                           or (pname in positional
                               and positional.index(pname)
                               >= code.co_argcount - len(defaults)))
            if has_default:
                note(type(_parameter_default(host, pname)),
                     path + (('default_type', pname),), depth)
        import inspect
        empty = inspect.Parameter.empty
        overrides = (decorations.option_overrides.get(obj)
                     if decorations is not None else None) or {}
        for pname, declarations in overrides.items():
            for index, declaration in enumerate(declarations):
                annotation = declaration['annotation']
                if (annotation is not None and annotation is not empty
                        and callable(annotation)):
                    note(_deref_annotated(annotation),
                         path + (('override', (pname, index)),),
                         depth)

    walk(fn, (), 0)
    return out


def _classify_refs(refs, impls, harvests, decorations):
    """
    Render the refs of a compiled MODULE.  Three fates: an impl
    callable (a command function) becomes a SLOT, bound live by the
    shim at registration; a vocabulary product re-runs its recipe,
    as ever; ANY converter reachable from its command's live
    function becomes a slot with a resolution PATH--importable or
    not, the compiled module imports NONE of the user's grammar (it
    walks the live function's annotations, ruled 2026-08-16, the
    relationship runs the other way).  Everything else (constants,
    non-converter refs) renders as today.  A converter's SIGNATURE
    drift is caught by its command's recursive fingerprint; the
    per-converter decoration fingerprint stays (@option/@parameter
    on a converter isn't in the signature).
    Returns (imports, constants, slots, impl_names, ref_specs).
    """
    from .runtime import (decoration_fingerprint, fingerprint,
                          _params_host)
    imports, constants, slots = [], [], []
    impl_names = {}
    ref_specs = {key: [] for key, _ in harvests}
    option_overrides = decorations.option_overrides
    parameter_usage = decorations.parameter_usage
    done = set()

    while True:
        pending = [(n, o) for n, o in refs.objects.items()
                   if n not in done]
        if not pending:
            return imports, constants, slots, impl_names, ref_specs
        for name, obj in pending:
            done.add(name)
            if id(obj) in impls:
                for key in impls[id(obj)]:
                    impl_names[key] = name
                slots.append(name)
                continue
            if not getattr(obj, '__appeal_recipe__', None) and callable(obj):
                owner = None
                for key, table in harvests:
                    hit = table.get(id(obj))
                    if hit is not None:
                        owner = (key, hit[1])
                        break
                if owner is not None:
                    key, path = owner
                    fingerprintable = _params_host(obj) is not None
                    ref_specs[key].append(
                        (name, path,
                         fingerprint(obj) if fingerprintable
                         else None,
                         decoration_fingerprint(
                             obj, option_overrides,
                             parameter_usage)))
                    slots.append(name)
                    continue
            rendered = render_ref(name, obj, refs)
            if rendered.startswith('from ') and '\n' in rendered:
                line, _, assignment = rendered.partition('\n')
                if line not in imports:
                    imports.append(line)
                constants.append(assignment)
            elif rendered.startswith('from '):
                if rendered not in imports:
                    imports.append(rendered)
            else:
                constants.append(rendered)


def _runtime_import_block():
    "The `from appeal.runtime import (...)` line, wrapped."
    names = ',\n    '.join(_RUNTIME_IMPORTS)
    return f'from appeal.runtime import (\n    {names},\n    )\n'


def emit_precompiled_module(commands, global_plan=None, *, argv0=None,
                            templates=None, repeat=False, subs=None,
                            sub_repeat=None, default=None,
                            sub_defaults=None, version=None,
                            max_columns=79, help=True, doc=None,
                            config=None, global_is_user=False,
                            decorations=None):
    """
    The text of a compiled parser MODULE implementing this program:
    an importable file wearing the Appeal API (see precompile.py).
    Appeal() and the decorators in the module don't build anything:
    they match the live functions to the precompiled bits by
    fingerprint (all-or-nothing verification at main(); any drift
    is a loud regenerate error naming every offender).  commands
    maps user command words to their plans; global_plan is the
    set's global (spec-visible only when a user registered it--the
    synthesized dispatcher and stock precommand carry no function
    to match).  config is the baked-knob blob the shim compares its
    constructor arguments against.
    """
    from .build import Decorations
    from .runtime import (decoration_fingerprint, fingerprint)
    if decorations is None:
        decorations = Decorations()
    prog = argv0 or 'program'
    # the facade says whether a USER registered the global command
    # (the synthesized dispatcher and the stock precommand carry
    # no function for the shim to match)
    user_global = global_is_user and global_plan is not None
    subs = subs or {}
    sub_repeat = sub_repeat or {}
    sub_defaults = sub_defaults or {}
    # the drift data, computed UP FRONT straight from the plans--so
    # fingerprint/options/arguments go INTO each Command's constructor.
    # (converters need the emitted ref names, so they're folded on
    # afterward--the one field with a genuine ordering dependency.)
    impls = {}
    harvests = []
    fingerprints = {}
    decor_fingerprints = {}
    key_fn = {}
    def note_fn(key, fn):
        # one function may serve several nodes; each key maps to the
        # Command object the shim fills at registration
        impls.setdefault(id(fn), []).append(key)
        key_fn[key] = fn
        harvests.append((key, _harvest_paths(fn, decorations)))
        fingerprints[key] = fingerprint(fn)
        decor_fingerprints[key] = decoration_fingerprint(
            fn, decorations.option_overrides,
            decorations.parameter_usage)
    for word, plan in commands.items():
        note_fn(('command', word), plan.callable)
    for parent, table in subs.items():
        for sub, plan in table.items():
            note_fn(('sub', parent, sub), plan.callable)
    if default is not None:
        note_fn(('default',), default.callable)
    for parent, plan in sub_defaults.items():
        note_fn(('subdefault', parent), plan.callable)
    if user_global:
        note_fn(('global',), global_plan.callable)

    # id(callable) -> (fingerprint, options, arguments), handed to the
    # emitter so it bakes them into `Command(...)` (a fn shared by
    # several words has one fingerprint)
    command_meta = {}
    for key, fn in key_fn.items():
        opts, args_ = decor_fingerprints[key]
        command_meta[id(fn)] = (fingerprints[key], opts, args_)

    if commands:
        source, refs = emit_command_set(
            commands, global_plan, prog, templates, None,
            repeat, subs or None, sub_repeat or None,
            version=version, max_columns=max_columns, help=help,
            default=default, sub_defaults=sub_defaults or None,
            doc=doc, compiled=True, command_meta=command_meta)
        entry = 'parse_command_set'
        complete = '_COMPLETE_command_set'
        description = f'command-line parsing ({", ".join(commands)})'
    else:
        source, refs = emit(global_plan, templates=templates,
                            max_columns=max_columns, compiled=True,
                            command_meta=command_meta)
        symbol = _ident(global_plan.name)
        entry = f'parse_{symbol}'
        complete = f'_COMPLETE_{symbol}'
        description = f'command-line parsing for {global_plan.name!r}'

    imports, constants, slots, impl_names, ref_specs = _classify_refs(
        refs, impls, harvests, decorations)

    # THE FOLD (ruled 2026-08-17): the verification data lives ON the
    # Command objects, not in a parallel spec tree.  fingerprint /
    # options / arguments are baked into the constructor (above, via
    # command_meta); only .converters is folded on here, because it
    # needs the ref names assigned during emission.  The shim walks
    # the runtime Command tree (skipping the fused version/help, which
    # carry no fingerprint).
    fold_lines = []
    for key, fn in key_fn.items():
        cn = refs.command_names.get(id(fn))
        if cn is None:
            continue        # a nested-class construct: no callable
        fold_lines.append(
            f'{cn}.converters = {tuple(sorted(ref_specs[key]))!r}')

    global_name = (refs.command_names.get(id(global_plan.callable))
                   if user_global else None)
    default_name = (refs.command_names.get(id(default.callable))
                    if default is not None else None)
    spec = {
        'program': prog,
        'entry': entry,
        'complete': complete,
        'templates': templates,
        'config': dict(config or {}),
        # names the shim resolves against the module globals and walks
        # (the Commands carry the fingerprints); None where absent
        'commands': '_COMMANDS' if commands else None,
        'global': global_name,
        'default': default_name,
    }

    header = (
        f'#\n'
        f'# {prog} -- {description}\n'
        f'# Generated by Appeal: a compiled parser MODULE, wearing '
        f'the Appeal API.\n'
        f'# Import it AS appeal and your program is unchanged:\n'
        f'#\n'
        f'#     try:\n'
        f'#         import compiled as appeal\n'
        f'#     except ImportError:\n'
        f'#         import appeal\n'
        f'#\n'
        f'# Imports appeal.runtime (the stdlib-only core) and bakes '
        f'ONLY\n'
        f'# parse+dispatch--no help or usage text.  Help and errors '
        f'render\n'
        f'# live through full Appeal (imported only when one fires).\n'
        f'# Regenerate rather than edit.\n'
        )

    parts = [header]
    parts.append('\n# ---- the appeal runtime (stdlib-only core) ----\n')
    parts.append('import sys\n')
    parts.append(_runtime_import_block())
    parts.append('from appeal.precompile import compiled_appeal\n')
    # a compiled module bakes no display text: help AND errors render
    # live through full Appeal, so render never appears on any path
    # here (not even lazily).
    parts.append('\n# ---- your program (bound live at registration) ----\n')
    if imports:
        parts.append('\n'.join(imports) + '\n')
    if constants:
        parts.append('\n'.join(constants) + '\n')
    if slots:
        parts.append('\n'.join(f'{name} = None' for name in slots)
                     + '\n')
    parts.append('\n# ---- generated parser ----\n')
    parts.append(source)
    if fold_lines:
        parts.append('\n# ---- drift fingerprints, folded onto the '
                     'Commands ----\n')
        parts.append('\n'.join(fold_lines) + '\n')
    parts.append(f'\n# ---- the Appeal your program imports ----\n'
                 f'_SPEC = {spec!r}\n\n'
                 f'Appeal = compiled_appeal(_SPEC, globals())\n')
    return '\n'.join(parts)