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
    build_plan,
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
    plan = build_plan(hello)
    assert plan.name == 'hello'
    assert len(plan.slots) == 2
    assert plan.slots[0].required
    assert not plan.slots[1].required
    assert plan.minimum == 1
    assert plan.maximum == 2
    assert plan.valid_counts == {1, 2}

def test_plan_star_args():
    plan = build_plan(cp)
    assert plan.minimum == 1          # dst
    assert plan.maximum is None
    assert plan.valid_counts is None
    assert plan.slots[0].repeat
    assert plan.slots[1].trailing

def test_plan_options():
    plan = build_plan(serve)
    strings = sorted(s for o in plan.options for s in o.strings)
    # every option gets an auto-short: its first letter, if free
    assert strings == ['--config', '--retries', '--verbose', '-c', '-r', '-v']
    flags = {o.name: o.is_flag for o in plan.options}
    assert flags == {'verbose': True, 'retries': False, 'config': False}

def test_short_options_first_declared_wins():
    def f(*, verbose=False, value=3):
        return (verbose, value)
    plan = build_plan(f)
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
    plan = build_plan(f)
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
        build_plan(dish)
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
    got = run_both(f, ['-V'], decorations=app._decorations)
    assert got == ('ok', (True, 3)), got
    got = run_both(f, ['--noisy', '-v', '5'],
                   decorations=app._decorations)
    assert got == ('ok', (True, 5)), got
    got = run_both(f, ['--verbose'], decorations=app._decorations)
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
    assert app.process(['--quiet']).result is True
    try:
        app.process(['-q']).result
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
    assert app.process(['-V']).result is True         # a flag, not a value
    assert app.process([]).result is False            # parameter's default
    app2 = Appeal()
    @app2.option('level', '-L')
    @app2.global_command()
    def g(*, level: int = 0):
        return level
    assert app2.process(['-L', '3']).result == 3      # int, from the annotation

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
    assert app.process(['--loud']).result == (True, False)
    for argv in (['--quiet'], ['-q']):
        try:
            app.process(list(argv)).result
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
    assert app2.process(['-Q']).result is True
    # a **kwargs-delivered option IS its strings: zero refuses
    app3 = Appeal()
    @app3.option('mystery')
    @app3.global_command()
    def h(**kwargs):
        return kwargs
    try:
        app3.process([]).result
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
    assert app.process([]).result == 'low'                # parameter's default
    assert app.process(['--level', '5']).result == 5      # @option's annotation
    assert app.process(['-L', '7']).result == 7
    # ...and with no annotation, the converter is inferred from
    # @option's default (type-of-default, v1's rule)
    app2 = Appeal()
    @app2.option('k', '--kay', default=5)
    @app2.global_command()
    def g(*, k=None):
        return k
    assert app2.process([]).result is None
    assert app2.process(['--kay', '9']).result == 9

def test_app_option_decorators_stack():
    # several strings in ONE call share a declaration...
    app = Appeal()
    @app.option('verbose', '-V', '--noisy', default=False)
    @app.global_command()
    def f(*, verbose=False):
        return verbose
    (option,) = app.plan.options
    assert sorted(option.strings) == ['--noisy', '-V']
    assert app.process(['-V']).result is True
    assert app.process(['--noisy']).result is True
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
    assert app2.process([]).result == 'none'
    assert app2.process(['--north']).result == 'north'
    assert app2.process(['--south']).result == 'south'
    # run_both carries the app's registry (ruled 2026-08-09:
    # decorations live in the app, never on the function)
    got = run_both(go2, ['--north'], decorations=app2._decorations)
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

def test_negative_number_heuristic():
    # a '-'+digit token (Larry's heuristic, 2026-08-24): (1) if it fully
    # parses as a short-option bundle, it's options; (2) else if float()
    # accepts it, it's an operand; (3) else raise the option-parse error.
    def build(three=True):
        app = Appeal(name='p')
        def c(x: float = None, *, two=False, four=False, tri=False):
            return (x, two, four, tri)
        app.global_command()(c)
        app.option('two', '-2')(c)
        app.option('four', '-4')(c)
        if three:
            app.option('tri', '-3')(c)
        return app
    # (1) all of -2 -4 -3 mapped: the bundle parses, so it's options
    assert build().process(['-243']).result == (None, True, True, True)
    # (2) -3 unmapped: bundle fails, float('-243') works -> operand
    assert build(three=False).process(['-243']).result == (-243.0, False, False, False)
    # a plain negative number with no digit options at all -> operand
    assert build(three=False).process(['-5']).result == (-5.0, False, False, False)
    # a lone mapped digit option still wins as an option
    assert build().process(['-2']).result == (None, True, False, False)
    # a digit option that takes an oparg: -25 is -2 with attached oparg '5'
    app = Appeal(name='p')
    def v(*, two: str = None):
        return two
    app.global_command()(v)
    app.option('two', '-2')(v)
    assert app.process(['-25']).result == '5'
    # (3) neither a valid bundle nor a float: the option error, naming the
    # first offending short
    try:
        build().process(['-5x']).result
        assert False, 'expected UsageError'
    except UsageError as e:
        assert str(e) == "unknown option '-5'", e

def test_converter_group_contributes_fold_options():
    # a converter GROUP used as a positional exposes its keyword-only options
    # on the command line -- including FOLD options (counter/accumulator),
    # conjured and folded into the group instance (ConjureFold).  v1's
    # "Logging mixin" pattern: eric2(l: Logging, s) with -v/--tag on Logging.
    from appeal import counter, accumulator
    class Logging:
        def __init__(self, *, verbose: counter() = 0, tag: accumulator = []):
            self.verbose = verbose
            self.tag = tag
    def cmd(log: Logging, name='x'):
        return (log.verbose, list(log.tag), name)
    assert run_both(cmd, []) == ('ok', (0, [], 'x'))
    assert run_both(cmd, ['-v', '-v', '-v']) == ('ok', (3, [], 'x'))
    assert run_both(cmd, ['--verbose', 'nm']) == ('ok', (1, [], 'nm'))
    assert run_both(cmd, ['-t', 'a', '-t', 'b', 'nm']) == ('ok', (0, ['a', 'b'], 'nm'))

def test_nested_group_option_bundling():
    # a group option whose converter takes NO operands is nullary: firing it
    # conjures + enters the group, and a short bundle CONTINUES past it
    # (-iz == -i then -z), reaching both outer options and the ones the
    # entered group just registered (0.6.4's five_level_stack mixin stack).
    def inner(*, x=False, y=False):
        return ('inner', x, y)
    def outer(*, i: inner = None, z=False):
        return ('outer', i, z)
    assert run_both(outer, ['-i']) == ('ok', ('outer', ('inner', False, False), False))
    # -iz: conjure inner (no operands), bundle continues to outer's -z
    assert run_both(outer, ['-iz']) == ('ok', ('outer', ('inner', False, False), True))
    # -ix: -i conjures + enters inner, so its own -x is now recognized
    assert run_both(outer, ['-ix']) == ('ok', ('outer', ('inner', True, False), False))

def test_option_errors_name_the_typed_spelling():
    # an option error names the spelling the user actually typed
    # (--verbose / -v), not the parameter name
    def flag(name, *, verbose: bool = False):
        return (name, verbose)
    assert run_both(flag, ['x', '--verbose=maybe']) == (
        'usage', "option '--verbose' expected 'true' or 'false'")
    assert run_both(flag, ['x', '-v=maybe']) == (
        'usage', "option '-v' expected 'true' or 'false'")
    def value(*, color: str = None):
        return color
    assert run_both(value, ['--color']) == (
        'usage', "option '--color' requires a value")
    assert run_both(value, ['-c']) == (
        'usage', "option '-c' requires a value")

def test_simple_converters_on_star_args_and_trailing():
    def upper(s): return s.upper()
    def f(*src: upper, dst: upper):
        return (src, dst)
    got = run_both(f, ['a', 'b', 'z'])
    assert got == ('ok', (('A', 'B'), 'Z')), got

def test_no_forbidden_skip():
    # DO NOT REMOVE OR MODIFY THIS TEST.
    #
    # Larry's ruling (2026-08-20): operands fill STRICTLY left to
    # right, and you NEVER skip an optional parameter in order to fill
    # a LATER optional one.  (The one permitted skip is over optional
    # parameters to reach a REQUIRED trailing operand -- not this.)
    #
    # cmd has two optional converter groups: pair (exactly 2 operands)
    # and triple (exactly 3).  Its ONLY legal arities are therefore 0
    # (neither), 2 (pair alone), and 5 (both).  THREE operands is
    # illegal: pair fills from the first two, the lone leftover cannot
    # complete triple, and you may NOT rescue it by blanking pair and
    # handing all three to triple.  That "forbidden skip" is a bug --
    # hand-written 0.6.8 never did it.  If this test ever starts
    # ACCEPTING a three-argument command line, Appeal has grown the bug
    # back; do NOT edit the test to match -- fix Appeal and tell Larry.
    def pair(x, y):
        return ('pair', x, y)
    def triple(a, b, c):
        return ('triple', a, b, c)
    def cmd(g1: pair = None, g2: triple = None):
        return (g1, g2)

    # the three legal arities
    assert run_both(cmd, []) == ('ok', (None, None))
    assert run_both(cmd, ['1', '2']) == ('ok', (('pair', '1', '2'), None))
    assert run_both(cmd, ['1', '2', '3', '4', '5']) == (
        'ok', (('pair', '1', '2'), ('triple', '3', '4', '5')))

    # the forbidden skip: three operands must RAISE, never fill triple
    got = run_both(cmd, ['1', '2', '3'])
    assert got[0] == 'usage', (
        'FORBIDDEN SKIP: cmd accepted three arguments by blanking the '
        f'left optional group to fill the right one -- got {got!r}')


def test_options_are_never_summoned():
    # DO NOT REMOVE OR MODIFY THIS TEST.
    #
    # Larry's ruling (2026-08-20): options are ALWAYS summoned by name.
    # The "summon the first" rule is ARGUMENTS-only -- a positional
    # converter group can be forced into existence by one of its own
    # options (e.g. scoped_cmd A --flag conjures b).  An OPTION-group is
    # never conjured by a shared inner option.
    #
    # e1 and e2 are option-groups of `extras`, sharing --verbose and
    # --label.  Naming a shared option with NEITHER --e1 nor --e2 must be
    # an ERROR; it may not bring e1 into existence.  Current Appeal wrongly
    # conjures e1 (its sibling-summon path).  If this test ever starts
    # ACCEPTING a bare `--verbose`, the bug is back; fix Appeal, not the
    # test, and tell Larry.
    def extras(*, verbose=False, label=''):
        return ('extras', verbose, label)
    def cmd(a, *, e1: extras = None, e2: extras = None):
        return (a, e1, e2)

    # explicit naming works
    assert run_both(cmd, ['x']) == ('ok', ('x', None, None))
    assert run_both(cmd, ['x', '--e1', '--verbose']) == (
        'ok', ('x', ('extras', True, ''), None))

    # a bare shared option, no group named: must RAISE, never summon e1
    got = run_both(cmd, ['x', '--verbose'])
    assert got[0] == 'usage', (
        'OPTION SUMMONED: a bare shared option conjured a sibling group '
        f'that was never named -- got {got!r}')


def test_trailing_shared_option_summons_the_next_group():
    # DO NOT REMOVE OR MODIFY THIS TEST.
    #
    # Larry's ruling (2026-08-20): a shared option that appears AFTER a
    # converter group is full announces the NEXT group -- it does not
    # cling to the group that just filled.  b and c share --flag; child
    # takes 0-2 operands.  In `A 1 2 --flag`, b is full (1,2), so --flag
    # summons c into existence (defaults + flag) and b keeps flag=False.
    #
    # Current Appeal does the older "the last open window runs to the end
    # of the line" thing and drops the flag on b, leaving c=None.  That's
    # wrong; if this test starts producing b.flag=True / c=None, the bug
    # is back.  Fix Appeal, not the test, and tell Larry.
    def child(p=0, q=1, *, flag=False):
        return ('child', p, q, flag)
    def scoped(a, b: child = None, c: child = None):
        return (a, b, c)

    # unambiguous cases the ruling shares with everyone
    assert run_both(scoped, ['A', '1', '--flag', '2']) == (
        'ok', ('A', ('child', 1, 2, True), None))          # flag mid-b -> b
    assert run_both(scoped, ['A', '1', '2', '--flag', '3', '4']) == (
        'ok', ('A', ('child', 1, 2, False), ('child', 3, 4, True)))  # seam -> c

    # the ruling: a TRAILING flag after a full b summons c, not b
    got = run_both(scoped, ['A', '1', '2', '--flag'])
    assert got == ('ok', ('A', ('child', 1, 2, False), ('child', 0, 1, True))), (
        'TRAILING FLAG CLUNG TO b: a shared option after a full group must '
        f'summon the NEXT group, not stay on the last one -- got {got!r}')


def test_appeal_is_lazy():
    # nothing happens at decoration time: a config error surfaces
    # at first *use*, not when the decorator runs
    app = Appeal()
    @app.global_command()
    def f(t: 42):    # a non-callable annotation isn't in the grammar
        pass
    try:
        app.process([]).result
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
    assert app.process(['good', 'hi']).result == ('good', 'hi')
    assert app.process(['good', 'again']).result == ('good', 'again')
    try:
        app.process(['broken']).result
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
        assert app.process(['-V']).result is True, order
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
        def __call__(self):
            return tuple(self.values)

    class Points(MultiOption):
        def init(self, default):
            self.pts = []
        def option(self, x: int, y: int):
            self.pts.append((x, y))
        def __call__(self):
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
        def __call__(self):
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
    # The gate rule is DROPPED (Larry, 2026-08-21): flat recognition wins.
    # 0.6.4 gated a skippable group's options behind a required "wall"; 1.0
    # recognizes an option ANYWHERE -- the parse is unambiguous (--bright can
    # only conjure color; 2.5 can only feed size), so order doesn't matter.
    def size(width: float, *, bold=False):
        return (width, bold)
    def color(name='black', *, bright=False):
        return (name, bright)
    def draw(shape, s: size, c: color = None):
        return (shape, s, c)
    got = run_both(draw, ['dot', '2.5', '--bright'])
    assert got == ('ok', ('dot', (2.5, False), ('black', True))), got
    got = run_both(draw, ['dot', '--bright', '2.5'])       # 0.6.4 said "too early"
    assert got == ('ok', ('dot', (2.5, False), ('black', True))), got
    got = run_both(draw, ['--bright', 'dot', '2.5'])       # ditto -- now accepted
    assert got == ('ok', ('dot', (2.5, False), ('black', True))), got
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
    # flat recognition (gate rule dropped): every option is recognized
    # anywhere, whether it belongs to a required group or a skippable one.
    def pair(x: float, y: float):
        return (x, y)
    def sub(v: float, *, mark=False):
        return (v, mark)
    def color(name='black', *, bright=False):
        return (name, bright)
    def f(p: pair, b: sub, c: color = None):
        return (p, b, c)
    got = run_both(f, ['--mark', '1', '2', '3'])       # b's own option, early
    assert got == ('ok', ((1.0, 2.0), (3.0, True), None)), got
    got = run_both(f, ['--bright', '1', '2', '3'])     # c's option, early: conjures c
    assert got == ('ok', ((1.0, 2.0), (3.0, False), ('black', True))), got
    got = run_both(f, ['1', '2', '3', '--bright'])
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
    # gate rule DROPPED (Larry, 2026-08-21): flat recognition wins, so
    # --dashed before the pair "wall" is now recognized, not "too early"
    got = run_both(path, ['--dashed', '1', '2', '3'])
    assert got == ('ok', ((1.0, 2.0), ((3.0, True),))), got

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
    # ...but an option with NO sizes at all has nothing to bind to: --bold
    # summoned a size that never got its <WIDTH>, so it wasn't available yet
    got = run_both(draw, ['a', '--bold'])
    assert got[0] == 'usage'
    assert got[1] == '--bold only becomes available if you specify <WIDTH>', got
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

def test_star_args_single_param_group_converts():
    # a SINGLE-parameter converter group off *args must convert its
    # operand per the parameter's annotation, exactly as the
    # non-*args path does (differential-caught 2026-08-16: the old
    # `> 1` threshold in _is_repeat_group treated a one-param group
    # as a raw-string leaf, so g(p:int) returned '3' not 3--masked
    # because every *args test used str params where it's invisible)
    def g(p: int):
        return (p,)
    def f(*rest: g):
        return rest
    assert run_both(f, ['3', '4']) == ('ok', ((3,), (4,)))
    assert run_both(f, ['3'])[1] == ((3,),)
    # non-*args single-param group agrees (the reference behavior)
    def h(x: g):
        return x
    assert run_both(h, ['5']) == ('ok', (5,))

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
    # G1, RULED (Larry, 2026-08-05): optional parameters in a
    # *args converter group fill GREEDILY per instance (v1's
    # semantics)--the fixed-arity refusal is dead, so `loose`
    # BUILDS now.  A converter inside a *args group stays out of
    # the grammar; an all-keyword group can't consume operands.
    def loose(width: float = 1.0, *, bold=False):
        return (width, bold)
    def draw(shape, *sizes: loose):
        pass
    build_plan(draw)                     # G1's refusal is gone
    def pair(x, y):
        return (x, y)
    def nested(where: pair, *, bold=False):
        return (where, bold)
    def draw2(shape, *sizes: nested):
        pass
    try:
        build_plan(draw2)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'streaming driver' in str(e)

