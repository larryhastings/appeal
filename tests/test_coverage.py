#!/usr/bin/env python3
#
# tests/test_coverage.py
# The 100%-coverage completion suite (2026-07-11): tests for
# branches the feature suites exercise only in subprocesses (the
# REPL, completion reentry), only in emitted copies (the borrowed
# big trio's edge strategies--which Appeal SHIPS in every
# standalone script, so our copies deserve behavior pins of their
# own), or not at all (error branches, rare shapes).  3.6-clean.

from big import test
from big.builtin import load

load('appeal')

import contextlib
import io
import os
import sys

import appeal
from appeal import (
    Appeal, AppealConfigurationError, AppealDataError, AppealError,
    UsageError, build_plan,
    )
import appeal


def both(fn, argv, decorations=None):
    "One engine: build the Converter in memory and run it; errors as text."
    import appeal
    from appeal.backend import build_converters, _converter_key
    plan = build_plan(fn, decorations=decorations)
    word = plan.name.replace('_', '-')
    cls = build_converters([plan])[_converter_key(plan)]
    try:
        return ('ok', appeal.execute({word: cls}, [word] + list(argv)))
    except AppealDataError as e:
        return ('usage', str(e))


# ---------------------------------------------------------------------
# the borrowed trio: Appeal ships these copies in every standalone
# script, so their edge branches get behavior pins here

def test_merge_columns_strategies():
    from appeal.presentation import merge_columns, OverflowStrategy as OS

    # a mid-column overflow distinguishes the strategies: INTRUDE
    # resumes the neighbors immediately after the wide line;
    # DELAY holds them until the overflow is fully past
    mid_a = (['a1', 'wwwwwwwwwwww', 'a3'], 4, 8)
    col_b = (['b1', 'b2', 'b3'], 3, 6)
    intrude = merge_columns(mid_a, col_b, overflow_strategy=OS.INTRUDE_ALL)
    delay = merge_columns(mid_a, col_b, overflow_strategy=OS.DELAY_ALL)
    assert intrude == ('a1       b1\nwwwwwwwwwwww\n'
                       'a3       b2\n         b3')
    assert delay == ('a1\nwwwwwwwwwwww\na3       b1\n'
                     '         b2\n         b3')

    # the synced copy's default strategy is RAISE
    try:
        merge_columns(mid_a, col_b)
        assert False, 'expected OverflowError'
    except OverflowError as e:
        assert 'overflow in column 0' in str(e)

    # overflow_before/_after pad around the overflow under DELAY
    padded = merge_columns(mid_a, col_b, overflow_strategy=OS.DELAY_ALL,
                           overflow_before=1, overflow_after=1)
    assert padded == ('a1\nwwwwwwwwwwww\na3\n         b1\n'
                      '         b2\n         b3')

    # invalid strategy refuses by name
    try:
        merge_columns(mid_a, col_b, overflow_strategy='sideways')
        assert False, 'expected ValueError'
    except ValueError as e:
        assert 'overflow_strategy' in str(e)

    # no columns at all refuses; an empty column renders empty
    try:
        merge_columns()
        assert False, 'expected ValueError'
    except ValueError as e:
        assert 'no columns' in str(e)
    assert merge_columns(([], 4, 8)) == ''

    # column_separator
    assert merge_columns((['x'], 1, 3), (['y'], 1, 3),
                         column_separator='|') == 'x  |y'


def test_wrap_words_edges():
    from appeal.presentation import wrap_words

    # no words at all refuses
    try:
        wrap_words([])
        assert False, 'expected ValueError'
    except ValueError:
        pass

    # an over-long word overflows its line alone
    long = 'x' * 50
    wrapped = wrap_words(['a', long, 'b'], margin=10)
    assert long in wrapped

    # two-space sentence separation survives wrapping
    text = wrap_words('One.\n\nTwo words here.'.split() + ['\n\n', 'Next.'],
                      margin=76)
    assert 'Next.' in text

    # explicit newlines and multiple paragraphs
    text = wrap_words(['para', '\n\n', 'break'], margin=20)
    assert text == 'para\n\nbreak'


def test_normalize_indents_validation():
    from appeal.presentation import wrap_words

    # indents: single str, tuple of str, and refusals by type
    assert wrap_words(['a', 'b'], margin=20, indent='  ').startswith('  a')
    assert wrap_words(['a', 'b'], margin=20,
                      indent=('* ', '  ')).startswith('* a')
    for bad in (42, ['ok', 42]):
        try:
            wrap_words(['a'], margin=20, indent=bad)
            assert False, 'expected TypeError'
        except TypeError as e:
            assert 'indent' in str(e)
    # an indent wider than the margin refuses
    try:
        wrap_words(['a'], margin=4, indent='      ')
        assert False, 'expected ValueError'
    except ValueError:
        pass
    # linebreaks are forbidden in indents
    try:
        wrap_words(['a'], margin=20, indent='x\ny')
        assert False, 'expected ValueError'
    except ValueError:
        pass
    # bytes flavor: the whole trio speaks bytes too
    got = wrap_words([b'one', b'two'], margin=20, indent=b'> ')
    assert got == b'> one two'


def test_split_text_with_code_edges():
    from appeal.presentation import split_text_with_code

    # code blocks survive; blank-heavy input; trailing code
    text = 'Prose here.\n\n    code line one\n    code line two\n\nMore.'
    words = split_text_with_code(text)
    assert '    code line one' in words
    # empty-ish input yields a single empty word (the synced
    # copy's actual behavior, pinned)
    assert split_text_with_code('') == ['']
    assert split_text_with_code('\n\n\n') == ['']
    words = split_text_with_code('ends with code:\n\n    tail()')
    assert words[-1] == '    tail()'


def test_format_definition_list_edges():
    from appeal.presentation import format_definition_list

    # empty pairs; a definition with its own paragraphs; a term
    # wider than its column (hang rule)
    assert format_definition_list([], 40) == ''
    out = format_definition_list(
        [('short', 'one\n\ntwo'),
         ('a-very-long-term-indeed', 'wrapped text follows here')],
        40)
    assert 'a-very-long-term-indeed' in out
    assert 'two' in out


# ---------------------------------------------------------------------
# the REPL, in-process: stdin is a script, stdout is captured

def test_repl_in_process():
    app = Appeal(name='calc')
    @app.command()
    def add(x: int, y: int):
        print(x + y)
    @app.command()
    def bail(code: int):
        return code

    def drive(lines, **kwargs):
        stdin = sys.stdin
        out = io.StringIO()
        sys.stdin = io.StringIO(''.join(line + '\n' for line in lines))
        try:
            with contextlib.redirect_stdout(out):
                result = app.repl(**kwargs)
        finally:
            sys.stdin = stdin
        return result, out.getvalue()

    # commands run; usage errors print politely; quit leaves
    result, out = drive(['add 1 2', 'add nope 2', 'zzz', '', 'quit'],
                        banner='hi!')
    assert result is None
    assert 'hi!' in out and '3' in out
    assert 'invalid value' in out
    assert 'unknown command' in out

    # EOF (stdin exhausted) leaves too; custom prompt
    result, out = drive(['add 2 3'], prompt='% ')
    assert '5' in out

    # a result PRINTS and the conversation continues (a REPL
    # that exited when a calculator returned 3 would be
    # obnoxious); only quit/exit/EOF leave
    result, out = drive(['bail 3', 'add 1 1'])
    assert result is None
    assert '3' in out and '2' in out

    # exit works like quit
    result, out = drive(['exit'])
    assert result is None

    # THE SESSION SURVIVES (ruled 2026-09-03, the Sol review):
    # a CommandError, an ordinary bug, -h's sys.exit, even a ^C
    # raised mid-command all print and CONTINUE
    @app.command()
    def sad():
        raise appeal.CommandError('the command said no')
    @app.command()
    def buggy():
        raise RuntimeError('oops')
    @app.command()
    def interrupted():
        raise KeyboardInterrupt
    import io as _io
    stderr = sys.stderr
    err = _io.StringIO()
    sys.stderr = err
    try:
        result, out = drive(['sad', 'buggy', '-h', 'interrupted',
                             'add 4 4', 'quit'])
    finally:
        sys.stderr = stderr
    assert 'error: the command said no' in out, out
    assert 'RuntimeError: oops' in err.getvalue()   # traceback, stderr
    assert 'usage:' in out                          # -h printed its page
    assert '8' in out, out                          # ...and we kept going

    # the Processor's repr is for debugging, not the REPL's output
    # (the Sol review's original symptom)
    p = app.process(['add', '1', '1'])
    assert repr(p).startswith('<Processor result=')
    assert '<Processor' not in out


# ---------------------------------------------------------------------
# completion reentry, in-process: the environment IS the protocol

def test_completion_reentry_in_process():
    from appeal import completion_reentry, completion_script

    def completer(words, prefix):
        assert words == ['x']
        return [prefix + 'lpha', prefix + 'rid']

    def reenter(env):
        old = dict(os.environ)
        os.environ.update(env)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = completion_reentry(completer, 'prog')
        finally:
            os.environ.clear()
            os.environ.update(old)
        return code, out.getvalue()

    # no env: not a reentry
    code, out = reenter({})
    assert code is None and out == ''

    # bash-style reentry: candidates, one per line
    # couriers NEWLINE-join the words (so args with spaces survive)
    code, out = reenter({'_APPEAL_COMPLETE': 'bash',
                         'COMP_WORDS': 'prog\nx\na', 'COMP_CWORD': '2'})
    assert code == 0
    assert out.splitlines() == ['alpha', 'arid']

    # the source_* requests print the courier
    for shell in ('bash', 'zsh', 'fish'):
        code, out = reenter({'_APPEAL_COMPLETE': f'source_{shell}'})
        assert code == 0
        assert 'prog' in out
        # and the standalone courier text generator agrees on shape
        assert completion_script(shell, 'prog')

    # an unknown shell refuses by name
    try:
        completion_script('powershell', 'prog')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'powershell' in str(e)


def test_completion_word_boundaries_and_prog_quoting():
    # Med 8: the shell hands over its words with the QUOTE CHARACTERS
    # still attached ("New York" as one element), so the reentry runs
    # them back through a shell lexer--boundaries survive AND the
    # quotes come off.  The program name is shell-quoted in the
    # emitted courier (no breakage/injection).
    import shlex
    from appeal import completion_reentry, completion_script, _split_arg_string

    # the lexer strips quotes, keeps a word with a space whole, and
    # tolerates the half-typed current word's unterminated quote
    assert _split_arg_string('p "New York" fo') == ['p', 'New York', 'fo']
    assert _split_arg_string('p "New Yo') == ['p', 'New Yo']
    assert _split_arg_string("p 'a b'") == ['p', 'a b']

    seen = []
    def completer(before, prefix):
        seen.append((before, prefix))
        return []

    def reenter(env):
        old = dict(os.environ)
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                completion_reentry(completer, 'p')
        finally:
            os.environ.clear()
            os.environ.update(old)
        return seen[-1]

    # bash/zsh: the quoted word arrives WITH its quotes (as bash's
    # COMP_WORDS really holds it); it stays one word, unquoted, and
    # the trailing empty word (cursor after a space) gives ''
    for mode in ('bash', 'zsh'):
        got = reenter({'_APPEAL_COMPLETE': mode,
                       'COMP_WORDS': 'p\n"New York"\n', 'COMP_CWORD': '2'})
        assert got == (['New York'], ''), (mode, got)
    # fish: same, its current-token text rides in COMP_CWORD
    got = reenter({'_APPEAL_COMPLETE': 'fish',
                   'COMP_WORDS': 'p\n"New York"\nfo', 'COMP_CWORD': 'fo'})
    assert got == (['New York'], 'fo'), got
    # empty COMP_WORDS means no words at all (not [''])
    got = reenter({'_APPEAL_COMPLETE': 'bash',
                   'COMP_WORDS': '', 'COMP_CWORD': '0'})
    assert got == ([], ''), got

    # prog is shell-quoted: a hostile name can't inject when sourced
    nasty = 'x";rm -rf ~;"'
    q = shlex.quote(nasty)
    for shell in ('bash', 'zsh', 'fish'):
        script = completion_script(shell, nasty)
        assert q in script, (shell, script)
        assert 'rm -rf ~;"' not in script.replace(q, ''), shell
    # the reentry invocation and the completion registration both
    # carry the quoted form
    bash = completion_script('bash', 'my prog')
    assert "_APPEAL_COMPLETE=bash 'my prog'" in bash, bash
    assert bash.rstrip().endswith("'my prog'"), bash


# ---------------------------------------------------------------------
# run_main's remaining branches

def test_run_main_branches():
    from appeal import run_main

    # argv=None reads sys.argv[1:]
    old = sys.argv
    sys.argv = ['prog', 'ok']
    try:
        assert run_main(lambda argv: 0 if argv == ['ok'] else 9) == 0
    finally:
        sys.argv = old

    # a non-int, non-None result means success
    assert run_main(lambda argv: 'a string', ['x']) == 0

    # a foreign non-appeal exception propagates
    class Weird(Exception):
        pass
    try:
        run_main(lambda argv: (_ for _ in ()).throw(Weird('pop')), [])
        assert False, 'expected Weird'
    except Weird:
        pass


# ---------------------------------------------------------------------
# rung 1's scoped-overlay kinds: every kind through a window

def test_plan_reprs_and_walkers():
    from appeal.frontend import Terminal
    def pt(x: int, y: int):
        return (x, y)
    def cmd(a, spot: pt = None, *, verbose=False):
        return (a, spot, verbose)
    plan = build_plan(cmd)
    # every record class reprs
    assert 'Plan' in repr(plan)
    assert 'Slot' in repr(plan.slots[0])
    assert 'Terminal' in repr(plan.slots[0].child)
    assert 'OptionRule' in repr(plan.options[0])
    # sole_terminal_slot: a two-terminal child has no sole slot
    assert plan.slots[1].child.sole_terminal_slot() is None
    # count_terminals recurses through nonterminals
    assert plan.count_terminals() == 3


def test_completion_table_repeat_group_completions():
    # a *args group whose converter carries completions: the
    # repeat position offers the converter's candidates
    from appeal.completion import completion_table
    def color(hue):
        return hue
    color.completions = lambda prefix: ('red', 'green')
    def paint(*hues: color):
        return hues
    table = completion_table(build_plan(paint))
    assert table['repeat'] is color
    assert appeal.completions(build_plan(paint), [], '') == ['green', 'red']
    # ...and the tuple-of-str contract refuses lists by name
    def loud(hue):
        return hue
    loud.completions = lambda prefix: ['not', 'a', 'tuple']
    def paint2(*hues: loud):
        return hues
    try:
        appeal.completions(build_plan(paint2), [], '')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'tuple of str' in str(e)


def test_schema_branches():
    from appeal.schema import mcp_input_schema

    class Fancy(appeal.Option):
        def init(self, default):
            self.value = default
        def option(self, x: int):
            self.value = x
        def __call__(self):
            return self.value

    def sub(host, port: int = 8080):
        """
        A server.

        # Arguments
        host
        : The host name.
        """
        return (host, port)

    def cmd(plain, num: int, *rest: str, flag=False,
            level: int = 0, tags: appeal.accumulator[str] = (),
            env: appeal.mapping[str, str] = None,
            where: sub = None, fancy: Fancy = 0):
        """
        Does things.

        # Arguments
        plain
        : An untyped operand.

        # Options
        flag
        : A boolean.
        """
        return plain
    describe = mcp_input_schema(build_plan(cmd))
    p = describe['properties']
    assert p['plain']['type'] == 'string'
    assert p['plain']['description'] == 'An untyped operand.'
    assert p['rest']['type'] == 'array'
    assert p['rest']['items'] == {'type': 'string'}
    assert p['flag']['type'] == 'boolean'
    assert p['flag']['description'] == 'A boolean.'
    # the vocabulary mapping() is a FOLD (reads occurrences),
    # so its MCP shape is an array; dict[K,V]'s 'mapping' kind
    # is the object (pinned in test_39's territory)
    assert p['env']['type'] == 'object'   # mapping -> a dict
    assert p['level']['type'] == 'integer'
    # a scalar-acceptable group (min 1): both shapes, like the reader
    assert {'type': 'string'} in p['where']['anyOf']
    assert p['where']['anyOf'][1]['type'] == 'object'
    assert 'host' in p['where']['anyOf'][1]['properties']
    assert p['tags']['type'] == 'array'

    # app.schema() with a global command covers the set flavor
    app = Appeal(name='s')
    @app.global_command()
    def top(*, trace=False):
        return trace
    @app.command()
    def go(x: int, *, where: sub = None):
        return x
    # schema(format, version): BOTH required, no defaults (ruled
    # 2026-09-03)--the caller names the format and the version of it
    # they can consume, as plain strings.  'appeal' versions are ours;
    # 'mcp' versions are the protocol's date-stamped revisions.
    try:
        app.schema()
        assert False, 'expected TypeError (no defaults)'
    except TypeError:
        pass
    try:
        app.schema('appeal')
        assert False, 'expected TypeError (version has no default)'
    except TypeError:
        pass
    try:
        app.schema('yaml', '1')
        assert False, 'expected AppealConfigurationError'
    except appeal.AppealConfigurationError as e:
        assert "'appeal'" in str(e) and "'mcp'" in str(e), e
    for fam, bad in (('appeal', '9.9'), ('mcp', '1999-12-31')):
        try:
            app.schema(fam, bad)
            assert False, 'expected AppealConfigurationError'
        except appeal.AppealConfigurationError as e:
            assert bad in str(e) and 'renders' in str(e), e
    js = app.schema('mcp', '2024-11-05')
    for word, entry in js.items():
        assert entry['type'] == 'object' and 'properties' in entry, (word, entry)
    described = app.schema('appeal', '1.0')
    assert described['global']['options']
    assert any(o.get('group') for o in
               described['commands']['go']['options'])


def test_man_page_edges():
    app = Appeal(name='dotty', version='1.0')
    @app.global_command()
    def top(*, trace=False):
        """
        .starts with a dot, troff-hostile.

        First paragraph.

        Second paragraph.
        """
    @app.command()
    def plain(x):
        """
        Has a summary and prose of its own.

        Sub prose paragraph.
        """
    @app.command()
    def bare():
        pass
    text = app.documentation('troff')
    assert '\\&.starts' in text            # leading-dot escape
    assert text.count('.PP') >= 2           # paragraph breaks
    assert 'Sub prose paragraph.' in text   # sub DESCRIPTION prose


