#!/usr/bin/env python3
#
# appeal/codegen2_trio_prototype.py
#
# NOT production code -- a hand-written prototype of what codegen2 would
# EMIT for the entangled trio (windowed / scoped / sibling), so the
# emission shape can be reviewed before it's wired into codegen2.emit_*.
#
# The thesis ("approach A"): the generated __call__ pulls each option
# occurrence off the interleaved stream tagged with its position
# (len(arguments) == operands-so-far, or a seq tick for siblings), then
# hands those occurrences to the SAME shared runtime helpers the
# interpreter and the old codegen use -- greedy_sizes/window_options for
# windowed, the ScopedQueue interval model for scoped, sibling_scopes for
# sibling.  No `given` collapse (every occurrence is kept); bit-identical
# to the oracle because the binding is literally the same functions.
#
# Run directly:  python -m appeal.codegen2_trio_prototype
# Each case is compared against appeal.Appeal.process.
import sys
sys.path.insert(0, '/home/larry/tron/src/appeal')
import appeal
import appeal.build as build
from appeal import codegen2
from appeal.runtime import (
    convert, UsageError,
    greedy_sizes, window_options,
    scopes_for, scoped_window, scoped_resolve, scoped_rewind, scoped_next,
    scoped_forces, sibling_scopes,
    )


# ====================================================================
#  WINDOWED  --  route(label, *legs: leg), leg(distance, *, fast)
# ====================================================================
def leg(distance: float, *, fast=False):
    return ('leg', distance, fast)

def route(label, *legs: leg):
    return ('route', label, legs)


class Command_route:
    name = 'route'
    arguments = ((1, None),)
    # ONE recognition table for tokenize -- the windowed child's strings
    # are folded in so tokenize knows -f/--fast take 0 opargs.
    options = {'--fast': ('--fast', (0, 1)), '-f': ('--fast', (0, 1))}
    callable = staticmethod(route)

    def __call__(self, stream):
        arguments = []
        fast_occurrences = []                   # (position, kind, value)
        for item in stream:
            if isinstance(item, str):
                arguments.append(item)
            else:
                option = item[0]
                if option == '--fast':          # a windowed child option
                    value = (item[1] == 'true') if len(item) > 1 else True
                    fast_occurrences.append((len(arguments), 'flag', value))
        n = len(arguments)
        i = 0
        remaining = n
        a_label = convert(str, arguments[i], 'label'); i += 1; remaining -= 1
        # the windowed *args-group slot `legs`
        take = remaining - 0
        sizes = greedy_sizes(take, 1, 1, 'legs', None)
        windows = window_options(
            {'--fast': fast_occurrences} if fast_occurrences else {},
            i, sizes, 'legs', None)
        a_legs = []
        for index, count in enumerate(sizes):
            window = windows[index]
            a_distance = convert(float, arguments[i], 'distance'); i += 1
            v_fast = window.get('--fast', False)
            a_legs.append(self.callable.__annotations__['legs'](
                a_distance, fast=v_fast))
        remaining -= take
        return self.callable(a_label, *tuple(a_legs))


# ====================================================================
#  SCOPED  --  scoped_cmd(a, b: child = None, c: child = None)
#  child(p=0, q=1, *, flag=False) reused across b and c, sharing --flag
# ====================================================================
def child(p=0, q=1, *, flag=False):
    return ('child', p, q, flag)

def scoped_cmd(a, b: child = None, c: child = None):
    return ('scoped_cmd', a, b, c)


class Command_scoped_cmd:
    name = 'scoped_cmd'
    arguments = ((1, 6),)
    options = {'--flag': ('--flag', (0, 1)), '-f': ('--flag', (0, 1))}
    callable = staticmethod(scoped_cmd)

    count_options = (2, 1, 0)                   # b and c both
    suffixes = {'b': frozenset({0, 1, 2}), 'c': frozenset({0})}

    def __call__(self, stream):
        arguments = []
        flag_occurrences = []
        for item in stream:
            if isinstance(item, str):
                arguments.append(item)
            else:
                if item[0] == '--flag':
                    value = (item[1] == 'true') if len(item) > 1 else True
                    flag_occurrences.append((len(arguments), 'flag', value))
        n = len(arguments)
        scopes = scopes_for({'--flag': 'last'},
                            {'--flag': flag_occurrences}
                            if flag_occurrences else {})

        # ScopedQueue can't bind in one forward pass: a window's extent
        # depends on where the NEXT window opens (and the last window
        # runs to end-of-line), so the structural pass records every
        # window first, resolve() assigns the occurrences, then the live
        # pass converts operands and pops each window's values.
        def walk(dry):
            i = 0
            remaining = n
            out = {}
            if not dry:
                out['a'] = convert(str, arguments[i], 'a')
            i += 1
            remaining -= 1
            for slot in ('b', 'c'):
                suffix = self.suffixes[slot]
                for count in self.count_options:
                    if count <= remaining and (remaining - count) in suffix:
                        chosen = count
                        break
                forced = False
                if chosen == 0:
                    if scoped_forces(scopes, ('--flag',)):
                        forced = True
                    else:
                        if not dry:
                            out[slot] = None
                        continue
                scoped_window(scopes, ['--flag'], 'in', i, forced)
                p_arg = q_arg = None
                if chosen >= 1:
                    if not dry:
                        p_arg = convert(int, arguments[i], 'p')
                    i += 1
                if chosen >= 2:
                    if not dry:
                        q_arg = convert(int, arguments[i], 'q')
                    i += 1
                remaining -= chosen
                scoped_window(scopes, ['--flag'], 'out', i)
                if not dry:
                    values = scoped_next(scopes, '--flag')
                    flag = values[-1] if values else False
                    out[slot] = self.callable.__annotations__['b'](
                        p_arg if p_arg is not None else 0,
                        q_arg if q_arg is not None else 1, flag=flag)
            return out

        walk(dry=True)
        scoped_resolve(scopes, None)
        scoped_rewind(scopes)
        out = walk(dry=False)
        return self.callable(out['a'], out.get('b'), out.get('c'))