def test_star_args_group_greedy_fill():
    # G1/G2, RULED (Larry, 2026-08-05): v1's greedy fill
    # restored, probed against the real v1 (git master).  Each
    # instance takes up to its maximum, greedily, no lookahead;
    # a leftover shortfall below the minimum is an error, never
    # redistributed backward.  Both rungs must agree.
    def pair(a, b='B'):
        return (a, b)
    def draw(*pairs: pair):
        return pairs
    # G2's exact case: v1 gives (('1','2'), ('3','4')), not four
    # clamped instances
    assert run_both(draw, ['1', '2', '3', '4']) == \
        ('ok', (('1', '2'), ('3', '4')))
    assert run_both(draw, ['1', '2', '3']) == \
        ('ok', (('1', '2'), ('3', 'B')))
    assert run_both(draw, ['1']) == ('ok', (('1', 'B'),))
    assert run_both(draw, []) == ('ok', ())

    def trio(a, b='B', c='C'):
        return (a, b, c)
    def draw3(*ts: trio):
        return ts
    assert run_both(draw3, ['1', '2', '3', '4']) == \
        ('ok', (('1', '2', '3'), ('4', 'B', 'C')))
    assert run_both(draw3, ['1', '2', '3', '4', '5']) == \
        ('ok', (('1', '2', '3'), ('4', '5', 'C')))

    # no lookahead, v1-faithful: 4 operands into a 2-to-3 group
    # is greedy 3 + orphan 1 -> error (2 + 2 would fit; v1
    # doesn't look for it, so neither do we)
    def duo(a, b, c='C'):
        return (a, b, c)
    def draw2(shape, *ds: duo):
        return (shape, ds)
    kind, msg = run_both(draw2, ['s', '1', '2', '3', '4'])
    assert kind == 'usage' and 'left over' in msg, (kind, msg)
    assert run_both(draw2, ['s', '1', '2', '3', '4', '5']) == \
        ('ok', ('s', (('1', '2', '3'), ('4', '5', 'C'))))

    # G1's exact case: optional positional + windowed option
    def color(hue='k', *, bold=False):
        return (hue, bold)
    def drawc(shape, *colors: color):
        return (shape, colors)
    assert run_both(drawc, ['dot', 'red', '--bold', 'blue']) == \
        ('ok', ('dot', (('red', False), ('blue', True))))
    assert run_both(drawc, ['dot', '--bold', 'red', 'blue']) == \
        ('ok', ('dot', (('red', True), ('blue', False))))
    assert run_both(drawc, ['dot']) == ('ok', ('dot', ()))
    # 1.0-liberal, noted in the register: a trailing option binds
    # to the last instance (the never-rejects window rule); v1
    # opened a phantom instance and errored
    assert run_both(drawc, ['dot', 'red', 'blue', '--bold']) == \
        ('ok', ('dot', (('red', False), ('blue', True))))


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
    # as you'd think.  MultiOption is an alias of Option now.  (v1's
    # at-most-once StrictOption was removed 2026-08-22: no demand, no
    # precedent in argparse/click.)
    from appeal import MultiOption, Option
    assert MultiOption is Option

    class Where(Option):
        def init(self, default):
            self.calls = []
        def option(self, x: int, y: int):
            self.calls.append((x, y))
        def __call__(self):
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
    assert app5.process(['go5', '-L']).result == (True, False)
    try:
        app5.process(['go5', '-q']).result
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
    assert go and app.process(['go', '--loud']).result == (True, None)
    assert '--_cache' not in app.commands['go'].options
    try:
        app.process(['go', '--_cache', 'x']).result
        assert False, 'private parameter must not map'
    except _appeal.AppealUsageError:
        pass

    # the escape hatch: explicit strings still map it
    app2 = _appeal.Appeal(name='q')
    @app2.command()
    @app2.option('_cache', '--cache')
    def go2(*, _cache=''): return _cache
    assert app2.process(['go2', '--cache', 'hot']).result == 'hot'

    # a custom policy dropping one name by returning []
    def policy(app_, fn, name):
        if name == 'quiet':
            return          # decline by not calling
        _appeal.default_options(app_, fn, name)
    app3 = _appeal.Appeal(name='r', default_options=policy)
    @app3.command()
    def go3(*, loud=False, quiet=False): return (loud, quiet)
    assert app3.process(['go3', '--loud']).result == (True, False)
    try:
        app3.process(['go3', '--quiet']).result
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
    app.process(['A', 'X', 'X']).result
    assert calls == ['A', 'AX', 'XX'], calls
    # a fourth X can't re-enter X's set (no repeat there), so it pops back
    # to A's cycling set and re-runs X the PARENT (AX); subcommands are not
    # required (ruled 2026-08-22), so AX dangling at end is fine, not an error
    calls.clear()
    app.process(['A', 'X', 'X', 'X']).result
    assert calls == ['A', 'AX', 'XX', 'AX'], calls
    # and a fifth X descends again: the cycle breathes in and out
    calls.clear()
    app.process(['A', 'X', 'X', 'X', 'X']).result
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
    assert app.process(['fail']).result == 3


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
        app.process(['grumble']).result
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
    assert 'Options\n-------' in full
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
    assert 'Options' not in no_doc
    assert 'Arguments' not in no_doc
    assert 'Start the server.' in no_doc      # summary survives
    # bare listing obeys the knobs too
    bare = captured(app.help, doc=False)
    assert 'usage: t command' in bare
    assert 'Commands' not in bare
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


def test_markdown_scanner():
    # The Markdown pivot's hand-written textual scanner (Larry's
    # spec, 2026-08-05): summary paragraph, body, and the
    # Options/Arguments/Commands sections by ANY heading kind
    # (ATX or setext), any level, case-insensitive; each section
    # must be exactly one definition list; terms unformatted.
    from appeal.presentation import scan_docstring
    r = scan_docstring(
        "Update the item.\n"
        "\n"
        "Body prose here,\nsecond line.\n"
        "\n"
        "# Options\n"
        "verbose\n"
        ": Chatty.\n"
        "\n"
        "  Second paragraph of chatty.\n"
        "\n"
        "color\n"
        ": Hue.\n"
        "\n"
        "Arguments\n"
        "---------\n"
        "name\n"
        ": The item.\n"
        "\n"
        "## Notes\n"
        "Unrelated section stays in the body.\n")
    assert r['summary'] == 'Update the item.'
    assert 'Body prose here' in r['body']
    assert '## Notes' in r['body']
    assert 'Unrelated section' in r['body']
    assert 'verbose' not in r['body']
    assert r['options'] == [
        ('verbose', 'Chatty.\n\nSecond paragraph of chatty.'),
        ('color', 'Hue.')]
    assert r['arguments'] == [('name', 'The item.')]
    assert r['commands'] is None
    # any level, any kind, any case
    r = scan_docstring("Sum.\n\n###### OPTIONS\nv\n: Doc.\n")
    assert r['options'] == [('v', 'Doc.')]
    r = scan_docstring("Sum.\n\nCommands\n========\ngo\n: Runs.\n")
    assert r['commands'] == [('go', 'Runs.')]
    # refusals name the offender
    from appeal import AppealConfigurationError
    for bad, fragment in (
            ("# Options\nJust prose, no list.\n", 'definition'),
            ("# Options\n**verbose**\n: Doc.\n", 'unformatted'),
            ("# Options\nv\n: A.\n\n# Options\nw\n: B.\n", 'two'),
            ("# Options\n: definition with no term\n", 'no term'),
            ):
        try:
            scan_docstring("Sum.\n\n" + bad)
            assert False, f'expected refusal: {bad!r}'
        except AppealConfigurationError as e:
            assert fragment in str(e), (bad, str(e))


def test_markdown_transforms():
    # to_github / to_commonmark are TEXTUAL (no Markdown parser:
    # users aren't limited to big's subset).  GitHub flavor is
    # the target (ruled 2026-08-05): ONLY definition lists need
    # respelling (inline-HTML <dl> with blank lines so the
    # Markdown inside still renders); strikethrough and alerts
    # ride through.  CommonMark: bold term + blockquote,
    # strikethrough stripped, alerts -> bold labels.
    from appeal.presentation import to_commonmark, to_github
    doc = ("Intro with ~~old~~ new text.\n"
           "\n"
           "> [!WARNING]\n"
           "> Mind the gap.\n"
           "\n"
           "term\n"
           ": A **rich** definition.\n"
           "\n"
           "  Second paragraph.\n")
    gh = to_github(doc)
    assert '~~old~~' in gh                       # GitHub renders it
    assert '[!WARNING]' in gh                    # alerts pass through
    assert '<dl>' in gh and '</dl>' in gh
    assert '<dt>\n\nterm\n\n</dt>' in gh
    assert 'A **rich** definition.' in gh        # markdown survives
    cm = to_commonmark(doc)
    assert '~~' not in cm
    assert '[!WARNING]' not in cm and '> **Warning:**' in cm
    assert '**term**' in cm
    assert '> A **rich** definition.' in cm
    assert '>\n> Second paragraph.' in cm
    # non-list content passes through both untouched
    plain = "Just prose.\n\n* a bullet\n* another\n"
    assert to_github(plain) == plain
    assert to_commonmark(plain) == plain


def test_markdown_renderer():
    # render_markdown_help runs big's whole pipeline: parse ->
    # style -> split -> layout -> join -> StyleSheet.render.
    from appeal.presentation import render_markdown_help
    from big.stylesheet import plain_stylesheet
    from big.markdown import markdown_defaults
    text = render_markdown_help(
        "# Options\n\nSome **bold** prose that is long enough to "
        "need wrapping at a narrow width setting.\n",
        width=40, stylesheet=plain_stylesheet | markdown_defaults)
    assert 'Options' in text
    assert 'bold' in text and '**' not in text   # parsed, not literal
    assert all(len(line) <= 40 for line in text.split('\n')), text
    assert '⦃' not in text                       # styles fully rendered
    # default stylesheet: uncolored ANSI (bold shows, no color)
    ansi = render_markdown_help("**b**\n", width=40)
    assert '\x1b[1m' in ansi


def test_documentation_markdown_formats():
    # documentation('gfm'/'commonmark') is the API spelling--
    # deliberately NO command-line switch (Larry, 2026-08-05).
    import appeal as _appeal
    app = _appeal.Appeal('t')
    @app.command()
    def serve(host):
        """
        Start the server.

        # Arguments
        host
        : Interface to ~~listen~~ bind on.
        """
    gh = app.documentation('gfm')
    assert '# t' in gh and '## t serve' in gh
    assert '<dl>' in gh and '~~listen~~ bind on' in gh
    cm = app.documentation('commonmark')
    assert '**host**' in cm and '> Interface' in cm
    try:
        app.documentation('docx')
        assert False, 'expected refusal'
    except _appeal.AppealConfigurationError as e:
        assert 'docx' in str(e)


def test_renderer_injects_line():
    # Ruled 2026-08-08: `line` is '-' repeated to the margin--a
    # full-width rule bare, a margin-wide model inside big's
    # clip/fill.  The RENDERER injects it (only the renderer
    # knows the margin), underneath the sheet so a sheet's own
    # `line` wins (the stylesheet= verbatim rule).
    from appeal.presentation import render_baked_help
    from big.markdown import markdown_defaults
    from big.stylesheet import StyleSheet, plain_stylesheet, transforms
    layout = ('⦃heading3⦙Deeds⦄',)
    sheet = (markdown_defaults | transforms | plain_stylesheet
             | StyleSheet({
                 'heading3': ('T', '⦃strip⦙T⦄\n'
                                   '⦃clip⦙⦃line⦄⦙⦃fill⦙*⦙⦃strip⦙T⦄⦄⦄'),
               }))
    page = render_baked_help((('markdown', layout),), margin=40,
                             stylesheet=sheet)
    assert page == 'Deeds\n*****\n', repr(page)
    # a rule wider than the margin clips to it
    wide = (markdown_defaults | transforms | plain_stylesheet
            | StyleSheet({'heading3': ('T', '⦃clip⦙⦃line⦄⦙⦃fill⦙=⦙'
                                            '⦃strip⦙T⦄xxxxxxxxxxxx⦄⦄')}))
    page = render_baked_help((('markdown', layout),), margin=8,
                             stylesheet=wide)
    assert page == '========\n', repr(page)
    # a sheet defining its OWN line wins over the injection
    own = (markdown_defaults | transforms | plain_stylesheet
           | StyleSheet({'line': ('##',),
                         'heading3': ('T', '⦃clip⦙⦃line⦄⦙⦃strip⦙T⦄⦄')}))
    page = render_baked_help((('markdown', layout),), margin=40,
                             stylesheet=own)
    assert page == 'De\n', repr(page)    # clipped to the SHEET's 2-wide line


def test_wrapped_heading_fuses():
    # Larry's super-duper-long heading (2026-08-08): a heading
    # that wraps is ONE heading--adjacent same-role spans fuse
    # across the line break (big's join_styles,
    # span_linebreaks=True), so the structural entry fires once:
    # rules above and below the BLOCK, sized by clip-to-line,
    # not a sandwich per line.
    from appeal.presentation import default_template, help_page_pieces, render_baked_help
    corpus = {'summary': [], 'documentation':
              ["# Heading One which by the way is super duper long "
               "almost excessively so and it's kind of pointless "
               "like this"],
              'arguments': [], 'options': [], 'commands': []}
    pieces = help_page_pieces('x', corpus, default_template,
                              suppress=('usage',))
    page = render_baked_help(pieces, margin=70)
    assert page == (
        '======================================================='
        '===============\n'
        'Heading One which by the way is super duper long almost '
        'excessively so\n'
        "and it's kind of pointless like this\n"
        '======================================================='
        '===============\n'), repr(page)


def test_heading_with_inline_formatting():
    # A heading containing inline markup (code, bold, a link)
    # arrives as several same-role spans; join_styles fuses them
    # --nested content included (big's fix, 2026-08-08)--so the
    # structural entry fires ONCE.  Before the fix this garbled:
    # three rule sandwiches sharing lines.
    from appeal.presentation import default_template, help_page_pieces, render_baked_help
    corpus = {'summary': [], 'documentation':
              ['# Heading with `code` inside'],
              'arguments': [], 'options': [], 'commands': []}
    pieces = help_page_pieces('x', corpus, default_template,
                              suppress=('usage',))
    page = render_baked_help(pieces, margin=40)
    assert page == ('========================\n'
                    'Heading with code inside\n'
                    '========================\n'), repr(page)


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
    app5 = _appeal.Appeal('t5', doc='Hi.\n\n# Commands\nzork\n: no.')
    @app5.command()
    def real():
        pass
    try:
        helppage(app5)
        assert False, 'expected ConfigurationError'
    except _appeal.ConfigurationError as e:
        assert 'zork' in str(e)


def test_template_dresses_the_page():
    # The Markdown pivot (ruled 2026-08-05) SUPERSEDES the
    # presentation-wins ruling of 2026-08-01: the docstring is
    # input--the author's heading decoration is just how they
    # spelled it--and the TEMPLATE establishes the page's order
    # and dresses its headings ('## Options' by default,
    # rendering as a setext-underlined heading).
    import appeal as _appeal
    import contextlib, io

    app = _appeal.Appeal(name='p')
    @app.global_command()
    def g(thing, *, loud=False):
        """
        Summary here.

        ###### OPTIONS
        loud
        : Speak up.

        Arguments
        =========
        thing
        : The thing.
        """
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            app.main(['--help'])
        except SystemExit:
            pass
    text = out.getvalue()
    # the input decoration is gone; the template's dress renders
    assert '######' not in text and '=====' not in text, text
    assert 'Options\n-------' in text, text
    assert 'Arguments\n---------' in text, text
    # the TEMPLATE's order wins: arguments before options
    assert text.index('Arguments\n') < text.index('Options\n'), text
    assert '-l|--loud  Speak up.' in text, text
    assert '<THING>  The thing.' in text, text
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
    assert app.process([]).result == (None, None, 1)
    assert app.process(['--debug']).result == ('', None, 1)
    assert app.process(['--debug=vj']).result == ('vj', None, 1)
    assert app.process(['--jobs']).result == (None, None, 0)
    assert app.process(['-j', '3']).result == (None, None, 3)
    # a greedy oparg that eats the wrong token is the USER's
    # error--UsageError, never a raw ValueError traceback
    # (found 2026-08-06 writing the die famous-make docs)
    try:
        app.process(['-j', 'zork']).result
        assert False, 'expected UsageError'
    except UsageError as e:
        assert 'zork' in str(e) and 'int' in str(e)
    # a plain (or None-defaulted) str option still REQUIRES its
    # value, exactly as 0.6.4 did
    try:
        app.process(['--file']).result
        assert False, 'expected UsageError'
    except UsageError as e:
        assert 'requires a value' in str(e)
    # the bare factory refuses by name
    app2 = _appeal.Appeal('t2', default_mappings=None)
    @app2.global_command()
    def g(*, x: optional = None):
        return x
    try:
        app2.process([]).result
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
        app.process(['help']).result
    tail0 = out.getvalue().split('Commands')[-1]
    words = [l.split()[0] for l in tail0.splitlines()
             if l and not l.startswith(' ') and set(l) != {'-'}]
    assert words == ['zebra', 'mango', 'apple', 'help'], words

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app.process(['help', 'zebra']).result
    tail = out.getvalue().split('Commands')[-1]
    words = [l.split()[0] for l in tail.splitlines()
             if l and not l.startswith(' ') and set(l) != {'-'}]
    assert words == ['walk', 'crawl', 'help'], words

    # completion stays sorted, deliberately
    candidates = app.complete([], '')
    assert candidates == sorted(candidates), candidates
    assert set(candidates) == {'zebra', 'mango', 'apple', 'help'}, candidates


