#!/usr/bin/env python3


# part of the Appeal software package
# Copyright 2021-2023 by Larry Hastings
# All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
# DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
# OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


import builtins

def make_stdout_capture():
    text = []
    actual_print = builtins.print
    saved_no_color = []

    def captured_print(*a, end="\n", sep=" ", flush=False):
        t = sep.join([str(o) for o in a])
        t += end
        text.append(t)

    def start():
        # Captured output must be terminal-independent.  This helper
        # swaps print() but NOT sys.stdout, so appeal's theme resolver
        # still sees the real terminal--and in an interactive run
        # (a TTY) it would paint ANSI into the text, breaking every
        # substring assertion.  Force monochrome for the capture
        # window; NO_COLOR always wins over a TTY or FORCE_COLOR.
        import os
        saved_no_color.append(os.environ.get('NO_COLOR'))
        os.environ['NO_COLOR'] = '1'
        builtins.print = captured_print
        return captured_print

    def end():
        import os
        builtins.print = actual_print
        prior = saved_no_color.pop() if saved_no_color else None
        if prior is None:
            os.environ.pop('NO_COLOR', None)
        else:
            os.environ['NO_COLOR'] = prior
        result = "".join(text)
        return result

    return start, captured_print, end


import collections
import math
import os.path
import shlex
import subprocess
import sys
import textwrap
import unittest


from big import test
from big.builtin import load
import os.path

# big >= 0.15: load() returns the imported module; the repo root is
# its package parent
appeal_dir = os.path.dirname(os.path.dirname(load('appeal').__file__))

import appeal

if not getattr(appeal, '__version__', '').startswith('0.'):
    # This tree is the v2 rewrite now.  This file is the v1 test
    # corpus, kept as the source of truth for migration (proposal
    # §9: "keep the tests, not the code")--its cases move into
    # test_all.py as the features they test come back to life.
    print("test_v1.py is the v1 corpus; this tree is v2. See tests/test_all.py.")
    sys.exit(0)

# big.test (imported above, for load) adds bare-assert
# introspection and a runner that returns control to us instead of
# exiting.  (Appeal already depends on big; this is big.test's
# first customer after big itself.)



app = command = process = None

def capture_stdout(cmdline):
    start, captured_print, end = make_stdout_capture()
    start()
    # end() restores builtins.print; it MUST run even if process() raises
    # (which it does in every assertRaises test), or the print swap leaks
    # and silently eats all subsequent output.
    try:
        process(shlex.split(cmdline))
    finally:
        result = end()
    return result




def int_float_verbose(x_int:int, y_float:float, *, verbose=False):
    """
    Pointless demonstration converter.

    # Arguments
    x_int
    : A pointless int.
    y_float
    : A pointless float.

    # Options
    verbose
    : Allows control of the int float pair's verbosity.
    """
    return (int_float_verbose, x_int, y_float, "verbose" if verbose else "silent")

def gloopfn(gloopstr, *, intfloat:int_float_verbose="(default value for intfloat)"):
    """
    A pointless demonstration converter with a gloopy string.

    # Arguments
    gloopstr
    : A pointless string, we don't even care about it.

    # Options
    intfloat
    : An optional pair of int float, with verbosity.
    """
    return (gloopfn, gloopstr, intfloat)

def test(str1, str2, optional_int=0, *, gloop:gloopfn=("(default value for gloop)")):
    """
    Simple test command function.

    Does this and that.  Actually just prints its arguments.

    More text goes down here, in the prose.

    # Arguments
    str1
    : A string!
    str2
    : It's another string.  Who knows why we add these things.
    optional_int
    : An optional integer that fills your heart with joy.

    # Options
    gloop
    : Does kind of a grab-bag of things.  gloopstr gets a string
      but nobody's sure why.
    """

    return (test, str1, str2, optional_int, gloop)


def rip(s, a:int_float_verbose, b:int_float_verbose="(b default)", c:int_float_verbose="(c default)", **kwargs:float):
    return (rip, s, a, b, c, kwargs)

def tear(s, *, verbose:appeal.counter()=0):
    return (tear, s, verbose)

def foosball(s, *, define:appeal.accumulator=[]):
    return (foosball, s, define)

def soccer(s, *, define:appeal.accumulator[int, str]=[]):
    return (soccer, s, define)

def pool(s, *, define:appeal.mapping={}):
    return (pool, s, define)

def snooker(s, *, define:appeal.mapping[int,str]={}):
    return (snooker, s, define)

# skittles used mapping[int,str,float] (three operands per occurrence);
# v2 unified mapping onto the KEY=VALUE MultiOption, which is exactly
# two halves, so 3-arity mapping is gone (Larry's ruling, 2026-08-18).

def go(direction:appeal.validate("north", "south", "east", "west")):
    return (go, f"go {direction} young man!")

def north(): return 'north'
def south(): return 'south'
def east(): return 'east'
def west(): return 'west'

def go2(*, direction='north'):
    return (go2, direction)

def pick30(number:appeal.validate_range(30)):
    return (pick30, number)

def pick60(number:appeal.validate_range(-30, 30)):
    return (pick60, number)

def verbosity(*, verbose:appeal.counter(delta=2, clamp=9)=0):
    return (verbosity, verbose)

def boolpos(v:bool):
    return (boolpos, v)


def logging(*,
    verbose:appeal.counter()=0,
    log_level:appeal.validate("critical", "error", "warning", "info", "debug", "notset")="info",
    log_dest:appeal.validate("stdout", "syslog")="syslog",
    ):
    return {
        'verbose': verbose,
        'log_level': log_level,
        'log_dest': log_dest,
        }

def eric(l:logging, *args):
    return (eric, l, args)


class Logging:
    def __init__(self,
        *,
        verbose:appeal.counter()=0,
        log_level:appeal.validate("critical", "error", "warning", "info", "debug", "notset")="info",
        log_dest:appeal.validate("stdout", "syslog")="syslog",
        ):
        self.verbose = verbose
        self.log_level = log_level
        self.log_dest = log_dest

    def __repr__(self):
        return f"<{self.__class__.__name__} verbose={self.verbose!r} log_level={self.log_level!r} log_dest={self.log_dest!r}>"

    # I had to implement this to make the tests involving
    # Logging objects work.  idk why Python thinks
    #     bool(Logging() == Logging()) == False
    # but there you are.
    def __eq__(self, other):
        return isinstance(other, self.__class__) and (other.__dict__ == self.__dict__)

def eric2(l:Logging, s='(default)'):
    return (eric2, l, s)

# all the invalid_* functions are deliberately illegal
# and should raise AppealConfigurationError

def invalid_logging(l:Logging, l2:Logging):
    return (invalid_logging, l, l2)

def invalid_annotation_1(a:appeal.split):
    return (invalid_annotation_1, a)

def invalid_annotation_2(*, a:appeal.counter=0):
    return (invalid_annotation_2, a)


def jobs(jobs:int=math.inf):
    return jobs

def make(*targets, jobs:jobs=1):
    print(f"make jobs={jobs} targets={targets}")

def three_ints(a:int=111, b:int=222, c:int=333):
    return (a, b, c)

def make2(*targets, jobs:three_ints=(0, 0, 0)):
    return (make2, jobs, targets)

def two_or_more_files(file, file2, *files):
    return (two_or_more_files, file, file2, files)

def set_path(path:appeal.split(":")):
    return (set_path, path)

def fgrep(pattern, filename=None, *, color="", id=0, verbose=False):
    return (fgrep, pattern, filename, color, id, verbose)

def weird_log_info(*, verbose=False, log_level='info'):
    return (verbose, log_level)

def weird(log:weird_log_info):
    return (weird, log)

def inferred_list(a, b=[0, 0.0]):
    return (inferred_list, a, b)


def nested_inner(c:int):
    return c

def nested_outer(b:nested_inner):
    return b

def undo(a:nested_outer=0):
    return (undo, a)

def hey_argparse_watch_this(*, i:int=None, f:float=None, c:complex=None):
    return (hey_argparse_watch_this, i, f, c)


def inner_option(*, e=False, f=False):
    return (inner_option, e, f)

def nested_option(*, c=False, d=False, nested:inner_option=(inner_option, False, False)):
    return (nested_option, c, d, nested)

def options_stack(x='abc', *, a=False, b=False, option:nested_option=(nested_option, False, False, (inner_option, False, False))):
    return (options_stack, x, a, b, option)


def five_m(*, p=False, q=False, r=False):
    return (five_m, p, q, r)

def five_j(*, m:five_m=None, n=False, o=False):
    return (five_j, m, n, o)

def five_g(*, j:five_j=None, k=False, l=False):
    return (five_g, j, k, l)

def five_d(*, g:five_g=None, h=False, i=False):
    return (five_d, g, h, i)

def five_a(*, d:five_d=None, e=False, f=False):
    return (five_a, d, e, f)

def five_level_stack(*, a:five_a=None, b=False, c=False):
    return (five_level_stack, a, b, c)


def multiple_groups_child(child_a, child_b, child_c=None, *, flag=False, color:int_float_verbose=(0, 0.0, False)):
    return (multiple_groups_child, child_a, child_b, child_c, flag, color)

def multiple_groups(a,
    b:multiple_groups_child=(multiple_groups_child, None, None, None, False, (0, 0.0, False)),
    c:multiple_groups_child=(multiple_groups_child, None, None, None, False, (0, 0.0, False)),
    d='',
    ):
    return (multiple_groups, a, b, c, d)

def simple_defaults(a: int=0, b: str=''):
    return (simple_defaults, a, b)


class IntFloat:
    def __init__(self, integer:int, real:float):
        self.i = integer
        self.f = real

    def __repr__(self):
        return f"<IntFloat i={self.i!r} f={self.f!r}>"

def str_i_f(value, i_f:IntFloat=None, *, option=None, verbose=False):
    return (str_i_f, value, i_f, option, verbose)


def earlier_int_float2(i2:int, f2:float, *, flag=False):
    return (earlier_int_float2, i2, f2, flag)

def earlier_int_float1(i1:int, f1:float, *, verbose=False):
    return (earlier_int_float1, i1, f1, verbose)

def earlier(a, b:earlier_int_float1, c:earlier_int_float2=(earlier_int_float2, 0, 0.0, False)):
    return (earlier, a, b, c)




class AppealTestsBase(unittest.TestCase):
    maxDiff = None


