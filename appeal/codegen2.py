#!/usr/bin/env python3
#
# appeal/codegen2.py
# Part of Appeal -- the REDESIGNED parser (2026-08-19), built alongside
# the old scan/run/given engine until it can replace it.
#
# The shape (co-designed with Larry):
#   * counts are tuples of [start, stop) ranges, None = unbounded --
#     the same representation for a command's arguments and an option's
#     opargs, so discontinuous count sets are expressible.
#   * ONE generic front-end tokenize(argv, commands) reads command CLASS
#     attributes and yields the IR: [(command_name, stream), ...], where
#     a stream item is a str (argument) or a tuple (normalized, *opargs).
#     '' is the global command.
#   * each command is a single-use Command subclass: class attrs feed
#     tokenize; __init__ builds per-parse state off self.callable
#     (converters from __annotations__, defaults from __kwdefaults__);
#     __call__(self, stream) walks the stream converting EVERY occurrence
#     (validate-all -- no `given`, no collapse).
#
# This module emits that class as source (for standalone/precompile) and
# execs it in-process.  Currently: flat commands (leaf operands, flag/
# value/fold options).  Groups/windowed/scoped/sibling/*args land next.

from .build import build_plan, all_options, subtree_option_keys
from .runtime import (convert, UsageError, MultiOption)

_BUILTINS = {str: 'str', int: 'int', float: 'float', bool: 'bool'}


# ---- ranges (start, stop) exclusive; None stop = unbounded ----------
def count_ok(n, ranges):
    return any(lo <= n and (hi is None or n < hi) for lo, hi in ranges)

def ceiling(ranges):
    tops = [(hi - 1) if hi is not None else None for _, hi in ranges]
    return None if None in tops else max(tops)

def runs(counts, minimum):
    "valid-count set (None=unbounded) -> tuple of (lo, hi) ranges."
    if counts is None:
        return ((minimum, None),)
    out = []
    for n in sorted(counts):
        if out and n == out[-1][1]:
            out[-1] = (out[-1][0], n + 1)
        else:
            out.append((n, n + 1))
    return tuple(out)


# ---- the master front-end: argv -> IR ------------------------------
def tokenize(argv, commands):
    words = frozenset(n for n in commands if n)
    ir = []
    name = '' if '' in commands else None
    stream, count = [], 0
    it = iter(argv)
    forced = False

    def finish():
        if name is None:
            return
        cls = commands[name]
        if not count_ok(count, cls.arguments):
            raise UsageError(f"{name or 'command'}: wrong number of "
                             f"arguments (got {count})", None)
        ir.append((name, stream))

    def consume(entry, first):
        top = ceiling(entry[1:])
        got = [] if first is None else [first]
        while top is None or len(got) < top:
            try:
                got.append(next(it))
            except StopIteration:
                break
        return got

    for token in it:
        if not forced and token == '--':
            forced = True
            continue
        if not forced and token.startswith('-') and token != '-':
            table = commands[name].options if name is not None else {}
            if token.startswith('--'):
                head, eq, val = token.partition('=')
                entry = table.get(head)
                if entry is None:
                    raise UsageError(f"unknown option {head!r}", None)
                if ceiling(entry[1:]) == 0:
                    stream.append((entry[0],) + ((val,) if eq else ()))
                else:
                    stream.append((entry[0], *consume(entry, val if eq else None)))
            else:
                entry = table.get('-' + token[1])
                if entry is None:
                    raise UsageError(f"unknown option {'-'+token[1]!r}", None)
                if ceiling(entry[1:]) == 0:
                    for ch in token[1:]:
                        stream.append((table['-' + ch][0],))
                else:
                    stream.append((entry[0], *consume(entry, token[2:] or None)))
            continue
        if name is None or (token in words and count >= commands[name].arguments[0][0]):
            finish()
            name, stream, count = token, [], 0
            continue
        stream.append(token)
        count += 1
    finish()
    return ir


# ---- the base Command ----------------------------------------------
class Command:
    callable = None
    def __init__(self):
        pass


def dispatch(argv, commands):
    result = None
    for name, stream in tokenize(argv, commands):
        result = commands[name]()(stream)
    return result