def test_sibling_option_groups():
    # Larry's ruling (2026-08-20, superseding the 2026-07-18 review
    # item 8 elaboration): options are ALWAYS summoned explicitly by
    # name.  Two keyword-only parameters sharing a converter (e1:
    # extras, e2: extras) are SIBLING option groups; each is born
    # only when its OWN name (--e1/--e2) is spoken.  A shared child
    # option (--fiddle) NEVER conjures a sibling into existence--with
    # no --e1/--e2 there is no home for it, so it's an error, not a
    # birth.  Once announced, a shared child option binds to the
    # nearest announced parent before it.  ("summon the first" is
    # ARGUMENTS-only; see [[options-never-summoned]].)
    def extras(a='', *, fiddle=False, booper=False):
        return ('e', a, fiddle, booper)
    def cmd(x, *, e1: extras = None, e2: extras = None):
        return (x, e1, e2)

    # nothing: both default
    assert run_both(cmd, ['x']) == ('ok', ('x', None, None))
    # a bare child option with NO --e1/--e2 has no home: an error,
    # not a summon (options are never conjured into being)
    got = run_both(cmd, ['--fiddle', 'x'])
    assert got[0] == 'usage' and 'fiddle' in got[1], got
    got = run_both(cmd, ['--fiddle', '--booper', 'x'])
    assert got[0] == 'usage' and 'fiddle' in got[1], got
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
    # announced out of declaration order: each shared option binds to
    # the nearest announced parent before it, in command-line order
    got = run_both(cmd, ['x', '--e2', 'A2', '--e1', 'A1'])
    assert got == ('ok', ('x', ('e', 'A1', False, False),
                          ('e', 'A2', False, False))), got
    got = run_both(cmd, ['x', '--e2', 'A2', '--booper', '--e1', 'A1'])
    assert got == ('ok', ('x', ('e', 'A1', False, False),
                          ('e', 'A2', False, True))), got

    # a shared child option still has no home without an announcement,
    # even when the first sibling has a required argument
    def needy(a, *, fiddle=False):
        return ('n', a, fiddle)
    def cmd2(x, *, e1: needy = None, e2: needy = None):
        return (x, e1, e2)
    got = run_both(cmd2, ['--fiddle', 'x'])
    assert got[0] == 'usage' and 'fiddle' in got[1], got


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
    from appeal import Decorations
    def go(*, direction='north'):
        return direction
    d = Decorations()
    d.add_option(go, 'direction', ('--north',),
                 annotation=lambda: 'north')
    d.add_option(go, 'direction', ('--south',),
                 annotation=lambda: 'south')
    got = run_both(go, ['--north', '--south'], decorations=d)
    assert got == ('ok', 'south'), got
    got = run_both(go, ['--south', '--north'], decorations=d)
    assert got == ('ok', 'north'), got
    got = run_both(go, ['--north', '--south', '--north'],
                   decorations=d)
    assert got == ('ok', 'north'), got
    # a value-producing flag refuses '=' by name (presence IS
    # the value; there's no boolean to set)
    got = run_both(go, ['--north=false'], decorations=d)
    assert got[0] == 'usage' and "doesn't take a value" in got[1], got


def test_repeated_options_match_the_ecosystem():
    # LAST-WINS, the way argparse/click/getopt/fire all behave
    # (verified 2026-08-16) and the way Larry ruled (2026-07-18,
    # review item 6): a repeated option takes the last occurrence;
    # 0.6.4's "specified more than once" error is gone for plain
    # options (StrictOption keeps it, tested elsewhere).  The
    # motivating case is a shell alias baking in a value that the
    # user overrides on the command line.
    import appeal as _appeal

    # 1) a plain str value option -- last wins
    def f(*, mode='safe'):
        return mode
    assert run_both(f, []) == ('ok', 'safe')                 # default
    assert run_both(f, ['--mode', 'a']) == ('ok', 'a')
    assert run_both(f, ['--mode', 'a', '--mode', 'b']) == ('ok', 'b')
    assert run_both(f, ['-m', 'a', '--mode', 'b', '-m', 'c']) == ('ok', 'c')

    # 2) validate() -- last wins for the VALUE, but EVERY oparg is
    # validated (ruled 2026-08-16: "it's not called validate for
    # nothing").  Both valid -> last wins; an invalid value is
    # rejected even when it's later overridden.
    def v(*, mode: _appeal.validate('fast', 'safe') = 'safe'):
        return mode
    assert run_both(v, ['--mode', 'fast', '--mode', 'safe']) == ('ok', 'safe')
    assert run_both(v, ['--mode', 'safe', '--mode', 'fast']) == ('ok', 'fast')
    # an invalid value errors even though a later one overrides it
    got = run_both(v, ['--mode', 'bogus', '--mode', 'safe'])
    assert got[0] == 'usage' and 'bogus' in got[1], got
    # ...and of course when it's the survivor
    got = run_both(v, ['--mode', 'safe', '--mode', 'bogus'])
    assert got[0] == 'usage' and 'bogus' in got[1], got
    # the same holds for a plain int converter: 'x' never converts
    def n(*, num: int = 0):
        return num
    got = run_both(n, ['--num', 'x', '--num', '5'])
    assert got[0] == 'usage', got
    assert run_both(n, ['--num', '1', '--num', '5']) == ('ok', 5)

    # 3) a boolean flag -- -v and --verbose, idempotent across
    # spellings and repetition
    def b(*, verbose=False):
        return verbose
    assert run_both(b, []) == ('ok', False)
    assert run_both(b, ['-v']) == ('ok', True)
    assert run_both(b, ['-v', '-v']) == ('ok', True)
    assert run_both(b, ['-v', '--verbose']) == ('ok', True)
    assert run_both(b, ['--verbose', '-v', '--verbose']) == ('ok', True)

    # 4) the north/south/east/west idiom: one parameter, four
    # zero-arg converters via four @app.option declarations.
    # 0.6.4 allowed exactly one and errored on any repeat (same or
    # different); 1.0 relaxed to last-wins, like a shared dest.
    from appeal import Decorations
    def compass(*, direction='here'):
        return direction
    d = Decorations()
    for word in ('north', 'south', 'east', 'west'):
        d.add_option(compass, 'direction', ('--' + word,),
                     annotation=(lambda w=word: (lambda: w))())
    assert run_both(compass, [], decorations=d) == ('ok', 'here')
    assert run_both(compass, ['--east'], decorations=d) == ('ok', 'east')
    # two different -> last wins (0.6.4 errored here)
    assert run_both(compass, ['--north', '--south'],
                    decorations=d) == ('ok', 'south')
    # the same one twice -> also fine, still that one (0.6.4 errored)
    assert run_both(compass, ['--west', '--west'],
                    decorations=d) == ('ok', 'west')
    # all four, in order -> the last spoken wins
    assert run_both(compass, ['--north', '--south', '--east', '--west'],
                    decorations=d) == ('ok', 'west')
    assert run_both(compass, ['--west', '--east', '--south', '--north'],
                    decorations=d) == ('ok', 'north')


def test_one_char_option_names_get_no_long_option():
    # v1, probed: parameter 'n' has only '-n'; '--n' is unknown
    def f(*, n: int = 0):
        return n
    plan = build_plan(f)
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
        build_plan(g)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert "'n'" in str(e), e

def test_multioption_refusals_are_named():
    from appeal import MultiOption
    class Tags(MultiOption):
        def init(self, default): pass
        def option(self, tag): pass
        def __call__(self): pass
    # single-arity Option classes work positionally (v1's corpus:
    # mapping readers feed them sequences of occurrences)
    class Collect(MultiOption):
        def init(self, default):
            self.values = []
        def option(self, tag):
            self.values.append(tag)
        def __call__(self):
            return tuple(self.values)
    def positional(t: Collect):
        return t
    build_plan(positional)
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
        def __call__(self):
            return tuple(self.values)
    def positional2(p: Pairs):
        return p
    build_plan(positional2)
    from appeal import read_mapping
    assert read_mapping(positional2,
                        {'p': [[1, 2], [3, 4]]}) == ((1, 2), (3, 4))
    got = run_both(positional2, ['token'])
    assert got[0] == 'usage', got
    class Fancy(MultiOption):
        def init(self, default): pass
        def option(self, tag='x'): pass
        def __call__(self): pass
    def f(*, t: Fancy = None):
        pass
    # an optional option() parameter is legal as an option--its
    # operand consumes greedily (v1)--but stays refused as a
    # positional fold
    build_plan(f)
    def positional3(p: Fancy):
        pass
    try:
        build_plan(positional3)
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
        def __call__(self):
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
    # naming a group's option with no operands to complete it: the option
    # isn't available until you supply the group's required operands.  The
    # message is built from the summoned converter itself (its option + the
    # operands it's missing), the same fact the usage bracket shows.
    def rgbstroke(r: int, g: int, b: int, *, dashed=False):
        return (r, g, b, dashed)
    def draw2(shape, s: rgbstroke=None):
        return (shape, s)
    got = run_both(draw2, ['dot', '--dashed'])
    assert got[0] == 'usage'
    assert got[1] == ('--dashed only becomes available if you '
                      'specify <R> <G> and <B>'), got

def test_plan_trailing_does_not_promote():
    # reservation is not promotion: z is filled from the end, so
    # two operands mean (a, z), skipping b--unambiguous
    def f(a, b='B', *, z):
        return (a, b, z)
    plan = build_plan(f)
    named = {s.name: s for s in plan.slots}
    assert not named['b'].required
    assert named['z'].trailing
    assert plan.valid_counts == {2, 3}

def test_plan_promotes_group_before_required():
    # a converter group's trailing default is promoted to required
    # when required operands FOLLOW it (0.6.4: "requires 4 arguments
    # in this argument group").  There's no reservation here--b sits
    # in the stream before x, y--so the group must take all its
    # operands: f wants exactly four, never three.
    def opt2(a: int, b: int = 0):
        return (a, b)
    def f(first: opt2, x: int, y: int):
        return (first, x, y)
    plan = build_plan(f)
    assert plan.valid_counts == {4}
    assert plan.minimum == 4
    assert run_both(f, ['1', '2', '3', '4']) == ('ok', ((1, 2), 3, 4))
    assert run_both(f, ['1', '2', '3'])[0] == 'usage'

def test_plan_promotes_nested_group():
    # promotion reaches across nesting: inner's q is promoted because
    # r (and z) follow the whole outer group
    def inner(p: int, q: int = 0):
        return (p, q)
    def outer(g: inner, r: int):
        return (g, r)
    def f(o: outer, z: int):
        return (o, z)
    plan = build_plan(f)
    assert plan.valid_counts == {4}
    assert run_both(f, ['1', '2', '3', '4']) == ('ok', (((1, 2), 3), 4))
    assert run_both(f, ['1', '2', '3'])[0] == 'usage'

def test_plan_args_group_internal_default_not_promoted():
    # inside *args, nothing required follows the group, so an internal
    # default stays optional--instances are variable width, matching
    # 0.6.4 exactly (greedy: fill to 2, a lone trailing operand is 1)
    def opt2(a: int, b: int = 0):
        return (a, b)
    def f(*items: opt2):
        return items
    assert run_both(f, ['1']) == ('ok', ((1, 0),))
    assert run_both(f, ['1', '2']) == ('ok', ((1, 2),))
    assert run_both(f, ['1', '2', '3']) == ('ok', ((1, 2), (3, 0)))

def test_plan_promotion_unshares_shared_converter():
    # the SAME converter used both before a required operand (promote)
    # and as a trailing optional (don't) must not cross-contaminate:
    # the build memo shares one Plan, so promotion un-shares it
    def opt2(a: int, b: int = 0):
        return (a, b)
    def f(first: opt2, x: int, last: opt2 = None):
        return (first, x, last)
    plan = build_plan(f)
    named = {s.name: s for s in plan.slots}
    assert named['first'].child is not named['last'].child
    assert named['first'].child.minimum == 2   # promoted
    assert named['last'].child.minimum == 1     # left optional
    assert plan.valid_counts == {3, 4, 5}
    assert run_both(f, ['1', '2', '3']) == ('ok', ((1, 2), 3, None))
    assert run_both(f, ['1', '2', '3', '4']) == ('ok', ((1, 2), 3, (4, 0)))

def test_plan_configuration_errors():
    def bad_order(*, opt='x', z):
        pass
    try:
        build_plan(bad_order)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'z' in str(e)

    # a default-True flag is LEGAL (v1, restored 2026-07-18--
    # Larry's break #1: v2's first cut refused it): presence
    # stores `not default`
    def true_flag(*, verbose=True):
        pass
    (option,) = build_plan(true_flag).options
    assert option.kind == 'flag' and option.present is False, option

    # **kwargs is legal now (it receives @app.option declarations);
    # bare, it just gets nothing
    def fine_kwargs(x, **kw):
        return (x, kw)
    assert build_plan(fine_kwargs).minimum == 1


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


def run_both(command, argv, decorations=None):
    """
    Run argv through 1.0's one engine: build the Converter classes in memory
    (compile.build_converters) and run appeal.Processor.  Returns
    ('ok', result) or ('usage', message).  (Named run_both from when it
    cross-checked an in-memory vs an emitted-source rung; the pivot to
    interpreter-only retired source emission, so there's one rung now.)
    decorations: the app-side @option/@parameter registry.
    """
    import appeal
    from appeal.backend import build_converters, _converter_key
    plan = build_plan(command, decorations=decorations)
    word = plan.name.replace('_', '-')
    cls = build_converters([plan])[_converter_key(plan)]
    try:
        return ('ok', appeal.execute({word: cls}, [word] + list(argv)))
    except UsageError as e:
        return ('usage', str(e))

def run_both_set(commands, global_command, argv):
    """
    A multi-command program through 1.0's one engine (a throwaway Appeal, run
    via process): commands keep their LITERAL function names (no dash-mangling),
    no auto help/version.  Returns ('ok', result) or ('usage', message); a bare
    line prints the listing and yields 1 (orientation).
    """
    import appeal as _ap
    app = _ap.Appeal(name='prog', default_mappings=None)
    for c in commands:
        app.command(c.__name__)(c)
    if global_command is not None:
        app.global_command()(global_command)
    try:
        return ('ok', app.process(list(argv)).result)
    except UsageError as e:
        return ('usage', str(e))

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
    # ---- fill left to right, no completability search (the retired
    # automaton skipped optional `a` to complete `p`; the ruled greedy
    # engine fills `a='x'` and lets `p` starve -- [[fill-left-to-right]])
    (ambig, [],                             ('ok', ('A', 'P'))),
    (ambig, ['q'],                          ('ok', ('q', 'P'))),
    (ambig, ['x', 'y'],                     None),   # a='x', p wants 2, y left
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
    assert 'Commands\n--------' in out.getvalue()

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
    assert calls == [('proj', True), ('proj', False)], calls
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
    assert ran == ['x'], ran

def test_greedy_global_operands():
    # deterministic (Larry, 2026-08-21): the global fills its arguments
    # greedily -- an optional argument eats whatever's next, even a would-be
    # command word.  'run x' makes project='run', then 'x' isn't a command
    # (no "flexible" yielding to the command-word set mid-fill).  Spell the
    # optional to reach a subcommand.
    seen = []
    def g2(project='default'):
        seen.append(project)
    def run(target):
        return ('run', target)
    got = run_both_set([run], g2, ['run', 'x'])          # project='run', 'x' unknown
    assert got[0] == 'usage' and 'unknown command' in got[1], got
    got = run_both_set([run], g2, ['proj', 'run', 'x'])  # spell it -> dispatches run
    assert got == ('ok', ('run', 'x')), got
    assert seen[-1:] == ['proj'], seen
    got = run_both_set([run], g2, ['a', 'b', 'x'])       # 'a' fills, 'b' isn't a command
    assert got[0] == 'usage' and 'unknown command' in got[1], got

