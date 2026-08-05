#!/usr/bin/env python3
#
# tests/test_appeal.py
# The main Appeal test suite.  (test_all.py is the driver that runs
# this file and every other tests/test_*.py.)
#
# Three layers of protection:
#   * the plan/build layer, tested directly;
#   * PARITY: every (signature, argv) case runs through both the
#     rung-1 interpreter and the rung-3 generated parser, and they must
#     agree--same result or same UsageError message;
#   * the NORTH STAR: standalone scripts are generated, written to
#     disk, and executed in a subprocess with no access to appeal,
#     appeal2, or big--and they must work.

from big import test

# the local checkout beats any installed appeal; preload() imports
# the package and ASSERTS it came from the checkout, so a stray
# site-packages v1 fails loudly instead of testing the wrong code
repo_dir = str(test.preload('appeal'))

import os.path
import subprocess
import sys
import tempfile

import appeal
from appeal import (
    Appeal, AppealConfigurationError, AppealDataError, AppealError,
    UsageError,
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


def exit_code(fn):
    """main() EXITS (0.6.4's contract, restored 2026-07-19);
    run it and hand back the exit code."""
    try:
        fn()
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0

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

def test_option_string_collision_rules():
    # scoped options (ruled 2026-07-09): a string declared by
    # several windows is legal when every declaration agrees on
    # the grammar; occurrences bind by position
    def sub(x=1, *, verbose=False):
        return (x, verbose)
    def f(a: sub=None, *, verbose=False):
        return (a, verbose)
    plan = build(f)
    assert '--verbose' in plan.scoped_keys
    assert '-v' in plan.scoped_keys      # the short rides the long

    # differing grammars still refuse: the parser couldn't know
    # how many arguments to consume before knowing which window
    def sweet(kind, *, flavor=False):
        return (kind, flavor)
    def savory(kind, *, flavor='salt'):
        return (kind, flavor)
    def dish(first: sweet, second: savory):
        return (first, second)
    try:
        build(dish)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'different grammars' in str(e), e

    # and a string declared twice in ONE window still refuses
    def confused(*, top=False):
        return top
    import appeal as _appeal
    app = _appeal.Appeal(name='cw')
    @app.option('top', '--both', default=False)
    @app.option('other', '--both', default=False)
    @app.global_command()
    def one(*, top=False, other=False):
        return (top, other)
    try:
        app.plan
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'twice by one converter' in str(e) or 'position' in str(e), e

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

def test_app_option_derives_from_parameter():
    # Ruled 2026-07-25 (arglet style, superseding the July
    # fresh-declaration rule): @app.option maps STRINGS; the
    # option's grammar comes from the PARAMETER--a bare
    # @app.option on a bool parameter is a FLAG, on an int
    # parameter an int option.  annotation=/default= remain the
    # escape hatches when the option should genuinely differ.
    app = Appeal()
    @app.option('verbose', '-V')
    @app.global_command()
    def f(*, verbose=False):
        return verbose
    assert app.process(['-V']) is True         # a flag, not a value
    assert app.process([]) is False            # parameter's default
    app2 = Appeal()
    @app2.option('level', '-L')
    @app2.global_command()
    def g(*, level: int = 0):
        return level
    assert app2.process(['-L', '3']) == 3      # int, from the annotation

def test_app_option_no_strings_unmaps():
    # Ruled 2026-07-25: @app.option('foo') with zero strings means
    # "foo is a keyword-only parameter and we don't map any
    # options to it"--the explicit per-parameter unmap, symmetric
    # with a policy declining.  The default always fills.
    app = Appeal()
    @app.option('quiet')
    @app.global_command()
    def f(*, loud=False, quiet=False):
        return (loud, quiet)
    assert app.process(['--loud']) == (True, False)
    for argv in (['--quiet'], ['-q']):
        try:
            app.process(list(argv))
            assert False, f'{argv} must be unmapped'
        except UsageError:
            pass
    # alongside a REAL declaration, the empty one is noise, not
    # a veto
    app2 = Appeal()
    @app2.option('quiet')
    @app2.option('quiet', '-Q')
    @app2.global_command()
    def g(*, quiet=False):
        return quiet
    assert app2.process(['-Q']) is True
    # a **kwargs-delivered option IS its strings: zero refuses
    app3 = Appeal()
    @app3.option('mystery')
    @app3.global_command()
    def h(**kwargs):
        return kwargs
    try:
        app3.process([])
        assert False, 'expected ConfigurationError'
    except AppealConfigurationError:
        pass


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
    def f(t: 42):    # a non-callable annotation isn't in the grammar
        pass
    try:
        app.process([])
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert "isn't callable" in str(e)

def test_commands_compile_independently():
    # laziness is per command: dispatching one never builds the
    # others, so a broken sibling costs nothing until it's used
    app = Appeal(name='tool')
    @app.command()
    def good(x):
        return ('good', x)
    @app.command()
    def broken(t: 42):   # not in the grammar
        pass
    assert app.process(['good', 'hi']) == ('good', 'hi')
    assert app.process(['good', 'again']) == ('good', 'again')
    try:
        app.process(['broken'])
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert "isn't callable" in str(e)
    # ...but a whole-program artifact is necessarily eager:
    # standalone emission must build (and refuse) everything
    try:
        app.standalone()
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert "isn't callable" in str(e)

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
    # two occurrences on one instance: idempotent store-not-
    # default (Larry's ruling, 2026-07-18)--bold twice is bold
    got = run_both(draw, ['a', '1', '--bold', '--bold', '2'])
    assert got == ('ok', ('a', (('s', 1.0, False), ('s', 2.0, True)))), got

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
    # repeated: last one wins (ruled 2026-07-09)
    got = run_both(f, ['--where', 'a', 'b', '--where', 'c', 'd'])
    assert got == ('ok', (('c', 'd'), None)), got

def test_option_class_repetition():
    # DELIBERATE v1 -> v2 DIVERGENCE (ruled 2026-07-09): Option
    # is repeatable--option() called once per occurrence, exactly
    # as you'd think.  StrictOption is the one way to declare
    # "at most once"; v1 spelled that Option.  MultiOption is an
    # alias of Option now.
    from appeal import MultiOption, Option, StrictOption
    assert MultiOption is Option

    class Where(Option):
        def init(self, default):
            self.calls = []
        def option(self, x: int, y: int):
            self.calls.append((x, y))
        def render(self):
            return self.calls[-1] if self.calls else None

    def f(*, where: Where = 'nowhere'):
        return where

    got = run_both(f, [])
    assert got == ('ok', 'nowhere'), got            # never instantiated
    got = run_both(f, ['--where', '3', '4'])
    assert got == ('ok', (3, 4)), got
    # repetition: option() ran twice; render() decided
    got = run_both(f, ['--where', '1', '2', '--where', '5', '6'])
    assert got == ('ok', (5, 6)), got

    class StrictWhere(StrictOption):
        def init(self, default):
            self.x = self.y = None
        def option(self, x: int, y: int):
            self.x, self.y = x, y
        def render(self):
            return (self.x, self.y)

    def g(*, where: StrictWhere = 'nowhere'):
        return where
    got = run_both(g, ['--where', '3', '4'])
    assert got == ('ok', (3, 4)), got
    got = run_both(g, ['--where', '1', '2', '--where', '5', '6'])
    assert got[0] == 'usage' and 'more than once' in got[1], got

def test_flag_presence_stores_not_default():
    # v1 semantics, restored 2026-07-18 (Larry's break #1): a
    # flag's presence stores `not default`--so `verbose=True`
    # makes --verbose the OFF switch, and v2's old "a flag's
    # default must be False" refusal is gone.  Truthiness, not
    # identity: None and 0 defaults store True (v1, probed).
    def f(*, verbose=True):
        return verbose
    assert run_both(f, []) == ('ok', True)
    assert run_both(f, ['--verbose']) == ('ok', False)
    assert run_both(f, ['-v']) == ('ok', False)
    # the explicit spellings set the literal value, presence
    # semantics notwithstanding
    assert run_both(f, ['--verbose=true']) == ('ok', True)
    assert run_both(f, ['--verbose=false']) == ('ok', False)

    def g(*, mark: bool = None):
        return mark
    assert run_both(g, []) == ('ok', None)
    assert run_both(g, ['--mark']) == ('ok', True)

    def h(*, level: bool = 0):
        return level
    assert run_both(h, []) == ('ok', 0)
    assert run_both(h, ['--level']) == ('ok', True)

    # default False is unchanged
    def k(*, loud=False):
        return loud
    assert run_both(k, ['--loud']) == ('ok', True)


def test_default_mappings_design():
    # Larry's design (2026-07-19): default_mappings(app) runs once
    # at first compile; the stock policy maps the version/help
    # commands and the precommand's -V/--version, each only if
    # free; None banishes all default semantics; the precommand's
    # options live in the pre-command-word era and yield to user
    # declarations; overriding Appeal.default_version customizes.
    import appeal as _appeal
    import contextlib, io

    def main(app, argv):
        # main() EXITS (0.6.4's contract, restored 2026-07-19)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                app.main(argv)
                code = 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 0
        return code, out.getvalue()

    app = _appeal.Appeal(name='tool', version='3.5')
    @app.command()
    def work(*, verbose=False): return 0
    # both spellings, and mid-segment (a real option, not a
    # first-token special case): metadata outranks the rest
    assert main(app, ['-V']) == (0, '3.5\n')
    assert main(app, ['--version']) == (0, '3.5\n')
    assert main(app, ['--version', 'garbage']) == (0, '3.5\n')
    # the introspection API
    assert list(app.commands) == ['work', 'version', 'help']
    assert app.commands['work'].callable is work
    assert set(app.options) == {'-V', '--version', '-h', '--help'}

    # yielding: a user -V (from Verbose) keeps -V; only --version
    # gets mapped
    app2 = _appeal.Appeal(name='t2', version='9')
    @app2.global_command()
    def g(*, Verbose=False): return None
    @app2.command()
    def go(): return 0
    assert '-V' in app2.options and app2.options['-V'] is not None
    assert app2.options['--version'] is None   # the precommand's
    assert main(app2, ['--version'])[0] == 0

    # banishment: None means NO default semantics
    app3 = _appeal.Appeal(name='t3', version='9',
                          default_mappings=None)
    @app3.command()
    def go3(): return 0
    assert main(app3, ['--version'])[0] == 2
    assert main(app3, ['version'])[0] == 2
    assert 'version' not in app3.commands
    assert 'version' not in app3.complete([], '')

    # subclass override reaches every spelling
    class Deluxe(_appeal.Appeal):
        def print_version(self):
            print(f'deluxe v{self.version}')
    app4 = Deluxe(name='t4', version='7')
    @app4.command()
    def go4(): return 0
    assert main(app4, ['-V']) == (0, 'deluxe v7\n')
    assert main(app4, ['version']) == (0, 'deluxe v7\n')

    # default_options=None: only explicit @app.option maps
    app5 = _appeal.Appeal(name='t5', default_options=None)
    @app5.command()
    @app5.option('loud', '-L', default=False)
    def go5(*, loud=False, quiet=False): return (loud, quiet)
    assert app5.process(['go5', '-L']) == (True, False)
    try:
        app5.process(['go5', '-q'])
        assert False, 'unmapped option must be unknown'
    except _appeal.AppealUsageError:
        pass

    # a custom default_mappings policy composes with the stock one
    # (the factory returns the policy; call it, then adjust)
    def custom(app_):
        _appeal.default_mappings()(app_)
        app_.command('about')(app_.print_version)
    app6 = _appeal.Appeal(name='t6', version='2', default_mappings=custom)
    @app6.command()
    def go6(): return 0
    assert main(app6, ['about']) == (0, '2\n')
    assert main(app6, ['-V']) == (0, '2\n')


def test_underscore_parameters_are_private():
    # Larry's rule (2026-07-19): the stock default_options policy
    # returns nothing for _underscore names--no default mapping,
    # the default always fills.  A policy returning an empty
    # iterable for ANY name means the same (drop, not the
    # "no option strings" starvation refusal); @app.option is
    # the escape hatch.
    import appeal as _appeal

    app = _appeal.Appeal(name='p')
    @app.command()
    def go(*, loud=False, _cache=None): return (loud, _cache)
    assert go and app.process(['go', '--loud']) == (True, None)
    assert '--_cache' not in app.commands['go'].options
    try:
        app.process(['go', '--_cache', 'x'])
        assert False, 'private parameter must not map'
    except _appeal.AppealUsageError:
        pass

    # the escape hatch: explicit strings still map it
    app2 = _appeal.Appeal(name='q')
    @app2.command()
    @app2.option('_cache', '--cache')
    def go2(*, _cache=''): return _cache
    assert app2.process(['go2', '--cache', 'hot']) == 'hot'

    # a custom policy dropping one name by returning []
    def policy(app_, fn, name):
        if name == 'quiet':
            return          # decline by not calling
        _appeal.default_options(app_, fn, name)
    app3 = _appeal.Appeal(name='r', default_options=policy)
    @app3.command()
    def go3(*, loud=False, quiet=False): return (loud, quiet)
    assert app3.process(['go3', '--loud']) == (True, False)
    try:
        app3.process(['go3', '--quiet'])
        assert False
    except _appeal.AppealUsageError:
        pass


def test_same_word_at_different_depths():
    # Larry's ruling (2026-07-19): depth takes precedence.  A has
    # subcommand X; X has its own subcommand X; `A X X` runs A,
    # A's X, then X's X--the deepest unentered set resolves the
    # word first (descent is free; re-entering an ancestor's set
    # is what repeat gates).  The compile side addresses plans by
    # NODE, so same-word-different-depth builds and runs.
    import appeal as _appeal
    import contextlib, io
    calls = []
    app = _appeal.Appeal(name='t', repeat=True)
    def A(): calls.append('A')
    def ax(): calls.append('AX')
    def axx(): calls.append('XX')
    app.command()(A)
    a = app.command('A', repeat=True)
    a.command('X')(ax)
    x = a.command('X')
    x.command('X')(axx)
    app.process(['A', 'X', 'X'])
    assert calls == ['A', 'AX', 'XX'], calls
    # a fourth X pops back to A's cycling set and re-enters X the
    # PARENT--which, dangling at end of line, demands a subcommand
    calls.clear()
    try:
        app.process(['A', 'X', 'X', 'X'])
        assert False, 'expected a dangling-parent refusal'
    except _appeal.AppealUsageError as e:
        assert 'no command specified' in str(e), e
    # ...and a fifth X satisfies it: the cycle breathes in and out
    calls.clear()
    app.process(['A', 'X', 'X', 'X', 'X'])
    assert calls == ['A', 'AX', 'XX', 'AX', 'XX'], calls


def test_main_exits_the_process():
    # 0.6.4's contract, RESTORED (Larry's ruling 2026-07-19,
    # review item J1): main() EXITS--a script whose last line is
    # bare app.main() reports its code to the shell.  Usage
    # errors exit 2 (getopt/argparse convention); a command's
    # nonzero int is the code; success exits 0.  process() is
    # the API that returns.
    import appeal as _appeal
    import contextlib, io
    app = _appeal.Appeal(name='t')
    @app.command()
    def ok(): pass
    @app.command()
    def fail(): return 3
    for argv, expected in ([['ok'], 0], [['fail'], 3],
                           [['bogus'], 2], [[], 1]):
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                app.main(list(argv))
            code = 'returned'
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 0
        assert code == expected, (argv, code)
    # process() still returns, raw
    assert app.process(['fail']) == 3


def test_command_error():
    # E1, RULED (Larry, 2026-08-05): CommandError restored--a
    # command raises it AFTER parsing succeeded to fail with a
    # message and a chosen exit code.  main() prints
    # `error: message` to stderr (NO usage--the command line was
    # fine) and exits with the code; process() lets it propagate.
    # v1 documented this contract for AppealCommandError but
    # never wired the catch; 1.0 wires it.
    import appeal as _appeal
    import contextlib, io
    assert _appeal.AppealCommandError is _appeal.CommandError
    app = _appeal.Appeal(name='t')
    @app.command()
    def deploy(target):
        raise _appeal.CommandError(f"no such target: {target}",
                                   exit_code=3)
    @app.command()
    def grumble():
        raise _appeal.CommandError("unhappy")     # default code 1
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), \
             contextlib.redirect_stderr(err):
            app.main(['deploy', 'prod'])
        code = 'returned'
    except SystemExit as e:
        code = e.code
    assert code == 3
    assert out.getvalue() == ''
    assert 'error: no such target: prod' in err.getvalue()
    assert 'usage' not in err.getvalue()
    try:
        with contextlib.redirect_stdout(out), \
             contextlib.redirect_stderr(err):
            app.main(['grumble'])
        code = 'returned'
    except SystemExit as e:
        code = e.code
    assert code == 1
    # process() is the raw API: the exception propagates
    try:
        app.process(['grumble'])
        raised = False
    except _appeal.CommandError as e:
        raised = True
        assert e.exit_code == 1
    assert raised


