#!/usr/bin/env python3
#
# tests/test_all.py
# Tests for Appeal v2.
#
# Three layers of protection:
#   * the plan/build layer, tested directly;
#   * PARITY: every (signature, argv) case runs through both the
#     rung-1 interpreter and the rung-3 generated parser, and they must
#     agree--same result or same UsageError message;
#   * the NORTH STAR: standalone scripts are generated, written to
#     disk, and executed in a subprocess with no access to appeal,
#     appeal2, or big--and they must work.

def preload_local_appeal():
    from os.path import abspath, dirname, isfile, join, normpath
    import sys
    here = abspath(dirname(sys.argv[0] or '.'))
    candidate = here
    while True:
        if isfile(join(candidate, "appeal/plan.py")):   # v2 marker; dodges site-packages v1
            break
        parent = normpath(join(candidate, ".."))
        if parent == candidate:
            raise RuntimeError("couldn't find the appeal v2 tree")
        candidate = parent
    sys.path.insert(0, candidate)
    return candidate

repo_dir = preload_local_appeal()

import os.path
import subprocess
import sys
import tempfile

try:
    import atest
except ImportError:
    from tests import atest

import appeal
from appeal import (
    Appeal, AppealConfigurationError, AppealError, UsageError,
    build, compile_plan, emit_standalone, interpreter_parse,
    )


# ---------------------------------------------------------------------
# commands under test

def hello(name, greeting='hello'):
    return f'{greeting}, {name}!'

def serve(host, port: int = 8080, *, verbose=False, retries: int = 1, config=''):
    return (host, port, verbose, retries, config)

def cp(*src, dst):
    return (src, dst)

def add(a: int, b: int):
    return a + b

def middle(a, b='B', c='C', d=None):
    # note: no annotation + default None -> str converter
    return (a, b, c, d)

def skipper(a, b='B', *, z):
    # trailing operand + middle optional: reservation, not promotion
    return (a, b, z)


# converters (nonterminals) for the recursion tests

def pair(x, y):
    return (x, y)

def rgb(r: int, g: int, b: int):
    return (r, g, b)

def sized(w: int, h: int = 10):
    # a nonterminal with its own optional slot: counts {1, 2}
    return (w, h)

def move(start: pair, end: pair):
    return (start, end)

def stroke(color: rgb, width: float = 1.0):
    return (color, width)

def draw(shape, where: move, brush: stroke=None):
    return (shape, where, brush)

def ambig(a='A', p: pair='P'):
    # THE counting-automaton case: two operands must skip a and
    # fill pair (taking a would strand one operand)
    return (a, p)


# ---------------------------------------------------------------------
# the plan/build layer

def test_plan_basics():
    plan = build(hello)
    assert plan.name == 'hello'
    assert len(plan.slots) == 2
    assert plan.slots[0].required
    assert not plan.slots[1].required
    assert plan.minimum == 1
    assert plan.maximum == 2
    assert plan.valid_counts == {1, 2}

def test_plan_star_args():
    plan = build(cp)
    assert plan.minimum == 1          # dst
    assert plan.maximum is None
    assert plan.valid_counts is None
    assert plan.slots[0].repeat
    assert plan.slots[1].trailing

def test_plan_options():
    plan = build(serve)
    strings = sorted(s for o in plan.options for s in o.strings)
    # every option gets an auto-short: its first letter, if free
    assert strings == ['--config', '--retries', '--verbose', '-c', '-r', '-v']
    flags = {o.name: o.is_flag for o in plan.options}
    assert flags == {'verbose': True, 'retries': False, 'config': False}

def test_short_options_first_declared_wins():
    def f(*, verbose=False, value=3):
        return (verbose, value)
    plan = build(f)
    strings = {o.name: o.strings for o in plan.options}
    assert strings['verbose'] == ('-v', '--verbose')
    assert strings['value'] == ('--value',)      # -v was taken
    got = run_both(f, ['-v'])
    assert got == ('ok', (True, 3)), got
    got = run_both(f, ['--value', '5'])
    assert got == ('ok', (False, 5)), got

def test_option_string_collision_is_config_error():
    def sub(x=1, *, verbose=False):
        return (x, verbose)
    def f(a: sub=None, *, verbose=False):
        return (a, verbose)
    try:
        build(f)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert '--verbose' in str(e)

def test_annotated_is_the_one_blessed_typing_import():
    from typing import Annotated
    def double(text):
        return int(text) * 2
    def f(n: Annotated[int, double], *, scale: Annotated[float, float] = 1.0):
        return (n, scale)
    got = run_both(f, ['21'])
    assert got == ('ok', (42, 1.0)), got
    got = run_both(f, ['21', '--scale', '2.5'])
    assert got == ('ok', (42, 2.5)), got

def test_tuple_slots():
    def f(point: tuple[int, int], label='here'):
        return (point, label)
    got = run_both(f, ['3', '4'])
    assert got == ('ok', ((3, 4), 'here')), got
    got = run_both(f, ['3', '4', 'there'])
    assert got == ('ok', ((3, 4), 'there')), got
    got = run_both(f, ['3'])
    assert got[0] == 'usage', got

def test_tuple_slot_distribution():
    # the automaton sees a tuple slot as arity (2, 2); the counting
    # decision skips the optional to keep the count completable
    def g(a='A', p: tuple[int, str] = ()):
        return (a, p)
    got = run_both(g, [])
    assert got == ('ok', ('A', ())), got
    got = run_both(g, ['1', 'x'])
    assert got == ('ok', ('A', (1, 'x'))), got
    got = run_both(g, ['hello', '1', 'x'])
    assert got == ('ok', ('hello', (1, 'x'))), got
    got = run_both(g, ['only'])          # one operand: a takes it, p skips
    assert got == ('ok', ('only', ())), got

def test_tuple_slot_element_converters():
    def _pair(x, y):
        return f'{x}+{y}'
    def f(t: tuple[int, _pair]):
        return t
    got = run_both(f, ['1', 'a', 'b'])
    assert got == ('ok', (1, 'a+b')), got

def test_tuple_refusals_are_named():
    def variadic(t: tuple[int, ...]):
        pass
    try:
        build(variadic)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'variable-length' in str(e) and '*args' in str(e)
    def variadic_option(*, t: tuple[int, ...] = ()):
        pass
    try:
        build(variadic_option)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'list[T]' in str(e)

def test_tuple_options():
    # a tuple annotation on an option: a multi-operand value option
    # that builds a tuple--same machinery as a multi-param
    # converter, nothing to call
    def f(*, span: tuple[int, int] = (), name: tuple[str, int] = None):
        return (span, name)
    got = run_both(f, [])
    assert got == ('ok', ((), None)), got
    got = run_both(f, ['--span', '3', '4'])
    assert got == ('ok', ((3, 4), None)), got
    got = run_both(f, ['-s', '1', '2', '--name', 'x', '9'])
    assert got == ('ok', ((1, 2), ('x', 9))), got
    got = run_both(f, ['--span', '3'])
    assert got[0] == 'usage' and '2 values' in got[1], got
    got = run_both(f, ['--span', '3', '4', '--span', '5', '6'])
    assert got[0] == 'usage' and 'more than once' in got[1], got

def test_app_option_overrules_auto_strings():
    # v1 semantics, by ruling: @app.option BLOWS AWAY all default
    # mappings for the parameter and maps ONLY what you specify
    app = Appeal()
    @app.option('verbose', '-V', '--noisy', default=False)
    @app.global_command()
    def f(*, verbose=False, value=3):
        return (verbose, value)
    strings = {o.name: o.strings for o in app.plan.options}
    assert strings['verbose'] == ('-V', '--noisy')
    # ...which frees up -v for value (first-letter rule, letter now unclaimed)
    assert strings['value'] == ('-v', '--value')
    got = run_both(f, ['-V'])
    assert got == ('ok', (True, 3)), got
    got = run_both(f, ['--noisy', '-v', '5'])
    assert got == ('ok', (True, 5)), got
    got = run_both(f, ['--verbose'])
    assert got[0] == 'usage', got

def test_app_option_suppresses_auto_short():
    # the motivating case for blow-away semantics: the only kwarg
    # starting with 'q', and the user does NOT want -q
    app = Appeal()
    @app.option('quiet', '--quiet', default=False)
    @app.global_command()
    def f(*, quiet=False):
        return quiet
    (option,) = app.plan.options
    assert option.strings == ('--quiet',)      # no -q, nothing else
    assert app.process(['--quiet']) is True
    try:
        app.process(['-q'])
        assert False, 'expected UsageError'
    except UsageError as e:
        assert '-q' in str(e)

def test_app_option_is_a_fresh_declaration():
    # v1 semantics, probed: the parameter's own annotation and
    # default do NOT leak into @app.option's grammar.  A bare
    # @app.option makes a plain str value option--even on a bool
    # parameter--and the parameter's default still fills when the
    # option is absent.
    app = Appeal()
    @app.option('verbose', '-V')
    @app.global_command()
    def f(*, verbose=False):
        return verbose
    assert app.process(['-V', 'chatty']) == 'chatty'
    assert app.process([]) is False            # parameter's default
    try:
        app.process(['-V'])
        assert False, 'expected UsageError'
    except UsageError as e:
        assert 'value' in str(e)

def test_app_option_overrides_annotation_and_default():
    # @option's (annotation, default) pair declares the option's
    # GRAMMAR (converter, flag-ness); the value when absent is
    # always the parameter's own default (v1: ungiven kwargs
    # simply aren't passed)
    app = Appeal()
    @app.option('level', '--level', '-L', annotation=int)
    @app.global_command()
    def f(*, level='low'):
        return level
    assert app.process([]) == 'low'                # parameter's default
    assert app.process(['--level', '5']) == 5      # @option's annotation
    assert app.process(['-L', '7']) == 7
    # ...and with no annotation, the converter is inferred from
    # @option's default (type-of-default, v1's rule)
    app2 = Appeal()
    @app2.option('k', '--kay', default=5)
    @app2.global_command()
    def g(*, k=None):
        return k
    assert app2.process([]) is None
    assert app2.process(['--kay', '9']) == 9

def test_app_option_decorators_stack():
    # several strings in ONE call share a declaration...
    app = Appeal()
    @app.option('verbose', '-V', '--noisy', default=False)
    @app.global_command()
    def f(*, verbose=False):
        return verbose
    (option,) = app.plan.options
    assert sorted(option.strings) == ['--noisy', '-V']
    assert app.process(['-V']) is True
    assert app.process(['--noisy']) is True
    # ...but each @app.option CALL is its own declaration (v1: go2's
    # --north and --south map one parameter through different
    # converters)--including zero-argument converters, which are
    # flags that produce a value
    def north(): return 'north'
    def south(): return 'south'
    app2 = Appeal()
    @app2.option('direction', '--north', annotation=north)
    @app2.option('direction', '--south', annotation=south)
    @app2.global_command()
    def go2(*, direction='none'):
        return direction
    assert app2.process([]) == 'none'
    assert app2.process(['--north']) == 'north'
    assert app2.process(['--south']) == 'south'
    got = run_both(go2, ['--north'])   # hmm: run_both builds from fn attrs
    assert got == ('ok', 'north'), got

def test_app_option_errors_are_named():
    app = Appeal()
    # bad option string shapes, v1's rule
    for bad in ('verbose', '-xy', '--x', '-'):
        try:
            @app.option('verbose', bad)
            def f(*, verbose=False): pass
            assert False, f'expected AppealConfigurationError for {bad!r}'
        except AppealConfigurationError as e:
            assert bad in str(e), (bad, str(e))
    # naming a parameter that isn't a keyword-only-with-default
    @app.option('nonesuch', '--nonesuch')
    @app.global_command()
    def g(x, *, verbose=False):
        return (x, verbose)
    try:
        app.plan
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'nonesuch' in str(e)

def test_annotated_is_dereferenced_everywhere():
    # the documented contract (v1 README): "Appeal only ever uses
    # the *last* value"--and it must hold at EVERY annotation site
    from typing import Annotated
    def upper(s): return s.upper()
    def halve(s): return int(s) // 2
    def f(a: Annotated[int, halve],                      # positional
          t: tuple[Annotated[int, halve], str] = None,   # tuple element
          *args: Annotated[str, upper],                  # *args
          dst: Annotated[str, upper],                    # trailing operand
          opt: Annotated[int, halve] = 0,                # option
          inc: list[Annotated[str, upper]] = (),         # list element
          dmap: dict[Annotated[str, upper],              # dict key
                     Annotated[int, halve]] = None,      # dict value
          ):
        return (a, t, args, dst, opt, inc, dmap)
    got = run_both(f, ['8', '2', 'x', 'mid', 'end',
                       '--opt', '10', '--inc', 'ab', '--dmap', 'k=8'])
    assert got == ('ok', (4, (1, 'x'), ('MID',), 'END', 5, ['AB'], {'K': 4})), got

def test_annotated_via_app_option_override():
    # the ninth site: @app.option's annotation= flows through the
    # same dereference as a parameter's own annotation
    from typing import Annotated
    def halve(s): return int(s) // 2
    app = Appeal()
    @app.option('level', '--level', annotation=Annotated[int, halve])
    @app.global_command()
    def f(*, level=0):
        return level
    assert app.process(['--level', '10']) == 5

def test_simple_converters_on_star_args_and_trailing():
    def upper(s): return s.upper()
    def f(*src: upper, dst: upper):
        return (src, dst)
    got = run_both(f, ['a', 'b', 'z'])
    assert got == ('ok', (('A', 'B'), 'Z')), got

def test_appeal_is_lazy():
    # nothing happens at decoration time: a config error surfaces
    # at first *use*, not when the decorator runs
    app = Appeal()
    @app.global_command()
    def f(t: tuple[int, ...]):  # variable-length tuples aren't in the grammar
        pass
    try:
        app.process([])
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'tuple' in str(e)