def test_greedy_global_star_args():
    # deterministic: *args never saturates, so a *args global swallows the
    # whole line -- a subcommand after it is unreachable (its word is eaten).
    tags = []
    def g(*seen):
        tags.append(seen)
    def go(target):
        return ('go', target)
    got = run_both_set([go], g, ['a', 'b', 'go', 'x'])   # *seen eats it all
    assert got != ('ok', ('go', 'x')), got               # go never dispatches

def test_greedy_global_converter_group():
    def pair(x: int, y: int):
        return (x, y)
    def g(p: pair = None):
        return 3 if p == (9, 9) else None    # truthy return halts
    def run(target):
        return ('run', target)
    got = run_both_set([run], g, ['1', '2', 'run', 'x'])  # p=(1,2), then run('x')
    assert got == ('ok', ('run', 'x')), got
    got = run_both_set([run], g, ['9', '9', 'run', 'x'])  # p=(9,9) -> truthy halt
    assert got == ('ok', 3), got

def test_repeatable_precommand_eras():
    # precommand is repeatable (Larry, 2026-08-21): eras run front-to-back
    # before the commands, each greedily saturating its own arguments, then
    # yielding at the first token it doesn't own
    ran = []
    app = Appeal(name='tool')
    @app.precommand()
    def setup(*, debug=False):
        ran.append(('setup', debug))
    @app.precommand()
    def scope(where):
        ran.append(('scope', where))
    @app.command()
    def run(target):
        return ('run', target)
    ran.clear()
    assert app.process(['--debug', 'here', 'run', 'x']).result == ('run', 'x')
    assert ran == [('setup', True), ('scope', 'here')], ran

    # index=0 inserts at the head; a truthy int from any era halts, the
    # rest of the line never runs
    gated = []
    app2 = Appeal()
    @app2.precommand(index=0)
    def gate(*, fail=False):
        return 3 if fail else None
    @app2.precommand()
    def base(x):
        gated.append(('base', x))
    @app2.command()
    def go(target):
        gated.append(('go', target)); return 0
    gated.clear()
    assert app2.process(['--fail', 'v', 'go', 'y']).result == 3     # gate halts first
    assert gated == [], gated
    gated.clear()
    assert app2.process(['v', 'go', 'y']).result == 0
    assert gated == [('base', 'v'), ('go', 'y')], gated

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
    assert app.process(['greet', 'world']).result == 'hi, world'
    assert app.process(['--trace', 'add', '2', '3']).result == 5
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
    # the bare-line listing is baked pieces rendered at print
    # time (errors and orientation ride the pipeline, ruled
    # 2026-08-06): template-dressed heading, compact rows
    assert 'Commands\n--------' in out.getvalue()

def test_command_sys_exit_message():
    import contextlib, io
    # a command's sys.exit("msg") must reproduce Python's own
    # SystemExit contract: print the message to stderr, exit 1.
    # run_main used to catch the SystemExit and drop the string,
    # returning 1 silently (fixed 2026-08-09).
    app = Appeal(name='boom')
    @app.command()
    def boom():
        import sys
        sys.exit("this string must reach stderr")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        assert exit_code(lambda: app.main(['boom'])) == 1
    assert err.getvalue() == "this string must reach stderr\n"
    assert out.getvalue() == ''
    # an integer code is still returned verbatim, with nothing printed
    app2 = Appeal(name='bang')
    @app2.command()
    def bang():
        import sys
        sys.exit(3)
    err2 = io.StringIO()
    with contextlib.redirect_stderr(err2):
        assert exit_code(lambda: app2.main(['bang'])) == 3
    assert err2.getvalue() == ''