class SmokeTests(AppealTestsBase):

    def setUp(self):
        global app
        global command
        global process
        app = appeal.Appeal(
            margin=80,
            # indent=2 dropped: the knob was killed unshipped
            # (ruled 2026-08-06)--big's renderer owns the
            # definition-list layout now
            # positional_argument_usage_format="<{name.upper()}>",
            version="0.5",
            )
        command = app.command()
        def my_process(args):
            return app.process(args).result
        process = my_process

    def assert_process(self, cmdline, result):
        self.assertEqual(process(shlex.split(cmdline)), result)

    def assert_process_raises(self, cmdline, exception, text=None):
        e = None
        with self.assertRaises(exception):
            try:
                process(shlex.split(cmdline))
            except exception as e2:
                e = e2
                raise e2
        if text:
            self.assertIn(text, str(e))
        return e

    def tearDown(self):
        global app
        global command
        app = command = None


    def test_test_1(self):
        command(test)
        self.assert_process(
            "test -g gloopy abc def",
            (test,'abc', 'def', 0, (gloopfn, 'gloopy', '(default value for intfloat)')),
            )

    def test_test_2(self):
        command(test)
        self.assert_process(
            "test -g gloopy -i 1 3.0 -v abc def 336",
            (test, 'abc', 'def', 336, (gloopfn, 'gloopy', (int_float_verbose, 1, 3.0, 'verbose'))),
            )

    def test_test_3(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(test)
        self.assert_process(
            "test -g gloopy abc def -i 1 3.0 -v 336",
            (test, 'abc', 'def', 336, (gloopfn, 'gloopy', (int_float_verbose, 1, 3.0, 'verbose'))),
            )

    def test_simple_defaults_1(self):
        command(simple_defaults)
        self.assert_process(
            "simple-defaults",
            (simple_defaults, 0, ''),
            )

    def test_simple_defaults_2(self):
        command(simple_defaults)
        self.assert_process(
            "simple-defaults 5",
            (simple_defaults, 5, ''),
            )

    def test_simple_defaults_3(self):
        command(simple_defaults)
        self.assert_process(
            "simple-defaults 33 abc",
            (simple_defaults, 33, 'abc'),
            )

    def test_simple_defaults_4(self):
        command(simple_defaults)
        self.assert_process_raises(
            "simple-defaults 3.14159",
            appeal.AppealUsageError,
            )

    def test_simple_defaults_5(self):
        command(simple_defaults)
        self.assert_process_raises(
            "simple-defaults 33 abc xxx",
            appeal.AppealUsageError,
            )


    def bind_rip(self):
        command(rip)
        app.option("imaginary", "-i", "--imaginary")(rip)

    def test_rip_1(self):
        self.bind_rip()
        self.assert_process(
            'rip xyz 1 2',
            (rip,
                'xyz',
                (int_float_verbose, 1, 2.0, 'silent'),
                "(b default)",
                "(c default)",
                {},
                ),
            )

    def test_rip_2(self):
        self.bind_rip()

        self.assert_process(
            'rip abc -v 1 2',
            (rip,
                'abc',
                (int_float_verbose, 1, 2.0, 'verbose'),
                "(b default)",
                "(c default)",
                {},
                ),
            )

    def test_rip_3(self):
        self.bind_rip()
        self.assert_process(
            'rip scooby 1 2 3 4 5 6',
            (rip,
                'scooby',
                (int_float_verbose, 1, 2.0, 'silent'),
                (int_float_verbose, 3, 4.0, 'silent'),
                (int_float_verbose, 5, 6.0, 'silent'),
                {},
                )
            )

    def test_rip_4(self):
        self.bind_rip()
        self.assert_process(
            'rip scooby 1 2 3 -v 4 5 6 -v',
            (rip,
                'scooby',
                (int_float_verbose, 1, 2.0, 'silent'),
                (int_float_verbose, 3, 4.0, 'verbose'),
                (int_float_verbose, 5, 6.0, 'verbose'),
                {},
                )
            )

    def test_rip_5(self):
        self.bind_rip()
        self.assert_process(
            'rip -v scooby 1 2 3 4  5 -v 6',
            (rip,
                'scooby',
                (int_float_verbose, 1, 2.0, 'verbose'),
                (int_float_verbose, 3, 4.0, 'silent'),
                (int_float_verbose, 5, 6.0, 'verbose'),
                {},
                )
            )


    def test_rip_6(self):
        self.bind_rip()
        self.assert_process(
            'rip scooby -v 1 2 3 --verbose 4 5 -v 6',
            (rip,
                'scooby',
                (int_float_verbose, 1, 2.0, 'verbose'),
                (int_float_verbose, 3, 4.0, 'verbose'),
                (int_float_verbose, 5, 6.0, 'verbose'),
                {},
                )
            )

    def test_rip_7(self):
        self.bind_rip()
        self.assert_process(
            'rip scooby -v 1 2  --verbose 3 4 --verbose 5 6',
            (rip,
                'scooby',
                (int_float_verbose, 1, 2.0, 'verbose'),
                (int_float_verbose, 3, 4.0, 'verbose'),
                (int_float_verbose, 5, 6.0, 'verbose'),
                {},
                )
            )

    def test_rip_8(self):
        """Test -- to turn off option processing."""
        self.bind_rip()
        self.assert_process(
            'rip xyz 1 -- -2',
            (rip,
                'xyz',
                (int_float_verbose, 1, -2.0, 'silent'),
                "(b default)",
                "(c default)",
                {},
                ),
            )


    def test_tear_1(self):
        command(tear)
        self.assert_process(
            'tear snacking -v --verbose -v',
            (tear, 'snacking', 3),
            )


    def test_foosball_1(self):
        command(foosball)
        self.assert_process(
            'foosball xyz -d a -d b -d c',
            (foosball, 'xyz', ['a', 'b', 'c']),
            )


    def test_soccer_1(self):
        command(soccer)
        self.assert_process(
            'soccer -d 1 x -d 2 y "cheap two-bit" -d 3 z',
            (soccer, "cheap two-bit", [(1, 'x'), (2, 'y'), (3, 'z')]),
            )


    def test_pool_1(self):
        command(pool)
        self.assert_process(
            'pool -d 1=x -d 2=y "bath salts" -d xya=z',
            (pool, "bath salts", {'1': 'x', '2': 'y', 'xya': 'z'}),
            )


    def test_snooker_1(self):
        command(snooker)
        self.assert_process(
            'snooker -d 1=e -d 2=f "part of the body" -d 3=g',
            (snooker, "part of the body", {1: 'e', 2: 'f', 3: 'g'}),
            )


    # test_skittles_1 removed: 3-arity mapping is gone in v2 (see the
    # skittles note above).

    def test_go_1(self):
        command(go)
        self.assert_process(
            'go north',
            (go, "go north young man!"),
            )

    def test_go_2(self):
        command(go)
        self.assert_process(
            'go south',
            (go, "go south young man!"),
            )

    def test_go_3(self):
        command(go)
        self.assert_process(
            'go east',
            (go, "go east young man!"),
            )

    def test_go_4(self):
        command(go)
        self.assert_process(
            'go west',
            (go, "go west young man!"),
            )

    def test_go_5(self):
        command(go)
        self.assert_process_raises(
            "go to-heck",
            appeal.AppealUsageError,
            )


    def bind_go2(self):
        command(go2)
        app.option("direction", "--north", annotation=north)(go2)
        app.option("direction", "--south", annotation=south)(go2)
        app.option("direction", "--east", annotation=east)(go2)
        app.option("direction", "--west", annotation=west)(go2)

    def test_go2_1(self):
        self.bind_go2()
        self.assert_process(
            'go2 --north',
            (go2, "north"),
            )

    def test_go2_2(self):
        self.bind_go2()
        self.assert_process(
            'go2 --south',
            (go2, "south"),
            )

    def test_go2_3(self):
        self.bind_go2()
        self.assert_process(
            'go2 --east',
            (go2, "east"),
            )

    def test_go2_4(self):
        self.bind_go2()
        self.assert_process(
            'go2 --west',
            (go2, "west"),
            )

    def test_go2_5(self):
        self.bind_go2()
        self.assert_process(
            'go2',
            (go2, "north"),
            )

    def test_go2_6(self):
        # v1 -> v2 DIVERGENCE, RULED by Larry 2026-07-18 (review
        # item 6): value options sharing a parameter are
        # last-one-wins in command-line order--kinder for
        # overriding defaults (an alias baking in --north is
        # harmlessly overridden by a later --south).  v1's golden
        # was assert_process_raises(AppealUsageError).
        self.bind_go2()
        self.assert_process(
            "go2 --north --south",
            (go2, "south"),
            )


    def test_pick30_1(self):
        command(pick30)
        self.assert_process(
            'pick30 5',
            (pick30, 5),
            )

    def test_pick30_2(self):
        command(pick30)
        self.assert_process_raises(
            "pick30 888",
            appeal.AppealUsageError,
            )


    def test_pick60_1(self):
        command(pick60)
        self.assert_process(
            'pick60 5',
            (pick60, 5),
            )

    def test_pick60_2(self):
        command(pick60)
        self.assert_process(
            'pick60 -- -22',
            (pick60, -22),
            )

    def test_pick60_3(self):
        command(pick60)
        self.assert_process_raises(
            "pick60 888",
            appeal.AppealUsageError,
            )


    def test_verbosity_1(self):
        command(verbosity)
        self.assert_process(
            'verbosity',
            (verbosity, 0),
            )

    def test_verbosity_2(self):
        command(verbosity)
        self.assert_process(
            'verbosity -v',
            (verbosity, 2),
            )

    def test_verbosity_3(self):
        command(verbosity)
        self.assert_process(
            'verbosity --verbose',
            (verbosity, 2),
            )

    def test_verbosity_4(self):
        command(verbosity)
        self.assert_process(
            'verbosity -v -v -v -v',
            (verbosity, 8),
            )

    def test_verbosity_5(self):
        command(verbosity)
        self.assert_process(
            'verbosity  -v -v -v --verbose -v --verbose -v',
            (verbosity, 9),
            )


    # 0.6.4 read a bool operand by truthiness ('x' and '0' True, ''
    # False); 1.0 reads the boolean language--true/yes/on/1,
    # false/no/off/0--and nothing else (Larry, 2026-09-11)
    def test_boolpos_1(self):
        command(boolpos)
        self.assert_process_raises(
            'boolpos x',
            appeal.AppealUsageError,
            )

    def test_boolpos_2(self):
        command(boolpos)
        self.assert_process(
            'boolpos 0',
            (boolpos, False),
            )

    def test_boolpos_3(self):
        command(boolpos)
        self.assert_process(
            'boolpos Yes',
            (boolpos, True),
            )


    def test_eric_1(self):
        command(eric)
        self.assert_process(
            'eric',
            (eric,
                {'verbose': 0, 'log_level': "info", "log_dest": "syslog"},
                (),
                ),
            )

    def test_eric_2(self):
        command(eric)
        self.assert_process(
            'eric -v -v -v --log-dest stdout -v ',
            (eric,
                {'verbose': 4, 'log_level': "info", "log_dest": "stdout"},
                (),
                ),
            )

    def test_eric_3(self):
        command(eric)
        self.assert_process(
            'eric -v -v --log-level error -v ',
            (eric,
                {'verbose': 3, 'log_level': "error", "log_dest": "syslog"},
                (),
                ),
            )

    def test_eric_4(self):
        command(eric)
        self.assert_process(
            'eric -v -v --log-level critical -v --verbose -v -v --log-dest stdout -v',
            (eric,
                {'verbose': 7, 'log_level': "critical", "log_dest": "stdout"},
                (),
                ),
            )


    def test_eric_5(self):
        command(eric)
        self.assert_process(
            'eric apples -v -v bananas --log-level critical -v coffee --verbose -v -v ramen --log-dest stdout -v eggs',
            (eric,
                {'verbose': 7, 'log_level': "critical", "log_dest": "stdout"},
                ('apples', 'bananas', 'coffee', 'ramen', 'eggs'),
                ),
            )



    def test_eric2_1(self):
        command(eric2)
        self.assert_process(
            'eric2',
            (eric2,
                Logging(),
                '(default)',
                ),
            )

    def test_eric2_2(self):
        command(eric2)
        self.assert_process(
            'eric2 -v -v -v --log-dest stdout -v ',
            (eric2,
                Logging(verbose=4, log_dest="stdout"),
                '(default)',
                ),
            )

    def test_eric2_3(self):
        command(eric2)
        self.assert_process(
            'eric2 -v -v --log-level error -v ',
            (eric2,
                Logging(verbose=3, log_level="error"),
                '(default)',
                ),
            )

    def test_eric2_4(self):
        command(eric2)
        self.assert_process(
            'eric2 -v -v --log-level critical -v --verbose -v -v --log-dest stdout -v',
            (eric2,
                Logging(verbose=7, log_level="critical", log_dest="stdout"),
                '(default)',
                ),
            )


    def test_eric2_5(self):
        command(eric2)
        self.assert_process(
            'eric2 -v -v --log-level warning -v --verbose -v -v "scotch egg" --log-dest stdout -v',
            (eric2,
                Logging(verbose=7, log_level="warning", log_dest="stdout"),
                'scotch egg',
                ),
            )

    def test_invalid_logging_1(self):
        # invalid_logging is deliberately illegal (Larry's ruling, 2026-08-24):
        # l and l2 are both ZERO-operand Logging groups sharing option strings,
        # so they stack at the same spot -- l2's -v stomps on l's, leaving l's
        # unreachable.  1.0 rejects it at compile.  (The 2026-07-09 "position
        # decides" migration was wrong: position can't tell two zero-width
        # groups apart.)
        command(invalid_logging)
        e = self.assert_process_raises(
            "invalid-logging -v",
            appeal.AppealConfigurationError,
            )
        self.assertIn("unreachable", str(e))

    def test_invalid_annotation_1_1(self):
        command(invalid_annotation_1)
        self.assert_process_raises(
            "invalid-annotation-1 a",
            appeal.AppealConfigurationError,
            )

    def test_invalid_annotation_2_1(self):
        command(invalid_annotation_2)
        self.assert_process_raises(
            "invalid-annotation-2 -a",
            appeal.AppealConfigurationError,
            )


    def test_make2_1(self):
        command(make2)
        self.assert_process(
            'make2',
            (make2, (0, 0, 0), ()),
            )

    def test_make2_2(self):
        command(make2)
        self.assert_process(
            'make2 a b c',
            (make2, (0, 0, 0), ('a', 'b', 'c')),
            )

    def test_make2_3(self):
        command(make2)
        self.assert_process(
            'make2 -j 33 44 55',
            (make2, (33, 44, 55), ()),
            )

    def test_make2_4(self):
        command(make2)
        self.assert_process(
            'make2 -j 33',
            (make2, (33, 222, 333), ()),
            )

    def test_make2_5(self):
        command(make2)
        self.assert_process(
            'make2 -j ',
            (make2, (111, 222, 333), ()),
            )

    def test_make2_6(self):
        command(make2)
        self.assert_process(
            'make2 a b c -j ',
            (make2, (111, 222, 333), ('a', 'b', 'c')),
            )

    def test_make2_7(self):
        command(make2)
        self.assert_process(
            'make2 a b c -j 88 ',
            (make2, (88, 222, 333), ('a', 'b', 'c')),
            )

    def test_make2_8(self):
        command(make2)
        self.assert_process(
            'make2 a b c -j 88 99',
            (make2, (88, 99, 333), ('a', 'b', 'c')),
            )


    def bind_two_or_more_files(self):
        command(two_or_more_files)
        app.parameter("file2", usage="file")(two_or_more_files)
        app.parameter("files", usage="file")(two_or_more_files)

    def test_two_or_more_files_usage(self):
        # converted from a v1 pin (ruled 2026-08-04): metavars
        # render <NAME.UPPER()> now--full clap, the git/docopt/
        # Rust convention; see register entry F-metavar
        self.bind_two_or_more_files()
        text = capture_stdout('help two-or-more-files')
        # every operand rides the one argument_decoration transform
        # now (ruled 2026-08-24, uniform): renames decorate too, so
        # all three show <FILE>
        # (the line wraps after [-h|--help] joined it, 2026-09-08)
        self.assertIn("<FILE> <FILE>", text)
        self.assertIn("[<FILE>]...", text)

    def test_two_or_more_files_1(self):
        self.bind_two_or_more_files()
        self.assert_process(
            'two-or-more-files a b ',
            (two_or_more_files, 'a', 'b', (),),
            )

    def test_two_or_more_files_2(self):
        self.bind_two_or_more_files()
        self.assert_process(
            'two-or-more-files a b c d e f g',
            (two_or_more_files, 'a', 'b', ('c', 'd', 'e', 'f', 'g'),),
            )


    def test_set_path_1(self):
        command(set_path)
        self.assert_process(
            'set-path a',
            (set_path, ['a']),
            )

    def test_set_path_2(self):
        command(set_path)
        self.assert_process(
            'set-path a:b:c',
            (set_path, ['a', 'b', 'c']),
            )


    def test_fgrep_1(self):
        command(fgrep)
        self.assert_process(
            'fgrep WM_CREATE window.c',
            (fgrep, 'WM_CREATE', 'window.c', '', 0, False),
            )

    def test_fgrep_2(self):
        command(fgrep)
        self.assert_process(
            'fgrep WM_CREATE --id 3 task.h',
            (fgrep, 'WM_CREATE', 'task.h', '', 3, False),
            )

    def test_fgrep_3(self):
        command(fgrep)
        self.assert_process(
            'fgrep --color blue WM_CREATE --id 55 task.h -v',
            (fgrep, 'WM_CREATE', 'task.h', 'blue', 55, True),
            )

    def test_fgrep_4(self):
        command(fgrep)
        self.assert_process(
            'fgrep --color blue WM_CREATE --verbose task.h --id 121',
            (fgrep, 'WM_CREATE', 'task.h', 'blue', 121, True),
            )


    def test_weird_1(self):
        command(weird)
        self.assert_process(
            'weird',
            (weird, (False, 'info')),
            )

    def test_weird_2(self):
        command(weird)
        self.assert_process(
            'weird -v',
            (weird, (True, 'info')),
            )

    def test_weird_3(self):
        command(weird)
        self.assert_process(
            'weird --log-level mickey',
            (weird, (False, 'mickey')),
            )

    def test_weird_4(self):
        command(weird)
        self.assert_process(
            'weird --verbose --log-level goofy',
            (weird, (True, 'goofy')),
            )

    def test_weird_5(self):
        command(weird)
        self.assert_process(
            'weird --log-level donald -v',
            (weird, (True, 'donald')),
            )

    def test_inferred_list_1(self):
        command(inferred_list)
        self.assert_process(
            'inferred-list x',
            (inferred_list, 'x', [0, 0.0]),
            )

    def test_inferred_list_2(self):
        command(inferred_list)
        self.assert_process(
            'inferred-list y 1 2.4',
            (inferred_list, 'y', [1, 2.4]),
            )

    def test_inferred_list_3(self):
        command(inferred_list)
        self.assert_process(
            'inferred-list z 2 4',
            (inferred_list, 'z', [2, 4.0]),
            )

    def test_undo_1(self):
        command(undo)
        self.assert_process(
            'undo',
            (undo, 0),
            )

    def test_undo_2(self):
        command(undo)
        self.assert_process(
            'undo 2',
            (undo, 2),
            )

    def test_hey_argparse_watch_this_1(self):
        command(hey_argparse_watch_this)
        self.assert_process(
            'hey-argparse-watch-this',
            (hey_argparse_watch_this, None, None, None),
            )

    def test_hey_argparse_watch_this_2(self):
        command(hey_argparse_watch_this)
        self.assert_process(
            'hey-argparse-watch-this -i 0 -f 0 -c 0j',
            (hey_argparse_watch_this, 0, 0.0, 0j),
            )

    def test_hey_argparse_watch_this_3(self):
        command(hey_argparse_watch_this)
        self.assert_process(
            'hey-argparse-watch-this -i 3 -f 4 -c 5',
            (hey_argparse_watch_this, 3, 4.0, (5+0j)),
            )

    def test_hey_argparse_watch_this_4(self):
        command(hey_argparse_watch_this)
        self.assert_process(
            'hey-argparse-watch-this -i -1 -f -2 -c -3j',
            (hey_argparse_watch_this, -1, -2.0, -3j),
            )

    def test_hey_argparse_watch_this_5(self):
        command(hey_argparse_watch_this)
        self.assert_process(
            'hey-argparse-watch-this -f inf',
            (hey_argparse_watch_this, None, float("inf"), None),
            )

    def test_hey_argparse_watch_this_6(self):
        command(hey_argparse_watch_this)
        self.assert_process(
            'hey-argparse-watch-this -f -inf',
            (hey_argparse_watch_this, None, float("-inf"), None),
            )

    def test_hey_argparse_watch_this_7(self):
        command(hey_argparse_watch_this)
        # can't use assert_process, that uses assertEqual,
        # and nan is never equal to nan.
        result = list(process(shlex.split('hey-argparse-watch-this -f -nan')))
        our_nan = result[2]
        self.assertTrue(math.isnan(our_nan))
        result[2] = "was_nan"
        self.assertEqual(result, [hey_argparse_watch_this, None, "was_nan", None])


    def test_options_stack_1(self):
        command(options_stack)
        self.assert_process(
            'options-stack',
            (options_stack, 'abc', False, False, (nested_option, False, False, (inner_option, False, False)))
            )

    def test_options_stack_2(self):
        command(options_stack)
        self.assert_process(
            'options-stack --option --nested',
            (options_stack, 'abc', False, False, (nested_option, False, False, (inner_option, False, False)))
            )

    def test_options_stack_3(self):
        command(options_stack)
        self.assert_process(
            'options-stack --option --nested -eca',
            (options_stack, 'abc', True, False, (nested_option, True, False, (inner_option, True, False)))
            )

    def test_options_stack_4(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(options_stack)
        self.assert_process(
            "options-stack --option --nested -ace",
            (options_stack, 'abc', True, False, (nested_option, True, False, (inner_option, True, False))),
            )

    def test_options_stack_5(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(options_stack)
        self.assert_process(
            "options-stack --option --nested -ace -b",
            (options_stack, 'abc', True, True, (nested_option, True, False, (inner_option, True, False))),
            )

    def test_options_stack_6(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(options_stack)
        self.assert_process(
            "options-stack --option --nested -ace -bdf",
            (options_stack, 'abc', True, True, (nested_option, True, True, (inner_option, True, True))),
            )

    def test_options_stack_7(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(options_stack)
        self.assert_process(
            "options-stack --option --nested -a -e",
            (options_stack, 'abc', True, False, (nested_option, False, False, (inner_option, True, False))),
            )

    def test_options_stack_8(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(options_stack)
        self.assert_process(
            "options-stack --option --nested -a -c",
            (options_stack, 'abc', True, False, (nested_option, True, False, (inner_option, False, False))),
            )

    def test_options_stack_9(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(options_stack)
        self.assert_process(
            "options-stack --option --nested -a -c",
            (options_stack, 'abc', True, False, (nested_option, True, False, (inner_option, False, False))),
            )


    def test_five_level_stack_1(self):
        command(five_level_stack)
        self.assert_process(
            "five-level-stack",
            (five_level_stack,  None, False, False),
            )

    def test_five_level_stack_2(self):
        command(five_level_stack)
        self.assert_process(
            "five-level-stack -a -d",
            (five_level_stack, (five_a, (five_d, None, False, False), False, False), False, False),
            )

    def test_five_level_stack_3(self):
        command(five_level_stack)
        self.assert_process(
            "five-level-stack -a -d -g -j -m",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), False, False), False, False), False, False), False, False),
            )

    def test_five_level_stack_4(self):
        command(five_level_stack)
        self.assert_process(
            "five-level-stack -a -d -g -j -m -e",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), False, False), False, False), True, False), False, False),
            )

    def test_five_level_stack_5(self):
        command(five_level_stack)
        self.assert_process(
            "five-level-stack -a -d -g -j -me",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), False, False), False, False), True, False), False, False),
            )

    def test_five_level_stack_6(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(five_level_stack)
        self.assert_process(
            "five-level-stack -a -d -g -j -me -n",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), True, False), False, False), False, False), True, False), False, False),
            )

    def test_five_level_stack_7(self):
        command(five_level_stack)
        self.assert_process(
            "five-level-stack -a -d -g -j -me",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), False, False), False, False), True, False), False, False),
            )

    def test_five_level_stack_8(self):
        command(five_level_stack)
        self.assert_process(
            "five-level-stack -a -d -g -j -mh -i",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), False, False), True, True), False, False), False, False),
            )


    def test_five_level_stack_9(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(five_level_stack)
        self.assert_process(
            "five-level-stack -a -d -g -j -mh -k",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), True, False), True, False), False, False), False, False),
            )

    def test_five_level_stack_10(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(five_level_stack)
        self.assert_process(
            "five-level-stack -a -d -g -j -mh -n",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), True, False), False, False), True, False), False, False), False, False),
            )

    def test_five_level_stack_11(self):
        command(five_level_stack)
        self.maxDiff=None
        self.assert_process(
            "five-level-stack -a -d -g -j -mn -o",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), True, True), False, False), False, False), False, False), False, False),
            )

    def test_five_level_stack_12(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(five_level_stack)
        self.assert_process(
            "five-level-stack -a -d -g -j -mb -e",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), False, False), False, False), True, False), True, False),
            )

    def test_five_level_stack_13(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(five_level_stack)
        self.assert_process(
            "five-level-stack -a -d -g -j -mb -h",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), False, False), True, False), False, False), True, False),
            )

    def test_five_level_stack_14(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(five_level_stack)
        self.assert_process(
            "five-level-stack -a -d -g -j -mb -k",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), True, False), False, False), False, False), True, False),
            )

    def test_five_level_stack_15(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(five_level_stack)
        self.assert_process(
            "five-level-stack -a -d -g -j -mb -n",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), True, False), False, False), False, False), False, False), True, False),
            )

    def test_five_level_stack_16(self):
        # converted from a v1 scope-rejection pin (ruled
        # 2026-07-08): the line is legal now--pin what it
        # means (see appeal.grammar.md, scoped options)
        command(five_level_stack)
        self.maxDiff = None
        self.assert_process(
            "five-level-stack -a -d -g -j -m -behknp",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, True, False, False), True, False), True, False), True, False), True, False), True, False),
            )


    def test_mixed_groups_1(self):
        command(multiple_groups)
        self.assert_process(
            "multiple-groups abc",
            (multiple_groups,
                'abc',
                (multiple_groups_child, None, None, None, False, (0, 0.0, False)),
                (multiple_groups_child, None, None, None, False, (0, 0.0, False)),
                ''
                ),
            )

    def test_mixed_groups_2(self):
        # fill-left-to-right (ruled 2026-08-20): 'def' can't complete the
        # optional bundle b (it needs child_a AND child_b), and we NEVER skip
        # an optional to reach a later one -- so this is an error, not 0.6.4's
        # completable a+d distribution (the older plan this test used to pin).
        command(multiple_groups)
        e = self.assert_process_raises(
            "multiple-groups abc def",
            appeal.AppealUsageError,
            )
        self.assertEqual(str(e), "missing argument 'child_b'")

    def test_mixed_groups_3(self):
        command(multiple_groups)
        self.assert_process_raises(
            "multiple-groups abc -v",
            appeal.AppealUsageError,
            )

    def test_mixed_groups_4(self):
        command(multiple_groups)
        self.assert_process(
            "multiple-groups a ba bb bc ca cb cc",
            (multiple_groups,
                'a',
                (multiple_groups_child, 'ba', 'bb', 'bc', False, (0, 0.0, False)),
                (multiple_groups_child, 'ca', 'cb', 'cc', False, (0, 0.0, False)),
                ''
                ),
            )

    def test_mixed_groups_5(self):
        # v2 divergence (the scope liberalization, again): v1
        # rejected -f before its group's window opened; v2's
        # interval model announce-binds it to the first window
        command(multiple_groups)
        self.assert_process(
            "multiple-groups -f a ba bb bc ca cb cc",
            (multiple_groups,
                'a',
                (multiple_groups_child, 'ba', 'bb', 'bc', True,
                 (0, 0.0, False)),
                (multiple_groups_child, 'ca', 'cb', 'cc', False,
                 (0, 0.0, False)),
                ''
                ),
            )

    def test_mixed_groups_6(self):
        command(multiple_groups)
        self.assert_process(
            "multiple-groups a -f ba bb bc ca cb cc",
            (multiple_groups,
                'a',
                (multiple_groups_child, 'ba', 'bb', 'bc', True, (0, 0.0, False)),
                (multiple_groups_child, 'ca', 'cb', 'cc', False, (0, 0.0, False)),
                ''
                ),
            )

    def test_mixed_groups_7(self):
        command(multiple_groups)
        self.assert_process(
            "multiple-groups a ba -f bb bc ca cb cc",
            (multiple_groups,
                'a',
                (multiple_groups_child, 'ba', 'bb', 'bc', True, (0, 0.0, False)),
                (multiple_groups_child, 'ca', 'cb', 'cc', False, (0, 0.0, False)),
                ''
                ),
            )

    def test_mixed_groups_8(self):
        command(multiple_groups)
        self.assert_process(
            "multiple-groups a ba bb -f bc ca cb cc",
            (multiple_groups,
                'a',
                (multiple_groups_child, 'ba', 'bb', 'bc', True, (0, 0.0, False)),
                (multiple_groups_child, 'ca', 'cb', 'cc', False, (0, 0.0, False)),
                ''
                ),
            )

    def test_mixed_groups_9(self):
        command(multiple_groups)
        self.assert_process(
            "multiple-groups a ba bb bc -f ca cb cc",
            (multiple_groups,
                'a',
                (multiple_groups_child, 'ba', 'bb', 'bc', False, (0, 0.0, False)),
                (multiple_groups_child, 'ca', 'cb', 'cc', True, (0, 0.0, False)),
                ''
                ),
            )

    def test_mixed_groups_10(self):
        command(multiple_groups)
        self.assert_process(
            "multiple-groups a ba bb bc ca -f cb cc",
            (multiple_groups,
                'a',
                (multiple_groups_child, 'ba', 'bb', 'bc', False, (0, 0.0, False)),
                (multiple_groups_child, 'ca', 'cb', 'cc', True, (0, 0.0, False)),
                ''
                ),
            )

    def test_mixed_groups_11(self):
        command(multiple_groups)
        self.assert_process(
            "multiple-groups a ba bb bc ca cb -f cc",
            (multiple_groups,
                'a',
                (multiple_groups_child, 'ba', 'bb', 'bc', False, (0, 0.0, False)),
                (multiple_groups_child, 'ca', 'cb', 'cc', True, (0, 0.0, False)),
                ''
                ),
            )

    # the stuff in the final group sticks around forever
    def test_mixed_groups_12(self):
        command(multiple_groups)
        self.assert_process(
            "multiple-groups a ba bb bc ca cb cc -f",
            (multiple_groups,
                'a',
                (multiple_groups_child, 'ba', 'bb', 'bc', False, (0, 0.0, False)),
                (multiple_groups_child, 'ca', 'cb', 'cc', True, (0, 0.0, False)),
                ''
                ),
            )


    def test_str_i_f_3(self):
        command(str_i_f)
        e = self.assert_process_raises(
            "str-i-f abc 1",
            appeal.AppealUsageError,
            )
        self.assertEqual(str(e), "missing argument 'real'")  # 1.0 message

    def test_str_i_f_2(self):
        command(str_i_f)
        # regression test:
        # when printing usage, we used to print the name of the last program
        # we'd called, regardless of whether or not it was currently running.
        # so this error used to read
        #   str_i_f, -v, --verbose requires 2 arguments in this argument group.
        e = self.assert_process_raises(
            "str-i-f abc 1 --verbose",
            appeal.AppealUsageError,
            )
        self.assertEqual(str(e), "missing argument 'real'")  # 1.0 message

    def test_str_i_f_1(self):
        command(str_i_f)
        e = self.assert_process_raises(
            "str-i-f abc 1 --option x",
            appeal.AppealUsageError,
            )
        self.assertEqual(str(e), "missing argument 'real'")  # 1.0 message

    def test_str_i_f_4(self):
        command(str_i_f)
        e = self.assert_process_raises(
            "str-i-f abc 1 --option",
            appeal.AppealUsageError,
            )
        self.assertEqual(str(e), "option '--option' requires a value")  # v2 message


    def test_app_class(self):
        # rewritten as a v2 pin (2026-07-08): v1's app.app_class()
        # two-registrar API retires; §8.6 spells it with the plain
        # decorators--@app.global_command() on the class,
        # @app.command() on the methods
        app = appeal.Appeal()

        instances = []
        @app.global_command()
        class MyApp:
            def __init__(self, *, verbose=False):
                self.verbose = verbose
                self.pattern = None
                self.filename = None
                self.context = None
                instances.append(self)

            @app.command()
            def fgrep(self, pattern, filename, *, context=0):
                self.pattern = pattern
                self.filename = filename
                self.context = context

            def dump(self):
                return self.verbose, self.pattern, self.filename, self.context

        result = app.process(shlex.split("-v fgrep patt -c 33 file")).result
        self.assertEqual(result, None)
        self.assertEqual(len(instances), 1)
        instance = instances.pop()
        self.assertIsInstance(instance, MyApp)
        self.assertEqual(instance.dump(), (True, 'patt', 'file', 33))


    def test_regression_raised_an_error_earlier_huh(self):
        # there's a usage error:
        # AppealUsageError(f"no argument supplied for {self}, we should have raised an error earlier huh.")
        # this test used to trip it.
        # (before I rewrote undoable converters to tie directly to argument groups)
        # (which was before I rewrote it two more times and renamed them to "discretionary" converter)
        command(earlier)
        self.assert_process(
            "earlier a  1  2.3 -v",
            (earlier,
                'a',
                (earlier_int_float1, 1, 2.3, True),
                (earlier_int_float2, 0, 0.0, False),
                ),
            )

    def test_discretionary_converter_torture_test_1(self):
        def first_child(*, verbose=False):
            return (first_child, verbose)

        def enfant_terrible(*, flag=0):
            return (enfant_terrible, flag)

        def parent(first_child: first_child=(first_child, False), enfant_terrible:enfant_terrible=(enfant_terrible, 0)):
            return (parent, first_child, enfant_terrible)

        @command
        def grandparent(parent:parent=(parent, (first_child, False), (enfant_terrible, 0))):
            return (grandparent, parent)

        self.assert_process(
            "grandparent --flag 3",
            (grandparent,
                (parent,
                    (first_child, False),
                    (enfant_terrible, 3),
                    ),
                ),
            )

    def test_custom_option(self):
        class MyOption(appeal.Option):
            def init(self, default):
                self.value = default

            def option(self, value:int=0):
                self.value = value

            def __call__(self):
                return self.value

        @command
        def c(a, b, *, option:MyOption=0):
            return (c, a, b, option)

        self.assert_process(
            "c aa bb",
            (c, 'aa', 'bb', 0)
            )

        self.assert_process(
            "c aa bb -o",
            (c, 'aa', 'bb', 0)
            )

        self.assert_process(
            "c aa bb -o 33",
            (c, 'aa', 'bb', 33)
            )

        self.assert_process(
            "c  xx -o 44 yy",
            (c, 'xx', 'yy', 44)
            )

        self.assert_process(
            "c  -o 55 xx yy",
            (c, 'xx', 'yy', 55)
            )