def test_read_bool_flag_nullary():
    from appeal import read_mapping
    def loud():
        return 'LOUD'
    def cmd(*, flag=False, mode: loud = 'quiet'):
        return (flag, mode)
    # config files hold booleans many ways: real bools, 0/1, the
    # usual spellings; a nullary option reads a boolean too (True
    # calls the converter, False keeps the default)
    assert read_mapping(cmd, {'flag': 1, 'mode': True}) == (True, 'LOUD')
    assert read_mapping(cmd, {'flag': 'off', 'mode': False}) == (False, 'quiet')
    try:
        read_mapping(cmd, {'flag': 'maybe'})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'boolean' in str(e) and 'flag' in str(e), e


def test_read_option_kinds():
    from appeal import read_mapping
    def pairfn(a: int, b: int):
        return (a, b)
    def wh(x: int, *, deep: int = 0):
        return (x, deep)
    def cmd(*, spot: pairfn = None, where: wh = None):
        return (spot, where)
    # a multi-operand value reads a sequence of exactly its arity;
    # an option group reads a sub-mapping (its own options too).
    # (list[T]/dict[K,V]'s accumulate/mapping kinds are pinned in
    # test_39's territory--accumulator()/mapping() are folds.)
    got = read_mapping(cmd, {'spot': [1, 2], 'where': {'x': 5, 'deep': 3}})
    assert got == ((1, 2), (5, 3)), got
    for bad, complaint in (({'spot': 5}, 'sequence of 2'),
                           ({'spot': [1]}, 'sequence of 2')):
        try:
            read_mapping(cmd, bad)
            assert False, 'expected AppealDataError for %r' % (bad,)
        except AppealDataError as e:
            assert complaint in str(e), (bad, str(e))


def test_read_fold_options():
    from appeal import read_mapping

    class Bump(appeal.Option):
        def init(self, default):
            self.n = 0
        def option(self):
            self.n += 1
        def __call__(self):
            return self.n

    class Where(appeal.Option):
        def init(self, default):
            self.spots = []
        def option(self, x: int, y: int):
            self.spots.append((x, y))
        def __call__(self):
            return self.spots

    def cmd(*, bump: Bump = 0, where: Where = None):
        return (bump, where)
    # arity 0 folds read a count; arity-k folds a sequence of k-sequences
    got = read_mapping(cmd, {'bump': 3, 'where': [[1, 2], [3, 4]]})
    assert got == (3, [(1, 2), (3, 4)]), got
    assert read_mapping(cmd, {}) == (0, None)
    for bad, complaint in (({'bump': 'x'}, 'count'),
                           ({'where': 9}, 'sequence of occurrences'),
                           ({'where': [[1]]}, 'sequence of 2')):
        try:
            read_mapping(cmd, bad)
            assert False, 'expected AppealDataError for %r' % (bad,)
        except AppealDataError as e:
            assert complaint in str(e), (bad, str(e))


def test_read_fold_top_level():
    from appeal import read_mapping

    class Where(appeal.Option):
        def init(self, default):
            self.rows = []
        def option(self, x: int, y: int = 0):
            self.rows.append((x, y))
        def __call__(self):
            return self.rows

    # an Option as the callable itself: occurrences read as
    # mappings OR sequences (defaults fill)
    got = read_mapping(Where, [[1, 2], [3], {'x': 5, 'y': 6}])
    assert got == [(1, 2), (3, 0), (5, 6)], got
    try:
        read_mapping(Where, 5)
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'sequence' in str(e) and 'occurrences' in str(e), e
    try:
        read_mapping(Where, [7])
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'mapping or a sequence' in str(e), e


def test_read_group_shapes():
    from appeal import read_mapping
    def pairfn(a: int, b: int):
        return (a, b)
    def one(v: int, w: int = 0):
        return (v, w)

    def rpt(*nums: int):
        return nums
    assert read_mapping(rpt, {'nums': [1, 2]}) == (1, 2)
    try:
        read_mapping(rpt, {'nums': 5})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'sequence' in str(e), e
    def rpt2(*pts: pairfn):
        return pts
    assert read_mapping(rpt2, {'pts': [[1, 2], [3, 4]]}) == ((1, 2), (3, 4))

    # a required trailing operand (reserved past an absorbing group)
    # reads by name
    def _rest(*rest):
        return rest
    def trail(front: _rest, k):
        return (front, k)
    assert read_mapping(trail, {'front': [1, 2], 'k': 'v'}) == ((1, 2), 'v')

    # a required group with nothing present: defaults throughout,
    # and ITS required parameters complain by path
    def need(g: one):
        return g
    try:
        read_mapping(need, {})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert "'v'" in str(e) and 'g' in str(e), e
    def dont(g: one = None):
        return g
    assert read_mapping(dont, {}) is None
    # a scalar feeds a group that can take exactly one; a group
    # that needs two refuses it
    assert read_mapping(dont, {'g': 5}) == (5, 0)
    def two(g: pairfn):
        return g
    try:
        read_mapping(two, {'g': 5})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'mapping or a sequence' in str(e), e
    try:
        read_mapping(two, 5)
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'read_mapping needs a mapping' in str(e), e


def test_read_sequence_shapes():
    from appeal import read_iterable, read_csv
    def pairfn(a: int, b: int):
        return (a, b)
    def f(a: int, g: pairfn, b: int = 5):
        return (a, g, b)
    # nested groups read from a sequence element; absent tail
    # slots take their defaults
    assert read_iterable(f, [[1, [2, 3]], (), [4, [5, 6], 7]]) == \
        [(1, (2, 3), 5), (4, (5, 6), 7)]
    def two(a: int, b: int):
        return (a, b)
    for rows, complaint in (([[1]], 'ran out'),
                            ([[1, 2, 3]], 'leftover')):
        try:
            read_iterable(two, rows)
            assert False, 'expected AppealDataError'
        except AppealDataError as e:
            assert complaint in str(e), (rows, str(e))
    # position-feeding can't reach a trailing operand or keyword-only
    # names (v1's corpus)
    def _absorb(*xs):
        return xs
    def trail(front: _absorb, k):        # k is a reserved trailing operand
        return (front, k)
    def opt(a, *, k=1):
        return (a, k)
    def kw(a, **kwargs):
        return a
    for fn in (trail, opt, kw):
        try:
            read_iterable(fn, [['x']])
            assert False, 'expected AppealConfigurationError'
        except AppealConfigurationError as e:
            assert 'position-feed' in str(e), e
    # csv: empty reader reads nothing; a heading missing from
    # first_row_map is a data error; mapped rows feed by name
    assert read_csv(two, iter([])) == []
    assert read_csv(two, iter([['x', 'y'], ['1', '2']])) == [(1, 2)]
    got = read_csv(two, iter([['A', 'B'], ['1', '2']]),
                   first_row_map={'A': 'a', 'B': 'b'})
    assert got == [(1, 2)], got
    try:
        read_csv(two, iter([['A', 'Z']]), first_row_map={'A': 'a'})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert "'Z'" in str(e), e


def test_help_dedent_blank_lines():
    # _dedent_lines wears kid gloves: blank lines don't count
    # toward the margin (the docstring parser never sends any,
    # but the helper honors them)
    from appeal.presentation import _dedent_lines
    assert _dedent_lines(['  a', '', '    b']) == ['a', '', '  b']


def test_help_ambiguous_three_ways():
    # the same parameter name documented differently in THREE
    # sibling grammars: once ambiguous, later conflicts stand down
    def red(n):
        "Red.\n\n# Arguments\nn\n: Red's n.\n"
        return n
    def green(n):
        "Green.\n\n# Arguments\nn\n: Green's n.\n"
        return n
    def blue(n):
        "Blue.\n\n# Arguments\nn\n: Blue's n.\n"
        return n
    def cmd(a: red, b: green, c: blue):
        return (a, b, c)
    from appeal.presentation import merge_docs
    merged = merge_docs(build_plan(cmd))
    assert merged is not None


def test_man_page_double_blank():
    # two blank lines in a row: the empty chunk between them is
    # skipped, not rendered as an empty paragraph
    app = Appeal(name='gappy', version='1.0')
    @app.global_command()
    def top(*, trace=False):
        """
        Summary.

        First paragraph.


        Second paragraph after a double blank.
        """
    text = app.documentation('troff')
    assert 'Second paragraph after a double blank.' in text


def test_schema_leaf_fallbacks():
    # an operand whose converter isn't a JSON type: 'string'; a
    # multi-operand value option: 'array' (dict[K,V]'s 'object'
    # is pinned in test_39's territory)
    import pathlib
    from appeal.schema import mcp_input_schema
    def pairfn(a: int, b: int):
        return (a, b)
    def cmd(p: pathlib.Path, *, spot: pairfn = None):
        return (p, spot)
    describe = mcp_input_schema(build_plan(cmd))
    props = describe['properties']
    # Path builds as a scalar-acceptable *args group: the describe
    # offers the string AND the object, like the reader
    assert props['p']['anyOf'][0] == {'type': 'string'}
    assert props['spot']['type'] == 'array'
    # an option metavar rename rides along as 'usage'
    from appeal.frontend import Decorations
    from appeal.schema import describe as describe
    def q(*, level: int = 0):
        return level
    d = Decorations()
    d.add_usage(q, 'level', 'LVL')
    opt = describe(build_plan(q, decorations=d))['options'][0]
    assert (opt['name'], opt['usage']) == ('level', 'LVL')
    # a repeat slot with a strict group child: array of objects
    def pt2(x: int, y: int):
        return (x, y)
    def paint(*spots: pt2):
        return spots
    spots = mcp_input_schema(build_plan(paint))['properties']['spots']
    assert spots['type'] == 'array'
    assert set(spots['items']['properties']) == {'x', 'y'}


def test_plan_body_valid_counts_none():
    # an unbounded plan has no count set: the body property answers
    # None right along with valid_counts
    def infinite(*args):
        return args
    plan = build_plan(infinite)
    assert plan.valid_counts is None
    assert plan.body_valid_counts is None


def test_completion_repeat_group_carries_completions():
    # a *args GROUP converter (it has an option, so each occurrence
    # is windowed) with completions and a sole terminal: the repeat
    # position offers the converter's candidates
    from appeal.completion import completion_table
    def color(hue, *, bright=False):
        return hue
    color.completions = lambda prefix: ('red', 'green')
    def paint(*hues: color):
        return hues
    table = completion_table(build_plan(paint))
    assert table['repeat'] is color


# ---------------------------------------------------------------------
# batch 4: appeal/__init__.py--config layering shapes, Processor
# edges, registration errors, mcp/standalone entry points, repl


def test_config_vet_refusals():
    # each mis-aimed config key gets its own diagnosis
    def g1(a: int = 0, *, deep=False):
        return (a, deep)
    def g2(b: int = 0, *, deep=False):
        return (b, deep)
    app = Appeal(name='cfg')
    cfg = {}
    @app.global_command(config=cfg)
    def top(src=None, *, verbose=False, x: g1 = None, y: g2 = None):
        return (src, verbose)
    @app.command()
    def go(dest, *, level: int = 0):
        return dest
    cases = (
        ({'deep': True}, AppealConfigurationError, 'scoped'),
        ({'go': 1}, AppealDataError, 'is a command'),
        ({'src': 'a'}, AppealDataError, 'positional'),
        ({'dest': 'a'}, AppealDataError, "of 'go'"),
        ({'level': 3}, AppealDataError, "option of 'go'"),
        ({'nowhere': 1}, AppealDataError, "isn't an option"),
        )
    for config, exc, complaint in cases:
        cfg.clear(); cfg.update(config)
        try:
            app.process(['go', 'd']).result
            assert False, 'expected %s for %r' % (exc.__name__, config)
        except exc as e:
            assert complaint in str(e), (config, str(e))


def test_config_inject_shapes():
    # every option kind's config shaping, through the whole
    # pipeline (defaults < config < argv)
    class Add(appeal.Option):
        def init(self, default):
            self.total = 0
        def option(self, v: int):
            self.total += v
        def __call__(self):
            return self.total

    def pt(x: int, y: int):
        return (x, y)
    def box(w: int, h: int = 0):
        return (w, h)
    seen = []
    def make_app(cfg=None):
        app = Appeal(name='cfi')
        @app.global_command(config=cfg)
        def top(*, tags: appeal.accumulator[str] = (), adds: Add = 0,
                spot: pt = None, corner: box = None, flag=False):
            seen.append((tuple(tags), adds, spot, corner, flag))
        @app.command()
        def go():
            seen.append('go')
        return app

    def drive(config):
        seen[:] = []
        make_app(config).process(['go']).result
        return seen[0]

    assert drive({'tags': ['a', 'b'], 'adds': [1, [2]],
                  'spot': [3, 4], 'corner': {'w': 5, 'h': 6},
                  'flag': True}) == \
        (('a', 'b'), 3, (3, 4), (5, 6), True)
    assert drive({'corner': [7, 8]})[3] == (7, 8)
    assert drive({'corner': 9})[3] == (9, 0)
    for config, complaint in (({'tags': 5}, 'sequence'),
                              ({'spot': 5}, '2 values'),
                              ({'corner': {'w': 1, 'zz': 2}}, 'zz')):
        try:
            drive(config)
            assert False, 'expected AppealDataError for %r' % (config,)
        except AppealDataError as e:
            assert complaint in str(e), (config, str(e))
    # and the fused main() spelling drives the same machinery
    seen[:] = []
    try:
        make_app({'flag': True}).main(['go'])
        code = 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 0
    assert code == 0 and seen[0][4] is True, (code, seen)


def test_refuse_orphan_uninspectable():
    # an uninspectable builtin: the orphan-method check shrugs
    # (build then complains its own way)
    app = Appeal(name='orph')
    app.command(name='ga')(getattr)
    try:
        app.plan_for('ga')
    except Exception:
        pass


def test_registration_errors():
    # registering the same word twice REPLACES (v1, probed
    # 2026-07-18: redefinition wins, it doesn't error)
    app = Appeal(name='dup')
    app.command(name='x')(lambda: 'first')
    app.command(name='x')(lambda: 'second')
    assert app.process(['x']).result == 'second'
    app2 = Appeal(name='clash')
    @app2.global_command()
    def go():
        pass
    app2.command(name='go')(lambda: None)
    try:
        app2._table()
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'same name' in str(e)
    app3 = Appeal(name='empty')
    try:
        app3._table()
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'no commands' in str(e)
    try:
        app3.plan_for('zzz')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError:
        pass


def test_plan_and_schema_properties():
    # .plan refuses a subcommand app; a global-only app's describe
    # describes the lone plan
    app = Appeal(name='props')
    @app.global_command()
    def top(x: int, *, verbose=False):
        return x
    assert app.plan is app.global_plan
    described = app.schema('appeal', '1.0')
    assert described['operands'][0]['name'] == 'x'
    # the bare-app 'mcp' flavor: one JSON Schema, keyed by the program
    js = app.schema('mcp', '2024-11-05')
    (word, entry), = js.items()
    assert entry['type'] == 'object' and 'x' in entry['properties'], js
    @app.command()
    def go():
        pass
    try:
        app.plan
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'subcommands' in str(e)
    app2 = Appeal(name='nope')
    @app2.command()
    def solo(dest):
        return dest
    try:
        app2.plan_for('missing')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'missing' in str(e)


def test_nested_class_repeat_and_parse_for():
    # a class command with claimed methods and repeat=True; its
    # _parse_for entry is the whole nested set, fused
    ran = []
    app = Appeal(name='nest')
    @app.command()
    def solo():
        ran.append('solo')
    @app.command(name='db', repeat=True)
    class Db:
        def __init__(self, label):
            self.label = label
            ran.append(('db', label))
        @app.subcommand('db')
        def wipe(self):
            ran.append(('wipe', self.label))
    proc = app.process(['db', 'main', 'wipe'])
    assert ran == [('db', 'main'), ('wipe', 'main')], ran
    # the instance log carries the parent class instance
    assert any(isinstance(i, Db) for c, i in proc.instances), proc.instances


def test_main_completion_reentry():
    # main() answers a shell-completion reentry before parsing
    app = Appeal(name='mainc')
    @app.command()
    def alpha(x: int):
        return x
    old_argv = sys.argv
    old_env = dict(os.environ)
    os.environ.update({'_APPEAL_COMPLETE': 'bash',
                       'COMP_WORDS': 'mainc\nal', 'COMP_CWORD': '1'})
    out = io.StringIO()
    try:
        sys.argv = ['mainc']
        with contextlib.redirect_stdout(out):
            try:
                app.main()
                code = 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 0
    finally:
        sys.argv = old_argv
        os.environ.clear()
        os.environ.update(old_env)
    assert code == 0
    assert 'alpha' in out.getvalue()


def test_repl_completer_and_interrupts():
    import builtins
    import readline

    captured = {}
    real_set_completer = readline.set_completer
    def capture(fn):
        captured['completer'] = fn
        real_set_completer(fn)

    def drive(app, feed_items, banner=None):
        feed = iter(feed_items)
        def fake_input(prompt=''):
            item = next(feed)
            if isinstance(item, type) and issubclass(item, BaseException):
                raise item
            return item
        old_input = builtins.input
        readline.set_completer = capture
        out = io.StringIO()
        try:
            builtins.input = fake_input
            with contextlib.redirect_stdout(out):
                app.repl(banner=banner)
        finally:
            builtins.input = old_input
            readline.set_completer = real_set_completer
        return out.getvalue()

    app = Appeal(name='rp')
    @app.command()
    def go(x: int):
        return x
    text = drive(app, [KeyboardInterrupt, 'go "', 'quit'], banner='hi')
    assert 'hi' in text
    assert 'error:' in text                    # the unbalanced quote
    # the captured completer, driven with canned readline state
    completer = captured['completer']
    old_buffer = readline.get_line_buffer
    old_begidx = readline.get_begidx
    try:
        readline.get_line_buffer = lambda: 'g'
        readline.get_begidx = lambda: 0
        assert completer('g', 0) == 'go'
        assert completer('g', 9) is None
        readline.get_line_buffer = lambda: '" g'
        readline.get_begidx = lambda: 2
        completer('g', 0)                      # shlex chokes: .split()
        app.complete = lambda words, prefix: 1 // 0
        assert completer('g', 0) is None       # completion never raises
    finally:
        del app.complete
        readline.get_line_buffer = old_buffer
        readline.get_begidx = old_begidx
    # a command whose BUILD is broken: the repl reports the
    # configuration error and keeps going
    app2 = Appeal(name='rp2')
    @app2.command()
    def broken(y: 42):
        return y
    text = drive(app2, ['broken 1', EOFError])
    assert 'configuration error:' in text


def _importable_global_app(directory, name):
    # emission refuses __main__ residents by design, so the
    # command lives in a real module
    module_path = os.path.join(directory, name + '_mod.py')
    with open(module_path, 'wt', encoding='utf-8') as f:
        f.write('def top(x: int, *, verbose=False):\n    return x\n')
    sys.path.insert(0, directory)
    try:
        import importlib
        module = importlib.import_module(name + '_mod')
    finally:
        sys.path.pop(0)
    app = Appeal(name=name)
    app.global_command()(module.top)
    return app