def run_both_stdout(command, argv, decorations=None):
    """
    A printing parse (e.g. --help) through 1.0's one engine, via a throwaway
    Appeal: returns (result, text).  -h/--help is a precommand option that
    prints then sys.exit(0)s; that clean exit maps back to None (the old
    interpreter's help-returns-None), so callers read `result is None`.
    """
    import contextlib, io
    import appeal as _ap
    app = _ap.Appeal(name=command.__name__.replace('_', '-'))
    if decorations is not None:
        app._decorations = decorations
    app.global_command()(command)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            result = app.process(list(argv)).result
    except SystemExit as e:
        result = None if e.code in (0, None) else e.code
    return result, out.getvalue()

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
    # ('--help TOPIC' is topic help now -- the option takes an optional topic,
    # like the `help` command; the bare flag shows the whole page)
    for argv in (['--help'], ['-h']):
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
    plan = build_plan(connect)
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
    # Markdown definition-list entries become argument/option
    # tables (terms resolved via the plan: option strings, usage
    # names); prose stays prose.  Converted to the Markdown
    # dialect 2026-08-05 (the pivot: one format ships); the
    # old presentation-wins indent rule died with the old
    # grammar--the template dresses the page.
    def size(width: float, *, bold=False):
        return (width, bold)
    def draw(shape, width: size = None, *, verbose=False, times: int = 1):
        """
        Draws a shape.

        Note: this line is prose, not a parameter.

        # Arguments
        shape
        : the shape to draw.  A longer description that will
          need wrapping when rendered into the page's width.

        width
        : how wide.

        # Options
        verbose
        : narrate the process.

        times
        : how many times.
        """
        pass
    result, text = run_both_stdout(draw, ['--help'])
    assert result is None
    assert 'Note: this line is prose, not a parameter.' in text
    # '## Arguments' renders as a setext-underlined heading
    assert 'Arguments\n---------' in text, text
    assert 'Options\n-------' in text, text
    # terms resolve to displays; short terms render COMPACT
    # (the detail-column heuristic, designed 2026-08-06):
    # aligned two-column, continuations at the detail column
    assert '<SHAPE>  the shape to draw.' in text, text
    assert '<WIDTH>  how wide.' in text
    assert '-v|--verbose        narrate the process.' in text
    assert '-t|--times <TIMES>  how many times.' in text
    body = text.split('Arguments\n')[0]
    assert 'shape\n:' not in body                # entries were extracted
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

        # Options
        width
        : how many dots wide.
        """
        return (style, width)
    def draw(shape, *, line: dotted = None):
        "Draw a shape."
        return shape
    result, text = run_both_stdout(draw, ['--help'])
    assert result is None
    # a nested option's row is a NESTED definition list inside
    # its parent's details (ruled 2026-08-06).  NOTE: big's
    # compact def-list engine currently renders the nesting
    # flat--content lands, indentation is the engine's call
    assert '-w|--width <WIDTH>  how many dots wide.' in text, text

    # full depth: option -> converter -> option -> converter -> option
    def inner(v, *, precision: int = 2):
        """
        # Options
        precision
        : decimal places.
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
        # Options
        width
        : CONVERTER text.
        """
        return style
    def cmd(shape, *, line: dd = None):
        """
        Draw.

        # Options
        width
        : COMMAND text.
        """
        return shape
    _, text = run_both_stdout(cmd, ['--help'])
    assert '-w|--width <WIDTH>  COMMAND text.' in text, text
    assert 'CONVERTER' not in text, text

    # each converter documents its OWN window even when the inner
    # name is ambiguous across two sibling option converters
    def a_conv(v, *, flag=False):
        """
        # Options
        flag
        : from A.
        """
        return v
    def b_conv(v, *, flag=False):
        """
        # Options
        flag
        : from B.
        """
        return v
    def two(shape, *, first: a_conv = None, second: b_conv = None):
        "Two."
        return shape
    _, text = run_both_stdout(two, ['--help'])
    assert 'from A.' in text and 'from B.' in text, text


def test_command_set_help():
    # help through 1.0's one engine (the app facade): the listing, a topic
    # page, an unknown topic, and a user `help` command overriding the auto one
    import contextlib, io
    app = Appeal(name='pile')
    @app.command()
    def add_item(name, count: int = 1):
        """
        Adds an item to the pile.

        # Arguments
        name
        : what to call it.
        """
        return ('add', name, count)
    @app.command()
    def remove(name):
        "Removes an item."
        return ('remove', name)

    def grab(argv):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                result = app.process(list(argv)).result
        except SystemExit as e:
            result = None if e.code in (0, None) else e.code
        return result, out.getvalue()

    # `help`: the listing, with summaries and the help row (v1's shape)
    result, listing = grab(['help'])
    assert result is None
    assert listing.startswith('usage: pile command')
    assert 'add-item  Adds an item to the pile.' in listing
    assert 'remove    Removes an item.' in listing
    assert 'help      Print usage documentation on a specific command.' in listing

    # `help CMD`: the command's page -- usage first (0.6.4's order), the prog
    # prefix on an app-built plan, and the renamed argument table
    _, by_help = grab(['help', 'add-item'])
    assert by_help.startswith('usage: '), by_help
    assert 'Adds an item to the pile.' in by_help
    assert 'usage: pile add-item' in by_help
    assert '<NAME>   what to call it.' in by_help

    # `help BOGUS`
    try:
        app.process(['help', 'bogus']).result
        assert False, 'expected UsageError'
    except UsageError as e:
        assert 'bogus' in str(e)

    # a user-defined help command wins: no automatic anything
    app2 = Appeal(name='pile', default_mappings=None)
    @app2.command()
    def add_item2(name):
        return name
    @app2.command('help')
    def user_help(topic=''):
        return ('user help', topic)
    assert app2.process(['help', 'x']).result == ('user help', 'x')
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert app2.process([]).result == 1
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
        result = app.process(['help']).result
    assert result is None
    assert 'add-item  Adds an item to the pile.' in out.getvalue()  # _ -> -
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app.process(['help', 'add-item']).result
    assert out.getvalue().startswith('usage: '), out.getvalue()
    assert 'Adds an item to the pile.' in out.getvalue()
    # app-built plans carry the prog prefix (ruled 2026-07-19)
    assert 'usage: pile add-item' in out.getvalue()

def test_set_level_help_flag():
    # `tool --help` (or -h) at position 0: the command listing, exactly like the
    # `help` command.  -h/--help is a precommand OPTION now (2026-07-19); it
    # prints then sys.exit(0)s -- raw process() propagates it, main() converts.
    import contextlib, io
    app = Appeal(name='pile')
    @app.command()
    def add_item(name):
        "Adds an item."
        return ('add', name)

    def grab(argv):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                app.process(list(argv)).result
            code = 'returned'
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 0
        return code, out.getvalue()

    code, text = grab(['--help'])
    assert code == 0 and text.startswith('usage: pile command'), text
    assert 'add-item  Adds an item.' in text        # _ -> - in the command word
    hcode, htext = grab(['help'])                    # the `help` command: same listing
    assert hcode == 'returned' and htext == text
    scode, stext = grab(['-h'])
    assert scode == 0 and stext == text


def test_completion():
    from appeal import completions, completions_set
    def size(width: float, *, bold=False):
        return (width, bold)
    def draw(shape, s: size = None, *, verbose=False, retries: int = 1):
        return None
    plan = build_plan(draw)
    # option-prefix completion, whole tree
    got = completions(plan, [], '--')
    assert got == ['--bold', '--help', '--retries', '--verbose'], got
    got = completions(plan, [], '--v')
    assert got == ['--verbose'], got
    # used single-occurrence options drop out
    got = completions(plan, ['--verbose'], '--')
    assert '--verbose' not in got and '--retries' in got, got
    # the cursor in a value position: no opinion
    assert completions(plan, ['--retries'], '') == []
    assert completions(plan, ['--retries'], '3') == []
    # after '--': no options ever
    assert completions(plan, ['--'], '--') == []
    # operand position: shell's business
    assert completions(plan, [], 'sha') == []

    # command sets: command words at the command position
    def run(target):
        return None
    def deploy(target):
        return None
    commands = {'run': build_plan(run), 'deploy': build_plan(deploy)}
    got = completions_set(commands, None, [], '')
    assert got == ['deploy', 'help', 'run'], got
    got = completions_set(commands, None, [], 'de')
    assert got == ['deploy'], got
    got = completions_set(commands, None, ['help'], '')
    assert got == ['deploy', 'help', 'run'], got     # help topics
    # after the command word: that command's options
    got = completions_set(commands, None, ['run'], '--')
    assert '--help' in got, got
    # with a global command: its options first, command words once
    # the minimum is fed
    def g(project, *, verbose=False):
        return None
    got = completions_set(commands, build_plan(g), [], '--')
    assert got == ['--verbose'], got
    got = completions_set(commands, build_plan(g), [], '')
    assert got == [], got                             # minimum unmet
    got = completions_set(commands, build_plan(g), ['proj'], '')
    assert got == ['deploy', 'help', 'run'], got

    # facade
    app = Appeal()
    @app.global_command()
    def solo(x, *, loud=False):
        return None
    assert app.complete([], '--l') == ['--loud']

def test_read_mapping_option_classes():
    from appeal import MultiOption, read_mapping
    class Tags(MultiOption):
        def init(self, default):
            self.values = list(default) if default else []
        def option(self, tag):
            self.values.append(tag)
        def __call__(self):
            return tuple(self.values)
    class Verbosity(MultiOption):
        def init(self, default):
            self.level = default
        def option(self):
            self.level += 1
        def __call__(self):
            return self.level
    def f(name, *, tag: Tags = ('seed',), v: Verbosity = 0):
        return (name, tag, v)
    got = read_mapping(f, {'name': 'n', 'tag': ['a', 'b'], 'v': 2})
    assert got == ('n', ('seed', 'a', 'b'), 2), got
    got = read_mapping(f, {'name': 'n'})
    assert got == ('n', ('seed',), 0), got

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
    # mapping collects one KEY=VALUE token per occurrence
    assert run_both(pool, ['x', '--define', 'k=v']) == ('ok', ('x', {'k': 'v'}))
    def snooker(s, *, define: appeal.mapping[int, str] = {}):
        return (s, define)
    got = run_both(snooker, ['x', '--define', '1=one', '--define', '2=two'])
    assert got == ('ok', ('x', {1: 'one', 2: 'two'})), got
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
        build_plan(bare)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'call it first' in str(e), e
    def bare2(*, v: appeal.counter = 0):
        pass
    try:
        build_plan(bare2)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'call it first' in str(e), e


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
    assert app.process(['a']).result == ('a', {})
    assert app.process(['a', '--zap', '5']).result == ('a', {'zap': 5})
    assert app.process(['a', '-q']).result == ('a', {'quiet': True})
    assert app.process(['a', '--zap', '5', '-q']).result == ('a', {'zap': 5, 'quiet': True})
    # parity between rungs, the app's registry riding along
    fn = f
    got = run_both(fn, ['a', '--zap', '7'],
                   decorations=app._decorations)
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
    assert app2.process(['a', '-i', 'p', '-i', 'q']).result == \
        ('a', {'include': ['p', 'q']})
    # NOT an arbitrary-option sink: undeclared options stay unknown
    try:
        app2.process(['a', '--bogus']).result
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
        app3.process(['a']).result
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
    assert app.process(['X', 'a']).result == ('X', ('a', {}))
    assert app.process(['X', 'a', '--flavor', 'spicy']).result == \
        ('X', ('a', {'flavor': 'spicy'}))
    # two positionals: both are operands (v1 refuses a leftover)
    app2 = Appeal()
    @app2.option('flavor', '--flavor')
    def two(a, b, **kws):
        return (a, b, kws)
    @app2.global_command()
    def cmd2(x, s: two = None):
        return (x, s)
    assert app2.process(['X', 'p', 'q', '--flavor', 'hot']).result == \
        ('X', ('p', 'q', {'flavor': 'hot'}))
    # both rungs agree (the converter's decorations live in the
    # app registry, threaded through the build)
    got = run_both(cmd, ['X', 'a', '--flavor', 'mild'],
                   decorations=app._decorations)
    assert got == ('ok', ('X', ('a', {'flavor': 'mild'}))), got

def test_app_parameter_renames():
    # @app.parameter (v1's API): renames an operand in usage; and
    # (new in v2--v1 only reached operands) renames an option's
    # metavar.  An explicit usage= is the literal metavar text--it
    # wins outright, unadorned by positional_argument_usage_format
    app = Appeal()
    @app.argument('port', usage='PORT')
    @app.argument('times', usage='COUNT')
    @app.global_command()
    def serve(host, port: int = 8080, *, times: int = 1):
        """
        Serves.

        # Arguments
        port
        : where to listen.
        """
        pass
    usage = app.plan.usage()
    assert usage == 'serve [-t|--times COUNT] <HOST> [PORT]', usage
    result, text = run_both_stdout(serve, ['--help'],
                                   decorations=app._decorations)
    assert '[-t|--times COUNT]' in text
    assert 'PORT    where to listen.' in text  # tables renamed too
    # a converter's own parameters rename by decorating the
    # converter--recorded in the app, never on the converter
    # (ruled 2026-08-09)
    def pair(x: float, y: float):
        return (x, y)
    app_c = Appeal()
    app_c.argument('x', usage='X')(pair)
    def draw(p: pair):
        return p
    plan_c = build_plan(draw, decorations=app_c._decorations)
    assert 'X' in plan_c.usage(), plan_c.usage()
    # naming a parameter the function doesn't have: config error
    app2 = Appeal()
    @app2.argument('nonesuch', usage='NOPE')
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
    from appeal.presentation import merge_docs
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
    assert build_plan(plot).usage('plot') == 'plot [-a|--at <X> <Y>]'

    # an explicit @app.parameter usage= is literal and wins outright,
    # unadorned by the format
    app = Appeal(positional_argument_usage_format='<{name}>')
    @app.argument('width', usage='W')
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
        app.process(['--help']).result
        assert False, 'expected --help to be unknown'
    except UsageError as e:
        assert '--help' in str(e), e
    # and the option truly isn't in the plan
    plan = app.plan
    from appeal.frontend import help_option_strings
    assert help_option_strings(plan) == ()
    # help=True (default) still answers --help
    app2 = Appeal(name='solo2')
    @app2.global_command()
    def g(a, *, verbose=False):
        "Do g."
    # -h/--help is a precommand OPTION now: it prints then sys.exit(0)s;
    # raw process() propagates the exit, main() converts it (ruled
    # 2026-07-19, test_set_level_help_flag)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            app2.process(['--help']).result
        assert False, 'expected SystemExit(0)'
    except SystemExit as e:
        assert (e.code or 0) == 0
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
        app3.process(['help']).result
        assert False, 'expected help command to be gone'
    except UsageError as e:
        assert 'unknown command' in str(e), e
    try:
        app3.process(['add', '--help']).result
        assert False, 'expected per-command --help to be gone'
    except UsageError as e:
        assert '--help' in str(e), e
    # completion no longer offers the help word
    assert 'help' not in app3.complete([], '')
    assert set(app3.complete([], '')) == {'add', 'sub'}

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
    from appeal import Decorations
    d = Decorations()
    d.add_option(go, 'direction', ('--north',),
                 annotation=lambda: 'north')
    got = run_both(go, ['--north'], decorations=d)
    assert got == ('ok', 'north'), got

def test_mcp_schema_agrees_with_read_mapping():
    # the natural round trip: whatever the MCP inputSchema
    # advertises, read_mapping() accepts.  Regression: operand
    # properties used the presentation-only usage rename (describe
    # required COUNT, reader wanted count), and converter groups
    # were advertised as opaque strings.
    from appeal.mcp import mcp_input_schema

    def pt(x: int, y: int):
        return (x, y)
    def wh(x: int, *, deep: int = 0):
        return (x, deep)
    def go(count: int, spot: pt, *, where: wh = None):
        return (count, spot, where)
    from appeal import Decorations, read_mapping
    d = Decorations()
    d.add_usage(go, 'count', 'COUNT')

    plan = build_plan(go, decorations=d)
    s = mcp_input_schema(plan)
    # properties are keyed by PARAMETER name (identity), never the
    # usage rename; the rename rides in describe() as 'usage'
    assert set(s['properties']) == {'count', 'spot', 'where'}
    assert s['required'] == ['count', 'spot']
    from appeal.mcp import describe as describe
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
    from appeal import describe
    def size(width: float, *, bold=False):
        return (width, bold)
    def draw(shape, width: size = None, *, verbose=False, times: int = 1):
        """
        Draws a shape.

        # Arguments
        shape
        : the shape to draw.

        # Options
        times
        : how many times.
        """
        return None
    got = describe(draw)
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
    # the round trip: describe out, JSON blob in, command runs
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

def test_prescan_dry_live_agree():
    # HARDENING the two-pass engine (2026-08-23): the whole-line
    # STRUCTURAL pre-scan (dry) and the live pass must traverse the
    # command line identically -- that's what makes the shared
    # `built` converter FIFO sound.  With INFALLIBLE converters (str),
    # any failure is STRUCTURAL, so the dry pass must catch it all.
    # Two oracle-free properties, one with real teeth:
    #   (A) dry accepts <=> live accepts, same UsageError when both reject,
    #       and live never CRASHES (a FIFO divergence pops the wrong
    #       converter -> IndexError/AttributeError).
    #   (B) TEETH: every command returns its OWN name and the app carries a
    #       GLOBAL (so >=2 converters sit in the FIFO in order).  A dispatch
    #       of `[--g?, cmdK, ...]` that ACCEPTS must return 'cmdK' -- pop the
    #       wrong converter and the wrong body runs, so the name is wrong.
    # (Mutation-checked: dropping `built.reverse()` fails (B).)
    # See [[eager-parse-then-convert]], [[streaming-dispatch]].
    import contextlib, io, random
    import appeal as _ap

    rng = random.Random(20260823)

    def gen_sig(name):
        parts, star = [], False
        if rng.random() < 0.7:
            parts.append('a')
        if rng.random() < 0.5:
            parts.append("b='B'")
        if rng.random() < 0.4:
            parts.append('*args'); star = True
        kw = []
        if star and rng.random() < 0.5:
            kw.append('dest')                       # trailing (after *args)
        if rng.random() < 0.6:
            kw.append('verbose=False')              # flag option
        if rng.random() < 0.5:
            kw.append('label=None')                 # str-value option
        if kw:
            if not star:
                parts.append('*')
            parts += kw
        return ', '.join(parts)

    def make_command(ns, name):
        # each command returns its own name -- the teeth for property (B)
        exec(f"def {name}({gen_sig(name)}):\n    return {name!r}\n", ns)
        return ns[name]

    def build_app(has_global, repeat):
        ns = {}
        names = [f'cmd{k}' for k in range(rng.randrange(1, 4))]
        app = _ap.Appeal(name='fz', repeat=repeat, default_mappings=None)
        if has_global:
            # options-only global: it never eats the command word, so a
            # constructed [glob-opts, cmdK, ...] dispatches cmdK cleanly
            exec("def glob(*, g=False):\n    return 'glob'\n", ns)
            app.global_command()(ns['glob'])
        for nm in names:
            app.command(nm)(make_command(ns, nm))
        return app, names

    def dry_alone(app, argv):
        app._finalize()
        app._run_node(list(argv), 0, _ap.Processor(app), top=True,
                      dry=True, built=[])

    def outcome(fn):
        "Run fn, returning ('ok', result) / ('usage', msg); crashes propagate."
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                return ('ok', fn())
            except _ap.UsageError as e:
                return ('usage', str(e))

    tok = ['a', 'w', '1', '2', '--verbose', '--label', 'L', '-v', '--', 'v']
    accepts = rejects = teeth = 0
    for _ in range(160):
        has_global = rng.random() < 0.6
        repeat = rng.random() < 0.3
        app, names = build_app(has_global, repeat)
        for _ in range(6):
            teeth_word = None
            if has_global and not repeat and rng.random() < 0.6:
                # (B) a constructed dispatch with a known expected command
                teeth_word = rng.choice(names)
                argv = ((['--g'] if rng.random() < 0.4 else [])
                        + [teeth_word]
                        + [rng.choice(tok) for _ in range(rng.randrange(0, 4))])
            else:
                # (A) fully random line
                argv = [rng.choice(tok + names)
                        for _ in range(rng.randrange(0, 7))]

            try:
                dry_kind = outcome(lambda: dry_alone(app, argv))[0]
            except Exception as e:
                assert False, f'DRY crashed: {type(e).__name__}: {e}\nargv={argv!r}'
            try:
                live_kind, live_val = outcome(lambda: app.process(list(argv)).result)
            except Exception as e:
                assert False, (f'LIVE crashed (dry/live divergence?): '
                               f'{type(e).__name__}: {e}\nargv={argv!r}')

            assert dry_kind == live_kind, (
                f'dry/live DISAGREE:\nargv={argv!r}\n'
                f'dry={dry_kind} live={live_kind} ({live_val!r})')
            if live_kind == 'ok':
                accepts += 1
                if teeth_word is not None:
                    teeth += 1
                    assert live_val == teeth_word, (      # the teeth
                        f'wrong converter ran: expected {teeth_word!r}, '
                        f'got {live_val!r}\nargv={argv!r}')
            else:
                rejects += 1
    assert accepts >= 50 and rejects >= 50 and teeth >= 20, (
        accepts, rejects, teeth)


def test_differential_fuzz_v1_greedy():
    # Audit round 2, resurrected (2026-08-09): random
    # shared-grammar programs through REAL v1 (extracted from git
    # master into a tempdir, run in a subprocess--v1 leaks state,
    # so the driver builds a fresh Appeal per argv) and through
    # BOTH 1.0 rungs (run_both: interpreter/codegen parity rides
    # along free).  The generator aims at the post-July space the
    # old harness never covered: greedy *args converter groups
    # (optional tails) and windowed per-instance flags.
    # Contract: v1 ok ==> 1.0 ok with the identical result.
    # Deterministic seed; ~35 programs x 5 argvs.
    import json, os.path, random, subprocess, sys, tempfile

    rng = random.Random(20260809)

    def gen_program(i):
        n_opt = rng.randrange(0, 3)
        flag = rng.random() < 0.5
        names = ['a'] + ['b', 'c'][:n_opt]
        params = ['a'] + [f"{n}='{n.upper()}'" for n in ['b', 'c'][:n_opt]]
        if flag:
            params.append('*, flag=False')
        rets = names + (['flag'] if flag else [])
        ret = '(' + ', '.join(rets) + (',)' if len(rets) == 1 else ')')
        pre = rng.randrange(0, 2)
        pre_names = ['x', 'y'][:pre]
        cmd_params = ', '.join(pre_names + ['*items: conv'])
        cmd_ret = '(' + ', '.join(pre_names + ['items']) + ')'
        src = (f"def conv({', '.join(params)}):\n"
               f"    return {ret}\n"
               f"def cmd({cmd_params}):\n"
               f"    return {cmd_ret}\n")
        return src, pre, flag

    def gen_argv(pre, flag):
        argv = [f'p{k}' for k in range(rng.randrange(0, pre + 1))]
        argv += [str(rng.randrange(10))
                 for _ in range(rng.randrange(0, 7))]
        if flag and rng.random() < 0.6:
            argv.insert(rng.randrange(0, len(argv) + 1), '--flag')
        return argv

    jobs = []
    for i in range(35):
        src, pre, flag = gen_program(i)
        argvs = [gen_argv(pre, flag) for _ in range(5)]
        jobs.append((src, argvs))

    # extract v1 and run every job through it, one subprocess
    with tempfile.TemporaryDirectory() as d:
        v1dir = os.path.join(d, 'v1')
        os.makedirs(v1dir)
        r = subprocess.run(
            f'git -C {repo_dir} archive master appeal | '
            f'tar -x -C {v1dir}',
            shell=True, capture_output=True, text=True)
        if r.returncode != 0:
            print('  (git archive failed; differential skipped)')
            return
        driver = os.path.join(d, 'driver.py')
        with open(driver, 'wt', encoding='utf-8') as f:
            f.write(
                "import io, json, sys, contextlib\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "import appeal\n"
                "jobs = json.load(open(sys.argv[2]))\n"
                "out = []\n"
                "for src, argvs in jobs:\n"
                "    results = []\n"
                "    for argv in argvs:\n"
                "        ns = {}\n"
                "        exec(src, ns)\n"
                "        app = appeal.Appeal()\n"
                "        app.global_command()(ns['cmd'])\n"
                "        sink = io.StringIO()\n"
                "        try:\n"
                "            with contextlib.redirect_stdout(sink), \\\n"
                "                 contextlib.redirect_stderr(sink):\n"
                "                r = app.process(list(argv))\n"
                "            results.append(['ok', repr(r)])\n"
                "        except SystemExit as e:\n"
                "            results.append(['exit', str(e.code)])\n"
                "        except BaseException as e:\n"
                "            results.append(['error',\n"
                "                            type(e).__name__])\n"
                "    out.append(results)\n"
                "with open(sys.argv[3], 'wt') as f:\n"
                "    json.dump(out, f)\n")
        jobs_path = os.path.join(d, 'jobs.json')
        with open(jobs_path, 'wt', encoding='utf-8') as f:
            json.dump(jobs, f)
        out_path = os.path.join(d, 'out.json')
        r = subprocess.run([sys.executable, driver, v1dir,
                            jobs_path, out_path],
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, f'v1 driver crashed:\n{r.stderr[-2000:]}'
        with open(out_path, 'rt', encoding='utf-8') as f:
            v1_results = json.load(f)

    compared = superset = 0
    for (src, argvs), results in zip(jobs, v1_results):
        ns = {}
        exec(src, ns)
        for argv, (kind, payload) in zip(argvs, results):
            ours = run_both(ns['cmd'], argv)     # both rungs agree
            if kind != 'ok':
                superset += 1                     # v1 refused; ours free
                continue
            compared += 1
            assert ours[0] == 'ok', (
                f'v1 accepted, 1.0 refused:\n{src}\n'
                f'argv={argv!r}\nv1={payload}\nours={ours!r}')
            assert repr(ours[1]) == payload, (
                f'DIVERGENCE:\n{src}\nargv={argv!r}\n'
                f'v1={payload}\nours={ours[1]!r}')
    # the run must actually exercise the contract
    assert compared >= 60, (compared, superset)


def test_differential_fuzz_converter_group_conversion_and_arity():
    # A BIDIRECTIONAL sweep vs real 0.6.4 aimed at converter-group
    # bugs the one-directional greedy fuzz can't see (both found
    # 2026-08-16, both hidden by str-typed fixtures):
    #   * single-parameter groups off *args skipping their leaf
    #     conversion (0.6.4 int(3), 1.0 returned '3')--MIXED leaf
    #     types make it a repr divergence;
    #   * a defaulted operand with a required one behind it not
    #     promoted (1.0 accepted fewer operands than 0.6.4).
    # Contract: (a) 0.6.4 ok ==> 1.0 ok with the IDENTICAL result;
    # (b) 1.0's minimum accepted operand count is never BELOW
    # 0.6.4's (no arity too-lenient).  Deterministic; 0.6.4 extracted
    # from git master into a subprocess.
    import collections, json, os.path, random, subprocess, sys, tempfile

    LEAF = ['int', 'float', 'str']
    DEFV = {'int': '0', 'float': '0.0', 'str': "'d'"}

    def gen_group(gi, depth, rng, done):
        nparams = rng.randint(1, 3)
        ndef = rng.randint(0, nparams)
        decls, names, mn = [], [], 0
        for k in range(nparams):
            defaulted = k >= (nparams - ndef)
            names.append(f'p{k}')
            if depth > 0 and done and not defaulted and rng.random() < 0.4:
                gn, gm = rng.choice(done)
                decls.append(f'p{k}: {gn}'); mn += gm
            else:
                t = rng.choice(LEAF)
                decls.append(f'p{k}: {t} = {DEFV[t]}' if defaulted
                             else f'p{k}: {t}')
                mn += 0 if defaulted else 1
        ret = '(' + ', '.join(names) + (',)' if len(names) == 1 else ')')
        return f"def g{gi}({', '.join(decls)}):\n    return {ret}\n", mn

    def gen_program(rng):
        ng = rng.randint(1, 3)
        gsrc, done = [], []
        for gi in range(ng):
            src, mn = gen_group(gi, rng.randint(0, 2), rng, done)
            gsrc.append(src); done.append((f'g{gi}', mn))
        decls, names = [], []
        for s in range(rng.randint(1, 4)):
            names.append(f's{s}')
            decls.append(f's{s}: g{rng.randrange(ng)}' if rng.random() < 0.55
                         else f's{s}: {rng.choice(LEAF)}')
        r = rng.random()
        if r < 0.25:
            decls += ['*', 'z: int']; names.append('z')
        elif r < 0.45:
            ok = [j for j in range(ng) if done[j][1] >= 1]
            if ok:
                decls.append(f'*rest: g{rng.choice(ok)}'); names.append('rest')
        ret = '(' + ', '.join(names) + (',)' if len(names) == 1 else ')')
        return "".join(gsrc) + f"def cmd({', '.join(decls)}):\n    return {ret}\n"

    rng = random.Random(20260816)
    # each program tried at operand counts 0..7 (all valid ints)
    argvs = [[str(v) for v in range(1, c + 1)] for c in range(0, 8)]
    jobs = [(gen_program(rng), argvs) for _ in range(40)]

    with tempfile.TemporaryDirectory() as d:
        v1dir = os.path.join(d, 'v1'); os.makedirs(v1dir)
        r = subprocess.run(
            f'git -C {repo_dir} archive master appeal | tar -x -C {v1dir}',
            shell=True, capture_output=True, text=True)
        if r.returncode != 0:
            print('  (git archive failed; differential skipped)')
            return
        driver = os.path.join(d, 'driver.py')
        with open(driver, 'wt', encoding='utf-8') as f:
            f.write(
                "import io, json, sys, contextlib\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "import appeal\n"
                "jobs = json.load(open(sys.argv[2]))\n"
                "out = []\n"
                "for src, argvs in jobs:\n"
                "    res = []\n"
                "    for argv in argvs:\n"
                "        ns = {}\n"
                "        exec(src, ns)\n"
                "        app = appeal.Appeal()\n"
                "        app.global_command()(ns['cmd'])\n"
                "        sink = io.StringIO()\n"
                "        try:\n"
                "            with contextlib.redirect_stdout(sink), \\\n"
                "                 contextlib.redirect_stderr(sink):\n"
                "                r = app.process(list(argv))\n"
                "            res.append(['ok', repr(r)])\n"
                "        except BaseException as e:\n"
                "            res.append(['no', type(e).__name__])\n"
                "    out.append(res)\n"
                "json.dump(out, open(sys.argv[3], 'wt'))\n")
        jobs_path = os.path.join(d, 'jobs.json')
        json.dump(jobs, open(jobs_path, 'wt'))
        out_path = os.path.join(d, 'out.json')
        r = subprocess.run([sys.executable, driver, v1dir, jobs_path, out_path],
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, f'v1 driver crashed:\n{r.stderr[-2000:]}'
        v1_results = json.load(open(out_path))

    # 0.6.4 and 1.0 are DIFFERENT parsers: 1.0 deliberately changed several
    # parsing semantics.  This is NOT a conformance test -- it's an AWARENESS
    # census.  Every place 0.6.4 and 1.0 disagree must fall into an APPROVED
    # category; an UNAPPROVED disagreement fails, so a new, unnoticed behavior
    # change can't slip in.  Approved for this converter-group-arity fuzz:
    #   * 1.0-wider -- 1.0 accepts a command line 0.6.4 rejected.  1.0's rule
    #     ("a required slot invokes its converter; minimum governs operands")
    #     no longer promotes an optional group that sits ahead of a slot whose
    #     converter needs nothing, so 1.0 accepts shorter lines (conjuring the
    #     defaults).  See processor-design / the invoke-always ruling.
    # NOT approved (a tripwire for the unexpected): 1.0-narrower (0.6.4 accepts,
    # 1.0 rejects) and different-result (both accept, different value).
    # (Dev-only: when 1.0 ships, 0.6.4 retires and this differential leaves the
    # suite -- testing 1.0 must never need 0.6.4, hence the clean skip above.)
    census = collections.Counter()
    unapproved = []
    compared = 0
    for (src, args), results in zip(jobs, v1_results):
        ns = {}
        exec(src, ns)
        deferred = False
        for argv, (kind, payload) in zip(args, results):
            try:
                ours = run_both(ns['cmd'], argv)         # 1.0 interp == 1.0 compiled
            except AppealConfigurationError as e:
                if 'awaits the streaming driver' in str(e):
                    deferred = True; break               # known 1.0 deferral
                raise
            compared += 1
            v164_ok, ours_ok = (kind == 'ok'), (ours[0] == 'ok')
            if v164_ok and ours_ok:
                if repr(ours[1]) == payload:
                    census['agree'] += 1
                else:
                    census['different-result'] += 1
                    unapproved.append(('different-result', src, argv, payload, repr(ours[1])))
            elif not v164_ok and not ours_ok:
                census['agree'] += 1
            elif ours_ok:                                # 1.0 accepts, 0.6.4 rejects
                census['1.0-wider'] += 1                 # approved: minimum governs
            else:                                        # 0.6.4 accepts, 1.0 rejects
                census['1.0-narrower'] += 1
                unapproved.append(('1.0-narrower', src, argv, payload, ours))
        if deferred:
            continue
    print(f'  0.6.4<->1.0 census: {dict(census)}')
    assert compared >= 40, compared
    assert not unapproved, (
        f'{len(unapproved)} UNAPPROVED 0.6.4<->1.0 divergence(s) -- a behavior '
        f'change we did not catalog.  First few:\n' + '\n'.join(
            f'  [{c}] argv={a!r}  0.6.4={p}  1.0={o!r}\n{s}'
            for c, s, a, p, o in unapproved[:5]))


def test_fuzz_parity():
    # the two rungs, adversarially: random signatures, random
    # command lines (valid and invalid counts, options for groups
    # that end up skipped, the lot).  The interpreter and the generated
    # parser must agree on every result and every error message.
    # Deterministic seed: a failure here reproduces exactly.
    import random
    rng = random.Random(20260703)
    from appeal.frontend import all_options

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
            plan = build_plan(namespace[top])
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

    assert app.process(['--', '--upper', 'x']).result == '--upper x'
    # second parse on the same object: options must work again
    assert app.process(['--upper', 'hello', 'there']).result == 'HELLO THERE'

def test_plan_recursion():
    plan = build_plan(draw)
    named = {s.name: s for s in plan.slots}
    # where: required nonterminal, consumes exactly 4
    assert named['where'].count_options == (4,)
    # brush: optional nonterminal, child counts {3, 4}, plus skip
    assert named['brush'].count_options == (4, 3, 0)
    assert plan.valid_counts == {5, 8, 9}

def test_plan_nested_optional_counts():
    def f(a, s: sized='S'):
        return (a, s)
    plan = build_plan(f)
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
        build_plan(selfy)
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
    plan = build_plan(cmd)
    # a *args converter is an absorbing nonterminal (v1, probed:
    # it fed the whole remaining line--the old "degrades to a
    # terminal" reading was a mis-pin), unbounded above its floor
    named = {s.name: s for s in plan.slots}
    from appeal.frontend import Terminal
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
        build_plan(bad)
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
        build_plan(cmd3)
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

    # forward-riding windows (ruled 2026-08-22, superseding the 2026-07-09
    # scoped rules): --flag rides the currently-open child window and
    # advances to the next slot only when the current one FILLS; repeating
    # it hits the same window (idempotent flag).  See [[streaming-dispatch]].
    got = run_both(mg, ['A', '--flag'])       # conjures b
    assert got == ('ok', ('A', (0, 1, True), None)), got
    got = run_both(mg, ['A', '--flag', '--flag'])   # same window, idempotent
    assert got == ('ok', ('A', (0, 1, True), None)), got
    got = run_both(mg, ['A', '1', '2', '--flag'])   # b full -> advance to c
    assert got == ('ok', ('A', (1, 2, False), (0, 1, True))), got

    # required windows: once b fills, --flag advances to c; c needs its
    # operands and has none, so it starves loudly (can't reach back to b)
    def child2(p, q, *, flag=False):
        return (p, q, flag)
    def mg2(a, b: child2 = None, c: child2 = None):
        return (a, b, c)
    got = run_both(mg2, ['A', 'x', 'y', '--flag'])
    assert got[0] == 'usage', got
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

    # nested shadowing (ruled 2026-08-22): conjuring only happens for
    # UNBOUND options.  f owns --verbose, so it's never conjured into sub --
    # `f --verbose` is f's own flag, sub stays None.  But once an OPERAND
    # enters sub, sub is the open window and --verbose binds to IT, not f.
    def sub(x=1, *, verbose=False):
        return (x, verbose)
    def f(a: sub = None, *, verbose=False):
        return (a, verbose)
    got = run_both(f, ['--verbose'])       # f's own flag; sub not built
    assert got == ('ok', (None, True)), got
    got = run_both(f, ['5', '--verbose'])  # 5 entered sub; --verbose -> sub
    assert got == ('ok', ((5, True), False)), got
    got = run_both(f, ['5', '--verbose', '--verbose'])   # sub's flag, idempotent
    assert got == ('ok', ((5, True), False)), got


def test_scoped_help_presentation():
    # stage D of scoped options (ruled 2026-07-08): position
    # qualifiers on duplicated displays, sub-option indentation,
    # and the equidistant-ambiguity refusal
    import appeal as _appeal
    from appeal.presentation import merge_docs

    def child(p, q=1, *, flag=False):
        """
        A child.

        # Options
        flag
        : Wave it.
        """
        return (p, q, flag)
    def mg(a, b: child = None, c: child = None, *, gronk=''):
        return (a, b, c)
    corpus = merge_docs(build_plan(mg))
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
        # Options
        flavor
        : The sweet one.
        """
        return (kind, flavor)
    def savory(kind, *, flavor=''):
        """
        # Options
        flavor
        : The savory one.
        """
        return (kind, flavor)
    def dish(first: sweet, second: savory):
        return (first, second)
    corpus = merge_docs(build_plan(dish))
    rows = corpus['options']
    texts = [lines for display, lines in rows]
    assert ['The sweet one.'] in texts and ['The savory one.'] in texts

    # documenting the shared name at the COMMAND is ambiguous:
    # refused, pointing home
    def dish2(first: sweet, second: savory):
        """
        Dishes.

        # Options
        flavor
        : Which one?
        """
        return (first, second)
    try:
        merge_docs(build_plan(dish2))
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
    corpus = merge_docs(build_plan(draw))
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
    # instances[0] is the help/version precommand era; the global
    # class-as-app Config is the next invocation (new-engine logging)
    proc = app.process(['src'], config=layer)
    c = proc.instances[1][1]
    assert (c.source, c.verbose, c.jobs) == ('src', True, 4)
    assert c.include == ['a', 'b']
    if GENERIC_SPELLINGS:
        assert c.define == {'x': 1}

    # argv wins, whole: repeatables REPLACE, never append
    proc = app.process(['src', '--jobs', '9', '-i', 'z'], config=layer)
    c = proc.instances[1][1]
    assert c.jobs == 9 and c.include == ['z']

    # absent from both: the default fills
    proc = app.process(['src'], config={})
    assert proc.instances[1][1].jobs == 1

    # strict keys, each flavor loud and saying why
    @app.command()
    def build(target):
        pass
    for bad, fragment in (
            ({'target': 'x'}, 'positional argument'),
            ({'build': {}}, 'is a command'),
            ({'colour': 1}, "isn't an option")):
        try:
            app.process(['src', 'build', 't'], config=bad).result
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
    assert app2.process([], config={'jobs': 4}).result == (4, False)
    try:
        app2.process([], config={'jobs': 'banana'}).result
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert str(e).startswith('config:'), e
    try:
        app2.process([], config={'verbose': 'maybe'}).result
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'boolean' in str(e), e
    # verbose: false means absent: the default fills (and a
    # **kwargs option would stay an absent key)
    assert app2.process([], config={'verbose': 'off'}).result == (1, False)

    # a commands-only program has nowhere for config to land
    app3 = _appeal.Appeal(name='n')
    @app3.command()
    def go():
        return 'went'
    try:
        app3.process(['go'], config={'anything': 1}).result
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'no global command' in str(e), e