class NewStyleTests(AppealTestsBase):
    ##
    ## Experimenting with a new style of writing tests here.
    ## Currently, if you have a failing test, it takes a little
    ## work to extract it from the test harness.  So I'm experimenting
    ## with writing them in such a way that there's virtually
    ##

    def test_generate_docs_for_option_with_simple_type(self):
        import appeal

        def assertIn(needle, haystack):
            assert needle in haystack, f"{needle!r} not in {haystack!r}"

        # The test hands Appeal the script name explicitly (the
        # script= constructor argument), instead of depending on
        # whatever sys.argv[0] happens to be however the suite was
        # launched.  For an unnamed program Appeal derives the usage
        # name from os.path.basename(script), falling back to
        # 'program' when that's empty--so a bare or trailing-slash
        # argv[0] never degenerates into "usage:  command".  Each
        # expected prog is written out literally, so this pins
        # Appeal's behavior rather than recomputing it.
        cases = (
            ('frobnicate',                'frobnicate'),
            ('./frobnicate',              'frobnicate'),
            ('/usr/local/bin/frobnicate', 'frobnicate'),
            ('frobnicate.py',             'frobnicate.py'),
            ('',                          'program'),
            ('/opt/tools/',               'program'),
        )
        for script, expected_prog in cases:
            app = appeal.Appeal(script=script)

            @app.command()
            def nuttall(*, verbose: bool = False):
                """
                Demo function, first line.
                """
                if verbose:
                    print(f"verbose={verbose}")

            start, captured_print, end = make_stdout_capture()
            start()
            app.help()
            text = end()

            assertIn(f"usage: {expected_prog} [-h|--help [<SUBJECT>]] <COMMAND>",
                     text)
            # converted from a v1 pin (ruled 2026-08-05, the
            # Markdown pivot): the listing's heading is the
            # template's '## Commands', rendered setext-style
            assertIn("Commands\n--------", text)
            assertIn("nuttall", text)
            assertIn("Demo function, first line.", text)