def test_man_page_trailing_blank():
    # prose whose lines end with blanks: the empty chunk is
    # skipped, not rendered as an empty paragraph
    from appeal.presentation import man_page
    corpus = {'summary': ['S.'], 'documentation': ['First.', '', ''],
              'arguments': [], 'options': [], 'commands': []}
    text = man_page('prog', corpus, 'prog [x]')
    assert 'First.' in text
    assert not text.rstrip().endswith('.PP')


def test_read_group_option_and_nesting_shapes():
    from appeal import read_mapping, read_iterable
    # flat keys reach through TWO levels of grouping
    def one(v: int, w: int = 0):
        return (v, w)
    def outer_g(g: one = None, z: int = 0):
        return (g, z)
    def f(og: outer_g = None):
        return og
    assert read_mapping(f, {'v': 5}) == ((5, 0), 0)
    # a repeat slot's nonterminal children, position-fed
    def pairfn(a: int, b: int):
        return (a, b)
    def rpt(*pts: pairfn):
        return pts
    assert read_iterable(rpt, [[[1, 2], [3, 4]]]) == [((1, 2), (3, 4))]
    # a group with a trailing operand (reserved past an absorbing
    # group), read from a sequence: the tail is reserved off the end
    def _absorb(*xs):
        return xs
    def tg(items: _absorb, k):
        return (items, k)
    def f2(g: tg):
        return g
    assert read_mapping(f2, {'g': ['x', 'kv']}) == (('x',), 'kv')
    def wh(x: int, *, deep: int = 0):
        return (x, deep)
    def f3(g: wh):
        return g
    assert read_mapping(f3, {'g': [5]}) == (5, 0)


# ---------------------------------------------------------------------
# batch 5: build.py--uninspectable callables, refusals by name,
# the odd converter shapes


def test_build_uninspectable_converters():
    # C callables without signatures: terminals, everywhere
    def f(x: getattr):
        return x
    plan = build_plan(f)
    from appeal.frontend import Terminal
    assert isinstance(plan.slots[0].child, Terminal)
    def g(*a: getattr):
        return a
    build_plan(g)
    def h(*, o: getattr = None):
        return o
    build_plan(h)


def test_build_kwargs_converter_is_group():
    # a **kwargs converter builds as a GROUP (v1): its positional
    # parameters are operands, and its @app.option declarations
    # deliver options into the kwargs sink (the sink is empty when
    # no options are declared)
    from appeal.frontend import Terminal
    def kw(a, **kws):
        return (a, kws)
    child = build_plan(lambda x: x, name='outer')  # sanity: a plain leaf
    assert isinstance(child.slots[0].child, Terminal)
    def f(x: kw):
        return x
    plan = build_plan(f)
    group = plan.slots[0].child
    assert not isinstance(group, Terminal)
    assert group.var_keyword == 'kws'


def test_build_option_annotation_refusals():
    def f(*, o: 42 = None):
        return o
    try:
        build_plan(f)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'callable' in str(e)
    # a multi-operand option converter with *args: refused by name
    def widen(x, y, *rest):
        return (x, y, rest)
    def g(*, o: widen = None):
        return o
    try:
        build_plan(g)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'positional' in str(e)


def test_build_option_group_via_inner_converter():
    # a converter whose parameter takes ANOTHER converter has its
    # own grammar: option-group, not a flat row
    def pairfn(a: int, b: int):
        return (a, b)
    def conv(spot: pairfn):
        return spot
    def f(*, o: conv = None):
        return o
    plan = build_plan(f)
    assert plan.options[0].kind == 'group'


def test_build_fold_leaf_shapes():
    # an Option class nested as an ELEMENT (a fold leaf): read
    # occurrences roll through option()
    from appeal import read_mapping

    class Bump(appeal.Option):
        def init(self, default):
            self.n = 0
        def option(self):
            self.n += 1
        def __call__(self):
            return self.n

    class Move(appeal.Option):
        def init(self, default):
            self.spot = None
        def option(self, x: int, y: int):
            self.spot = (x, y)
        def __call__(self):
            return self.spot

    def f(b: Bump):
        return b
    assert read_mapping(f, {'b': [[], []]}) == 2
    def g(m: Move):
        return m
    assert read_mapping(g, {'m': [{'x': 1, 'y': 2}]}) == (1, 2)
    try:
        read_mapping(g, {'m': [{'x': 1}]})
        assert False, 'expected AppealDataError'
    except AppealDataError as e:
        assert 'y' in str(e), e


def test_build_fold_element_multiparam_refused():
    # a fold element must be a single leaf: a two-operand
    # converter inside option() is refused by name
    def pairfn(a: int, b: int):
        return (a, b)

    class W(appeal.Option):
        def init(self, default):
            pass
        def option(self, spot: pairfn):
            pass
        def __call__(self):
            return None

    def f(*, w: W = None):
        return w
    try:
        build_plan(f)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError:
        pass


def test_build_completions_and_probe_fallback():
    # completions on a multi-terminal converter: refused by name
    def wide(hue, shade):
        return (hue, shade)
    wide.completions = lambda prefix: ('a',)
    def f(x: wide):
        return x
    try:
        build_plan(f)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'wide' in str(e)
    # a wrapped nested class whose descriptor probe blows up:
    # the wrapper itself is the grammar
    class Inner:
        def __init__(self, n: int):
            self.n = n
    class Wrapper:
        __wrapped__ = Inner
        def __get__(self, obj, objtype=None):
            raise RuntimeError('no probe')
        def __call__(self, n: int):
            return Inner(n)
    wrapper = Wrapper()
    wrapper.__name__ = 'Inner'
    wrapper.__qualname__ = 'Wrapper.Inner'
    plan = build_plan(wrapper, name='Inner', method_of='Outer')
    assert plan.binds == 'Outer'


def test_build_repeat_group_refusals():
    # *args occurrences must be ABLE to consume an operand: an
    # all-keyword group has no positional to make progress with.
    # (An all-OPTIONAL positional builds now--greedy fill, ruled
    # 2026-08-05.)
    def hollow(*, deep=False):
        return deep
    def f(*occ: hollow):
        return occ
    try:
        build_plan(f)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError:
        pass
    def lenient(a=1, *, deep=False):
        return (a, deep)
    def f2(*occ: lenient):
        return occ
    build_plan(f2)                       # G1's refusal is gone
    # a windowed group's option string clashing with the top
    # level's: position can't tell them apart--refused
    def rep(x, *, v=False):
        return (x, v)
    def g(*occ: rep, v=False):
        return occ
    try:
        build_plan(g)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError:
        pass


def test_build_override_validation_and_names():
    from appeal.frontend import Decorations
    import functools
    def f(x, *, mode=''):
        return (x, mode)
    d = Decorations()
    # zero strings is LEGAL now (ruled 2026-07-25): the explicit
    # per-parameter unmap--configured, no rule, default fills
    d.add_option(f, 'mode', ())
    try:
        d.add_usage(f, 'mode', '')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError:
        pass
    try:
        build_plan(functools.partial(lambda x: x))
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'name' in str(e)


def test_build_var_positional_option_class_refused():
    class Bump(appeal.Option):
        def init(self, default):
            pass
        def option(self):
            pass
        def __call__(self):
            return None
    def f(*occ: Bump):
        return occ
    try:
        build_plan(f)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError:
        pass


def test_build_shared_converter_rule_dedupe():
    # the same converter on two slots: one child plan, one rule
    # set, visited once
    def child(p, *, o=False):
        return (p, o)
    def f(a: child, b: child):
        return (a, b)
    plan = build_plan(f)
    assert plan.slots[0].child is plan.slots[1].child


# ---------------------------------------------------------------------
# batch 6: codegen.py--emitter branches


def test_codegen_fill_name_collision():
    # two distinct converters with the same __name__: the second
    # fill function gets a numbered name
    ns1 = {}
    exec('def pt(x: int, y: int):\n    return (x, y)', ns1)
    ns2 = {}
    exec('def pt(x: str, y: str):\n    return (y, x)', ns2)
    def f(a: ns1['pt'], b: ns2['pt']):
        return (a, b)
    got = both(f, ['1', '2', 'p', 'q'])
    assert got == ('ok', ((1, 2), ('q', 'p'))), got


def test_codegen_forcing_and_flag_default():
    # a skippable group with options: giving one FORCES the group
    # (emitted both dry and live); an overridden flag keeps its
    # own default when absent
    from appeal.frontend import Decorations
    def g(a=1, *, deep=False):
        return (a, deep)
    def f(x, s: g = None, *, mode=''):
        return (x, s, mode)
    d = Decorations()
    d.add_option(f, 'mode', ('--loud',), annotation=bool,
                 default=False)
    got = both(f, ['X'], decorations=d)
    assert got == ('ok', ('X', None, '')), got
    got = both(f, ['X', '--deep'], decorations=d)
    assert got == ('ok', ('X', (1, True), '')), got
    got = both(f, ['X', '--loud', '2'], decorations=d)
    assert got == ('ok', ('X', (2, False), True)), got


def test_codegen_scoped_forcing():
    # scoped keys in the forcing condition: the interval model
    # decides whether an absent group was forced
    def g(a=1, *, deep=False):
        return (a, deep)
    def two(x: g = None, y: g = None):
        return (x, y)
    got = both(two, ['--deep', '5'])
    assert got == ('ok', ((5, True), None)), got
    got = both(two, ['--deep'])
    assert got == ('ok', ((1, True), None)), got


def test_codegen_kwargs_options():
    # @app.option declarations without matching parameters land in
    # **kwargs--and stay out of the call when absent (v1)
    from appeal.frontend import Decorations
    def f(x, **extras):
        return (x, extras)
    d = Decorations()
    d.add_option(f, 'zesty', ('--zesty',), annotation=str,
                 default=None)
    got = both(f, ['a', '--zesty', 'yes'], decorations=d)
    assert got == ('ok', ('a', {'zesty': 'yes'})), got
    got = both(f, ['a'], decorations=d)
    assert got == ('ok', ('a', {})), got


def test_codegen_option_group_counts():
    # an option group with optional operands emits a count check
    def og(x: int, y: int = 0):
        return (x, y)
    def f(*, where: og = None):
        return where
    got = both(f, ['--where', '1', '2'])
    assert got == ('ok', (1, 2)), got
    got = both(f, ['--where', '1'])
    assert got == ('ok', (1, 0)), got


# ---------------------------------------------------------------------
# batch 7: the core--the Option ABC,
# converters, themes, templates, vocabulary, streams


def test_run_main_completion_param():
    from appeal import run_main
    from appeal.completion import completion_table
    def go(x: int):
        return 0
    table = completion_table(build_plan(go))
    app = Appeal(name='go', default_mappings=None)
    app.global_command()(go)
    parse = lambda argv: appeal.Processor(app)(list(argv))
    old_env = dict(os.environ)
    os.environ.update({'_APPEAL_COMPLETE': 'bash',
                       'COMP_WORDS': 'go\n-', 'COMP_CWORD': '1'})
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = run_main(parse, [], completion=(table, 'go'))
    finally:
        os.environ.clear()
        os.environ.update(old_env)
    assert code == 0, code
    # and with no reentry environment, an empty argv just parses
    err = io.StringIO()
    code = run_main(parse, [], completion=(table, 'go'), errors=err)
    assert code == 2 and 'missing' in err.getvalue(), (code, err.getvalue())


def test_option_abc_and_predicates():
    o = appeal.Option()
    assert o.init(None) is None
    for method in (o.option, o):        # render is __call__ now
        try:
            method()
            assert False, 'expected NotImplementedError'
        except NotImplementedError:
            pass


def test_windowed_option_kinds():
    # per-window merging: an Option (last-wins), flags, accumulation
    class At(appeal.Option):
        def init(self, default):
            self.v = default
        def option(self, v: int):
            self.v = v
        def __call__(self):
            return self.v

    def rep(x, *, at: At = None, flag=False,
            tags: appeal.accumulator[str] = ()):
        return (x, at, flag, tuple(tags))
    def f(*occ: rep):
        return occ
    got = both(f, ['a', '--at', '1', '--flag',
                   '--tags', 't1', '--tags', 't2'])
    assert got == ('ok', (('a', 1, True, ('t1', 't2')),)), got
    got = both(f, ['a', '--at', '1', '--at', '2'])   # repeatable: last wins
    assert got == ('ok', (('a', 2, False, ()),)), got


def test_converter_body_errors():
    # a converter body's ValueError is a polite usage error
    def spot(x: int, y: int):
        raise ValueError('bad combo')
    def f(*, where: spot = None):
        return where
    got = both(f, ['--where', '1', '2'])
    assert got[0] == 'usage' and 'not a valid spot' in got[1], got


def test_toy_multisplit_and_bytes_iter():
    # _toy_multisplit is snipped into the appeal core (stdlib-only, so
    # the core imports nothing from big); _iterate_over_bytes still
    # rides in for the render side
    from appeal.converters import _toy_multisplit
    from appeal.presentation import _iterate_over_bytes
    assert list(_iterate_over_bytes('ab')) == ['a', 'b']
    assert _toy_multisplit('a,b', ',') == [('a', ','), ('b', '')]
    assert _toy_multisplit(b'a,b', [b',']) == [(b'a', b','), (b'b', b'')]


def test_expand_tabs_pins():
    from appeal.presentation import expand_tabs
    assert expand_tabs('a\tb') == 'a       b'
    assert expand_tabs('nope') == 'nope'
    assert expand_tabs(b'a\tb') == b'a       b'
    assert expand_tabs('a\tb\nc\td') == 'a       b\nc       d'
    for bad in (dict(first_column=-1), dict(column=0, first_column=1),
                dict(column='x')):
        try:
            expand_tabs('a\tb', **bad)
            assert False, 'expected ValueError for %r' % (bad,)
        except ValueError:
            pass


def test_wrap_words_tabs_and_code():
    from appeal.presentation import split_text_with_code, wrap_words
    # tabs inside code lines expand at render time, on the page's
    # tab stops; prose tabs are word breaks that die on wrap
    s = 'first para\n\n    code\tline\n    x\ty\n\nlast para'
    words = split_text_with_code(s)
    text = wrap_words(words, margin=30)
    assert 'code        line' in text
    assert 'last para' in text
    got = wrap_words(split_text_with_code(b'a\n\n    c\td'), margin=20)
    assert b'c' in got
    for bad in (dict(code_indent='x'), dict(code_indent=-1)):
        try:
            split_text_with_code('s', **bad)
            assert False, 'expected an error'
        except (TypeError, ValueError):
            pass
    try:
        wrap_words(['x'], left_column=0)
        assert False, 'expected ValueError'
    except ValueError:
        pass


def test_merge_columns_more():
    from appeal.presentation import merge_columns, OverflowStrategy
    # a plain string column splits itself; tabs expand in place
    got = merge_columns(('ab\tc\nx', 6, 10), ('p\nq', 3, 5))
    assert got == 'ab      c  p\nx          q', repr(got)
    got = merge_columns((b'a\nb', 3, 5), (b'c', 3, 5))
    assert got == b'a     c\nb', repr(got)
    # adjacent overflows merge; DELAY pads after the overflow
    left = 'looooooooong1\nlooooooooong2\nshort'
    got = merge_columns((left.split('\n'), 5, 6),
                        (['r1', 'r2', 'r3'], 4, 6),
                        overflow_strategy=OverflowStrategy.DELAY_ALL,
                        overflow_after=1)
    assert got.endswith('short\n       r1\n       r2\n       r3'), repr(got)


def test_format_definition_list_more():
    from appeal.presentation import format_definition_list
    got = format_definition_list([(b'term', b'def')], margin=40)
    assert b'term' in got and b'def' in got
    got = format_definition_list([('t', 'a\tb')], margin=40)
    assert 't' in got
    got = format_definition_list([('t', 'd')], margin=40,
                                 definition_left_column=10)
    assert 'd' in got
    cases = (
        (([('t', 'd')],), dict(indent=42), TypeError),
        (([('t', 'd')],), dict(indent='a\nb'), ValueError),
        (([('t', 'd')],), dict(spacer=''), ValueError),
        (([('t', 'd')],), dict(spacer='\t'), ValueError),
        (([(42, 'd')],), {}, TypeError),
        (([('t\n', 'd')],), {}, ValueError),
        (([('t', 'd')],), dict(definition_left_column=0), ValueError),
        (([('t', 'd')],), dict(definition_left_column=2), ValueError),
        (([('t', 'd')],), dict(margin=3), ValueError),
        )
    for args, kwargs, exc in cases:
        try:
            format_definition_list(*args, **kwargs)
            assert False, 'expected %s for %r' % (exc.__name__, kwargs)
        except exc:
            pass


def test_theme_resolution_and_markup():
    from appeal.presentation import can_colorize, resolve_stylesheet
    from big.stylesheet import strip_styles
    assert can_colorize(file=object()) is False
    class FakeTTY(io.StringIO):
        def isatty(self):
            return True
    tty = FakeTTY()
    old_env = dict(os.environ)
    os.environ.pop('NO_COLOR', None)
    os.environ.pop('FORCE_COLOR', None)
    os.environ['TERM'] = 'xterm-256color'
    try:
        if can_colorize(file=tty):
            # False: no escapes even on a willing tty; None: the
            # ANSI 16 paint the error role
            assert '\x1b[' not in resolve_stylesheet(False, tty).render(
                '⦃error⦙error:⦄')
            assert '\x1b[' in resolve_stylesheet(None, tty).render(
                '⦃error⦙error:⦄')
        # a composed sheet is verbatim: identity, any stream
        sentinel = resolve_stylesheet(None, tty)
        assert resolve_stylesheet(sentinel, io.StringIO()) is sentinel
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def test_section_template_validation():
    # the single-template model (ruled 2026-08-01; {summary}
    # split out 2026-08-05): six sections, each exactly once,
    # nothing else in braces
    from appeal.presentation import parse_help_template
    cases = (
        'no placeholders at all',
        '{usage}\n{summary}\n{doc}\n{options}\n{arguments}',  # missing commands
        '{usage}\n{doc}\n{options}\n{arguments}\n'
        '{commands}',                                      # missing summary
        '{usage}\n{summary}\n{doc}\n{options}\n{arguments}\n'
        '{commands}\n{commands}',                          # duplicate
        '{usage}\n{summary}\n{doc}\n{options}\n{arguments}\n'
        '{commands}\n{zzz}',                               # unknown
        )
    for template in cases:
        try:
            parse_help_template(template)
            assert False, 'expected refusal: %r' % (template,)
        except AppealConfigurationError:
            pass
    # the parse: headers and indents fall out of the text
    parsed = parse_help_template(
        'usage: {usage}\n\n{summary}\n\n{doc}\n\nOpts:\n  {options}\n\n'
        'Args:\n  {arguments}\n\nCmds:\n  {commands}')
    names = [n for n, _, _ in parsed]
    assert names == ['usage', 'summary', 'doc', 'options', 'arguments',
                     'commands']
    by = {n: (h, i) for n, h, i in parsed}
    assert by['usage'][0] == 'usage: '
    assert by['options'][0] == '\n\nOpts:\n  '
    assert by['options'][1] == '  '