def test_commands_compile_independently():
    # laziness is per command: dispatching one never builds the
    # others, so a broken sibling costs nothing until it's used
    app = Appeal(name='tool')
    @app.command()
    def good(x):
        return ('good', x)
    @app.command()
    def broken(t: tuple[int, ...]):   # not in the grammar
        pass
    assert app.process(['good', 'hi']) == ('good', 'hi')
    assert app.process(['good', 'again']) == ('good', 'again')
    try:
        app.process(['broken'])
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'tuple' in str(e)
    # ...but a whole-program artifact is necessarily eager:
    # standalone emission must build (and refuse) everything
    try:
        app.standalone()
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'tuple' in str(e)

def test_app_option_works_in_either_decorator_order():
    def make(order):
        app = Appeal()
        if order == 'option-on-top':
            @app.option('verbose', '-V', default=False)
            @app.global_command()
            def f(*, verbose=False):
                return verbose
        else:
            @app.global_command()
            @app.option('verbose', '-V', default=False)
            def f(*, verbose=False):
                return verbose
        return app
    for order in ('option-on-top', 'command-on-top'):
        app = make(order)
        assert app.process(['-V']) is True, order
        (option,) = app.plan.options
        assert option.strings == ('-V',), (order, option.strings)

def test_nested_generics_are_refused_by_name():
    # list/dict are option-repetition spellings, not operand shapes;
    # nesting them where they have no command-line meaning is a
    # named config error at every level
    def f(t: tuple[list[dict[str, str]], int]):
        pass
    try:
        build(f)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'option' in str(e)
    def g(*, inc: list[list[int]] = ()):
        pass
    try:
        build(g)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'generic' in str(e)

def test_multioption_fold():
    # v1's MultiOption protocol, probed and kept: init(default)
    # once, option(...) per occurrence (its signature defines the
    # per-occurrence operands, converted), render() at the end
    from appeal import MultiOption

    class Tags(MultiOption):
        def init(self, default):
            self.values = list(default) if default else []
        def option(self, tag):
            self.values.append(tag)
        def render(self):
            return tuple(self.values)

    class Points(MultiOption):
        def init(self, default):
            self.pts = []
        def option(self, x: int, y: int):
            self.pts.append((x, y))
        def render(self):
            return self.pts

    def f(x, *, tag: Tags = ('seed',), pt: Points = None):
        return (x, tag, pt)

    # expected values are the OUTPUTS OF SHIPPING v1, probed
    got = run_both(f, ['op'])
    assert got == ('ok', ('op', ('seed',), None)), got     # absent: class never instantiated
    got = run_both(f, ['op', '--tag', 'a', '--tag', 'b'])
    assert got == ('ok', ('op', ('seed', 'a', 'b'), None)), got
    got = run_both(f, ['op', '-t', 'c'])                   # auto-short works
    assert got == ('ok', ('op', ('seed', 'c'), None)), got
    got = run_both(f, ['op', '--tag=eq'])
    assert got == ('ok', ('op', ('seed', 'eq'), None)), got
    got = run_both(f, ['op', '--pt', '3', '4', '--pt', '5', '6'])
    assert got == ('ok', ('op', ('seed',), [(3, 4), (5, 6)])), got
    got = run_both(f, ['op', '--pt', '3'])                 # starved occurrence
    assert got[0] == 'usage' and '2 values' in got[1], got
    got = run_both(f, ['op', '--pt=3'])                    # '=' needs arity 1
    assert got[0] == 'usage', got
    got = run_both(f, ['op', '--pt', 'x', 'y'])            # conversion errors
    assert got[0] == 'usage', got

def test_multioption_zero_arity():
    from appeal import MultiOption
    class Verbosity(MultiOption):
        def init(self, default):
            self.level = default
        def option(self):
            self.level += 1
        def render(self):
            return self.level
    def f(*, v: Verbosity = 0):
        return v
    got = run_both(f, [])
    assert got == ('ok', 0), got
    got = run_both(f, ['-v', '-v', '--v-2'])
    assert got[0] == 'usage', got                          # bogus option sanity
    got = run_both(f, ['-v', '-v', '-v'])
    assert got == ('ok', 3), got
    got = run_both(f, ['-vvv'])                            # bundles: takes no value
    assert got == ('ok', 3), got

def test_gate_rule():
    # Larry's ruling: a REQUIRED group is a wall--options of
    # skippable things behind it wait until it has been fed
    def size(width: float, *, bold=False):
        return (width, bold)
    def color(name='black', *, bright=False):
        return (name, bright)
    def draw(shape, s: size, c: color = None):     # s required: a wall
        return (shape, s, c)
    got = run_both(draw, ['dot', '2.5', '--bright'])
    assert got == ('ok', ('dot', (2.5, False), ('black', True))), got
    got = run_both(draw, ['dot', '--bright', '2.5'])
    assert got[0] == 'usage' and 'too early' in got[1], got
    got = run_both(draw, ['--bright', 'dot', '2.5'])
    assert got[0] == 'usage' and 'too early' in got[1], got
    # ...but the wall's OWN options float free: size is coming no
    # matter what, so --bold announces it (v1, probed)
    got = run_both(draw, ['--bold', 'dot', '2.5'])
    assert got == ('ok', ('dot', (2.5, True), None)), got

def test_gate_skippable_groups_never_gate():
    # size itself skippable (though hungry if it appears): no wall,
    # leap-over stays spellable (Larry: "walls are required
    # parameters")
    def size(width: float, *, bold=False):
        return (width, bold)
    def color(name='black', *, bright=False):
        return (name, bright)
    def draw(shape, s: size = None, c: color = None):
        return (shape, s, c)
    got = run_both(draw, ['dot', '--bright'])
    assert got == ('ok', ('dot', None, ('black', True))), got
    got = run_both(draw, ['--bright', 'dot'])
    assert got == ('ok', ('dot', None, ('black', True))), got

def test_gate_certain_nested_options_float():
    # caught by the differential fuzz: v1 lets a required group's
    # options appear anywhere, even nested behind other unfed
    # walls--they announce something certain to exist
    def pair(x: float, y: float):
        return (x, y)
    def sub(v: float, *, mark=False):
        return (v, mark)
    def color(name='black', *, bright=False):
        return (name, bright)
    def f(p: pair, b: sub, c: color = None):
        return (p, b, c)
    got = run_both(f, ['--mark', '1', '2', '3'])       # b's own, early: fine
    assert got == ('ok', ((1.0, 2.0), (3.0, True), None)), got
    got = run_both(f, ['--bright', '1', '2', '3'])     # c is skippable: gated
    assert got[0] == 'usage' and 'too early' in got[1], got
    got = run_both(f, ['1', '2', '3', '--bright'])     # both walls fed
    assert got == ('ok', ((1.0, 2.0), (3.0, False), ('black', True))), got

def test_gate_and_windows():
    def pair(x: float, y: float):
        return (x, y)
    def seg(length: float, *, dashed=False):
        return (length, dashed)
    def path(p: pair, *segs: seg):
        return (p, segs)
    got = run_both(path, ['1', '2', '3', '--dashed'])
    assert got == ('ok', ((1.0, 2.0), ((3.0, True),))), got
    got = run_both(path, ['--dashed', '1', '2', '3'])  # before the wall
    assert got[0] == 'usage' and 'too early' in got[1], got

def test_star_args_option_windows():
    # v1 semantics, probed: a converter group on *args gets one
    # instance per fixed-size chunk of operands, and the group's
    # options bind to the instance being built or ABOUT to be
    # built (forward binding)--an option at operand position p
    # configures instance (p - first) // arity
    def size(width: float, *, bold=False):
        return ('s', width, bold)
    def draw(shape, *sizes: size):
        return (shape, sizes)
    got = run_both(draw, ['a', '1', '2', '3'])
    assert got == ('ok', ('a', (('s', 1.0, False), ('s', 2.0, False), ('s', 3.0, False)))), got
    got = run_both(draw, ['a', '1', '--bold', '2', '3'])   # forward: #2
    assert got == ('ok', ('a', (('s', 1.0, False), ('s', 2.0, True), ('s', 3.0, False)))), got
    got = run_both(draw, ['a', '--bold', '1', '2'])        # #1
    assert got == ('ok', ('a', (('s', 1.0, True), ('s', 2.0, False)))), got
    got = run_both(draw, ['a', '--bold', '1', '--bold', '2'])
    assert got == ('ok', ('a', (('s', 1.0, True), ('s', 2.0, True)))), got
    # past the ends, the nearest size: with sizes A B C the four
    # positions 1 A 2 B 3 C 4 map to A, B, C, C (Larry's rule;
    # v1 errors on the trailing one--deliberate v2 improvement)
    got = run_both(draw, ['a', '1', '2', '3', '--bold'])
    assert got == ('ok', ('a', (('s', 1.0, False), ('s', 2.0, False), ('s', 3.0, True)))), got
    got = run_both(draw, ['a', '1', '--bold'])       # only size: it
    assert got == ('ok', ('a', (('s', 1.0, True),))), got
    # ...but an option with NO sizes at all has nothing to bind to
    got = run_both(draw, ['a', '--bold'])
    assert got[0] == 'usage' and 'at least one' in got[1], got
    got = run_both(draw, ['a', '1', '--bold', '--bold', '2'])
    assert got[0] == 'usage' and 'more than once' in got[1], got

def test_star_args_two_operand_windows():
    def size2(width: float, height: float, *, bold=False):
        return (width, height, bold)
    def draw2(shape, *sizes: size2):
        return (shape, sizes)
    got = run_both(draw2, ['a', '1', '--bold', '2', '3', '4'])   # mid-instance: #1
    assert got == ('ok', ('a', ((1.0, 2.0, True), (3.0, 4.0, False)))), got
    got = run_both(draw2, ['a', '1', '2', '--bold', '3', '4'])   # boundary: #2
    assert got == ('ok', ('a', ((1.0, 2.0, False), (3.0, 4.0, True)))), got
    got = run_both(draw2, ['a', '1', '2', '3'])                  # odd leftover
    assert got[0] == 'usage' and 'left over' in got[1], got

def test_star_args_group_value_option():
    def seg(length: float, *, label=''):
        return (length, label)
    def path(*segs: seg):
        return segs
    got = run_both(path, ['1', '--label', 'up', '2'])
    assert got == ('ok', ((1.0, ''), (2.0, 'up'))), got          # forward: #2
    got = run_both(path, ['--label', 'up', '1', '2'])
    assert got == ('ok', ((1.0, 'up'), (2.0, ''))), got

def test_star_args_group_refusals_are_named():
    def loose(width: float = 1.0, *, bold=False):
        return (width, bold)
    def draw(shape, *sizes: loose):
        pass
    try:
        build(draw)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'width' in str(e) and 'streaming driver' in str(e)
    def pair(x, y):
        return (x, y)
    def nested(where: pair, *, bold=False):
        return (where, bold)
    def draw2(shape, *sizes: nested):
        pass
    try:
        build(draw2)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'streaming driver' in str(e)

def test_multiparam_option_converters():
    # v1, probed: a plain converter with several parameters
    # consumes several operands per occurrence--no class needed
    def pair(x, y):
        return (x, y)
    def rgb(r: int, g: int, b: int):
        return (r, g, b)
    def f(*, where: pair = None, color: rgb = None):
        return (where, color)
    got = run_both(f, [])
    assert got == ('ok', (None, None)), got
    got = run_both(f, ['--where', 'a', 'b'])
    assert got == ('ok', (('a', 'b'), None)), got
    got = run_both(f, ['--color', '1', '2', '3'])
    assert got == ('ok', (None, (1, 2, 3))), got
    got = run_both(f, ['-w', 'a', 'b', '-c', '4', '5', '6'])
    assert got == ('ok', (('a', 'b'), (4, 5, 6))), got
    got = run_both(f, ['--where', 'a'])                    # starved
    assert got[0] == 'usage' and '2 values' in got[1], got
    got = run_both(f, ['--where=a'])                       # '=' needs arity 1
    assert got[0] == 'usage', got
    got = run_both(f, ['--color', '1', 'x', '3'])          # conversion error
    assert got[0] == 'usage', got
    got = run_both(f, ['--where', 'a', 'b', '--where', 'c', 'd'])
    assert got[0] == 'usage' and 'more than once' in got[1], got

def test_option_class_single_occurrence():
    # v1's Option: same protocol as MultiOption, but "specified
    # more than once" on repetition (probed--NOT last-wins)
    from appeal import Option

    class Where(Option):
        def init(self, default):
            self.x = self.y = None
        def option(self, x: int, y: int):
            self.x, self.y = x, y
        def render(self):
            return (self.x, self.y)

    def f(*, where: Where = 'nowhere'):
        return where

    got = run_both(f, [])
    assert got == ('ok', 'nowhere'), got            # never instantiated
    got = run_both(f, ['--where', '3', '4'])
    assert got == ('ok', (3, 4)), got
    got = run_both(f, ['--where', '1', '2', '--where', '5', '6'])
    assert got[0] == 'usage' and 'more than once' in got[1], got

def test_options_do_not_repeat():
    # v1 semantics, probed: flags and value options error when
    # repeated; only the repeatable kinds collect
    def f(*, num: int = 0, loud=False, tag: list[str] = ()):
        return (num, loud, tag)
    got = run_both(f, ['--loud', '--loud'])
    assert got[0] == 'usage' and 'more than once' in got[1], got
    got = run_both(f, ['--num', '1', '--num', '2'])
    assert got[0] == 'usage' and 'more than once' in got[1], got
    got = run_both(f, ['--tag', 'a', '--tag', 'b'])   # multi: fine
    assert got == ('ok', (0, False, ['a', 'b'])), got