##
## The v1 README's example programs, frozen 2026-07-09.
##
## These examples used to be extracted from README.md at test
## time ("I got tired of the examples in README.md not working
## ... so now I run them").  README.md now documents v2, so the
## v1 corpus carries its own copies of the v1 examples here,
## exactly as the extractor last saw them.
##
## readme_tests maps a v1 README section name (its '## ' heading)
## to that section's examples, in order of appearance.  Each
## entry is [example_source, times_run]: exec_readme() bumps the
## counter, and the completeness check at the bottom of this file
## fails if any example was never exercised.
##

readme_tests = {
    'Quickstart': [
        ['import appeal\nimport sys\n\napp = appeal.Appeal()\n\n@app.command()\ndef hello(name):\n    print(f"Hello, {name}!")\n\npass', 0],
        ['import appeal\nimport sys\n\napp = appeal.Appeal()\n\n@app.command()\ndef fgrep(pattern, *files, ignore_case=False):\n    if not files:\n        files = [\'-\']\n    print_file = len(files) > 1\n    if ignore_case:\n        pattern = pattern.lower()\n    for file in files:\n        if file == "-":\n            f = sys.stdin\n        else:\n            f = open(file, "rt")\n        for line in f:\n            if ignore_case:\n                match = pattern in line.lower()\n            else:\n                match = pattern in line\n            if match:\n                if print_file:\n                    print(file + ": ", end="")\n                print(line.rstrip())\n        if file != "-":\n            f.close()\n\n\nif __name__ == "__main__":\n    pass', 0],
    ],
    'Hello, World!': [
        ['import appeal\napp = appeal.Appeal()\n\n@app.command()\ndef hello(name):\n    print(f"Hello, {name}!")\n\npass', 0],
    ],
    'Default Values And `*args`': [
        ['import appeal\napp = appeal.Appeal()\n\n@app.command()\ndef fgrep(pattern, filename=None):\n    print(f"fgrep {pattern} {filename}")\n\npass', 0],
        ['import appeal\napp = appeal.Appeal()\n\n@app.command()\ndef fgrep(pattern, *filenames):\n    print(f"fgrep {pattern} {filenames}")\n\npass', 0],
    ],
    'Options, Opargs, And Keyword-Only Parameters': [
        ['import appeal\napp = appeal.Appeal()\n\n@app.command()\ndef fgrep(pattern, *filenames, color="", number=0, ignore_case=False):\n    print(f"fgrep {pattern} {filenames} {color!r} {number} {ignore_case}")\n\npass', 0],
    ],
    'Annotations And Introspection': [
        ['import appeal\napp = appeal.Appeal()\n\n@app.command()\ndef fgrep(pattern, *filenames, id:float=None):\n    print(f"fgrep {pattern} {filenames} {id}")\n\npass', 0],
        ['import appeal\napp = appeal.Appeal()\n\ndef int_and_float(integer: int, real: float):\n    return [integer*3, real*5]\n\n@app.command()\ndef fgrep(pattern, *filenames, position:int_and_float=(0, 0.0)):\n    print(f"fgrep {pattern} {filenames} {position}")\n\npass', 0],
        ['import appeal\napp = appeal.Appeal()\n\nclass IntAndFloat:\n    def __init__(self, integer: int, real: float):\n        self.integer = integer * 3\n        self.real = real * 5\n\n    def __repr__(self):\n        return f"<IntAndFloat {self.integer} {self.real}>"\n\n@app.command()\ndef fgrep(pattern, *filenames, position=IntAndFloat(0, 0.0)):\n    print(f"fgrep {pattern} {filenames} {position}")\n\npass', 0],
    ],
    'Specifying An Option More Than Once': [
        ['import appeal\napp = appeal.Appeal()\n\n@app.command()\ndef fgrep(*, verbose:appeal.counter()=0):\n    print(f"fgrep verbose={verbose!r}")\n\npass', 0],
        ['import appeal\napp = appeal.Appeal()\n\n@app.command()\ndef fgrep(*, pattern:appeal.accumulator=[]):\n    print(f"fgrep pattern={pattern!r}")\n\npass', 0],
        ['import appeal\napp = appeal.Appeal()\n\n@app.command()\ndef fgrep(*, pattern:appeal.accumulator[int]=[]):\n    print(f"fgrep pattern={pattern!r}")\n\npass', 0],
    ],
    'Data Validation': [
        ['import appeal\napp = appeal.Appeal()\n\n@app.command()\ndef go(direction:appeal.validate(\'up\', \'down\', \'left\', \'right\', \'forward\', \'back\')):\n    print(f"go direction={direction!r}")\n\npass', 0],
    ],
    'Multiple Options For The Same Parameter': [
        ['import appeal\napp = appeal.Appeal()\n\n@app.command()\n@app.option("direction", "--north", annotation=lambda: "north")\n@app.option("direction", "--south", annotation=lambda: "south")\n@app.option("direction", "--east",  annotation=lambda: "east")\n@app.option("direction", "--west",  annotation=lambda: "west")\ndef go(*, direction=\'north\'):\n    print(f"go direction={direction!r}")\n\npass', 0],
    ],
    'Recursive Converters': [
        ['import appeal\napp = appeal.Appeal()\n\ndef int_float(i: int, f: float):\n    return (i, f)\n\ndef my_converter(i_f: int_float, s: str):\n    return [i_f, s]\n\n@app.command()\ndef recurse(a:str, b:my_converter=[(0, 0), \'\']):\n    print(f"recurse a={a!r} b={b!r}")\n\npass', 0],
        ['import appeal\napp = appeal.Appeal()\n\ndef int_float(i: int, f: float):\n    return (i, f)\n\ndef my_converter(i_f: int_float, s: str, *, verbose=False):\n    return [i_f, s, verbose]\n\n@app.command()\ndef recurse2(a:str, b:my_converter=[(0, 0), \'\', False]):\n    print(f"recurse2 a={a!r} b={b!r}")\n\npass', 0],
    ],
    'Options that map other options': [
        ['import appeal\napp = appeal.Appeal()\n\ndef my_converter(a: int, *, verbose=False):\n    return [a, verbose]\n\n@app.command()\ndef inception(*, option:my_converter=[0, False]):\n    print(f"inception option={option!r}")\n\npass', 0],
    ],
    "Multiple options that aren't MultiOptions": [
        ['import appeal\napp = appeal.Appeal()\n\ndef my_converter(a: int, *, verbose=False):\n    return [a, verbose]\n\n@app.command()\ndef repetition(*args:my_converter):\n    print(f"repetition args={args!r}")\n\npass', 0],
    ],
    'Positional parameters that only consume options': [
        ['import appeal\napp = appeal.Appeal()\n\nclass Logging:\n    def __init__(self, *, verbose=False, log_level=\'info\'):\n        self.verbose = verbose\n        self.log_level = log_level\n\n    def __repr__(self):\n        return f"<Logging verbose={self.verbose!r} log_level={self.log_level}>"\n\n@app.command()\ndef mixin(log:Logging):\n    print(f"mixin log={log!r}")\n\npass', 0],
    ],
    'Classes, Instances, And Preparers': [
        ['import appeal\n\napp = appeal.Appeal()\napp_class, command_method = app.app_class()\n\n@app_class()\nclass MyApp:\n    def __init__(self, *, verbose=False):\n        print(f"MyApp init verbose={verbose!r}")\n        self.verbose = verbose\n\n    def __repr__(self):\n        return "<MyApp>"\n\n    @command_method()\n    def add(self, a, b, c):\n        print(f"MyApp add self={self!r} a={a!r} b={b!r} c={c!r} self.verbose={self.verbose!r}")\n\npass', 0],
        ['import appeal\n\napp = appeal.Appeal()\ncommand_method = app.command_method()\n\nclass MyApp:\n    def __init__(self, id):\n        self.id = id\n\n    def __repr__(self):\n        return f"<MyApp id={self.id!r}>"\n\n    @command_method()\n    def add(self, a, b, c):\n        print(f"MyApp add self={self!r} a={a!r} b={b!r} c={c!r}")\n\nmy_app = MyApp("dingus")\n\np = app.processor()\np.preparer(command_method.bind(my_app))\npass', 0],
    ],
}