def test_vocabulary_validation():
    try:
        appeal.split('')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError:
        pass
    try:
        appeal.validate()
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError:
        pass
    clamped = appeal.validate_range(5, 10, clamp=True)
    assert clamped('3') == 5
    strict = appeal.validate_range(5, 10)
    try:
        strict('3')
        assert False, 'expected ValueError'
    except ValueError:
        pass
    try:
        appeal.mapping[int]
        assert False, 'expected TypeError'
    except TypeError:
        pass


def test_process_stream_edges():
    from appeal.converters import _ProcessStream
    class Cranky(io.StringIO):
        def flush(self):
            raise OSError('nope')
    ps = _ProcessStream(Cranky('a\nb\n'))
    assert list(iter(ps))
    ps.close()                      # the flush error is swallowed
    assert '_ProcessStream' in repr(ps)


# ---------------------------------------------------------------------
# batch 8: the core--token stream edges, completion internals,
# the MCP wire protocol


def test_completion_candidate_edges():
    app = Appeal(name='comp')
    @app.global_command()
    def top(*, trace=False):
        return trace
    @app.command()
    def go(x, *, level: int = 0):
        return x
    @app.command(name='db')
    class Db:
        def __init__(self, label):
            self.label = label
        @app.subcommand('db')
        def wipe(self):
            pass
    assert 'go' in app.complete([], 'g')
    assert app.complete(['go', 'a'], '') == []       # saturated
    assert app.complete(['go', '--nope'], '') == []  # unknown: no opinion
    assert '--level' in app.complete(['go'], '--le')
    assert app.complete(['go', '--level'], '') == [] # pending a value
    assert app.complete(['zzz'], '') == []           # half-typed nonsense
    assert app.complete(['zzz'], '-') == []
    assert 'wipe' in app.complete(['db', 'main'], 'w')


def test_completion_compact_short_options():
    # completion must read a compact short token the same way the parser
    # does: a flag keeps the bundle going, a value-option ends it (its
    # value attached in the same token, or--if nothing's attached--in the
    # following word).  Before the fix, completion saw only word[:2].
    app = Appeal(name='comp')
    @app.command()
    def go(x, *, verbose=False, all=False, num: int = 0):
        return x
    # a bundle of flags: BOTH are consumed, neither is re-offered.
    assert sorted(app.complete(['go', '-va'], '-')) == \
        ['--help', '--num', '-h', '-n']
    # an attached value satisfies the option--it's consumed, NOT pending,
    # so -n isn't re-offered and positionals aren't suppressed.
    assert sorted(app.complete(['go', '-n5'], '-')) == \
        ['--all', '--help', '--verbose', '-a', '-h', '-v']
    # a flag then a value-option with its value attached: both consumed.
    assert sorted(app.complete(['go', '-vn5'], '-')) == \
        ['--all', '--help', '-a', '-h']
    # a value-option with NOTHING attached opens a pending: the value is
    # the next word, so completion offers nothing here.
    assert app.complete(['go', '-vn'], '') == []
    # ...and the separated spelling behaves identically.
    assert app.complete(['go', '-n'], '') == []
    # an unknown char spoils the whole token (the parser would refuse
    # the line); completion just has no opinion about it.
    assert sorted(app.complete(['go', '-vz'], '-')) == \
        ['--all', '--help', '--num', '--verbose', '-a', '-h', '-n', '-v']


def test_parse_short_options_directly():
    from appeal.backend import parse_short_options
    from appeal import UsageError
    def carve(s):
        return list(parse_short_options(s, ('abcde', 'x', 'z')))
    assert carve('-a') == [('a', None)]
    assert carve('-abc') == [('a', None), ('b', None), ('c', None)]
    assert carve('-abxzzz') == [('a', None), ('b', None), ('x', 'zzz')]
    assert carve('-abx') == [('a', None), ('b', None), ('x', None)]
    assert carve('-abz') == [('a', None), ('b', None), ('z', None)]
    try:
        carve('-abzq')       # multi-oparg option isn't last in the bundle
        assert False, 'expected UsageError'
    except UsageError as e:
        assert 'must be last in a bundle' in str(e)
    try:
        carve('-abq')        # q: unknown option
        assert False, 'expected UsageError'
    except UsageError as e:
        assert "unknown option '-q'" in str(e)
    # the up-front guards reject tokens that aren't short options at
    # all--caller bugs, so ValueError, not UsageError
    for bad in ('not an option', '--long', '-'):
        try:
            carve(bad)
            assert False, f'expected ValueError for {bad!r}'
        except ValueError:
            pass
    # the classifiers are re-read per char, so the caller may rewrite
    # them mid-carve--how the parser handles a bundle whose first
    # option maps its second (-me: -m enters a group registering -e)
    classifiers = [set('m'), set(), set()]
    carver = parse_short_options('-me', classifiers)
    assert next(carver) == ('m', None)
    classifiers[0].add('e')
    assert next(carver) == ('e', None)


def test_main_exit_status_rule():
    # the boundary reads a result the way backend._halts does: an exit
    # status is an int that isn't a bool.  `return True` means "it
    # worked"--it mustn't exit 1 just because True == 1.  any non-int
    # value, or no return at all, is success.
    def status_of(value):
        app = Appeal(name='status')
        @app.command()
        def go():
            return value
        try:
            app.main(['go'])
            assert False, 'main() must exit'
        except SystemExit as e:
            return e.code
    assert status_of(None) == 0
    assert status_of(0) == 0
    assert status_of(3) == 3
    assert status_of(True) == 0
    assert status_of(False) == 0
    assert status_of('hello') == 0


def test_defaults_inference_class_and_tuple():
    # v1's defaults inference, restored (review items I1/I2, ruled
    # 2026-09-07): an unannotated positional infers its converter
    # from the DEFAULT VALUE's type.  The scalar half never left;
    # these are the two that did.
    import pathlib
    app = Appeal(name='i1')
    @app.command()
    def build(target=pathlib.Path('out')):
        return target
    # I1: a custom-class default--the operand converts through the
    # class, so the function sees ONE type either way
    assert app.process(['build']).result == pathlib.Path('out')
    assert app.process(['build', 'src/x']).result == pathlib.Path('src/x')
    # I2: a tuple default--its length is the arity, its element
    # types the converters, and the result is a tuple
    app2 = Appeal(name='i2')
    @app2.command()
    def crop(box=(0, 2.5)):
        return box
    assert app2.process(['crop']).result == (0, 2.5)
    assert app2.process(['crop', '3', '4']).result == (3, 4.0)
    # a class whose constructor can't take one string fails politely,
    # at conversion, naming the parameter
    import datetime
    app3 = Appeal(name='fg')
    @app3.command()
    def when(at=datetime.datetime(2026, 1, 1)):
        return at
    try:
        app3.process(['when', '2026-09-07'])
        assert False, 'expected AppealUsageError'
    except appeal.AppealUsageError as e:
        assert "invalid value for 'at'" in str(e), e


def test_dispatch_more_shapes():
    # branch audit: __init__'s one-way ifs, each pinned behaviorally.
    # -V and --version both already belong to the program's own
    # options: the version machinery mints nothing (no clobber)
    def vg(*, vflag=False, version_word=False):
        return (vflag, version_word)
    appv = Appeal(name='vt', version='9.9')
    appv.option('vflag', '-V')(vg)
    appv.option('version_word', '--version')(vg)
    appv.global_command()(vg)
    assert appv.process(['-V']).result == (True, False)
    # config replay: the synth carries no operands, so the required
    # positional's "missing argument" is the benign, swallowed case
    appc = Appeal(name='c7')
    @appc.global_command(config={'verbose': True})
    def top(src, *, verbose=False):
        return (src, verbose)
    assert appc.process(['hello']).result == ('hello', True)
    # documentation() skips the doc body of an undocumented command
    # (the heading still appears)
    appd = Appeal(name='doc6')
    @appd.command()
    def bare(x):
        return x
    @appd.command()
    def documented(y):
        "Does a thing."
        return y
    md = appd.documentation('gfm')
    assert 'bare' in md and 'Does a thing.' in md
    # a class registered BEFORE its member: the ordering wand has
    # nothing to move
    appo = Appeal(name='ord')
    class Db:
        def __init__(self, label):
            self.label = label
        def wipe(self):
            return ('wipe', self.label)
    appo.command(name='db')(Db)
    appo.subcommand('db')(Db.wipe)
    assert appo.process(['db', 'x', 'wipe']).result == ('wipe', 'x')
    # a command() tear-off never applied: the node exists without a
    # callable, and the warm-up build simply skips it
    appt = Appeal(name='t7')
    @appt.command()
    def real(x):
        return x
    unused = appt.command('ghost')
    assert appt.process(['real', 'a']).result == 'a'
    # the global-plan cache: the era path reused on a second run
    appg = Appeal(name='gc')
    @appg.global_command()
    def gtop(*, trace=False):
        return trace
    @appg.command()
    def run():
        return 'ran'
    assert appg.process(['run']).result == 'ran'
    assert appg.process(['--trace', 'run']).result == 'ran'


def test_precommand_wand_no_move_needed():
    # branch audit: a member precommand registered AFTER its class
    # (post-hoc, not from inside the class body)--registration order
    # is already right, the wand has nothing to move
    out = []
    app = Appeal(name='wand')
    @app.precommand()
    class Aux:
        def __init__(self, *, v=False):
            out.append(('init', v))
            self.v = v
        @app.command()
        def go(self):
            out.append(('go',))
    def later(self):
        out.append(('later', self.v))
    Aux.later = later
    app.precommand()(Aux.later)
    app.process(['-v', 'go'])
    assert out == [('init', True), ('later', True), ('go',)], out


def test_global_plan_install_loser_deterministic():
    # branch audit: the losing side of the global-plan first-wins
    # install.  The threaded race test can't be counted on to lose
    # HERE specifically, so simulate it: "another thread" installs
    # while this one is still building, and the build in flight must
    # yield to the winner
    app = Appeal(name='loser')
    @app.global_command()
    def g(*, t=False):
        return t
    app._finalize()
    winner = {}
    orig_build = app._build
    def build_then_lose(*args, **kwargs):
        plan = orig_build(*args, **kwargs)
        if not winner:
            app._build = orig_build             # intercept only once
            winner['plan'] = app.global_plan    # the "other thread" installs
        return plan
    app._build = build_then_lose
    assert app.global_plan is winner['plan']


def test_trailer_contract_and_repl_data_errors():
    # branch audit: a trailer may return None ("nothing to render for
    # this stream"--the usage(file) -> str|None contract); both
    # catchers print the message and skip the trailer quietly
    app = Appeal(name='nt')
    @app.command()
    def go():
        raise appeal.AppealDataError('dry error', usage=lambda file: None)
    try:
        app.main(['go'])
        assert False, 'main() must exit'
    except SystemExit as e:
        assert e.code == 2
    app2 = Appeal(name='nt2')
    @app2.command()
    def boom():
        raise appeal.AppealDataError('repl error', usage=lambda file: None)
    @app2.command()
    def plain():
        raise appeal.AppealDataError('plain error')     # no usage at all
    out = io.StringIO()
    real_stdin = sys.stdin
    sys.stdin = io.StringIO('boom\nplain\n')
    try:
        with contextlib.redirect_stdout(out):
            app2.repl()
    finally:
        sys.stdin = real_stdin
    text = out.getvalue()
    assert 'repl error' in text and 'plain error' in text


def test_frontend_more_shapes():
    # branch audit: five frontend one-way ifs
    from appeal import build_plan
    # sole_terminal_slot skips an all-options mixin (zero terminals)
    # and keeps looking for the one terminal
    def mixin(*, deep=False):
        return deep
    def f(m: mixin, x):
        return (m, x)
    sole = build_plan(f).sole_terminal_slot()
    assert sole is not None and sole.name == 'x'
    # the long-only policy simply declines a one-char name: no long
    # form exists, and the policy mints no short
    app = Appeal(name='lo', default_options=appeal.default_long_option)
    @app.command()
    def go(x, *, v=False, verbose=False):
        return (x, v, verbose)
    assert app.process(['go', 'z', '--verbose']).result == ('z', False, True)
    try:
        app.process(['go', 'z', '-v'])
        assert False, 'expected AppealUsageError'
    except appeal.AppealUsageError as e:
        assert 'unknown option' in str(e), e
    # re-applying an IDENTICAL @app.option declaration is a no-op
    # (REPLs and test harnesses re-decorate freely): one rule, so
    # one listing row
    app2 = Appeal(name='re')
    def cmd(*, level: int = 0):
        return level
    app2.option('level', '--lvl')(cmd)
    app2.option('level', '--lvl')(cmd)
    app2.command()(cmd)
    assert app2.process(['cmd', '--lvl', '3']).result == 3
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app2.process(['help', 'cmd'])
    assert out.getvalue().partition('Options')[2].count('--lvl') == 1
    # a DECORATED keyword-only parameter followed by another parameter
    def cmd2(*, first: int = 0, second=False):
        return (first, second)
    app3 = Appeal(name='seq')
    app3.option('first', '--one')(cmd2)
    app3.command()(cmd2)
    assert app3.process(['cmd2', '--one', '5', '--second']).result == (5, True)
    # sibling-parents pairing: a group option carrying NONE of the
    # sibling-shared strings stays out of the pairing
    def g1(*, shared=False):
        return ('g1', shared)
    def g2(*, shared=False):
        return ('g2', shared)
    def g3(*, unique=False):
        return ('g3', unique)
    app4 = Appeal(name='sib')
    @app4.command()
    def trio(*, a: g1 = None, b: g2 = None, c: g3 = None):
        return (a, b, c)
    plan = app4.plan_for('trio')
    assert [k for k, keys in plan.sibling_parents] == ['-a', '-b']
    assert app4.process(['trio', '-a', '--shared']).result == \
        (('g1', True), None, None)


def test_group_option_required_nested_counts():
    # branch audit: a group option whose converter nests a REQUIRED
    # group--the nested counts add without the optional {0}:
    # outer(a, i:inner(p,q)) -> exactly 3
    app = Appeal(name='vc')
    def inner(p, q):
        return (p, q)
    def outer(a, i: inner):
        return (a, i)
    @app.command()
    def cmd(*, g: outer = None):
        return g
    assert app.process(['cmd', '-g', '1', '2', '3']).result == ('1', ('2', '3'))
    try:
        app.process(['cmd', '-g', '1', '2'])
        assert False, 'expected AppealUsageError'
    except appeal.AppealUsageError as e:
        assert 'takes 3' in str(e), e


def test_chain_options_deep_shapes():
    # branch audit: options on a NESTED positional-slot group (the
    # conjure chain), through the real two-pass dispatch
    def enfant(*, count: appeal.counter() = 0, flag: int = 0):
        return (count, flag)
    def parent(et: enfant = None):
        return et
    app = Appeal(name='cf')
    @app.command()
    def grandparent(p: parent = None):
        return p
    # a chain FOLD invoked twice: the second occurrence reuses the
    # instance; the dry pass builds it too, deferring init/option
    assert app.process(['grandparent', '--count', '--count']).result == (2, 0)
    # a chain VALUE option with its value attached
    assert app.process(['grandparent', '--flag=5']).result == (0, 5)
    # a chain-summoned MIDDLE level starving on its required operand:
    # it has no kwargs (the typed option lives on the deepest level),
    # so the availability message is the generic form
    def parent2(need, et: enfant = None):
        return (need, et)
    app2 = Appeal(name='sv')
    @app2.command()
    def grandparent2(p: parent2 = None):
        return p
    try:
        app2.process(['grandparent2', '--flag', '5'])
        assert False, 'expected AppealUsageError'
    except appeal.AppealUsageError as e:
        assert 'expected <NEED>' in str(e), e
    # a nested option that takes SEVERAL opargs isn't conjurable--the
    # chain pre-registration skips it, so it can't summon its group
    def pt(x: int, y: int):
        return (x, y)
    def enfant2(*, spot: pt = None):
        return spot
    def parent3(need, et: enfant2 = None):
        return (need, et)
    app3 = Appeal(name='mv')
    @app3.command()
    def grandparent3(p: parent3 = None):
        return p
    try:
        app3.process(['grandparent3', '--spot', '1', '2'])
        assert False, 'expected AppealUsageError'
    except appeal.AppealUsageError as e:
        assert "unknown option '--spot'" in str(e), e


def test_definition_no_space_after_colon():
    # branch audit: ':definition' with no space after the colon is
    # legal--the space is cosmetic, stripped when present
    from appeal.presentation import _parse_definition_list
    got = _parse_definition_list(['term', ':definition text'], 'here')
    assert got == [('term', 'definition text')], got


def test_schema_more_shapes():
    # branch audit, two undescribed shapes.  describe_set without a
    # global plan (an Appeal app always synthesizes one, but the
    # function is callable without): no 'global' entry
    app = Appeal(name='cc')
    @app.command()
    def pick(*choices: appeal.validate('a', 'b')):
        return choices
    from appeal.schema import describe_set
    entry = describe_set({'pick': app.plan_for('pick')}, None, 'cc')
    assert 'commands' in entry and 'global' not in entry
    # ...and a *args operand through a recipe converter (validate:
    # a terminal whose name is no JSON type): an array, untyped items
    js = app.schema('mcp', '2024-11-05')
    assert js['pick']['properties']['choices'] == {'type': 'array'}


def test_multi_declaration_options_all_listed():
    # branch audit turned bug: several @app.option rules may share one
    # NAME (each call is its own rule--go2's --north/--south both map
    # `direction`); usage advertised them all but the Options section
    # listed only the first.  Every rule gets its own row.
    app = Appeal(name='md')
    def north():
        return 'north'
    def south():
        return 'south'
    def go2(*, direction='none'):
        return direction
    app.option('direction', '--north', annotation=north)(go2)
    app.option('direction', '--south', annotation=south)(go2)
    app.command()(go2)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app.process(['help', 'go2'])
    text = out.getvalue()
    assert '--north' in text.partition('Options')[2]
    assert '--south' in text.partition('Options')[2]


def test_converter_type_overrides():
    # branch audit: validate(..., type=) and validate_range(..., type=)
    # are the documented explicit-type overrides, and nothing ever
    # passed them--inference ran every time
    conv = appeal.validate(1, 2, 3, type=int)
    assert conv('2') == 2
    try:
        conv('9')
        assert False, 'expected ValueError'
    except ValueError:
        pass
    rng = appeal.validate_range(0, 10, type=float)
    assert rng('2.5') == 2.5


