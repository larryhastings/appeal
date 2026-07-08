#!/usr/bin/env python3
#
# appeal/plan.py
# Part of Appeal v2.
# Copyright 2021-2026 by Larry Hastings
#
# The plan tree: the passive, immutable data structure the compiler
# produces.  One Plan per callable (per grammar rule).  All behavior
# lives in the consumers--the parser rungs, usage generation, codegen,
# the JSON schema--never here.  Nothing at parse time ever writes to
# the tree.

class Terminal:
    """
    Marker for a terminal slot's converter: consumes one command-line
    string, converts it with a single callable (str, int, float,
    or any one-string-in callable).
    """
    __slots__ = ('converter',)

    def __init__(self, converter):
        self.converter = converter

    def __repr__(self):
        name = getattr(self.converter, '__name__', repr(self.converter))
        return f'<Terminal {name}>'


class Slot:
    """
    One positional parameter of a rule: an edge to a child.

    name          the Python parameter name
    usage_name    the name shown in usage (dashes, case, etc.)
    child         a Terminal, or another Plan (a nonterminal--not yet
                  implemented in the builder)
    required      computed by analysis (a defaulted parameter may
                  still be promoted to required by grouping)
    default       the default value, or NO_DEFAULT
    repeat        True for *args
    trailing      True for a keyword-only-no-default parameter
                  (a required trailing operand, filled from the end)

    count_options and suffix_after are the counting automaton's
    tables, computed by build's analysis pass:

    count_options   the operand counts this slot can consume, sorted
                    descending (a required terminal: (1,); an optional
                    nonterminal: its child's valid counts plus 0).
                    None for a repeat slot (it absorbs).
    suffix_after    (counts, minimum) completable by the slots after
                    this one; counts is a frozenset, or None meaning
                    unbounded (a repeat slot follows).
    """

    __slots__ = ('name', 'usage_name', 'child', 'required', 'default',
                 'repeat', 'trailing', 'count_options', 'suffix_after',
                 'barrier')

    def __init__(self, name, usage_name, child, required, default,
                 repeat=False, trailing=False):
        self.name = name
        self.usage_name = usage_name
        self.child = child
        self.required = required
        self.default = default
        self.repeat = repeat
        self.trailing = trailing
        self.count_options = None
        self.suffix_after = None
        self.barrier = False    # a group CERTAIN to consume operands:
                                # options behind it wait for it (gate rule)

    def __repr__(self):
        flags = []
        if self.required: flags.append('required')
        if self.repeat: flags.append('repeat')
        if self.trailing: flags.append('trailing')
        return f'<Slot {self.name} {self.child!r} {" ".join(flags)}>'


NO_DEFAULT = object()


class OptionRule:
    """
    One option of a rule.

    strings       the option strings, e.g. ('-v', '--verbose').
                  The long string is the *key*: it names the option
                  in the parse's "given" table (option strings are
                  globally unique within one command, checked at
                  build time).
    name          the Python parameter it fills
    kind          'flag'        presence means True, consumes nothing
                  'value'       consumes one operand (last one wins)
                  'accumulate'  list[T]: repeatable, collects values
                  'mapping'     dict[K, V]: repeatable, KEY=VALUE
    converters    () for a flag; (T,) for value/accumulate;
                  (K, V) for mapping
    default       the value when the option never appears
    """
    __slots__ = ('strings', 'name', 'kind', 'converters', 'default', 'explicit',
                 'usage_name', 'kwargs_delivered', 'child')

    def __init__(self, strings, name, kind, converters, default):
        self.strings = strings
        self.name = name
        self.kind = kind
        self.converters = converters
        self.default = default
        self.explicit = False    # True when @app.option supplied the strings
        self.usage_name = None   # @app.parameter metavar override
        self.kwargs_delivered = False   # @app.option into **kwargs:
                                        # omitted from the call when absent
        self.child = None        # kind='group': the converter's Plan

    def table_entry(self, windowed=False):
        """
        This option's parse_tokens table value.  Folds carry their
        per-occurrence operand count; everything else is a pair.
        A windowed option (owned by a *args group) gets a 'w:'-
        prefixed kind and an explicit operand count: occurrences
        are collected with positions and bound to instances later.
        """
        if self.kind == 'nullary':
            entry = (self.key, 'flag')      # consumes nothing
        elif self.kind == 'group':
            # consumes the group's minimum operands inline, as one
            # no-repeat tuple; short-concat (-fjoe) may add one more
            # when the group has optional operands
            entry = (self.key, 'group', self.child.minimum)
        elif self.kind in ('fold', 'fold1'):
            entry = (self.key, self.kind, len(self.converters) - 1)
        elif self.kind == 'value' and len(self.converters) > 1:
            entry = (self.key, 'value', len(self.converters) - 1)
        else:
            entry = (self.key, self.table_kind)
        if not windowed:
            return entry
        kind = entry[1]
        nargs = entry[2] if len(entry) > 2 else (0 if kind == 'flag' else 1)
        return (entry[0], 'w:' + kind, nargs)

    @property
    def key(self):
        return self.strings[-1]

    @property
    def is_flag(self):
        return self.kind == 'flag'

    @property
    def table_kind(self):
        "The kind as parse_tokens sees it: accumulate/mapping are both 'multi'."
        if self.kind in ('accumulate', 'mapping'):
            return 'multi'
        return self.kind

    def __repr__(self):
        return f'<OptionRule {"/".join(self.strings)} ({self.kind}) -> {self.name}>'