# print the tests
if "-v" in sys.argv:
    for section, tests in readme_tests.items():
        for i, l in enumerate(tests):
            print(repr(section))
            t, counter = l
            print(f"    [ #{i}")
            for line in t.split("\n"):
                print("   ", line)
            print("    ]")


class ReadmeTests(AppealTestsBase):

    def exec_readme(self, section, index, cmdline, expected):
        global app
        global process

        l = readme_tests[section][index]
        text, count = l
        text = "p = None\napp = None\n" + text
        code = compile(text, "-", "exec")
        globals_dict = {}
        # print(section, index)
        # print(repr(text))
        result = exec(code, globals_dict, globals_dict)
        app = globals_dict['app']
        p = globals_dict['p']

        def my_process(args):
            return app.process(args).result
        process = my_process

        result = capture_stdout(cmdline)
        self.assertEqual(result.strip(), expected)
        app = process = None
        l[1] = count + 1

    # 'Quickstart'
    #     [ #0
    #     import appeal
    #     import sys
    #
    #     app = appeal.Appeal()
    #
    #     @app.command()
    #     def hello(name):
    #         print(f"Hello, {name}!")
    #
    #     app.main()
    #     ]
    def test_quickstart_0_1(self):
        self.exec_readme(
            'Quickstart',
            0,
            "hello world",
            "Hello, world!",
            )


    def test_quickstart_0_2(self):
        with self.assertRaises(appeal.AppealUsageError):
            self.exec_readme(
                'Quickstart',
                0,
                "hello",
                "Hello, !",
                )


    # 'Quickstart'
    #     [ #1
    #     import appeal
    #     import sys
    #
    #     app = appeal.Appeal()
    #
    #     @app.command()
    #     def fgrep(pattern, *files, ignore_case=False):
    #         if not files:
    #             files = ['-']
    #         print_file = len(files) > 1
    #         if ignore_case:
    #             pattern = pattern.lower()
    #         for file in files:
    #             if file == "-":
    #                 f = sys.stdin
    #             else:
    #                 f = open(file, "rt")
    #             for line in f:
    #                 if ignore_case:
    #                     match = pattern in line.lower()
    #                 else:
    #                     match = pattern in line
    #                 if match:
    #                     if print_file:
    #                         print(file + ": ", end="")
    #                     print(line.rstrip())
    #             if file != "-":
    #                 f.close()
    #
    #
    #     if __name__ == "__main__":
    #         app.main()
    #     ]
    def test_quickstart_1_1(self):
        # we're testing fgrep, using test.py itself as the input file.
        # let's search for a string that exists exactly once.
        # it's a little tricky because the string needs to be on the
        # command-line, and also in the output string that we're matching against.
        # so, we use automatic string concatenation to break up the special string.
        self.exec_readme(
            'Quickstart',
            1,
            "fgrep xy" f"zzy '{appeal_dir}/tests/test_v1.py'",
            # xyzzy!
            "# xyz" "zy!",
            )


    # 'Our First Example'
    #     [ #0
    #     import appeal
    #     app = appeal.Appeal()
    #     @app.command()
    #     def fgrep(pattern, filename):
    #         print(f"fgrep {pattern} {filename}")
    #     ]
    def test_our_first_example_0_1(self):
        self.exec_readme(
            'Hello, World!',
            0,
            "hello world",
            "Hello, world!",
            )

    def test_default_values_and_star_args_0_1(self):
        self.exec_readme(
            'Default Values And `*args`',
            0,
            "fgrep WM_CREATE",
            "fgrep WM_CREATE None",
            )

    def test_default_values_and_star_args_0_2(self):
        self.exec_readme(
            'Default Values And `*args`',
            0,
            "fgrep WM_CREATE foo",
            "fgrep WM_CREATE foo",
            )

    # 'Default Values And `*args`'
    #     [ #0
    #     import appeal
    #     app = appeal.Appeal()
    #     @app.command()
    #     def fgrep(pattern, filename=None):
    #         print(f"fgrep {pattern} {filename}")
    #     ]
    #     [ #1
    #     import appeal
    #     app = appeal.Appeal()
    #     @app.command()
    #     def fgrep(pattern, *filenames):
    #         print(f"fgrep {pattern} {filenames}")
    #     ]
    def test_default_values_and_star_args_1_1(self):
        self.exec_readme(
            'Default Values And `*args`',
            1,
            "fgrep WM_CREATE",
            "fgrep WM_CREATE ()",
            )

    def test_default_values_and_star_args_1_2(self):
        self.exec_readme(
            'Default Values And `*args`',
            1,
            "fgrep WM_CREATE a b c",
            "fgrep WM_CREATE ('a', 'b', 'c')",
            )

    def test_default_values_and_star_args_1_3(self):
        self.exec_readme(
            'Default Values And `*args`',
            1,
            "fgrep WM_CREATE a b c d f",
            "fgrep WM_CREATE ('a', 'b', 'c', 'd', 'f')",
            )

    # 'Options, Opargs, And Keyword-Only Parameters'
    #     [ #0
    #     import appeal
    #     app = appeal.Appeal()
    #     @app.command()
    #     def fgrep(pattern, *filenames, color="", id=0, verbose=False):
    #         print(f"fgrep {pattern} {filenames} {color!r} {id} {verbose}")
    #     ]
    def test_options_opargs_and_kwonly_0_1(self):
        self.exec_readme(
            'Options, Opargs, And Keyword-Only Parameters',
            0,
            "fgrep WM_CREATE --color red a b c --number 33 -i",
            "fgrep WM_CREATE ('a', 'b', 'c') 'red' 33 True",
            )

    def test_options_opargs_and_kwonly_0_2(self):
        self.exec_readme(
            'Options, Opargs, And Keyword-Only Parameters',
            0,
            "fgrep poodle -i x -n 88 y --color blue z",
            "fgrep poodle ('x', 'y', 'z') 'blue' 88 True",
            )

    def test_options_opargs_and_kwonly_0_3(self):
        self.exec_readme(
            'Options, Opargs, And Keyword-Only Parameters',
            0,
            "fgrep poodle x --number 88 y --color blue z",
            "fgrep poodle ('x', 'y', 'z') 'blue' 88 False",
            )

    # 'Annotations And Introspection',
    # [ #0
    # import appeal
    # app = appeal.Appeal()
    # @app.command()
    # def fgrep(pattern, *filenames, id:float=0.0):
    #     print(f"fgrep {pattern} {filenames} {id}")
    # ]
    def test_annotations_and_introspection_0_1(self):
        self.exec_readme(
            'Annotations And Introspection',
            0,
            "fgrep poodle x y z",
            "fgrep poodle ('x', 'y', 'z') None",
            )

    def test_annotations_and_introspection_0_2(self):
        self.exec_readme(
            'Annotations And Introspection',
            0,
            "fgrep noodle x --id 83.4 y z",
            "fgrep noodle ('x', 'y', 'z') 83.4",
            )

    def test_annotations_and_introspection_0_3(self):
        self.exec_readme(
            'Annotations And Introspection',
            0,
            "fgrep --id 98.5 spoodle x q z",
            "fgrep spoodle ('x', 'q', 'z') 98.5",
            )

    # 'Annotations And Introspection',
    #     [ #1
    #     import appeal
    #     app = appeal.Appeal()
    #     def int_and_float(integer: int, real: float):
    #         return [integer*3, real*5]
    #     @app.command()
    #     def fgrep(pattern, *filenames, position:int_and_float=(0, 0.0)):
    #         print(f"fgrep {pattern} {filenames} {position}")
    #     ]
    def test_annotations_and_introspection_1_1(self):
        self.exec_readme(
            'Annotations And Introspection',
            1,
            "fgrep canoodle q r s t",
            "fgrep canoodle ('q', 'r', 's', 't') (0, 0.0)",
            )

    def test_annotations_and_introspection_1_2(self):
        self.exec_readme(
            'Annotations And Introspection',
            1,
            "fgrep kit-and-kaboodle --position 1 3.5 v w x",
            "fgrep kit-and-kaboodle ('v', 'w', 'x') [3, 17.5]",
            )

    def test_annotations_and_introspection_1_3(self):
        self.exec_readme(
            'Annotations And Introspection',
            1,
            "fgrep snoodle v -p 2 4.5 -- w x",
            "fgrep snoodle ('v', 'w', 'x') [6, 22.5]",
            )

    def test_annotations_and_introspection_1_4(self):
        self.exec_readme(
            'Annotations And Introspection',
            1,
            "fgrep snoodle v w x -p 89 -100.5",
            "fgrep snoodle ('v', 'w', 'x') [267, -502.5]",
            )

    # 'Annotations And Introspection',
    # [ #2
    # import appeal
    # app = appeal.Appeal()
    # class IntAndFloat:
    #     def __init__(self, integer: int, real: float):
    #         self.integer = integer * 3
    #         self.real = real * 5
    #     def __repr__(self):
    #         return f"<IntAndFloat {self.integer} {self.real}>"
    # @app.command()
    # def fgrep(pattern, *filenames, position=IntAndFloat(0, 0.0)):
    #     print(f"fgrep {pattern} {filenames} {position}")
    # ]
    def test_annotations_and_introspection_2_1(self):
        self.exec_readme(
            'Annotations And Introspection',
            2,
            "fgrep canoodle q r s t",
            "fgrep canoodle ('q', 'r', 's', 't') <IntAndFloat 0 0.0>",
            )

    def test_annotations_and_introspection_2_2(self):
        self.exec_readme(
            'Annotations And Introspection',
            2,
            "fgrep kit-and-kaboodle --position 1 3.5 v w x",
            "fgrep kit-and-kaboodle ('v', 'w', 'x') <IntAndFloat 3 17.5>",
            )

    def test_annotations_and_introspection_2_3(self):
        self.exec_readme(
            'Annotations And Introspection',
            2,
            "fgrep snoodle v -p 2 4.5 -- w x",
            "fgrep snoodle ('v', 'w', 'x') <IntAndFloat 6 22.5>",
            )

    def test_annotations_and_introspection_2_4(self):
        self.exec_readme(
            'Annotations And Introspection',
            2,
            "fgrep snoodle v w x -p 89 -100.5",
            "fgrep snoodle ('v', 'w', 'x') <IntAndFloat 267 -502.5>",
            )

    # 'Specifying An Option More Than Once'
    #     [ #0
    #     import appeal
    #     app = appeal.Appeal()
    #     @app.command()
    #     def fgrep(*, verbose:appeal.counter()=0):
    #         print(f"fgrep verbose={verbose}")
    #     ]

    def test_specifying_an_option_more_than_once_0_1(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            0,
            "fgrep",
            "fgrep verbose=0",
            )

    def test_specifying_an_option_more_than_once_0_2(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            0,
            "fgrep -v",
            "fgrep verbose=1",
            )

    def test_specifying_an_option_more_than_once_0_3(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            0,
            "fgrep --verbose",
            "fgrep verbose=1",
            )

    def test_specifying_an_option_more_than_once_0_4(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            0,
            "fgrep -v --verbose",
            "fgrep verbose=2",
            )

    def test_specifying_an_option_more_than_once_0_5(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            0,
            "fgrep --verbose -v -v --verbose -v",
            "fgrep verbose=5",
            )

    def test_specifying_an_option_more_than_once_0_6(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            0,
            "fgrep -v -v -v --verbose",
            "fgrep verbose=4",
            )

    # 'Specifying An Option More Than Once'
    #     [ #1
    #     import appeal
    #     app = appeal.Appeal()
    #     @app.command()
    #     def fgrep(*, pattern:appeal.accumulator=[]):
    #         print(f"fgrep pattern={pattern}")
    #     ]

    def test_specifying_an_option_more_than_once_1_1(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            1,
            "fgrep",
            "fgrep pattern=[]",
            )

    def test_specifying_an_option_more_than_once_1_2(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            1,
            "fgrep --pattern=34",
            "fgrep pattern=['34']",
            )

    def test_specifying_an_option_more_than_once_1_3(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            1,
            "fgrep -p weightless",
            "fgrep pattern=['weightless']",
            )

    def test_specifying_an_option_more_than_once_1_4(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            1,
            "fgrep -p night --pattern so -p bright",
            "fgrep pattern=['night', 'so', 'bright']",
            )

    # 'Specifying An Option More Than Once'
    #     [ #2
    #     import appeal
    #     app = appeal.Appeal()
    #     @app.command()
    #     def fgrep(*, pattern:appeal.accumulator[int]=[]):
    #         print(f"fgrep pattern={pattern}")
    #     ]

    def test_specifying_an_option_more_than_once_2_1(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            2,
            "fgrep",
            "fgrep pattern=[]",
            )

    def test_specifying_an_option_more_than_once_2_2(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            2,
            "fgrep -p 8",
            "fgrep pattern=[8]",
            )

    def test_specifying_an_option_more_than_once_2_3(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            2,
            "fgrep --pattern 44 -p 22",
            "fgrep pattern=[44, 22]",
            )

    def test_specifying_an_option_more_than_once_2_4(self):
        self.exec_readme(
            'Specifying An Option More Than Once',
            2,
            "fgrep --pattern 2 -p 4 --pattern=6 -p 8 -p 10",
            "fgrep pattern=[2, 4, 6, 8, 10]",
            )

    # 'Data Validation'
    #     [ #0
    #     import appeal
    #     app = appeal.Appeal()
    #     @app.command()
    #     def go(direction:appeal.validate('up', 'down', 'left', 'right', 'forward', 'back')):
    #         print(f"go direction={direction}")
    #     ]
    def test_data_validation_0_1(self):
        self.exec_readme(
            'Data Validation',
            0,
            "go up",
            "go direction='up'",
            )

    def test_data_validation_0_2(self):
        self.exec_readme(
            'Data Validation',
            0,
            "go down",
            "go direction='down'",
            )

    def test_data_validation_0_3(self):
        self.exec_readme(
            'Data Validation',
            0,
            "go left",
            "go direction='left'",
            )

    def test_data_validation_0_4(self):
        self.exec_readme(
            'Data Validation',
            0,
            "go right",
            "go direction='right'",
            )

    def test_data_validation_0_5(self):
        self.exec_readme(
            'Data Validation',
            0,
            "go forward",
            "go direction='forward'",
            )

    def test_data_validation_0_6(self):
        self.exec_readme(
            'Data Validation',
            0,
            "go back",
            "go direction='back'",
            )


    # 'Multiple Options For The Same Parameter'
    #     [ #0
    #     import appeal
    #     app = appeal.Appeal()
    #     @app.command()
    #     @app.option("direction", "--north", annotation=lambda: "north")
    #     @app.option("direction", "--south", annotation=lambda: "south")
    #     @app.option("direction", "--east", annotation=lambda: "east")
    #     @app.option("direction", "--west", annotation=lambda: "west")
    #     def go(*, direction='north'):
    #         print(f"go direction={direction}")
    #     ]
    def test_multiple_options_for_the_same_parameter_0_1(self):
        self.exec_readme(
            'Multiple Options For The Same Parameter',
            0,
            "go",
            "go direction='north'",
            )

    def test_multiple_options_for_the_same_parameter_0_2(self):
        self.exec_readme(
            'Multiple Options For The Same Parameter',
            0,
            "go --north",
            "go direction='north'",
            )

    def test_multiple_options_for_the_same_parameter_0_3(self):
        self.exec_readme(
            'Multiple Options For The Same Parameter',
            0,
            "go --south",
            "go direction='south'",
            )

    def test_multiple_options_for_the_same_parameter_0_4(self):
        self.exec_readme(
            'Multiple Options For The Same Parameter',
            0,
            "go --east",
            "go direction='east'",
            )

    def test_multiple_options_for_the_same_parameter_0_5(self):
        self.exec_readme(
            'Multiple Options For The Same Parameter',
            0,
            "go --west",
            "go direction='west'",
            )

    # 'Recursive Converters'
    #     [ #0
    #     import appeal
    #     app = appeal.Appeal()
    #
    #     def int_float(i: int, f: float):
    #         return (i, f)
    #
    #     def my_converter(i_f: int_float, s: str):
    #         return [i_f, s]
    #
    #     @app.command()
    #     def recurse(a:str, b:my_converter=[(0, 0), '']):
    #         print(f"recurse a={a} b={b}")
    #
    #     app.main()
    #     ]
    def test_recursive_converters_0_1(self):
        self.exec_readme(
            'Recursive Converters',
            0,
            "recurse x",
            "recurse a='x' b=[(0, 0), '']",
            )

    def test_recursive_converters_0_2(self):
        self.exec_readme(
            'Recursive Converters',
            0,
            "recurse abc 1 2 abc",
            "recurse a='abc' b=[(1, 2.0), 'abc']",
            )


    # 'Recursive Converters'
    #     [ #1
    #   import appeal
    #   app = appeal.Appeal()
    #
    #   def int_float(i: int, f: float):
    #       return (i, f)
    #
    #   def my_converter(i_f: int_float, s: str, *, verbose=False):
    #       return [i_f, s, verbose]
    #
    #   @app.command()
    #   def recurse(a:str, b:my_converter=[(0, 0), '', False]):
    #       print(f"weird a={a} b={b}")
    #
    #   app.main()

    def test_recursive_converters_1_1(self):
        self.exec_readme(
            'Recursive Converters',
            1,
            "recurse2 x",
            "recurse2 a='x' b=[(0, 0), '', False]",
            )

    def test_recursive_converters_1_2(self):
        self.exec_readme(
            'Recursive Converters',
            1,
            "recurse2 x 35 -v 46 googly",
            "recurse2 a='x' b=[(35, 46.0), 'googly', True]",
            )

    def test_recursive_converters_1_3(self):
        self.exec_readme(
            'Recursive Converters',
            1,
            "recurse2 way 77 88 --verbose no",
            "recurse2 a='way' b=[(77, 88.0), 'no', True]",
            )


    # 'Now Witness The Power Of This Fully Armed And Operational Battle Station'
    #     [ #0
    #     import appeal
    #     app = appeal.Appeal()
    #
    #     def my_converter(a: int, *, verbose=False):
    #         return [a, verbose]
    #
    #     @app.command()
    #     def inception(*args:my_converter):
    #         print(f"inception args={args}")
    #
    #     app.main()
    #     ]
    def test_now_witness_the_power_of_this_etc_0_1(self):
        self.exec_readme(
            'Options that map other options',
            0,
            "inception",
            "inception option=[0, False]",
            )

    def test_now_witness_the_power_of_this_etc_0_2(self):
        self.exec_readme(
            'Options that map other options',
            0,
            "inception -o 33",
            "inception option=[33, False]",
            )

    def test_now_witness_the_power_of_this_etc_0_3(self):
        self.exec_readme(
            'Options that map other options',
            0,
            "inception -o 1965 -v",
            "inception option=[1965, True]",
            )

    def test_now_witness_the_power_of_this_etc_0_4(self):
        self.exec_readme(
            'Options that map other options',
            0,
            "inception --option=2112 --verbose",
            "inception option=[2112, True]",
            )

    # 'Now Witness The Power Of This Fully Armed And Operational Battle Station'
    #     [ #1
    #     import appeal
    #     app = appeal.Appeal()
    #     def my_converter(a: int, *, verbose=False):
    #         return [a, verbose]
    #     @app.command()
    #     def weird(*args:my_converter):
    #         print(f"weird args={args}")
    #     ]
    def test_now_witness_the_power_of_this_etc_1_1(self):
        self.exec_readme(
            "Multiple options that aren't MultiOptions",
            0,
            "repetition",
            "repetition args=()",
            )

    def test_now_witness_the_power_of_this_etc_1_2(self):
        self.exec_readme(
            "Multiple options that aren't MultiOptions",
            0,
            "repetition 0",
            "repetition args=([0, False],)",
            )

    def test_now_witness_the_power_of_this_etc_1_3(self):
        self.exec_readme(
            "Multiple options that aren't MultiOptions",
            0,
            "repetition 0 1 -v 2 3 --verbose 4 5",
            "repetition args=([0, False], [1, False], [2, True], [3, False], [4, True], [5, False])",
            )

    # 'Now Witness The Power Of This Fully Armed And Operational Battle Station'
    #     [ #2
    #     import appeal
    #     app = appeal.Appeal()
    #
    #     class Logging:
    #         def __init__(self, *, verbose=False, log_level='info'):
    #             self.verbose = verbose
    #             self.log_level = log_level
    #
    #         def __repr__(self):
    #             return f"<Logging verbose={self.verbose} log_level={self.log_level}>"
    #
    #     @app.command()
    #     def mixin(log:Logging):
    #         print(f"mixin log={log}")
    #
    #     app.main()
    #     ]
    def test_now_witness_the_power_of_this_etc_2_1(self):
        self.exec_readme(
            'Positional parameters that only consume options',
            0,
            "mixin",
            "mixin log=<Logging verbose=False log_level=info>",
            )

    def test_now_witness_the_power_of_this_etc_2_2(self):
        self.exec_readme(
            'Positional parameters that only consume options',
            0,
            "mixin --log-level ascerbic -v",
            "mixin log=<Logging verbose=True log_level=ascerbic>",
            )

    def test_now_witness_the_power_of_this_etc_2_3(self):
        self.exec_readme(
            'Positional parameters that only consume options',
            0,
            "mixin -l didactic  --verbose",
            "mixin log=<Logging verbose=True log_level=didactic>",
            )

    def test_now_witness_the_power_of_this_etc_2_4(self):
        self.exec_readme(
            'Positional parameters that only consume options',
            0,
            "mixin -l elective",
            "mixin log=<Logging verbose=False log_level=elective>",
            )


    # 'Classes, Instances, And Preparers'                                                 [202/1984]
    #     [ #0
    #     import appeal
    #
    #     app = appeal.Appeal()
    #     command_method = app.command_method()
    #
    #     class MyApp:
    #         def __init__(self, id):
    #             self.id = id
    #
    #         def __repr__(self):
    #             return f"<MyApp id={self.id!r}>"
    #
    #         @command_method()
    #         def add(self, a, b, c):
    #             print(f"MyApp add self={self} a={a} b={b} c={c}")
    #
    #     my_app = MyApp("dingus")
    #
    #     p = app.processor()
    #     p.preparer(command_method.bind(my_app))
    #     pass
    #     ]

    def _class_app(self, printer):
        # the v1 README's "Classes, Instances, And Preparers"
        # examples, rewritten as v2 pins (2026-07-08): the class
        # decorated whole; the preparer API retires with it (the
        # environment does its job internally)
        app = appeal.Appeal()

        @app.global_command()
        class MyApp:
            def __init__(self, *, verbose=False):
                self.verbose = verbose
                printer(f"MyApp init verbose={verbose}")

            @app.command()
            def add(self, a, b, c):
                printer(f"MyApp add self=<MyApp> a={a!r} b={b!r} "
                        f"c={c!r} self.verbose={self.verbose}")

        return app

    def test_classes_instances_and_preparers_0_1(self):
        lines = []
        app = self._class_app(lines.append)
        app.process(shlex.split("add f g h")).result
        self.assertEqual(lines, [
            "MyApp init verbose=False",
            "MyApp add self=<MyApp> a='f' b='g' c='h' self.verbose=False"])

    def test_classes_instances_and_preparers_0_2(self):
        lines = []
        app = self._class_app(lines.append)
        app.process(shlex.split("-v add f g h")).result
        self.assertEqual(lines, [
            "MyApp init verbose=True",
            "MyApp add self=<MyApp> a='f' b='g' c='h' self.verbose=True"])

    def test_classes_instances_and_preparers_1_1(self):
        # v1's second technique--a pre-made instance--survives as
        # ordinary bound methods: no preparer machinery needed
        lines = []
        app = appeal.Appeal()

        class MyApp:
            def __init__(self, id):
                self.id = id

            def add(self, a, b, c):
                lines.append(f"MyApp add self=<MyApp id={self.id!r}> "
                             f"a={a!r} b={b!r} c={c!r}")

        my_app = MyApp("dingus")
        app.command()(my_app.add)
        app.process(shlex.split("add f g h")).result
        self.assertEqual(lines, [
            "MyApp add self=<MyApp id='dingus'> a='f' b='g' c='h'"])


