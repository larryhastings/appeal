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

def _validate_arg_format(fmt):
    "Only {name} and {name.upper()} may interpolate."
    from .runtime import AppealConfigurationError
    if not isinstance(fmt, str):
        raise AppealConfigurationError(
            f"positional_argument_usage_format must be a string, "
            f"not {fmt!r}")
    probe = fmt.replace('{name.upper()}', '').replace('{name}', '')
    if '{' in probe or '}' in probe:
        raise AppealConfigurationError(
            f"positional_argument_usage_format {fmt!r}: the only "
            f"interpolations are {{name}} and {{name.upper()}}")


# --8<-- start appeal plan classes --8<--
DEFAULT_ARG_FORMAT = '{name}'


def format_arg(fmt, name):
    "Render one operand's usage name through the format string."
    return fmt.replace('{name.upper()}', name.upper()).replace(
        '{name}', name)


def _oparg_names(o):
    """
    The bare operand names an option's value(s) render as, before
    the format string decorates them.  A single-operand option
    echoes its own parameter name (--width -> 'width'); a
    multi-parameter converter (--where X Y) borrows its
    parameters' names; a tuple option falls back to element type
    names (no natural names to borrow).
    """
    if len(o.converters) <= 1:
        return [o.name]
    if o.converters[0] is tuple:
        return [getattr(c, '__name__', o.name) for c in o.converters[1:]]
    import inspect
    try:
        params = inspect.signature(o.converters[0]).parameters.values()
        return [p.name for p in params]
    except (ValueError, TypeError):
        return [getattr(c, '__name__', o.name) for c in o.converters[1:]]


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
                 'usage_name', 'kwargs_delivered', 'child', 'fold_minimum',
                 'annotation', 'auto_shorts', 'present')

    def __init__(self, strings, name, kind, converters, default):
        self.strings = strings
        self.name = name
        self.kind = kind
        self.converters = converters
        self.default = default
        self.present = True      # kind='flag': the value presence
                                 # stores--v1: `not default`, so a
                                 # default-True flag turns its
                                 # thing OFF
        self.explicit = False    # True when @app.option supplied the strings
        self.usage_name = None   # @app.parameter metavar override
        self.kwargs_delivered = False   # @app.option into **kwargs:
                                        # omitted from the call when absent
        self.child = None        # kind='group': the converter's Plan
        self.fold_minimum = None # fold kinds: option()'s required count
        self.annotation = None   # the parameter's annotation, kept so
                                 # default_options (the option-string
                                 # policy) can see it at finalize time
        self.auto_shorts = ()    # short strings the policy proposes;
                                 # the finalize pass claims each if free

    def table_entry(self, windowed=False):
        """
        This option's parse_tokens table value.  Folds and groups
        carry their per-occurrence (minimum, maximum) operand
        counts--consumption is greedy to the maximum (v1: an
        optional operand takes the next token unconditionally);
        everything else is a pair or an exact count.
        A windowed option (owned by a *args group) gets a 'w:'-
        prefixed kind and an explicit operand count: occurrences
        are collected with positions and bound to instances later.
        """
        if self.kind == 'nullary':
            # consumes nothing, like a flag--but refuses '='
            # (there's no boolean to set; presence IS the value)
            entry = (self.key, 'nullary')
        elif self.kind == 'group':
            entry = (self.key, 'group',
                     self.child.minimum, self.child.maximum)
        elif self.kind in ('fold', 'fold1'):
            n = len(self.converters) - 1
            m = self.fold_minimum if self.fold_minimum is not None else n
            entry = (self.key, self.kind, m, n)
        elif self.kind == 'value' and len(self.converters) > 1:
            entry = (self.key, 'value', len(self.converters) - 1)
        elif self.kind == 'flag':
            # a flag entry always carries the value presence
            # stores (v1: `not default`)--never an operand count,
            # flags consume nothing
            entry = (self.key, 'flag', self.present)
        else:
            entry = (self.key, self.table_kind)
        if not windowed:
            return entry
        kind = entry[1]
        if len(entry) > 2:
            return (entry[0], 'w:' + kind) + tuple(entry[2:])
        return (entry[0], 'w:' + kind,
                0 if kind in ('flag', 'nullary') else 1)

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
                 'certain', 'var_keyword', 'constructs', 'binds',
                 'tree_trailing', 'scoped_keys', 'arg_format', 'auto_help',
                 'sibling_parents', 'sibling_keys', 'pre_plan', 'prog')

    def __init__(self, callable, name, slots, options,
                 minimum, maximum, valid_counts):
        # how operands render in usage/help: a format string over
        # the operand name (the app's positional_argument_usage_format,
        # stamped onto the top plan at build time; children inherit
        # the default and read it off the root at render time).
        self.arg_format = DEFAULT_ARG_FORMAT
        # whether this command answers -h/--help (the app's help=
        # knob, stamped at build time; False suppresses the
        # automatic help option entirely).
        self.auto_help = True
        self.windowed = False   # True: a *args group; options bind by window
        self.gated = False      # True on the top plan: barriers exist somewhere
        self.certain = True     # False: this group might never be entered
        self.var_keyword = None # the **kwargs parameter's name, if any
        # class-based commands: the execution environment's keys.
        # constructs: this command builds an instance--stash it
        # under this key.  binds: this command reads the instance
        # under this key (a method's self, or the parent instance a
        # nested class is constructed from).
        self.constructs = None
        self.binds = None
        # trailing arguments in this whole subtree: they reserve
        # from the END of the command's argument stream, wherever
        # they sit in the tree (the uniform end-reservation rule)
        self.tree_trailing = 0
        # option strings declared by more than one window (a
        # converter reused across sibling slots, or two converters
        # agreeing on the grammar): occurrences record positionally
        # and bind by stream order.  Set on the top plan.
        self.scoped_keys = frozenset()
        # SIBLING OPTION GROUPS (Larry's ruling, 2026-07-18): when
        # every declarer of a scoped key is the direct child of a
        # top-level group OPTION (e1: extras, e2: extras), the
        # windows are announced by the parent options, not by
        # operand position.  sibling_parents: ((parent key,
        # (its scoped child keys...)), ...) in declaration order;
        # sibling_keys: the union.  Child options may summon the
        # FIRST declared sibling into existence; later siblings
        # exist only when announced.  Set on the top plan.
        self.sibling_parents = ()
        self.sibling_keys = frozenset()
        # the precommand's mini plan (program metadata: -V,
        # --version, ...), attached to the global-era plan at
        # compile when default_mappings mapped anything to it.
        # Its options scan in the same era; the run stage resolves
        # and pops them FIRST, then proceeds (Larry's design,
        # 2026-07-19).
        self.pre_plan = None
        # the program name, stamped on COMMAND plans at build so
        # the usage line reads `usage: prog go ...` (0.6.4's
        # shape, ruled 2026-07-19)--pasteable into a shell
        self.prog = None
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
        fmt = self.arg_format

        def name_text(slot):
            # a positional operand's rendered metavar: an explicit
            # @app.parameter/add_parameter_usage rename (usage_name
            # != name) is the literal text and wins outright;
            # otherwise the name flows through the format string.
            if slot.usage_name != slot.name:
                return slot.usage_name
            return format_arg(fmt, slot.usage_name)

        def transparent_name(slot):
            # the outer slot's name flows through iff the child
            # consumes exactly one operand AND that terminal wasn't
            # explicitly renamed (usage_name != name means
            # @app.parameter or add_parameter_usage spoke; explicit
            # wins).  Returns the FINAL display text (formatted, or
            # literal if the outer slot was itself renamed).
            inner = slot.child.sole_terminal_slot()
            if inner is None:
                return None
            if inner.usage_name != inner.name:
                return None
            return name_text(slot)

        def option_text(o):
            bits = ['|'.join(o.strings)]
            if o.kind == 'group':
                bits.append(body_text(o.child))
            elif o.kind not in ('flag', 'nullary'):
                if o.usage_name is not None:
                    # @app.parameter renamed the metavar: explicit
                    # wins outright over the format string
                    bits.append(o.usage_name)
                else:
                    # v1's knob: an option operand shows the NAME
                    # (the parameter's, or the converter parameters'
                    # for a multi-operand option), formatted through
                    # positional_argument_usage_format
                    for name in _oparg_names(o):
                        bits.append(format_arg(fmt, name))
            return '[' + ' '.join(bits) + ']'

        def slot_text(slot, rename=None):
            child = slot.child
            if isinstance(child, Terminal):
                # rename, when present, is already final display text
                name = rename if rename is not None else name_text(slot)
                if slot.repeat:
                    return f'[{name}]...'
                if slot.required:
                    return name
                return f'[{name}]'
            # the transparency rule: a converter that consumes
            # exactly one operand is transparent to naming--the
            # annotated parameter's name flows through to its
            # terminal.  (An explicit rename on the inner
            # parameter still wins.)  Applied at render time:
            # plans are memoized and shared, so `flavor` used
            # under two different outer names must render
            # differently per use.
            body = body_text(child, rename=transparent_name(slot))
            if slot.repeat:
                return f'[{body}]...'
            if slot.required:
                return body
            return f'[{body}]'

        def body_text(plan, rename=None):
            bits = [option_text(o) for o in plan.options]
            bits.extend(slot_text(s, rename) for s in plan.slots)
            return ' '.join(bits)

        head = prog
        if head is None:
            head = (f'{self.prog} {self.name}' if self.prog
                    else self.name)
        return f'{head} {body_text(self)}'.rstrip()

    @property
    def body_minimum(self):
        "Arity in distribution space: the subtree's trailing excluded."
        return self.minimum - self.tree_trailing

    @property
    def body_valid_counts(self):
        if self.valid_counts is None:
            return None
        return {c - self.tree_trailing for c in self.valid_counts}

    def sole_terminal_slot(self):
        """
        The transparency rule's eligibility test: if this plan
        consumes exactly one operand, returns that Terminal's
        slot; otherwise None.
        """
        found = None
        for s in self.slots:
            if isinstance(s.child, Terminal):
                if found is not None:
                    return None
                found = s
            else:
                inner = s.child.sole_terminal_slot()
                if inner is None and s.child.count_terminals():
                    return None
                if inner is not None:
                    if found is not None:
                        return None
                    found = inner
        return found

    def count_terminals(self):
        n = 0
        for s in self.slots:
            if isinstance(s.child, Terminal):
                n += 1
            else:
                n += s.child.count_terminals()
        return n
# --8<-- end appeal plan classes --8<--


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