class Plan:
    """
    One grammar rule: one callable, analyzed.

    callable      the thing we'll invoke
    name          its name (for usage and generated code)
    slots         positional Slots, in call order.  Trailing required
                  operands (kwonly-no-default) come after any *args
                  slot, mirroring the signature.
    options       OptionRules, in declaration order
    minimum,      operand-count arity, folded over the slots
    maximum       (maximum is None for unbounded)
    valid_counts  the set of acceptable operand counts, or None
                  when unbounded (then: count >= minimum)
    """
    __slots__ = ('callable', 'name', 'slots', 'options',
                 'minimum', 'maximum', 'valid_counts', 'windowed', 'gated',
                 'certain', 'var_keyword')

    def __init__(self, callable, name, slots, options,
                 minimum, maximum, valid_counts):
        self.windowed = False   # True: a *args group; options bind by window
        self.gated = False      # True on the top plan: barriers exist somewhere
        self.certain = True     # False: this group might never be entered
        self.var_keyword = None # the **kwargs parameter's name, if any
        self.callable = callable
        self.name = name
        self.slots = slots
        self.options = options
        self.minimum = minimum
        self.maximum = maximum
        self.valid_counts = valid_counts

    def __repr__(self):
        return (f'<Plan {self.name} slots={len(self.slots)} '
                f'options={len(self.options)} '
                f'arity=({self.minimum}, {self.maximum})>')

    def usage(self, prog=None):
        """
        A one-line usage string, read straight off the tree.
        v1's shape, kept: options first (all strings, pipe-joined,
        converter names as metavars), then operands; inside a
        group's brackets, the group's options come first--so every
        bracket reads left-to-right as something you can type
        (announce-first, truth in advertising).
        """
        def option_text(o):
            bits = ['|'.join(o.strings)]
            if o.kind == 'group':
                bits.append(body_text(o.child))
            elif o.kind not in ('flag', 'nullary'):
                if o.usage_name is not None:
                    # @app.parameter renamed the metavar
                    bits.append(f'<{o.usage_name}>')
                else:
                    converters = o.converters[1:] if len(o.converters) > 1 else o.converters
                    for c in converters:
                        if c is str or c is tuple:
                            name = o.name
                        else:
                            name = getattr(c, '__name__', o.name)
                        bits.append(f'<{name}>')
            return '[' + ' '.join(bits) + ']'

        def slot_text(slot):
            child = slot.child
            if isinstance(child, Terminal):
                if slot.repeat:
                    return f'[{slot.usage_name}]...'
                if slot.required:
                    return slot.usage_name
                return f'[{slot.usage_name}]'
            body = body_text(child)
            if slot.repeat:
                return f'[{body}]...'
            if slot.required:
                return body
            return f'[{body}]'

        def body_text(plan):
            bits = [option_text(o) for o in plan.options]
            bits.extend(slot_text(s) for s in plan.slots)
            return ' '.join(bits)

        return f'{prog or self.name} {body_text(self)}'.rstrip()


def command_set_usage(prog, global_plan):
    """
    The usage LINE for a multi-command program: the program name,
    the global command's options and operands (if any), and the
    'command' word.  The command listing is rendered separately,
    from the corpus (help.command_set_corpus).
    """
    parts = [prog]
    if global_plan is not None:
        rest = global_plan.usage().partition(' ')[2]
        if rest:
            parts.append(rest)
    parts.append('command')
    return ' '.join(parts)