class BugfixRegressionTests(AppealTestsBase):
    #
    # Regression tests for bugs found during code review (Feb 2026).
    # Most of these bugs lived on error paths that Appeal's own test
    # suite never exercised, which is why they went unnoticed.
    #

    def setUp(self):
        self.app = appeal.Appeal(version="0.5")

    def test_counter_negative_step(self):
        # counter() with a negative step used to crash.  0.6.4's option()
        # computed "min if self.step > 0 else max", and in the negative
        # case "max" resolved to the (float) max PARAMETER rather than the
        # builtin, producing a non-callable.  1.0 renamed the parameters
        # (step -> delta, max -> clamp), so the builtin can't be shadowed,
        # and the clamp is a direction-agnostic barrier--no min/max pick.
        app = self.app
        @app.command()
        def cmd(*, v:appeal.counter(delta=-1)=0):
            return v
        self.assertEqual(app.process(shlex.split("cmd -v -v")).result, -2)

    def test_no_argument_option_given_value(self):
        # Specifying "=value" on an option that takes no oparg used to put
        # the repr of the denormalize_option *function* into the error
        # message instead of the option name.
        app = self.app
        @app.command()
        def cmd(*, flag=False):
            return flag
        with self.assertRaises(appeal.AppealUsageError) as cm:
            app.process(shlex.split("cmd --flag=x")).result
        msg = str(cm.exception)
        self.assertIn("--flag", msg)
        self.assertNotIn("<function", msg)

    def test_mapping_duplicate_key_message(self):
        # The "defined ... more than once" message wasn't an f-string, so it
        # printed the literal "{key}" rather than the offending key.
        app = self.app
        @app.command()
        def cmd(*, define:appeal.mapping={}):
            return define
        with self.assertRaises(appeal.AppealUsageError) as cm:
            app.process(shlex.split("cmd --define key=one --define key=two")).result
        msg = str(cm.exception)
        self.assertIn("key", msg)
        self.assertNotIn("{key}", msg)

    def test_validate_non_homogeneous_message(self):
        # validate()'s non-homogeneous error message wasn't an f-string, so
        # it printed the literal "{failed}" rather than the offending values.
        with self.assertRaises(appeal.ConfigurationError) as cm:
            appeal.validate(int, "notatype")
        msg = str(cm.exception)
        self.assertIn("notatype", msg)
        self.assertNotIn("{failed}", msg)