def test_one_char_option_names_get_no_long_option():
    # v1, probed: parameter 'n' has only '-n'; '--n' is unknown
    def f(*, n: int = 0):
        return n
    plan = build(f)
    (option,) = plan.options
    assert option.strings == ('-n',), option.strings
    got = run_both(f, ['-n', '5'])
    assert got == ('ok', 5), got
    got = run_both(f, ['--n', '5'])
    assert got[0] == 'usage' and '--n' in got[1], got
    # ...and if the letter is already taken, the option has no
    # strings at all: a named config error
    def g(*, nope=False, n: int = 0):
        return (nope, n)
    try:
        build(g)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert "'n'" in str(e), e

def test_multioption_refusals_are_named():
    from appeal import MultiOption
    class Tags(MultiOption):
        def init(self, default): pass
        def option(self, tag): pass
        def render(self): pass
    # single-arity Option classes work positionally (v1's corpus:
    # mapping readers feed them sequences of occurrences)
    class Collect(MultiOption):
        def init(self, default):
            self.values = []
        def option(self, tag):
            self.values.append(tag)
        def render(self):
            return tuple(self.values)
    def positional(t: Collect):
        return t
    build(positional)
    from appeal import read_mapping
    assert read_mapping(positional, {'t': ['a', 'b']}) == ('a', 'b')
    # multi-arity ones are refused by name
    class Pairs(MultiOption):
        def init(self, default): pass
        def option(self, x, y): pass
        def render(self): pass
    def positional2(p: Pairs):
        pass
    try:
        build(positional2)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'multiple parameters' in str(e)
    class Fancy(MultiOption):
        def init(self, default): pass
        def option(self, tag='x'): pass
        def render(self): pass
    def f(*, t: Fancy = None):
        pass
    try:
        build(f)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'Fancy' in str(e) and 'default' in str(e)

def test_accumulator_option():
    def f(*, include: list[int] = ()):
        return include
    got = run_both(f, [])
    assert got == ('ok', ()), got
    got = run_both(f, ['--include', '1', '--include', '2', '-i', '3'])
    assert got == ('ok', [1, 2, 3]), got
    got = run_both(f, ['--include', 'potato'])
    assert got[0] == 'usage', got

def test_mapping_option():
    def f(*, define: dict[str, int] = None):
        return define
    got = run_both(f, [])
    assert got == ('ok', None), got
    got = run_both(f, ['--define', 'x=1', '-d', 'y=2'])
    assert got == ('ok', {'x': 1, 'y': 2}), got
    got = run_both(f, ['--define', 'nope'])
    assert got[0] == 'usage'
    assert 'KEY=VALUE' in got[1], got

def test_options_inside_converters():
    # the probe-verified v1 cases (and v2's one deliberate
    # liberalization: options are recognized anywhere on the line)
    def stroke(width: float=1.0, *, dashed=False):
        return (width, dashed)
    def draw(shape, s: stroke=None, *, verbose=False):
        return (shape, s, verbose)

    got = run_both(draw, ['dot'])
    assert got == ('ok', ('dot', None, False)), got
    got = run_both(draw, ['dot', '2.5', '--dashed'])
    assert got == ('ok', ('dot', (2.5, True), False)), got
    got = run_both(draw, ['dot', '--dashed', '2.5'])
    assert got == ('ok', ('dot', (2.5, True), False)), got
    # v2 liberalization: v1 rejected the option before the parent's
    # operands; v2's flat recognition accepts it
    got = run_both(draw, ['--dashed', 'dot', '2.5'])
    assert got == ('ok', ('dot', (2.5, True), False)), got
    # v1 semantics (differential fuzz caught v2 diverging): giving
    # a group's option FORCES the group, entered with zero operands,
    # defaults and all
    got = run_both(draw, ['dot', '--dashed'])
    assert got == ('ok', ('dot', (1.0, True), False)), got
    # ...but the *parent's* options don't force anything
    got = run_both(draw, ['dot', '--verbose'])
    assert got == ('ok', ('dot', None, True)), got
    # a forced group that NEEDS operands it can't have is still an
    # error, named after the option (v1 errors here too)
    def rgbstroke(r: int, g: int, b: int, *, dashed=False):
        return (r, g, b, dashed)
    def draw2(shape, s: rgbstroke=None):
        return (shape, s)
    got = run_both(draw2, ['dot', '--dashed'])
    assert got[0] == 'usage'
    assert "'s'" in got[1], got

def test_plan_trailing_does_not_promote():
    # reservation is not promotion: z is filled from the end, so
    # two operands mean (a, z), skipping b--unambiguous
    def f(a, b='B', *, z):
        return (a, b, z)
    plan = build(f)
    named = {s.name: s for s in plan.slots}
    assert not named['b'].required
    assert named['z'].trailing
    assert plan.valid_counts == {2, 3}

def test_plan_configuration_errors():
    def bad_order(*, opt='x', z):
        pass
    try:
        build(bad_order)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'z' in str(e)

    def bad_flag(*, verbose=True):
        pass
    try:
        build(bad_flag)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'verbose' in str(e)

    # **kwargs is legal now (it receives @app.option declarations);
    # bare, it just gets nothing
    def fine_kwargs(x, **kw):
        return (x, kw)
    assert build(fine_kwargs).minimum == 1


# ---------------------------------------------------------------------
# PARITY: rung 1 (interpreter) vs rung 3 (generated), forever

def run_both(command, argv):
    """
    Run argv through the interpreter and the generated parser.
    Returns ('ok', result) or ('usage', message)--and asserts
    the two rungs agree exactly.
    """
    plan = build(command)
    parse = compile_plan(plan)

    def run(fn):
        try:
            return ('ok', fn())
        except UsageError as e:
            return ('usage', str(e))

    a = run(lambda: interpreter_parse(plan, list(argv)))
    b = run(lambda: parse(list(argv)))
    assert a == b, f'parity failure on {command.__name__} {argv!r}:\n  interpreter: {a!r}\n  rung 3: {b!r}'
    return a

def run_both_set(commands, global_command, argv):
    """
    run_both for a multi-command program: the interpreter dispatch and
    the generated dispatcher must agree exactly.
    """
    from appeal import compile_command_set, interpreter_dispatch
    plans = {c.__name__: build(c) for c in commands}
    global_plan = build(global_command) if global_command else None
    parse = compile_command_set(plans, global_plan, prog='prog')

    def run(fn):
        try:
            return ('ok', fn())
        except UsageError as e:
            return ('usage', str(e))

    a = run(lambda: interpreter_dispatch(plans, global_plan, list(argv), prog='prog'))
    b = run(lambda: parse(list(argv)))
    assert a == b, f'dispatch parity failure on {argv!r}:\n  interpreter: {a!r}\n  rung 3: {b!r}'
    return a

PARITY_CASES = [
    (hello, ['world'],                      ('ok', 'hello, world!')),
    (hello, ['world', 'howdy'],             ('ok', 'howdy, world!')),
    (hello, [],                             None),   # usage error, don't care about exact text here
    (hello, ['a', 'b', 'c'],                None),
    (add,   ['2', '3'],                     ('ok', 5)),
    (add,   ['2', 'potato'],                None),
    (serve, ['localhost'],                  ('ok', ('localhost', 8080, False, 1, ''))),
    (serve, ['localhost', '9090'],          ('ok', ('localhost', 9090, False, 1, ''))),
    (serve, ['--verbose', 'localhost'],     ('ok', ('localhost', 8080, True, 1, ''))),
    (serve, ['localhost', '--retries', '5'], ('ok', ('localhost', 8080, False, 5, ''))),
    (serve, ['localhost', '--retries=5'],   ('ok', ('localhost', 8080, False, 5, ''))),
    (serve, ['localhost', '--config', '-'], ('ok', ('localhost', 8080, False, 1, '-'))),
    (serve, ['--bogus', 'localhost'],       None),
    (serve, ['localhost', '--retries'],     None),
    (serve, ['localhost', '--verbose=yes'], None),
    (cp,    ['a', 'b', 'dest'],             ('ok', (('a', 'b'), 'dest'))),
    (cp,    ['dest'],                       ('ok', ((), 'dest'))),
    (cp,    [],                             None),
    (middle, ['a'],                         ('ok', ('a', 'B', 'C', None))),
    (skipper, ['x', 'y'],                   ('ok', ('x', 'B', 'y'))),
    (skipper, ['x', 'mid', 'y'],            ('ok', ('x', 'mid', 'y'))),
    (skipper, ['x'],                        None),
    (middle, ['a', 'b', 'c', 'd'],          ('ok', ('a', 'b', 'c', 'd'))),
    # ---- converter recursion ----
    (move,  ['1', '2', '3', '4'],           ('ok', (('1', '2'), ('3', '4')))),
    (move,  ['1', '2', '3'],                None),
    (move,  ['1', '2'],                     None),
    (draw,  ['dot', 'a', 'b', 'c', 'd'],    ('ok', ('dot', (('a', 'b'), ('c', 'd')), None))),
    (draw,  ['dot', 'a', 'b', 'c', 'd', '9', '8', '7'],
            ('ok', ('dot', (('a', 'b'), ('c', 'd')), ((9, 8, 7), 1.0)))),
    (draw,  ['dot', 'a', 'b', 'c', 'd', '9', '8', '7', '2.5'],
            ('ok', ('dot', (('a', 'b'), ('c', 'd')), ((9, 8, 7), 2.5)))),
    (draw,  ['dot', 'a', 'b', 'c', 'd', '9'], None),
    (draw,  ['dot', 'a', 'b', 'c', 'd', '9', 'x', '7'], None),  # conversion error inside child
    # ---- the automaton's ambiguity resolution ----
    (ambig, [],                             ('ok', ('A', 'P'))),
    (ambig, ['q'],                          ('ok', ('q', 'P'))),
    (ambig, ['x', 'y'],                     ('ok', ('A', ('x', 'y')))),
    (ambig, ['q', 'x', 'y'],                ('ok', ('q', ('x', 'y')))),
    (ambig, ['1', '2', '3', '4'],           None),
]

def test_parity():
    for command, argv, expected in PARITY_CASES:
        got = run_both(command, argv)
        if expected is not None:
            assert got == expected, f'{command.__name__} {argv!r}: {got!r} != {expected!r}'
        else:
            assert got[0] == 'usage', f'{command.__name__} {argv!r}: expected a usage error, got {got!r}'

def test_command_dispatch():
    # v1 semantics, probed: the first operand names a command--the
    # LITERAL function name, no dash-mangling--even with only one
    # command registered
    def add_item(name, count: int = 1):
        return ('add', name, count)
    def remove(name, *, force=False):
        return ('remove', name, force)
    got = run_both_set([add_item, remove], None, ['add_item', 'widget', '3'])
    assert got == ('ok', ('add', 'widget', 3)), got
    got = run_both_set([add_item, remove], None, ['remove', 'w', '--force'])
    assert got == ('ok', ('remove', 'w', True)), got
    got = run_both_set([add_item, remove], None, ['add-item', 'widget'])
    assert got[0] == 'usage' and 'unknown command' in got[1], got
    got = run_both_set([add_item, remove], None, [])
    assert got == ('usage', 'no command specified.'), got

def test_global_command_dispatch():
    calls = []
    def config(project, *, verbose=False):
        calls.append((project, verbose))
    def run(target):
        return ('run', target)
    # the global command's options and operands come BEFORE the
    # command word; it is called even when none were given
    got = run_both_set([run], config, ['proj', '--verbose', 'run', 'x'])
    assert got == ('ok', ('run', 'x')), got
    got = run_both_set([run], config, ['proj', 'run', 'x'])
    assert got == ('ok', ('run', 'x')), got
    assert calls == [('proj', True), ('proj', True),     # run_both_set runs both rungs
                     ('proj', False), ('proj', False)], calls
    # a global option after the command word belongs to nobody (v1 agrees)
    got = run_both_set([run], config, ['proj', 'run', '--verbose', 'x'])
    assert got[0] == 'usage' and '--verbose' in got[1], got

def test_global_command_truthy_return_halts():
    # v1's contract, probed: a truthy global return is the result;
    # the command never runs
    def gate(*, fail=False):
        return 3 if fail else None
    ran = []
    def go(target):
        ran.append(target)
        return 0
    got = run_both_set([go], gate, ['--fail', 'go', 'x'])
    assert got == ('ok', 3), got
    assert ran == [], ran
    got = run_both_set([go], gate, ['go', 'x'])
    assert got == ('ok', 0), got
    assert ran == ['x', 'x'], ran      # both rungs

def test_flexible_global_operands():
    # the command word is the first operand naming a command, once
    # the global's minimum is satisfied (v1's greedy ate the word:
    # 'run x' became project='run'--a footgun, deliberately fixed)
    def g(project='default', *, verbose=False):
        return None
    seen = []
    def g2(project='default'):
        seen.append(project)
    def run(target):
        return ('run', target)
    got = run_both_set([run], g2, ['run', 'x'])          # word yields
    assert got == ('ok', ('run', 'x')), got
    assert seen == ['default', 'default'], seen          # both rungs
    got = run_both_set([run], g2, ['proj', 'run', 'x'])
    assert got == ('ok', ('run', 'x')), got
    assert seen[-2:] == ['proj', 'proj'], seen
    # over the maximum: the next operand is forced to be the word
    got = run_both_set([run], g2, ['a', 'b', 'x'])
    assert got[0] == 'usage' and 'unknown command' in got[1], got

def test_flexible_global_star_args():
    tags = []
    def g(*seen):
        tags.append(seen)
    def go(target):
        return ('go', target)
    got = run_both_set([go], g, ['a', 'b', 'go', 'x'])
    assert got == ('ok', ('go', 'x')), got
    assert tags[-1] == ('a', 'b'), tags
    got = run_both_set([go], g, ['go', 'x'])
    assert got == ('ok', ('go', 'x')), got
    assert tags[-1] == (), tags