# ====================================================================
#  SIBLING  --  sib(a, *, e1: extras = None, e2: extras = None)
#  extras(*, verbose=False, label='') -- e1/e2 announced by --e1/--e2,
#  the shared child options bind by ANNOUNCEMENT (declaration order),
#  and with no announcement at all they summon the first sibling.
# ====================================================================
def extras(*, verbose=False, label=''):
    return ('extras', verbose, label)

def sib(a, *, e1: extras = None, e2: extras = None):
    return ('sib', a, e1, e2)


class Command_sib:
    name = 'sib'
    arguments = ((1, 2),)
    # e1/e2 are group options (announce, 0 opargs); verbose is a flag
    # (0 opargs); label is a value (1 oparg).
    options = {
        '-e': ('--e1', (0, 1)), '--e1': ('--e1', (0, 1)),
        '--e2': ('--e2', (0, 1)),
        '-v': ('--verbose', (0, 1)), '--verbose': ('--verbose', (0, 1)),
        '-l': ('--label', (1, 2)), '--label': ('--label', (1, 2)),
    }
    callable = staticmethod(sib)

    sibling_parents = (('--e1', ('--label', '--verbose', '-l', '-v')),
                       ('--e2', ('--label', '--verbose')))

    def __call__(self, stream):
        arguments = []
        given = {}
        positions = {}
        seq = 0
        for item in stream:
            if isinstance(item, str):
                arguments.append(item)
                continue
            key = item[0]
            seq += 1
            if key in ('--e1', '--e2'):         # a sibling parent: announce
                positions[('seq', key)] = seq
                positions.setdefault(key, len(arguments))
                given.setdefault(key, ())       # group value: no operands
            elif key == '--verbose':
                value = (item[1] == 'true') if len(item) > 1 else True
                given.setdefault(key, []).append((seq, 'flag', value))
            elif key == '--label':
                given.setdefault(key, []).append((seq, 'value', item[1]))
        a = convert(str, arguments[0], 'a')

        # bind the shared child options by announcement
        specs = {'--verbose': 'last', '--label': 'last'}
        scopes, summon = sibling_scopes(self.sibling_parents, specs, given,
                                        positions, None, summonable=True)

        def build_parent(parent_key):
            if not (parent_key in given or summon == parent_key):
                return None
            # pop this window's claimed child options, in child order
            verbose = False
            label = ''
            if scopes:
                popped = scoped_next(scopes, '--verbose')
                if popped:
                    verbose = popped[-1]
                popped = scoped_next(scopes, '--label')
                if popped:
                    label = convert(str, popped[-1], 'label')
            name = 'e1' if parent_key == '--e1' else 'e2'
            return self.callable.__annotations__[name](verbose=verbose,
                                                       label=label)

        e1 = build_parent('--e1')
        e2 = build_parent('--e2')
        return self.callable(a, e1=e1, e2=e2)


# ====================================================================
commands = {
    'route': Command_route,
    'scoped_cmd': Command_scoped_cmd,
    'sib': Command_sib,
}
# prototype-only: names -> function, to build the Appeal oracle we diff
# against.  Nothing like this exists in production.
functions = {'route': route, 'scoped_cmd': scoped_cmd, 'sib': sib}


def compare(name, argv):
    fn = functions[name]
    app = appeal.Appeal(); app.command(name)(fn)
    try:
        mine = ('ok', codegen2.dispatch([name] + argv, {name: commands[name]}))
    except appeal.AppealError as e:
        mine = ('err', str(e)[:34])
    try:
        ref = ('ok', app.process([name] + argv))
    except appeal.AppealError as e:
        ref = ('err', str(e)[:34])
    ok = mine == ref or (mine[0] == 'err' and ref[0] == 'err')
    print(f"  {'OK ' if ok else 'XX '}{name} {argv}")
    if not ok:
        print(f"        mine={mine}\n        ref ={ref}")
    return ok


def main():
    total = passed = 0
    print("=== WINDOWED ===")
    for argv in (['a', '1', '2', '3'], ['a', '1', '--fast', '2', '3'],
                 ['a', '1', '2', '3', '--fast'], ['a', '-f', '1']):
        total += 1; passed += compare('route', argv)
    print("=== SCOPED ===")
    for argv in (['A'], ['A', '--flag'], ['A', '1', '2', '--flag'],
                 ['A', '--flag', '1', '2'],
                 ['A', '--flag', '1', '2', '--flag', '3', '4'],
                 ['A', '1', '2', '3', '4', '--flag'], ['A', '1', '2', '3']):
        total += 1; passed += compare('scoped_cmd', argv)
    print("=== SIBLING ===")
    for argv in (['x'], ['x', '--verbose'], ['x', '--label', 'hi'],
                 ['x', '--e1', '--verbose'], ['x', '--e2', '--label', 'q'],
                 ['x', '--e1', '--verbose', '--e2', '--label', 'q'],
                 ['x', '--e1', '-v', '-l', 'a', '--e2', '-v']):
        total += 1; passed += compare('sib', argv)
    print(f"\n{passed}/{total} match the reference engine")


if __name__ == '__main__':
    main()