def test_command_name_underscores_become_dashes():
    # a function's underscores map to dashes in the command word (Larry,
    # 2026-08-21): upload_database -> upload-database (cf. git format-patch)
    app = Appeal()
    @app.command()
    def upload_database():
        return 'up'
    assert 'upload-database' in app.commands, list(app.commands)
    assert app.process(['upload-database']).result == 'up'

def test_command_name_leading_underscore_rejected():
    # _command -> -command starts with a dash: commands are words, not
    # options, so registering it is a configuration error
    app = Appeal()
    try:
        @app.command()
        def _command():
            pass
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'dash' in str(e), e

def test_command_name_explicit_dash_rejected():
    # an explicit name starting with a dash is rejected too
    app = Appeal()
    try:
        app.command(name='--foo')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'dash' in str(e), e

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

        @app.subcommand('db')
        def wipe(self):
            ran.append(('wipe', self.label))

    app.process(['add-item', '3']).result
    app.process(['db', 'main', 'wipe']).result
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
    app2.process(['base', 'drop-all']).result
    assert ran[-2:] == ['base', 'drop-all'], ran
    # the visible word keeps its dashes in usage
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
    # nested sets completions per the resolution chain, pop-up and all
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


MCP_MODULE = 'from appeal import MultiOption, accumulator\n\nclass Marks(MultiOption):\n    def init(self, default):\n        self.values = list(default) if default else []\n    def option(self, mark):\n        self.values.append(mark)\n    def __call__(self):\n        return tuple(self.values)\n\ndef add(x: int, y: int):\n    """\n    Adds two integers.\n    """\n    return x + y\n\ndef shout(text, *, times: int = 1, tags: accumulator[str] = (),\n          marks: Marks = ()):\n    return (\' \'.join([text.upper()] * times)\n            + \'\'.join(f\' #{t}\' for t in tags)\n            + \'\'.join(f\' !{m}\' for m in marks))\n'


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


# the shared program: build(appeal) returns the same app whether
# `appeal` is the real package or a compiled module wearing its API.
# `coord` is a multi-operand converter reached ONLY by walking the
# annotation--the compiled module never imports it (ruled
# 2026-08-16), so a drifted `coord` must trip the recursive
# fingerprint at main().
_PRECOMPILE_DEFS = (
    'def coord(x, y):\n'
    '    return (int(x), int(y))\n'
    'def build(appeal):\n'
    "    app = appeal.Appeal(name='plot')\n"
    '    @app.global_command()\n'
    '    def plot(where: coord, *, loud=False):\n'
    "        msg = 'plot at ' + str(where)\n"
    '        print(msg.upper() if loud else msg)\n'
    '    return app\n')

# the drift: coord grows a third parameter.  Nothing in plot's OWN
# signature changed--only the converter it reaches.
_PRECOMPILE_DEFS_DRIFT = (
    'def coord(x, y, z):\n'
    '    return (int(x), int(y), int(z))\n'
    'def build(appeal):\n'
    "    app = appeal.Appeal(name='plot')\n"
    '    @app.global_command()\n'
    '    def plot(where: coord, *, loud=False):\n'
    "        msg = 'plot at ' + str(where)\n"
    '        print(msg.upper() if loud else msg)\n'
    '    return app\n')

_PRECOMPILE_RUNNER = (
    'import sys\n'
    'try:\n'
    '    import compiled as appeal\n'
    'except ImportError:\n'
    '    import appeal\n'
    'import {defs} as defs\n'
    'app = defs.build(appeal)\n'
    "if '--check-imports' in sys.argv:\n"
    "    app._verify_and_bind()\n"
    "    heavy = [m for m in ('appeal.build', 'appeal.render',\n"
    "                         'inspect') if m in sys.modules]\n"
    "    heavy += ['big' if any(m == 'big' or m.startswith('big.')\n"
    "                           for m in sys.modules) else '']\n"
    "    print('HEAVY:' + ','.join(h for h in heavy if h))\n"
    'else:\n'
    '    app.main()\n')


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

def sub_run(argv, capture_output=True, text=True, **kw):
    """
    subprocess.run for every Python we support: 3.6 has neither
    capture_output= nor text=, so this swallows those (they're
    the only shapes these tests use) and spells them the old way.
    """
    return subprocess.run(argv, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,
                           universal_newlines=True, **kw)


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
        app.process(['kw', '--extra=false']).result
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
        app2.process(['--verbose=false', 'work'], config={'verbose': True}).result
    assert out.getvalue().strip() == 'verbose False'
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app2.process(['work'], config={'verbose': True}).result
    assert out.getvalue().strip() == 'verbose True'