def test_flexible_global_converter_group():
    def pair(x: int, y: int):
        return (x, y)
    def g(p: pair = None):
        return 3 if p == (9, 9) else None    # truthy return halts
    def run(target):
        return ('run', target)
    got = run_both_set([run], g, ['1', '2', 'run', 'x'])
    assert got == ('ok', ('run', 'x')), got
    got = run_both_set([run], g, ['run', 'x'])           # group skipped
    assert got == ('ok', ('run', 'x')), got
    got = run_both_set([run], g, ['9', '9', 'run', 'x'])
    assert got == ('ok', 3), got                          # halt still works
    got = run_both_set([run], g, ['1', 'run', 'x'])      # 1 ∉ {0, 2}
    assert got[0] == 'usage', got

def test_appeal_facade_dispatch():
    app = Appeal(name='tool')
    @app.global_command()
    def config(*, trace=False):
        return None
    @app.command()
    def greet(name):
        return f'hi, {name}'
    @app.command()
    def add(a: int, b: int):
        return a + b
    assert app.process(['greet', 'world']) == 'hi, world'
    assert app.process(['--trace', 'add', '2', '3']) == 5
    import contextlib, io
    with contextlib.redirect_stderr(io.StringIO()) as err:
        assert app.main(['bogus']) == 2
        assert app.main([]) == 2
    assert 'unknown command' in err.getvalue()
    assert 'no command specified.' in err.getvalue()

def run_both_stdout(command, argv):
    "run_both for parses that print (--help): compare text too."
    import contextlib, io
    plan = build(command)
    parse = compile_plan(plan)
    results = []
    for fn in (lambda: interpreter_parse(plan, list(argv)),
               lambda: parse(list(argv))):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = fn()
        results.append((result, out.getvalue()))
    assert results[0] == results[1], f'help parity failure on {argv!r}'
    return results[0]

def test_help_flag():
    # automatic -h/--help (v1, probed): usage line first, then the
    # docstring re-wrapped; exits successfully; never in usage text
    def size(width: float, *, bold=False):
        return (width, bold)
    def draw(shape, s: size = None, *, verbose=False):
        """
        Draws a shape.

        A very long second paragraph that will definitely need to be
        rewrapped by the embedded formatter because it rambles on at
        considerable length about nothing whatsoever, artisanally.

            indented code paragraphs pass through intact
        """
        return (shape, s, verbose)
    for argv in (['--help'], ['-h'], ['--help', 'ignored', 'operands']):
        result, text = run_both_stdout(draw, argv)
        assert result is None
        # the page opens with the summary; usage follows
        assert text.splitlines()[0] == 'Draws a shape.', text
        assert 'usage: draw [-v|--verbose] shape [[-b|--bold] width]' in text
        assert '--help' not in text                  # never advertised (v1)
        assert '    indented code paragraphs pass through intact' in text
        assert max(len(line) for line in text.splitlines()) <= 79
    # help wins even when required operands are missing
    result, text = run_both_stdout(draw, ['--help'])
    assert result is None and text.startswith('Draws a shape.')

def test_usage_metavars_and_wrapping():
    # symbolic names render as <int>-style metavars, and a long
    # usage line wraps at WHOLE units--'[-t|--times <int>]' can
    # never be split--with continuations aligned under the first
    def connect(host, port: int = 8080, *,
                times: int = 1, retries: int = 3, timeout: float = 30.0,
                certificate='', verbose=False, compression='gzip'):
        "Connects somewhere."
        pass
    plan = build(connect)
    usage = plan.usage()
    assert '[-t|--times <int>]' in usage, usage
    assert '[--timeout <float>]' in usage, usage
    assert '[-c|--certificate <certificate>]' in usage, usage
    result, text = run_both_stdout(connect, ['--help'])
    lines = text.splitlines()
    usage_at = next(i for i, l in enumerate(lines) if l.startswith('usage: connect '))
    assert lines[usage_at + 1].startswith(' ' * len('usage: '))   # aligned
    for line in lines:
        assert len(line) <= 79, line
        assert line.count('[') == line.count(']'), line    # units whole

def test_help_parameter_sections():
    # `name: description` docstring lines become argument/option
    # tables (labels from the plan: option strings, usage names);
    # names that match no parameter stay prose
    def size(width: float, *, bold=False):
        return (width, bold)
    def draw(shape, s: size = None, *, verbose=False, times: int = 1):
        """
        Draws a shape.

        Note: this line is prose, not a parameter.

        Arguments:
          shape: the shape to draw.  A longer description that will
              need wrapping when rendered into the table's column.
          width: how wide.

        Options:
          verbose: narrate the process.
          times: how many times.
        """
        pass
    result, text = run_both_stdout(draw, ['--help'])
    assert result is None
    assert 'Note: this line is prose, not a parameter.' in text
    assert 'Arguments:' in text and 'Options:' in text
    assert '    shape  the shape to draw.' in text
    assert '    width  how wide.' in text
    assert '    -v|--verbose      narrate the process.' in text
    assert '    -t|--times <int>  how many times.' in text
    body = text.split('Arguments:')[0]
    assert 'shape:' not in body                       # entries were extracted
    for line in text.splitlines():
        assert len(line) <= 79, line

def test_command_set_help():
    import contextlib, io
    from appeal import compile_command_set, interpreter_dispatch

    def add_item(name, count: int = 1):
        """
        Adds an item to the pile.

        Arguments:
          name: what to call it.
        """
        return ('add', name, count)
    def remove(name):
        "Removes an item."
        return ('remove', name)

    plans = {'add_item': build(add_item), 'remove': build(remove)}
    parse = compile_command_set(plans, None, prog='pile')

    def grab(fn):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = fn()
        return result, out.getvalue()

    # `help`: the listing, with summaries and the help row (v1's shape)
    result, listing = grab(lambda: parse(['help']))
    assert result is None
    oresult, olisting = grab(lambda: interpreter_dispatch(plans, None, ['help'], prog='pile'))
    assert (result, listing) == (oresult, olisting)
    assert listing.startswith('usage: pile command')
    assert 'add_item  Adds an item to the pile.' in listing
    assert 'remove    Removes an item.' in listing
    assert 'help      Print usage documentation on a specific command.' in listing

    # `help CMD` == `CMD --help`
    _, by_help = grab(lambda: parse(['help', 'add_item']))
    _, by_flag = grab(lambda: parse(['add_item', '--help']))
    assert by_help == by_flag
    assert by_help.startswith('Adds an item to the pile.')
    assert 'usage: add_item' in by_help
    assert '    name   what to call it.' in by_help   # 'count' row widens the column
    _, interpreter_help = grab(lambda: interpreter_dispatch(plans, None, ['help', 'add_item'], prog='pile'))
    assert interpreter_help == by_help

    # `help BOGUS`
    try:
        parse(['help', 'bogus'])
        assert False, 'expected UsageError'
    except UsageError as e:
        assert 'bogus' in str(e)

    # a user-defined help command wins: no automatic anything
    def help(topic=''):
        return ('user help', topic)
    plans2 = {'add_item': build(add_item), 'help': build(help)}
    parse2 = compile_command_set(plans2, None, prog='pile')
    assert parse2(['help', 'x']) == ('user help', 'x')
    try:
        parse2([])
        assert False, 'expected UsageError'
    except UsageError as e:
        assert 'Print usage documentation' not in (e.usage or '')

def test_command_set_help_facade():
    import contextlib, io
    app = Appeal(name='pile')
    @app.command()
    def add_item(name):
        "Adds an item to the pile."
        return ('add', name)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = app.process(['help'])
    assert result is None
    assert 'add_item  Adds an item to the pile.' in out.getvalue()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app.process(['help', 'add_item'])
    assert out.getvalue().startswith('Adds an item to the pile.')
    assert 'usage: add_item' in out.getvalue()

def test_set_level_help_flag():
    # `tool --help` (or -h) at position 0: the command listing,
    # exactly like the `help` command; user options win as usual
    import contextlib, io
    from appeal import compile_command_set, interpreter_dispatch
    def add_item(name):
        "Adds an item."
        return ('add', name)
    plans = {'add_item': build(add_item)}
    parse = compile_command_set(plans, None, prog='pile')
    def grab(fn):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = fn()
        return result, out.getvalue()
    result, text = grab(lambda: parse(['--help']))
    assert result is None and text.startswith('usage: pile command')
    assert 'add_item  Adds an item.' in text
    oresult, otext = grab(lambda: interpreter_dispatch(plans, None, ['--help'], prog='pile'))
    assert (oresult, otext) == (result, text)
    _, htext = grab(lambda: parse(['help']))
    assert htext == text                              # same listing
    # facade
    app = Appeal(name='pile')
    @app.command()
    def add_item2(name):
        return name
    result, text = grab(lambda: app.process(['-h']))
    assert result is None and 'add_item2' in text

def test_completion():
    from appeal import complete, complete_set
    def size(width: float, *, bold=False):
        return (width, bold)
    def draw(shape, s: size = None, *, verbose=False, retries: int = 1):
        return None
    plan = build(draw)
    # option-prefix completion, whole tree
    got = complete(plan, [], '--')
    assert got == ['--bold', '--help', '--retries', '--verbose'], got
    got = complete(plan, [], '--v')
    assert got == ['--verbose'], got
    # used single-occurrence options drop out
    got = complete(plan, ['--verbose'], '--')
    assert '--verbose' not in got and '--retries' in got, got
    # the cursor in a value position: no opinion
    assert complete(plan, ['--retries'], '') == []
    assert complete(plan, ['--retries'], '3') == []
    # after '--': no options ever
    assert complete(plan, ['--'], '--') == []
    # operand position: shell's business
    assert complete(plan, [], 'sha') == []

    # command sets: command words at the command position
    def run(target):
        return None
    def deploy(target):
        return None
    commands = {'run': build(run), 'deploy': build(deploy)}
    got = complete_set(commands, None, [], '')
    assert got == ['deploy', 'help', 'run'], got
    got = complete_set(commands, None, [], 'de')
    assert got == ['deploy'], got
    got = complete_set(commands, None, ['help'], '')
    assert got == ['deploy', 'help', 'run'], got     # help topics
    # after the command word: that command's options
    got = complete_set(commands, None, ['run'], '--')
    assert '--help' in got, got
    # with a global command: its options first, command words once
    # the minimum is fed
    def g(project, *, verbose=False):
        return None
    got = complete_set(commands, build(g), [], '--')
    assert got == ['--verbose'], got
    got = complete_set(commands, build(g), [], '')
    assert got == [], got                             # minimum unmet
    got = complete_set(commands, build(g), ['proj'], '')
    assert got == ['deploy', 'help', 'run'], got

    # facade
    app = Appeal()
    @app.global_command()
    def solo(x, *, loud=False):
        return None
    assert app.complete([], '--l') == ['--loud']

def test_read_mapping_option_classes():
    from appeal import MultiOption, Option, read_mapping
    class Tags(MultiOption):
        def init(self, default):
            self.values = list(default) if default else []
        def option(self, tag):
            self.values.append(tag)
        def render(self):
            return tuple(self.values)
    class Where(Option):
        def init(self, default):
            self.x = self.y = None
        def option(self, x: int, y: int):
            self.x, self.y = x, y
        def render(self):
            return (self.x, self.y)
    class Verbosity(MultiOption):
        def init(self, default):
            self.level = default
        def option(self):
            self.level += 1
        def render(self):
            return self.level
    def f(name, *, tag: Tags = ('seed',), where: Where = 'nowhere',
          v: Verbosity = 0):
        return (name, tag, where, v)
    got = read_mapping(f, {'name': 'n', 'tag': ['a', 'b'],
                           'where': ['3', '4'], 'v': 2})
    assert got == ('n', ('seed', 'a', 'b'), (3, 4), 2), got
    got = read_mapping(f, {'name': 'n'})
    assert got == ('n', ('seed',), 'nowhere', 0), got

def test_split_matches_v1_multisplit_semantics():
    # v1's split is big.multisplit, and now so is v2's: one pass,
    # longest separator match wins, adjacent separators count as
    # one, and strip=True strips leading and trailing SEPARATORS
    # (not whitespace).  probed against shipping v1 0.6.4.
    from appeal import split
    assert split(':')('a::b') == ['a', 'b']
    assert split('::')('a::b::c') == ['a', 'b', 'c']
    assert split(':', ',')('a:b,c:d') == ['a', 'b', 'c', 'd']
    assert split(':', strip=True)('  a : b  ') == ['  a ', ' b  ']
    assert split(':', strip=True)(':a:b:') == ['a', 'b']
    assert split('ab', 'b')('xabyb') == ['x', 'y', '']
    # no separators = whitespace, like str.split().  (v1 0.6.4
    # CRASHES here--multisplit rejects an empty separator tuple--
    # so v1's docstring is the spec, not its behavior.)
    assert split()('  a  b ') == ['a', 'b']