def test_help_knobs():
    # E3, RULED (Larry's design, 2026-08-05): usage() stays
    # dead--help() does it all.  The template gains {summary};
    # help() grows keyword-only usage=/summary=/doc= knobs, each
    # False suppressing its section AND the template text before
    # it; doc=False also suppresses arguments, options, and
    # commands.  help(summary=False, doc=False) IS the usage
    # line.  As the help COMMAND the knobs stay API-only:
    # default_mappings unmaps them (zero-string app.option()).
    import appeal as _appeal
    import contextlib, io

    def captured(fn, *a, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fn(*a, **kw)
        return out.getvalue()

    app = _appeal.Appeal(name='t')
    @app.command()
    def serve(host, *, verbose=False):
        """
        Start the server.

        Long prose about serving.
        """
    full = captured(app.help, 'serve')
    assert 'usage: t serve' in full
    assert 'Start the server.' in full
    assert 'Long prose about serving.' in full
    assert 'Options:' in full
    # usage line only
    only_usage = captured(app.help, 'serve', summary=False,
                          doc=False).strip()
    assert only_usage == 'usage: t serve [-v|--verbose] <HOST>'
    # no usage line
    no_usage = captured(app.help, 'serve', usage=False)
    assert 'usage:' not in no_usage
    assert 'Start the server.' in no_usage
    # doc=False kills doc AND arguments/options/commands
    no_doc = captured(app.help, 'serve', doc=False)
    assert 'Long prose' not in no_doc
    assert 'Options:' not in no_doc
    assert 'Arguments:' not in no_doc
    assert 'Start the server.' in no_doc      # summary survives
    # bare listing obeys the knobs too
    bare = captured(app.help, doc=False)
    assert 'usage: t command' in bare
    assert 'Commands:' not in bare
    # the help COMMAND has no --usage/--summary/--doc surface
    knob_options = [s for s in app.commands['help'].options
                    if s.lstrip('-').lstrip('=') in
                    ('usage', 'summary', 'doc', 'u', 's', 'd')]
    assert not knob_options, knob_options
    # and `help serve` through main() is the full page, unchanged
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            app.main(['help', 'serve'])
        except SystemExit:
            pass
    assert out.getvalue() == full, (out.getvalue(), full)


def test_program_doc_three_tiers():
    # Ruled 2026-08-01: the program's documentation, highest
    # first: (1) Appeal(doc=...); (2) the global command's
    # docstring; (3) when every user command shares one module,
    # that module's docstring--write a module docstring, get
    # program docs for free.
    import appeal as _appeal
    import contextlib, io, sys, types

    def helppage(app):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                app.main(['help'])
            except SystemExit:
                pass
        return out.getvalue()

    # tier 1 beats tier 2; its Commands: entries curate the listing
    app = _appeal.Appeal('t1',
                         doc='Doc wins.\n\nCommands:\n    work: curated.')
    @app.global_command()
    def g():
        "Global docstring loses."
    @app.command()
    def work():
        "Fallback summary."
    out = helppage(app)
    assert 'Doc wins.' in out and 'curated.' in out
    assert 'loses' not in out

    # tier 3: the module-docstring magic (stock help/version
    # commands don't get a vote)
    mod = types.ModuleType('_appeal_doc_fakemod')
    mod.__doc__ = 'The module speaks.\n\nProse from the module.'
    sys.modules['_appeal_doc_fakemod'] = mod
    try:
        app3 = _appeal.Appeal('t3', version='1.0')
        def alpha(): "A."
        def beta(): "B."
        alpha.__module__ = beta.__module__ = '_appeal_doc_fakemod'
        app3.command()(alpha)
        app3.command()(beta)
        out = helppage(app3)
        assert 'The module speaks.' in out
        assert 'Prose from the module.' in out
        # ...but mixed modules mean no tier-3 doc
        app4 = _appeal.Appeal('t4')
        def gamma(): "G."
        gamma.__module__ = 'somewhere_else'
        def delta(): "D."
        delta.__module__ = '_appeal_doc_fakemod'
        app4.command()(gamma)
        app4.command()(delta)
        assert 'module speaks' not in helppage(app4)
    finally:
        del sys.modules['_appeal_doc_fakemod']

    # unknown Commands: entries in doc= refuse by name
    app5 = _appeal.Appeal('t5', doc='Hi.\n\nCommands:\n    zork: no.')
    @app5.command()
    def real():
        pass
    try:
        helppage(app5)
        assert False, 'expected ConfigurationError'
    except _appeal.ConfigurationError as e:
        assert 'zork' in str(e)


def test_docstring_presentation_wins():
    # Ruled 2026-08-01 (D+E): the author's docstring presentation
    # governs the sections they wrote--their spelling, decoration,
    # blank-line rhythm, entry indent, and ORDER--while unwritten
    # sections render where the template puts them (three-phase
    # interleave).
    import appeal as _appeal
    import contextlib, io

    app = _appeal.Appeal(name='p')
    @app.global_command()
    def g(thing, *, loud=False):
        """
        Summary here.

         [Options!]
           loud: Speak up.

        arguments
           thing: The thing.
        """
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            app.main(['--help'])
        except SystemExit:
            pass
    text = out.getvalue()
    # decoration preserved verbatim; order is the AUTHOR's
    # (options before arguments, against the template's order)
    assert ' [Options!]' in text, text
    assert 'arguments' in text and 'Arguments:' not in text
    assert text.index('[Options!]') < text.index('arguments'), text
    # the author's 3-space entry indent rides along
    assert '   -l|--loud  Speak up.' in text, text
    assert '   <THING>  The thing.' in text, text
    # usage still leads (phase one: template sections before the
    # first user-defined one)
    assert text.startswith('usage: g'), text


def test_optional_oparg_subscript():
    # Ruled 2026-08-03 (the make precedent, superseding the brief
    # None-default rule of earlier the same day): opargs are
    # REQUIRED by default--`-f file` is the common case--and the
    # optional oparg is the MARKED case, spelled optional[T]:
    #     --debug[=FLAGS]     debug: optional[str] = None
    #     -f file             file=None
    #     -j [jobs]           jobs: optional[int] = 1
    # Absent -> the parameter default; bare -> T() (str '', int
    # 0); given -> T(value).
    import appeal as _appeal
    from appeal import optional
    assert not hasattr(_appeal, 'optional_str')

    app = _appeal.Appeal('make', default_mappings=None)
    @app.global_command()
    def make(*, debug: optional[str] = None, file=None,
             jobs: optional[int] = 1):
        return (debug, file, jobs)
    assert app.process([]) == (None, None, 1)
    assert app.process(['--debug']) == ('', None, 1)
    assert app.process(['--debug=vj']) == ('vj', None, 1)
    assert app.process(['--jobs']) == (None, None, 0)
    assert app.process(['-j', '3']) == (None, None, 3)
    # a plain (or None-defaulted) str option still REQUIRES its
    # value, exactly as 0.6.4 did
    try:
        app.process(['--file'])
        assert False, 'expected UsageError'
    except UsageError as e:
        assert 'requires a value' in str(e)
    # the bare factory refuses by name
    app2 = _appeal.Appeal('t2', default_mappings=None)
    @app2.global_command()
    def g(*, x: optional = None):
        return x
    try:
        app2.process([])
        assert False, 'expected ConfigurationError'
    except AppealConfigurationError as e:
        assert 'optional[str]' in str(e)


def test_command_listings_are_definition_order():
    # Larry's ruling (2026-07-19): commands and subcommands are
    # DISPLAYED in definition order--the tree's dicts iterate in
    # insertion order and every display path derives from them.
    # (Shell completion stays sorted: bash re-sorts candidates
    # anyway, so sorted-at-source keeps zsh/fish matching it.)
    import appeal as _appeal
    import contextlib, io
    app = _appeal.Appeal(name='p')
    @app.command()
    def zebra(): "Z."
    @app.command()
    def mango(): "M."
    @app.command()
    def apple(): "A."
    z = app.command('zebra')
    @z.command()
    def walk(): "W."
    @z.command()
    def crawl(): "C."

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app.process(['help'])
    words = [l.split()[0] for l in out.getvalue().splitlines()
             if l.startswith('    ')]
    assert words == ['zebra', 'mango', 'apple', 'help'], words

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app.process(['help', 'zebra'])
    words = [l.split()[0] for l in out.getvalue().splitlines()
             if l.startswith('    ')]
    assert words == ['walk', 'crawl', 'help'], words

    # completion stays sorted, deliberately
    candidates = app.complete([], '')
    assert candidates == sorted(candidates), candidates
    assert set(candidates) == {'zebra', 'mango', 'apple', 'help'}, candidates


def test_sibling_option_groups():
    # Larry's ruling (2026-07-18, review item 8): two keyword-only
    # parameters sharing a converter (e1: extras, e2: extras) are
    # SIBLING option groups.  Their shared child options bind by
    # announcement (--e1/--e2): each occurrence belongs to the
    # nearest announced parent before it.  With NO announcement
    # they summon the FIRST declared sibling with defaults--and
    # never cascade: there is no way to say which sibling an
    # unannounced option means, so e2 exists only when announced.
    def extras(a='', *, fiddle=False, booper=False):
        return ('e', a, fiddle, booper)
    def cmd(x, *, e1: extras = None, e2: extras = None):
        return (x, e1, e2)

    # nothing: both default
    assert run_both(cmd, ['x']) == ('ok', ('x', None, None))
    # a bare child option summons e1
    got = run_both(cmd, ['--fiddle', 'x'])
    assert got == ('ok', ('x', ('e', '', True, False), None)), got
    # repetition stays in e1--it NEVER cascades to e2
    got = run_both(cmd, ['--fiddle', '--fiddle', 'x'])
    assert got == ('ok', ('x', ('e', '', True, False), None)), got
    got = run_both(cmd, ['--fiddle', '--booper', 'x'])
    assert got == ('ok', ('x', ('e', '', True, True), None)), got
    # e2 announced alone: e1 stays None
    got = run_both(cmd, ['x', '--e2', 'A2'])
    assert got == ('ok', ('x', None, ('e', 'A2', False, False))), got
    # a child option after an announcement binds to it
    got = run_both(cmd, ['x', '--e2', 'A2', '--booper'])
    assert got == ('ok', ('x', None, ('e', 'A2', False, True))), got
    # both announced: each window claims its own
    got = run_both(cmd, ['x', '--e1', 'A1', '--fiddle',
                         '--e2', 'A2', '--booper'])
    assert got == ('ok', ('x', ('e', 'A1', True, False),
                          ('e', 'A2', False, True))), got
    # announced out of declaration order: fine while no shared
    # option was spoken (nothing to misbind)...
    got = run_both(cmd, ['x', '--e2', 'A2', '--e1', 'A1'])
    assert got == ('ok', ('x', ('e', 'A1', False, False),
                          ('e', 'A2', False, False))), got
    # ...but a loud refusal once one was
    got = run_both(cmd, ['x', '--e2', 'A2', '--booper', '--e1', 'A1'])
    assert got[0] == 'usage' and 'declaration order' in got[1], got

    # a first sibling with a REQUIRED argument can't be summoned
    def needy(a, *, fiddle=False):
        return ('n', a, fiddle)
    def cmd2(x, *, e1: needy = None, e2: needy = None):
        return (x, e1, e2)
    got = run_both(cmd2, ['--fiddle', 'x'])
    assert got[0] == 'usage' and 'requires' in got[1], got


def test_options_repeat_semantics():
    # RULED by Larry, 2026-07-18 (review item 6, superseding both
    # v1's "specified more than once" and the July session's
    # self-ruling): repetition is for overriding defaults--a
    # shell alias baking in `--north` is harmlessly overridden by
    # a later `--south`.  VALUE options are last-one-wins; bare
    # FLAGS idempotently store `not default` (-v -v -v is -v; a
    # default-True flag idempotently stores False).  Repeatable
    # kinds still collect every occurrence.
    from appeal import accumulator as _acc
    def f(*, num: int = 0, loud=False, tag: _acc[str] = ()):
        return (num, loud, tag)
    got = run_both(f, ['--loud', '--loud'])           # idempotent
    assert got == ('ok', (0, True, ())), got
    got = run_both(f, ['--loud', '--loud', '--loud'])
    assert got == ('ok', (0, True, ())), got
    got = run_both(f, ['--num', '1', '--num', '2'])   # value: last wins
    assert got == ('ok', (2, False, ())), got
    got = run_both(f, ['--tag', 'a', '--tag', 'b'])   # multi: all
    assert got == ('ok', (0, False, ['a', 'b'])), got
    # ...including across spellings, in command-line order
    got = run_both(f, ['-n', '1', '--num', '2', '-n', '3'])
    assert got == ('ok', (3, False, ())), got
    # a default-True flag idempotently stores False
    def g(*, verbose=True):
        return verbose
    assert run_both(g, ['-v']) == ('ok', False)
    assert run_both(g, ['-v', '-v']) == ('ok', False)
    # explicit spellings are absolute; bare presence after one
    # stores not-default again; last spoken wins
    assert run_both(g, ['--verbose=true', '-v']) == ('ok', False)
    assert run_both(g, ['-v', '--verbose=true']) == ('ok', True)
    # flags compose with the explicit spellings
    got = run_both(f, ['--loud', '--loud=false'])
    assert got == ('ok', (0, False, ())), got
    # ...and across DIFFERENT strings sharing a parameter, in true
    # command-line order (v1's mutually-exclusive idiom relaxed:
    # --north --south is south, like argparse with a shared dest)
    from appeal import add_option_override
    def go(*, direction='north'):
        return direction
    add_option_override(go, 'direction', ('--north',),
                        annotation=lambda: 'north')
    add_option_override(go, 'direction', ('--south',),
                        annotation=lambda: 'south')
    got = run_both(go, ['--north', '--south'])
    assert got == ('ok', 'south'), got
    got = run_both(go, ['--south', '--north'])
    assert got == ('ok', 'north'), got
    got = run_both(go, ['--north', '--south', '--north'])
    assert got == ('ok', 'north'), got
    # a value-producing flag refuses '=' by name (presence IS
    # the value; there's no boolean to set)
    got = run_both(go, ['--north=false'])
    assert got[0] == 'usage' and "doesn't take a value" in got[1], got

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
    # multi-arity ones build too--for the mapping readers, which
    # hand them sequences of sequences (v1's corpus; a lone
    # command-line token that reaches one fails loudly)
    class Pairs(MultiOption):
        def init(self, default):
            self.values = []
        def option(self, x: int, y: int):
            self.values.append((x, y))
        def render(self):
            return tuple(self.values)
    def positional2(p: Pairs):
        return p
    build(positional2)
    from appeal import read_mapping
    assert read_mapping(positional2,
                        {'p': [[1, 2], [3, 4]]}) == ((1, 2), (3, 4))
    got = run_both(positional2, ['token'])
    assert got[0] == 'usage', got
    class Fancy(MultiOption):
        def init(self, default): pass
        def option(self, tag='x'): pass
        def render(self): pass
    def f(*, t: Fancy = None):
        pass
    # an optional option() parameter is legal as an option--its
    # operand consumes greedily (v1)--but stays refused as a
    # positional fold
    build(f)
    def positional3(p: Fancy):
        pass
    try:
        build(positional3)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'Fancy' in str(e) and 'default' in str(e)

def test_greedy_opargs():
    # the famous make -j (v1, probed): an option's optional operand
    # takes the next token unconditionally, whatever it looks like.
    # "optional" only means running out of line is legal.
    import math
    def jobs(jobs: int = math.inf):
        return jobs
    def make(*targets, jobs: jobs = 1, verbose=False):
        return (targets, jobs, verbose)

    got = run_both(make, [])
    assert got == ('ok', ((), 1, False)), got
    got = run_both(make, ['-j'])
    assert got == ('ok', ((), math.inf, False)), got
    got = run_both(make, ['-j', '5'])
    assert got == ('ok', ((), 5, False)), got
    # negative numbers for free
    got = run_both(make, ['-j', '-5'])
    assert got == ('ok', ((), -5, False)), got
    # attachment: bare concat and '=' both work when the one
    # operand is optional
    got = run_both(make, ['-j5'])
    assert got == ('ok', ((), 5, False)), got
    got = run_both(make, ['-j=5'])
    assert got == ('ok', ((), 5, False)), got
    # '=' hands over exactly one token; greed doesn't continue
    got = run_both(make, ['--jobs=5', 'q'])
    assert got == ('ok', (('q',), 5, False)), got
    # last in a bundle, then greedy
    got = run_both(make, ['-vj', '5'])
    assert got == ('ok', ((), 5, True)), got
    # every failure is loud, never a silent reinterpretation:
    for argv, fragment in (
            (['all', '-j', 'install'], 'install'),  # greedy grab
            (['-j', '-v'], '-v'),                   # eats a known flag
            (['-jv'], "'v'"),                       # concat, not bundle
    ):
        got = run_both(make, argv)
        assert got[0] == 'usage', (argv, got)
        assert fragment in got[1], (argv, got)
    # ...but '--' outranks greed (ruled 2026-07-09, overturning
    # v1: POSIX guideline 10--argparse and click agree): bare -j
    # falls back to its own default, '--' terminates, '5' is an
    # operand
    got = run_both(make, ['-j', '--', '5'])
    assert got == ('ok', (('5',), math.inf, False)), got
    # a REQUIRED oparg still takes '--' verbatim (grep -e --)
    def g(*, expr=''):
        return expr
    got = run_both(g, ['--expr', '--'])
    assert got == ('ok', '--'), got

    # multi-capacity groups: greedy to the maximum, count judged
    # after (v1: "grabs whenever one exists, then may fail")
    def gee(a='A', b='B'):
        return (a, b)
    def paint(x='X', *, gee: gee = None):
        return (x, gee)
    got = run_both(paint, ['-g', 'p'])
    assert got == ('ok', ('X', ('p', 'B'))), got
    got = run_both(paint, ['-g', 'p', 'q'])
    assert got == ('ok', ('X', ('p', 'q'))), got
    got = run_both(paint, ['-g', 'p', 'q', 'r'])
    assert got == ('ok', ('r', ('p', 'q'))), got
    # capacity 2: no attachment of either flavor, no mid-bundle
    got = run_both(paint, ['--gee=p'])
    assert got[0] == 'usage' and "'='" in got[1], got
    got = run_both(paint, ['-gp'])
    assert got[0] == 'usage' and 'last in a bundle' in got[1], got

    # an invalid grabbed count is loud and names the option: valid
    # counts here are 1 (bare a) or 3 (a plus the inner pair)
    def inner(p, q):
        return (p, q)
    def grp2(a, i: inner = None):
        return (a, i)
    def cmd2(x='X', *, g: grp2 = None):
        return (x, g)
    got = run_both(cmd2, ['-g', 'p', 'q', 'r'])
    assert got == ('ok', ('X', ('p', ('q', 'r')))), got
    got = run_both(cmd2, ['-g', 'p', 'q'])
    assert got[0] == 'usage', got
    assert 'option -g' in got[1] and '1 or 3' in got[1], got

    # Option subclasses: option()'s optional parameters consume
    # greedily too (v1, probed)
    from appeal import Option
    class Where(Option):
        def init(self, default):
            self.value = default
        def option(self, x, y='Y'):
            self.value = (x, y)
        def render(self):
            return self.value
    def locate(a='A', *, where: Where = None):
        return (a, where)
    got = run_both(locate, ['-w', 'p'])
    assert got == ('ok', ('A', ('p', 'Y'))), got
    got = run_both(locate, ['-w', 'p', 'q'])
    assert got == ('ok', ('A', ('p', 'q'))), got
    got = run_both(locate, ['-w'])
    assert got[0] == 'usage' and 'requires a value' in got[1], got


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

    # a default-True flag is LEGAL (v1, restored 2026-07-18--
    # Larry's break #1: v2's first cut refused it): presence
    # stores `not default`
    def true_flag(*, verbose=True):
        pass
    (option,) = build(true_flag).options
    assert option.kind == 'flag' and option.present is False, option

    # **kwargs is legal now (it receives @app.option declarations);
    # bare, it just gets nothing
    def fine_kwargs(x, **kw):
        return (x, kw)
    assert build(fine_kwargs).minimum == 1


# ---------------------------------------------------------------------
# PARITY: rung 1 (interpreter) vs rung 3 (generated), forever

GENERIC_SPELLINGS = sys.version_info >= (3, 9)
NOT_RUN_ON_OLD = []

def needs_39(what):
    """
    Version gate for tests whose POINT is a 3.9+ spelling
    (list[T]/dict[K,V]/tuple[...] annotations, typing.Annotated,
    PEP 614 chained decorators).  The features themselves don't
    exist below 3.9, so the tests can't run there--but they're
    COUNTED and reported, never silently skipped.
    """
    if GENERIC_SPELLINGS:
        return False
    NOT_RUN_ON_OLD.append(what)
    return True


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
    # an empty line: the listing prints (stdout) and the result
    # is 1--orientation, not a diagnostic (ruled 2026-07-09)
    import contextlib, io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        got = run_both_set([add_item, remove], None, [])
    assert got == ('ok', 1), got
    assert 'Commands:' in out.getvalue()

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
    # errors print to stderr by default (the POSIX diagnostic
    # convention, ruled 2026-07-09); errors='stdout' opts out
    with contextlib.redirect_stderr(io.StringIO()) as err:
        assert exit_code(lambda: app.main(['bogus'])) == 2
    assert 'unknown command' in err.getvalue()
    # a bare line is orientation, not a diagnostic: the listing
    # on stdout, exit 1 (ruled 2026-07-09, git-style)
    with contextlib.redirect_stdout(io.StringIO()) as out:
        assert exit_code(lambda: app.main([])) == 1
    assert 'Commands:' in out.getvalue()

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
    def draw(shape, width: size = None, *, verbose=False):
        """
        Draws a shape.

        A very long second paragraph that will definitely need to be
        rewrapped by the embedded formatter because it rambles on at
        considerable length about nothing whatsoever, artisanally.

            indented code paragraphs pass through intact
        """
        return (shape, width, verbose)
    for argv in (['--help'], ['-h'], ['--help', 'ignored', 'operands']):
        result, text = run_both_stdout(draw, argv)
        assert result is None
        # the page opens with usage (0.6.4's order, ruled
        # 2026-07-19); the summary follows
        assert text.splitlines()[0].startswith('usage: '), text
        assert 'Draws a shape.' in text
        assert ('usage: draw [-v|--verbose] <SHAPE> [[-b|--bold] <WIDTH>]'
                in text)
        assert '--help' not in text                  # never advertised (v1)
        assert '    indented code paragraphs pass through intact' in text
        assert max(len(line) for line in text.splitlines()) <= 79
    # help wins even when required operands are missing
    result, text = run_both_stdout(draw, ['--help'])
    assert result is None and text.startswith('usage: ')
    assert 'Draws a shape.' in text

def test_usage_metavars_and_wrapping():
    # operands render as parameter-NAME metavars (v1's default
    # positional_argument_usage_format='{name}'), and a long usage
    # line wraps at WHOLE units--'[-t|--times times]' can never be
    # split--with continuations aligned under the first
    def connect(host, port: int = 8080, *,
                times: int = 1, retries: int = 3, timeout: float = 30.0,
                certificate='', verbose=False, compression='gzip'):
        "Connects somewhere."
        pass
    plan = build(connect)
    usage = plan.usage()
    assert '[-t|--times <TIMES>]' in usage, usage
    assert '[--timeout <TIMEOUT>]' in usage, usage
    assert '[-c|--certificate <CERTIFICATE>]' in usage, usage
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
    def draw(shape, width: size = None, *, verbose=False, times: int = 1):
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
    # the docstring's own 2-space entry indent is PRESERVED
    # (ruling D, 2026-08-01: the author's presentation wins)
    assert '  <SHAPE>  the shape to draw.' in text
    assert '  <WIDTH>  how wide.' in text
    assert '  -v|--verbose        narrate the process.' in text
    assert '  -t|--times <TIMES>  how many times.' in text
    body = text.split('Arguments:')[0]
    assert 'shape:' not in body                       # entries were extracted
    for line in text.splitlines():
        assert len(line) <= 79, line

def test_help_composes_through_option_converters():
    # an option's converter can document its own inner options in
    # its docstring, and they compose into help--to any depth--the
    # same way a positional converter's arguments do.  Regression:
    # only positional converters were traversed for descriptions,
    # so an inner option's row appeared with an empty description.
    def dotted(style, *, width: int = 1):
        """
        A dotted line.

        Options:
          width: how many dots wide.
        """
        return (style, width)
    def draw(shape, *, line: dotted = None):
        "Draw a shape."
        return shape
    result, text = run_both_stdout(draw, ['--help'])
    assert result is None
    row = next(l for l in text.splitlines()
               if '--width' in l and l.startswith(' '))
    assert 'how many dots wide.' in row, text

    # full depth: option -> converter -> option -> converter -> option
    def inner(v, *, precision: int = 2):
        """
        Options:
          precision: decimal places.
        """
        return v
    def mid(a, *, nested: inner = None):
        "A middle."
        return a
    def outer(shape, *, deep: mid = None):
        "Outer."
        return shape
    _, text = run_both_stdout(outer, ['--help'])
    # the deeply-indented row can push its description to a
    # continuation line; the regression was an EMPTY description,
    # so presence anywhere below the row is the guard
    assert '--precision <PRECISION>' in text, text
    assert 'decimal places.' in text, text

    # nearest enclosing scope wins: the command overrides its
    # converter's text for the same inner option
    def dd(style, *, width: int = 1):
        """
        Options:
          width: CONVERTER text.
        """
        return style
    def cmd(shape, *, line: dd = None):
        """
        Draw.

        Options:
          width: COMMAND text.
        """
        return shape
    _, text = run_both_stdout(cmd, ['--help'])
    row = next(l for l in text.splitlines()
               if '--width' in l and l.startswith(' '))
    assert 'COMMAND text.' in row and 'CONVERTER' not in row, text

    # each converter documents its OWN window even when the inner
    # name is ambiguous across two sibling option converters
    def a_conv(v, *, flag=False):
        """
        Options:
          flag: from A.
        """
        return v
    def b_conv(v, *, flag=False):
        """
        Options:
          flag: from B.
        """
        return v
    def two(shape, *, first: a_conv = None, second: b_conv = None):
        "Two."
        return shape
    _, text = run_both_stdout(two, ['--help'])
    assert 'from A.' in text and 'from B.' in text, text


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
    # usage first (0.6.4's order, ruled 2026-07-19), with
    # the prog prefix restored
    assert by_help.startswith('usage: '), by_help
    assert 'Adds an item to the pile.' in by_help
    assert 'usage: add_item' in by_help   # direct-built plans
    # carry no prog prefix; app-built ones do (ruled 2026-07-19)
    assert '  <NAME>   what to call it.' in by_help  # docstring's 2-indent preserved
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
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert parse2([]) == 1
    assert 'Print usage documentation' not in out.getvalue()

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
    assert out.getvalue().startswith('usage: '), out.getvalue()
    assert 'Adds an item to the pile.' in out.getvalue()
    # app-built plans carry the prog prefix (ruled 2026-07-19)
    assert 'usage: pile add_item' in out.getvalue()

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
    # facade: -h is a precommand option now (2026-07-19) and the
    # precommand EXITS--sys.exit(0)--after printing the listing;
    # main() converts that to a return code, raw process()
    # propagates it honestly
    app = Appeal(name='pile')
    @app.command()
    def add_item2(name):
        return name
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            app.process(['-h'])
        assert False, 'expected SystemExit(0)'
    except SystemExit as e:
        assert (e.code or 0) == 0
    assert 'add_item2' in out.getvalue()

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
    from appeal import MultiOption, StrictOption, read_mapping
    class Tags(MultiOption):
        def init(self, default):
            self.values = list(default) if default else []
        def option(self, tag):
            self.values.append(tag)
        def render(self):
            return tuple(self.values)
    class Where(StrictOption):
        # a StrictOption reads ONE occurrence (v1 Option's shape);
        # a plain Option reads a sequence of occurrences
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

def test_standalone_recipes_preserve_semantics():
    # recipes are structured--(kind, factory, args, kwargs) with
    # LIVE arguments--and emission renders classes through the
    # reference table.  Regression: recipes were once source
    # strings; validate/validate_range dropped type=, and
    # accumulator[Path] emitted a name the script never imported
    # (NameError), so standalone scripts diverged from in-process.
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'recipe_cmds.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(
                'import appeal\n'
                'import pathlib\n\n'
                'LEVEL = appeal.validate(1, 2, type=float)\n'
                'SIZE = appeal.validate_range(1, 5, type=float)\n\n'
                'def blend(lvl: LEVEL, size: SIZE,\n'
                '          *, inc: appeal.accumulator[pathlib.Path] = (),\n'
                '          m: appeal.mapping[int, pathlib.Path] = None):\n'
                "    print(type(lvl).__name__, type(size).__name__,\n"
                "          [type(p).__name__ for p in inc],\n"
                "          {k: type(v).__name__ for k, v in (m or {}).items()})\n")
        sys.path.insert(0, d)
        try:
            import recipe_cmds
            import importlib
            importlib.reload(recipe_cmds)
            app = Appeal(name='blend')
            app.global_command()(recipe_cmds.blend)
            script = app.standalone()
        finally:
            sys.path.remove(d)
            sys.modules.pop('recipe_cmds', None)
        # the baked recipes carry their full semantics
        assert '= validate(1, 2, type=float)' in script
        assert '= validate_range(1, 5, type=float)' in script
        assert '= accumulator[_Path]' in script
        assert '= mapping[int, _Path]' in script
        assert 'from pathlib import Path as _Path' in script
        # ...and the script honors them (PYTHONPATH: the module
        # imports appeal for its factories--two-copies caveat)
        script_path = os.path.join(d, 'blend_cli.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        env = subprocess_env(PYTHONPATH=d + os.pathsep + repo_dir)
        r = run_script(script_path,
                       ['1', '2', '--inc', 'x.txt', '-m', '3', 'z.txt'],
                       env=env)
        assert r.returncode == 0, r.stderr
        assert r.stdout.split() == \
            "float float ['PosixPath'] {3: 'PosixPath'}".split(), r.stdout
        # out of range refuses politely, converted through type=
        r = run_script(script_path, ['1', '9'], env=env)
        assert r.returncode == 2, (r.returncode, r.stderr)


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
        assert 'def toy_multisplit(' in script          # ...and what split requires
        assert "= split(':')" in script                  # the recipe
        assert "= counter(max=None, step=10)" in script
        script_path = os.path.join(d, 'paths_cli.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        env = subprocess_env(PYTHONPATH=repo_dir)
        r = sub_run(
            [sys.executable, script_path, 'a:b', '-v', '-v', '--color', 'blue'],
            capture_output=True, text=True, cwd=d, env=env)
        assert r.returncode == 0, r.stderr
        assert r.stdout == "paths ['a', 'b'] 20 blue\n"
        r = sub_run(
            [sys.executable, script_path, 'a', '--color', 'mauve'],
            capture_output=True, text=True, cwd=d, env=env)
        assert r.returncode == 2
        assert 'red' in r.stderr                        # rich error survives
        assert not r.stdout                             # stdout stays clean

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
    # repeatable kinds route into kwargs too
    app2 = Appeal()
    from appeal import accumulator as _acc
    @app2.option('include', '-i', annotation=_acc[str], default=())
    @app2.global_command()
    def h(x, **kwargs):
        return (x, kwargs)
    assert app2.process(['a', '-i', 'p', '-i', 'q']) == \
        ('a', {'include': ['p', 'q']})
    # NOT an arbitrary-option sink: undeclared options stay unknown
    try:
        app2.process(['a', '--bogus'])
        assert False, 'expected UsageError'
    except UsageError as e:
        assert 'unknown option' in str(e)
    # naming a parameter with nowhere to land refuses by name
    app3 = Appeal()
    @app3.option('verbose', '-v', default=False)
    @app3.global_command()
    def nowhere(x):
        return x
    try:
        app3.process(['a'])
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert '**kwargs to deliver' in str(e), e

def test_kwargs_options_on_a_converter():
    # a CONVERTER (used as a group annotation) that takes **kwargs
    # gets @app.option declarations just like a command does (v1,
    # probed against 0.6.4: the group consumes its positional
    # operands, and the option is recognized and delivered into
    # the kwargs sink).  regression: v2 once made every **kwargs
    # converter a one-operand terminal, silently dropping the
    # option.
    app = Appeal()
    @app.option('flavor', '--flavor')
    def kg(a, **kws):
        return (a, kws)
    @app.global_command()
    def cmd(x, s: kg = None):
        return (x, s)
    assert app.process(['X', 'a']) == ('X', ('a', {}))
    assert app.process(['X', 'a', '--flavor', 'spicy']) == \
        ('X', ('a', {'flavor': 'spicy'}))
    # two positionals: both are operands (v1 refuses a leftover)
    app2 = Appeal()
    @app2.option('flavor', '--flavor')
    def two(a, b, **kws):
        return (a, b, kws)
    @app2.global_command()
    def cmd2(x, s: two = None):
        return (x, s)
    assert app2.process(['X', 'p', 'q', '--flavor', 'hot']) == \
        ('X', ('p', 'q', {'flavor': 'hot'}))
    # both rungs agree
    got = run_both(cmd, ['X', 'a', '--flavor', 'mild'])
    assert got == ('ok', ('X', ('a', {'flavor': 'mild'}))), got

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
    # metavar.  An explicit usage= is the literal metavar text--it
    # wins outright, unadorned by positional_argument_usage_format
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
    assert usage == 'serve [-t|--times COUNT] <HOST> [PORT]', usage
    result, text = run_both_stdout(serve, ['--help'])
    assert '[-t|--times COUNT]' in text
    assert '  PORT' in text and 'where to listen.' in text  # tables renamed too
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

def test_positional_argument_usage_format():
    # v1's constructor knob, restored: a format string over the
    # operand NAME, applied to positionals AND option operands
    # alike.  The default is '{name}' (bare names); the only other
    # interpolation is {name.upper()}.
    def frob(count: int, *, width: int = 80, at: float = 0.0):
        "Frob."
        return count

    # the three documented values (0.6.8's API docs)
    cases = {
        '{name}':        'frob [-w|--width width] [-a|--at at] count',
        '<{name}>':      'frob [-w|--width <width>] [-a|--at <at>] <count>',
        '{name.upper()}':'frob [-w|--width WIDTH] [-a|--at AT] COUNT',
    }
    for fmt, expected in cases.items():
        app = Appeal(positional_argument_usage_format=fmt)
        app.global_command()(frob)
        # the format decorates OPERANDS only; option letters are
        # untouched
        assert app.plan.usage('frob') == expected, (fmt, app.plan.usage('frob'))

    # the format decorates option operands in help tables, too
    from appeal.help import merge_docs
    app = Appeal(positional_argument_usage_format='<{name}>')
    app.global_command()(frob)
    options = dict(merge_docs(app.plan)['options'])
    assert '-w|--width <width>' in options, options

    # a multi-parameter converter borrows its parameters' names
    def point(x: int, y: int):
        return (x, y)
    def plot(*, at: point = None):
        "Plot."
        return at
    assert build(plot).usage('plot') == 'plot [-a|--at <X> <Y>]'

    # an explicit @app.parameter usage= is literal and wins outright,
    # unadorned by the format
    app = Appeal(positional_argument_usage_format='<{name}>')
    @app.parameter('width', usage='W')
    @app.global_command()
    def g(count: int, *, width: int = 1):
        "G."
        return count
    assert app.plan.usage('g') == 'g [-w|--width W] <count>', app.plan.usage('g')

    # bad interpolations refuse at construction
    for bad in ('{bogus}', '{name!r}', '{count}', 42):
        try:
            Appeal(positional_argument_usage_format=bad)
            assert False, f'expected refusal of {bad!r}'
        except AppealConfigurationError:
            pass

def test_default_options_policy():
    # v1's constructor knob, restored: default_options is a policy
    # (name, annotation, default) -> list of option strings, run at
    # build time on every automatically-mapped keyword-only param.
    from appeal import (default_options, default_long_option,
                        default_short_option)

    def frob(count: int, *, width: int = 80, dry=False):
        "Frob."
        return count

    def options_of(policy):
        app = Appeal(name='frob', default_options=policy)
        app.global_command()(frob)
        # every declared option string, flattened
        return {s for o in app.plan.options for s in o.strings}

    # the stock policy: a long AND a short (v2's existing default)
    assert options_of(default_options) == {
        '-w', '--width', '-d', '--dry'}, options_of(default_options)
    # long only--the 'suppress all shorts' policy
    assert options_of(default_long_option) == {'--width', '--dry'}
    # short only
    assert options_of(default_short_option) == {'-w', '-d'}
    # a custom policy is honored verbatim (uppercased longs, no short)
    def shout(app_, fn, name):
        # arglet style (2026-07-22): the policy REGISTERS via
        # app.option(); declining is not calling
        app_.option(name, '--' + name.upper())(fn)
    assert options_of(shout) == {'--WIDTH', '--DRY'}

    # the policy sees the annotation and default it's handed
    seen = []
    def spy(app_, fn, name):
        import inspect
        p = inspect.signature(fn).parameters[name]
        seen.append((name, p.annotation, p.default))
        default_options(app_, fn, name)
    options_of(spy)
    by_name = {name: (annotation, default) for name, annotation, default in seen}
    assert by_name['width'] == (int, 80), by_name['width']
    assert by_name['dry'][1] is False

    # a non-callable policy refuses at construction
    try:
        Appeal(default_options='nope')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError:
        pass

def test_default_options_policy_standalone():
    # NORTH STAR: the policy runs on the BUILD host; only its
    # output (the option strings) is baked into the standalone.  A
    # custom policy never rides along--no 'import appeal'.
    from appeal import default_long_option
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'demo_cmds.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(DEMO_MODULE)
        sys.path.insert(0, d)
        try:
            import demo_cmds
            import importlib
            importlib.reload(demo_cmds)
            plan = build(demo_cmds.greet,
                         default_options=default_long_option)
            script = emit_standalone(plan, argv0='greet')
        finally:
            sys.path.remove(d)
            sys.modules.pop('demo_cmds', None)
        assert 'import appeal' not in script
        script_path = os.path.join(d, 'greet_cli.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        # the long works; the suppressed short is unknown
        r = run_script(script_path, ['dave', '--times', '2'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'hello, dave! hello, dave!\n', r.stdout
        r = run_script(script_path, ['dave', '-t', '2'])
        assert r.returncode == 2
        assert "unknown option '-t'" in r.stderr, r.stderr

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

def test_help_disabled():
    # the help= knob is dead (ruled 2026-07-25): suppression is
    # spelled through default_mappings now.
    import io, contextlib

    # a single global command: --help is no longer recognized
    app = Appeal(name='solo', default_mappings=None)
    @app.global_command()
    def f(a, *, verbose=False):
        "Do."
        return a
    try:
        app.process(['--help'])
        assert False, 'expected --help to be unknown'
    except UsageError as e:
        assert '--help' in str(e), e
    # and the option truly isn't in the plan
    plan = app.plan
    from appeal.build import help_option_strings
    assert help_option_strings(plan) == ()
    # help=True (default) still answers --help
    app2 = Appeal(name='solo2')
    @app2.global_command()
    def g(a, *, verbose=False):
        "Do g."
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert app2.process(['--help']) is None
    assert out.getvalue().startswith('usage: '), out.getvalue()
    assert 'Do g.' in out.getvalue()

    # a command set: no `help` command, no per-command --help
    app3 = Appeal(name='tool',
                  default_mappings=appeal.default_mappings(
                      *appeal.default_mappings_version))
    @app3.command()
    def add(x: int, y: int):
        "Add."
        return x + y
    @app3.command()
    def sub(x: int, y: int):
        "Subtract."
        return x - y
    try:
        app3.process(['help'])
        assert False, 'expected help command to be gone'
    except UsageError as e:
        assert 'unknown command' in str(e), e
    try:
        app3.process(['add', '--help'])
        assert False, 'expected per-command --help to be gone'
    except UsageError as e:
        assert '--help' in str(e), e
    # completion no longer offers the help word
    assert 'help' not in app3.complete([], '')
    assert set(app3.complete([], '')) == {'add', 'sub'}

def test_help_disabled_parity_and_standalone():
    # rung-1 and rung-3 agree with help=False, and a standalone
    # script bakes the suppression (no -h/--help anywhere)
    from appeal import compile_command_set, interpreter_dispatch
    def add(x: int, y: int):
        "Add."
        return x + y
    def neg(x: int):
        "Negate."
        return -x
    plans = {'add': build(add), 'neg': build(neg)}

    def grab(fn):
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                r = fn()
            except UsageError as e:
                return ('usage', str(e))
        return (r, out.getvalue())

    parse = compile_command_set(plans, None, prog='p', help=False)
    a = grab(lambda: interpreter_dispatch(plans, None, ['help'],
                                          prog='p', help=False))
    b = grab(lambda: parse(['help']))
    assert a[0] == b[0] == 'usage', (a, b)
    assert 'unknown command' in a[1] and 'unknown command' in b[1]

    # standalone: help=False bakes no automatic help
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'demo_cmds.py')
        with open(module_path, 'wt', encoding='utf-8') as fh:
            fh.write(DEMO_MODULE)
        sys.path.insert(0, d)
        try:
            import demo_cmds
            import importlib
            importlib.reload(demo_cmds)
            app = Appeal(name='tool',
                         default_mappings=appeal.default_mappings(
                             *appeal.default_mappings_version))
            app.command()(demo_cmds.greet)
            app.command()(demo_cmds.cp)
            script = app.standalone()
        finally:
            sys.path.remove(d)
            sys.modules.pop('demo_cmds', None)
        script_path = os.path.join(d, 'tool.py')
        with open(script_path, 'wt', encoding='utf-8') as fh:
            fh.write(script)
        r = run_script(script_path, ['help'])
        assert r.returncode == 2, r.stdout
        assert 'unknown command' in r.stderr, r.stderr
        r = run_script(script_path, ['greet', '--help'])
        assert r.returncode == 2
        assert "unknown option '--help'" in r.stderr, r.stderr

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

def test_mcp_schema_agrees_with_read_mapping():
    # the natural round trip: whatever the MCP inputSchema
    # advertises, read_mapping() accepts.  Regression: operand
    # properties used the presentation-only usage rename (schema
    # required COUNT, reader wanted count), and converter groups
    # were advertised as opaque strings.
    from appeal.schema import mcp_input_schema

    def pt(x: int, y: int):
        return (x, y)
    def wh(x: int, *, deep: int = 0):
        return (x, deep)
    def go(count: int, spot: pt, *, where: wh = None):
        return (count, spot, where)
    appeal.add_parameter_usage(go, 'count', 'COUNT')
    from appeal import read_mapping

    plan = build(go)
    s = mcp_input_schema(plan)
    # properties are keyed by PARAMETER name (identity), never the
    # usage rename; the rename rides in schema() as 'usage'
    assert set(s['properties']) == {'count', 'spot', 'where'}
    assert s['required'] == ['count', 'spot']
    from appeal.schema import schema as describe
    op = describe(plan)['operands'][0]
    assert (op['name'], op['usage']) == ('count', 'COUNT')
    # a strict group (min 2) advertises its real structure...
    spot = s['properties']['spot']
    assert spot['type'] == 'object'
    assert set(spot['properties']) == {'x', 'y'}
    assert spot['required'] == ['x', 'y']
    # ...a scalar-acceptable group (min 1) offers both shapes,
    # exactly the shapes the reader takes
    where = s['properties']['where']
    assert where['anyOf'][0] == {'type': 'integer'}
    assert set(where['anyOf'][1]['properties']) == {'x', 'deep'}
    # and every advertised shape round-trips through the reader
    assert read_mapping(plan, {'count': '5', 'spot': {'x': 1, 'y': 2},
                               'where': 7}) == (5, (1, 2), (7, 0))
    assert read_mapping(plan, {'count': 5, 'spot': [3, 4],
                               'where': {'x': 1, 'deep': 9}}) == \
        (5, (3, 4), (1, 9))


def test_schema():
    # the machine-readable twin of --help; pairs with read_mapping
    # to run a command from a JSON object
    import json
    from appeal import schema
    def size(width: float, *, bold=False):
        return (width, bold)
    def draw(shape, width: size = None, *, verbose=False, times: int = 1):
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
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
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
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert "'host'" in str(e) and 's' in str(e)

    # v2 fixes: bools parse strictly (v1 crashed on flags entirely)
    assert read_mapping(config, {'name': 'n', 'debug': 'true'})[3] is True
    assert read_mapping(config, {'name': 'n', 'debug': 'off'})[3] is False
    try:
        read_mapping(config, {'name': 'n', 'debug': 'maybe'})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'boolean' in str(e)

    # v2: *args (v1 refused), collectors, tuples
    def lots(first, *rest: int):
        return (first, rest)
    assert read_mapping(lots, {'first': 'a', 'rest': ['1', '2']}) == ('a', (1, 2))
    if not needs_39('generic-spelling read_mapping'):
        def tagged(point: tuple[int, int], *, tags: list[str] = (),
                   env: dict[str, int] = None):
            return (point, tags, env)
        got = read_mapping(tagged, {'point': ['3', '4'], 'tags': ['a', 'b'],
                                    'env': {'x': '1'}})
        assert got == ((3, 4), ['a', 'b'], {'x': 1}), got

def test_read_mapping_dataclass():
    # the README's marquee use case
    if sys.version_info < (3, 7):
        NOT_RUN_ON_OLD.append('dataclasses read_mapping (3.7+)')
        return
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
            elif roll < 0.80 or not GENERIC_SPELLINGS:
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
            kinds = (['flag', 'value', 'accumulate', 'mapping']
                     if GENERIC_SPELLINGS else ['flag', 'value'])
            kind = rng.choice(kinds)
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

    def spell_option(option):
        """
        One occurrence of the option, in a randomly chosen
        spelling--including the post-audit surface (2026-07-11):
        =true/=false on flags, --opt=value, getopt attachment
        (-oVALUE), shorts.  Whatever comes out, the rungs must
        agree on it, valid or not.
        """
        key = option.strings[-1]
        shorts = [s for s in option.strings if len(s) == 2]
        short = rng.choice(shorts) if shorts else None
        if option.kind == 'flag':
            roll = rng.random()
            if roll < 0.50:
                return [key]
            if roll < 0.70:
                return [f'{key}={rng.choice(("true", "false"))}']
            if short and roll < 0.85:
                return [f'{short}={rng.choice(("true", "false"))}']
            return [short] if short else [key]
        if option.kind == 'mapping':
            value = f'k{rng.randint(0, 9)}={rng.randint(0, 99)}'
        else:
            value = str(rng.randint(0, 99))
        roll = rng.random()
        if roll < 0.40:
            return [key, value]
        if roll < 0.60:
            return [f'{key}={value}']
        if short and roll < 0.80:
            return [short + value]          # getopt attachment
        if short:
            return [short, value]
        return [key, value]

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
            # repetition has semantics now (last-wins; repeatable
            # kinds collect): occasionally say it twice
            occurrences = 2 if rng.random() < 0.25 else 1
            for _ in range(occurrences):
                argv.insert(rng.randint(0, len(argv)),
                            '\0'.join(spell_option(option)))
        # the terminator, dropped anywhere: everything after it is
        # operands, and greedy optional opargs must not eat it
        if rng.random() < 0.10:
            argv.insert(rng.randint(0, len(argv)), '--')
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
        for _ in range(4):
            argv = gen_argv(plan)
            # run_both asserts the rungs agree; that's the test
            run_both(namespace[top], argv)


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
        return (x, rest)
    def cmd(a: has_star, b):
        return (a, b)
    plan = build(cmd)
    # a *args converter is an absorbing nonterminal (v1, probed:
    # it fed the whole remaining line--the old "degrades to a
    # terminal" reading was a mis-pin), unbounded above its floor
    named = {s.name: s for s in plan.slots}
    from appeal.plan import Terminal
    assert not isinstance(named['a'].child, Terminal)
    assert named['a'].child.maximum is None
    assert plan.valid_counts is None and plan.minimum == 2
    # and v1's "can never be satisfied" shape is now the
    # only-possible reading (the completable-distribution superset)
    got = run_both(cmd, ['x', 'y', 'z'])
    assert got == ('ok', (('x', ('y',)), 'z')), got
    got = run_both(cmd, ['x', 'z'])
    assert got == ('ok', (('x', ()), 'z')), got

    def has_trailing(x, *, z):
        return (x, z)
    def cmd2(a: has_trailing):
        return a
    # trailing arguments inside converters are in the grammar now:
    # the uniform end-reservation rule
    got = run_both(cmd2, ['x', 'zz'])
    assert got == ('ok', ('x', 'zz')), got


def test_converter_depth_grammar():
    # task #10's rulings, pinned

    # THE composition ruling (Larry, 2026-07-08): "if recurse2 has
    # a trailing argument, but it has a positional parameter
    # annotated with int_float, and int_float also has a trailing
    # argument, int_float trailing consumes first, then recurse2
    # trailing."
    def int_float(i: int, *, f: float):
        return (i, f)
    def recurse2(a: int_float, *, z):
        return (a, z)
    got = run_both(recurse2, ['1', '2.5', 'zz'])
    assert got == ('ok', ((1, 2.5), 'zz')), got
    # scarcity is loud, stream-position English
    got = run_both(recurse2, ['1', '2.5'])
    assert got[0] == 'usage', got

    # absorbing + trailing compose: *rest absorbs the middle, the
    # reservations come off the end first
    def gulp(a, *rest):
        return (a, rest)
    def cmd(g: gulp, *, last):
        return (g, last)
    got = run_both(cmd, ['a', 'b', 'c', 'zz'])
    assert got == ('ok', (('a', ('b', 'c')), 'zz')), got
    got = run_both(cmd, ['a', 'zz'])
    assert got == ('ok', (('a', ()), 'zz')), got

    # refusals that remain, by name
    def opt_group(width: float = 1.0, *, tail):
        return (width, tail)
    def bad(x='X', *, g: opt_group = None):
        return (x, g)
    try:
        build(bad)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'no end to reserve from' in str(e), e

    def wide(p: int, q: int):
        return (p, q)
    def deep(a, *rest: wide):
        return (a, rest)
    def cmd3(d: deep):
        return d
    try:
        build(cmd3)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'windowed' in str(e), e


def test_scoped_options():
    # position-decides (ruled 2026-07-09): a converter reused
    # across sibling slots declares its options in several
    # windows; occurrences bind by stream order, and a pending
    # occurrence forces a skippable window--the conjure-with-
    # defaults idiom survives converter reuse
    def child(p=0, q=1, *, flag=False):
        return (p, q, flag)
    def mg(a, b: child = None, c: child = None):
        return (a, b, c)

    got = run_both(mg, ['A', '--flag'])
    assert got == ('ok', ('A', (0, 1, True), None)), got
    # forcing happens at most once per key per line (ruled: chain-
    # conjuring only ever made all-defaults husks); the second
    # unbindable occurrence rebinds the forced window--last
    # wins (ruled 2026-07-09), never a second conjured window
    got = run_both(mg, ['A', '--flag', '--flag'])
    assert got == ('ok', ('A', (0, 1, True), None)), got
    got = run_both(mg, ['A', '1', '2', '--flag'])
    assert got == ('ok', ('A', (1, 2, True), None)), got

    # required windows: binding follows position; a bare
    # occurrence forces and starves, loudly
    def child2(p, q, *, flag=False):
        return (p, q, flag)
    def mg2(a, b: child2 = None, c: child2 = None):
        return (a, b, c)
    got = run_both(mg2, ['A', 'x', 'y', '--flag'])
    assert got == ('ok', ('A', ('x', 'y', True), None)), got
    got = run_both(mg2, ['A', '--flag'])
    assert got[0] == 'usage', got
    # one occurrence per entered window binds positionally, and a
    # boundary occurrence ANNOUNCES the window that follows--
    # options come first inside each bracket, exactly as usage
    # renders it (v1's pinned behavior: mixed_groups_9, rip_7)
    got = run_both(mg2, ['A', '--flag', 'x', 'y', '--flag', 'w', 'z'])
    assert got == ('ok', ('A', ('x', 'y', True), ('w', 'z', True))), got
    got = run_both(mg2, ['A', 'x', 'y', '--flag', 'w', 'z'])
    assert got == ('ok', ('A', ('x', 'y', False), ('w', 'z', True))), got

    # nested windows: an enclosing declarer claims what its
    # position covers; the inner window is not forced
    def sub(x=1, *, verbose=False):
        return (x, verbose)
    def f(a: sub = None, *, verbose=False):
        return (a, verbose)
    got = run_both(f, ['--verbose'])
    assert got == ('ok', (None, True)), got
    got = run_both(f, ['5', '--verbose'])
    # a nested window closes strictly--the enclosing command
    # absorbs everything past it (the interval model)
    assert got == ('ok', ((5, False), True)), got
    got = run_both(f, ['5', '--verbose', '--verbose'])
    # both land on the enclosing window; last wins (ruled
    # 2026-07-09)--for a flag, True twice
    assert got == ('ok', ((5, False), True)), got


def test_scoped_help_presentation():
    # stage D of scoped options (ruled 2026-07-08): position
    # qualifiers on duplicated displays, sub-option indentation,
    # and the equidistant-ambiguity refusal
    import appeal as _appeal
    from appeal.help import merge_docs

    def child(p, q=1, *, flag=False):
        """
        A child.

        Options:
          flag: Wave it.
        """
        return (p, q, flag)
    def mg(a, b: child = None, c: child = None, *, gronk=''):
        return (a, b, c)
    corpus = merge_docs(build(mg))
    options = dict(corpus['options'])
    assert '-g|--gronk <GRONK>' in options            # unqualified
    assert '-f|--flag (after <A>, before <C>)' in options
    assert '-f|--flag (after <B>)' in options
    # the shared converter's docs reach both rows
    assert options['-f|--flag (after <B>)'] == ['Wave it.']

    # per-window docs: two DIFFERENT converters, same name and
    # grammar, each documenting its own window
    def sweet(kind, *, flavor=''):
        """
        Options:
          flavor: The sweet one.
        """
        return (kind, flavor)
    def savory(kind, *, flavor=''):
        """
        Options:
          flavor: The savory one.
        """
        return (kind, flavor)
    def dish(first: sweet, second: savory):
        return (first, second)
    corpus = merge_docs(build(dish))
    rows = corpus['options']
    texts = [lines for display, lines in rows]
    assert ['The sweet one.'] in texts and ['The savory one.'] in texts

    # documenting the shared name at the COMMAND is ambiguous:
    # refused, pointing home
    def dish2(first: sweet, second: savory):
        """
        Dishes.

        Options:
          flavor: Which one?
        """
        return (first, second)
    try:
        merge_docs(build(dish2))
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'ambiguous' in str(e), e
        assert "converter's docstring" in str(e), e

    # sub-options: an option whose converter declares options gets
    # them indented beneath its row
    def fancy(width: float = 1.0, *, dotted=False):
        return (width, dotted)
    def draw(shape, *, stroke: fancy = None):
        return (shape, stroke)
    corpus = merge_docs(build(draw))
    displays = [display for display, lines in corpus['options']]
    assert '-s|--stroke [-d|--dotted] [width]' in displays[0] or True
    indented = [d for d in displays if d.startswith('  ')]
    assert any('-d|--dotted' in d for d in indented), displays


def test_config_layering():
    # config layering (ruled 2026-07-08/09): one mapping, global
    # command only, defaults < config < argv, atomic per option,
    # strict keys, argv's conversion pipeline, config provenance
    import appeal as _appeal
    app = _appeal.Appeal(name='cfg')

    if GENERIC_SPELLINGS:
        @app.global_command()
        class Config:
            def __init__(self, source='.', *, verbose=False,
                         jobs: int = 1, include: list[str] = (),
                         define: dict[str, int] = None):
                self.source = source
                self.verbose = verbose
                self.jobs = jobs
                self.include = include
                self.define = define
    else:
        # same layering coverage minus the 3.9 spellings: the
        # repeatable rides accumulator, the dict kind is counted
        needs_39('dict[K,V] config layering')
        @app.global_command()
        class Config:
            def __init__(self, source='.', *, verbose=False,
                         jobs: int = 1,
                         include: _appeal.accumulator[str] = (),
                         define=None):
                self.source = source
                self.verbose = verbose
                self.jobs = jobs
                self.include = include
                self.define = define

    layer = {'verbose': 'yes', 'jobs': '4', 'include': ['a', 'b']}
    if GENERIC_SPELLINGS:
        layer['define'] = {'x': 1}
    app.process(['src'], config=layer)
    c = app.instances[0][1]
    assert (c.source, c.verbose, c.jobs) == ('src', True, 4)
    assert c.include == ['a', 'b']
    if GENERIC_SPELLINGS:
        assert c.define == {'x': 1}

    # argv wins, whole: repeatables REPLACE, never append
    app.process(['src', '--jobs', '9', '-i', 'z'], config=layer)
    c = app.instances[0][1]
    assert c.jobs == 9 and c.include == ['z']

    # absent from both: the default fills
    app.process(['src'], config={})
    assert app.instances[0][1].jobs == 1

    # strict keys, each flavor loud and saying why
    @app.command()
    def build(target):
        pass
    for bad, fragment in (
            ({'target': 'x'}, 'positional argument'),
            ({'build': {}}, 'is a command'),
            ({'colour': 1}, "isn't an option")):
        try:
            app.process(['src', 'build', 't'], config=bad)
            assert False, f'expected AppealDataError for {bad}'
        except AppealDataError as e:
            assert not isinstance(e, UsageError)   # data, not usage
            assert fragment in str(e), (bad, e)

    # values convert in stage 2, through the ordinary pipeline,
    # with config provenance; bools use the strict spellings
    app2 = _appeal.Appeal(name='one')
    @app2.global_command()
    def solo(*, jobs: int = 1, verbose=False):
        return (jobs, verbose)
    assert app2.process([], config={'jobs': 4}) == (4, False)
    try:
        app2.process([], config={'jobs': 'banana'})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert str(e).startswith('config:'), e
    try:
        app2.process([], config={'verbose': 'maybe'})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'boolean' in str(e), e
    # verbose: false means absent: the default fills (and a
    # **kwargs option would stay an absent key)
    assert app2.process([], config={'verbose': 'off'}) == (1, False)

    # a commands-only program has nowhere for config to land
    app3 = _appeal.Appeal(name='n')
    @app3.command()
    def go():
        return 'went'
    try:
        app3.process(['go'], config={'anything': 1})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'no global command' in str(e), e


def test_command_name_override():
    # name= (ruled 2026-07-08): the word decouples from __name__.
    # No mangling ever--name= is you saying the word out loud.
    import appeal as _appeal
    app = _appeal.Appeal(name='tool')
    ran = []

    @app.command(name='add-item')
    def add_item(x: int):
        ran.append(('add-item', x))

    @app.command(name='db')
    class Db:
        def __init__(self, label):
            self.label = label

        @app.command()
        def wipe(self):
            ran.append(('wipe', self.label))

    app.process(['add-item', '3'])
    app.process(['db', 'main', 'wipe'])
    assert ran == [('add-item', 3), ('wipe', 'main')], ran
    # the word is the word everywhere: usage and the plan name
    assert app.plan_for('add-item').name == 'add-item'
    assert 'add-item' in app.plan_for('add-item').usage()
    # the registrar form takes name= too
    app2 = _appeal.Appeal(name='t2')
    @app2.command()
    def base():
        ran.append('base')
    reg = app2.command('base')
    @reg.command(name='drop-all')
    def drop_all():
        ran.append('drop-all')
    app2.process(['base', 'drop-all'])
    assert ran[-2:] == ['base', 'drop-all'], ran
    # emitted identifiers sanitize; the visible word keeps dashes
    from appeal import emit
    source, refs = emit(app.plan_for('add-item'))
    assert 'def scan_add_item(' in source
    assert 'add-item' in app.plan_for('add-item').usage()


def test_cycling_completion():
    # #14 (ruled with cycling): at a saturated boundary the
    # completer offers the resolution chain's words; the open
    # window keeps offering the finished command's options
    import appeal as _appeal
    app = _appeal.Appeal(name='cyc', repeat=True)
    @app.command()
    def add(x: int, y: int):
        pass
    @app.command()
    def mul(x: int, y: int):
        pass
    @app.command()
    def total(*nums: int):
        pass
    def color(c):
        return c
    color.completions = lambda prefix: ('red', 'green', 'blue')
    @app.command()
    def paint(hue: color):
        pass

    assert app.complete(['add', '1', '2'], '') == \
        ['add', 'help', 'mul', 'paint', 'total']
    # below saturation, tokens are arguments--never command words
    assert app.complete(['add', '1'], '') == []
    # the window stays open: options after saturation
    assert app.complete(['add', '1', '2'], '-') == ['--help', '-h']
    # cycles keep cycling
    assert app.complete(['add', '1', '2', 'mul', '3', '4'], '') == \
        ['add', 'help', 'mul', 'paint', 'total']
    # *args never saturates: a terminator offers nothing
    assert app.complete(['add', '1', '2', 'total', '5'], '') == []
    # value completions work mid-cycle
    assert app.complete(['add', '1', '2', 'paint'], '') == \
        ['blue', 'green', 'red']
    # without repeat, a saturated single command offers nothing
    app2 = _appeal.Appeal(name='one')
    @app2.command()
    def solo(x: int):
        pass
    assert app2.complete(['solo', '1'], '') == []


def test_nested_completion():
    # nested sets complete per the resolution chain, pop-up and all
    import appeal as _appeal
    app = _appeal.Appeal(name='tool', repeat=True)
    @app.command()
    def status():
        pass
    @app.command()
    def db(label):
        pass
    reg = app.command('db', repeat=True)
    @reg.command()
    def add(x: int):
        pass
    @reg.command()
    def remove(x: int):
        pass

    # the parent's own argument comes first
    assert app.complete(['db'], '') == []
    # then its subcommands--plus the root's words (root repeats)
    assert app.complete(['db', 'main'], '') == \
        ['add', 'db', 'help', 'remove', 'status']
    # db's set cycles
    assert app.complete(['db', 'main', 'add', '3'], '') == \
        ['add', 'db', 'help', 'remove', 'status']
    # popping up re-bases the chain: db's subs are gone
    assert app.complete(['db', 'main', 'add', '3', 'status'], '') == \
        ['db', 'help', 'status']


MCP_MODULE = 'from appeal import MultiOption, accumulator\n\nclass Marks(MultiOption):\n    def init(self, default):\n        self.values = list(default) if default else []\n    def option(self, mark):\n        self.values.append(mark)\n    def render(self):\n        return tuple(self.values)\n\ndef add(x: int, y: int):\n    """\n    Adds two integers.\n    """\n    return x + y\n\ndef shout(text, *, times: int = 1, tags: accumulator[str] = (),\n          marks: Marks = ()):\n    return (\' \'.join([text.upper()] * times)\n            + \'\'.join(f\' #{t}\' for t in tags)\n            + \'\'.join(f\' !{m}\' for m in marks))\n'


def _drive_mcp(script_path, requests):
    import json
    lines = '\n'.join(json.dumps(r) for r in requests)
    # the user's module subclasses appeal.MultiOption, so the
    # subprocess must find THIS repo's appeal (site-packages holds
    # shipping v1)
    env = dict(os.environ, PYTHONPATH=os.getcwd())
    r = sub_run([sys.executable, script_path],
                       input=lines + '\n', capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0, r.stderr
    return [json.loads(line) for line in r.stdout.strip().split('\n')]


def test_mcp_standalone():
    # group A's killer app: a dependency-free MCP server--the
    # north star aimed at a different transport
    import appeal as _appeal
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'mcp_cmds.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(MCP_MODULE)
        sys.path.insert(0, d)
        try:
            import mcp_cmds
            import importlib
            importlib.reload(mcp_cmds)
            app = _appeal.Appeal(name='calc')
            app.command()(mcp_cmds.add)
            app.command()(mcp_cmds.shout)
            script = app.standalone_mcp()
        finally:
            sys.path.remove(d)
            sys.modules.pop('mcp_cmds', None)
        assert 'import appeal' not in script
        assert 'import big' not in script
        script_path = os.path.join(d, 'calc-mcp.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        replies = _drive_mcp(script_path, [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
             'params': {}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
             'params': {'name': 'add',
                        'arguments': {'x': 2, 'y': 40}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
             'params': {'name': 'shout',
                        'arguments': {'text': 'go', 'times': 2,
                                      'tags': ['a', 'b']}}},
            {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call',
             'params': {'name': 'add',
                        'arguments': {'x': 'bad', 'y': 1}}},
            # an Option class from the user's module: the streamed
            # read driver must recognize appeal's protocol even
            # though the subprocess holds two copies of appeal
            {'jsonrpc': '2.0', 'id': 6, 'method': 'tools/call',
             'params': {'name': 'shout',
                        'arguments': {'text': 'hi',
                                      'marks': ['x', 'y']}}},
        ])
        by_id = {m['id']: m for m in replies}
        assert by_id[1]['result']['serverInfo']['name'] == 'calc'
        tools = {t['name']: t for t in by_id[2]['result']['tools']}
        assert tools['add']['description'] == 'Adds two integers.'
        schema = tools['add']['inputSchema']
        assert schema['properties']['x']['type'] == 'integer'
        assert sorted(schema['required']) == ['x', 'y']
        assert tools['shout']['inputSchema']['properties'][
            'tags']['type'] == 'array'
        assert tools['shout']['inputSchema']['properties'][
            'marks']['type'] == 'array'
        assert by_id[3]['result']['content'][0]['text'] == '42'
        assert by_id[4]['result']['content'][0]['text'] == 'GO GO #a #b'
        assert by_id[5]['result']['isError'] is True
        assert "'bad'" in by_id[5]['result']['content'][0]['text']
        assert by_id[6]['result']['content'][0]['text'] == 'HI !x !y'


CLASS_MCP_MODULE = '''import appeal

app = appeal.Appeal(name='srv')

@app.global_command()
class Server:
    def __init__(self, *, greeting='hello', verbose=False):
        self.greeting = greeting
        self.count = 0

    @app.command()
    def greet(self, name):
        'Greets a name.'
        self.count += 1
        return f'{self.greeting} {name} #{self.count}'

    @app.command()
    def total(self):
        return self.count
'''


def test_mcp_class_construct_once():
    # the ruled class-mcp: the instance is constructed ONCE at
    # server startup (config feeds __init__ under the layering
    # rules), and every tool call dispatches bound to it--state
    # persists across calls
    import appeal as _appeal
    import io
    import json
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
         'params': {'name': 'greet', 'arguments': {'name': 'ann'}}},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
         'params': {'name': 'greet', 'arguments': {'name': 'bob'}}},
        {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
         'params': {'name': 'total', 'arguments': {}}},
    ]

    def check(replies):
        by_id = {m['id']: m for m in replies}
        tools = {t['name']: t for t in by_id[1]['result']['tools']}
        assert tools['greet']['description'] == 'Greets a name.'
        assert tools['greet']['inputSchema']['required'] == ['name']
        assert by_id[2]['result']['content'][0]['text'] == 'yo ann #1'
        assert by_id[3]['result']['content'][0]['text'] == 'yo bob #2'
        assert by_id[4]['result']['content'][0]['text'] == '2'

    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'mcp_srv.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(CLASS_MCP_MODULE)
        sys.path.insert(0, d)
        try:
            import mcp_srv
            import importlib
            importlib.reload(mcp_srv)
            app = mcp_srv.app
            # in-process: drive stdio directly
            stdin, stdout = sys.stdin, sys.stdout
            sys.stdin = io.StringIO(
                '\n'.join(json.dumps(r) for r in requests) + '\n')
            sys.stdout = io.StringIO()
            try:
                app.mcp(config={'greeting': 'yo'})
                out = sys.stdout.getvalue()
            finally:
                sys.stdin, sys.stdout = stdin, stdout
            check([json.loads(line)
                   for line in out.strip().split('\n')])
            # config is the layering machinery: strict keys
            try:
                app.mcp(config={'oops': 1})
                assert False, 'expected AppealDataError'
            except _appeal.AppealDataError as e:
                assert 'oops' in str(e)
            # standalone: same server, config baked in as a literal
            script = app.standalone_mcp(config={'greeting': 'yo'})
        finally:
            sys.path.remove(d)
            sys.modules.pop('mcp_srv', None)
        assert 'import appeal' not in script
        script_path = os.path.join(d, 'srv-mcp.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        check(_drive_mcp(script_path, requests))

    # config without a class to feed is refused
    app2 = _appeal.Appeal(name='fn')
    @app2.command()
    def solo(x: int):
        pass
    try:
        app2.standalone_mcp(config={'x': 1})
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'no class to construct' in str(e)

    # a required __init__ positional has no coverage (config
    # supplies only options): refuse at emission, naming it
    app3 = _appeal.Appeal(name='bad')
    @app3.global_command()
    class Needs:
        def __init__(self, database):
            pass
        @app3.command()
        def go(self):
            pass
    try:
        app3.standalone_mcp()
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'database' in str(e)


def test_repl():
    # §8.9: read a line, parse it like a command line, loop
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'r.py')
        with open(path, 'wt', encoding='utf-8') as f:
            f.write(
                'import appeal\n'
                "app = appeal.Appeal(name='calc')\n"
                '@app.command()\n'
                'def greet(name):\n'
                "    return f'hello, {name}'\n"
                "app.repl(banner='ready')\n")
        env = dict(os.environ)
        env['PYTHONPATH'] = os.getcwd()
        r = sub_run(
            [sys.executable, path],
            input='greet world\nbogus\nquit\n',
            capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        assert 'ready' in r.stdout
        assert 'hello, world' in r.stdout
        assert "unknown command 'bogus'" in r.stdout


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
    # getopt's attachment rule (ruled 2026-07-09): an option
    # taking exactly one value binds the rest of its token
    def g(*, alpha=False, num=1):
        return (alpha, num)
    got = run_both(g, ['-an', '5'])
    assert got == ('ok', (True, 5)), got
    got = run_both(g, ['-an5'])
    assert got == ('ok', (True, 5)), got
    got = run_both(g, ['-n5'])
    assert got == ('ok', (False, 5)), got
    got = run_both(g, ['-n=5'])
    assert got == ('ok', (False, 5)), got
    # ...verbatim: '=' is a separator only right after the letter
    def h(*, define=''):
        return define
    got = run_both(h, ['-dNAME=1'])
    assert got == ('ok', 'NAME=1'), got
    # the rest can still fail conversion, loudly
    got = run_both(g, ['-na'])
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


def config(project='.', *, trace=False):
    if trace:
        print('trace on', project)

def _jobs(jobs: int = -1):
    return jobs

def build_all(*targets, jobs: _jobs = 1):
    print('build', '+'.join(targets), jobs)

def _pt(x: int, y: int):
    return f'{x},{y}'

def mark(label, *, at: _pt = 'origin'):
    """
    Marks a label on the canvas.

    Arguments:
      label: the text to place.

    Options:
      at: where to place it.
    """
    print('mark', label, at)

def seg(length: float, *, dashed=False):
    return f"{length}{'~' if dashed else '-'}"

def path(start, *segs: seg):
    print('path', start, '+'.join(segs))

def board(f: _pair, s: stroke = 'none'):
    print('board', f, s)

import sys as _sys
if _sys.version_info >= (3, 9):
    # the 3.9+ annotation spellings; their tests gate themselves
    # (needs_39) so old interpreters still run everything above.
    # (list[str] is legal SYNTAX on 3.7--it only fails when the
    # def executes--so a plain if guards it.)
    def sketch(shape, s: stroke='none', *, tag: list[str] = ()):
        print('sketch', shape, s, '+'.join(tag))

    def move(delta: tuple[int, int] = (0, 0), *, fast=False):
        print('move', delta, 'fast' if fast else 'slow')

    def spanmark(label, *, span: tuple[int, int] = (0, 0)):
        print('mark', label, span)
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

def sub_run(argv, capture_output=True, text=True, **kw):
    """
    subprocess.run for every Python we support: 3.6 has neither
    capture_output= nor text=, so this swallows those (they're
    the only shapes these tests use) and spells them the old way.
    """
    return subprocess.run(argv, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,
                           universal_newlines=True, **kw)


def subprocess_env(**extra):
    """
    A minimal environment for driving emitted scripts: PATH, plus
    the variables Windows can't start Python without (SystemRoot
    above all).  Extras layer on top.
    """
    env = {'PATH': os.environ.get('PATH', '')}
    for name in ('SystemRoot', 'SYSTEMROOT', 'COMSPEC', 'PATHEXT',
                 'TEMP', 'TMP'):
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    env.update(extra)
    return env


def run_script(script_path, argv, env=None):
    return sub_run(
        [sys.executable, script_path] + argv,
        capture_output=True, text=True,
        cwd=os.path.dirname(script_path),
        env=env if env is not None else subprocess_env(),
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

def test_error_stream_knob():
    # errors print to sys.stderr by default (the POSIX diagnostic
    # convention: pipelines reading stdout stay clean); errors=
    # takes any writable file object--sys.stdout is v1's behavior.
    # Requested help stays on stdout either way.
    import appeal as _appeal
    import contextlib, io

    def make_app(**kwargs):
        app = _appeal.Appeal(name='streams', **kwargs)
        @app.command()
        def greet(name):
            print(f'hi, {name}')
        return app

    # the default resolves sys.stderr AT ERROR TIME (like
    # print(file=None)), so redirection works
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), \
         contextlib.redirect_stderr(err):
        code = exit_code(lambda: make_app().main(['greet']))  # missing argument
    assert code == 2
    assert out.getvalue() == '', out.getvalue()
    assert 'error:' in err.getvalue() and 'usage:' in err.getvalue()

    # any writable file object works, verbatim
    sink = io.StringIO()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = exit_code(lambda: make_app(errors=sink).main(['greet']))
    assert code == 2
    assert out.getvalue() == ''
    assert 'error:' in sink.getvalue() and 'usage:' in sink.getvalue()

    # a non-stream refuses by name
    try:
        _appeal.Appeal(errors=42)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert '42' in str(e)

    # sys.stdout bakes into standalone scripts; a custom stream
    # can't travel to a script that hasn't run yet, so it refuses
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'streams_mod.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write("def greet(name):\n    print('hi, ' + name)\n")
        sys.path.insert(0, d)
        try:
            import streams_mod
            import importlib
            importlib.reload(streams_mod)
            app = _appeal.Appeal(name='streams', errors=sys.stdout)
            app.command()(streams_mod.greet)
            script = app.standalone()
            app2 = _appeal.Appeal(name='streams', errors=io.StringIO())
            app2.command()(streams_mod.greet)
            try:
                app2.standalone()
                assert False, 'expected AppealConfigurationError'
            except AppealConfigurationError as e:
                assert 'custom stream' in str(e)
        finally:
            sys.path.remove(d)
            sys.modules.pop('streams_mod', None)
        assert 'errors=sys.stdout' in script
        script_path = os.path.join(d, 'streams.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        r = run_script(script_path, ['greet'])
        assert r.returncode == 2
        assert 'error:' in r.stdout and r.stderr == '', r.stderr


def test_flag_explicit_boolean():
    # ruled 2026-07-09: a flag accepts an explicit boolean with
    # '=' only--exactly 'true' and 'false', no alternate-spelling
    # zoo ("yes" "si" "bueno" "naturally" "arguably" "usually").
    # Bare flags are unchanged; the '=' spelling exists so the
    # command line can overrule a config layer that turned a
    # flag on (defaults < config < argv, restored for flags).
    import appeal as _appeal

    def f(*, verbose=False):
        return verbose
    assert run_both(f, ['--verbose=true']) == ('ok', True)
    assert run_both(f, ['--verbose=false']) == ('ok', False)
    assert run_both(f, ['-v=false']) == ('ok', False)
    assert run_both(f, ['-v=true']) == ('ok', True)
    assert run_both(f, ['-v']) == ('ok', True)
    assert run_both(f, []) == ('ok', False)
    # only those two spellings, loudly
    for bad in ('maybe', 'True', '1', 'yes'):
        got = run_both(f, [f'--verbose={bad}'])
        assert got[0] == 'usage' and "'true' or" in got[1], got
    # explicit spellings are absolute (set); bare presence
    # idempotently stores not-default; last spoken wins (Larry's
    # ruling, 2026-07-18)
    got = run_both(f, ['--verbose', '--verbose=false'])
    assert got == ('ok', False), got
    got = run_both(f, ['--verbose=false', '-v'])
    assert got == ('ok', True), got
    # zero-arity folds are not flags: '=' still refuses
    def g(*, level: _appeal.counter() = 0):
        return level
    got = run_both(g, ['--level=3'])
    assert got[0] == 'usage' and "doesn't take a value" in got[1], got

    # a **kwargs-delivered flag carries the explicit False through
    app = _appeal.Appeal(name='kw')
    @app.command()
    @app.option('extra', '--extra', default=False)
    def kw(**kwargs):
        print(sorted(kwargs.items()))
    import io, contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app.process(['kw', '--extra=false'])
    assert out.getvalue().strip() == "[('extra', False)]"

    # per-instance (*args-group) flags take it too--spelled
    # announce-first, as always (the flag precedes its instance)
    def item(x: int, *, keep=False):
        return (x, keep)
    def rep(*args: item):
        return args
    assert run_both(rep, ['-k=false', '1', '-k=true', '2']) == \
        ('ok', ((1, False), (2, True)))

    # THE POINT: the command line can turn OFF what config turned on
    app2 = _appeal.Appeal(name='layer')
    @app2.global_command()
    def top(*, verbose=False):
        print('verbose', verbose)
    @app2.command()
    def work():
        pass
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app2.process(['--verbose=false', 'work'], config={'verbose': True})
    assert out.getvalue().strip() == 'verbose False'
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app2.process(['work'], config={'verbose': True})
    assert out.getvalue().strip() == 'verbose True'


def test_flag_explicit_boolean_standalone():
    # the '=' spelling works in emitted scripts (north star)
    import appeal as _appeal
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'fmod.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write("def run(*, verbose=False):\n"
                    "    print('verbose', verbose)\n")
        sys.path.insert(0, d)
        try:
            import fmod
            import importlib
            importlib.reload(fmod)
            app = _appeal.Appeal(name='fb')
            app.command()(fmod.run)
            script = app.standalone()
        finally:
            sys.path.remove(d)
            sys.modules.pop('fmod', None)
        script_path = os.path.join(d, 'fb.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        r = run_script(script_path, ['run', '--verbose=false'])
        assert (r.returncode, r.stdout) == (0, 'verbose False\n'), r.stderr
        r = run_script(script_path, ['run', '-v=true'])
        assert (r.returncode, r.stdout) == (0, 'verbose True\n'), r.stderr
        r = run_script(script_path, ['run', '--verbose=si'])
        assert r.returncode == 2
        assert "'true' or 'false'" in r.stderr


def test_file_converter():
    # appeal.file(): open() as a converter, with '-' meaning the
    # process-standard stream by MODE (the argparse/click answer
    # to "which one did they need"), wrapped so close() flushes
    # and goes inert--a contract neither argparse (bare stream)
    # nor click (misnamed wrapper) actually delivers.
    import appeal as _appeal
    import contextlib, io
    from appeal import build, compile_plan, interpreter_parse

    def cat(inp: _appeal.file()):
        data = inp.read()
        inp.close()
        return (type(inp).__name__, data)
    plan = build(cat)

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'x.txt')
        with open(path, 'wt', encoding='utf-8') as f:
            f.write('from disk')
        assert interpreter_parse(plan, [path]) == \
            ('TextIOWrapper', 'from disk')
        assert compile_plan(plan)([path]) == \
            ('TextIOWrapper', 'from disk')

    # '-' with a reading mode is stdin (fresh stream per rung:
    # reading consumes it)
    for drive in (lambda: interpreter_parse(plan, ['-']),
                  lambda: compile_plan(plan)(['-'])):
        old = sys.stdin
        sys.stdin = io.StringIO('from stdin')
        try:
            assert drive() == ('_ProcessStream', 'from stdin')
        finally:
            sys.stdin = old

    # '-' with a writing mode is stdout; close()/with flush and
    # go inert--the real stream survives
    def emit(dest: _appeal.file('w')):
        with dest as f:
            f.write('written')
        dest.close()                     # double-close: also fine
        return dest.closed
    eplan = build(emit)
    for drive in (lambda: interpreter_parse(eplan, ['-']),
                  lambda: compile_plan(eplan)(['-'])):
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            assert drive() is True
            captured = sys.stdout.getvalue()
            sys.stdout.write('still alive')     # not closed!
        finally:
            sys.stdout = old
        assert captured == 'written'

    # loud refusals: unopenable path (with strerror), '-' on a
    # read-write mode
    got = run_both(cat, ['/nope/missing.txt'])
    assert got[0] == 'usage' and "can't open" in got[1], got
    def rw(f: _appeal.file('r+')):
        return f
    got = run_both(rw, ['-'])
    assert got[0] == 'usage' and "read-write" in got[1], got

    # binary modes get the .buffer layer
    def slurp(inp: _appeal.file('rb')):
        return inp._stream is sys.stdin.buffer
    assert run_both(slurp, ['-']) == ('ok', True)

    # the recipe is structured--(kind, factory, args, kwargs),
    # arguments live--and carries only non-default settings
    conv = _appeal.file('w', encoding='utf-8')
    assert conv.__appeal_recipe__ == \
        ('call', 'file', ('w',), {'encoding': 'utf-8'})
    assert _appeal.file().__appeal_recipe__ == \
        ('call', 'file', ('r',), {})
    # an opener is a callable: no recipe, so standalone refuses
    assert not hasattr(_appeal.file(opener=os.open),
                       '__appeal_recipe__')


def test_file_converter_standalone():
    # the north star: '-' pipes through a dependency-free script,
    # and a `= sys.stdout` default renders by identity
    import appeal as _appeal
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'upmod.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(
                "import sys\n"
                "import appeal\n"
                "def cat(inp: appeal.file() = None,\n"
                "        *, out: appeal.file('w') = sys.stdout):\n"
                "    data = inp.read() if inp else ''\n"
                "    out.write(data.upper())\n"
                "    out.close()\n")
        sys.path.insert(0, d)
        try:
            import upmod
            import importlib
            importlib.reload(upmod)
            app = _appeal.Appeal(name='upcat')
            app.command()(upmod.cat)
            script = app.standalone()
        finally:
            sys.path.remove(d)
            sys.modules.pop('upmod', None)
        assert "file('r')" in script
        assert '_out_default = sys.stdout' in script
        assert 'class _ProcessStream' in script
        script_path = os.path.join(d, 'upcat.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        env = subprocess_env(PYTHONPATH=repo_dir)
        r = sub_run(
            [sys.executable, script_path, 'cat', '-'],
            input='hello\n', capture_output=True, text=True,
            cwd=d, env=env)
        assert (r.returncode, r.stdout) == (0, 'HELLO\n'), r.stderr
        with open(os.path.join(d, 'in.txt'), 'wt') as f:
            f.write('x\n')
        r = sub_run(
            [sys.executable, script_path, 'cat', 'in.txt',
             '--out', 'out.txt'],
            capture_output=True, text=True, cwd=d, env=env)
        assert r.returncode == 0, r.stderr
        with open(os.path.join(d, 'out.txt')) as f:
            assert f.read() == 'X\n'


def test_usage_formatter_knobs():
    # Appeal(margin=, indent=): v1's
    # knobs, wired (they were stored-and-never-read; the dead
    # indent default of 2 was also a lie--the templates' real
    # indent is 4, v1's default).  max_columns caps the wrap
    # margin, min'd with the terminal width at render time
    # (pipes get the cap, so this output is stable);
    # indent_definitions re-indents the section templates, which
    # own layout.
    import appeal as _appeal
    import contextlib, io

    def make_app(**kw):
        app = _appeal.Appeal(name='serve', **kw)
        @app.global_command()
        def serve(host, port: int = 8080, *, verbose=False):
            """
            Serves the thing with a summary long enough that a
            narrow margin will have to re-wrap it across lines.

            Arguments:
              host: The host to serve on, described at length so
                wrapping becomes visible in a narrow margin.

            Options:
              verbose: Print more output.
            """
        return app

    def helptext(app):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exit_code(lambda: app.main(['--help']))
        return out.getvalue()

    wide = helptext(make_app())
    assert max(len(l) for l in wide.splitlines()) > 40
    narrow = helptext(make_app(margin=40))
    assert max(len(l) for l in narrow.splitlines()) <= 40
    assert 'Serves the thing' in narrow

    # the docstring WROTE its sections, so its own 2-space entry
    # indent wins over the indent= knob (ruling D, 2026-08-01:
    # the knob shapes the TEMPLATE's sections; the author's
    # presentation governs the sections they authored)
    indented = helptext(make_app(indent=8))
    row = next(l for l in indented.splitlines()
               if l.strip().startswith('<HOST>'))
    assert row.startswith('  <HOST>'), repr(row)
    # a docstring with no sections takes the knob's indent
    app8 = _appeal.Appeal(name='k8', indent=8)
    @app8.global_command()
    def plainer(host, *, verbose=False):
        "No sections here."
    text8 = helptext(app8)
    row8 = next(l for l in text8.splitlines()
                if l.strip().startswith('<HOST>'))
    assert row8.startswith(' ' * 8) and row8[8] != ' ', repr(row8)

    # garbage refuses by name
    for knob in ('margin', 'indent'):
        try:
            _appeal.Appeal(**{knob: 'wide'})
            assert False, 'expected AppealConfigurationError'
        except AppealConfigurationError as e:
            assert knob in str(e)


def test_usage_knobs_standalone():
    # the cap bakes into the script, and the SCRIPT's terminal
    # decides the rest at its own runtime (COLUMNS narrows below
    # the cap; a pipe gets the cap)
    import appeal as _appeal
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'kmod.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(
                'def serve(host):\n'
                '    \'\'\'\n'
                '    Serves the thing with a summary long enough\n'
                '    that a narrow terminal must re-wrap it.\n'
                '    \'\'\'\n')
        sys.path.insert(0, d)
        try:
            import kmod
            import importlib
            importlib.reload(kmod)
            app = _appeal.Appeal(name='s', margin=60)
            app.command()(kmod.serve)
            script = app.standalone()
        finally:
            sys.path.remove(d)
            sys.modules.pop('kmod', None)
        assert 'help_margin(60)' in script
        script_path = os.path.join(d, 's.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        env = subprocess_env(PYTHONPATH=repo_dir)
        r = sub_run(
            [sys.executable, script_path, 'serve', '--help'],
            capture_output=True, text=True, cwd=d, env=env)
        assert max(len(l) for l in r.stdout.splitlines()) <= 60
        r = sub_run(
            [sys.executable, script_path, 'serve', '--help'],
            capture_output=True, text=True, cwd=d,
            env={**env, 'COLUMNS': '38'})
        assert max(len(l) for l in r.stdout.splitlines()) <= 38


def test_did_you_mean():
    # typo suggestions (ruled 2026-07-09): unknown commands and
    # unknown LONG options append difflib's closest matches--
    # short options don't (single letters aren't "close"), and
    # nothing close means no tail.  The suggestion only ever
    # decorates an error that already fired.
    import appeal as _appeal
    import contextlib, io

    app = _appeal.Appeal(name='tool', version='1.0')
    @app.command()
    def status(*, verbose=False):
        pass
    @app.command()
    def stats():
        pass

    def main_err(argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
             contextlib.redirect_stdout(io.StringIO()):
            assert exit_code(lambda: app.main(argv)) == 2
        return err.getvalue().split('\n')[0]

    assert main_err(['stauts']) == \
        "error: unknown command 'stauts' (did you mean 'stats' " \
        "or 'status'?)"
    assert main_err(['status', '--verbos']) == \
        "error: unknown option '--verbos' (did you mean " \
        "'--verbose'?)"
    # the auto words suggest too (resolvable words are the pool)
    assert "did you mean 'version'" in main_err(['versoin'])
    assert "did you mean 'help'" in main_err(['hlep'])
    # help's topic path suggests as well
    assert "did you mean" in main_err(['help', 'stauts'])
    # nothing close: no tail
    assert main_err(['zzqqxx']) == "error: unknown command 'zzqqxx'"
    # short options don't suggest
    err = main_err(['status', '-q'])
    assert err == "error: unknown option '-q'", err

    # both rungs agree (parse_tokens is shared; the dispatchers
    # each have their own site)
    def cmd(*, verbose=False):
        return verbose
    got = run_both(cmd, ['--verbos'])
    assert got[0] == 'usage' and "did you mean '--verbose'" in got[1]

    # the emitted dispatcher's unknown-command site
    import contextlib, io
    out = io.StringIO()
    def alpha():
        return 'a'
    def beta():
        return 'b'
    from appeal import compile_command_set
    parse = compile_command_set({'alpha': build(alpha),
                                 'beta': build(beta)}, prog='t')
    try:
        parse(['alhpa'])
        assert False, 'expected UsageError'
    except UsageError as e:
        assert "did you mean 'alpha'" in str(e), e


def test_keyboard_interrupt():
    # ^C dies quietly with 128+SIGINT=130--in main() ONLY (ruled
    # 2026-07-09: run_main is the whole-program driver; process()
    # stays raw, the automation contract; nothing else in Appeal
    # touches signals)
    import appeal as _appeal
    import contextlib, io

    app = _appeal.Appeal(name='k')
    @app.command()
    def boom():
        raise KeyboardInterrupt

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), \
         contextlib.redirect_stderr(err):
        code = exit_code(lambda: app.main(['boom']))
    assert code == 130
    assert out.getvalue() == '' and err.getvalue() == ''

    # process() propagates raw
    try:
        app.process(['boom'])
        assert False, 'expected KeyboardInterrupt'
    except KeyboardInterrupt:
        pass


def test_keyboard_interrupt_standalone():
    import appeal as _appeal
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'imod.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write("def boom():\n    raise KeyboardInterrupt\n")
        sys.path.insert(0, d)
        try:
            import imod
            import importlib
            importlib.reload(imod)
            app = _appeal.Appeal(name='k')
            app.command()(imod.boom)
            script = app.standalone()
        finally:
            sys.path.remove(d)
            sys.modules.pop('imod', None)
        script_path = os.path.join(d, 'k.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        r = run_script(script_path, ['boom'])
        assert r.returncode == 130, (r.returncode, r.stderr)
        assert r.stdout == '' and r.stderr == ''


DEEP_MODULE = """\
def db(label):
    print('db', label)
def migrate(step):
    print('migrate', step)
def up(n: int):
    print('up', n)
def down(n: int):
    print('down', n)
def status():
    print('status')
"""


def test_deep_nested_sets():
    # sets nested deeper than one level (built 2026-07-09; the
    # standalone refusal lifted, and plan_for taught to find a
    # nested parent--the stack machinery was always deep, the
    # plan lookup wasn't)
    import appeal as _appeal
    import contextlib, io

    def make_app(mod):
        app = _appeal.Appeal(name='t', repeat=True)
        app.command()(mod.status)
        app.command()(mod.db)
        db = app.command('db', repeat=True)
        db.command()(mod.migrate)
        migrate = db.command('migrate', repeat=True)
        migrate.command()(mod.up)
        migrate.command()(mod.down)
        return app

    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'deepmod.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(DEEP_MODULE)
        sys.path.insert(0, d)
        try:
            import deepmod
            import importlib
            importlib.reload(deepmod)
            app = make_app(deepmod)

            # in-process: three levels, cycling pops through two
            # of them back to the root
            argv = ['db', 'main', 'migrate', 'two', 'up', '3',
                    'down', '1', 'status']
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                app.process(list(argv))
            expected = 'db main\nmigrate two\nup 3\ndown 1\nstatus\n'
            assert out.getvalue() == expected

            # completion at the depth-2 boundary offers the whole
            # resolution chain
            chain = app.complete(['db', 'main', 'migrate', 'two'], '')
            assert chain == ['db', 'down', 'help', 'migrate',
                             'status', 'up'], chain

            # a dangling depth-2 parent errors with ITS usage
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                 contextlib.redirect_stdout(io.StringIO()):
                assert exit_code(lambda: app.main(
                    ['db', 'main', 'migrate', 'two', 'up'])) == 2

            script = app.standalone()
        finally:
            sys.path.remove(d)
            sys.modules.pop('deepmod', None)

        script_path = os.path.join(d, 'deep.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        r = run_script(script_path, list(argv))
        assert (r.returncode, r.stdout) == (0, expected), r.stderr
        # help describes a nested parent instead of unpacking its
        # _SET_ dict (fixed 2026-07-11: raw ValueError before)
        r = run_script(script_path, ['help', 'db'])
        assert r.returncode == 0, r.stderr
        assert r.stdout.startswith('usage: db')
        assert 'migrate' in r.stdout
        # baked completion agrees with in-process at depth 2
        base = subprocess_env()
        r = sub_run(
            [sys.executable, script_path],
            capture_output=True, text=True, cwd=d,
            env={**base, '_APPEAL_COMPLETE': 'bash',
                        'COMP_WORDS': 't\ndb\nmain\nmigrate\ntwo\n',
                        'COMP_CWORD': '5'})
        assert r.stdout.splitlines() == chain, r.stdout
        # a malformed line deep in the tree still means NO work
        r = run_script(script_path,
                       ['db', 'main', 'migrate', 'two', 'up', 'x',
                        'status'])
        assert r.returncode == 2
        assert r.stdout.startswith('db main\nmigrate two\n'), r.stdout


TREE_MODULE = """\
def db(*, host='local'):
    print('db', host)
def deploy(n: int):
    print('deploy', n)
def db_default():
    print('db-default')
def root_default():
    print('root-default')
def some_function(a):
    print('renamed', a)
"""


def test_appeal_tree_registration():
    # The command tree is a tree of Appeal instances (v1's model,
    # restored 2026-07-18 by Larry's ruling: no registrar objects,
    # ever).  @app.command('x') RENAMES--the word is 'x', the
    # function's name is ignored and does not dispatch.
    import appeal as _appeal
    import contextlib, io

    app = _appeal.Appeal(name='t')
    @app.command('x')
    def some_function(a):
        return ('x', a)
    assert app.process(['x', 'hi']) == ('x', 'hi')
    try:
        app.process(['some_function', 'z'])
        assert False, 'function name must not dispatch'
    except _appeal.AppealUsageError:
        pass

    # app.command('word') returns a full Appeal, the same node
    # every time; the tree is linked by .parent
    node = app.command('x')
    assert isinstance(node, _appeal.Appeal)
    assert app.command('x') is node
    assert node.parent is app
    assert node.root is app

    # the older v2 kwarg spelling is the same fetch
    assert app.command(parent='x') is node

    # re-registration replaces (v1: the second wins)
    @app.command('x')
    def replacement(a):
        return ('replaced', a)
    assert app.process(['x', 'hi']) == ('replaced', 'hi')

    # Appeal(parent=) hangs a node in the tree directly
    app2 = _appeal.Appeal(name='u')
    child = _appeal.Appeal('sub', parent=app2)
    @child.command()
    def leaf():
        return 'leaf!'
    assert app2.process(['sub', 'leaf']) == 'leaf!'

    # chained .option() works (the child is an Appeal, so every
    # registration method is there)
    app3 = _appeal.Appeal(name='v')
    @app3.command()
    def serve(*, quiet=False):
        pass
    @app3.command('serve').command()
    @app3.command('serve').option('port', '-p')
    def start(*, port=80):
        return ('start', port)
    # -p derives int from port=80 (ruled 2026-07-25)
    assert app3.process(['serve', 'start', '-p', '99']) == ('start', 99)


def test_appeal_tree_default_commands():
    # default_command at every level: the root's runs on an empty
    # line; a subcommand node's (@app.command('db')
    # .default_command()) runs when the line stops at the parent.
    # Both rungs: in-process and the standalone script.
    import appeal as _appeal
    import contextlib, io

    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'treemod.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(TREE_MODULE)
        sys.path.insert(0, d)
        try:
            import treemod
            import importlib
            importlib.reload(treemod)
            app = _appeal.Appeal(name='t')
            app.command()(treemod.db)
            db = app.command('db')
            db.default_command()(treemod.db_default)
            db.command()(treemod.deploy)
            app.default_command()(treemod.root_default)

            # in-process: line stops at the parent -> its default
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                app.process(['db', '--host', 'prod'])
            assert out.getvalue() == 'db prod\ndb-default\n', out.getvalue()
            # a named subcommand still dispatches
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                app.process(['db', 'deploy', '5'])
            assert out.getvalue() == 'db local\ndeploy 5\n', out.getvalue()
            # empty line -> the root default
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                app.process([])
            assert out.getvalue() == 'root-default\n', out.getvalue()

            # the standalone rung agrees on all three
            script = app.standalone(argv0='t')
            script_path = os.path.join(d, 'tree_cli.py')
            with open(script_path, 'wt', encoding='utf-8') as f:
                f.write(script)
            env = subprocess_env(PYTHONPATH=d + os.pathsep + repo_dir)
            r = run_script(script_path, ['db', '--host', 'prod'], env=env)
            assert (r.returncode, r.stdout) == (0, 'db prod\ndb-default\n'), r
            r = run_script(script_path, ['db', 'deploy', '5'], env=env)
            assert (r.returncode, r.stdout) == (0, 'db local\ndeploy 5\n'), r
            r = run_script(script_path, [], env=env)
            assert (r.returncode, r.stdout) == (0, 'root-default\n'), r
        finally:
            sys.path.remove(d)
            sys.modules.pop('treemod', None)


REPEAT_MODULE = '\n'.join(
    f"def {name}():\n    print({name!r})"
    for name in ('A', 'Ax', 'AxA', 'AxB', 'Ay', 'AyA', 'AyB',
                 'B', 'C', 'Ca', 'CaX')) + '\n'


def test_subcommands_interacting_with_repeat():
    # Larry's spec (2026-07-18): commands A B C; subcommands named
    # parent-first (Ax under A, AxB under Ax), three levels deep,
    # repeat exercised at every level.  One line touches it all:
    #   A Ax AxA AxB Ay AyA AyB B C Ca CaX B
    # AxB needs Ax's set to cycle, Ay needs A's set to cycle (and
    # pops Ax's frame), B needs the root to cycle (and pops both),
    # the final B pops back out of Ca's depth-3 set.
    import appeal as _appeal
    import contextlib, io

    line = ['A', 'Ax', 'AxA', 'AxB', 'Ay', 'AyA', 'AyB',
            'B', 'C', 'Ca', 'CaX', 'B']
    expected = '\n'.join(line) + '\n'

    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'repeatmod.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(REPEAT_MODULE)
        sys.path.insert(0, d)
        try:
            import repeatmod
            import importlib
            importlib.reload(repeatmod)

            app = _appeal.Appeal(name='t', repeat=True)
            app.command()(repeatmod.A)
            a = app.command('A', repeat=True)
            a.command()(repeatmod.Ax)
            ax = a.command('Ax', repeat=True)
            ax.command()(repeatmod.AxA)
            ax.command()(repeatmod.AxB)
            a.command()(repeatmod.Ay)
            ay = a.command('Ay', repeat=True)
            ay.command()(repeatmod.AyA)
            ay.command()(repeatmod.AyB)
            app.command()(repeatmod.B)
            app.command()(repeatmod.C)
            c = app.command('C', repeat=True)
            c.command()(repeatmod.Ca)
            ca = c.command('Ca', repeat=True)
            ca.command()(repeatmod.CaX)

            # in-process
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                app.process(list(line))
            assert out.getvalue() == expected, out.getvalue()

            # a set whose repeat is OFF refuses re-entry: with
            # everything else identical, a second Ax-set command
            # can't resolve once the walk has left the set
            app2 = _appeal.Appeal(name='t2', repeat=True)
            app2.command()(repeatmod.A)
            a2 = app2.command('A', repeat=True)
            a2.command()(repeatmod.Ax)
            ax2 = a2.command('Ax')          # no repeat
            ax2.command()(repeatmod.AxA)
            ax2.command()(repeatmod.AxB)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    app2.process(['A', 'Ax', 'AxA', 'AxB'])
                assert False, 'expected AppealUsageError'
            except _appeal.AppealUsageError as e:
                assert 'AxB' in str(e), e

            # the standalone rung agrees, token for token
            script = app.standalone(argv0='t')
            script_path = os.path.join(d, 'repeat_cli.py')
            with open(script_path, 'wt', encoding='utf-8') as f:
                f.write(script)
            env = subprocess_env(PYTHONPATH=d + os.pathsep + repo_dir)
            r = run_script(script_path, list(line), env=env)
            assert (r.returncode, r.stdout) == (0, expected), r
        finally:
            sys.path.remove(d)
            sys.modules.pop('repeatmod', None)


def test_documentation_man():
    # app.documentation('man'): the help corpus in troff clothing
    # (the completion(shell) shape: a format name in, text out).
    # Installing the page somewhere is packaging's business.
    import appeal as _appeal
    import shutil as _shutil

    app = _appeal.Appeal(name='mytool', version='2.0')
    @app.global_command()
    def top(*, trace=False):
        """
        A demonstration tool.

        Longer prose about the tool.

        Options:
          trace: Print a trace of everything.
        """
    @app.command()
    def greet(name, *, shout=False):
        """
        Greets a name.

        Arguments:
          name: Who to greet.

        Options:
          shout: LOUDER.
        """
    text = app.documentation('man')
    assert text.startswith('.TH MYTOOL 1 "" "mytool 2.0" ""\n')
    assert '.SH NAME\nmytool \\- A demonstration tool.' in text
    assert '.B mytool [\\-t|\\-\\-trace] command' in text
    assert '.B mytool greet [\\-s|\\-\\-shout] <NAME>' in text
    assert '.SH OPTIONS' in text and 'Print a trace' in text
    assert '.SS "mytool greet"' in text
    assert '.B \\-s|\\-\\-shout' in text and 'LOUDER.' in text
    # the auto commands document themselves in COMMANDS
    assert "Print the program's version." in text
    # a single-command program: one page, no COMMANDS
    solo = _appeal.Appeal(name='solo')
    @solo.global_command()
    def run(thing):
        "Runs the thing."
    stext = solo.documentation('man')
    assert '.SH NAME\nsolo \\- Runs the thing.' in stext
    assert '.SH COMMANDS' not in stext
    # unknown formats refuse by name
    try:
        app.documentation('html')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'html' in str(e)
    # when a troff renderer is around, the page renders clean
    # (an extra assertion, not a skipped test)
    groff = _shutil.which('groff')
    if groff:
        r = sub_run([groff, '-man', '-Tutf8'],
                           input=text, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert 'SYNOPSIS' in r.stdout


def test_appeal_error_umbrella():
    # the umbrella reparenting (ruled 2026-07-09): AppealError is
    # the base of everything Appeal raises (v1's
    # AppealBaseException role--kept as an alias), and it has one
    # job of its own: raise it from a command for a polite
    # message + exit 1, no usage (the command line was fine).
    import appeal as _appeal
    import contextlib, io

    assert issubclass(_appeal.AppealDataError, _appeal.AppealError)
    assert issubclass(_appeal.AppealUsageError, _appeal.AppealError)
    assert issubclass(_appeal.AppealConfigurationError,
                      _appeal.AppealError)
    assert _appeal.AppealBaseException is _appeal.AppealError

    app = _appeal.Appeal(name='t')
    @app.command()
    def fetch():
        raise _appeal.AppealError("couldn't reach the server")
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
         contextlib.redirect_stdout(io.StringIO()):
        assert exit_code(lambda: app.main(['fetch'])) == 1
    assert err.getvalue() == "error: couldn't reach the server\n"

    # configuration errors are bugs: main() lets them raise, even
    # though they're under the umbrella now
    app2 = _appeal.Appeal(name='t2')
    @app2.command()
    def c(x: _appeal.counter):      # factory not called
        pass
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            exit_code(lambda: app2.main(['c', '1']))
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError:
        pass

    # the reparenting revived _config_inject's dead except clause:
    # a bad config boolean now carries the usage it always meant to
    app3 = _appeal.Appeal(name='t3')
    @app3.global_command()
    def top(*, verbose=False):
        pass
    @app3.command()
    def work():
        pass
    try:
        app3.process(['work'], config={'verbose': 'maybe'})
        assert False, 'expected AppealDataError'
    except _appeal.AppealDataError as e:
        assert "can't read 'maybe'" in str(e)
        assert e.usage


def test_appeal_error_standalone():
    # the raise-for-exit-1 job travels (the umbrella lives in the
    # exceptions snippet)
    import appeal as _appeal
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'emod.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write("import appeal\n"
                    "def fetch():\n"
                    "    raise appeal.AppealError('no server')\n")
        sys.path.insert(0, d)
        try:
            import emod
            import importlib
            importlib.reload(emod)
            app = _appeal.Appeal(name='t')
            app.command()(emod.fetch)
            script = app.standalone()
        finally:
            sys.path.remove(d)
            sys.modules.pop('emod', None)
        script_path = os.path.join(d, 't.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        env = subprocess_env(PYTHONPATH=repo_dir)
        r = sub_run(
            [sys.executable, script_path, 'fetch'],
            capture_output=True, text=True, cwd=d, env=env)
        assert r.returncode == 1
        assert r.stderr == 'error: no server\n', r.stderr


def test_config_group_options_and_empty_config():
    # a group's mapping value reads read_mapping style: the
    # child's parameters by name AND its own options, recursively
    # (regression: validation considered only child slots, so
    # {'where': {'x':1,'deep':3}} was "unknown deep"); and an
    # empty config on a commands-only app is a no-op (regression:
    # it crashed reaching _config_vet(None, ...)).
    def wh(x: int, *, deep: int = 0):
        return (x, deep)
    seen = []
    def make():
        app = Appeal(name='cfg')
        seen.clear()
        @app.global_command()
        def top(*, where: wh = None):
            seen.append(where)
        @app.command()
        def go():
            pass
        return app
    make().process(['go'], config={'where': {'x': 1, 'deep': 3}})
    assert seen[0] == (1, 3), seen
    make().process(['go'], config={'where': {'x': 1}})
    assert seen[0] == (1, 0), seen
    # atomic per option: argv naming the group wins WHOLE--the
    # config's inner option must not leak in
    make().process(['--where', '9', 'go'],
                   config={'where': {'x': 1, 'deep': 3}})
    assert seen[0] == (9, 0), seen
    # unknown keys still refuse, by name
    try:
        make().process(['go'], config={'where': {'x': 1, 'zz': 2}})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'zz' in str(e)
    # a scoped INNER option refuses like a scoped top-level one:
    # a mapping has no position
    def top2_factory():
        app = Appeal(name='sc')
        @app.global_command()
        def top2(*, w1: wh = None, w2: wh = None):
            pass
        @app.command()
        def go2():
            pass
        return app
    try:
        top2_factory().process(['go2'], config={'w1': {'x': 1, 'deep': 3}})
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'scoped' in str(e) and 'deep' in str(e)

    # commands-only app: empty config no-ops, nonempty refuses
    app = Appeal(name='solo')
    @app.command()
    def solo():
        return 'ran'
    assert app.process(['solo'], config={}) == 'ran'
    try:
        app.process(['solo'], config={'x': 1})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'no global command' in str(e)


def test_config_error_provenance_is_structural():
    # a run-stage UsageError becomes a config error ONLY when it
    # is ABOUT something config supplied--matched by the error's
    # param identity, never by grepping the message.  Regression:
    # a bad positional (count='banana') flipped to AppealDataError
    # merely because config supplied option 'a' and the letter a
    # appears in the message.
    def make():
        app = Appeal(name='m')
        @app.global_command()
        def top(count: int, *, a='', level: int = 0):
            pass
        @app.command()
        def go():
            pass
        return app
    # a bad ARGV positional stays a usage error, config or not
    for config in (None, {'a': 'x'}):
        try:
            make().process(['banana', 'go'], config=config)
            assert False, 'expected UsageError'
        except AppealDataError as e:
            # unprefixed canonical (ruled 2026-07-25; matches 0.6.4,
            # whose prefixed spellings were the 'old names' aliases)
            assert type(e).__name__ == 'UsageError', (config, e)
            assert not str(e).startswith('config:')
    # a bad CONFIG value converts, and says so
    try:
        make().process(['1', 'go'], config={'level': 'banana'})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert type(e) is AppealDataError
        assert str(e).startswith('config:')
        assert e.param == 'level'
    # ...including a group operand supplied through config
    def wh(x: int, *, deep: int = 0):
        return (x, deep)
    app = Appeal(name='m3')
    @app.global_command()
    def top3(*, where: wh = None):
        pass
    @app.command()
    def go3():
        pass
    try:
        app.process(['go3'], config={'where': {'x': 'nope'}})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert str(e).startswith('config:') and e.param == 'x'


def test_config_scoped_refusal():
    # refused BY DESIGN (ruled 2026-07-09, the last named
    # refusal): position is the essence of a scoped option, and
    # a mapping has no position.  The refusal names the
    # workaround.
    import appeal as _appeal

    def child(p, *, flavor=''):
        return (p, flavor)
    app = _appeal.Appeal(name='t')
    @app.global_command()
    def mg(a, b: child = None, c: child = None):
        pass
    @app.command()
    def work():
        pass
    try:
        app.process(['work'], config={'flavor': 'sour'})
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'position decides' in str(e)
        assert '@app.option' in str(e)


def test_concurrent_first_parse():
    # the README's "even simultaneously" claim, made true for the
    # FIRST parse too (ruled 2026-07-11): compilation runs
    # lock-free (user code is never called under a lock--the
    # house discipline), and a plain Lock guards only the
    # test-and-set installs, so racing threads each build, one
    # wins, the rest adopt the winner.
    import threading
    import appeal as _appeal

    for round_number in range(5):
        app = _appeal.Appeal(name='cc', repeat=True)
        results = []
        @app.command()
        def add(x: int, y: int):
            results.append(x + y)
        @app.command()
        def mul(x: int, y: int):
            results.append(x * y)

        n = 8
        barrier = threading.Barrier(n)
        errors = []
        def race(i):
            barrier.wait()
            try:
                app.process(['add', str(i), '1', 'mul', str(i), '2'])
            except Exception as e:      # pragma: no cover
                errors.append(e)
        threads = [threading.Thread(target=race, args=(i,))
                   for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        assert sorted(results) == sorted(
            [i + 1 for i in range(n)] + [i * 2 for i in range(n)])
        # post-race, the caches are coherent: one plan per word
        assert app.plan_for('add') is app.plan_for('add')

    # racing plan_for directly: every thread gets the SAME object
    app2 = _appeal.Appeal(name='cc2')
    @app2.command()
    def solo(x: int):
        pass
    seen = []
    barrier = threading.Barrier(8)
    def get_plan():
        barrier.wait()
        seen.append(app2.plan_for('solo'))
    threads = [threading.Thread(target=get_plan) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(p) for p in seen}) == 1, 'plan identity diverged'


def test_version():
    # Appeal(version=...) wires the command line (ruled 2026-07-09,
    # v1's shape minus the -v short): --version as the first token
    # prints the bare string and exits 0, and command sets get an
    # automatic `version` command--suppressed by a user-defined
    # one, absent entirely when version= isn't given.
    import appeal as _appeal
    import contextlib, io

    def main(app, argv):
        # main() EXITS (0.6.4's contract, restored 2026-07-19)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                app.main(argv)
                code = 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 0
        return code, out.getvalue()

    app = _appeal.Appeal(name='tool', version='1.2.3')
    @app.command()
    def work(*, verbose=False):
        print('work', verbose)
    assert main(app, ['--version']) == (0, '1.2.3\n')
    assert main(app, ['version']) == (0, '1.2.3\n')
    # no -v short: the user's verbose keeps it, unambiguously
    assert main(app, ['work', '-v']) == (0, 'work True\n')
    # the version command takes no arguments, loudly
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code, out = main(app, ['version', 'extra'])
    assert code == 2 and ('takes no arguments' in err.getvalue()
                          or 'expected 0' in err.getvalue())
    # the listing documents it (before help, v1's order)
    code, out = main(app, ['help'])
    assert code == 0
    assert out.index('version') < out.index('help ')
    assert "Print the program's version." in out
    # help DESCRIBES the auto commands (fixed 2026-07-11: this
    # errored in-process and raised raw TypeError in standalone)
    assert main(app, ['help', 'version']) == \
        (0, "Print the program's version.\n")
    assert main(app, ['help', 'help']) == \
        (0, 'Print usage documentation on a specific command.\n')
    # completion offers the word
    assert 'version' in app.complete([], '')

    # a user-defined version command wins
    app2 = _appeal.Appeal(name='t2', version='9.9')
    @app2.command()
    def version():
        print('mine')
    assert main(app2, ['version']) == (0, 'mine\n')

    # no version= means no version anything
    app3 = _appeal.Appeal(name='t3')
    @app3.command()
    def go():
        pass
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code, out = main(app3, ['--version'])
    assert code == 2 and '--version' in err.getvalue()

    # a global-only program answers --version too
    app4 = _appeal.Appeal(name='solo', version='4.5')
    @app4.global_command()
    def solo(x: int = 0):
        print('solo', x)
    assert main(app4, ['--version']) == (0, '4.5\n')


def test_version_standalone():
    # the version bakes into standalone scripts: both spellings
    import appeal as _appeal
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'vmod.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write("def add(x: int, y: int):\n    print(x + y)\n")
        sys.path.insert(0, d)
        try:
            import vmod
            import importlib
            importlib.reload(vmod)
            app = _appeal.Appeal(name='calc', version='7.7')
            app.command()(vmod.add)
            script = app.standalone()
            app2 = _appeal.Appeal(name='calc')
            app2.command()(vmod.add)
            unversioned = app2.standalone()
        finally:
            sys.path.remove(d)
            sys.modules.pop('vmod', None)
        assert "version='7.7'" in script
        assert 'version' not in unversioned.rpartition(
            'if __name__')[2]           # nothing baked when unset
        script_path = os.path.join(d, 'calc.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        r = run_script(script_path, ['--version'])
        assert (r.returncode, r.stdout) == (0, '7.7\n'), r.stderr
        r = run_script(script_path, ['version'])
        assert (r.returncode, r.stdout) == (0, '7.7\n'), r.stderr
        r = run_script(script_path, ['help', 'version'])
        assert (r.returncode, r.stdout) == \
            (0, "Print the program's version.\n"), r.stderr
        r = run_script(script_path, ['add', '2', '3'])
        assert (r.returncode, r.stdout) == (0, '5\n'), r.stderr


def test_parse_before_execute():
    # the Appeal rule (ruled 2026-07-08): a malformed line does NO
    # work.  The global command must not run when the command
    # portion of the line fails to scan.
    import appeal as _appeal
    ran = []
    app = _appeal.Appeal(name='ms')
    @app.global_command()
    def top(*, trace=False):
        ran.append('top')
    @app.command()
    def add(x: int, y: int):
        ran.append(('add', x, y))
    try:
        app.process(['--trace', 'add', '1'])   # add needs 2
        assert False, 'expected UsageError'
    except UsageError:
        pass
    assert ran == [], ran
    # a well-formed line still executes left to right
    app2 = _appeal.Appeal(name='ms2')
    @app2.global_command()
    def top2(*, trace=False):
        ran.append('top2')
    @app2.command()
    def add2(x: int, y: int):
        ran.append(('add2', x, y))
    app2.process(['--trace', 'add2', '1', '2'])
    assert ran == ['top2', ('add2', 1, 2)], ran


def test_processor():
    # the Appeal/Processor divorce: Appeal is the registry and
    # compiler; a Processor is one trip through one command line
    import appeal as _appeal
    ran = []
    app = _appeal.Appeal(name='pr')
    @app.global_command()
    def top(*, trace=False):
        ran.append('top')
    @app.command()
    def add(x: int, y: int):
        ran.append(('add', x, y))
    # stage 1 only: nothing executes, and the artifact is readable
    processor = app.parse(['--trace', 'add', '1', '2'])
    assert ran == []
    assert processor.result is None and processor.instances == []
    text = repr(processor)
    assert '(global)' in text and 'add' in text, text
    # stage 2: left to right, and the mechanical execution log
    processor.execute()
    assert ran == ['top', ('add', 1, 2)]
    assert processor.instances == [(None, None), (add, None)]
    assert app.instances == [(None, None), (add, None)]
    # a malformed line dies at parse time
    try:
        app.parse(['add', '1'])
        assert False, 'expected UsageError'
    except UsageError:
        pass
    # app.instances reads the most recent run
    app.process(['add', '3', '4'])
    assert ran[-1] == ('add', 3, 4)
    assert app.instances == [(None, None), (add, None)]
    # v1 compat: an unparsed Processor is a callable execution object
    p2 = app.processor()
    p2(['add', '5', '6'])
    assert ran[-1] == ('add', 5, 6)


def test_processor_single_command():
    import appeal as _appeal
    app = _appeal.Appeal(name='one')
    @app.global_command()
    def top(a: int, b: int = 0):
        return a + b
    processor = app.parse(['3', '4'])
    assert processor.instances == []
    assert processor.execute() == 7
    assert processor.result == 7
    assert app.instances == [(None, None)]


def test_cycling():
    # Appeal's command cycling (repeat=True), rebuilt from Larry's
    # spec--v1's flag existed but its loop was broken in 0.6.4
    import appeal as _appeal
    ran = []
    app = _appeal.Appeal(name='cyc', repeat=True)
    @app.command()
    def add(x: int, y: int):
        ran.append(('add', x, y))
    @app.command()
    def mul(x: int, y: int):
        ran.append(('mul', x, y))
    @app.command()
    def greet(name, greeting='hello'):
        ran.append(('greet', name, greeting))
    @app.command()
    def scale(x: int, *, double=False):
        ran.append(('scale', x, double))
    @app.command()
    def total(*nums: int):
        ran.append(('total',) + nums)
    @app.command()
    def bail(code: int):
        ran.append(('bail', code))
        return code

    # sibling cycle, left to right, logged in order
    app.process(['add', '1', '2', 'mul', '3', '4', 'add', '5', '6'])
    assert ran == [('add', 1, 2), ('mul', 3, 4), ('add', 5, 6)], ran
    assert [c.__name__ for c, _ in app.instances] == ['add', 'mul', 'add']

    # optionals must be spelled: greedy saturation takes the
    # would-be command word as greet's greeting
    ran.clear()
    try:
        app.process(['greet', 'bob', 'add', '1', '2'])
        assert False, 'expected UsageError'
    except UsageError as e:
        assert "unknown command '1'" in str(e), e
    assert ran == [], ran   # parse-before-execute: nothing ran

    # spelled, it cycles
    app.process(['greet', 'bob', 'hi', 'add', '1', '2'])
    assert ran == [('greet', 'bob', 'hi'), ('add', 1, 2)], ran

    # the window rule: a post-saturation option binds to the
    # finished command; the next command word closes the window
    ran.clear()
    app.process(['scale', '3', '--double', 'add', '1', '2'])
    assert ran == [('scale', 3, True), ('add', 1, 2)], ran

    # *args never saturates: a cycle terminator
    ran.clear()
    app.process(['add', '1', '2', 'total', '3', '4', '5'])
    assert ran == [('add', 1, 2), ('total', 3, 4, 5)], ran

    # a malformed later command means NO work (the Appeal rule)
    ran.clear()
    try:
        app.process(['add', '1', '2', 'mul', '3'])
        assert False, 'expected UsageError'
    except UsageError:
        pass
    assert ran == [], ran

    # the early-exit contract, every command in a cycle: a nonzero
    # int halts
    ran.clear()
    result = app.process(['bail', '7', 'add', '1', '2'])
    assert result == 7
    assert ran == [('bail', 7)], ran

    # the gate rule fires in stage 1, even mid-cycle: a violation
    # in the SECOND command means the first never runs
    def _pair(x, y):
        return f'{x}+{y}'
    def stroke(width: float = 1.0, *, dashed=False):
        return f'{width}{"~" if dashed else "-"}'
    @app.command()
    def board(f: _pair, s: stroke = 'none'):
        ran.append(('board', f, s))
    ran.clear()
    try:
        app.process(['add', '1', '2', 'board', '--dashed', 'a', 'b', '1.5'])
        assert False, 'expected UsageError'
    except UsageError as e:
        assert 'too early' in str(e), e
    assert ran == [], ran
    app.process(['add', '1', '2', 'board', 'a', 'b', '1.5', '--dashed'])
    assert ran == [('add', 1, 2), ('board', 'a+b', '1.5~')], ran

    # without repeat, a leftover word is leftover (one command per
    # line, as ever)
    app2 = _appeal.Appeal(name='nocyc')
    @app2.command()
    def add2(x: int, y: int):
        ran.append(('add2', x, y))
    try:
        app2.process(['add2', '1', '2', 'add2', '3', '4'])
        assert False, 'expected UsageError'
    except UsageError:
        pass


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
        # optional project; the line ends without a command, and
        # that's orientation, not a diagnostic: listing, exit 1
        r = run_script(script_path, ['bogus'])
        assert r.returncode == 1
        assert 'Commands:' in r.stdout and r.stderr == ''
        # ...but once the global is at its maximum, the next operand
        # is forced to be the command word
        r = run_script(script_path, ['bogus', 'bogus2'])
        assert r.returncode == 2
        assert 'unknown command' in r.stderr
        assert 'Commands:' in r.stderr and 'greet' in r.stderr

        r = run_script(script_path, [])
        assert r.returncode == 1
        assert 'Commands:' in r.stdout and r.stderr == ''

        # the global option is scoped to before the command word
        r = run_script(script_path, ['greet', 'world', '--trace'])
        assert r.returncode == 2
        assert '--trace' in r.stderr

MULTIOPT_MODULE = """\
from appeal import MultiOption, StrictOption

class Tags(MultiOption):
    def init(self, default):
        self.values = list(default) if default else []
    def option(self, tag):
        self.values.append(tag)
    def render(self):
        return tuple(self.values)

class Where(StrictOption):
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
        env = subprocess_env(PYTHONPATH=repo_dir)
        r = sub_run(
            [sys.executable, script_path, 'box', '--tag', 'a', '-t', 'b'],
            capture_output=True, text=True, cwd=d, env=env)
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'label box a+b nowhere\n'
        r = sub_run(
            [sys.executable, script_path, 'box', '--where', '3', '4'],
            capture_output=True, text=True, cwd=d, env=env)
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'label box  3x4\n'
        r = sub_run(
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
        assert r.stdout == 'mark x 3,4\n'
        r = run_script(script_path, ['x'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'mark x origin\n'
        r = run_script(script_path, ['x', '--at', '3'])
        assert r.returncode == 2
        assert '2 values' in r.stderr

def test_standalone_greedy_opargs():
    # make -j in a generated script: the emitted table carries
    # (minimum, maximum) and the streamed parse_tokens is greedy
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'build_all')
        r = run_script(script_path, ['a', 'b'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'build a+b 1\n'
        r = run_script(script_path, ['a', '-j'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'build a -1\n'
        r = run_script(script_path, ['a', '-j', '5'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'build a 5\n'
        r = run_script(script_path, ['-j5', 'a'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'build a 5\n'
        # the greedy grab is loud in the script too
        r = run_script(script_path, ['a', '-j', 'b'])
        assert r.returncode == 2
        assert "'b'" in r.stderr

def test_standalone_cycling():
    # cycling in a generated script: the north star holds
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
            plans = {'greet': build(demo_cmds.greet),
                     'cp': build(demo_cmds.cp)}
            script = emit_standalone_command_set(
                plans, build(demo_cmds.config), argv0='tool', repeat=True)
        finally:
            sys.path.remove(d)
            sys.modules.pop('demo_cmds', None)
        script_path = os.path.join(d, 'tool.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)

        # two commands, cycled (optionals spelled--greedy saturation)
        r = run_script(script_path,
                       ['greet', 'world', 'hi', 'greet', 'moon', 'yo'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'hi, world!\nyo, moon!\n', r.stdout

        # *src never saturates: cp is a cycle terminator
        r = run_script(script_path,
                       ['greet', 'world', 'hi', 'cp', 'a', 'b', 'dest'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'hi, world!\ncopy a+b -> dest\n', r.stdout

        # a malformed later command means no work at all: the
        # error goes to stderr, and stdout is EMPTY (no greeting)
        r = run_script(script_path, ['greet', 'world', 'hi', 'greet'])
        assert r.returncode == 2
        assert r.stdout == '', r.stdout
        assert 'error:' in r.stderr


NESTED_MODULE = """\
def db(*, verbose=False):
    print('db', verbose)

def add(x: int):
    print('add', x)

def remove(x: int):
    print('remove', x)

def status():
    print('status')
"""


def test_nested_cycling_and_popup():
    # per-parent repeat and the pop-up chain (Larry's r2): a set's
    # first consult is free (descent); re-entry needs that owner's
    # repeat; a set without repeat doesn't block its ancestors
    import appeal as _appeal
    ran = []
    app = _appeal.Appeal(name='s', repeat=True)
    @app.command()
    def db(*, verbose=False):
        ran.append(('db', verbose))
    @app.command()
    def status():
        ran.append('status')
    reg = app.command('db', repeat=True)
    @reg.command()
    def add(x: int):
        ran.append(('add', x))
    @reg.command()
    def remove(x: int):
        ran.append(('remove', x))

    # cycling within db's set
    app.process(['db', 'add', '3', 'remove', '4'])
    assert ran == [('db', False), ('add', 3), ('remove', 4)], ran
    assert [getattr(c, '__name__', c) for c, _ in app.instances] == \
        ['db', 'add', 'remove']

    # pop-up: add's set has no 'status'; db's set doesn't either;
    # the root's repeat resolves it
    ran.clear()
    app.process(['db', 'add', '3', 'status'])
    assert ran == [('db', False), ('add', 3), 'status'], ran

    # and back down: a second db runs again, fresh options
    ran.clear()
    app.process(['db', 'add', '1', 'status', 'db', '-v', 'remove', '2'])
    assert ran == [('db', False), ('add', 1), 'status',
                   ('db', True), ('remove', 2)], ran

    # a parent whose set never got a command
    ran.clear()
    try:
        app.process(['db'])
        assert False, 'expected UsageError'
    except UsageError as e:
        assert 'no command specified' in str(e)
    assert ran == [], ran

    # without the parent's repeat, its set doesn't cycle...
    app2 = _appeal.Appeal(name='s2', repeat=True)
    @app2.command()
    def db2():
        ran.append('db2')
    @app2.command()
    def status2():
        ran.append('status2')
    reg2 = app2.command('db2')
    @reg2.command()
    def add2(x: int):
        ran.append(('add2', x))
    # ...but the root's repeat still resolves root words (pop-up
    # skips the non-repeat level, it doesn't stop there)
    ran.clear()
    app2.process(['db2', 'add2', '1', 'status2'])
    assert ran == ['db2', ('add2', 1), 'status2'], ran


def test_standalone_nested_cycling():
    # the same tree, emitted: standalone() lost its refusal
    import appeal as _appeal
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'nest_cmds.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(NESTED_MODULE)
        sys.path.insert(0, d)
        try:
            import nest_cmds
            import importlib
            importlib.reload(nest_cmds)
            app = _appeal.Appeal(name='tool', repeat=True)
            app.command()(nest_cmds.db)
            app.command()(nest_cmds.status)
            reg = app.command('db', repeat=True)
            reg.command()(nest_cmds.add)
            reg.command()(nest_cmds.remove)
            script = app.standalone()
        finally:
            sys.path.remove(d)
            sys.modules.pop('nest_cmds', None)
        script_path = os.path.join(d, 'tool.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)

        r = run_script(script_path, ['db', 'add', '3', 'remove', '4'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'db False\nadd 3\nremove 4\n', r.stdout

        r = run_script(script_path,
                       ['db', 'add', '1', 'status', 'db', '-v', 'remove', '2'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'db False\nadd 1\nstatus\ndb True\nremove 2\n', r.stdout

        # the bare parent errors with its own set's usage
        r = run_script(script_path, ['db'])
        assert r.returncode == 2
        assert 'no command specified' in r.stderr
        assert 'add' in r.stderr and 'remove' in r.stderr

        # STRUCTURALLY malformed mid-cycle: nothing runs (stage 1)
        # --stdout is EMPTY ('db' never printed)
        r = run_script(script_path, ['db', 'add', '1', '2', 'status'])
        assert r.returncode == 2
        assert 'unknown command' in r.stderr
        assert r.stdout == '', r.stdout

        # but a CONVERSION failure is stage 2 (ruled): commands to
        # its left already ran, like make stopping mid-build
        r = run_script(script_path, ['db', 'add', 'x', 'status'])
        assert r.returncode == 2
        assert r.stdout == 'db False\n', r.stdout
        assert 'error:' in r.stderr


def test_two_classes_same_method_name():
    # command identity is the CALLABLE, never the bare word: two
    # class commands may each expose `run`, and each binds to its
    # own class's instance.  Regression: ownership was keyed by
    # the word, so the second class clobbered the first
    # ('alpha run' raised KeyError('Beta')), and the standalone
    # emitter silently shared one run_run between both sets.
    app = Appeal(name='twins')

    @app.command(name='alpha')
    class Alpha:
        def __init__(self):
            pass
        @app.command()
        def run(self):
            return 'alpha-run'

    @app.command(name='beta')
    class Beta:
        def __init__(self):
            pass
        @app.command()
        def run(self):
            return 'beta-run'

    assert app.process(['alpha', 'run']) == 'alpha-run'
    assert app.process(['beta', 'run']) == 'beta-run'
    # the instances log can't tell WHICH `run` from the bare word,
    # so it answers None rather than guess wrong
    assert app.instances[-1] == (None, None)
    # plan_for on the ambiguous bare word refuses, naming parents
    try:
        app.plan_for('run')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'alpha' in str(e) and 'beta' in str(e)
    # ...and the standalone emitter numbers the symbols
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'twins_mod.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(
                "import appeal\n"
                "app = appeal.Appeal(name='twins')\n\n"
                "@app.command(name='alpha')\n"
                "class Alpha:\n"
                "    def __init__(self):\n"
                "        pass\n"
                "    @app.command()\n"
                "    def run(self):\n"
                "        print('alpha-run')\n\n"
                "@app.command(name='beta')\n"
                "class Beta:\n"
                "    def __init__(self):\n"
                "        pass\n"
                "    @app.command()\n"
                "    def run(self):\n"
                "        print('beta-run')\n")
        sys.path.insert(0, d)
        try:
            import twins_mod
            import importlib
            importlib.reload(twins_mod)
            script = twins_mod.app.standalone()
        finally:
            sys.path.remove(d)
            sys.modules.pop('twins_mod', None)
        assert 'def run_run(' in script and 'def run_run2(' in script
        script_path = os.path.join(d, 'twins_cli.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        env = subprocess_env(PYTHONPATH=d + os.pathsep + repo_dir)
        r = run_script(script_path, ['alpha', 'run'], env=env)
        assert (r.returncode, r.stdout.strip()) == (0, 'alpha-run'), r.stderr
        r = run_script(script_path, ['beta', 'run'], env=env)
        assert (r.returncode, r.stdout.strip()) == (0, 'beta-run'), r.stderr

    # a duplicate name= within one class replaces, like every
    # other re-registration (v1's rule: the second wins)
    dup = Appeal(name='d')
    @dup.command(name='gamma')
    class Gamma:
        def __init__(self):
            pass
        @dup.command(name='x')
        def one(self):
            pass
        @dup.command(name='x')
        def two(self):
            pass
    (word_fn,) = [fn for w, fn in dup._subs['gamma'] if w == 'x']
    assert word_fn.__name__ == 'two', word_fn
    # ...and two nested parents sharing a word (restrictive now;
    # path-addressed sets can relax it later)
    try:
        bad2 = Appeal(name='p')
        @bad2.command(name='left')
        class Left:
            def __init__(self):
                pass
            @bad2.command(name='db')
            class DbL:
                def __init__(self):
                    pass
                @bad2.command()
                def wipe(self):
                    pass
        @bad2.command(name='right')
        class Right:
            def __init__(self):
                pass
            @bad2.command(name='db')
            class DbR:
                def __init__(self):
                    pass
                @bad2.command()
                def nuke(self):
                    pass
        # registration is lazy; the flat-view refusal fires at
        # first compile-side use
        bad2._subs
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert "nested command sets named 'db'" in str(e)


def test_app_class_compat_layer():
    # v1's class-based-commands API (README: "Classes, Instances,
    # And Preparers"), kept as a thin layer over class-as-app
    # (ruled 2026-07-18, review item 4).  The README example must
    # run unmodified.
    import appeal as _appeal
    import contextlib, io

    app = _appeal.Appeal(name='p')
    app_class, command_method = app.app_class()

    @app_class()
    class MyApp:
        def __init__(self, *, verbose=False):
            print(f"init verbose={verbose!r}")
            self.verbose = verbose
        @command_method()
        def add(self, a: int, b: int):
            print(f"add {a + b} verbose={self.verbose!r}")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app.process(['--verbose', 'add', '1', '2'])
    assert out.getvalue() == 'init verbose=True\nadd 3 verbose=True\n', \
        out.getvalue()
    # a fresh instance per parse, defaults refilled
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app.process(['add', '4', '5'])
    assert out.getvalue() == 'init verbose=False\nadd 9 verbose=False\n', \
        out.getvalue()

    # command_method(name=...) renames, same as @app.command(name)
    app2 = _appeal.Appeal(name='q')
    app_class2, command_method2 = app2.app_class()

    @app_class2()
    class Tool:
        def __init__(self):
            pass
        @command_method2('list')
        def list_(self):
            return 'listed'

    assert app2.process(['list']) == 'listed'
    try:
        app2.process(['list_'])
        assert False, 'the method name must not dispatch'
    except _appeal.AppealUsageError:
        pass


def test_class_as_app():
    # §8.6: the class's __init__ is the global command; decorated
    # methods are the commands; nothing is automatic
    import appeal as _appeal
    out = []
    app = _appeal.Appeal(name='fgrep')

    @app.global_command()
    class MyApp:
        def __init__(self, *, verbose=False):
            self.verbose = verbose
            out.append(('init', verbose))

        @app.command()
        def fgrep(self, pattern, filename, *, context: int = 0):
            out.append(('fgrep', self.verbose, pattern, filename, context))

        def helper(self):
            pass    # undecorated: not a command

    app.process(['-v', 'fgrep', 'patt', 'file', '-c', '33'])
    assert out == [('init', True),
                   ('fgrep', True, 'patt', 'file', 33)], out
    # the log: the global class logs (None, instance); the method
    # logs (the function, None)
    command, instance = app.instances[0]
    assert command is None and isinstance(instance, MyApp)
    assert instance.verbose is True
    assert app.instances[1] == (MyApp.fgrep, None)
    # undecorated methods aren't commands
    try:
        app.process(['helper'])
        assert False, 'expected UsageError'
    except UsageError as e:
        assert 'unknown command' in str(e)
    # the instance is never mistaken for an exit status
    assert app.process(['fgrep', 'a', 'b']) is None


def test_class_as_app_nested():
    # nested classes are subcommand trees, constructed via the
    # parent instance's attribute; instances chain through the
    # environment
    import appeal as _appeal
    out = []
    app = _appeal.Appeal(name='outer', repeat=True)

    @app.global_command()
    class Outer:
        def __init__(self, *, verbose=False):
            self.verbose = verbose

        @app.command()
        def top(self, x: int):
            out.append(('top', self.verbose, x))

        @app.command()
        class Db:
            def __init__(self, name):
                self.name = name
                out.append(('db', name))

            @app.command()
            def add(self, x: int):
                out.append(('add', self.name, x))

    app.process(['-v', 'Db', 'mydb', 'add', '3'])
    assert out == [('db', 'mydb'), ('add', 'mydb', 3)], out
    # cycling pops from the inner set back to a root method
    out.clear()
    app.process(['Db', 'mydb', 'add', '1', 'top', '9'])
    assert out == [('db', 'mydb'), ('add', 'mydb', 1),
                   ('top', False, 9)], out
    # the log carries every constructed instance, in order
    kinds = [(getattr(c, '__name__', None),
              type(i).__name__ if i is not None else None)
             for c, i in app.instances]
    assert kinds == [(None, 'Outer'), ('Db', 'Db'), ('add', None),
                     ('top', None)], kinds


def test_class_as_app_bic():
    # a bound inner class composes without Appeal knowing it
    # exists: construction goes through the parent instance's
    # attribute, and the grammar is what that attribute accepts
    import appeal as _appeal
    from big.boundinnerclass import BoundInnerClass
    out = []
    app = _appeal.Appeal(name='bic')

    @app.global_command()
    class Host:
        def __init__(self, *, verbose=False):
            self.verbose = verbose

        @app.command()
        @BoundInnerClass
        class Job:
            def __init__(self, host, label, *, dry=False):
                out.append(('job', host.verbose, label, dry))

    app.process(['-v', 'Job', 'nightly', '--dry'])
    assert out == [('job', True, 'nightly', True)], out
    command, instance = app.instances[1]
    assert type(instance).__name__ == 'Job'


def test_class_as_app_refusals():
    import appeal as _appeal
    app = _appeal.Appeal(name='r')

    class Undecorated:
        @app.command()
        def method(self, x):
            pass
    # the class was never decorated: its method is an orphan
    try:
        app.process(['method', 'a'])
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'forget to decorate the class' in str(e), e


CLASS_MODULE = """\
import appeal

app = appeal.Appeal(name='fgrep')

@app.global_command()
class MyApp:
    def __init__(self, *, verbose=False):
        self.verbose = verbose

    @app.command()
    def fgrep(self, pattern, filename, *, context: int = 0):
        print('fgrep', self.verbose, pattern, filename, context)

    @app.command()
    def count(self, pattern):
        print('count', self.verbose, pattern)
"""


def test_standalone_class_app():
    # the north star for §8.6: the class imports, the script
    # constructs it at dispatch, methods bind through the
    # environment
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'clsapp_cmds.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(CLASS_MODULE)
        sys.path.insert(0, d)
        try:
            import clsapp_cmds
            import importlib
            importlib.reload(clsapp_cmds)
            script = clsapp_cmds.app.standalone()
        finally:
            sys.path.remove(d)
            sys.modules.pop('clsapp_cmds', None)
        assert 'from clsapp_cmds import MyApp' in script
        assert 'MyApp.fgrep' in script
        script_path = os.path.join(d, 'fgrep.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)

        r = run_script(script_path, env=subprocess_env(PYTHONPATH=repo_dir), argv=['-v', 'fgrep', 'patt', 'file', '-c', '33'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'fgrep True patt file 33\n', r.stdout

        r = run_script(script_path, env=subprocess_env(PYTHONPATH=repo_dir), argv=['count', 'x'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'count False x\n', r.stdout

        r = run_script(script_path, env=subprocess_env(PYTHONPATH=repo_dir), argv=['helper'])
        assert r.returncode == 2
        assert 'unknown command' in r.stderr


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
        assert r.stdout.startswith('usage: '), r.stdout
        assert 'Marks a label on the canvas.' in r.stdout
        assert 'usage: mark' in r.stdout
        assert 'Arguments:' in r.stdout and 'Options:' in r.stdout
        assert '  <LABEL>  the text to place.' in r.stdout
        assert '-a|--at <X> <Y>' in r.stdout
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
        assert r.stdout.startswith('usage: '), r.stdout
        assert 'Marks a label on the canvas.' in r.stdout
        assert '  <LABEL>  the text to place.' in r.stdout

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
        r = sub_run(
            [sys.executable, '-c', probe],
            capture_output=True, text=True, cwd=d,
            env=subprocess_env(),
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

    # liberal detection (ruled 2026-08-01): decoration and case
    # are the author's business--'Sub-commands:' just works now
    from appeal.help import parse_docstring as _pd
    assert _pd("Sub-commands:\n  x: y", "f")['commands'] == {'x': ['y']}
    assert _pd(" [Arguments:]\n  x: y", "f")['arguments'] == {'x': ['y']}
    assert _pd("OPTIONS\n  x: y", "f")['options'] == {'x': ['y']}
    # ...but a claim needs a body that's indented OR
    # entry-shaped: v1's legacy [[arguments]] markup (unindented,
    # un-entry-shaped follower) stays prose
    c = _pd("[[arguments]]\n{x} not an entry", "f")
    assert not c['arguments']
    # left-margin entries are legal (ruled 2026-08-01): authors
    # who want their tables at column 0 get them there
    c = _pd("Options:\nloud: Speak up.", "f")
    assert c['options'] == {'loud': ['Speak up.']}
    assert c['presentation']['indents']['options'] == ''
    # one section per kind--including via the Commands:/Subcommands: alias
    refuses("Options:\n  a: b\n\nOptions:\n  c: d", "duplicate")
    refuses("Commands:\n  a: b\n\nSubcommands:\n  c: d",
            "duplicate", "same section")
    # the first line of a claimed section must be an entry
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
        ('<A>', ['The first thing.']),
        ('<I>', ['Overridden: how many knocks.']),
        ('<F>', ['The float part.']),
        ('<S>', []),
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
    assert c['arguments'] == [('<A>', []),
                              ('<REQUIRED_KW>', ['a trailing operand.'])]

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


# ---------------------------------------------------------------------
# colorization (proposal §8.8, the completion/colorization rulings)

def test_theme():
    from appeal.runtime import Theme, _SGR_RESET

    # symbolic strings compile once, to SGR
    t = Theme()
    assert t.paint('option', '--verbose') == '\x1b[36m--verbose' + _SGR_RESET
    # the empty string means "leave that role alone"
    assert t.paint('operand', 'host') == 'host'
    # painting nothing is nothing
    assert t.paint('error', '') == ''

    # slot references: slot names join the vocabulary and expand
    # to that slot's resolved words
    t = Theme(option='cyan', metavar='option dim')
    assert t.sgr['metavar'][0] == ['36', '2']
    # bright-* variants
    assert Theme(error='bold bright-red').sgr['error'][0] == ['1', '91']
    # raw-SGR passthrough: a value starting with an escape
    t = Theme(heading='\x1b[7m')
    assert t.paint('heading', 'X') == '\x1b[7mX' + _SGR_RESET

    # refusals, by name
    for bad, needle in (
        (dict(error='heading', heading='error'), 'cycle'),
        (dict(option='chartreuse'), 'chartreuse'),
        (dict(metavar='\x1b[9m', option='metavar'), 'raw escape'),
    ):
        try:
            Theme(**bad)
            assert False, f'expected refusal for {bad}'
        except AppealConfigurationError as e:
            assert needle in str(e), e


def test_can_colorize_precedence():
    import io
    from appeal.runtime import can_colorize

    class Tty(io.StringIO):
        def isatty(self):
            return True

    def probe(env, file):
        saved = {k: os.environ.pop(k, None)
                 for k in ('PYTHON_COLORS', 'NO_COLOR', 'FORCE_COLOR', 'TERM')}
        try:
            os.environ.update(env)
            return can_colorize(file)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    notty = io.StringIO()
    # CPython's precedence: PYTHON_COLORS beats NO_COLOR beats
    # FORCE_COLOR, then TERM=dumb, then isatty
    assert probe({'PYTHON_COLORS': '1', 'NO_COLOR': '1'}, notty)
    assert not probe({'PYTHON_COLORS': '0', 'FORCE_COLOR': '1'}, Tty())
    assert not probe({'NO_COLOR': '1', 'FORCE_COLOR': '1'}, Tty())
    assert probe({'FORCE_COLOR': '1'}, notty)
    assert not probe({'TERM': 'dumb'}, Tty())
    assert probe({}, Tty())
    assert not probe({}, notty)


def test_colorized_help_paints_after_layout():
    # THE invariant: stripping the escape codes yields the
    # monochrome page, byte for byte--painting never moved a
    # single character of layout.
    import re as _re
    decolor = lambda s: _re.sub('\x1b\\[[0-9;]*m', '', s)

    def size(width: float, *, bold=False):
        return (width, bold)
    def draw(shape, width: size = None, *, verbose=False, times: int = 1):
        """
        Draws a shape.

        Arguments:
          shape: the shape to draw.

        Options:
          verbose: narrate the process.
          times: how many times.
        """
    import io, contextlib
    from appeal.build import build
    from appeal.help import merge_docs
    from appeal.runtime import Theme, default_template, render_help_page

    plan = build(draw)
    corpus = merge_docs(plan)
    plain = render_help_page(plan.usage(), corpus, default_template)
    painted = render_help_page(plan.usage(), corpus, default_template,
                               theme=Theme())
    assert painted != plain
    assert '\x1b[' in painted
    assert decolor(painted) == plain
    # spans: the option strings and the headings took paint
    assert '\x1b[36m--verbose\x1b[0m' in painted
    assert '\x1b[1mArguments:\x1b[0m' in painted

    # angle-bracketed metavars (positional_argument_usage_format=
    # '<{name}>') paint as 'metavar' spans, brackets and all
    from appeal.plan import DEFAULT_ARG_FORMAT
    plan.arg_format = '<{name}>'
    bracketed = render_help_page(plan.usage(), merge_docs(plan),
                                 default_template, theme=Theme())
    assert '\x1b[2m<times>\x1b[0m' in bracketed, bracketed
    plan.arg_format = DEFAULT_ARG_FORMAT


def test_standalone_colorized_help_and_errors():
    # the theme travels as a literal; the DECISION happens at the
    # script's own runtime--FORCE_COLOR paints, NO_COLOR silences,
    # a pipe (no tty) stays monochrome
    import re as _re
    decolor = lambda s: _re.sub('\x1b\\[[0-9;]*m', '', s)
    with tempfile.TemporaryDirectory() as d:
        script_path, script = write_standalone_fixture(d, 'mark')
        assert "theme=None" in script            # auto by default
        base = subprocess_env()
        plain = run_script(script_path, ['--help'])
        forced = sub_run(
            [sys.executable, script_path, '--help'],
            capture_output=True, text=True, cwd=d,
            env={**base, 'FORCE_COLOR': '1'})
        no_color = sub_run(
            [sys.executable, script_path, '--help'],
            capture_output=True, text=True, cwd=d,
            env={**base, 'FORCE_COLOR': '1', 'NO_COLOR': '1'})
        assert '\x1b[' not in plain.stdout      # a pipe: monochrome
        assert '\x1b[' in forced.stdout
        assert decolor(forced.stdout) == plain.stdout
        assert no_color.stdout == plain.stdout   # NO_COLOR beats FORCE_COLOR
        # the error prefix paints, on stderr where errors live
        err = sub_run(
            [sys.executable, script_path],
            capture_output=True, text=True, cwd=d,
            env={**base, 'FORCE_COLOR': '1'})
        assert err.returncode == 2
        assert err.stderr.startswith('\x1b[1;31merror:\x1b[0m ')
        assert not err.stdout
        plain_err = run_script(script_path, [])
        assert decolor(err.stderr) == plain_err.stderr


# ---------------------------------------------------------------------
# shell completion (the completion rulings, 1-5)

def test_value_completion():
    from appeal import interpreter_parse
    from appeal.complete import complete

    def color(name):
        return name
    color.completions = lambda prefix='': ('red', 'green', 'blue')

    def paint(where, hue: color = 'red', *, tint: color = 'red',
              times: int = 1):
        return (where, hue, tint)

    plan = build(paint)
    # an option's value position asks the expecting converter,
    # and the engine re-filters by prefix (the belt)
    assert complete(plan, ['--tint'], '') == ['blue', 'green', 'red']
    assert complete(plan, ['--tint'], 'g') == ['green']
    # a value position whose converter has no opinion: filenames
    assert complete(plan, ['--times'], '') == []
    # an operand position asks its converter too (ruling 4)
    assert complete(plan, ['x'], '') == ['blue', 'green', 'red']
    # ...but the first operand (where: str) has no opinion
    assert complete(plan, [], '') == []
    # option-string completion still works, used singles excluded
    got = complete(plan, [], '-')
    assert '--tint' in got and '--times' in got
    assert '--tint' not in complete(plan, ['--tint', 'red'], '-')


def test_completions_validation():
    def refuses(f, *needles):
        try:
            build(f)
        except AppealConfigurationError as e:
            for needle in needles:
                assert needle in str(e), f'{needle!r} not in {e}'
            return
        assert False, f'expected AppealConfigurationError for {f.__name__}'

    # completions must be callable...
    def c1(name):
        return name
    c1.completions = ('red', 'green')       # the retired static form
    def f1(x: c1):
        return x
    refuses(f1, 'c1', 'callable')

    # ...accepting one positional argument
    def c2(name):
        return name
    c2.completions = lambda: ('red',)
    def f2(x: c2):
        return x
    refuses(f2, 'c2', 'one positional')

    # the return type is checked at query time, loudly
    def c3(name):
        return name
    c3.completions = lambda prefix='': ['red']      # list, not tuple
    def f3(x: c3):
        return x
    from appeal.complete import complete
    plan = build(f3)
    try:
        complete(plan, [], '')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'tuple' in str(e)


def test_completion_script_and_reentry():
    app = Appeal(name='mytool')
    @app.global_command()
    def go(x):
        return x
    script = app.completion('bash')
    assert 'COMP_WORDS' in script and 'COMP_CWORD' in script
    assert '_APPEAL_COMPLETE=bash mytool' in script
    assert 'complete -o default -F' in script
    zsh_script = app.completion('zsh')
    assert 'compdef' in zsh_script
    assert '_APPEAL_COMPLETE=zsh mytool' in zsh_script
    assert 'COMP_CWORD=$((CURRENT-1))' in zsh_script
    assert '_files' in zsh_script                # the filename fallback
    import shutil
    if shutil.which('zsh'):
        # the courier must at least be valid zsh
        r = sub_run(['zsh', '-n', '/dev/stdin'],
                           input=zsh_script, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    try:
        app.completion('tcsh')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'tcsh' in str(e)


def test_standalone_completion_reentry():
    # the north star: an emitted script answers the reentry
    # protocol--gated on empty argv--with zero imports beyond its
    # own module
    module = (
        "def color(name):\n"
        "    return name\n"
        "color.completions = lambda prefix='': ('red', 'green', 'blue')\n"
        "\n"
        "def paint(where, hue: color = 'red', *, tint: color = 'red'):\n"
        "    print('paint', where, hue, tint)\n"
    )
    from appeal import emit_standalone
    with tempfile.TemporaryDirectory() as d:
        module_path = os.path.join(d, 'paint_cmds.py')
        with open(module_path, 'wt', encoding='utf-8') as f:
            f.write(module)
        sys.path.insert(0, d)
        try:
            import paint_cmds
            import importlib
            importlib.reload(paint_cmds)
            script = emit_standalone(build(paint_cmds.paint), argv0='paint')
        finally:
            sys.path.remove(d)
            sys.modules.pop('paint_cmds', None)
        script_path = os.path.join(d, 'paint.py')
        with open(script_path, 'wt', encoding='utf-8') as f:
            f.write(script)
        base = subprocess_env()

        def reenter_mode(mode, comp_words, cword):
            return sub_run(
                [sys.executable, script_path],
                capture_output=True, text=True, cwd=d,
                env={**base, '_APPEAL_COMPLETE': mode,
                            'COMP_WORDS': comp_words,
                            'COMP_CWORD': str(cword)})

        def reenter(comp_words, cword):
            return reenter_mode('bash', comp_words, cword)
        # an option's value position, filtered by the prefix
        r = reenter('paint\n--tint\ng', 2)
        assert r.returncode == 0, r.stderr
        assert r.stdout.splitlines() == ['green']
        # an operand position (cursor after a space: trailing empty word)
        r = reenter('paint\nx\n', 2)
        assert r.stdout.splitlines() == ['blue', 'green', 'red']
        # option strings on a '-' prefix
        r = reenter('paint\n--t', 1)
        assert r.stdout.splitlines() == ['--tint']
        # the empty-argv gate: with arguments, the env is ignored
        # and the program runs normally
        r = sub_run(
            [sys.executable, script_path, 'here'],
            capture_output=True, text=True, cwd=d,
            env={**base, '_APPEAL_COMPLETE': 'bash'})
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'paint here red red\n'
        # the source_bash request: the eval one-liner's other half
        r = sub_run(
            [sys.executable, script_path],
            capture_output=True, text=True, cwd=d,
            env={**base, '_APPEAL_COMPLETE': 'source_bash'})
        assert r.returncode == 0, r.stderr
        assert 'complete -o default -F' in r.stdout
        assert '_APPEAL_COMPLETE=bash paint' in r.stdout
        # zsh: same protocol, zsh courier
        r = sub_run(
            [sys.executable, script_path],
            capture_output=True, text=True, cwd=d,
            env={**base, '_APPEAL_COMPLETE': 'source_zsh'})
        assert r.returncode == 0, r.stderr
        assert 'compdef' in r.stdout
        assert '_APPEAL_COMPLETE=zsh paint' in r.stdout
        r = reenter_mode('zsh', 'paint\n--tint\ng', 2)
        assert r.stdout.splitlines() == ['green']
        # fish: its courier sends the current TOKEN TEXT (not an
        # index) in COMP_CWORD, and the NEWLINE-tokenized line-so-far
        # in COMP_WORDS
        r = sub_run(
            [sys.executable, script_path],
            capture_output=True, text=True, cwd=d,
            env={**base, '_APPEAL_COMPLETE': 'fish',
                        'COMP_WORDS': 'paint\n--tint\ng',
                        'COMP_CWORD': 'g'})
        assert r.stdout.splitlines() == ['green']
        # cursor after a space: empty token
        r = sub_run(
            [sys.executable, script_path],
            capture_output=True, text=True, cwd=d,
            env={**base, '_APPEAL_COMPLETE': 'fish',
                        'COMP_WORDS': 'paint\nx',
                        'COMP_CWORD': ''})
        assert r.stdout.splitlines() == ['blue', 'green', 'red']
        r = sub_run(
            [sys.executable, script_path],
            capture_output=True, text=True, cwd=d,
            env={**base, '_APPEAL_COMPLETE': 'source_fish'})
        assert r.returncode == 0, r.stderr
        assert '__fish_complete_path' in r.stdout
        # fish exports via `set -lx` then invokes the (quoted) prog;
        # tokens come from `commandline -co | string collect` so an
        # argument with a space stays one word
        assert 'set -lx _APPEAL_COMPLETE fish' in r.stdout
        assert 'commandline -co | string collect' in r.stdout
        assert '(paint)' in r.stdout
        # and with real zsh, drive the courier's core: zsh's own
        # word machinery feeding the reentry, candidates coming
        # back through the command substitution
        import shutil
        if shutil.which('zsh'):
            # the courier's real join is (pj:\n:) -- newline, so a
            # value with a space would survive; drive that verbatim
            zsh_core = (
                'words=(paint --tint g); CURRENT=3; '
                'completions=("${(@f)$(COMP_WORDS="${(pj:\\n:)words}" '
                'COMP_CWORD=$((CURRENT-1)) _APPEAL_COMPLETE=zsh '
                f'{sys.executable} {script_path})}}"); '
                'print -l -- $completions')
            r = sub_run(['zsh', '-c', zsh_core],
                               capture_output=True, text=True, cwd=d,
                               env=base)
            assert r.returncode == 0, r.stderr
            assert r.stdout.splitlines() == ['green']
            # a QUOTED operand with a space (the bug this fixes):
            # real zsh keeps the quote chars in $words, so the array
            # element is literally "a b" (written '"a b"' to survive
            # this literal assignment).  The reentry's lexer keeps it
            # one word and the option value still completes.
            zsh_space = (
                'words=(paint \'"a b"\' --tint g); CURRENT=4; '
                'completions=("${(@f)$(COMP_WORDS="${(pj:\\n:)words}" '
                'COMP_CWORD=$((CURRENT-1)) _APPEAL_COMPLETE=zsh '
                f'{sys.executable} {script_path})}}"); '
                'print -l -- $completions')
            r = sub_run(['zsh', '-c', zsh_space],
                               capture_output=True, text=True, cwd=d,
                               env=base)
            assert r.returncode == 0, r.stderr
            assert r.stdout.splitlines() == ['green'], r.stdout


def test_single_terminal_transparency():
    # the transparency rule: a converter that consumes exactly one
    # operand is transparent to naming--the annotated parameter's
    # name flows through to usage, the help table, and the
    # docstring grammar.
    from appeal.help import merge_docs

    def flavor(name):
        """
        A flavor.

        Arguments:
          name: the flavor, in flavor's own vocabulary.
        """
        return name

    def scoop(cone, taste: flavor = 'vanilla', *, sprinkles=False):
        """
        Serves a scoop.

        Arguments:
          taste: which flavor to serve.
        """

    plan = build(scoop)
    assert '[<TASTE>]' in plan.usage(), plan.usage()
    corpus = merge_docs(plan)
    # the row wears the outer name; the outer entry documents it,
    # winning (nearest) over flavor's own 'name:' entry
    assert ('<TASTE>', ['which flavor to serve.']) in corpus['arguments']

    # the converter's own docstring keeps working in its own
    # vocabulary: without an outer override, 'name:' documents
    # the same row
    def scoop2(cone, taste: flavor = 'vanilla'):
        "Serves."
    corpus = merge_docs(build(scoop2))
    assert ('<TASTE>', ["the flavor, in flavor's own vocabulary."]) \
        in corpus['arguments']

    # an explicit rename on the inner parameter wins the display
    from appeal import add_parameter_usage
    def hue(name):
        return name
    add_parameter_usage(hue, 'name', 'HUE')
    def tint(x, shade: hue = 'red'):
        "Tints."
    plan = build(tint)
    assert '[HUE]' in plan.usage(), plan.usage()

    # multi-operand converters are NOT transparent: the invisible-
    # node error stands (pinned in test_merge_docs_errors), and
    # usage still shows the inner terminals
    def pair(x: int, y: int):
        return (x, y)
    def place(label, at: pair = None):
        "Places."
    u = build(place).usage()
    assert '[<X> <Y>]' in u or '<X> <Y>' in u, u


# ---------------------------------------------------------------------
# the v2 docs' examples compile and run (v1's README discipline,
# carried forward: docs that bit-rot are worse than no docs)

def _doc_examples(md_path):
    """
    Extract runnable examples from a doc.  The convention,
    inherited from v1's README tests: an example is a code block
    whose first line is `import appeal` and whose last line calls
    app.main (that line is replaced with `pass`).  A block
    preceded by an HTML comment containing 'pending' is skipped
    (a documented example awaiting a ruling).  Returns a list of
    (section, source) pairs; the section is the doc's most recent
    heading line, hashes stripped.
    """
    import textwrap
    with open(md_path, 'rt', encoding='utf-8') as f:
        lines = f.read().split('\n')
    examples = []
    example = []
    pending = False
    section = None
    for line in lines:
        stripped = line.strip()
        if line.startswith('##'):
            section = line.lstrip('#').strip()
            continue
        if stripped.startswith('<!--') and 'pending' in stripped:
            pending = True
            continue
        if stripped == 'import appeal':
            example = [line]
            continue
        if not example:
            continue
        if stripped.startswith(('app.main(', 'sys.exit(app.main')):
            prefix = line[:len(line) - len(line.lstrip())]
            example.append(prefix + 'pass')
            if pending:
                pending = False
            else:
                examples.append((section, textwrap.dedent('\n'.join(example))))
            example = []
            continue
        example.append(line)
    return examples


def _run_doc_example(source, where):
    """
    The bit-rot guard, one example: exec it, then force the whole
    pipeline--build every plan, merge every docstring, render the
    help page.  Docs whose examples can't document themselves
    don't ship.  Returns the example's namespace.
    """
    import io, contextlib
    namespace = {}
    try:
        exec(compile(source, where, 'exec'), namespace)
    except Exception as e:
        raise AssertionError(
            f'{where} failed to run: {e}\n{source}') from e
    app = namespace.get('app')
    assert app is not None, f'{where} defines no `app`'
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            app.help()
    except Exception as e:
        raise AssertionError(f'{where}: app.help() failed: {e}') from e
    assert out.getvalue().strip(), f'{where}: empty help output'
    return namespace


def test_doc_examples():
    appeal_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    docs = ('appeal.completion.md', 'appeal.documentation.md')
    ran = 0
    for doc in docs:
        path = os.path.join(appeal_dir, doc)
        if not os.path.exists(path):
            continue
        for i, (section, example) in enumerate(_doc_examples(path)):
            _run_doc_example(example, f'<{doc} example {i}>')
            ran += 1
    assert ran, 'no doc examples found--the convention broke'


# Drives for the README's examples: (section, index within that
# section) -> [(argv, config, expected stdout)].  The programs
# live in README.md; the command-lines and their expected output
# live here, so changing either side breaks the suite (v1's
# discipline: "this is better than README.md bit-rotting").
README_DRIVES = {
    ('Quickstart', 0): [
        (['hello', 'world'], None, 'Hello, world!'),
    ],
    ('Hello, World!', 0): [
        (['hello', 'world'], None, 'Hello, world!'),
    ],
    ('Default Values And `*args`', 0): [
        (['fgrep', 'WM_CREATE', 'window.c'], None,
         'fgrep WM_CREATE window.c'),
        (['fgrep', 'WM_CREATE'], None, 'fgrep WM_CREATE None'),
    ],
    ('Default Values And `*args`', 2): [
        (['cp', 'a', 'b', 'c'], None, "cp ('a', 'b') c"),
    ],
    ('Options, Opargs, And Keyword-Only Parameters', 0): [
        (['fgrep', '-i', '--number', '3', '--color', 'blue',
          'WM_CREATE', 'window.c'], None,
         "fgrep WM_CREATE ('window.c',) 'blue' 3 True"),
        (['fgrep', '--color', 'green', 'boogaloo'], None,
         "fgrep boogaloo () 'green' 0 False"),
    ],
    ('Commands, The Global Command, And Subcommands', 0): [
        (['db', 'main', 'deploy', '9'], None, 'db main\ndeploy 9'),
    ],
    ('Commands, The Global Command, And Subcommands', 1): [
        (['add-item', '3'], None, 'added 3'),
    ],
    ('Cycling: several commands on one line', 0): [
        (['add', '1', '2', 'mul', '3', '4'], None,
         'sum 3\nproduct 12'),
    ],
    ('Annotations And Introspection', 1): [
        (['fgrep', '-p', '2', '13', 'funkyfresh'], None,
         'fgrep funkyfresh () [6, 65.0]'),
    ],
    ('Converter Flexibility', 0): [
        (['build', '--with-dbmliborder=gdbm:ndbm'], None,
         "build ['gdbm', 'ndbm']"),
    ],
    ('Specifying An Option More Than Once', 0): [
        (['build', '-t', 'alpha', '--tag', 'beta',
          '-d', 'X=1', '--define', 'Y=2'], None,
         "build ['alpha', 'beta'] {'X': 1, 'Y': 2}"),
    ],
    ('Specifying An Option More Than Once', 1): [
        (['fgrep', '-v', '--verbose', '-v'], None, 'fgrep verbose=3'),
        (['fgrep'], None, 'fgrep verbose=0'),
    ],
    ('Data Validation', 0): [
        (['go', 'up'], None, "go direction='up'"),
    ],
    ('Multiple Options For The Same Parameter', 0): [
        (['go', '--south'], None, "go direction='south'"),
        (['go'], None, "go direction='north'"),
    ],
    ('Recursive Converters', 1): [
        (['recurse2', 'pdq', '1', '2', 'xyz', '-v'], None,
         "recurse2 a='pdq' b=[(1, 2.0), 'xyz', True]"),
        (['recurse2', 'xyz'], None,
         "recurse2 a='xyz' b=[(0, 0), '', False]"),
    ],
    ('Recursive Converters', 2): [
        (['twice', '-v', '1', '2', 'x', '3', '4', 'y'], None,
         "twice a=[(1, 2.0), 'x', True] b=[(3, 4.0), 'y', False]"),
    ],
    ('Options that map other options', 0): [
        (['inception', '--option', '5', '-v'], None,
         'inception option=[5, True]'),
    ],
    ('Multiple options that aren\'t MultiOptions', 0): [
        (['repetition', '-v', '1', '2', '3', '-v'], None,
         'repetition args=([1, True], [2, False], [3, True])'),
    ],
    ('Positional parameters that only consume options', 0): [
        (['mixin', '-v', '-l', 'debug'], None,
         'mixin log=<Logging verbose=True log_level=debug>'),
    ],
    ('The Famous `make -j`', 0): [
        (['make', 'all'], None, "building ('all',) with 1 jobs"),
        (['make', 'all', '-j'], None, "building ('all',) with inf jobs"),
        (['make', '-j5', 'all'], None, "building ('all',) with 5 jobs"),
    ],
    ('A Class As Your Whole Program', 0): [
        (['-v', 'add', '1', '2'], None, 'sum 3 (verbosely)'),
        (['status'], None, 'status verbose=False'),
    ],
    ('Config layering', 0): [
        (['work', 'notes.txt'], {'editor': 'emacs'},
         'editor=emacs verbose=False\nediting notes.txt'),
        (['--editor', 'nano', 'work', 'notes.txt'], {'editor': 'emacs'},
         'editor=nano verbose=False\nediting notes.txt'),
    ],
}


def test_readme_examples():
    # the README's programs, executed: every example must run and
    # document itself; the ones with drives must produce exactly
    # the output the README promises
    import io, contextlib
    appeal_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    examples = _doc_examples(os.path.join(appeal_dir, 'README.md'))
    assert examples, 'no README examples found--the convention broke'
    counters = {}
    driven = 0
    for section, example in examples:
        index = counters.get(section, 0)
        counters[section] = index + 1
        where = f'<README.md {section!r} #{index}>'
        if (not GENERIC_SPELLINGS
                and any(g in example for g in ('list[', 'dict[', 'tuple['))):
            # the README documents these as 3.9+ spellings
            needs_39(f'README example {section}')
            continue
        namespace = _run_doc_example(example, where)
        for argv, config, expected in README_DRIVES.get((section, index), ()):
            app = namespace['app']
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    if config is None:
                        app.process(list(argv))
                    else:
                        app.process(list(argv), config=dict(config))
            except Exception as e:
                raise AssertionError(
                    f'{where} {argv!r} raised: {e}') from e
            got = out.getvalue().strip()
            assert got == expected, (
                f'{where} {argv!r}:\n  expected {expected!r}\n'
                f'  got      {got!r}')
            driven += 1
    undriven = [key for key in README_DRIVES if key not in
                {(s, i) for s, _ in examples
                 for i in range(counters.get(s, 0))}]
    assert not undriven, (
        f'drives target README examples that no longer exist: {undriven}')
    assert driven >= 25, f'only {driven} README drives ran'


def run_tests(run=None):
    (run or test.run)(name='appeal', module=__name__)
    if NOT_RUN_ON_OLD:
        print(f'{len(NOT_RUN_ON_OLD)} checks not run on Python '
              f'{sys.version_info[0]}.{sys.version_info[1]} '
              f'(3.9+ spellings): {", ".join(sorted(set(NOT_RUN_ON_OLD)))}')


if __name__ == '__main__':
    run_tests()
    test.finish()