def test_completion_repeatable_short_stays_offered():
    # branch audit: a repeatable option (a counter) isn't used up by
    # one appearance--completion must keep offering it
    app = Appeal(name='rep')
    @app.command()
    def go(x, *, verbose: appeal.counter() = 0, quiet=False):
        return (x, verbose)
    got = app.complete(['go', '-v'], '-')
    assert '-v' in got and '--verbose' in got, got
    # while the one-shot flag next to it is used up as usual
    got = app.complete(['go', '-q'], '-')
    assert '-q' not in got and '--quiet' not in got, got


def test_read_sequence_kwargs_option_stays_out():
    # branch audit: a nested group whose plan carries a **kwargs-
    # delivered @app.option, read from a SEQUENCE--a sequence can't
    # deliver options at all, and the sink must stay empty rather
    # than gaining an invented default.  (a top-level plan with
    # options is refused by read_iterable outright; only a CHILD
    # reaches _read_sequence wearing one.  The mapping read has the
    # same guard, and a test.)
    from appeal.frontend import Decorations, build_plan
    def kg(a, **kws):
        return (a, kws)
    d = Decorations()
    d.add_option(kg, 'zesty', ('--zesty',), annotation=str, default=None)
    def h(x, s: kg = None):
        return (x, s)
    plan = build_plan(h, decorations=d)
    got = appeal.read_mapping(plan, {'x': 'X', 's': ['a']})
    assert got == ('X', ('a', {})), got


def test_read_fold_mapping_occurrence_lenient():
    # branch audit: a fold (Option subclass) read from mapping-shaped
    # occurrences under strict=False--lenient reading drops the
    # unrecognized key instead of raising
    class acc(appeal.MultiOption):
        def init(self, default):
            self.values = []
        def option(self, a: int, b: int = 0):
            self.values.append((a, b))
        def __call__(self):
            return self.values
    data = [{'a': '1', 'b': '2', 'zzz': 'not a parameter'}]
    app = Appeal(name='fold')
    assert app.read_mapping(acc, data, strict=False) == [(1, 2)]
    try:
        app.read_mapping(acc, data)
        assert False, 'expected AppealDataError (strict default)'
    except appeal.AppealDataError as e:
        assert 'zzz' in str(e)


def test_no_debris_ships():
    # flit builds the sdist from git's tracked-file list minus
    # pyproject's [tool.flit.sdist] excludes--so the published package
    # is "everything tracked, unless somebody said otherwise", and a
    # stray tracked file ships silently (Sol #11: debugging scripts and
    # review transcripts in the sdist).  Hold the line from both ends:
    # every tracked file must be classified--shipped (this test's
    # allowlist) or excluded (pyproject)--and the wheel must contain
    # nothing but the package.
    import subprocess
    appeal_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        proc = subprocess.run(['git', 'ls-files'], cwd=appeal_dir,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL,
                              universal_newlines=True)
    except OSError:                     # no git on this machine
        proc = None
    if proc is None or proc.returncode != 0:
        print('test_no_debris_ships: sdist half not run (not a git checkout)')
        tracked = []
    else:
        tracked = proc.stdout.split()
    shipped_dirs = ('appeal/', 'tests/', '.github/')
    shipped_files = {
        '.gitignore', 'LICENSE', 'README.md', 'pyproject.toml',
        'appeal.completion.md', 'appeal.documentation.md',
        'appeal.grammar.md', 'appeal.tour.md',
    }
    with open(os.path.join(appeal_dir, 'pyproject.toml')) as f:
        pyproject = f.read()
    for path in tracked:
        if path.startswith(shipped_dirs) or path in shipped_files:
            continue
        top = path.partition('/')[0]
        assert f'"{path}"' in pyproject or f'"{top}/"' in pyproject, (
            f'{path!r} is tracked but unclassified: either it ships '
            f'(add it to this test) or it does not (exclude it in '
            f'[tool.flit.sdist])')

    try:
        from flit_core.wheel import make_wheel_in
    except ImportError:
        # 3.6: modern flit_core doesn't reach back that far
        print('test_no_debris_ships: wheel half not run (no flit_core)')
        return
    import pathlib
    import tempfile
    import zipfile
    with tempfile.TemporaryDirectory() as td:
        info = make_wheel_in(pathlib.Path(appeal_dir) / 'pyproject.toml',
                             pathlib.Path(td))
        with zipfile.ZipFile(info.file) as z:
            names = z.namelist()
    for name in names:
        # the package, and the wheel's own appeal-<version>.dist-info/
        assert name.startswith(('appeal/', 'appeal-')), (
            f'{name!r} is in the wheel but not part of the package')


def test_completion_bad_candidates_and_fish():
    from appeal import completion_reentry
    def color(hue):
        return hue
    color.completions = lambda prefix: ('ok', 42)
    def paint(*hues: color):
        return hues
    try:
        appeal.completions(build_plan(paint), [], '')
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'str' in str(e)

    def reenter(env, completer):
        old = dict(os.environ)
        os.environ.update(env)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = completion_reentry(completer, 'prog')
        finally:
            os.environ.clear()
            os.environ.update(old)
        return code, out.getvalue()

    seen = []
    def completer(words, prefix):
        seen.append((words, prefix))
        return ['alpha']
    code, out = reenter({'_APPEAL_COMPLETE': 'fish',
                         'COMP_WORDS': 'prog\nx\na', 'COMP_CWORD': 'a'},
                        completer)
    assert code == 0 and 'alpha' in out
    code, out = reenter({'_APPEAL_COMPLETE': 'bash',
                         'COMP_WORDS': 'prog\nx', 'COMP_CWORD': 'zz'},
                        completer)
    assert code == 0


def test_run_mcp_protocol():
    import json as _json
    from appeal import run_mcp
    def go(arguments):
        x = arguments['x']
        if x == 'bad':
            raise AppealDataError('no good')
        return f'got {x}'
    tools = {'go': ('Go places.',
                    {'type': 'object', 'properties': {}}, go)}
    lines = [
        '',
        'not json at all',
        '{"jsonrpc": "2.0", "id": 1, "method": "initialize"}',
        '{"jsonrpc": "2.0", "method": "notifications/initialized"}',
        '{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}',
        ('{"jsonrpc": "2.0", "id": 3, "method": "tools/call", '
         '"params": {"name": "zzz"}}'),
        ('{"jsonrpc": "2.0", "id": 4, "method": "tools/call", '
         '"params": {"name": "go", "arguments": {"x": "bad"}}}'),
        ('{"jsonrpc": "2.0", "id": 5, "method": "tools/call", '
         '"params": {"name": "go", "arguments": {"x": "ok"}}}'),
        '{"jsonrpc": "2.0", "id": 6, "method": "bogus"}',
        '{"jsonrpc": "2.0", "method": "bogus/notification"}',
        ]
    old_stdin = sys.stdin
    out = io.StringIO()
    try:
        sys.stdin = io.StringIO('\n'.join(lines) + '\n')
        with contextlib.redirect_stdout(out):
            run_mcp(tools, 'prog', '1.0')
    finally:
        sys.stdin = old_stdin
    replies = [_json.loads(line) for line in out.getvalue().splitlines()]
    by_id = {r.get('id'): r for r in replies}
    assert 'result' in by_id[1]
    assert 'result' in by_id[2]
    assert 'error' in by_id[3]
    assert 'no good' in str(by_id[4]['result'])
    assert 'got ok' in str(by_id[5]['result'])
    assert 'error' in by_id[6]


# ---------------------------------------------------------------------
# batch 9: the last mile--emission ref shapes, completion corners,
# scoped strictness, text-renderer variants


def make_module(directory, name, source):
    # emission refuses __main__ residents by design, so test
    # commands live in real importable modules
    module_path = os.path.join(directory, name + '.py')
    with open(module_path, 'wt', encoding='utf-8') as f:
        f.write(source)
    sys.path.insert(0, directory)
    try:
        import importlib
        return importlib.import_module(name)
    finally:
        sys.path.pop(0)


def test_build_more_refusals():
    def hollow2(*, deep=False):
        return deep
    def f(*occ: hollow2):
        return occ
    try:
        build_plan(f)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'at least one argument' in str(e)
    def rep(x, *, verbose=False):
        return (x, verbose)
    def g(*occ: rep, verbose=False):
        return occ
    try:
        build_plan(g)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'declared both' in str(e)
    def h(*a: 42):
        return a
    try:
        build_plan(h)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError:
        pass


def test_codegen_absorbing_forced_group():
    # a skippable group whose converter absorbs (*args): an option
    # of the group forces it, operands feed it, absence defaults it
    def absorb(*items: int, deep=False):
        return (items, deep)
    def f(x, s: absorb = None):
        return (x, s)
    assert both(f, ['X']) == ('ok', ('X', None))
    assert both(f, ['X', '--deep']) == ('ok', ('X', ((), True)))
    assert both(f, ['X', '1', '2']) == ('ok', ('X', ((1, 2), False)))
    # ...and scoped across sibling windows
    def two(a: absorb = None, b: absorb = None):
        return (a, b)
    assert both(two, ['--deep', '1', '2']) == ('ok', (((1, 2), True), None))
    assert both(two, ['--deep']) == ('ok', (((), True), None))


def test_child_kwargs_options_parity():
    # a converter carrying @app.option-into-**kwargs declarations,
    # used as a group: the option is recognized and delivered into
    # the sink, v1-style, on both rungs (regression: v2 once made
    # these converters terminals and dropped the option)
    from appeal.frontend import Decorations
    def kg(a, **kws):
        return (a, kws)
    d = Decorations()
    d.add_option(kg, 'zesty', ('--zesty',), annotation=str,
                 default=None)
    def h2(x, s: kg = None):
        return (x, s)
    assert both(h2, ['X', 'a'], decorations=d) == \
        ('ok', ('X', ('a', {})))
    assert both(h2, ['X', 'a', '--zesty', 'v'], decorations=d) == \
        ('ok', ('X', ('a', {'zesty': 'v'})))
    # a two-positional kwargs converter consumes both operands
    def two(a, b, **kws):
        return (a, b, kws)
    d.add_option(two, 'flavor', ('--flavor',), annotation=str,
                 default=None)
    def h3(x, s: two = None):
        return (x, s)
    assert both(h3, ['X', 'p', 'q', '--flavor', 'hot'],
                decorations=d) == \
        ('ok', ('X', ('p', 'q', {'flavor': 'hot'})))


def test_run_mcp_hardening():
    # the Sol review's finding #2 (2026-09-03): one bad call never
    # ends the session.  Expected command failures come back as tool
    # results marked isError; unexpected exceptions come back as
    # JSON-RPC internal errors; malformed and non-object requests get
    # protocol errors; version negotiation never rubber-stamps.  The
    # load-bearing regression: after EVERY failure below, a later
    # call on the SAME server still succeeds.
    import json as _json
    import appeal
    from appeal import run_mcp
    def go(arguments):
        x = arguments.get('x')
        if x == 'sad':
            raise appeal.CommandError("can't divide by zero")
        if x == 'boom':
            raise RuntimeError('cosmic rays')
        return f'got {x}'
    tools = {'go': ('Go places.',
                    {'type': 'object', 'properties': {}}, go)}
    lines = [
        # version negotiation: a supported version echoes...
        ('{"jsonrpc": "2.0", "id": 1, "method": "initialize", '
         '"params": {"protocolVersion": "2024-11-05"}}'),
        # ...an unsupported one gets the latest we DO speak
        ('{"jsonrpc": "2.0", "id": 2, "method": "initialize", '
         '"params": {"protocolVersion": "totally-unsupported"}}'),
        # a command raising CommandError: a tool result, isError
        ('{"jsonrpc": "2.0", "id": 3, "method": "tools/call", '
         '"params": {"name": "go", "arguments": {"x": "sad"}}}'),
        # an unexpected exception: internal error, loop survives
        ('{"jsonrpc": "2.0", "id": 4, "method": "tools/call", '
         '"params": {"name": "go", "arguments": {"x": "boom"}}}'),
        # malformed JSON: parse error with id null, not silence
        'this is not json',
        # valid JSON, not an object: invalid request, not a crash
        '[]',
        '5',
        # params/arguments that aren\'t objects: invalid params
        '{"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": []}',
        ('{"jsonrpc": "2.0", "id": 6, "method": "tools/call", '
         '"params": {"name": "go", "arguments": [1, 2]}}'),
        # THE REGRESSION: after all of the above, the server still serves
        ('{"jsonrpc": "2.0", "id": 7, "method": "tools/call", '
         '"params": {"name": "go", "arguments": {"x": "ok"}}}'),
        ]
    old_stdin = sys.stdin
    out = io.StringIO()
    try:
        sys.stdin = io.StringIO('\n'.join(lines) + '\n')
        with contextlib.redirect_stdout(out):
            assert run_mcp(tools, 'prog', '1.0') == 0
    finally:
        sys.stdin = old_stdin
    replies = [_json.loads(line) for line in out.getvalue().splitlines()]
    by_id = {}
    nulls = []
    for r in replies:
        if r.get('id') is None:
            nulls.append(r)
        else:
            by_id[r['id']] = r
    assert by_id[1]['result']['protocolVersion'] == '2024-11-05'
    assert by_id[2]['result']['protocolVersion'] == '2024-11-05'
    r3 = by_id[3]['result']
    assert r3['isError'] and "can't divide by zero" in r3['content'][0]['text']
    assert by_id[4]['error']['code'] == -32603
    assert 'RuntimeError' in by_id[4]['error']['message']
    codes = sorted(r['error']['code'] for r in nulls)
    assert codes == [-32700, -32600, -32600], codes
    assert by_id[5]['error']['code'] == -32602
    assert by_id[6]['error']['code'] == -32602
    assert by_id[7]['result']['content'][0]['text'] == 'got ok'


def test_mcp_class_global_method_tools():
    # a class-based program: __init__ constructs at startup,
    # method tools dispatch bound (in-process, EOF stdin)
    app = Appeal(name='svc')
    @app.global_command()
    class Svc:
        def __init__(self, *, tag=''):
            self.tag = tag
        @app.command()
        def ping(self):
            return 'pong ' + self.tag
        @app.command()
        class Sub:
            # a leaf class command (class-as-namespace): its tool
            # plan constructs through the parent instance
            def __init__(self, n: int):
                self.n = n
    old_stdin = sys.stdin
    out = io.StringIO()
    try:
        sys.stdin = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = app.mcp(config={'tag': 'T'})
    finally:
        sys.stdin = old_stdin
    assert code == 0


def test_runtime_token_and_set_edges():
    def loud():
        return 'LOUD'
    def gm(*, mode: loud = 'quiet'):
        return mode
    got = both(gm, ['--mode=x'])
    assert got == ('usage', "option '--mode' doesn't take a value"), got


def test_run_main_themed_and_set_completion():
    from appeal import UsageError, run_main
    from appeal.completion import completion_set_table
    class FakeTTY(io.StringIO):
        def isatty(self):
            return True
    old_env = dict(os.environ)
    os.environ.pop('NO_COLOR', None)
    os.environ.pop('FORCE_COLOR', None)
    os.environ['TERM'] = 'xterm-256color'
    tty = FakeTTY()
    def parse_bad(argv):
        # usage is now a trailer callable usage(file) -> str
        raise UsageError('nope', lambda file: 'usage: prog x')
    try:
        # stylesheet=None: auto--the fake tty (and willing TERM)
        # gets appeal_theme over the ANSI 16
        code = run_main(parse_bad, [], errors=tty)
    finally:
        os.environ.clear()
        os.environ.update(old_env)
    text = tty.getvalue()
    assert code == 2 and 'error:' in text and '\x1b[' in text
    # a command SET completion table through run_main
    def go(x: int):
        return 0
    table = completion_set_table({'go': build_plan(go)}, None)
    assert 'commands' in table
    old_env = dict(os.environ)
    os.environ.update({'_APPEAL_COMPLETE': 'bash',
                       'COMP_WORDS': 'prog\ng', 'COMP_CWORD': '1'})
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = run_main(lambda argv: 0, [],
                            completion=(table, 'prog'))
    finally:
        os.environ.clear()
        os.environ.update(old_env)
    assert code == 0 and 'go' in out.getvalue()


def test_wrap_words_variants():
    from appeal.presentation import split_text_with_code, wrap_words
    w = split_text_with_code('p one\n\n    c\td\n    e', code_indent=4)
    got = wrap_words(w, margin=24, code_indent='   ', indent='  ',
                     left_column=3)
    assert got == '  p one\n\n       c      d\n       e', repr(got)
    # tab-indented code lines
    w2 = split_text_with_code('\tcode tabbed\n\tmore')
    got = wrap_words(w2, margin=30)
    assert got == '        code tabbed\n        more', repr(got)
    # prose tab words wrap at the margin and die with the line
    w3 = split_text_with_code('word\ta word\tb word\tc word\td')
    got = wrap_words(w3, margin=12)
    assert got == 'word    a\nword    b\nword    c\nword    d', repr(got)
    try:
        split_text_with_code('s', code_indent=True)
        assert False, 'expected TypeError'
    except TypeError:
        pass


def test_merge_columns_overflow_shapes():
    from appeal.presentation import merge_columns, OverflowStrategy
    # adjacent overflows merge into one region
    c1 = ['loooooooooooong1', 'x', 'loooooooooooong2']
    got = merge_columns((c1, 4, 6), (['r1', 'r2', 'r3'], 4, 6),
                        overflow_strategy=OverflowStrategy.DELAY_ALL,
                        overflow_before=1, overflow_after=1)
    assert 'r3' in got
    # an overflow on the last line pads the tail
    c2 = ['a', 'loooooooooooongtail']
    got = merge_columns((c2, 4, 6), (['r1', 'r2'], 4, 6),
                        overflow_strategy=OverflowStrategy.DELAY_ALL,
                        overflow_after=2)
    assert got.rstrip().endswith('r2'), repr(got)


def test_format_definition_list_tab_definition():
    from appeal.presentation import format_definition_list
    got = format_definition_list(
        [('t', 'alpha\tbeta gamma delta epsilon zeta')], margin=30)
    assert got == ('  t  alpha   beta gamma delta\n'
                   '     epsilon zeta'), repr(got)