def test_converter_vocabulary():
    # v1's vocabulary, all outputs probed against shipping 0.6.4
    def verbosity(*, verbose: appeal.counter(max=9, step=2) = 0):
        return verbose
    assert run_both(verbosity, ['-v', '-v', '-v']) == ('ok', 6)
    assert run_both(verbosity, ['-v'] * 6) == ('ok', 9)     # capped
    assert run_both(verbosity, []) == ('ok', 0)
    def soccer(s, *, define: appeal.accumulator[int, str] = []):
        return (s, define)
    got = run_both(soccer, ['x', '--define', '1', 'one', '--define', '2', 'two'])
    assert got == ('ok', ('x', [(1, 'one'), (2, 'two')])), got
    def pool(s, *, define: appeal.mapping = {}):
        return (s, define)
    assert run_both(pool, ['x', '--define', 'k', 'v']) == ('ok', ('x', {'k': 'v'}))
    def skittles(s, *, define: appeal.mapping[int, str, float] = {}):
        return (s, define)
    got = run_both(skittles, ['x', '--define', '1', 'one', '1.5'])
    assert got == ('ok', ('x', {1: ('one', 1.5)})), got
    def set_path(path: appeal.split(':')):
        return path
    assert run_both(set_path, ['a:b:c']) == ('ok', ['a', 'b', 'c'])
    def go(direction: appeal.validate('north', 'south')):
        return direction
    assert run_both(go, ['north']) == ('ok', 'north')
    got = run_both(go, ['up'])
    assert got[0] == 'usage' and 'north' in got[1], got     # rich error
    def pick30(number: appeal.validate_range(30)):
        return number
    assert run_both(pick30, ['20']) == ('ok', 20)
    assert run_both(pick30, ['35'])[0] == 'usage'
    def clamped(n: appeal.validate_range(0, 10, clamp=True)):
        return n
    assert run_both(clamped, ['15']) == ('ok', 10)
    # bare factories are named config errors (v1)
    for bad in (lambda a=None: None,):
        pass
    def bare(a: appeal.split):
        pass
    try:
        build(bare)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'call it first' in str(e), e
    def bare2(*, v: appeal.counter = 0):
        pass
    try:
        build(bare2)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'call it first' in str(e), e

def test_standalone_vocabulary_recipes():
    # the north star: factory products (closures! dynamic classes!)
    # survive standalone emission--the script re-runs the recipe
    # from the embedded vocabulary region
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'vocab_cmds.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(
                'import appeal\n\n'
                'def paths(path: appeal.split(":"),\n'
                '          *, v: appeal.counter(step=10) = 0,\n'
                '          color: appeal.validate("red", "blue") = "red"):\n'
                "    print('paths', path, v, color)\n")
        sys.path.insert(0, d)
        try:
            import vocab_cmds
            import importlib
            importlib.reload(vocab_cmds)
            script = emit_standalone(build(vocab_cmds.paths), argv0='paths')
        finally:
            sys.path.remove(d)
            sys.modules.pop('vocab_cmds', None)
        assert 'def split(' in script                    # region embedded
        assert 'def _toy_multisplit' in script          # ...and what split requires
        assert "= split(':')" in script                  # the recipe
        assert "= counter(max=None, step=10)" in script
        script_path = os.path.join(d, 'paths_cli.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': repo_dir}
        r = subprocess.run(
            [sys.executable, script_path, 'a:b', '-v', '-v', '--color', 'blue'],
            capture_output=True, text=True, cwd=d, env=env)
        assert r.returncode == 0, r.stderr
        assert r.stdout == "paths ['a', 'b'] 20 blue\n"
        r = subprocess.run(
            [sys.executable, script_path, 'a', '--color', 'mauve'],
            capture_output=True, text=True, cwd=d, env=env)
        assert r.returncode == 2
        assert 'red' in r.stderr                        # rich error survives

def test_kwargs_options():
    # @app.option declarations for parameters not in the signature
    # deliver through **kwargs; absent ones simply aren't passed
    # (v1, probed: F a {} / {'zap': 5} / {'quiet': True})
    app = Appeal()
    @app.option('zap', '--zap', annotation=int)
    @app.option('quiet', '-q', '--quiet', default=False)
    @app.global_command()
    def f(x, **kwargs):
        return (x, kwargs)
    assert app.process(['a']) == ('a', {})
    assert app.process(['a', '--zap', '5']) == ('a', {'zap': 5})
    assert app.process(['a', '-q']) == ('a', {'quiet': True})
    assert app.process(['a', '--zap', '5', '-q']) == ('a', {'zap': 5, 'quiet': True})
    # parity between rungs
    fn = f
    got = run_both(fn, ['a', '--zap', '7'])
    assert got == ('ok', ('a', {'zap': 7})), got
    # bare **kwargs: legal, receives nothing
    def g(x, **kw):
        return (x, kw)
    got = run_both(g, ['a'])
    assert got == ('ok', ('a', {})), got