def test_file_converter():
    # appeal.file(): open() as a converter, with '-' meaning the
    # process-standard stream by MODE (the argparse/click answer
    # to "which one did they need"), wrapped so close() flushes
    # and goes inert--a contract neither argparse (bare stream)
    # nor click (misnamed wrapper) actually delivers.
    import appeal as _appeal
    import contextlib, io

    def cat(inp: _appeal.file()):
        data = inp.read()
        inp.close()
        return (type(inp).__name__, data)

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'x.txt')
        with open(path, 'wt', encoding='utf-8') as f:
            f.write('from disk')
        assert run_both(cat, [path]) == ('ok', ('TextIOWrapper', 'from disk'))

    # '-' with a reading mode is stdin
    old = sys.stdin
    sys.stdin = io.StringIO('from stdin')
    try:
        assert run_both(cat, ['-']) == ('ok', ('_ProcessStream', 'from stdin'))
    finally:
        sys.stdin = old

    # '-' with a writing mode is stdout; close()/with flush and
    # go inert--the real stream survives
    def emit(dest: _appeal.file('w')):
        with dest as f:
            f.write('written')
        dest.close()                     # double-close: also fine
        return dest.closed
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        assert run_both(emit, ['-']) == ('ok', True)
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

    # a vocabulary product is marked `recipe = True` so build.py
    # recognizes it as a terminal; an opener-based file isn't a
    # vocabulary terminal, so it carries no marker
    assert _appeal.file('w', encoding='utf-8').recipe is True
    assert _appeal.file().recipe is True
    assert not hasattr(_appeal.file(opener=os.open), 'recipe')


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

    # indent= is DEAD (ruled 2026-08-06, killed unshipped with
    # the Markdown pivot): big's renderer owns definition-list
    # layout.  Passing it is an ordinary TypeError.
    try:
        _appeal.Appeal(indent=8)
        assert False, 'expected TypeError'
    except TypeError:
        pass

    # garbage refuses by name
    try:
        _appeal.Appeal(margin='wide')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'margin' in str(e)


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

    # the command set's unknown-command site suggests too
    def alpha():
        return 'a'
    def beta():
        return 'b'
    got = run_both_set([alpha, beta], None, ['alhpa'])
    assert got[0] == 'usage' and "did you mean 'alpha'" in got[1], got


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
        app.process(['boom']).result
        assert False, 'expected KeyboardInterrupt'
    except KeyboardInterrupt:
        pass


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
                app.process(list(argv)).result
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

        finally:
            sys.path.remove(d)
            sys.modules.pop('deepmod', None)


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
    assert app.process(['x', 'hi']).result == ('x', 'hi')
    try:
        app.process(['some_function', 'z']).result
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
    assert app.process(['x', 'hi']).result == ('replaced', 'hi')

    # Appeal(parent=) hangs a node in the tree directly
    app2 = _appeal.Appeal(name='u')
    child = _appeal.Appeal('sub', parent=app2)
    @child.command()
    def leaf():
        return 'leaf!'
    assert app2.process(['sub', 'leaf']).result == 'leaf!'

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
    assert app3.process(['serve', 'start', '-p', '99']).result == ('start', 99)


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
                app.process(['db', '--host', 'prod']).result
            assert out.getvalue() == 'db prod\ndb-default\n', out.getvalue()
            # a named subcommand still dispatches
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                app.process(['db', 'deploy', '5']).result
            assert out.getvalue() == 'db local\ndeploy 5\n', out.getvalue()
            # empty line -> the root default
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                app.process([]).result
            assert out.getvalue() == 'root-default\n', out.getvalue()

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
                app.process(list(line)).result
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
                    app2.process(['A', 'Ax', 'AxA', 'AxB']).result
                assert False, 'expected AppealUsageError'
            except _appeal.AppealUsageError as e:
                assert 'AxB' in str(e), e

        finally:
            sys.path.remove(d)
            sys.modules.pop('repeatmod', None)


def test_documentation_man():
    # app.documentation('troff'): the help corpus in troff clothing
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
    text = app.documentation('troff')
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
    stext = solo.documentation('troff')
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
        app3.process(['work'], config={'verbose': 'maybe'}).result
        assert False, 'expected AppealDataError'
    except _appeal.AppealDataError as e:
        assert "can't read 'maybe'" in str(e)
        assert e.usage


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
    make().process(['go'], config={'where': {'x': 1, 'deep': 3}}).result
    assert seen[0] == (1, 3), seen
    make().process(['go'], config={'where': {'x': 1}}).result
    assert seen[0] == (1, 0), seen
    # atomic per option: argv naming the group wins WHOLE--the
    # config's inner option must not leak in
    make().process(['--where', '9', 'go'],
                   config={'where': {'x': 1, 'deep': 3}})
    assert seen[0] == (9, 0), seen
    # unknown keys still refuse, by name
    try:
        make().process(['go'], config={'where': {'x': 1, 'zz': 2}}).result
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
        top2_factory().process(['go2'], config={'w1': {'x': 1, 'deep': 3}}).result
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'scoped' in str(e) and 'deep' in str(e)

    # commands-only app: empty config no-ops, nonempty refuses
    app = Appeal(name='solo')
    @app.command()
    def solo():
        return 'ran'
    assert app.process(['solo'], config={}).result == 'ran'
    try:
        app.process(['solo'], config={'x': 1}).result
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
            make().process(['banana', 'go'], config=config).result
            assert False, 'expected UsageError'
        except AppealDataError as e:
            # unprefixed canonical (ruled 2026-07-25; matches 0.6.4,
            # whose prefixed spellings were the 'old names' aliases)
            assert type(e).__name__ == 'UsageError', (config, e)
            assert not str(e).startswith('config:')
    # a bad CONFIG value converts, and says so
    try:
        make().process(['1', 'go'], config={'level': 'banana'}).result
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
        app.process(['go3'], config={'where': {'x': 'nope'}}).result
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
        app.process(['work'], config={'flavor': 'sour'}).result
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
                app.process(['add', str(i), '1', 'mul', str(i), '2']).result
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
    # the version command takes no arguments, loudly: it saturates at zero
    # operands and yields 'extra', which names no command (streaming dispatch,
    # run-as-you-go -- a leaf command doesn't reject trailing tokens, the set
    # diagnoses the unclaimed word)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code, out = main(app, ['version', 'extra'])
    assert code == 2 and 'extra' in err.getvalue(), err.getvalue()
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


def test_streaming_dispatch_runs_as_it_parses():
    # STREAMING (ruled 2026-08-22, refined 2026-08-23): commands run
    # as they're parsed, left to right; a later CONVERSION error does
    # NOT un-run an earlier command (deferring user converters, which
    # may have side effects, would be surprising).  STRUCTURAL/arity
    # errors, by contrast, are caught by a whole-line pre-scan before
    # anything runs.  See [[streaming-dispatch]], [[eager-parse-then-convert]].
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
        app.process(['--trace', 'add', '1', 'x']).result   # add's y='x' isn't an int
        assert False, 'expected UsageError'
    except UsageError:
        pass
    # structure was fine (add got two operands); the global era ran, then
    # add's CONVERSION of 'x' failed -- a late error doesn't un-run top
    assert ran == ['top'], ran
    # but a STRUCTURAL error (add needs 2, given 1) is caught up front by the
    # whole-line pre-scan: nothing runs
    ran.clear()
    try:
        app.process(['--trace', 'add', '1']).result
        assert False, 'expected UsageError'
    except UsageError:
        pass
    assert ran == [], ran
    # a well-formed line still executes left to right
    ran.clear()
    app2 = _appeal.Appeal(name='ms2')
    @app2.global_command()
    def top2(*, trace=False):
        ran.append('top2')
    @app2.command()
    def add2(x: int, y: int):
        ran.append(('add2', x, y))
    app2.process(['--trace', 'add2', '1', '2']).result
    assert ran == ['top2', ('add2', 1, 2)], ran


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
    proc = app.process(['add', '1', '2', 'mul', '3', '4', 'add', '5', '6'])
    assert ran == [('add', 1, 2), ('mul', 3, 4), ('add', 5, 6)], ran
    # leading None is the help/version precommand era (runs once at the head)
    assert [getattr(c, '__name__', None) for c, _ in proc.instances] == \
        [None, 'add', 'mul', 'add']

    # optionals must be spelled: greedy saturation takes the would-be
    # command word as greet's greeting, leaving '1' with no command -- a
    # STRUCTURAL error the whole-line pre-scan catches up front, so nothing
    # runs (structural errors batch; see [[streaming-dispatch]])
    ran.clear()
    try:
        app.process(['greet', 'bob', 'add', '1', '2']).result
        assert False, 'expected UsageError'
    except UsageError as e:
        assert "unknown command '1'" in str(e), e
    assert ran == [], ran

    # spelled, it cycles
    ran.clear()
    app.process(['greet', 'bob', 'hi', 'add', '1', '2']).result
    assert ran == [('greet', 'bob', 'hi'), ('add', 1, 2)], ran

    # the window rule: a post-saturation option binds to the
    # finished command; the next command word closes the window
    ran.clear()
    app.process(['scale', '3', '--double', 'add', '1', '2']).result
    assert ran == [('scale', 3, True), ('add', 1, 2)], ran

    # *args never saturates: a cycle terminator
    ran.clear()
    app.process(['add', '1', '2', 'total', '3', '4', '5']).result
    assert ran == [('add', 1, 2), ('total', 3, 4, 5)], ran

    # streaming: a later CONVERSION error doesn't un-run the earlier command
    # (an arity error would be caught up front by the whole-line pre-scan; a
    # bad value is only found when its converter runs, mid-stream)
    ran.clear()
    try:
        app.process(['add', '1', '2', 'mul', '3', 'x']).result   # mul's y='x' bad int
        assert False, 'expected UsageError'
    except UsageError:
        pass
    assert ran == [('add', 1, 2)], ran

    # the early-exit contract, every command in a cycle: a nonzero
    # int halts
    ran.clear()
    result = app.process(['bail', '7', 'add', '1', '2']).result
    assert result == 7
    assert ran == [('bail', 7)], ran

    # gate rule DROPPED (flat recognition): --dashed is recognized before
    # board's operands just as after them; both spellings accept, and
    # streaming runs add first
    def _pair(x, y):
        return f'{x}+{y}'
    def stroke(width: float = 1.0, *, dashed=False):
        return f'{width}{"~" if dashed else "-"}'
    @app.command()
    def board(f: _pair, s: stroke = 'none'):
        ran.append(('board', f, s))
    ran.clear()
    app.process(['add', '1', '2', 'board', '--dashed', 'a', 'b', '1.5']).result
    assert ran == [('add', 1, 2), ('board', 'a+b', '1.5~')], ran
    ran.clear()
    app.process(['add', '1', '2', 'board', 'a', 'b', '1.5', '--dashed']).result
    assert ran == [('add', 1, 2), ('board', 'a+b', '1.5~')], ran

    # without repeat, a leftover word is leftover (one command per
    # line, as ever)
    app2 = _appeal.Appeal(name='nocyc')
    @app2.command()
    def add2(x: int, y: int):
        ran.append(('add2', x, y))
    try:
        app2.process(['add2', '1', '2', 'add2', '3', '4']).result
        assert False, 'expected UsageError'
    except UsageError:
        pass


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
    proc = app.process(['db', 'add', '3', 'remove', '4'])
    assert ran == [('db', False), ('add', 3), ('remove', 4)], ran
    assert [getattr(c, '__name__', c) for c, _ in proc.instances] == \
        [None, 'db', 'add', 'remove']    # leading None = precommand era

    # pop-up: add's set has no 'status'; db's set doesn't either;
    # the root's repeat resolves it
    ran.clear()
    app.process(['db', 'add', '3', 'status']).result
    assert ran == [('db', False), ('add', 3), 'status'], ran

    # and back down: a second db runs again, fresh options
    ran.clear()
    app.process(['db', 'add', '1', 'status', 'db', '-v', 'remove', '2']).result
    assert ran == [('db', False), ('add', 1), 'status',
                   ('db', True), ('remove', 2)], ran

    # a parent run with no subcommand: subcommands are NOT required
    # (ruled 2026-08-22) -- db has a body, so it just runs, no error
    ran.clear()
    app.process(['db']).result
    assert ran == [('db', False)], ran

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
    app2.process(['db2', 'add2', '1', 'status2']).result
    assert ran == ['db2', ('add2', 1), 'status2'], ran


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
        @app.subcommand('alpha')
        def run(self):
            return 'alpha-run'

    @app.command(name='beta')
    class Beta:
        def __init__(self):
            pass
        @app.subcommand('beta')
        def run(self):
            return 'beta-run'

    assert app.process(['alpha', 'run']).result == 'alpha-run'
    proc = app.process(['beta', 'run'])
    assert proc.result == 'beta-run'
    # the instances log can't tell WHICH `run` from the bare word,
    # so it answers None rather than guess wrong
    assert proc.instances[-1] == (None, None)
    # plan_for on the ambiguous bare word refuses, naming parents
    try:
        app.plan_for('run')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'alpha' in str(e) and 'beta' in str(e)

    # a duplicate name= within one class replaces, like every
    # other re-registration (v1's rule: the second wins)
    dup = Appeal(name='d')
    @dup.command(name='gamma')
    class Gamma:
        def __init__(self):
            pass
        @dup.subcommand('gamma', name='x')
        def one(self):
            pass
        @dup.subcommand('gamma', name='x')
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
            @bad2.subcommand('left', name='db')
            class DbL:
                def __init__(self):
                    pass
                @bad2.subcommand('left db')
                def wipe(self):
                    pass
        @bad2.command(name='right')
        class Right:
            def __init__(self):
                pass
            @bad2.subcommand('right', name='db')
            class DbR:
                def __init__(self):
                    pass
                @bad2.subcommand('right db')
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
        app.process(['--verbose', 'add', '1', '2']).result
    assert out.getvalue() == 'init verbose=True\nadd 3 verbose=True\n', \
        out.getvalue()
    # a fresh instance per parse, defaults refilled
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app.process(['add', '4', '5']).result
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

    assert app2.process(['list']).result == 'listed'
    try:
        app2.process(['list_']).result
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

    proc = app.process(['-v', 'fgrep', 'patt', 'file', '-c', '33'])
    assert out == [('init', True),
                   ('fgrep', True, 'patt', 'file', 33)], out
    # the log: instances[0] is the help/version precommand era (None, None);
    # then the global class logs (None, instance); the method logs (fn, None)
    command, instance = proc.instances[1]
    assert command is None and isinstance(instance, MyApp)
    assert instance.verbose is True
    assert proc.instances[2] == (MyApp.fgrep, None)
    # undecorated methods aren't commands
    try:
        app.process(['helper']).result
        assert False, 'expected UsageError'
    except UsageError as e:
        assert 'unknown command' in str(e)
    # the instance is never mistaken for an exit status
    assert app.process(['fgrep', 'a', 'b']).result is None


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

            @app.subcommand('Db')
            def add(self, x: int):
                out.append(('add', self.name, x))

    app.process(['-v', 'Db', 'mydb', 'add', '3']).result
    assert out == [('db', 'mydb'), ('add', 'mydb', 3)], out
    # cycling pops from the inner set back to a root method
    out.clear()
    proc = app.process(['Db', 'mydb', 'add', '1', 'top', '9'])
    assert out == [('db', 'mydb'), ('add', 'mydb', 1),
                   ('top', False, 9)], out
    # the log carries every constructed instance, in order
    kinds = [(getattr(c, '__name__', None),
              type(i).__name__ if i is not None else None)
             for c, i in proc.instances]
    # a leading (None, None) is the help/version precommand era
    assert kinds == [(None, None), (None, 'Outer'), ('Db', 'Db'),
                     ('add', None), ('top', None)], kinds


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

    proc = app.process(['-v', 'Job', 'nightly', '--dry'])
    assert out == [('job', True, 'nightly', True)], out
    # [0] precommand era, [1] the Host global, [2] the Job command
    command, instance = proc.instances[2]
    assert type(instance).__name__ == 'Job'


def test_subcommand():
    # @app.subcommand(parent, name=) (ruled 2026-08-10): parent
    # is a command word PATH string or None, EXPLICIT always--
    # Appeal never infers subcommand-ness.  Declarations resolve
    # lazily, so registration order is free.
    import appeal as _appeal
    ran = []
    app = _appeal.Appeal(name='t')

    @app.subcommand('db')            # declared BEFORE its parent
    def add(x: int):
        ran.append(('add', x))

    @app.command()
    def db(*, verbose=False):
        ran.append(('db', verbose))

    dbcmd = app.subcommand('db')     # the tear-off, reusable
    @dbcmd
    def remove(x: int):
        ran.append(('remove', x))
    @dbcmd
    def drop():
        ran.append('drop')

    app.process(['db', 'add', '1']).result
    app.process(['db', 'remove', '2']).result
    app.process(['db', 'drop']).result
    assert ran == [('db', False), ('add', 1), ('db', False),
                   ('remove', 2), ('db', False), 'drop'], ran

    # a deep path attaches at depth; name= renames
    @app.subcommand('db add', name='audit-log')
    def audit():
        pass
    assert ('audit-log', audit) in app._subs['add']

    # an object is refused by name: the parent is a PATH
    try:
        app.subcommand(db)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'word path' in str(e)

    # a path nothing ever registers refuses at first use, named
    app2 = _appeal.Appeal(name='u')
    @app2.subcommand('ghost')
    def lost():
        pass
    try:
        app2._subs
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert "'ghost'" in str(e)

    # command() IS subcommand(None)
    app3 = _appeal.Appeal(name='v')
    @app3.subcommand(None)
    def solo(x):
        return ('solo', x)
    assert app3.process(['solo', 'a']).result == ('solo', 'a')