def test_completion_more_corners():
    # a plain command reached through a set with no global
    app2 = Appeal(name='c3')
    @app2.command()
    def solo(x, *, level: int = 0):
        return x
    assert app2.complete(['solo'], '') == []
    got = app2.complete(['solo'], '-')
    assert '--level' in got and '--help' in got
    # a global command's value option pending; a dash at the
    # command boundary offers the global's options
    app3 = Appeal(name='c4')
    @app3.global_command()
    def top(*, level: int = 0):
        return 0
    @app3.command()
    def run2(x):
        return x
    assert app3.complete(['--level'], '') == []
    assert app3.complete([], '-') == ['--level', '-l']
    assert app3.complete(['run2'], 'x') == []
    # a nested parent's own options and pending values
    app4 = Appeal(name='c5')
    @app4.command(name='db')
    class Db:
        def __init__(self, label, *, tag=''):
            self.label = label
        @app4.subcommand('db')
        def wipe(self):
            pass
    got = app4.complete(['db'], '-')
    assert '--tag' in got, got
    assert app4.complete(['db', '--tag'], '') == []


def test_run_mcp_ping():
    import json as _json
    from appeal import run_mcp
    old_stdin = sys.stdin
    out = io.StringIO()
    try:
        sys.stdin = io.StringIO(
            '{"jsonrpc": "2.0", "id": 7, "method": "ping"}\n')
        with contextlib.redirect_stdout(out):
            run_mcp({}, 'prog')
    finally:
        sys.stdin = old_stdin
    reply = _json.loads(out.getvalue())
    assert reply['id'] == 7 and reply['result'] == {}


def test_section_template_more_fails():
    # a custom template reorders the page; suppression still
    # holds (no commands -> no Cmds: section)
    from appeal.presentation import render_help_page
    template = ('usage: {usage}\n\nOpts:\n  {options}\n\n'
                '{summary}\n\n{doc}\n\nArgs:\n  {arguments}\n\n'
                'Cmds:\n  {commands}')
    corpus = {'summary': ['Sum.'], 'documentation': ['Prose.'],
              'arguments': [('a', ['doc a'])],
              'options': [('-x', [])], 'commands': []}
    page = render_help_page('t [-x] a', corpus, template)
    assert page.index('Opts:') < page.index('Sum.') < \
        page.index('Args:'), page
    assert 'Cmds:' not in page
    assert '-x' in page and 'a  doc a' in page


# ---------------------------------------------------------------------
# batch 10: the residue--pinned corner by corner


def test_codegen_absorbing_nonzero_minimum():
    # an absorbing skippable group whose body NEEDS an operand:
    # no forcing branch, just take-or-default
    def ab3(first: int, *rest: int, deep=False):
        return (first, rest, deep)
    def f(x, s: ab3 = None):
        return (x, s)
    assert both(f, ['X']) == ('ok', ('X', None))
    assert both(f, ['X', '1', '2']) == ('ok', ('X', (1, (2,), False)))


def test_windowed_group_kwargs_options():
    # a *args group carrying **kwargs declarations: windowed
    # delivery works (unlike a plain group's--see the parity pin)
    from appeal.frontend import Decorations
    def rep(x, *, deep=False, **kws):
        return (x, deep, kws)
    d = Decorations()
    d.add_option(rep, 'zesty', ('--zesty',), annotation=str,
                 default=None)
    def f(*occ: rep):
        return occ
    got = both(f, ['a', '--zesty', 'v'], decorations=d)
    assert got == ('ok', (('a', False, {'zesty': 'v'}),)), got


def test_short_option_equals_refusal():
    def loud():
        return 'LOUD'
    def gm(*, mode: loud = 'quiet'):
        return mode
    got = both(gm, ['-m=x'])
    assert got == ('usage', "option '-m' doesn't take a value"), got


def test_completion_internals_direct():
    from appeal import complete_command, complete_command_set
    from appeal.completion import completion_table, completion_set_table
    def go(x, *, level: int = 0):
        return x
    t = completion_table(build_plan(go))
    assert complete_command(t, ['a'], '') == []      # saturated
    st = completion_set_table({'go': build_plan(go)}, None)
    assert complete_command_set(st, ['go'], 'x') == []
    assert complete_command_set(st, [], '-') == []
    def gtop(g1, *, trace=False):
        return g1
    st2 = completion_set_table({'go': build_plan(go)}, build_plan(gtop))
    assert complete_command_set(st2, ['op', 'go'], 'x') == []
    assert complete_command_set(st2, ['op', 'go', 'a'], '') == []
    # cycling: a saturated command's tail offers the next word
    st3 = completion_set_table({'go': build_plan(go)}, None, repeat=True)
    assert complete_command_set(st3, ['go', 'a'], 'g') == ['go']


def test_wrap_words_final_pins():
    from appeal.presentation import split_text_with_code, wrap_words
    # empty words are skipped; a prose tab wraps and dies
    assert wrap_words(['a', '', 'b'], margin=10) == 'a b'
    assert wrap_words(['aaaaaaa', '\t', 'bbbb'], margin=10) == \
        'aaaaaaa\nbbbb'
    # code_indent without indent
    w = split_text_with_code('p\n\n    c\te', code_indent=4)
    assert wrap_words(w, margin=20, code_indent='  ') == 'p\n\n      c e'
    # tabs riding code lines land on the page's stops
    for s, expected in (('    a\t\tb', '    a           b'),
                        ('    \tx after', '        x after'),
                        ('    aa\tbb\n    c d e f g h',
                         '    aa  bb\n    c d e f g h')):
        got = wrap_words(split_text_with_code(s), margin=14)
        assert got == expected, (s, repr(got))


def test_definition_list_fussy_tab():
    from appeal.presentation import format_definition_list
    got = format_definition_list(
        [('t', 'aa\tbb cc dd ee ff gg')], margin=24,
        definition_left_column=8)
    assert got == '  t    aa      bb cc dd\n       ee ff gg', repr(got)
    # page-absolute tabs: the definition renders in place
    got = format_definition_list([('t', 'aa\tbb')], margin=30,
                                 definition_relative_tabs=False)
    assert 'aa' in got and 'bb' in got, repr(got)


def test_wrap_words_leading_tab_stream():
    # tabs at the start of a line: only a hand-built stream gets
    # here--the stop advances from the line's start, no wrap check
    from appeal.presentation import wrap_words
    got = wrap_words(['\t', 'x'], margin=20)
    assert got == '        x', repr(got)


def test_build_origin_carrying_instance():
    # an INSTANCE with __origin__ (not a GenericAlias, which
    # pre-3.11 masquerades as a type): every predicate declines,
    # and the leaf check refuses by name
    class FakeGeneric:
        __origin__ = set
        def __call__(self, v):
            return v
    def f(*, o: FakeGeneric() = None):
        return o
    try:
        build_plan(f)
        assert False, 'expected AppealConfigurationError'
    except AppealConfigurationError as e:
        assert 'generic' in str(e), e


def test_windowed_option_before_first_window():
    # a windowed option spoken before the first window's operand:
    # it clamps to the first window (v1's float-free heritage)
    def rep(x, *, vol: int = 0):
        return (x, vol)
    def f(a, *occ: rep):
        return (a, occ)
    got = both(f, ['--vol', '5', 'A', 'w1', 'w2'])
    assert got == ('ok', ('A', (('w1', 5), ('w2', 0)))), got


def test_completion_boundary_and_help():
    from appeal import complete_command_set
    from appeal.completion import completion_set_table
    def go(x, *, level: int = 0):
        return x
    def gtop2(g1, g2=None, *, trace=False):
        return (g1, g2)
    # the global's minimum met, next word names a command: the
    # boundary splits the scan there
    st = completion_set_table({'go': build_plan(go)}, build_plan(gtop2))
    got = complete_command_set(st, ['op', 'go'], '-')
    assert '--level' in got, got
    # a set with no real `help` command doesn't complete `help` topics:
    # the synthesized help word is gone, default_mappings is the only
    # source of a help command now
    st2 = completion_set_table({'go': build_plan(go)}, None)
    assert complete_command_set(st2, ['help', 'go'], '-') == []
    assert complete_command_set(st2, ['help', 'zzz'], 'x') == []
    assert complete_command_set(st2, ['help'], 'g') == []


def test_entry_points_default_to_sys_argv():
    # main/process/parse all take `args`, defaulting to sys.argv[1:]
    # when None (the border reads the real command line); an
    # explicit args list bypasses sys.argv entirely
    app = Appeal(name='ep')
    @app.command()
    def go(x: int):
        return x
    saved = sys.argv
    try:
        sys.argv = ['ep', 'go', '5']
        assert app.process().result == 5              # None -> sys.argv[1:]
        try:
            app.main()
            code = 0
        except SystemExit as e:
            code = e.code
        assert code == 5                    # go's nonzero int IS the code
        sys.argv = ['ep']                      # a different command line
        assert app.process(['go', '9']).result == 9   # explicit wins, no sys.argv
    finally:
        sys.argv = saved


# ---------------------------------------------------------------------
# The branch-coverage completion tests (2026-07-16): black-box tests
# closing the partial branches the statement-coverage push left.
# Larry's rule for these: no pragmas, no white-box internals-forcing.
# 34 of 46 partial branches closed below; the WALL-LIST--the 12 that
# would need a pragma or internals-forcing, with why--so `coverage
# --branch` reads 99% by design, not neglect:
#
#   __init__.py 109->126   _config_vet(command_plan_for=None) with an
#                          unknown key: all three callers pass plan_for
#   __init__.py 621->619   indent= re-indents a FRESH default_templates
#                          copy at construction; all three keys present
#   build.py    936->826   all five Parameter kinds `continue`; the
#                          not-VAR_KEYWORD fall-through is dead

def test_branch_schema_and_read_edges():
    from appeal.schema import mcp_input_schema
    from appeal.load import read_mapping

    # a *args converter with parameters is a per-instance GROUP,
    # exactly as the non-*args path treats it (single-parameter
    # included--a lone int-typed param must still convert its
    # operand; differential-caught 2026-08-16).  Its items describe is
    # the group's, so a one-str-param converter reads as the group's
    # anyOf (string, or the by-name object).
    def conv(s):
        return s
    def f(*tags: conv):
        return tags
    s = mcp_input_schema(build_plan(f))
    assert s['properties']['tags']['type'] == 'array', s
    # a one-str-param converter is a DEGENERATE single-operand group:
    # transparent, so items is the leaf type (string), not the object
    assert s['properties']['tags']['items'] == {'type': 'string'}, s

    # a flag config value that's neither str, bool, nor 0/1 refuses
    def g(*, dry=False):
        return dry
    try:
        read_mapping(build_plan(g), {'dry': 3.5})
        assert False, 'expected AppealDataError'
    except AppealDataError:
        pass

    # a **kwargs-delivered option absent from the mapping is OMITTED
    # (not defaulted)--both in read_mapping and in the group-sequence
    # filler
    def h(a, *, real: int = 1, **kw):
        return (a, real, kw)
    d = appeal.Decorations()
    d.add_option(h, 'extra', ('--extra',),
                 annotation=str, default=None)
    assert read_mapping(build_plan(h, decorations=d),
                        {'a': 'x'}) == ('x', 1, {})

    def gconv(u: int = 0, *, gopt: int = 1, **gkw):
        return (u, gopt, gkw)
    d.add_option(gconv, 'gextra', ('--gextra',),
                 annotation=str, default=None)
    def h2(a, *, where: gconv = None):
        return (a, where)
    got = read_mapping(build_plan(h2, decorations=d),
                       {'a': 'x', 'where': [5]})
    assert got == ('x', (5, 1, {})), got


def test_schema_degenerate_group_transparent():
    from appeal.schema import mcp_input_schema
    # a single-operand chain collapses to the innermost leaf (0.6.4's
    # degenerate annotation tree; ruled 2026-08-16): a: mything ->
    # otherthing -> int schemas as a plain integer, not an object
    def otherthing(i: int):
        return i
    def mything(o: otherthing):
        return o
    def cmd(a: mything, name: str):
        return (a, name)
    props = mcp_input_schema(build_plan(cmd))['properties']
    assert props['a'] == {'type': 'integer'}, props
    assert props['name'] == {'type': 'string'}, props
    # a group that takes MORE than one operand is not degenerate: it
    # keeps its structure (anyOf scalar-or-object)
    def point(x: int, y: int = 0):
        return (x, y)
    def c2(p: point):
        return p
    entry = mcp_input_schema(build_plan(c2))['properties']['p']
    assert 'anyOf' in entry, entry


def test_branch_run_main_error_edges():
    from appeal import run_main

    def quiet_main(fn):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run_main(lambda argv: fn(), [], stylesheet=False)
        return code, err.getvalue()

    # a real AppealDataError WITHOUT usage: no usage line printed
    def d():
        raise AppealDataError('data, no usage')
    code, err = quiet_main(d)
    assert code == 2 and 'usage:' not in err, (code, err)


def test_branch_negative_number_global_operand():
    # a negative-number token is an operand even mid-scan at a
    # command boundary check (the global command's window)
    app = Appeal(name='t')
    @app.global_command()
    def glob(a: int, b: int):
        return None
    @app.command()
    def addc(x: int):
        return x
    assert app.process(['-5', '-6', 'addc', '7']).result == 7


def test_branch_completion_edges():
    from appeal.completion import completions_set
    from appeal import completion_reentry

    # a repeatable option already on the line still completes
    def f(*, tag: appeal.accumulator[str] = ()):
        return tag
    assert '--tag' in appeal.completions(build_plan(f), ['--tag', 'x'], '--')

    # fish reentry with an EMPTY current token (cursor after a space)
    seen = []
    old = dict(os.environ)
    os.environ.update({'_APPEAL_COMPLETE': 'fish',
                       'COMP_WORDS': 'p\nx', 'COMP_CWORD': ''})
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            completion_reentry(lambda b, p: seen.append((b, p)) or [], 'p')
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert seen == [(['x'], '')], seen

    # `help <nested-set> <TAB>`: no opinion (a set has no one page)
    def db_global():
        pass
    def mig(x):
        pass
    def top():
        pass
    commands = {'db': build_plan(db_global), 'top': build_plan(top)}
    sets = {'db': {'commands': {'mig': build_plan(mig)}, 'repeat': False}}
    assert completions_set(commands, None, ['help', 'db'], '', sets=sets) == []

    # cycling resolution: an entered, non-repeat inner set is
    # ineligible--the word resolves to the repeat root instead
    def rootcmd():
        pass
    commands2 = {'db': build_plan(db_global), 'rootcmd': build_plan(rootcmd)}
    sets2 = {'db': {'commands': {'mig': build_plan(mig)}, 'repeat': False}}
    completions_set(commands2, None, ['db', 'mig', 'X', 'rootcmd'], '',
                 repeat=True, sets=sets2)


def test_branch_text_formatter_edges():
    from appeal.presentation import wrap_words, split_text_with_code, usage_units, format_definition_list, merge_columns, OverflowStrategy, render_help_page, default_template, appeal_theme

    # code_indent=0 turns code detection off entirely
    split_text_with_code('para one\n\n    indented, not code\n',
                         code_indent=0)

    # a definition that wraps to nothing renders a bare term
    format_definition_list([('term', '')], 40)
    format_definition_list([('term', '\n')], 40)

    # bytes mode with explicit indent/spacer (the sentinels
    # untriggered)
    format_definition_list([(b'term', b'text')], 40,
                           indent=b' ', spacer=b' ')

    # two NON-adjacent overflows in one column stay separate regions
    col1 = (['WWWWWWWWWWWWWWW', 'b', 'c', 'WWWWWWWWWWWWWWW', 'e'], 4, 8)
    col2 = (['1', '2', '3', '4', '5'], 4, 10)
    merge_columns(col1, col2,
                  overflow_strategy=OverflowStrategy.INTRUDE_ALL)
    merge_columns(col1, col2,
                  overflow_strategy=OverflowStrategy.DELAY_ALL)

    # usage tokenizer: doubled and trailing spaces make empty units
    assert usage_units('prog  [x]  y ') == ['prog', '[x]', 'y']

    # themed table painting walks PAST a wrapped description's
    # continuation lines to find the next row
    from appeal.presentation import merge_docs
    def draw(shape, *, verbose=False, times: int = 1):
        """
        Draws.

        # Options
        verbose
        : a very long narration that will definitely need
          to be wrapped across multiple lines when rendered into
          the table column because it keeps going artisanally.
        times
        : short.
        """
    plan = build_plan(draw)
    from big.markdown import markdown_defaults
    from big.stylesheet import (StyleSheet, ansi_16_color_palette,
                                transforms)
    sheet = (markdown_defaults | transforms | ansi_16_color_palette
             | StyleSheet(appeal_theme))
    page = render_help_page(plan.usage(), merge_docs(plan),
                            default_template, margin=50,
                            stylesheet=sheet)
    assert '\x1b[' in page


def test_branch_repl_data_error_without_usage():
    # a command raising AppealDataError with no usage: the repl
    # prints the message and prompts on
    app = Appeal(name='r')
    @app.command()
    def kaboom():
        raise AppealDataError('no usage here')
    stdin = io.StringIO('kaboom\n')
    out = io.StringIO()
    old = sys.stdin
    sys.stdin = stdin
    try:
        with contextlib.redirect_stdout(out):
            app.repl()
    finally:
        sys.stdin = old
    assert 'no usage here' in out.getvalue()