# ====================================================================
#  the emitter
# ====================================================================
def _converter_expr(name, converter, callable_expr='self.callable'):
    "The converter for a leaf: a builtin baked, else read off the live fn."
    b = _BUILTINS.get(converter)
    if b:
        return b
    return f"{callable_expr}.__annotations__[{name!r}]"


def _no_group_options(plan):
    for o in plan.options:
        if o.child is not None or o.kwargs_delivered:
            return False
    return True


def _is_leafy(plan):
    "Every operand slot a Terminal (leaf or bare *args); no groups yet."
    from .plan import Terminal
    for slot in plan.slots:
        if slot.trailing or not isinstance(slot.child, Terminal):
            return False
    return _no_group_options(plan)


def _is_groupy(plan):
    """Leaves, bare *args, and OPTION-FREE converter groups (recursively).
    Rejects trailing operands, windowed (*args-of-group) slots, and any
    group carrying inner options -- scoped/sibling/windowed land next."""
    from .plan import Terminal
    if not _no_group_options(plan):
        return False
    for slot in plan.slots:
        if slot.trailing:
            return False
        if isinstance(slot.child, Terminal):
            continue
        if slot.child is None:      # never happens here, but be safe
            return False
        if slot.repeat:             # *args of a converter group == windowed
            return False
        if subtree_option_keys(slot.child):
            return False
        if not _is_groupy(slot.child):
            return False
    return True


def _feasible(c, suffix, rem='remaining'):
    "Can the slots after this one consume what's left after taking c?"
    counts, minimum = suffix
    take = rem if c == 0 else f'{rem} - {c}'
    if counts is None:
        return f'{take} >= {minimum}'
    if len(counts) == 1:
        return f'{take} == {next(iter(counts))}'
    return f'{take} in ({", ".join(str(x) for x in sorted(counts))})'


def _default_literal(value):
    "A simple default baked inline; complex ones await a later pass."
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    if isinstance(value, tuple) and all(
            x is None or isinstance(x, (bool, int, float, str)) for x in value):
        return repr(value)
    raise NotImplementedError(f"non-literal positional default {value!r}")


def _emit_fill(L, slots, rem, callable_expr, prefix, ind):
    """Emit the counting automaton (leftmost-maximal-completable) for
    `slots`, consuming from the shared `arguments`/`i`, bounded by the
    `rem` operand-count variable.  Binds a_<prefix><name> per slot and
    returns the positional call-arg expressions for this level.  Recurses
    into option-free converter groups, threading the live converter chain
    through `callable_expr` and a fresh var prefix."""
    from .plan import Terminal
    call_args = []
    for slot in slots:
        var = f"a_{prefix}{slot.name}"
        if slot.repeat:                     # bare *args: takes the suffix spares
            conv = _converter_expr(slot.name, slot.child.converter, callable_expr)
            _, smin = slot.suffix_after
            L.append(f"{ind}_take = {rem} - {smin}")
            L.append(f"{ind}{var} = tuple(convert({conv}, _x, {slot.name!r}) "
                     f"for _x in arguments[i:i + _take])")
            L.append(f"{ind}i += _take; {rem} -= _take")
            call_args.append(f"*{var}")
            continue
        opts = slot.count_options
        is_group = not isinstance(slot.child, Terminal)

        def emit_take(c, pad):
            "Bind `var` by taking exactly c operands (0 -> the default)."
            if c == 0:
                L.append(f"{pad}{var} = {_default_literal(slot.default)}")
            elif is_group:
                gconv = _converter_expr(slot.name, slot.child, callable_expr)
                rv = f"r_{prefix}{slot.name}"
                L.append(f"{pad}{rv} = {c}")
                inner = _emit_fill(L, slot.child.slots, rv, gconv,
                                   f"{prefix}{slot.name}_", pad)
                L.append(f"{pad}{var} = {gconv}({', '.join(inner)})")
                L.append(f"{pad}{rem} -= {c}")
            else:
                conv = _converter_expr(slot.name, slot.child.converter, callable_expr)
                L.append(f"{pad}{var} = convert({conv}, arguments[i], "
                         f"{slot.name!r}); i += 1; {rem} -= 1")

        if opts is not None and len(opts) == 1:
            emit_take(opts[0], ind)
        else:                               # a choice: leftmost-maximal
            for k, c in enumerate(opts):
                head = 'if' if k == 0 else ('elif' if k < len(opts) - 1 else None)
                if head is None:
                    L.append(f"{ind}else:")
                else:
                    L.append(f"{ind}{head} {_feasible(c, slot.suffix_after, rem)}:")
                emit_take(c, ind + '    ')
        call_args.append(var)
    return call_args


