#!/usr/bin/env python3
#
# tests/test_39.py
# The tests whose SUBJECT is a Python 3.9+ spelling:
# list[T]/dict[K,V]/tuple[...] annotations and typing.Annotated.
# The features don't exist below 3.9, so neither can these tests;
# test_all runs this file when the interpreter is new enough and
# COUNTS it out loud when it isn't (never a silent skip).
#
# Helpers and fixtures come from test_appeal: one harness, two
# files, split by interpreter floor (ruled 2026-07-11).

import sys

if sys.version_info < (3, 9):
    sys.exit("test_39.py needs Python 3.9 (that's the point)")

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_appeal import (
    Appeal, AppealConfigurationError, UsageError, appeal, test,
    build, run_both, run_script, subprocess_env, repo_dir,
    write_standalone_fixture,
    )
import subprocess
import tempfile


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
    # repeated: last one wins (ruled 2026-07-09)
    got = run_both(f, ['--span', '3', '4', '--span', '5', '6'])
    assert got == ('ok', ((5, 6), None)), got


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
        # ...and tuple OPTIONS, in the same script family
        script_path, script = write_standalone_fixture(d, 'spanmark')
        r = run_script(script_path, ['x', '--span', '5', '6'])
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'mark x (5, 6)\n'


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



def test_generic_kind_reads_and_schema():
    # the read driver's accumulate/mapping kinds, and MCP's
    # 'object' for dict[K,V]--the generic spellings own these
    # kinds (accumulator()/mapping() are folds)
    from appeal import read_mapping
    from appeal.schema import mcp_input_schema
    def cmd(*, env: dict[str, int] = None, tags: list[str] = ()):
        return (env, tuple(tags))
    got = read_mapping(cmd, {'env': {'k': '1'}, 'tags': ['a', 'b']})
    assert got == ({'k': 1}, ('a', 'b')), got
    for bad, complaint in (({'env': 5}, 'mapping'),
                           ({'tags': 5}, 'sequence')):
        try:
            read_mapping(cmd, bad)
            assert False, 'expected AppealDataError for %r' % (bad,)
        except appeal.AppealDataError as e:
            assert complaint in str(e), (bad, str(e))
    props = mcp_input_schema(build(cmd))['properties']
    assert props['env']['type'] == 'object'
    assert props['tags']['type'] == 'array'


def test_tuple_reads():
    # tuple[...] through the read driver: as an option (a sequence
    # of exactly its arity) and as a positional group (a mapping
    # by its synthetic element names, or a sequence)
    from appeal import read_mapping
    def cmd(*, t: tuple[int, str] = None):
        return t
    assert read_mapping(cmd, {'t': [1, 'x']}) == (1, 'x')
    def f(t: tuple[int, str]):
        return t
    assert read_mapping(f, {'t': {'t_0': 2, 't_1': 'y'}}) == (2, 'y')
    assert read_mapping(f, {'t': [3, 'z']}) == (3, 'z')


def test_config_mapping_option():
    # dict[K,V] through the config layer: a mapping (and only a
    # mapping) shapes into KEY=VALUE occurrences
    seen = []
    def make_app():
        app = Appeal(name='cfgm')
        @app.global_command()
        def top(*, env: dict[str, str] = None):
            seen.append(env)
        @app.command()
        def go():
            pass
        return app
    P = appeal.Processor
    P(make_app()).parse(['go'], {'env': {'k': 'v'}}).execute()
    assert seen == [{'k': 'v'}], seen
    try:
        P(make_app()).parse(['go'], {'env': 5}).execute()
        assert False, 'expected AppealDataError'
    except appeal.AppealDataError as e:
        assert 'mapping' in str(e), e


def test_generic_refusals():
    # set[int] means nothing to the grammar; tuple[()] has no
    # elements--each refused by name, in every position
    def f(*, o: set[int] = None):
        return o
    def g(*a: set[int]):
        return a
    def h(t: tuple[()]):
        return t
    def i(*, t: tuple[()] = None):
        return t
    for fn in (f, g, h, i):
        try:
            build(fn)
            assert False, 'expected refusal for %r' % (fn,)
        except AppealConfigurationError:
            pass


def test_generic_scoped_overlay():
    # scoped list[T]/dict[K,V] options: rung 1 converts the top
    # window's occurrences through the overlay's accumulate and
    # mapping branches
    def child(p, *, tags: list[str] = (), env: dict[str, str] = None):
        return p
    def cmd(a: child = None, *, tags: list[str] = (),
            env: dict[str, str] = None):
        return (a, tuple(tags), env)
    got = run_both(cmd, ['A', '--tags', 't', '--env', 'k=v'])
    assert got == ('ok', ('A', ('t',), {'k': 'v'})), got


def run_tests(run=None):
    (run or test.run)(name='appeal 3.9+ spellings', module=__name__)


if __name__ == '__main__':
    run_tests()
    test.finish()