class ConfigFileReadingTests(AppealTestsBase):
    #
    # Coverage + regression tests for the 0.6 config-file reading API
    # (read_mapping / read_iterable / read_csv) and the Charm mapping and
    # iterator compilers behind them.
    #
    # These paths were almost entirely uncovered.  read_iterable, read_csv
    # in positional mode, and read_mapping with a MultiOption all shipped
    # broken: the mapping/iterator compilers called next_to_o() with a
    # usage_name= keyword the assembler method never accepted.
    #

    def setUp(self):
        self.app = appeal.Appeal(version="0.5")

    # ---- read_mapping ----

    # 0.6.4 converted '5' to 5 here; 1.0 reads a mapping as typed
    # data--an int is an int (Larry, 2026-09-11)
    def test_read_mapping_flat_and_convert(self):
        def cfg(a:int, b:str='x'):
            return (a, b)
        self.assertEqual(self.app.read_mapping(cfg, {'a': 5, 'b': 'hi'}), (5, 'hi'))

    def test_read_mapping_default_used(self):
        def cfg(a:int, b:str='x'):
            return (a, b)
        self.assertEqual(self.app.read_mapping(cfg, {'a': 7}), (7, 'x'))

    def test_read_mapping_missing_required(self):
        def cfg(a:int, b:str='x'):
            return (a, b)
        with self.assertRaises(Exception) as cm:
            self.app.read_mapping(cfg, {'b': 'only'})
        self.assertIn("a", str(cm.exception))

    def test_read_mapping_nested(self):
        def read_b(verbose=False, color='black'):
            return (verbose, color)
        def cfg(a:int, b:read_b):
            return (a, b)
        self.assertEqual(
            self.app.read_mapping(cfg, {'a': 33, 'b': {'verbose': True, 'color': 'blue'}}),
            (33, (True, 'blue')),
            )

    def test_read_mapping_unnested(self):
        app = self.app
        @app.unnested()
        def read_b(verbose=False, color='black'):
            return (verbose, color)
        def cfg(a:int, b:read_b):
            return (a, b)
        self.assertEqual(
            app.read_mapping(cfg, {'a': 1, 'verbose': True, 'color': 'red'}),
            (1, (True, 'red')),
            )

    # ---- read_iterable ----

    def test_read_iterable_basic(self):
        def row(a:int, b:str):
            return (a, b)
        self.assertEqual(
            self.app.read_iterable(row, [['1', 'x'], ['2', 'y']]),
            [(1, 'x'), (2, 'y')],
            )

    def test_read_iterable_skips_empty_rows(self):
        def row(a:int, b:str):
            return (a, b)
        self.assertEqual(
            self.app.read_iterable(row, [['1', 'x'], [], ['2', 'y']]),
            [(1, 'x'), (2, 'y')],
            )

    def test_read_iterable_var_positional(self):
        def row(first, *rest:int):
            return (first, rest)
        self.assertEqual(
            self.app.read_iterable(row, [['a', '1', '2', '3']]),
            [('a', (1, 2, 3))],
            )

    # ---- read_csv ----

    def test_read_csv_positional(self):
        import csv, io
        def row(a:int, b:str):
            return (a, b)
        reader = csv.reader(io.StringIO("h1,h2\n1,x\n2,y\n"))
        # first row is consumed as headings and ignored in positional mode
        self.assertEqual(self.app.read_csv(row, reader), [(1, 'x'), (2, 'y')])

    def test_read_csv_mapping(self):
        import csv, io
        def row(a:int, b:str):
            return (a, b)
        reader = csv.reader(io.StringIO("a,b\n10,foo\n20,bar\n"))
        self.assertEqual(
            self.app.read_csv(row, reader, first_row_map={'a': 'a', 'b': 'b'}),
            [(10, 'foo'), (20, 'bar')],
            )

    def test_read_csv_mapping_remaps_headings(self):
        import csv, io
        def row(x:int, y:str):
            return (x, y)
        reader = csv.reader(io.StringIO("col_x,col_y\n1,z\n"))
        self.assertEqual(
            self.app.read_csv(row, reader, first_row_map={'col_x': 'x', 'col_y': 'y'}),
            [(1, 'z')],
            )

    def test_read_mapping_with_multioption_list(self):
        # README-documented pattern: a child MultiOption (here accumulator)
        # reads its repeated values from a list keyed under that parameter
        # name in the mapping.
        def cfg(lines:appeal.accumulator, color:str=''):
            return (lines, color)
        self.assertEqual(
            self.app.read_mapping(cfg, {'color': 'blue', 'lines': ['a', 'b', 'c']}),
            (['a', 'b', 'c'], 'blue'),
            )

    def test_read_iterable_rejects_keyword_only(self):
        # The iterator compiler can't position-feed a keyword-only parameter,
        # so it raises ConfigurationError at compile time.  Locks in the
        # f-string fix at line 3260 -- the message must interpolate the
        # offending parameter name.
        def fn(a, *, b=1):
            return (a, b)
        with self.assertRaises(appeal.ConfigurationError) as cm:
            self.app.read_iterable(fn, [['x']])
        msg = str(cm.exception)
        self.assertIn("'b'", msg)
        self.assertNotIn("{child.name}", msg)
        self.assertNotIn("{parameter.name}", msg)

    def test_read_iterable_rejects_var_keyword(self):
        # Same for **kwargs.  Locks in the f-string fix at line 3262.
        def fn(a, **kw):
            return (a, kw)
        with self.assertRaises(appeal.ConfigurationError) as cm:
            self.app.read_iterable(fn, [['x']])
        msg = str(cm.exception)
        self.assertIn("'kw'", msg)
        self.assertNotIn("{child.name}", msg)
        self.assertNotIn("{parameter.name}", msg)

    def test_read_mapping_multi_param_multioption_rejects_scalars(self):
        # When a multi-parameter MultiOption is read from a mapping, each
        # element of the list value must itself be iterable (so its items
        # can feed the option's multiple parameters).  Scalars trigger the
        # compile-time-emitted abort opcode at line ~3050, which exercises
        # the runtime abort handler at line ~4579 as well as locking in
        # the f-string fix from the bug-review pass.
        # (v2 divergence, 2026-07-09: multi-parameter positional
        # MultiOptions BUILD now--the mapping readers feed them
        # sequences of sequences--so the rejection moved from
        # build time to read time, still loud, still saying why)
        class TwoParam(appeal.MultiOption):
            def init(self, default=None):
                self.results = []
            def option(self, x:int, y:int):
                self.results.append((x, y))
            def __call__(self):
                return self.results
        def cfg(pairs:TwoParam):
            return pairs
        with self.assertRaises(Exception) as cm:
            self.app.read_mapping(cfg, {'pairs': [1, 2, 3]})
        msg = str(cm.exception)
        self.assertIn("TwoParam", msg)
        self.assertIn("occurrence takes 2 values", msg)


class ConverterVocabularyTests(AppealTestsBase):
    #
    # Coverage for Appeal's built-in converter vocabulary:
    # validate, validate_range, split, accumulator, mapping, counter.
    #

    def setUp(self):
        self.app = appeal.Appeal(version="0.5")

    def test_validate_ok(self):
        app = self.app
        @app.command()
        def go(direction:appeal.validate('up', 'down', 'left')):
            return direction
        self.assertEqual(app.process(shlex.split("go up")).result, 'up')

    def test_validate_rejects(self):
        app = self.app
        @app.command()
        def go(direction:appeal.validate('up', 'down', 'left')):
            return direction
        with self.assertRaises(appeal.AppealUsageError):
            app.process(shlex.split("go sideways")).result

    def test_validate_range_inside(self):
        app = self.app
        @app.command()
        def n(v:appeal.validate_range(0, 10)):
            return v
        self.assertEqual(app.process(shlex.split("n 5")).result, 5)

    def test_validate_range_equals_stop_allowed(self):
        app = self.app
        @app.command()
        def n(v:appeal.validate_range(0, 10)):
            return v
        # validate_range differs from range(): the stop value is allowed.
        self.assertEqual(app.process(shlex.split("n 10")).result, 10)

    def test_validate_range_rejects_out_of_range(self):
        app = self.app
        @app.command()
        def n(v:appeal.validate_range(0, 10)):
            return v
        with self.assertRaises(appeal.AppealUsageError):
            app.process(shlex.split("n 99")).result

    def test_validate_range_clamp(self):
        app = self.app
        @app.command()
        def n(v:appeal.validate_range(0, 10, clamp=True)):
            return v
        self.assertEqual(app.process(shlex.split("n 99")).result, 10)

    def test_split_with_delimiter(self):
        app = self.app
        @app.command()
        def s(items:appeal.split(':')=''):
            return items
        self.assertEqual(app.process(shlex.split("s a:b:c")).result, ['a', 'b', 'c'])

    def test_split_default_whitespace(self):
        app = self.app
        @app.command()
        def s(items:appeal.split()=''):
            return items
        self.assertEqual(app.process(shlex.split("s 'a b c'")).result, ['a', 'b', 'c'])

    def test_accumulator_typed(self):
        app = self.app
        @app.command()
        def a(*, p:appeal.accumulator[int]=[]):
            return p
        self.assertEqual(app.process(shlex.split("a -p 1 -p 2 -p 3")).result, [1, 2, 3])

    def test_accumulator_multiple_types(self):
        app = self.app
        @app.command()
        def a(*, p:appeal.accumulator[int, float]=[]):
            return p
        self.assertEqual(
            app.process(shlex.split("a -p 1 2.5 -p 3 4.5")).result,
            [(1, 2.5), (3, 4.5)],
            )

    def test_mapping(self):
        app = self.app
        @app.command()
        def m(*, define:appeal.mapping={}):
            return define
        self.assertEqual(
            app.process(shlex.split("m --define k1=v1 --define k2=v2")).result,
            {'k1': 'v1', 'k2': 'v2'},
            )

    def test_mapping_typed(self):
        app = self.app
        @app.command()
        def m(*, define:appeal.mapping[str, int]={}):
            return define
        self.assertEqual(
            app.process(shlex.split("m --define age=5 --define count=9")).result,
            {'age': 5, 'count': 9},
            )

    def test_counter(self):
        app = self.app
        @app.command()
        def c(*, v:appeal.counter()=0):
            return v
        self.assertEqual(app.process(shlex.split("c -v -v -v")).result, 3)

    def test_counter_max(self):
        app = self.app
        @app.command()
        def c(*, v:appeal.counter(clamp=2)=0):
            return v
        self.assertEqual(app.process(shlex.split("c -v -v -v -v")).result, 2)


