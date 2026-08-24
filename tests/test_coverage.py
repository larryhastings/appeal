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

appeal_dir = test.preload('appeal')

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
    from appeal.mcp import mcp_input_schema

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
    described = app.schema()
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

    # a trailing (required keyword-only) parameter reads by name
    def trail(a, *, k):
        return (a, k)
    assert read_mapping(trail, {'a': 1, 'k': 'v'}) == (1, 'v')

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
    # position-feeding can't reach keyword-only names (v1's corpus)
    def trail(a, *, k):
        return (a, k)
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
    from appeal.mcp import mcp_input_schema
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
    from appeal.mcp import describe as describe
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
    @app.global_command()
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
        try:
            app.process(['go', 'd'], config=config).result
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
    def make_app():
        app = Appeal(name='cfi')
        @app.global_command()
        def top(*, tags: appeal.accumulator[str] = (), adds: Add = 0,
                spot: pt = None, corner: box = None, flag=False):
            seen.append((tuple(tags), adds, spot, corner, flag))
        @app.command()
        def go():
            seen.append('go')
        return app

    def drive(config):
        seen[:] = []
        make_app().process(['go'], config=config).result
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
        make_app().main(['go'], config={'flag': True})
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
    described = app.schema()
    assert described['operands'][0]['name'] == 'x'
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
    # a group with its own trailing argument and options, read
    # from a sequence: the tail is reserved, the options default
    def tg(a, *, k):
        return (a, k)
    def f2(g: tg):
        return g
    assert read_mapping(f2, {'g': ['x', 'kv']}) == ('x', 'kv')
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
        raise UsageError('nope', 'prog x')
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
    from appeal.mcp import mcp_input_schema
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
    from appeal.mcp import mcp_input_schema
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
    from appeal.mcp import _degenerate_leaf_type
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
    # a tuple[...] option: element type names
    def cmd2(*, pt: tuple[int, int] = ()):
        return pt
    assert '--pt' in strip_styles(build_plan(cmd2).usage())


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
    # a counter takes no value, so an attached one is refused
    def cnt(*, v: appeal.counter() = 0): return v
    status, msg = both(cnt, ['-v=5'])
    assert status == 'usage' and "doesn't take a value" in msg, (status, msg)
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