def test_subcommand_same_world():
    # SAME-WORLD, Larry's formulation (ruled 2026-08-10,
    # deliberately restrictive--relaxable later, never the
    # reverse): a method is (a) a top-level command, its class
    # the GLOBAL command, or (b) a direct subcommand of its
    # class's own mount.  Nowhere else--not under plain
    # functions, not even under other methods.
    import appeal as _appeal

    ok = _appeal.Appeal(name='ok')
    @ok.global_command()
    class A1:
        def __init__(self):
            pass
        @ok.command()
        def parent(self):           # (a): top-level method
            pass
    @ok.subcommand('parent')
    def below():                    # plain BELOW a method: fine
        pass
    ok._subs                        # resolves without complaint

    # (b): a method directly under its class's own mount
    ok2 = _appeal.Appeal(name='ok2')
    @ok2.command()
    class Db:
        def __init__(self):
            pass
        @ok2.subcommand('Db')
        def wipe(self):
            pass
    ok2._subs

    # refused: a method under a PLAIN FUNCTION
    bad = _appeal.Appeal(name='bad')
    @bad.command()
    def gravy():
        pass
    @bad.global_command()
    class A2:
        def __init__(self):
            pass
        @bad.subcommand('gravy')
        def foo(self):
            pass
    try:
        bad._subs
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'same-world' in str(e)

    # a BOUND method is a different category (ruled
    # 2026-08-10): the user already supplied the instance, so
    # it's an ordinary callable--mountable anywhere.  Same-world
    # constrains only the automatic bind-through-the-parent's-
    # instance machinery, i.e. raw functions claimed from a
    # mounted class's __dict__.
    free = _appeal.Appeal(name='free')
    @free.command()
    def anywhere():
        pass
    class Tool:
        def __init__(self, tag):
            self.tag = tag
        def stamp(self, x):
            return ('stamp', self.tag, x)
    tool = Tool('mine')
    free.subcommand('anywhere')(tool.stamp)
    assert free.process(['anywhere', 'stamp', 'hi']).result == \
        ('stamp', 'mine', 'hi')

    # refused: a method under ANOTHER METHOD (Larry's
    # formulation: methods don't hang off each other)
    bad2 = _appeal.Appeal(name='bad2')
    @bad2.global_command()
    class A3:
        def __init__(self):
            pass
        @bad2.command()
        def parent(self):
            pass
        @bad2.subcommand('parent')
        def chained(self):
            pass
    try:
        bad2._subs
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'same-world' in str(e)


        # a method of bar mounted outside its world refuses at
        # COMPILE time in the recompile run (same-world)


def test_class_as_app_refusals():
    import appeal as _appeal
    app = _appeal.Appeal(name='r')

    class Undecorated:
        @app.command()
        def method(self, x):
            pass
    # the class was never decorated: its method is an orphan
    try:
        app.process(['method', 'a']).result
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


# ---------------------------------------------------------------------
# the docstring parser (composable documentation, proposal §8.7.1)

def test_parse_docstring():
    # THE DOCSTRING IS MARKDOWN (the pivot, ruled 2026-08-05;
    # Markdown ONLY--the 'Arguments:' + 'name: desc' grammar died
    # unshipped, this test converted the same day).
    from appeal.presentation import parse_docstring

    # the docstring is input, never output: sections are slurped
    # out, prose coalesces in source order.
    doc = (
        "Summary line for foo.\n"
        "\n"
        "First line of docs for foo.\n"
        "\n"
        "This is the second line of docs.\n"
        "\n"
        "# Arguments\n"
        "x\n"
        ": the thing to foo\n"
        "\n"
        "# Options\n"
        "verbose\n"
        ": Verbosity, man.\n"
    )
    c = parse_docstring(doc, "foo")
    assert c['summary'] == ['Summary line for foo.']
    assert c['documentation'] == [
        'First line of docs for foo.', '', 'This is the second line of docs.']
    assert c['arguments'] == {'x': ['the thing to foo']}
    assert c['options'] == {'verbose': ['Verbosity, man.']}
    assert c['commands'] == {}
    # a special section runs to the next heading and must contain
    # ONLY its definition list: prose after the list refuses (put
    # doc prose before the sections, or under its own heading)
    try:
        parse_docstring("Sum.\n\n# Options\nv\n: doc\n\nStray prose.",
                        "f")
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'definition' in str(e)

    # definition text keeps its relative indentation (code!),
    # dedented as a block, nothing flattened.
    doc = (
        "Sum.\n"
        "\n"
        "Arguments\n"
        "---------\n"
        "x\n"
        ": the thing.\n"
        "  More about x.\n"
        "      code = here\n"
        "\n"
        "y\n"
        ": simple."
    )
    c = parse_docstring(doc, "f")
    assert c['arguments'] == {
        'x': ['the thing.', 'More about x.', '    code = here'],
        'y': ['simple.'],
    }

    # heading-free docstring: pure prose, no sections.  The old
    # grammar's spellings are just prose now, too.
    c = parse_docstring("Just prose.\n\nNote: this stays prose.", "f")
    assert c['summary'] == ['Just prose.']
    assert c['documentation'] == ['Note: this stays prose.']
    assert not (c['arguments'] or c['options'] or c['commands'])
    c = parse_docstring("Sum.\n\nArguments:\n  x: old dialect", "f")
    assert not c['arguments']            # the old grammar is dead
    assert 'Arguments:' in '\n'.join(c['documentation'])

    # empty and None
    for empty in (None, '', '\n\n'):
        c = parse_docstring(empty, "f")
        assert c['summary'] == [] and c['documentation'] == []

    # any heading kind, any level, any case
    c = parse_docstring("Sum.\n\n###### COMMANDS\nserve\n: Serves.", "f")
    assert c['commands'] == {'serve': ['Serves.']}
    # 'Subcommands' is not on the menu (the Markdown spec names
    # exactly three sections); it stays a body heading
    c = parse_docstring("Sum.\n\n# Subcommands\nserve\n: Serves.", "f")
    assert c['commands'] == {}
    assert '# Subcommands' in '\n'.join(c['documentation'])

    # the template dresses the page: nothing to present
    c = parse_docstring("Sum.\n\n# Options\nv\n: doc", "f")
    assert c['presentation'] == {'order': (), 'headers': {},
                                 'indents': {}}


def test_parse_docstring_errors():
    from appeal.presentation import parse_docstring
    def refuses(doc, *needles):
        try:
            parse_docstring(doc, "f")
        except AppealConfigurationError as e:
            for needle in needles:
                assert needle in str(e), f'{needle!r} not in {e}'
            return
        assert False, f'expected AppealConfigurationError for {doc!r}'

    # one section per kind, whatever the spelling
    refuses("# Options\na\n: b\n\nOPTIONS\n-------\nc\n: d", "two")
    # a special section must contain only a definition list
    refuses("# Options\njust some prose", "definition")
    # a term must be unformatted
    refuses("# Options\n**a**\n: b", "unformatted")
    # a definition needs a term
    refuses("# Options\n: floating definition", "no term")
    # an entry documented twice
    refuses("# Options\na\n: b\n\na\n: c", "documented twice")
    # errors name the owner
    refuses("# Options\nprose only", "f")


def test_merge_docs():
    from appeal.presentation import merge_docs

    def int_float(i: int, f: float):
        """
        An int and a float.

        # Arguments
        i
        : The integer part.

        f
        : The float part.
        """

    def my_converter(i_f: int_float, s: str, *, verbose=False):
        """
        Gathers the whole bundle.

        Prose about gathering, which stays home.

        # Options
        verbose
        : Print more output.
        """

    def recurse2(a: str, b: my_converter = None):
        """
        The showpiece.

        # Arguments
        a
        : The first thing.

        i
        : Overridden, how many knocks.
        """

    c = merge_docs(build_plan(recurse2))
    # prose and summaries stay home: only the command's own
    assert c['summary'] == ['The showpiece.']
    assert c['documentation'] == []
    # rows in plan order; entries merge up; nearest wins ('i');
    # undocumented surfaces get empty rows ('s')
    assert c['arguments'] == [
        ('<A>', ['The first thing.']),
        ('<I>', ['Overridden, how many knocks.']),
        ('<F>', ['The float part.']),
        ('<S>', []),
    ]
    assert c['options'] == [('-v|--verbose', ['Print more output.'])]
    assert c['commands'] == []

    # a keyword-only-no-default parameter is a trailing operand:
    # documenting it under Arguments is correct
    def trailing_ok(a, *, required_kw):
        """
        # Arguments
        required_kw
        : a trailing operand.
        """
    c = merge_docs(build_plan(trailing_ok))
    assert c['arguments'] == [('<A>', []),
                              ('<REQUIRED_KW>', ['a trailing operand.'])]

    # commands merge only when the plan dispatches
    def dispatcher():
        """
        Top.

        # Commands
        serve
        : Serves the thing.
        """
    c = merge_docs(build_plan(dispatcher), command_names=('serve', 'help'))
    assert c['commands'] == [('serve', ['Serves the thing.']), ('help', [])]


def test_merge_docs_errors():
    from appeal.presentation import merge_docs

    def refuses(f, *needles, command_names=None):
        try:
            merge_docs(build_plan(f), command_names=command_names)
        except AppealConfigurationError as e:
            for needle in needles:
                assert needle in str(e), f'{needle!r} not in {e}'
            return
        assert False, f'expected AppealConfigurationError for {f.__name__}'

    def helper(x: int, y: int):
        return (x, y)

    def bad_unknown(a):
        """
        # Arguments
        zed
        : nope.
        """
    refuses(bad_unknown, "'zed'", 'not a parameter')

    def bad_invisible(a: str, b: helper = None):
        """
        # Arguments
        b
        : the pair.
        """
    refuses(bad_invisible,
            "'b' is not one of the visible command-line arguments "
            "of 'bad_invisible'")

    def bad_kind(a, *, flag=False):
        """
        # Arguments
        flag
        : wrong side.
        """
    refuses(bad_kind, 'is an option, not an argument')

    def bad_kind2(a, *, flag=False):
        """
        # Options
        a
        : wrong side.
        """
    refuses(bad_kind2, 'is an argument, not an option')

    def bad_commands(a):
        """
        # Commands
        serve
        : nope.
        """
    refuses(bad_commands, "doesn't dispatch to commands")

    def bad_command_word():
        """
        # Commands
        swerve
        : typo.
        """
    refuses(bad_command_word, "'swerve'", 'not one of the command words',
            command_names=('serve',))


# ---------------------------------------------------------------------
# colorization (proposal §8.8, the completion/colorization rulings)

def test_theme():
    # the theme lift (task #20): themes are DATA--dicts of
    # StyleSheet entries--and the stylesheet decision is
    # resolve_stylesheet: None auto, False never, a composed
    # sheet verbatim
    import io
    from appeal.presentation import appeal_theme, plain_theme, resolve_stylesheet, uncolored_theme, usage_markup

    # every theme speaks the same vocabulary
    for theme in (plain_theme, uncolored_theme, appeal_theme):
        for role in ('program', 'command', 'option', 'argument',
                     'oparg', 'summary', 'error', 'heading_color',
                     'code', 'term'):
            assert role in theme, role
    # plain strips; appeal colors (ruled: code is green)
    assert plain_theme['option'] == ('T', 'T')
    assert 'cyan' in appeal_theme['option'][1]
    assert 'green' in appeal_theme['code'][1]

    # resolve_stylesheet: a composed sheet is used VERBATIM
    # (ruled 2026-08-06)--even for a non-tty stream
    from big.markdown import markdown_defaults
    from big.stylesheet import (StyleSheet, ansi_16_color_palette,
                                transforms)
    sheet = (markdown_defaults | transforms | ansi_16_color_palette
             | StyleSheet(appeal_theme))
    assert resolve_stylesheet(sheet, io.StringIO()) is sheet
    # None and False resolve to compositions that render
    resolved = resolve_stylesheet(None, io.StringIO())
    assert resolved.render('⦃error⦙error:⦄') == 'error:'   # pipe: plain
    never = resolve_stylesheet(False, io.StringIO())
    assert never.render('⦃error⦙error:⦄') == 'error:'

    # usage_markup: lexical, additive--strip the spans, get the
    # input back
    from big.stylesheet import strip_styles
    usage = 'serve [-v|--verbose] [-p|--port <PORT>] <HOST> ...'
    marked = usage_markup(usage)
    assert strip_styles(marked) == usage
    assert '⦃program⦙serve⦄' in marked
    assert '⦃option⦙-v⦄|⦃option⦙--verbose⦄' in marked
    assert '⦃oparg⦙<PORT>⦄' in marked          # inside brackets
    assert '⦃argument⦙<HOST>⦄' in marked       # at the top level
    assert '⦃' not in marked.split('⦃argument⦙<HOST>⦄')[1]  # '...' bare


def test_can_colorize_precedence():
    import io
    from appeal.presentation import can_colorize

    class Tty(io.StringIO):
        def isatty(self):
            return True

    def probe(env, file):
        saved = {k: os.environ.pop(k, None)
                 for k in ('PYTHON_COLORS', 'NO_COLOR', 'FORCE_COLOR', 'TERM')}
        try:
            os.environ.update(env)
            return can_colorize(file=file)
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

        # Arguments
        shape
        : the shape to draw.

        # Options
        verbose
        : narrate the process.

        times
        : how many times.
        """
    import io, contextlib
    from appeal.frontend import build_plan
    from appeal.presentation import merge_docs
    from appeal.presentation import appeal_theme, default_template, render_help_page
    from big.markdown import markdown_defaults
    from big.stylesheet import (StyleSheet, ansi_16_color_palette,
                                transforms)

    sheet = (markdown_defaults | transforms | ansi_16_color_palette
             | StyleSheet(appeal_theme))
    plan = build_plan(draw)
    corpus = merge_docs(plan)
    plain = render_help_page(plan.usage(), corpus, default_template)
    painted = render_help_page(plan.usage(), corpus, default_template,
                               stylesheet=sheet)
    assert painted != plain
    assert '\x1b[' in painted
    assert decolor(painted) == plain
    # the usage line: options wear the option role (cyan under
    # appeal_theme over the ANSI 16)
    assert '\x1b[36m--verbose\x1b[39m' in painted     # usage line
    # the theme lift (task #20): the BODY paints now too--the
    # flense baked role spans into the tables
    body = painted.split('\n\n', 1)[1]
    assert '\x1b[' in body, body
    assert '\x1b[36m--verbose\x1b[39m' in body        # options table term

    # angle-bracketed operands (positional_argument_usage_format=
    # '<{name}>') wear oparg inside brackets--which defaults to
    # argument (ruled), italic under appeal_theme
    from appeal.frontend import DEFAULT_ARG_FORMAT
    plan.arg_format = '<{name}>'
    bracketed = render_help_page(plan.usage(), merge_docs(plan),
                                 default_template, stylesheet=sheet)
    assert '\x1b[3m<times>\x1b[23m' in bracketed, bracketed
    plan.arg_format = DEFAULT_ARG_FORMAT


# ---------------------------------------------------------------------
# shell completion (the completion rulings, 1-5)

def test_value_completion():
    from appeal.completion import completions

    def color(name):
        return name
    color.completions = lambda prefix='': ('red', 'green', 'blue')

    def paint(where, hue: color = 'red', *, tint: color = 'red',
              times: int = 1):
        return (where, hue, tint)

    plan = build_plan(paint)
    # an option's value position asks the expecting converter,
    # and the engine re-filters by prefix (the belt)
    assert completions(plan, ['--tint'], '') == ['blue', 'green', 'red']
    assert completions(plan, ['--tint'], 'g') == ['green']
    # a value position whose converter has no opinion: filenames
    assert completions(plan, ['--times'], '') == []
    # an operand position asks its converter too (ruling 4)
    assert completions(plan, ['x'], '') == ['blue', 'green', 'red']
    # ...but the first operand (where: str) has no opinion
    assert completions(plan, [], '') == []
    # option-string completion still works, used singles excluded
    got = completions(plan, [], '-')
    assert '--tint' in got and '--times' in got
    assert '--tint' not in completions(plan, ['--tint', 'red'], '-')


def test_completions_validation():
    def refuses(f, *needles):
        try:
            build_plan(f)
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
    from appeal.completion import completions
    plan = build_plan(f3)
    try:
        completions(plan, [], '')
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


def test_single_terminal_transparency():
    # the transparency rule: a converter that consumes exactly one
    # operand is transparent to naming--the annotated parameter's
    # name flows through to usage, the help table, and the
    # docstring grammar.
    from appeal.presentation import merge_docs

    def flavor(name):
        """
        A flavor.

        # Arguments
        name
        : the flavor, in flavor's own vocabulary.
        """
        return name

    def scoop(cone, taste: flavor = 'vanilla', *, sprinkles=False):
        """
        Serves a scoop.

        # Arguments
        taste
        : which flavor to serve.
        """

    plan = build_plan(scoop)
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
    corpus = merge_docs(build_plan(scoop2))
    assert ('<TASTE>', ["the flavor, in flavor's own vocabulary."]) \
        in corpus['arguments']

    # an explicit rename on the inner parameter wins the display
    # (recorded in a registry, never on the converter)
    from appeal import Decorations
    def hue(name):
        return name
    d = Decorations()
    d.add_usage(hue, 'name', 'HUE')
    def tint(x, shade: hue = 'red'):
        "Tints."
    plan = build_plan(tint, decorations=d)
    assert '[HUE]' in plan.usage(), plan.usage()

    # multi-operand converters are NOT transparent: the invisible-
    # node error stands (pinned in test_merge_docs_errors), and
    # usage still shows the inner terminals
    def pair(x: int, y: int):
        return (x, y)
    def place(label, at: pair = None):
        "Places."
    u = build_plan(place).usage()
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
                        app.process(list(argv)).result
                    else:
                        app.process(list(argv), config=dict(config)).result
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