class OptionParsingTests(AppealTestsBase):
    #
    # Coverage for the interpreter's command-line option and positional
    # parsing edge cases.
    #

    def setUp(self):
        self.app = appeal.Appeal(version="0.5")

    def test_long_option_equals_value(self):
        # --name=joe style; the option-and-value share one argv token.
        app = self.app
        @app.command()
        def c(*, name='', age:int=0):
            return (name, age)
        self.assertEqual(
            app.process(shlex.split("c --name=joe --age=30")).result,
            ('joe', 30),
            )

    def test_short_option_chain(self):
        # -abc collapses three single-character zero-oparg short options.
        app = self.app
        @app.command()
        def c(*, a=False, b=False, c=False):
            return (a, b, c)
        self.assertEqual(
            app.process(shlex.split("c -abc")).result,
            (True, True, True),
            )

    def test_optional_positional_omitted(self):
        # No positional supplied for a parameter with a default.
        # Exercises the engine's "next_to_o, required=False, iterator
        # exhausted -> resume loop 1 with flag=False" path on the
        # command line (as opposed to inside an in-program iterator).
        app = self.app
        @app.command()
        def c(name='default'):
            return name
        self.assertEqual(app.process(shlex.split("c")).result, 'default')

    def test_optional_positional_given(self):
        # The positive arm of the same parameter shape.
        app = self.app
        @app.command()
        def c(name='default'):
            return name
        self.assertEqual(app.process(shlex.split("c specified")).result, 'specified')

    def test_short_option_concatenated_oparg(self):
        # -fX where -f takes exactly one *optional* oparg.  Appeal allows
        # the concatenated form in this specific case.
        app = self.app
        def with_default(value='hello'):
            return value
        @app.command()
        def c(*, f:with_default=''):
            return f
        self.assertEqual(app.process(shlex.split("c -fjoe")).result, 'joe')

    def test_short_option_bare_uses_converter_default(self):
        # -f alone, no oparg: the converter's own default ('hello') applies.
        app = self.app
        def with_default(value='hello'):
            return value
        @app.command()
        def c(*, f:with_default=''):
            return f
        self.assertEqual(app.process(shlex.split("c -f")).result, 'hello')

    def test_short_option_equals_value(self):
        # DELIBERATE v1 -> v2 DIVERGENCE (ruled 2026-08-27, getopt-pure):
        # '=' is NOT a separator for short options.  v1 stripped it
        # (-f=joe was 'joe'); getopt/click/docopt do not, and neither
        # does v2 now--'-f=joe' binds the rest of the token VERBATIM,
        # '=' and all, so f is '=joe'.  ('=' remains the separator for
        # the LONG spelling, --pattern=6.)
        app = self.app
        def with_default(value='hello'):
            return value
        @app.command()
        def c(*, f:with_default=''):
            return f
        self.assertEqual(app.process(shlex.split("c -f=joe")).result, '=joe')

    def test_short_option_concat_binds_rest(self):
        # DELIBERATE v1 -> v2 DIVERGENCE (ruled 2026-07-09):
        # getopt's rule adopted.  v1 rejected -fjoe for a
        # required-oparg short option ("must be last"); v2 binds
        # the rest of the token as the oparg, like getopt,
        # GNU getopt, and argparse.
        app = self.app
        @app.command()
        def c(*, f=''):
            return f
        self.assertEqual(app.process(shlex.split("c -fjoe")).result, 'joe')

    def test_short_option_concat_takes_rest_verbatim(self):
        # DELIBERATE v1 -> v2 DIVERGENCE (same ruling): the rest of
        # the token is the oparg VERBATIM.  Nothing in it is a
        # separator--not even a '='--so -fjoe=extra is -f 'joe=extra'
        # (think -DNAME=1).
        app = self.app
        @app.command()
        def c(*, f=''):
            return f
        self.assertEqual(app.process(shlex.split("c -fjoe=extra")).result,
                         'joe=extra')

    def test_unknown_option_rejected(self):
        # An option the program doesn't define should produce a clean error.
        app = self.app
        @app.command()
        def c(*, name=''):
            return name
        with self.assertRaises(appeal.AppealUsageError) as cm:
            app.process(shlex.split("c --bogus value")).result
        self.assertIn("--bogus", str(cm.exception))

    def test_too_many_positionals_rejected(self):
        # Extra positionals beyond the signature should produce a clean error.
        app = self.app
        @app.command()
        def c(a, b):
            return (a, b)
        with self.assertRaises(appeal.AppealUsageError):
            app.process(shlex.split("c x y z")).result

    def test_missing_required_positional_rejected(self):
        app = self.app
        @app.command()
        def c(a, b):
            return (a, b)
        with self.assertRaises(appeal.AppealUsageError):
            app.process(shlex.split("c only_one")).result

    def test_end_of_options_terminator(self):
        # '--' on the command line terminates option processing: subsequent
        # tokens that start with '-' are treated as positionals.
        app = self.app
        @app.command()
        def c(*args, verbose=False):
            return (verbose, args)
        self.assertEqual(
            app.process(shlex.split("c --verbose -- --not-an-option positional")).result,
            (True, ('--not-an-option', 'positional')),
            )


class SignatureUtilityTests(unittest.TestCase):
    #
    # Direct unit tests for the signature-stripping utilities used by the
    # class-binding machinery.  Tested directly so they have coverage even
    # though their main callers will be rewritten in the app.cls() redesign.
    #

    def test_strip_first_argument_strips_first(self):
        import inspect
        from appeal import strip_first_argument_from_signature
        def f(self, a, b): pass
        result = strip_first_argument_from_signature(inspect.signature(f))
        self.assertEqual(list(result.parameters), ['a', 'b'])

    def test_strip_first_argument_strips_first_regardless_of_name(self):
        import inspect
        from appeal import strip_first_argument_from_signature
        def f(this, a): pass
        result = strip_first_argument_from_signature(inspect.signature(f))
        self.assertEqual(list(result.parameters), ['a'])

    def test_strip_first_argument_raises_on_empty(self):
        import inspect
        from appeal import strip_first_argument_from_signature, ConfigurationError
        def f(): pass
        with self.assertRaises(ConfigurationError):
            strip_first_argument_from_signature(inspect.signature(f))

    def test_strip_self_strips_when_first_is_self(self):
        import inspect
        from appeal import strip_self_from_signature
        def f(self, a, b): pass
        result = strip_self_from_signature(inspect.signature(f))
        self.assertEqual(list(result.parameters), ['a', 'b'])

    def test_strip_self_leaves_signature_alone_when_first_is_not_self(self):
        import inspect
        from appeal import strip_self_from_signature
        def f(other, a): pass
        sig = inspect.signature(f)
        result = strip_self_from_signature(sig)
        self.assertEqual(list(result.parameters), ['other', 'a'])

    def test_strip_self_handles_empty_signature(self):
        import inspect
        from appeal import strip_self_from_signature
        def f(): pass
        sig = inspect.signature(f)
        result = strip_self_from_signature(sig)
        self.assertEqual(list(result.parameters), [])


class SubcommandTests(AppealTestsBase):
    #
    # Coverage for Appeal's command-group features: subcommands attached via
    # @app.command("parent").command(), global_command, and default_command.
    #

    def test_subcommand_runs_after_parent(self):
        app = appeal.Appeal(version="0.5")
        calls = []
        @app.command()
        def db(*, host='localhost'):
            calls.append(('db', host))
        # (the chained @app.command("db").command() spelling is
        # PEP 614 syntax--3.9+; the assign-then-decorate spelling
        # parses everywhere)
        db_registrar = app.command("db")
        @db_registrar.command()
        def deploy(version:int):
            calls.append(('deploy', version))
        app.process(shlex.split("db deploy 5")).result
        self.assertEqual(calls, [('db', 'localhost'), ('deploy', 5)])

    def test_subcommand_parent_option_propagates(self):
        app = appeal.Appeal(version="0.5")
        calls = []
        @app.command()
        def db(*, host='localhost'):
            calls.append(('db', host))
        # (the chained @app.command("db").command() spelling is
        # PEP 614 syntax--3.9+; the assign-then-decorate spelling
        # parses everywhere)
        db_registrar = app.command("db")
        @db_registrar.command()
        def deploy(version:int):
            calls.append(('deploy', version))
        app.process(shlex.split("db --host prod deploy 7")).result
        self.assertEqual(calls, [('db', 'prod'), ('deploy', 7)])

    def test_parent_command_without_subcommand_runs(self):
        # 1.0 ruling (no-pure-dispatcher): having subcommands does NOT force the
        # user to pick one -- a parent has its own body and runs it.  result is
        # the last command that ran.
        app = appeal.Appeal(version="0.5")
        @app.command()
        def db(*, host='localhost'):
            return host
        db_registrar = app.command("db")    # PEP 614: chaining
        @db_registrar.command()             # needs 3.9
        def deploy(version:int):
            return version
        self.assertEqual(app.process(shlex.split("db")).result, 'localhost')
        self.assertEqual(app.process(shlex.split("db --host prod")).result, 'prod')
        self.assertEqual(app.process(shlex.split("db deploy 5")).result, 5)

    def test_global_command_runs_before_command(self):
        app = appeal.Appeal(version="0.5")
        calls = []
        @app.global_command()
        def setup(*, verbose=False):
            calls.append(('global', verbose))
        @app.command()
        def go():
            calls.append(('go',))
        app.process(shlex.split("--verbose go")).result
        self.assertEqual(calls, [('global', True), ('go',)])

    def test_default_command_invoked_with_no_args(self):
        app = appeal.Appeal(version="0.5")
        calls = []
        @app.command()
        def a():
            calls.append(('a',))
        @app.command()
        def b():
            calls.append(('b',))
        @app.default_command()
        def default():
            calls.append(('default',))
        app.process(shlex.split("")).result
        self.assertEqual(calls, [('default',)])

    def test_default_command_not_invoked_when_command_specified(self):
        app = appeal.Appeal(version="0.5")
        calls = []
        @app.command()
        def a():
            calls.append(('a',))
        @app.default_command()
        def default():
            calls.append(('default',))
        app.process(shlex.split("a")).result
        self.assertEqual(calls, [('a',)])


class OptionsThatMapOptionsTests(AppealTestsBase):
    #
    # The recursive-converter feature where an option's annotation introduces
    # *new* options that exist only inside that option's scope.  Documented
    # in the README as "Options that map other options".  Exercises the
    # hierarchical Options stack and the scope-pop logic.
    #

    def test_parent_option_alone_uses_inner_default(self):
        # --color red (no --brightness) -- inner converter's brightness
        # parameter takes its default, here None.
        app = appeal.Appeal(version="0.5")
        def color(name, *, brightness:int=None):
            return (name, brightness)
        @app.command()
        def paint(*, color:color=None):
            return color
        self.assertEqual(
            app.process(shlex.split("paint --color red")).result,
            ('red', None),
            )

    def test_child_option_active_inside_parent_scope(self):
        # --color red --brightness 50 -- brightness is in scope because
        # --color introduced it; the value reaches the inner converter.
        app = appeal.Appeal(version="0.5")
        def color(name, *, brightness:int=None):
            return (name, brightness)
        @app.command()
        def paint(*, color:color=None):
            return color
        self.assertEqual(
            app.process(shlex.split("paint --color red --brightness 50")).result,
            ('red', 50),
            )

    def test_child_option_unknown_outside_parent_scope(self):
        # Using --brightness without first using --color: the brightness
        # option is never registered, so it's reported as unknown.
        app = appeal.Appeal(version="0.5")
        def color(name, *, brightness:int=None):
            return (name, brightness)
        @app.command()
        def paint(*, color:color=None):
            return color
        with self.assertRaises(appeal.AppealUsageError) as cm:
            app.process(shlex.split("paint --brightness 50")).result
        self.assertIn("--brightness", str(cm.exception))


if __name__ == "__main__":
    # Run everything (TestCase classes and any plain test_* functions)
    # through big.test.run, which returns control here instead of
    # exiting (so the README-completeness check below still runs).
    #
    # This is what fixes a real bug: the bottom of this file *used* to be
    #     if __name__ == "__main__":
    #         unittest.main()      # <- runs, then sys.exit()s the process
    #     ...README-completeness check...
    # The first unittest.main() exited before the README check ever ran,
    # so the check was silently dead.  Now the check runs again.
    total, failures = test.run(name='appeal v1 corpus')

    not_run = []
    for section, tests in readme_tests.items():
        for i, (text, counter) in enumerate(tests):
            if counter == 0:
                not_run.append((section, i))
    if not_run:
        print()
        print("The following README.md tests weren't run:")
        for section, i in not_run:
            print(f"  {section!r} #{i}")
        failures += len(not_run)

    sys.exit(1 if failures else 0)