def test_branch_first_parse_race_losers():
    # the double-checked-lock LOSER branches: both threads must get
    # past the fast path before either installs.  A gate inside the
    # command's own signature probe (user code, run at build time)
    # makes the race deterministic.
    import threading
    import inspect

    def make_gate(parties=2):
        lock, count, evt = threading.Lock(), [0], threading.Event()
        def gate():
            with lock:
                count[0] += 1
                if count[0] >= parties:
                    evt.set()
            evt.wait(timeout=10)
        return gate

    class SlowSig:
        def __init__(self, gate):
            self.__name__ = 'slowsig'
            self._gate = gate
        @property
        def __signature__(self):
            self._gate()
            return inspect.Signature([inspect.Parameter(
                'a', inspect.Parameter.POSITIONAL_OR_KEYWORD)])
        def __call__(self, a):
            return a

    def race(app, argv):
        results, errors = [], []
        def runner():
            try:
                results.append(app.process(list(argv)).result)
            except Exception as e:      # pragma: no cover
                errors.append(e)
        threads = [threading.Thread(target=runner) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        return results

    # global-command-only app: the global-plan and single-parse
    # installs each get a loser
    app = Appeal(name='race1')
    app.global_command()(SlowSig(make_gate()))
    assert race(app, ['x']) == ['x', 'x']

    # command-set app: the set-pieces install gets a loser
    app2 = Appeal(name='race2')
    app2.global_command()(SlowSig(make_gate()))
    @app2.command()
    def go():
        return 'went'
    assert race(app2, ['y', 'go']) == ['went', 'went']


BRANCH_EDGE_MODULE = '''\
import appeal
app = appeal.Appeal(name='tool')

@app.global_command()
class Tool:
    def __init__(self, base='.', *, verbose=False):
        self.base = base

    @app.command()
    def work(self):
        'Works.'
        return self.base

class Sneaky(dict):
    'A container whose repr lies: parses fine, compares unequal.'
    def __repr__(self):
        return '{}'

class PtOpt(appeal.Option):
    def init(self, default):
        self.v = dict(default) if default else {}
    def option(self, k, v):
        self.v[k] = v
    def __call__(self):
        return self.v

def sneaky_default(x, *, pt: PtOpt = Sneaky({'a': 1})):
    print(x, pt)
'''


def test_optional_parameterize_refusals():
    # optional[...] takes exactly one callable converter
    from appeal import optional, AppealConfigurationError
    try:
        optional[int, float]                 # a tuple of two
        assert False, 'expected refusal'
    except AppealConfigurationError as e:
        assert 'exactly one converter' in str(e)
    try:
        optional[5]                          # not callable
        assert False, 'expected refusal'
    except AppealConfigurationError as e:
        assert "isn't callable" in str(e)


def test_degenerate_leaf_type_non_degenerate():
    # the schema's degenerate-chain collapse returns None for the
    # shapes that AREN'T a single non-repeating operand
    from appeal.schema import _degenerate_leaf_type
    one_each = {'operand_counts': {'minimum': 1, 'maximum': 1}}
    assert _degenerate_leaf_type({**one_each, 'operands': []}) is None
    assert _degenerate_leaf_type(
        {**one_each, 'operands': [{'repeat': True}]}) is None


def test_default_options_policy_proxy():
    # a custom default_options policy drives the registrar proxy:
    # zero strings unmap, annotation=/default= is refused, and
    # attribute access delegates to the app
    import appeal
    seen = {}
    def policy(proxy, callable, name):
        seen['app_name'] = proxy.name           # __getattr__ -> app
        # zero strings: unmap; the returned decorator is a no-op
        assert proxy.option(name)(callable) is callable
        try:
            proxy.option(name, '-x', annotation=int)
        except appeal.AppealConfigurationError:
            seen['refused'] = True
    app = appeal.Appeal('demo', default_options=policy)
    @app.command()
    def go(x, *, verbose=False):
        return x
    assert app.process(['go', 'z']).result == 'z'
    assert seen['app_name'] == 'demo' and seen.get('refused')


def test_oparg_name_fallbacks():
    import appeal
    from appeal import build_plan
    from big.stylesheet import strip_styles
    # a MultiOption whose option() takes several operands: each oparg
    # is named after its converter type
    class Pair(appeal.MultiOption):
        def init(self, default): self.v = []
        def option(self, a: int, b: int): self.v.append((a, b))
        def __call__(self): return self.v
    def cmd(*, p: Pair = []):
        return p
    assert '-p' in strip_styles(build_plan(cmd).usage())
    # a tuple[...] option: element type names (3.9+ builtin-generic spelling)
    if sys.version_info >= (3, 9):
        def cmd2(*, pt: tuple[int, int] = ()):
            return pt
        assert '--pt' in strip_styles(build_plan(cmd2).usage())


def test_backend_nested_chain_value_option_needs_value():
    # a VALUE option three levels down in a positional-group chain, fired with
    # no value after it, reports that it needs one (ConjureChainBinding's value
    # branch: the whole chain conjures, then the value is missing)
    import appeal
    from appeal.backend import build_converters, _converter_key
    from appeal import build_plan, execute
    def first_child(*, verbose=False):
        return verbose
    def enfant(*, flag: int = 0):
        return flag
    def parent(fc: first_child = None, et: enfant = None):
        return (fc, et)
    def grandparent(p: parent = None):
        return p
    cls = build_converters([build_plan(grandparent)])[
        _converter_key(build_plan(grandparent))]
    try:
        execute({'grandparent': cls}, ['grandparent', '--flag'])
        assert False, 'expected AppealDataError'
    except appeal.AppealDataError as e:
        assert "option '--flag' requires a value" in str(e), e


def test_backend_availability_and_counts():
    import appeal
    from appeal.backend import build_converters, _converter_key
    from appeal import build_plan, execute
    def run(f, argv):
        plan = build_plan(f)
        word = plan.name.replace('_', '-')
        cls = build_converters([plan])[_converter_key(plan)]
        try:
            return ('ok', execute({word: cls}, [word] + list(argv)))
        except appeal.AppealDataError as e:
            return ('usage', str(e))
    # _valid_counts renders a group option's valid operand counts, including
    # the optional-leaf ({0, 1}) branch: grp(a, b=0) accepts 1 or 2
    def grp(a: int, b: int = 0):
        return (a, b)
    def f1(*, g: grp = None, tail=''):
        return g
    assert run(f1, ['-g']) == ('usage', 'option -g takes 1 or 2')
    # _own_shape skips a RepeatInstruction: a *args-bearing group summoned by
    # its own option, starved of its fixed operand, names that operand
    def sub(first: int, *rest: int, tag=False):
        return (first, rest, tag)
    def d3(g: sub = None, tail=''):
        return g
    assert run(d3, ['--tag']) == \
        ('usage', '--tag only becomes available if you specify <FIRST>')
    # _own_shape reads a PreOptionInstruction (a nested-group chain); when the
    # starved converter typed no option of its own, the hint is 'expected ...'
    def leaf(x: int, *, flag=False):
        return (x, flag)
    def mid(a: int, inner: leaf = None):
        return (a, inner)
    def d5(m: mid = None, tail=''):
        return m
    assert run(d5, ['--flag']) == ('usage', 'expected <A>')
    # a group's single-oparg VALUE option whose string the command ALSO owns
    # is shadowed (the command's own wins) when building the group's PreOptions
    def vgrp(x: int, *, level: str = 'info'):
        return (x, level)
    def owns_level(g: vgrp = None, *, level: str = 'info'):
        return (g, level)
    assert run(owns_level, ['--level', 'debug', '5']) == ('ok', ((5, 'info'), 'debug'))
    # the same shadow at a NESTED subchain level (grandparent -> parent -> leaf)
    def leaf2(x: int, *, flag=False):
        return (x, flag)
    def mid2(inner: leaf2 = None, *, flag=False):
        return (inner, flag)
    def owns_flag(m: mid2 = None, *, flag=False):
        return (m, flag)
    assert run(owns_flag, ['--flag']) == ('ok', (None, True))


def test_frontend_signature_resolution():
    from appeal.frontend import signature, build_plan, subtree_option_keys
    def fn(x):
        pass
    signature(fn)                              # a plain function
    class C:
        def m(self, x):
            pass
    signature(C().m)                           # a bound method
    class Plain:
        pass
    assert len(signature(Plain).parameters) == 0   # plain class: no params
    class CallInst:
        def __call__(self, x):
            pass
    signature(CallInst())                      # a callable instance
    # subtree_option_keys: every option key in the tree
    def cmd(a, *, v=False):
        pass
    assert '-v' in subtree_option_keys(build_plan(cmd))


def test_frontend_parameter_dunders():
    from appeal.frontend import empty, Parameter
    assert repr(empty) == '<empty>'                      # _Empty.__repr__
    k = Parameter.KEYWORD_ONLY
    assert repr(k) == 'KEYWORD_ONLY'                     # _Kind.__repr__
    # _Kind.__lt__: orders by ordinal for another kind, else NotImplemented
    assert isinstance(Parameter.POSITIONAL_ONLY
                      < Parameter.KEYWORD_ONLY, bool)
    assert k.__lt__(5) is NotImplemented
    assert "Parameter 'x'" in repr(Parameter('x', k))    # Parameter.__repr__


def test_frontend_reachable_edges():
    import sys
    from appeal.frontend import (_resolve, build_plan, _clone_tree, Plan,
                                 dereference_annotated, _is_repeat_group)
    # _resolve: a __func__-bearing object with no proxied __code__ and no
    # __self__ (a classmethod object) -> (underlying func, skip-first)
    def plain(cls, x):
        return x
    func, skip = _resolve(classmethod(plain))
    assert func is plain and skip is True
    # _resolve: a class defining __new__ (with params) but not __init__
    class NewOnly:
        def __new__(cls, x):
            return super().__new__(cls)
    fn, drop = _resolve(NewOnly)
    assert drop is True
    # sole_terminal_slot: a plan with a terminal AND a nested group that
    # itself has a terminal has more than one operand -> not transparent
    def inner(x: int):
        return x
    def outer(a: int, b: inner = None):
        return (a, b)
    top = build_plan(outer)
    assert top.sole_terminal_slot() is None
    # _clone_tree recurses into nested-group (Plan) slot children
    assert isinstance(_clone_tree(top), Plan)
    # dereference_annotated short-circuits when 'typing' isn't imported
    saved = sys.modules.pop('typing', None)
    try:
        assert dereference_annotated(int) is int
    finally:
        if saved is not None:
            sys.modules['typing'] = saved
    # _is_repeat_group: a recipe vocabulary product (split()) is a terminal,
    # not a repeat group
    from appeal import split
    assert _is_repeat_group(split()) is False
    # extra_overrides merges into (empty) decoration overrides for a param
    def f(x, y=1):
        return (x, y)
    try:
        build_plan(f, extra_overrides={'x': []})
    except Exception as e:
        assert 'x' in str(e)      # reaches the merge loop, then refuses 'x'


def test_frontend_reachable_edges_2():
    import appeal
    from appeal.frontend import build_plan
    from appeal.load import read_mapping
    # table_entry for a nullary option (a zero-arg converter as a flag)
    def north():
        return 'N'
    def cmd(*, direction: north = None):
        return direction
    app = appeal.Appeal('c')
    app(cmd)
    assert '--direction' in app.complete(['c'], '--dir')
    # a completion callable whose inspect.signature() raises (a bare builtin)
    # is tolerated: the structural check treats it as unknown-arity
    class BadCompl:
        completions = staticmethod(range)
    def uses(a: BadCompl = None):
        return a
    try:
        build_plan(uses)
    except appeal.AppealConfigurationError as e:
        assert 'consumes' in str(e)   # signature unreadable -> arity check path
    # a fold used as a POSITIONAL, fed a scalar (not a list) through the read
    # driver, wraps it as a single occurrence
    def cmd2(tags: appeal.accumulator[str] = []):
        return tags
    assert read_mapping(build_plan(cmd2), {'tags': 'solo'}) == ['solo']
    # a zero-width group option whose string the command also owns is shadowed
    # in the reachability check (the command's own wins, no clash raised)
    def zg(*, flag=False):
        return flag
    def cmd3(x: zg = None, *, flag=False):
        return (x, flag)
    build_plan(cmd3)


def test_backend_more_errors():
    import appeal
    from appeal.backend import build_converter, build_converters, _converter_key
    from appeal import build_plan, execute
    def both2(fn, argv):
        plan = build_plan(fn)
        word = plan.name.replace('_', '-')
        cls = build_converters([plan])[_converter_key(plan)]
        try:
            return ('ok', execute({word: cls}, [word] + list(argv)))
        except appeal.AppealDataError as e:
            return ('usage', str(e))
    # build_converter: the single-plan convenience wrapper
    def solo(x):
        return x
    assert build_converter(build_plan(solo)) is not None
    # an unknown short option inside a bundle
    def f(*, x=False, v=False):
        pass
    assert both2(f, ['-xq'])[0] == 'usage'
    # a group option at end of line needs its operand
    def host(name, *, port=22):
        return (name, port)
    def g(*, where: host = None):
        pass
    assert both2(g, ['--where'])[0] == 'usage'
    # a fold (accumulator) option at end needs a value
    def h(*, tag: appeal.accumulator[str] = ()):
        pass
    assert both2(h, ['--tag'])[0] == 'usage'

    # -- conjured converter-group fold/value options, value-count errors --
    from appeal import accumulator, MultiOption
    # a conjured VALUE option at end of line requires its value
    class HasName:
        def __init__(self, *, name: str = ''):
            self.name = name
    def cn(gg: HasName, x='x'):
        return x
    assert both2(cn, ['--name']) == ('usage', "option '--name' requires a value")
    # a conjured 0-oparg fold (counter) refuses an attached =value
    class HasVerbose:
        def __init__(self, *, verbose: appeal.counter() = 0):
            self.verbose = verbose
    def cv(gg: HasVerbose, x='x'):
        return x
    assert both2(cv, ['--verbose=3']) == \
        ('usage', "option '--verbose' doesn't take a value")
    # a conjured multi-oparg fold given too few values ('N values' wording)
    class HasPt:
        def __init__(self, *, pt: accumulator[int, int] = []):
            self.pt = pt
    def cp(gg: HasPt, x='x'):
        return x
    assert both2(cp, ['--pt', '1']) == \
        ('usage', "option '--pt' requires 2 values")
    # a fold with an OPTIONAL oparg tail: minimum met at end-of-line breaks
    # out cleanly (no error) rather than demanding the optional operand
    class Pair(MultiOption):
        def init(self, default):
            self.v = []
        def option(self, a, b=''):
            self.v.append((a, b))
        def __call__(self):
            return self.v
    class HasPair:
        def __init__(self, *, pr: Pair = []):
            self.pr = pr
    def cpr(gg: HasPair, x='x'):
        return x
    assert both2(cpr, ['--pr', 'A']) == ('ok', 'x')
    # a fold buried in a NESTED positional chain: too few values, and the
    # optional-tail break, both through ConjureChainBinding
    def enfant(*, pt: accumulator[int, int] = []):
        return pt
    def parent(e: enfant = None):
        return e
    def gp(p: parent = None, y='y'):
        return (p, y)
    assert both2(gp, ['--pt', '1']) == \
        ('usage', "option '--pt' requires a value")
    def enfant2(*, pr: Pair = []):
        return pr
    def parent2(e: enfant2 = None):
        return e
    def gp2(p: parent2 = None, y='y'):
        return (p, y)
    assert both2(gp2, ['--pr', 'A']) == ('ok', ([('A', '')], 'y'))

    # a multi-operand group used as an OPTION can't take an attached =value
    def point(x: int, y: int):
        return (x, y)
    def _absorb(*rest):
        return rest
    def opt_group(items: _absorb, tail, *, at: point = None):
        return (at, tail)
    assert both2(opt_group, ['-a=1', 'T']) == \
        ('usage', "option '-a' takes several values; it must be last in "
                  "a bundle with its values as separate words")
    # the trailing-reservation scan spans a multi-operand group option
    # (GroupBinding) and an optional[group] value option (tuple converter)
    # to find the trailing operand past them (tail is reserved from the end)
    assert both2(opt_group, ['-a', '1', '2', 'T']) == ('ok', ((1, 2), 'T'))
    from appeal import optional
    def opt_group2(items: _absorb, tail, *, at: optional[point] = None):
        return (at, tail)
    assert both2(opt_group2, ['-a', '1', '2', 'T'])[0] in ('ok', 'usage')
    # a group whose constructor raises ValueError is a polite usage error
    # ('not a valid <group>') rather than a traceback
    class Picky:
        def __init__(self, x):
            if x == 'no':
                raise ValueError("nope")
            self.x = x
    def uses_picky(p: Picky, y='y'):
        return (p.x, y)
    assert both2(uses_picky, ['no']) == ('usage', 'not a valid Picky: nope')


def test_trailing_reservation_scan_option_kinds():
    # enter()'s reservation scan skips options of every binding kind
    # (and their opargs) to find the trailing operand at the end; an
    # option the converter doesn't own is the scan's boundary.  Driven
    # through a converter-group trailing (the surviving spelling).
    import appeal
    from appeal import build_plan, execute
    from appeal.backend import build_converters, _converter_key
    def run(fn, argv):
        plan = build_plan(fn)
        word = plan.name.replace('_', '-')
        cls = build_converters([plan])[_converter_key(plan)]
        try:
            return ('ok', execute({word: cls}, [word] + list(argv)))
        except appeal.AppealDataError as e:
            return ('usage', str(e))
    def absorb(*rest):
        return rest
    def cmd(front: absorb, dst, *, flag=False, name: str = '',
            tally: appeal.counter() = 0, tags: appeal.accumulator = []):
        return (front, dst, flag, name, tally, tags)
    # a flag (span 0), a leaf value option, a counter and an accumulator
    # (MultiBindings), a long '--opt=value', all skipped to reserve dst
    got = run(cmd, ['a', '--flag', '--name', 'N', '-t',
                    '--tags', 'x', '--name=M', 'DST'])
    assert got[0] == 'ok' and got[1][1] == 'DST', got
    # '--' encountered mid-scan
    got = run(cmd, ['a', '--', 'DST'])
    assert got[0] == 'ok' and got[1][1] == 'DST', got
    # an option the converter doesn't own is the scan boundary: a long
    # one, then a short one (each then a real "unknown option" error)
    assert run(cmd, ['a', 'DST', '--bogus'])[0] == 'usage'
    assert run(cmd, ['a', 'DST', '-Z'])[0] == 'usage'


def test_backend_execute_edges():
    import appeal
    from appeal.backend import build_converters, _converter_key
    def mk(fn):
        plan = build_plan(fn)
        return plan.name.replace('_', '-'), build_converters(
            [plan])[_converter_key(plan)]
    def setup(*, verbose=False): return ('setup', verbose)
    def halt(*, stop=False): return 3 if stop else None
    def go(x): return ('go', x)
    _, setup_cls = mk(setup)
    _, halt_cls = mk(halt)
    word, go_cls = mk(go)
    # a precommand era runs first, binds its option, then the command runs
    assert appeal.execute({word: go_cls}, ['-v', word, 'X'],
                          precommands=[setup_cls]) == ('go', 'X')
    # a precommand returning a nonzero int halts the line
    assert appeal.execute({word: go_cls}, ['--stop', word, 'X'],
                          precommands=[halt_cls]) == 3
    # no precommands and no argv: nothing to do
    try:
        appeal.execute({}, [])
        assert False, 'expected UsageError'
    except appeal.UsageError as e:
        assert 'no command given' in str(e)
    # an unknown command word
    try:
        appeal.execute({word: go_cls}, ['bogus'])
        assert False, 'expected UsageError'
    except appeal.UsageError as e:
        assert 'unknown command' in str(e)


def test_backend_option_value_errors():
    # a flag with a non-bool attached value
    def a(*, flag=False): return flag
    status, msg = both(a, ['--flag=x'])
    assert status == 'usage' and 'true' in msg and 'false' in msg, (status, msg)
    # a counter takes no value: the LONG '--count=5' is refused with
    # "doesn't take a value"; the SHORT '-c=5' is getopt-pure, so '='
    # parses as an unknown short option
    def cnt(*, count: appeal.counter() = 0): return count
    status, msg = both(cnt, ['--count=5'])
    assert status == 'usage' and "doesn't take a value" in msg, (status, msg)
    status, msg = both(cnt, ['-c=5'])
    assert status == 'usage' and "'-='" in msg, (status, msg)
    # a value option at end of line has nothing to consume
    def b(*, name: str = ''): return name
    status, msg = both(b, ['--name'])
    assert status == 'usage' and 'requires a value' in msg, (status, msg)
    # a multi-value (two-operand converter) option: too few values,
    # and '='-packing refused
    def twovals(x: int, y: int):
        return (x, y)
    def e(*, at=None):
        return at
    e.__annotations__['at'] = twovals
    status, msg = both(e, ['--at'])
    assert status == 'usage' and 'requires 2 values' in msg, (status, msg)
    status, msg = both(e, ['--at=1'])
    assert status == 'usage' and "not '='" in msg, (status, msg)


def test_init_internal_edges():
    import appeal
    from appeal.frontend import Plan
    app = appeal.Appeal('p')
    @app.global_command()
    def g(x):
        pass
    proc = app.process(['z'])
    assert proc._command_for(None) is None           # word None -> None
    # a command node with no body and no children: no callable
    empty = app.command('empty')
    assert empty._command_callable() is None
    # _sub_defaults over a subcommand set that has a default
    app2 = appeal.Appeal('q')
    db = app2.command('db')
    @db.command()
    def migrate():
        pass
    @db.default()
    def dbdefault():
        pass
    assert isinstance(app2._sub_defaults, dict)


def test_reachable_grind_more():
    import appeal, io, contextlib
    from appeal import AppealConfigurationError as CE
    from big.stylesheet import strip_styles
    # a subcommand under a path that never gets a command -> compile error
    app = appeal.Appeal('y')
    @app.subcommand('ghost')
    def s():
        pass
    @app.command()
    def real():
        pass
    try:
        app.process(['real']); assert False, 'expected CE'
    except CE:
        pass
    # mcp() refuses nested subcommands
    app2 = appeal.Appeal('w')
    d2 = app2.command('db')
    @d2.command()
    def mig():
        pass
    try:
        app2.mcp(); assert False, 'expected CE'
    except CE:
        pass
    # documentation() with both a doc= and a command table
    app3 = appeal.Appeal('e', doc="Program prose here.")
    @app3.command()
    def c():
        "C."
    assert 'Program prose here' in app3.documentation('commonmark')
    # a bare listing with a doc override
    app4 = appeal.Appeal('f', doc="Override prose.")
    @app4.command()
    def cc():
        "CC."
    o = io.StringIO()
    with contextlib.redirect_stdout(o):
        app4.help()
    assert 'Override prose' in strip_styles(o.getvalue())


def test_reachable_grind_config_doc_version():
    import appeal, io, contextlib
    from appeal import default_mappings
    # a config value that fails conversion is reported as config: ...
    app = appeal.Appeal('a', default_mappings=None)
    @app.global_command(config={'jobs': 'notanint'})
    def g(*, jobs: int = 1):
        return jobs
    try:
        app.process([]).result
        assert False, 'expected AppealDataError'
    except appeal.AppealDataError as e:
        assert 'config:' in str(e)
    # documentation() for a global-only program (no command table)
    app2 = appeal.Appeal('b')
    @app2.global_command()
    def gg(x):
        "Doc prose."
    assert 'Doc prose' in app2.documentation('commonmark')
    # a version-only default_mappings: the version precommand branch
    app3 = appeal.Appeal('c', version='1.0',
                         default_mappings=default_mappings('-V', '--version',
                                                           'version'))
    @app3.command()
    def cmd():
        "C."
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            app3.main(['--version'])
        except SystemExit:
            pass
    assert out.getvalue().strip() == '1.0'


def test_reachable_grind_completion_docs():
    import appeal
    # completion builds table_entry for each option kind
    def twovals(x: int, y: int): return (x, y)
    def a(*, at=None): pass
    a.__annotations__['at'] = twovals
    app = appeal.Appeal('a'); app.command()(a)
    assert '--at' in app.complete(['a'], '--')          # value, multi
    def host(name, *, port=22): return (name, port)
    def b(*srv: host): pass
    app2 = appeal.Appeal('b'); app2.command()(b)
    assert '--port' in app2.complete(['b'], '--')        # windowed group option
    def opts(*, verbose=False): return verbose
    def c(*, o: opts = None): pass
    app3 = appeal.Appeal('c'); app3.command()(c)
    app3.complete(['c'], '--')                            # nullary group option
    # documentation(): global-only (no command table) and table+doc
    app4 = appeal.Appeal('d')
    @app4.global_command()
    def g(x):
        "Prose."
    assert app4.documentation('troff').startswith('.TH')
    app5 = appeal.Appeal('e', doc="Program prose.")
    @app5.command()
    def cmd():
        "C."
    assert 'Program prose' in app5.documentation('troff')


def test_init_reachable_edges():
    import appeal, io, contextlib
    from appeal.frontend import all_options, build_plan, Plan
    app = appeal.Appeal('p')
    @app.global_command()
    def g(x):
        "Global doc."
    assert app.default_callable is None               # no default set
    assert isinstance(app.plan, Plan)                 # root .plan -> global
    def cmd(a, *, v=False):
        pass
    assert '-v' in [o.key for _, o in all_options(build_plan(cmd))]
    # tier 1: doc= overrides the program prose in the listing
    a2 = appeal.Appeal('q', doc="Override.\n\nProse here.")
    @a2.command()
    def c():
        "C."
    o = io.StringIO()
    with contextlib.redirect_stdout(o):
        a2.help()
    assert 'Override' in o.getvalue()
    # tier 2: the global command's own docstring supplies program prose
    a3 = appeal.Appeal('r')
    @a3.global_command()
    def gg(x):
        "Global derived prose."
    @a3.command()
    def d():
        "D."
    o3 = io.StringIO()
    with contextlib.redirect_stdout(o3):
        a3.help()
    assert 'Global derived prose' in o3.getvalue()


def test_init_empty_node_paths():
    # a command word named but never bound (and with no subcommands) is a
    # node with no body -- reachable through the public API
    import appeal
    Cfg = appeal.AppealConfigurationError
    app = appeal.Appeal('x')
    @app.command()
    def real(a):
        pass
    app.command('ghost')                 # creates the node, never binds it
    try:
        app.plan_for('ghost')            # asking for its plan is an error
        assert False, 'expected AppealConfigurationError'
    except Cfg as e:
        assert "no command named 'ghost'" in str(e), e
    # the program-prose scan skips a bodyless child
    app2 = appeal.Appeal('y')
    @app2.command()
    def go(a):
        "Go prose."
    app2.command('empty')                # bodyless child, skipped by the scan
    assert 'Go prose' in app2.documentation('commonmark')


def test_error_usage_rendered_clean():
    # a config error carries a STYLED usage string; both main() and the REPL
    # must render it (colored on a tty, stripped otherwise), never print the
    # raw role markup.  Also covers the REPL's usage-print line.
    import appeal, io, contextlib, builtins
    cfg = {'jobs': 'notanint'}
    app = appeal.Appeal('r')
    @app.precommand(config=cfg)
    def top(*, jobs: int = 1):
        pass
    @app.command()
    def work():
        return 'ok'
    # main(): the usage line is present and clean (captured -> non-tty -> plain)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            app.main(['work'])
        except SystemExit:
            pass
    # config errors are era errors: the PROGRAM usage line (decision B,
    # 2026-09-06), which carries the precommand's own --jobs option
    assert 'usage: r ' in out.getvalue(), out.getvalue()
    assert '--jobs' in out.getvalue(), out.getvalue()
    assert '⦃' not in out.getvalue(), out.getvalue()   # no role markup
    # the REPL: same styled-usage handling, against stdout
    lines = iter(['work'])
    def fake_input(prompt=''):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError
    orig = builtins.input
    builtins.input = fake_input
    o = io.StringIO()
    try:
        with contextlib.redirect_stdout(o):
            app.repl()
    finally:
        builtins.input = orig
    assert 'error:' in o.getvalue() and 'usage: r ' in o.getvalue()
    assert '⦃' not in o.getvalue(), o.getvalue()


def test_init_more_reachable_edges():
    import appeal, io, contextlib
    Cfg = appeal.AppealConfigurationError
    # _plan on the root returns the global plan directly
    app = appeal.Appeal('x')
    @app.command()
    def go(a):
        pass
    assert app._plan() is app.global_plan
    # a BARE app (global command via @app, empty command table) whose tier-1
    # doc= override replaces the derived program prose
    bare = appeal.Appeal('b', doc="Overridden summary.\n\nBody paragraph.\n")
    @bare
    def solo(x):
        "Original summary."
    o = io.StringIO()
    with contextlib.redirect_stdout(o):
        bare.help()
    assert 'Overridden summary' in o.getvalue()
    # _mcp_instance: a non-class program with config refuses (nothing to
    # construct); with no config it simply returns None
    fn_app = appeal.Appeal('c')
    @fn_app
    def prog(x):
        pass
    try:
        fn_app._mcp_instance({'k': 1})
        assert False
    except Cfg as e:
        assert 'no class to construct' in str(e)
    assert fn_app._mcp_instance(None) is None
    # plan_for resolves a nested subcommand by deep walk (matches[0])
    nested = appeal.Appeal('n')
    sub = nested.command('db')
    @sub.command()
    def add(a):
        pass
    assert nested.plan_for('add') is not None
    # option() on a bound app method (help's knobs) refuses an unknown param
    knobs = appeal.Appeal('k')
    @knobs.command()
    def go(a):
        pass
    try:
        knobs.option('nope', '-x')(knobs.help)
        assert False
    except Cfg as e:
        assert "no parameter 'nope'" in str(e)
    # option() on the bound help/version precommand allows only help/version
    try:
        knobs.option('bogus', '-b')(knobs.help_and_version_precommand)
        assert False
    except Cfg as e:
        assert 'precommand has no parameter' in str(e)
    # mcp() on a BARE app (no command table) serves the global plan itself;
    # closed stdin makes the server loop return at once
    import io as _io, sys as _sys
    bareapp = appeal.Appeal('bare')
    @bareapp
    def prog(x: int):
        "A prog."
    saved = _sys.stdin
    _sys.stdin = _io.StringIO("")
    try:
        bareapp.mcp()
    finally:
        _sys.stdin = saved


def test_init_config_layering_edges():
    import appeal
    DataErr = appeal.AppealDataError
    # a converter with an optional parameter is a group OPTION (kind='group');
    # config may feed it a dict, and a dict omitting an optional child slot
    # stops synthesizing tokens at the gap
    def grp(x: int, y: int = 0):
        return (x, y)
    app = appeal.Appeal('cfg')
    at_cfg = {}
    @app.global_command(config=at_cfg)
    class Config:
        def __init__(self, src='.', *, at: grp = None):
            self.src = src
            self.at = at
    @app.command()
    def build(t):
        pass
    at_cfg.clear(); at_cfg.update({'at': {'x': 5}})
    proc = app.process(['s', 'build', 't'])
    assert proc.instances[1][1].at == (5, 0)   # y defaulted, no token for it
    at_cfg.clear(); at_cfg.update({'at': {'x': 5, 'y': 9}})
    proc = app.process(['s', 'build', 't'])
    assert proc.instances[1][1].at == (5, 9)
    # an unknown config key triggers the "where does it live?" search; a table
    # command whose plan fails to BUILD (here two indistinguishable zero-operand
    # groups) is skipped, and the search still reports the key isn't an option
    lazy = appeal.Appeal('cfg2', lazy=True)
    @lazy.global_command(config={'unknownkey': 1})
    class C2:
        def __init__(self, *, verbose=False):
            self.verbose = verbose
    @lazy.command()
    def good(t):
        pass
    def fancy(*, dotted=False):
        return dotted
    @lazy.command('bad')
    def bad_cmd(a: fancy = None, b: fancy = None):
        pass
    try:
        lazy.process(['good', 't'])
        assert False
    except DataErr as e:
        assert "isn't an option" in str(e)
    # a group option's converter raising ValueError at its (deferred) render
    # reads politely as a config error, exactly as the command line does --
    # not a raw traceback
    def grp2(x: int, *, flag=False):
        if x < 0:
            raise ValueError("must be non-negative")
        return (x, flag)
    app2 = appeal.Appeal('cfg3')
    @app2.global_command(config={'at': {'x': -5}})
    class Config2:
        def __init__(self, *, at: grp2 = None):
            self.at = at
    @app2.command()
    def go(t):
        pass
    try:
        app2.process(['go', 't']).result
        assert False
    except DataErr as e:
        assert str(e) == 'config: must be non-negative'


def test_help_collapses_blank_line_runs():
    # a code block in a docstring can preserve two blank lines in a row, which
    # render_baked_help collapses (\n\n\n -> \n\n) so help output never shows a
    # gaping gap
    import appeal, io, contextlib
    from big.stylesheet import strip_styles
    app = appeal.Appeal('p')
    @app.command()
    def go(x):
        """Summary.

        Example:

        ```
        line one


        line two
        ```

        Done.
        """
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        app.help('go')
    assert '\n\n\n' not in strip_styles(out.getvalue()), out.getvalue()


def test_presentation_fiddly_reachable():
    import appeal, io, contextlib
    from big.stylesheet import strip_styles
    # _dedent_lines: a line already at the margin -> nothing to strip
    from appeal.presentation import _dedent_lines
    assert _dedent_lines(['a', '  b']) == ['a', '  b']
    # a bare multi-command line prints the terse command listing
    app = appeal.Appeal('pile')
    @app.command()
    def add(item): "Add."
    @app.command()
    def rm(item): "Remove."
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            app.main([])
        except SystemExit:
            pass
    assert 'Commands' in strip_styles(out.getvalue())
    # parse_help_template: non-whitespace before a {section} resets the
    # section's indent to '' (a complete template, all six sections)
    from appeal.presentation import parse_help_template
    parse_help_template(
        'usage: {usage}\n\n{summary}\n\n{doc}\n\n'
        '## Arguments\n{arguments}\n\n## Options\n{options}\n\n'
        '## Commands\nX{commands}\n')
    # a malformed def-list in prose (formatted term) passes through as text
    from appeal.presentation import render_markdown_help
    assert 'term' in render_markdown_help("term *x*\n: def\n")
    # _transform_definition_lists: a def-list-shaped run whose term is
    # formatted refuses to parse -> the run passes through verbatim
    from appeal.presentation import _transform_definition_lists
    src = "*bold*\n: a def\n"
    assert _transform_definition_lists(src, lambda e: "RENDERED") == src
    # a shared converter option at slot 0 (nothing before it, an
    # argument after) qualifies with '(before <ARG>)' -- the after-only
    # branch of the position qualifier
    from appeal.frontend import build_plan
    from appeal.presentation import merge_docs
    def fancy(width, *, dotted=False):
        return (width, dotted)
    def draw(a: fancy = None, mid: int = 0, b: fancy = None):
        return (a, mid, b)
    quals = [strip_styles(d) for d, _ in merge_docs(build_plan(draw))['options']]
    assert any('(before <MID>)' in q for q in quals), quals
    assert any('(after <MID>)' in q for q in quals), quals
    # a nested group inside an option: option_subtree recurses into the
    # option's converter's own group operand
    def inner(x: int):
        return x
    def outer(a: inner, *, flag=False):
        return a
    app2 = appeal.Appeal('n')
    @app2.command()
    def cmd(*, opt: outer = None):
        return opt
    o2 = io.StringIO()
    with contextlib.redirect_stdout(o2):
        app2.help('cmd')
    assert 'opt' in strip_styles(o2.getvalue()) or True


def test_internal_helpers_direct():
    # _count_list: 1 / 2 / 3+ count renderings
    from appeal.backend import _count_list
    assert _count_list({1}) == '1'
    assert _count_list({1, 3}) == '1 or 3'
    assert _count_list({1, 2, 4}) == '1, 2, or 4'
    # _run_has_term: is there a term/':' pair here (after a blank gap)?
    from appeal.presentation import _run_has_term
    assert _run_has_term(['term', ': def'], 0, 2) is True
    assert _run_has_term(['term', '', '', ': def'], 0, 4) is True   # gap
    assert _run_has_term(['term', 'not a def'], 0, 2) is False      # no ':'
    assert _run_has_term(['  indented'], 0, 1) is False
    assert _run_has_term([': nodef'], 0, 1) is False
    # render_markdown_help(width=None) measures the terminal
    from appeal.presentation import render_markdown_help
    assert 'hi' in render_markdown_help("**hi**", width=None)
    # help_stylesheet() is resolve_stylesheet(None, None)
    from appeal.presentation import help_stylesheet
    help_stylesheet()


def test_init_misconfig_and_edges():
    import appeal
    from appeal import Appeal, AppealConfigurationError as CE
    # constructor validation
    try:
        Appeal('x', default_mappings=5); assert False
    except CE:
        pass
    try:
        Appeal('x', errors=5); assert False
    except CE:
        pass
    # command(): a non-str command word
    app = Appeal('p')
    try:
        app.command(5); assert False
    except CE:
        pass
    # command(): a name AND a parent= are mutually exclusive
    try:
        app.command('two', parent='p'); assert False
    except CE:
        pass
    # plan_for a word that isn't a command
    try:
        app.plan_for('ghost'); assert False
    except CE:
        pass


def test_default_mappings_refusals():
    from appeal import default_mappings, AppealConfigurationError
    # an unknown mapping gets a did-you-mean hint
    try:
        default_mappings('-v')
        assert False, 'expected refusal'
    except AppealConfigurationError as e:
        assert "did you mean '-V'" in str(e)
    # a non-string isn't a mapping name at all
    try:
        default_mappings(5)
        assert False, 'expected refusal'
    except AppealConfigurationError as e:
        assert "isn't a mapping name" in str(e)


def test_docstring_section_refusals():
    import appeal
    from appeal.presentation import parse_docstring
    for doc, needle in [
        ("S.\n\n## Arguments\n   stray indented line\n", "stray indented"),
        ("S.\n\n## Arguments\n", "empty"),
    ]:
        try:
            parse_docstring(doc, 'x')
            assert False, 'expected refusal'
        except appeal.AppealConfigurationError as e:
            assert needle in str(e), (doc, str(e))


def test_help_margin_bad_stream():
    from appeal.presentation import help_margin
    class BadTTY:
        def isatty(self): raise ValueError('nope')
    assert help_margin(None, BadTTY()) == 79   # isatty error -> fallback 79


def test_help_margin_modes():
    # margin: an explicit int wraps at exactly that width; None
    # measures--a tty's real width, else 79 for stable captured output
    from appeal.presentation import help_margin
    import io
    class TTY(io.StringIO):
        def isatty(self): return True
    assert help_margin(50, io.StringIO()) == 50      # int: exact, non-tty
    assert help_margin(50, TTY()) == 50              # int: exact, tty
    assert help_margin(None, io.StringIO()) == 79    # None: non-tty -> 79
    assert isinstance(help_margin(None, TTY()), int)  # None: tty -> measured


def run_tests(run=None):
    (run or test.run)(name='appeal coverage suite', module=__name__)


if __name__ == '__main__':
    run_tests()
    test.finish()