def test_standalone_kwargs_options():
    # north star: **kwargs options survive standalone emission
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'kw_cmds.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write('def stamp(label, **kwargs):\n'
                    "    print('stamp', label, sorted(kwargs.items()))\n")
        sys.path.insert(0, d)
        try:
            import kw_cmds
            import importlib
            importlib.reload(kw_cmds)
            from appeal import add_option_override
            add_option_override(kw_cmds.stamp, 'depth', ('--depth',),
                                annotation=int)
            script = emit_standalone(build(kw_cmds.stamp), argv0='stamp')
        finally:
            sys.path.remove(d)
            sys.modules.pop('kw_cmds', None)
        script_path = os.path.join(d, 'stamp_cli.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        r = run_script(script_path, ['x', '--depth', '3'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == "stamp x [('depth', 3)]\n"
        r = run_script(script_path, ['x'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'stamp x []\n'

def test_app_parameter_renames():
    # @app.parameter (v1's API): renames an operand in usage; and
    # (new in v2--v1 only reached operands) renames an option's
    # metavar: [-t|--times <COUNT>]
    app = Appeal()
    @app.parameter('port', usage='PORT')
    @app.parameter('times', usage='COUNT')
    @app.global_command()
    def serve(host, port: int = 8080, *, times: int = 1):
        """
        Serves.

        Arguments:
          port: where to listen.
        """
        pass
    usage = app.plan.usage()
    assert usage == 'serve [-t|--times <COUNT>] host [PORT]', usage
    result, text = run_both_stdout(serve, ['--help'])
    assert '[-t|--times <COUNT>]' in text
    assert '    PORT  where to listen.' in text        # tables renamed too
    # a converter's own parameters rename by decorating the converter
    from appeal import add_parameter_usage
    def pair(x: float, y: float):
        return (x, y)
    add_parameter_usage(pair, 'x', 'X')
    def draw(p: pair):
        return p
    assert 'X' in build(draw).usage(), build(draw).usage()
    # naming a parameter the function doesn't have: config error
    app2 = Appeal()
    @app2.parameter('nonesuch', usage='NOPE')
    @app2.global_command()
    def f(x):
        pass
    try:
        app2.plan
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'nonesuch' in str(e)

def test_help_yields_to_user_options():
    # a program that claims --help keeps it; no automatic help
    def f(*, help=False):
        return help
    got = run_both(f, ['--help'])
    assert got == ('ok', True), got
    # a program that claims only -h still gets --help
    def g(*, hush=False):
        "Quietly."
        return hush
    result, text = run_both_stdout(g, ['--help'])
    assert result is None and 'Quietly.' in text
    got = run_both(g, ['-h'])                        # -h is hush's
    assert got == ('ok', True), got

def test_generated_code_name_collisions():
    # the corpus caught this: a converter parameter named `i`
    # clobbered the generated fill function's operand index.  Any
    # parameter name the emitter uses internally must be mangled.
    def int_float(i: int, f: float):
        return (i, f)
    def wrapper(i_f: int_float, s: str, *, verbose=False):
        return [i_f, s, verbose]
    def cmd(a: str, b: wrapper = None):
        return (a, b)
    got = run_both(cmd, ['x', '35', '-v', '46', 'googly'])
    assert got == ('ok', ('x', [(35, 46.0), 'googly', True])), got
    def nasty(i: int, remaining: str = 'r', *, given=False, operands: int = 0):
        return (i, remaining, given, operands)
    got = run_both(nasty, ['5', 'x', '--given', '--operands', '9'])
    assert got == ('ok', (5, 'x', True, 9)), got
    # lambdas as converters need identifier-safe ref names
    def go(*, direction=None):
        return direction
    from appeal import add_option_override
    add_option_override(go, 'direction', ('--north',),
                        annotation=lambda: 'north')
    got = run_both(go, ['--north'])
    assert got == ('ok', 'north'), got

def test_schema():
    # the machine-readable twin of --help; pairs with read_mapping
    # to run a command from a JSON object
    import json
    from appeal import schema
    def size(width: float, *, bold=False):
        return (width, bold)
    def draw(shape, s: size = None, *, verbose=False, times: int = 1):
        """
        Draws a shape.

        Arguments:
          shape: the shape to draw.

        Options:
          times: how many times.
        """
        return None
    got = schema(draw)
    json.dumps(got)                                      # JSON-safe throughout
    assert got['summary'] == 'Draws a shape.'
    assert got['usage'].startswith('draw ')
    assert got['operand_counts'] == {'minimum': 1, 'maximum': 2, 'valid': [1, 2]}
    shape_entry, s_entry = got['operands']
    assert shape_entry == {'name': 'shape', 'required': True, 'repeat': False,
                           'trailing': False, 'converter': 'str',
                           'doc': 'the shape to draw.'}
    assert s_entry['group']['name'] == 'size'
    assert s_entry['group']['options'][0]['strings'] == ['-b', '--bold']
    by_name = {o['name']: o for o in got['options']}
    assert by_name['verbose']['kind'] == 'flag'
    assert by_name['times']['operands'] == ['int']
    assert by_name['times']['doc'] == 'how many times.'
    # command sets
    app = Appeal(name='tool')
    @app.command()
    def run(target):
        "Runs the target."
        return target
    got = app.schema()
    json.dumps(got)
    assert got['name'] == 'tool'
    assert got['commands']['run']['summary'] == 'Runs the target.'
    # the round trip: schema out, JSON blob in, command runs
    from appeal import read_mapping
    blob = json.loads('{"shape": "dot", "times": 3}')
    assert read_mapping(draw, blob) is None

def test_read_mapping():
    # the metaphor pointed at a config file (v1's API; every case
    # marked "v1" below reproduces probed v1 0.6.4 output exactly)
    from appeal import read_mapping
    def basic(host, port: int = 8080, *, retries: int = 1):
        return (host, port, retries)
    got = read_mapping(basic, {'host': 'h', 'port': '99', 'retries': '5'})
    assert got == ('h', 99, 5), got                       # v1
    got = read_mapping(basic, {'host': 'h', 'port': 99, 'retries': 5})
    assert got == ('h', 99, 5), got                       # v1: converters always apply
    got = read_mapping(basic, {'host': 'h', 'extra': 'ignored'})
    assert got == ('h', 8080, 1), got                     # extras: v1; defaults: v2 fix
    try:
        read_mapping(basic, {'port': 1})
        assert False, 'expected AppealError'
    except AppealError as e:
        assert "'host'" in str(e)

    def server(host, port: int = 8080):
        return ('server', host, port)
    def config(name, s: server = None, *, debug=False):
        return ('config', name, s, debug)
    # both spellings, v1: a sub-mapping under the group's name...
    got = read_mapping(config, {'name': 'n', 's': {'host': 'h', 'port': '1'}})
    assert got == ('config', 'n', ('server', 'h', 1), False), got
    # ...or flat keys at the same level
    got = read_mapping(config, {'name': 'n', 'host': 'h', 'port': '1'})
    assert got == ('config', 'n', ('server', 'h', 1), False), got
    # errors carry the path
    try:
        read_mapping(config, {'name': 'n', 's': {'port': '1'}})
        assert False, 'expected AppealError'
    except AppealError as e:
        assert "'host'" in str(e) and 's' in str(e)

    # v2 fixes: bools parse strictly (v1 crashed on flags entirely)
    assert read_mapping(config, {'name': 'n', 'debug': 'true'})[3] is True
    assert read_mapping(config, {'name': 'n', 'debug': 'off'})[3] is False
    try:
        read_mapping(config, {'name': 'n', 'debug': 'maybe'})
        assert False, 'expected AppealError'
    except AppealError as e:
        assert 'boolean' in str(e)

    # v2: *args (v1 refused), collectors, tuples
    def lots(first, *rest: int):
        return (first, rest)
    assert read_mapping(lots, {'first': 'a', 'rest': ['1', '2']}) == ('a', (1, 2))
    def tagged(point: tuple[int, int], *, tags: list[str] = (), env: dict[str, int] = None):
        return (point, tags, env)
    got = read_mapping(tagged, {'point': ['3', '4'], 'tags': ['a', 'b'],
                                'env': {'x': '1'}})
    assert got == ((3, 4), ['a', 'b'], {'x': 1}), got

def test_read_mapping_dataclass():
    # the README's marquee use case
    import dataclasses
    from appeal import read_mapping
    @dataclasses.dataclass
    class ConfigFile:
        editor: str = ''
        threads: int = 4
        debug: bool = False
    got = read_mapping(ConfigFile, {'editor': 'vi', 'threads': '8', 'debug': 'yes'})
    assert got == ConfigFile('vi', 8, True), got
    assert read_mapping(ConfigFile, {}) == ConfigFile()

def test_read_iterable():
    # row-oriented (v1's corpus): one call per row, results listed,
    # empty rows skipped; keyword-only parameters can't be
    # position-fed and are config errors
    from appeal import read_csv, read_iterable
    def row(a: int, b: str):
        return (a, b)
    assert read_iterable(row, [['1', 'x'], ['2', 'y']]) == [(1, 'x'), (2, 'y')]
    assert read_iterable(row, [['1', 'x'], [], ['2', 'y']]) == [(1, 'x'), (2, 'y')]
    def var(first, *rest: int):
        return (first, rest)
    assert read_iterable(var, [['a', '1', '2', '3']]) == [('a', (1, 2, 3))]
    def optioned(a, *, b=1):
        return (a, b)
    try:
        read_iterable(optioned, [['x']])
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert "'b'" in str(e)
    # groups within a row still nest
    def server(host, port: int = 8080):
        return ('server', host, port)
    def config(name, s: server = None):
        return ('config', name, s)
    got = read_iterable(config, [['n', ['h', '1']]])
    assert got == [('config', 'n', ('server', 'h', 1))], got
    # read_csv: headings consumed; positional or mapped
    import csv, io
    reader = csv.reader(io.StringIO("h1,h2\n1,x\n2,y\n"))
    assert read_csv(row, reader) == [(1, 'x'), (2, 'y')]
    reader = csv.reader(io.StringIO("col_a,col_b\n10,foo\n"))
    assert read_csv(row, reader,
                    first_row_map={'col_a': 'a', 'col_b': 'b'}) == [(10, 'foo')]

def test_read_mapping_facade():
    app = Appeal()
    def f(x: int):
        return x
    assert app.read_mapping(f, {'x': '5'}) == 5
    assert app.read_iterable(f, [['5']]) == [5]

def test_text_trio_synced_from_big():
    # appeal/runtime.py carries the word-wrap trio excerpted from
    # big between scissors markers (tools/sync_snippets.py re-syncs).
    # This test fails if big's snippets have drifted: re-run the tool.
    import os.path
    big_dir = os.path.normpath(os.path.join(repo_dir, '..', 'big', 'big'))
    if not os.path.isdir(big_dir):
        print('  (big sibling checkout not found; sync check skipped)')
        return

    def snippet(path, id):
        with open(path, 'rt', encoding='utf-8') as f:
            text = f.read()
        start_marker = f'# --8<-- start {id} --8<--\n'
        end_marker = f'# --8<-- end {id} --8<--\n'
        start = text.index(start_marker)
        end = text.index(end_marker) + len(end_marker)
        return text[start:end]

    runtime = os.path.join(repo_dir, 'appeal', 'runtime.py')
    for source, ours, id in ((os.path.join(big_dir, 'text.py'), runtime, 'big _iterate_over_bytes'),
                             (os.path.join(big_dir, 'text.py'), runtime, 'big word wrap trio')):
        assert snippet(source, id) == snippet(ours, id), (
            f'{id!r} drifted: run tools/sync_snippets.py')

def test_runtime_source_is_self_contained():
    # the whole streamed runtime runs in a BARE namespace (the
    # north star: a standalone script's library imports nothing)
    # and its borrowed trio agrees with live big where available
    from appeal.runtime import runtime_source
    ns = {}
    exec(runtime_source(), ns)
    for name in ('parse_tokens', 'run_main', 'render_help_page', 'split',
                 'accumulator', 'wrap_words', 'merge_columns'):
        assert name in ns, f'streamed runtime is missing {name}'
    words = ('the streamed runtime wraps words with no imports '
             'from anywhere at all.  Even two-space sentences.').split()
    wrapped = ns['wrap_words'](words, 30)
    assert max(len(line) for line in wrapped.split('\n')) <= 30
    assert 'all.  Even' in wrapped.replace('\n', ' ')
    merged = ns['merge_columns']((['one', 'two'], 5, 5),
                                 (['three', 'four'], 5, 5))
    assert merged == 'one   three\ntwo   four'
    split = ns['split_text_with_code'](
        'Hello there.\n\n    code stays intact\n\nBye.')
    assert '    code stays intact' in split
    try:
        import big.text
    except ImportError:
        print('  (big not importable; live-parity check skipped)')
        return
    assert wrapped == big.text.wrap_words(list(words), 30)
    assert merged == big.text.merge_columns((['one', 'two'], 5, 5),
                                            (['three', 'four'], 5, 5))

def test_fuzz_parity():
    # the two rungs, adversarially: random signatures, random
    # command lines (valid and invalid counts, options for groups
    # that end up skipped, the lot).  The interpreter and the generated
    # parser must agree on every result and every error message.
    # Deterministic seed: a failure here reproduces exactly.
    import random
    rng = random.Random(20260703)
    from appeal.build import all_options

    LEAVES = ['str', 'int', 'float']

    def gen_function(depth, defs, counter):
        "Append a function def to defs; return its name."
        fname = f'fz{next(counter)}'
        params = []      # (source, is_operand)
        rets = []
        n_pos = rng.randint(1, 3)
        defaulted = False    # Python: no required after defaulted
        for i in range(n_pos):
            pname = f'{fname}_p{i}'
            roll = rng.random()
            if roll < 0.55 or depth >= 2:
                ann = rng.choice(LEAVES)
            elif roll < 0.80:
                ann = gen_function(depth + 1, defs, counter)
            else:
                ann = f'tuple[{rng.choice(LEAVES)}, {rng.choice(LEAVES)}]'
            if defaulted or rng.random() < 0.35:
                defaulted = True
                params.append(f'{pname}: {ann} = None')
            else:
                params.append(f'{pname}: {ann}')
            rets.append(pname)
        opt_sources = []
        for i in range(rng.randint(0, 2)):
            oname = f'{fname}_o{i}'
            kind = rng.choice(['flag', 'value', 'accumulate', 'mapping'])
            if kind == 'flag':
                opt_sources.append(f'{oname}=False')
            elif kind == 'value':
                opt_sources.append(f'{oname}: int = 0')
            elif kind == 'accumulate':
                opt_sources.append(f'{oname}: list[int] = ()')
            else:
                opt_sources.append(f'{oname}: dict[str, int] = None')
            rets.append(oname)
        star = trailing = None
        if depth == 0 and rng.random() < 0.20:
            star = f'*{fname}_star'
            rets.append(f'{fname}_star')
        if depth == 0 and rng.random() < 0.20:
            trailing = f'{fname}_dst'
            rets.append(trailing)
        pieces = list(params)
        if star:
            pieces.append(star)
        elif trailing or opt_sources:
            pieces.append('*')
        if trailing:
            pieces.append(trailing)
        pieces.extend(opt_sources)
        defs.append(f"def {fname}({', '.join(pieces)}):\n"
                    f"    return ({fname!r}, {', '.join(rets)},)")
        return fname

    def gen_argv(plan):
        "A random command line for the plan: operands + options."
        if plan.valid_counts is None:
            count = plan.minimum + rng.randint(0, 4)
        else:
            counts = sorted(plan.valid_counts)
            count = rng.choice(counts)
            if rng.random() < 0.30:
                count = rng.choice([max(0, min(counts) - 1), max(counts) + 1,
                                    count + 1])
        argv = [str(rng.randint(0, 99)) for _ in range(count)]
        for owner, option in all_options(plan):
            if rng.random() > 0.40:
                continue
            key = option.strings[-1]
            if option.kind == 'flag':
                tokens = [key]
            elif option.kind == 'value':
                tokens = [key, str(rng.randint(0, 99))]
            elif option.kind == 'accumulate':
                tokens = [key, str(rng.randint(0, 99))]
            else:
                tokens = [key, f'k{rng.randint(0, 9)}={rng.randint(0, 99)}']
            argv.insert(rng.randint(0, len(argv)), '\0'.join(tokens))
        return [t for token in argv for t in token.split('\0')]

    from itertools import count as _count
    for round_number in range(150):
        defs = []
        counter = _count()
        top = gen_function(0, defs, counter)
        namespace = {}
        source = '\n\n'.join(defs)
        try:
            exec(source, namespace)
            plan = build(namespace[top])
        except AppealConfigurationError:
            continue     # generator made something illegal; fine
        for _ in range(3):
            argv = gen_argv(plan)
            # run_both asserts the rungs agree; that's the test
            run_both(namespace[top], argv)

V1_RUNNER = '''\
import contextlib, io, json, sys

def main():
    with open(sys.argv[1], 'rt', encoding='utf-8') as f:
        cases = json.load(f)
    import appeal as v1
    results = []
    for case in cases:
        case_results = []
        for argv in case['argvs']:
            ns = {'CALLS': []}
            exec(case['source'], ns)
            app = v1.Appeal()
            app.global_command()(ns[case['top']])
            sink = io.StringIO()
            try:
                with contextlib.redirect_stdout(sink), \\
                     contextlib.redirect_stderr(sink):
                    code = None
                    try:
                        app.main(list(argv))
                    except SystemExit as e:
                        code = e.code
                if code in (None, 0):
                    case_results.append({'status': 'ok', 'calls': ns['CALLS']})
                else:
                    case_results.append({'status': 'error'})
            except Exception as e:
                case_results.append(
                    {'status': 'crash', 'exception': f'{type(e).__name__}: {e}'})
        results.append(case_results)
    with open(sys.argv[2], 'wt', encoding='utf-8') as f:
        json.dump({'version': v1.__version__, 'results': results}, f)

main()
'''

def test_differential_fuzz_against_installed_v1():
    # the backward-compatibility contract, executed: random programs
    # in the v1-and-v2 shared grammar, random command lines, through
    # the SHIPPING v1 (site-packages) and through v2.  Wherever v1
    # succeeds, v2 must succeed with identical calls.  Where v1
    # errors, v2 may error or parse (the documented strict-superset
    # gains).  Deterministic seed.
    import json
    import random
    rng = random.Random(20260704)
    from appeal.build import all_options

    LEAVES = ['str', 'int', 'float']

    def gen_function(depth, defs, counter, top=False):
        fname = f'df{next(counter)}'
        params = []
        args = []
        n_pos = rng.randint(1, 3)
        defaulted = False
        for i in range(n_pos):
            pname = f'{fname}_p{i}'
            roll = rng.random()
            if roll < 0.60 or depth >= 2:
                ann = rng.choice(LEAVES)
            else:
                ann = gen_function(depth + 1, defs, counter)
            if defaulted or rng.random() < 0.35:
                defaulted = True
                params.append(f'{pname}: {ann} = None')
            else:
                params.append(f'{pname}: {ann}')
            args.append(pname)
        opt_sources = []
        for i in range(rng.randint(0, 2)):
            if top and i == 0 and rng.random() < 0.15:
                oname = rng.choice('jkmwz')     # one-char name: short only
            else:
                oname = f'{fname}_o{i}'
            roll = rng.random()
            if roll < 0.45:
                opt_sources.append(f'{oname}=False')
            elif roll < 0.85:
                opt_sources.append(f'{oname}: int = 0')
            else:
                # a multi-param converter: several operands per use
                helper = f'{fname}_oc{i}'
                defs.append(f"def {helper}(a: int, b: int):\n"
                            f"    return ({helper!r}, a, b)")
                opt_sources.append(f'{oname}: {helper} = None')
            args.append(oname)
        # no trailing operands here: kwonly-no-default is a v2
        # INNOVATION--shipping v1 refuses such signatures outright
        # ("keyword-only argument ... doesn't have a default value"),
        # so they have no differential story to test
        star = None
        if top and rng.random() < 0.25:
            if rng.random() < 0.4:
                # a converter group on *args, with a windowed flag
                helper = f'{fname}_seg'
                defs.append(f"def {helper}(v: int, *, {helper}_m=False):\n"
                            f"    return ({helper!r}, v, {helper}_m)")
                star = f'*{fname}_star: {helper}'
            else:
                star = f'*{fname}_star'
            args.append(f'{fname}_star')
        pieces = list(params)
        if star:
            pieces.append(star)
        elif opt_sources:
            pieces.append('*')
        pieces.extend(opt_sources)
        body = (f"CALLS.append(({fname!r}, {', '.join(args)},))"
                if top else
                f"return ({fname!r}, {', '.join(args)},)")
        defs.append(f"def {fname}({', '.join(pieces)}):\n    {body}")
        return fname

    def gen_argv(plan):
        if plan.valid_counts is None:
            count = plan.minimum + rng.randint(0, 4)
        else:
            counts = sorted(plan.valid_counts)
            count = rng.choice(counts)
            if rng.random() < 0.30:
                count = rng.choice([max(0, min(counts) - 1),
                                    max(counts) + 1, count + 1])
        argv = [str(rng.randint(0, 99)) for _ in range(count)]
        for owner, option in all_options(plan):
            if rng.random() > 0.40:
                continue
            key = option.strings[-1]
            if option.kind == 'flag':
                tokens = [key]
            else:
                nargs = max(1, len(option.converters) - 1)
                tokens = [key] + [str(rng.randint(0, 99)) for _ in range(nargs)]
            # repeat occasionally: repetition of non-multi options
            # must error identically in both versions
            for _ in range(1 if rng.random() < 0.85 else 2):
                argv.insert(rng.randint(0, len(argv)), '\0'.join(tokens))
        return [t for token in argv for t in token.split('\0')]

    # generate the corpus
    from itertools import count as _count
    cases = []
    plans = []
    for _ in range(100):
        defs = []
        counter = _count()
        top = gen_function(0, defs, counter, top=True)
        source = 'CALLS = []\n\n' + '\n\n'.join(defs)
        ns = {'CALLS': []}
        exec(source, ns)
        plan = build(ns[top])
        cases.append({'source': source, 'top': top,
                      'argvs': [gen_argv(plan) for _ in range(3)]})
        plans.append(plan)

    # run the corpus through installed v1, one subprocess
    with tempfile.TemporaryDirectory() as d:
        runner = os.path.join(d, 'v1_runner.py')
        cases_path = os.path.join(d, 'cases.json')
        out_path = os.path.join(d, 'results.json')
        with open(runner, 'wt', encoding='utf-8') as f:
            f.write(V1_RUNNER)
        with open(cases_path, 'wt', encoding='utf-8') as f:
            json.dump(cases, f)
        r = subprocess.run(
            [sys.executable, runner, cases_path, out_path],
            capture_output=True, text=True, cwd=d)
        if r.returncode != 0 and 'ModuleNotFoundError' in r.stderr:
            print('  (v1 not installed in site-packages; differential fuzz skipped)')
            return
        assert r.returncode == 0, r.stderr
        with open(out_path, 'rt', encoding='utf-8') as f:
            v1_out = json.load(f)
    assert v1_out['version'].startswith('0.'), v1_out['version']

    # run it through v2 and compare
    def normalize(calls):
        return json.loads(json.dumps(calls))

    # 'reject', not 'error': the fuzzer generates invalid command
    # lines on purpose, and rejecting one is the parser doing its
    # job.  agreement on garbage is parity too.
    tally = {'both ok': 0, 'both reject': 0, 'v1 rejects, v2 ok': 0,
             'v1 rejects program': 0, 'v1 crash': 0}
    incompatibilities = []
    for case, plan, case_results in zip(cases, plans, v1_out['results']):
        ns = {'CALLS': []}
        exec(case['source'], ns)
        parse = compile_plan(build(ns[case['top']]))
        for argv, v1_result in zip(case['argvs'], case_results):
            ns['CALLS'].clear()
            try:
                parse(list(argv))
                v2_result = {'status': 'ok', 'calls': normalize(ns['CALLS'])}
            except UsageError:
                v2_result = {'status': 'error'}
            v1_status = v1_result['status']
            if v1_status == 'crash':
                if v1_result['exception'].startswith('ConfigurationError'):
                    tally['v1 rejects program'] += 1
                else:
                    tally['v1 crash'] += 1
            elif v1_status == 'error':
                tally['both reject' if v2_result['status'] == 'error'
                      else 'v1 rejects, v2 ok'] += 1
            elif v2_result['status'] != 'ok':
                incompatibilities.append(
                    (case['source'], argv, 'v1 parses this; v2 errors'))
            elif normalize(v1_result['calls']) != v2_result['calls']:
                incompatibilities.append(
                    (case['source'], argv,
                     f"v1 calls {v1_result['calls']!r}, v2 calls {v2_result['calls']!r}"))
            else:
                tally['both ok'] += 1

    assert not incompatibilities, (
        f'{len(incompatibilities)} incompatibilities with shipping v1; first: '
        f'{incompatibilities[0]}')
    assert tally['both ok'] > 100, tally     # the corpus must be substantive
    print(f'Tests vs v1 (Appeal {v1_out["version"]}):')
    for label, count in tally.items():
        print(f'    {label}: {count}')

def test_double_dash_state_never_leaks():
    app = Appeal()

    @app.global_command()
    def echo(*words, upper=False):
        s = ' '.join(words)
        return s.upper() if upper else s

    assert app.process(['--', '--upper', 'x']) == '--upper x'
    # second parse on the same object: options must work again
    assert app.process(['--upper', 'hello', 'there']) == 'HELLO THERE'

def test_plan_recursion():
    plan = build(draw)
    named = {s.name: s for s in plan.slots}
    # where: required nonterminal, consumes exactly 4
    assert named['where'].count_options == (4,)
    # brush: optional nonterminal, child counts {3, 4}, plus skip
    assert named['brush'].count_options == (4, 3, 0)
    assert plan.valid_counts == {5, 8, 9}

def test_plan_nested_optional_counts():
    def f(a, s: sized='S'):
        return (a, s)
    plan = build(f)
    assert plan.valid_counts == {1, 2, 3}
    got = run_both(f, ['q', '5'])
    assert got == ('ok', ('q', (5, 10))), got
    got = run_both(f, ['q', '5', '6'])
    assert got == ('ok', ('q', (5, 6))), got

def test_converter_cycle_detected():
    def selfy(x):
        return x
    selfy.__annotations__['x'] = selfy
    try:
        build(selfy)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'cycle' in str(e)
        assert 'selfy' in str(e)

def test_converter_restrictions_named():
    # still-restricted converter constructs refuse by name
    def has_star(x, *rest):
        return x
    def cmd(a: has_star, b):
        return (a, b)
    plan = build(cmd)
    # *args converters degrade to terminals (v1-style one-operand call)
    named = {s.name: s for s in plan.slots}
    from appeal.plan import Terminal
    assert isinstance(named['a'].child, Terminal)

    def has_trailing(x, *, z):
        return (x, z)
    def cmd2(a: has_trailing):
        return a
    try:
        build(cmd2)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'has_trailing' in str(e)

def test_converter_error_names_child_parameter():
    got = run_both(draw, ['dot', 'a', 'b', 'c', 'd', '9', 'x', '7'])
    assert got[0] == 'usage'
    assert "'g'" in got[1], got     # the failing parameter inside rgb
    assert "'x'" in got[1], got     # the offending operand

def test_bundled_flags():
    def f(*, alpha=False, beta=False):
        return (alpha, beta)
    got = run_both(f, ['-ab'])
    assert got == ('ok', (True, True)), got
    # a short value option must be last in a bundle
    def g(*, alpha=False, num=1):
        return (alpha, num)
    got = run_both(g, ['-an', '5'])
    assert got == ('ok', (True, 5)), got
    got = run_both(g, ['-na', '5'])
    assert got[0] == 'usage', got

def test_generated_source_is_readable():
    plan = build(serve)
    parse = compile_plan(plan)
    # the generated source is the disassembly
    assert 'def parse_serve(argv):' in parse.source
    assert 'check_count' in parse.source


# ---------------------------------------------------------------------
# THE NORTH STAR: standalone emission

DEMO_MODULE = '''\
def greet(name, greeting='hello', *, shout=False, times: int = 1):
    s = f"{greeting}, {name}!"
    if shout:
        s = s.upper()
    print(' '.join([s] * times))

def cp(*src, dst):
    print('copy', '+'.join(src), '->', dst)

def _pair(x, y):
    return f'{x}+{y}'

def between(start: _pair, end: _pair=None):
    print('between', start, end)

def stroke(width: float=1.0, *, dashed=False):
    return f'{width}{"~" if dashed else "-"}'

def sketch(shape, s: stroke='none', *, tag: list[str] = ()):
    print('sketch', shape, s, '+'.join(tag))

def move(delta: tuple[int, int] = (0, 0), *, fast=False):
    print('move', delta, 'fast' if fast else 'slow')

def config(project='.', *, trace=False):
    if trace:
        print('trace on', project)

def _pt(x: int, y: int):
    return f'{x},{y}'

def mark(label, *, at: _pt = 'origin', span: tuple[int, int] = (0, 0)):
    """
    Marks a label on the canvas.

    Arguments:
      label: the text to place.

    Options:
      at: where to place it.
    """
    print('mark', label, at, span)

def seg(length: float, *, dashed=False):
    return f"{length}{'~' if dashed else '-'}"

def path(start, *segs: seg):
    print('path', start, '+'.join(segs))

def board(f: _pair, s: stroke = 'none'):
    print('board', f, s)
'''

def write_standalone_fixture(dirname, command_name, decorate=None):
    "Write demo_cmds.py and a generated standalone script into dirname."
    module_path = os.path.join(dirname, 'demo_cmds.py')
    with open(module_path, 'wt', encoding='utf-8') as f:
        f.write(DEMO_MODULE)

    # import the demo module the same way the script will
    sys.path.insert(0, dirname)
    try:
        import demo_cmds
        import importlib
        importlib.reload(demo_cmds)
        command = getattr(demo_cmds, command_name)
        if decorate is not None:
            decorate(command)
        plan = build(command)
        script = emit_standalone(plan, argv0=command_name)
    finally:
        sys.path.remove(dirname)
        sys.modules.pop('demo_cmds', None)

    script_path = os.path.join(dirname, f'{command_name}_cli.py')
    with open(script_path, 'wt', encoding='utf-8') as f:
        f.write(script)
    return script_path, script

def run_script(script_path, argv):
    return subprocess.run(
        [sys.executable, script_path] + argv,
        capture_output=True, text=True,
        cwd=os.path.dirname(script_path),
        env={'PATH': os.environ.get('PATH', '')},
        )

def test_standalone_runs():
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'greet')

        r = run_script(script_path, ['world'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'hello, world!\n'

        r = run_script(script_path, ['world', 'howdy', '--shout', '--times', '2'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'HOWDY, WORLD! HOWDY, WORLD!\n'

        r = run_script(script_path, [])
        assert r.returncode == 2
        assert 'error:' in r.stderr
        assert 'usage:' in r.stderr

        r = run_script(script_path, ['--bogus', 'x'])
        assert r.returncode == 2
        assert '--bogus' in r.stderr

def test_standalone_command_set():
    # the north star holds for multi-command programs: one script,
    # every command's parser plus the dispatcher and global command
    from appeal import emit_standalone_command_set
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'demo_cmds.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(DEMO_MODULE)
        sys.path.insert(0, d)
        try:
            import demo_cmds
            import importlib
            importlib.reload(demo_cmds)
            plans = {'greet': build(demo_cmds.greet), 'cp': build(demo_cmds.cp)}
            script = emit_standalone_command_set(
                plans, build(demo_cmds.config), argv0='tool')
        finally:
            sys.path.remove(d)
            sys.modules.pop('demo_cmds', None)
        script_path = os.path.join(d, 'tool.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)

        r = run_script(script_path, ['greet', 'world'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'hello, world!\n'

        r = run_script(script_path, ['--trace', 'cp', 'a', 'b', 'dest'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'trace on .\ncopy a+b -> dest\n'

        # flexible global operand: 'proj' feeds config, 'greet' is
        # the command word (first operand naming a command)
        r = run_script(script_path, ['proj', '--trace', 'greet', 'world'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'trace on proj\nhello, world!\n'

        # 'bogus' doesn't name a command, so it feeds the global's
        # optional project; the line then ends without a command
        r = run_script(script_path, ['bogus'])
        assert r.returncode == 2
        assert 'no command specified.' in r.stderr
        # ...but once the global is at its maximum, the next operand
        # is forced to be the command word
        r = run_script(script_path, ['bogus', 'bogus2'])
        assert r.returncode == 2
        assert 'unknown command' in r.stderr
        assert 'Commands:' in r.stderr and 'greet' in r.stderr

        r = run_script(script_path, [])
        assert r.returncode == 2
        assert 'no command specified.' in r.stderr

        # the global option is scoped to before the command word
        r = run_script(script_path, ['greet', 'world', '--trace'])
        assert r.returncode == 2
        assert '--trace' in r.stderr

MULTIOPT_MODULE = """\
from appeal import MultiOption, Option

class Tags(MultiOption):
    def init(self, default):
        self.values = list(default) if default else []
    def option(self, tag):
        self.values.append(tag)
    def render(self):
        return tuple(self.values)

class Where(Option):
    def init(self, default):
        self.x = self.y = None
    def option(self, x: int, y: int):
        self.x, self.y = x, y
    def render(self):
        return f'{self.x}x{self.y}'

def label(thing, *, tag: Tags = (), where: Where = 'nowhere'):
    print('label', thing, '+'.join(tag), where)
"""

def test_standalone_multioption():
    # the generated script never imports appeal--but the USER'S
    # module does (the MultiOption base class lives there), so the
    # subprocess needs the appeal the class was written against.
    # On this machine site-packages holds shipping v1, so point
    # PYTHONPATH at the v2 tree; on a real v2 install this is moot.
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'label_cmds.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(MULTIOPT_MODULE)
        sys.path.insert(0, d)
        try:
            import label_cmds
            import importlib
            importlib.reload(label_cmds)
            script = emit_standalone(build(label_cmds.label), argv0='label')
        finally:
            sys.path.remove(d)
            sys.modules.pop('label_cmds', None)
        script_path = os.path.join(d, 'label_cli.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': repo_dir}
        r = subprocess.run(
            [sys.executable, script_path, 'box', '--tag', 'a', '-t', 'b'],
            capture_output=True, text=True, cwd=d, env=env)
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'label box a+b nowhere\n'
        r = subprocess.run(
            [sys.executable, script_path, 'box', '--where', '3', '4'],
            capture_output=True, text=True, cwd=d, env=env)
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'label box  3x4\n'
        r = subprocess.run(
            [sys.executable, script_path, 'box',
             '--where', '1', '2', '--where', '3', '4'],
            capture_output=True, text=True, cwd=d, env=env)
        assert r.returncode == 2
        assert 'more than once' in r.stderr

def test_standalone_multiparam_option():
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'mark')
        r = run_script(script_path, ['x', '--at', '3', '4'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'mark x 3,4 (0, 0)\n'
        r = run_script(script_path, ['x', '--span', '5', '6'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'mark x origin (5, 6)\n'
        r = run_script(script_path, ['x'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'mark x origin (0, 0)\n'
        r = run_script(script_path, ['x', '--at', '3'])
        assert r.returncode == 2
        assert '2 values' in r.stderr

def test_standalone_gate_rule():
    # the wall in a generated script: board's frame (_pair, two
    # required operands) gates the skippable stroke's --dashed
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'board')
        r = run_script(script_path, ['a', 'b', '--dashed'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'board a+b 1.0~\n'
        r = run_script(script_path, ['--dashed', 'a', 'b'])
        assert r.returncode == 2
        assert 'too early' in r.stderr

def test_standalone_star_args_windows():
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'path')
        r = run_script(script_path, ['home', '1', '--dashed', '2', '3'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'path home 1.0-+2.0~+3.0-\n'
        r = run_script(script_path, ['home', '1', '2', '--dashed'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'path home 1.0-+2.0~\n'    # trailing: nearest seg
        r = run_script(script_path, ['home', '--dashed'])
        assert r.returncode == 2
        assert 'at least one' in r.stderr

def test_standalone_help_sections():
    # parameter tables render inside a generated script, formatted
    # at run time by the embedded trio
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'mark')
        r = run_script(script_path, ['--help'])
        assert r.returncode == 0, r.stderr
        assert r.stdout.startswith('Marks a label on the canvas.')
        assert 'usage: mark' in r.stdout
        assert 'Arguments:' in r.stdout and 'Options:' in r.stdout
        assert '    label  the text to place.' in r.stdout
        assert '-a|--at <int> <int>' in r.stdout
        assert 'where to place it.' in r.stdout

def test_standalone_command_set_help():
    from appeal import emit_standalone_command_set
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'demo_cmds.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(DEMO_MODULE)
        sys.path.insert(0, d)
        try:
            import demo_cmds
            import importlib
            importlib.reload(demo_cmds)
            plans = {'greet': build(demo_cmds.greet), 'mark': build(demo_cmds.mark)}
            script = emit_standalone_command_set(plans, None, argv0='tool')
        finally:
            sys.path.remove(d)
            sys.modules.pop('demo_cmds', None)
        script_path = os.path.join(d, 'tool.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)

        r = run_script(script_path, ['help'])
        assert r.returncode == 0, r.stderr
        assert r.stdout.startswith('usage: tool command')
        assert 'mark   Marks a label on the canvas.' in r.stdout
        assert 'help   Print usage documentation' in r.stdout

        r = run_script(script_path, ['help', 'mark'])
        r2 = run_script(script_path, ['mark', '--help'])
        assert r.returncode == 0 and r.stdout == r2.stdout
        assert r.stdout.startswith('Marks a label on the canvas.')
        assert '    label  the text to place.' in r.stdout

def test_standalone_help():
    # the north star: --help works in a generated script, formatted
    # at run time by the embedded trio, importing nothing
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'greet')
        assert 'def render_help_page' in script
        assert 'def wrap_words' in script
        r = run_script(script_path, ['--help'])
        assert r.returncode == 0, r.stderr
        # greet has no docstring: no summary, so the page opens
        # with usage
        assert r.stdout.startswith('usage: greet')
        r2 = run_script(script_path, ['-h'])
        assert r2.stdout == r.stdout

def test_standalone_tuple_slot():
    # tuple slots emit a tuple display, not a call: no import needed
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'move')
        r = run_script(script_path, ['3', '4'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'move (3, 4) slow\n'
        r = run_script(script_path, ['--fast'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'move (0, 0) fast\n'
        r = run_script(script_path, ['3'])
        assert r.returncode == 2
        assert 'error:' in r.stderr

def test_standalone_app_option_override():
    # @app.option's strings are baked into the emitted table; the
    # standalone script never needs the decorator at runtime
    from appeal.build import add_option_override
    def decorate(command):
        add_option_override(command, 'shout', ('-S', '--yell'), default=False)
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'greet', decorate)

        r = run_script(script_path, ['world', '--yell'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'HELLO, WORLD!\n'

        r = run_script(script_path, ['world', '-S', '--times', '2'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'HELLO, WORLD! HELLO, WORLD!\n'

        r = run_script(script_path, ['world', '--shout'])   # overruled away
        assert r.returncode == 2
        assert '--shout' in r.stderr

def test_standalone_star_args():
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'cp')
        r = run_script(script_path, ['a', 'b', 'c', 'dest'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'copy a+b+c -> dest\n'

def test_standalone_plucks_minimal_runtime():
    # the emitter plucks only the snippets the parser needs out of
    # the runtime warehouse.  a plain command gets the core and the
    # help system (help is a permanent fixture, and brings big's
    # word-wrap trio via requires)--and none of the vocabulary,
    # collectors, folds, windows, or command-set machinery.
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'greet')
        for expected in ('def parse_tokens', 'def check_count', 'def run_main',
                         'def render_help_page', 'def wrap_words'):
            assert expected in script, f'minimal script is missing {expected!r}'
        for unexpected in ('def split(', 'def validate(', 'def counter(',
                           'class accumulator', 'def accumulate(',
                           'def collect_mapping(', 'class MultiOption',
                           'def window_options(', 'def run_command_set(',
                           # "if you don't call split, you don't
                           # need multisplit."  --larry
                           'def multisplit', 'def multistrip'):
            assert unexpected not in script, f'minimal script needlessly contains {unexpected!r}'
        # ...and it still runs.
        r = run_script(script_path, ['world'])
        assert r.returncode == 0, r.stderr

def test_standalone_is_standalone():
    # the north star's teeth, part 1: the generated text imports
    # nothing but the stdlib and the user's own module
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'greet')
        for forbidden in ('import appeal', 'from appeal', 'import big', 'from big'):
            assert forbidden not in script, f'standalone script contains {forbidden!r}'

        # part 2: it runs in an environment where appeal/appeal2/big
        # aren't even importable (cwd is the tmpdir; no repo on path),
        # and afterwards none of them appear in sys.modules
        probe = (
            "import sys, runpy\n"
            "sys.argv = ['greet', 'world']\n"
            "try:\n"
            f"    runpy.run_path({script_path!r}, run_name='__main__')\n"
            "except SystemExit as e:\n"
            "    assert (e.code or 0) == 0, e.code\n"
            "bad = sorted(m for m in sys.modules"
            " if m.split('.')[0] in ('appeal', 'big'))\n"
            "print('CLEAN' if not bad else 'CONTAMINATED ' + ' '.join(bad))\n"
            )
        r = subprocess.run(
            [sys.executable, '-c', probe],
            capture_output=True, text=True, cwd=d,
            env={'PATH': os.environ.get('PATH', '')},
            )
        assert r.returncode == 0, r.stderr
        assert 'CLEAN' in r.stdout, r.stdout

def test_standalone_nested_converters():
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'between')
        r = run_script(script_path, ['a', 'b'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'between a+b None\n'
        r = run_script(script_path, ['a', 'b', 'c', 'd'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'between a+b c+d\n'
        r = run_script(script_path, ['a', 'b', 'c'])
        assert r.returncode == 2
        assert 'expected 2 or 4' in r.stderr, r.stderr

def test_standalone_child_options_and_collectors():
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'sketch')
        r = run_script(script_path, ['dot'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'sketch dot none \n'
        r = run_script(script_path, ['dot', '--dashed', '2.5', '--tag', 'a', '-t', 'b'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'sketch dot 2.5~ a+b\n'
        # option owned by an operand-less group: the group is
        # FORCED (v1 semantics), in a script that has never
        # imported appeal
        r = run_script(script_path, ['dot', '--dashed'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'sketch dot 1.0~ \n'    # stroke() defaults, dashed

def test_standalone_refuses_unimportable_by_name():
    # the north star's teeth, part 3: constructs that can't survive
    # standalone emission refuse loudly, naming the offender
    def local_command(x):
        return x
    plan = build(local_command)
    # in-process works fine...
    parse = compile_plan(plan)
    assert parse(['hi']) == 'hi'
    # ...standalone refuses by name (nested callable)
    try:
        emit_standalone(plan)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'local_command' in str(e)

    lam = lambda x: x
    def cmd(a: lam):
        return a
    # lambda converter: same refusal.  (which offender gets named
    # first depends on ref order; both are unimportable, either
    # name is an honest refusal)
    plan = build(cmd)
    try:
        emit_standalone(plan)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert ('lambda' in str(e)) or ('cmd' in str(e))


# ---------------------------------------------------------------------
# the docstring parser (composable documentation, proposal §8.7.1)

def test_parse_docstring():
    from appeal.help import parse_docstring

    # the docstring is input, never output: sections are slurped
    # out, prose coalesces into one blob in source order.
    doc = (
        "Summary line for foo.\n"
        "\n"
        "First line of docs for foo.\n"
        "\n"
        "Arguments:\n"
        "  x: the thing to foo\n"
        "\n"
        "Options:\n"
        "  verbose: Verbosity, man.\n"
        "\n"
        "This is the second line of docs."
    )
    c = parse_docstring(doc, "foo")
    assert c['summary'] == ['Summary line for foo.']
    assert c['documentation'] == [
        'First line of docs for foo.', '', 'This is the second line of docs.']
    assert c['arguments'] == {'x': ['the thing to foo']}
    assert c['options'] == {'verbose': ['Verbosity, man.']}
    assert c['commands'] == {}

    # entry text is kept as dedented lines, kid gloves: relative
    # indentation (code!) survives, nothing is flattened.
    doc = (
        "Sum.\n"
        "\n"
        "Arguments:\n"
        "  x: the thing.\n"
        "      More about x.\n"
        "          code = here\n"
        "  y: simple."
    )
    c = parse_docstring(doc, "f")
    assert c['arguments'] == {
        'x': ['the thing.', 'More about x.', '    code = here'],
        'y': ['simple.'],
    }

    # a bare 'name:' first line, documentation all in continuation
    c = parse_docstring("Arguments:\n  x:\n     doc for x", "f")
    assert c['arguments'] == {'x': ['doc for x']}

    # heading-free docstring: the do-almost-nothing behavior is
    # emergent.  bare top-level 'name: text' lines are NOT entries;
    # the plan-resolution heuristic is retired.
    c = parse_docstring("Just prose.\n\nNote: this stays prose.", "f")
    assert c['summary'] == ['Just prose.']
    assert c['documentation'] == ['Note: this stays prose.']
    assert not (c['arguments'] or c['options'] or c['commands'])

    # empty and None
    for empty in (None, '', '\n\n'):
        c = parse_docstring(empty, "f")
        assert c['summary'] == [] and c['documentation'] == []

    # Commands: and Subcommands: are machine-identical
    c = parse_docstring("Subcommands:\n  serve: Serves.", "f")
    assert c['commands'] == {'serve': ['Serves.']}


def test_parse_docstring_errors():
    from appeal.help import parse_docstring
    def refuses(doc, *needles):
        try:
            parse_docstring(doc, "f")
        except AppealConfigurationError as e:
            for needle in needles:
                assert needle in str(e), f'{needle!r} not in {e}'
            return
        assert False, f'expected AppealConfigurationError for {doc!r}'

    # 'Sub-commands:' is detected and raised at the user
    refuses("Sub-commands:\n  x: y", "Subcommands:", "Sub-commands:")
    # one section per kind--including via the Commands:/Subcommands: alias
    refuses("Options:\n  a: b\n\nOptions:\n  c: d", "duplicate")
    refuses("Commands:\n  a: b\n\nSubcommands:\n  c: d",
            "duplicate", "same section")
    # exact spellings: whitespace variants are refused, not demoted
    refuses("Arguments: \n  x: y", "exact")
    refuses("  Arguments:\n  x: y", "exact")
    # entries are indented; only a blank line ends a section
    refuses("Options:\nx: y", "isn't indented")
    # the first line of a section must be an entry
    refuses("Options:\n  just some text", "expected a")
    # an entry documented twice
    refuses("Options:\n  a: b\n  a: c", "documented twice")
    # an entry-shaped line at the wrong indent is malformed,
    # not silently adopted
    refuses("Options:\n    a: b\n  c: d", "expected a")


def test_merge_docs():
    from appeal.help import merge_docs

    def int_float(i: int, f: float):
        """
        An int and a float.

        Arguments:
          i: The integer part.
          f: The float part.
        """

    def my_converter(i_f: int_float, s: str, *, verbose=False):
        """
        Gathers the whole bundle.

        Prose about gathering, which stays home.

        Options:
          verbose: Print more output.
        """

    def recurse2(a: str, b: my_converter = None):
        """
        The showpiece.

        Arguments:
          a: The first thing.
          i: Overridden: how many knocks.
        """

    c = merge_docs(build(recurse2))
    # prose and summaries stay home: only the command's own
    assert c['summary'] == ['The showpiece.']
    assert c['documentation'] == []
    # rows in plan order; entries merge up; nearest wins ('i');
    # undocumented surfaces get empty rows ('s')
    assert c['arguments'] == [
        ('a', ['The first thing.']),
        ('i', ['Overridden: how many knocks.']),
        ('f', ['The float part.']),
        ('s', []),
    ]
    assert c['options'] == [('-v|--verbose', ['Print more output.'])]
    assert c['commands'] == []

    # a keyword-only-no-default parameter is a trailing operand:
    # documenting it under Arguments: is correct
    def trailing_ok(a, *, required_kw):
        """
        Arguments:
          required_kw: a trailing operand.
        """
    c = merge_docs(build(trailing_ok))
    assert c['arguments'] == [('a', []), ('required_kw', ['a trailing operand.'])]

    # commands merge only when the plan dispatches
    def dispatcher():
        """
        Top.

        Commands:
          serve: Serves the thing.
        """
    c = merge_docs(build(dispatcher), command_names=('serve', 'help'))
    assert c['commands'] == [('serve', ['Serves the thing.']), ('help', [])]


def test_merge_docs_errors():
    from appeal.help import merge_docs

    def refuses(f, *needles, command_names=None):
        try:
            merge_docs(build(f), command_names=command_names)
        except AppealConfigurationError as e:
            for needle in needles:
                assert needle in str(e), f'{needle!r} not in {e}'
            return
        assert False, f'expected AppealConfigurationError for {f.__name__}'

    def helper(x: int, y: int):
        return (x, y)

    def bad_unknown(a):
        """
        Arguments:
          zed: nope.
        """
    refuses(bad_unknown, "'zed'", 'not a parameter')

    def bad_invisible(a: str, b: helper = None):
        """
        Arguments:
          b: the pair.
        """
    refuses(bad_invisible,
            "'b' is not one of the visible command-line arguments "
            "of 'bad_invisible'")

    def bad_kind(a, *, flag=False):
        """
        Arguments:
          flag: wrong side.
        """
    refuses(bad_kind, 'is an option, not an argument')

    def bad_kind2(a, *, flag=False):
        """
        Options:
          a: wrong side.
        """
    refuses(bad_kind2, 'is an argument, not an option')

    def bad_commands(a):
        """
        Commands:
          serve: nope.
        """
    refuses(bad_commands, "doesn't dispatch to commands")

    def bad_command_word():
        """
        Commands:
          swerve: typo.
        """
    refuses(bad_command_word, "'swerve'", 'not one of the command words',
            command_names=('serve',))


if __name__ == '__main__':
    total, failures = atest.run(verbose='-v' in sys.argv, exit=False)
    print(f'{total} tests, {len(failures) if isinstance(failures, list) else failures} failures')
    sys.exit(1 if failures else 0)