def _option_ranges(o):
    if o.kind in ('flag', 'nullary'):
        return ((0, 1),)
    if o.kind in ('fold', 'fold1'):
        n = len(o.converters) - 1
        m = o.fold_minimum if o.fold_minimum is not None else n
        return runs(set(range(m, n + 1)), m)
    if o.kind == 'value' and len(o.converters) > 1:
        n = len(o.converters) - 1
        return ((n, n + 1),)
    return ((1, 2),)


def emit_command_class(plan, name):
    "Return source for `class Command_<name>(Command)` -- flat only."
    assert _is_groupy(plan), f"{name}: has inner-option groups (land next)"
    sym = name or 'global'
    L = [f"class Command_{sym}(Command):"]
    L.append(f"    name = {name!r}")
    L.append(f"    arguments = {runs(plan.valid_counts, plan.minimum)!r}")
    # options table
    opts = {}
    for _owner, o in all_options(plan):
        entry = (o.key, *_option_ranges(o))
        for s in o.strings:
            opts[s] = entry
    L.append(f"    options = {opts!r}")

    folds = [o for o in plan.options if o.kind in ('fold', 'fold1')]
    if folds:
        L.append("    def __init__(self):")
        L.append("        a = self.callable.__annotations__")
        L.append("        d = self.callable.__kwdefaults__ or {}")
        for o in folds:
            L.append(f"        self.o_{o.name} = a[{o.name!r}]()")
            L.append(f"        self.o_{o.name}.init(d.get({o.name!r}))")

    L.append("    def __call__(self, stream):")
    L.append("        arguments = []")
    for o in plan.options:
        if o.kind not in ('fold', 'fold1'):
            L.append(f"        v_{o.name} = self.callable.__kwdefaults__[{o.name!r}]")
    L.append("        for item in stream:")
    L.append("            if isinstance(item, str):")
    L.append("                arguments.append(item)")
    L.append("            else:")
    L.append("                option = item[0]")
    first = True
    for o in plan.options:
        head = 'if' if first else 'elif'
        first = False
        L.append(f"                {head} option == {o.key!r}:")
        if o.kind == 'flag':
            L.append(f"                    v_{o.name} = (item[1] == 'true') "
                     f"if len(item) > 1 else {o.present!r}")
        elif o.kind in ('fold', 'fold1'):
            convs = [_converter_expr(o.name, c) for c in o.converters[1:]]
            args = ', '.join(f"convert({c}, item[{k+1}], {o.name!r})"
                             for k, c in enumerate(convs))
            L.append(f"                    self.o_{o.name}.option({args})")
        else:  # value
            conv = _converter_expr(o.name, o.converters[0])
            L.append(f"                    v_{o.name} = convert({conv}, item[1], {o.name!r})")
    # operands: the counting automaton -- leftmost-maximal-completable,
    # recursing into option-free converter groups.  tokenize already
    # validated the total count, so some branch always fires.
    L.append("        n = len(arguments)")
    L.append("        i = 0")
    L.append("        remaining = n")
    call_args = _emit_fill(L, plan.slots, 'remaining', 'self.callable',
                           '', '        ')
    for o in plan.options:
        val = f"self.o_{o.name}.render()" if o.kind in ('fold', 'fold1') else f"v_{o.name}"
        call_args.append(f"{o.name}={val}")
    L.append(f"        return self.callable({', '.join(call_args)})")
    return '\n'.join(L)


def build_command_class(plan, name):
    src = emit_command_class(plan, name)
    ns = {'Command': Command, 'convert': convert, 'UsageError': UsageError}
    exec(compile(src, f'<Command_{name or "global"}>', 'exec'), ns)
    cls = ns[f'Command_{name or "global"}']
    cls.source = src
    return cls
