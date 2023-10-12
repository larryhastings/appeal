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

    def captured_print(*a, end="\n", sep=" "):
        t = sep.join([str(o) for o in a])
        t += end
        text.append(t)

    def start():
        builtins.print = captured_print
        return captured_print

    def end():
        builtins.print = actual_print
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


def preload_local_appeal():
    """
    Pre-load the local "appeal" module, to preclude finding
    an already-installed one on the path.
    """
    from os.path import abspath, dirname, isfile, join, normpath
    import sys
    appeal_dir = abspath(dirname(sys.argv[0]))
    while True:
        appeal_init = join(appeal_dir, "appeal/__init__.py")
        if isfile(appeal_init):
            break
        appeal_dir = normpath(join(appeal_dir, ".."))
    sys.path.insert(1, appeal_dir)
    import appeal
    return appeal_dir

appeal_dir = preload_local_appeal()
import appeal



app = command = process = None

def capture_stdout(cmdline):
    start, captured_print, end = make_stdout_capture()
    start()
    process(shlex.split(cmdline))
    return end()




def int_float_verbose(x_int:int, y_float:float, *, verbose=False):
    """
    Pointless demonstration converter.

    [[arguments]]
    {x_int} A pointless int.
    {y_float} A pointless float.
    [[end]]

    [[options]]
    {verbose} Allows control of
      the int float pair's verbosity.

      Example:
          for a in code:
              print(example(a))
       Hopefully this all survives.
    [[end]]
    """
    return (int_float_verbose, x_int, y_float, "verbose" if verbose else "silent")

def gloopfn(gloopstr, *, intfloat:int_float_verbose="(default value for intfloat)"):
    """
    [[arguments]]
    {gloopstr} A pointless string, we don't even care about it.
    [[end]]

    [[options]]
    {intfloat} An optional pair of int float, with verbosity.
    [[end]]

    """
    return (gloopfn, gloopstr, intfloat)

def test(str1, str2, optional_int=0, *, gloop:gloopfn=("(default value for gloop)")):
    """
    Simple test command function.

    Does this and that.  Actually just prints its arguments.

    Arguments:

    [[arguments]]
    {str1}  A string!
    {str2} It's another string.
      Who knows why we add these things.
      I sure don't.
      Look, I'm writing these docs but I have no jokes.
    {optional_int}
      An optional integer that fills your heart with joy.
    [[end]]

    Options:

    [[options]]
    {gloop} Does kind of a grab-bag of things.  {gloopfn.gloopstr} gets
       a string but nobody's sure why.
    [[end]]

    More text goes down here.  This should be in a fifth section.

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

def skittles(s, *, define:appeal.mapping[int,str,float]={}):
    return (skittles, s, define)

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

def verbosity(*, verbose:appeal.counter(max=9, step=2)=0):
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
            usage_max_columns=80,
            usage_indent_definitions=2,
            # positional_argument_usage_format="<{name.upper()}>",
            version="0.5",
            )
        command = app.command()
        def my_process(args):
            return app.process(args)
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

    def test_test_usage(self):
        command(test)
        text = capture_stdout('help test')
        self.assertIn("Simple test command function.", text)
        self.assertIn("test [-g|--gloop [-i|--intfloat [-v|--verbose] x_int y_float] gloopstr] str1 str2 [optional_int]", text)
        self.assertIn("A string!", text)
        self.assertIn("grab-bag", text)
        self.assertIn("for a in code:", text)
        self.assertIn("fifth section.", text)

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
        command(test)
        self.assert_process_raises(
            "test -g gloopy abc def -i 1 3.0 -v 336",
            appeal.AppealUsageError,
            )

    def test_simple_defaults_1(self):
        command(simple_defaults)
        self.assert_process(
            "simple_defaults",
            (simple_defaults, 0, ''),
            )

    def test_simple_defaults_2(self):
        command(simple_defaults)
        self.assert_process(
            "simple_defaults 5",
            (simple_defaults, 5, ''),
            )

    def test_simple_defaults_3(self):
        command(simple_defaults)
        self.assert_process(
            "simple_defaults 33 abc",
            (simple_defaults, 33, 'abc'),
            )

    def test_simple_defaults_4(self):
        command(simple_defaults)
        self.assert_process_raises(
            "simple_defaults 3.14159",
            appeal.AppealUsageError,
            )

    def test_simple_defaults_5(self):
        command(simple_defaults)
        self.assert_process_raises(
            "simple_defaults 33 abc xxx",
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
            'pool -d 1 x -d 2 y "bath salts" -d xya z',
            (pool, "bath salts", {'1': 'x', '2': 'y', 'xya': 'z'}),
            )


    def test_snooker_1(self):
        command(snooker)
        self.assert_process(
            'snooker -d 1 e -d 2 f "part of the body" -d 3 g',
            (snooker, "part of the body", {1: 'e', 2: 'f', 3: 'g'}),
            )


    def test_skittles_1(self):
        command(skittles)
        self.assert_process(
            'skittles -d 1 e 3.3 -d 2 f 4.4 "part of the body" -d 3 g 5.5',
            (skittles, "part of the body", {1: ('e', 3.3), 2: ('f', 4.4), 3: ('g', 5.5)}),
            )


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
        self.bind_go2()
        self.assert_process_raises(
            "go2 --north --south",
            appeal.AppealUsageError,
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


    def test_boolpos_1(self):
        command(boolpos)
        self.assert_process(
            'boolpos x',
            (boolpos, True),
            )

    def test_boolpos_2(self):
        command(boolpos)
        self.assert_process(
            'boolpos 0',
            (boolpos, True),
            )

    def test_boolpos_3(self):
        command(boolpos)
        self.assert_process(
            'boolpos ""',
            (boolpos, False),
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
        command(invalid_logging)
        self.assert_process_raises(
            "invalid_logging -v",
            appeal.AppealConfigurationError,
            )

    def test_invalid_annotation_1_1(self):
        command(invalid_annotation_1)
        self.assert_process_raises(
            "invalid_annotation_1 a",
            appeal.AppealConfigurationError,
            )

    def test_invalid_annotation_2_1(self):
        command(invalid_annotation_2)
        self.assert_process_raises(
            "invalid_annotation_2 -a",
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
        self.bind_two_or_more_files()
        text = capture_stdout('help two_or_more_files')
        self.assertIn("file file [file]...", text)

    def test_two_or_more_files_1(self):
        self.bind_two_or_more_files()
        self.assert_process(
            'two_or_more_files a b ',
            (two_or_more_files, 'a', 'b', (),),
            )

    def test_two_or_more_files_2(self):
        self.bind_two_or_more_files()
        self.assert_process(
            'two_or_more_files a b c d e f g',
            (two_or_more_files, 'a', 'b', ('c', 'd', 'e', 'f', 'g'),),
            )


    def test_set_path_1(self):
        command(set_path)
        self.assert_process(
            'set_path a',
            (set_path, ['a']),
            )

    def test_set_path_2(self):
        command(set_path)
        self.assert_process(
            'set_path a:b:c',
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
            'inferred_list x',
            (inferred_list, 'x', [0, 0.0]),
            )

    def test_inferred_list_2(self):
        command(inferred_list)
        self.assert_process(
            'inferred_list y 1 2.4',
            (inferred_list, 'y', [1, 2.4]),
            )

    def test_inferred_list_3(self):
        command(inferred_list)
        self.assert_process(
            'inferred_list z 2 4',
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
            'hey_argparse_watch_this',
            (hey_argparse_watch_this, None, None, None),
            )

    def test_hey_argparse_watch_this_2(self):
        command(hey_argparse_watch_this)
        self.assert_process(
            'hey_argparse_watch_this -i 0 -f 0 -c 0j',
            (hey_argparse_watch_this, 0, 0.0, 0j),
            )

    def test_hey_argparse_watch_this_3(self):
        command(hey_argparse_watch_this)
        self.assert_process(
            'hey_argparse_watch_this -i 3 -f 4 -c 5',
            (hey_argparse_watch_this, 3, 4.0, (5+0j)),
            )

    def test_hey_argparse_watch_this_4(self):
        command(hey_argparse_watch_this)
        self.assert_process(
            'hey_argparse_watch_this -i -1 -f -2 -c -3j',
            (hey_argparse_watch_this, -1, -2.0, -3j),
            )

    def test_hey_argparse_watch_this_5(self):
        command(hey_argparse_watch_this)
        self.assert_process(
            'hey_argparse_watch_this -f inf',
            (hey_argparse_watch_this, None, float("inf"), None),
            )

    def test_hey_argparse_watch_this_6(self):
        command(hey_argparse_watch_this)
        self.assert_process(
            'hey_argparse_watch_this -f -inf',
            (hey_argparse_watch_this, None, float("-inf"), None),
            )

    def test_hey_argparse_watch_this_7(self):
        command(hey_argparse_watch_this)
        # can't use assert_process, that uses assertEqual,
        # and nan is never equal to nan.
        result = list(process(shlex.split('hey_argparse_watch_this -f -nan')))
        our_nan = result[2]
        self.assertTrue(math.isnan(our_nan))
        result[2] = "was_nan"
        self.assertEqual(result, [hey_argparse_watch_this, None, "was_nan", None])


    def test_options_stack_1(self):
        command(options_stack)
        self.assert_process(
            'options_stack',
            (options_stack, 'abc', False, False, (nested_option, False, False, (inner_option, False, False)))
            )

    def test_options_stack_2(self):
        command(options_stack)
        self.assert_process(
            'options_stack --option --nested',
            (options_stack, 'abc', False, False, (nested_option, False, False, (inner_option, False, False)))
            )

    def test_options_stack_3(self):
        command(options_stack)
        self.assert_process(
            'options_stack --option --nested -eca',
            (options_stack, 'abc', True, False, (nested_option, True, False, (inner_option, True, False)))
            )

    def test_options_stack_4(self):
        command(options_stack)
        self.assert_process_raises(
            'options_stack --option --nested -ace',
            # (options_stack, 'abc', True, False, (nested_option, True, False, (inner_option, True, False)))
            appeal.AppealUsageError,
            )

    def test_options_stack_5(self):
        command(options_stack)
        self.assert_process_raises(
            'options_stack --option --nested -ace -b',
            # (options_stack, 'abc', True, True, (nested_option, True, False, (inner_option, True, False)))
            appeal.AppealUsageError,
            )

    def test_options_stack_6(self):
        command(options_stack)
        self.assert_process_raises(
            'options_stack --option --nested -ace -bdf',
            appeal.AppealUsageError,
            )

    def test_options_stack_7(self):
        command(options_stack)
        self.assert_process_raises(
            'options_stack --option --nested -a -e',
            appeal.AppealUsageError,
            )

    def test_options_stack_8(self):
        command(options_stack)
        self.assert_process_raises(
            'options_stack --option --nested -a -c',
            appeal.AppealUsageError,
            )

    def test_options_stack_9(self):
        command(options_stack)
        self.assert_process_raises(
            'options_stack --option --nested -a -c',
            appeal.AppealUsageError,
            )


    def test_five_level_stack_1(self):
        command(five_level_stack)
        self.assert_process(
            "five_level_stack",
            (five_level_stack,  None, False, False),
            )

    def test_five_level_stack_2(self):
        command(five_level_stack)
        self.assert_process(
            "five_level_stack -a -d",
            (five_level_stack, (five_a, (five_d, None, False, False), False, False), False, False),
            )

    def test_five_level_stack_3(self):
        command(five_level_stack)
        self.assert_process(
            "five_level_stack -a -d -g -j -m",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), False, False), False, False), False, False), False, False),
            )

    def test_five_level_stack_4(self):
        command(five_level_stack)
        self.assert_process(
            "five_level_stack -a -d -g -j -m -e",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), False, False), False, False), True, False), False, False),
            )

    def test_five_level_stack_5(self):
        command(five_level_stack)
        self.assert_process(
            "five_level_stack -a -d -g -j -me",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), False, False), False, False), True, False), False, False),
            )

    def test_five_level_stack_6(self):
        command(five_level_stack)
        self.assert_process_raises(
            "five_level_stack -a -d -g -j -me -n",
            appeal.AppealUsageError,
            )

    def test_five_level_stack_7(self):
        command(five_level_stack)
        self.assert_process(
            "five_level_stack -a -d -g -j -me",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), False, False), False, False), True, False), False, False),
            )

    def test_five_level_stack_8(self):
        command(five_level_stack)
        self.assert_process(
            "five_level_stack -a -d -g -j -mh -i",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), False, False), False, False), True, True), False, False), False, False),
            )


    def test_five_level_stack_9(self):
        command(five_level_stack)
        self.assert_process_raises(
            "five_level_stack -a -d -g -j -mh -k",
            appeal.AppealUsageError,
            )

    def test_five_level_stack_10(self):
        command(five_level_stack)
        self.assert_process_raises(
            "five_level_stack -a -d -g -j -mh -n",
            appeal.AppealUsageError,
            )

    def test_five_level_stack_11(self):
        command(five_level_stack)
        self.maxDiff=None
        self.assert_process(
            "five_level_stack -a -d -g -j -mn -o",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, False, False, False), True, True), False, False), False, False), False, False), False, False),
            )

    def test_five_level_stack_12(self):
        command(five_level_stack)
        self.assert_process_raises(
            "five_level_stack -a -d -g -j -mb -e",
            appeal.AppealUsageError,
            )

    def test_five_level_stack_13(self):
        command(five_level_stack)
        self.assert_process_raises(
            "five_level_stack -a -d -g -j -mb -h",
            appeal.AppealUsageError,
            )

    def test_five_level_stack_14(self):
        command(five_level_stack)
        self.assert_process_raises(
            "five_level_stack -a -d -g -j -mb -k",
            appeal.AppealUsageError,
            )

    def test_five_level_stack_15(self):
        command(five_level_stack)
        self.assert_process_raises(
            "five_level_stack -a -d -g -j -mb -n",
            appeal.AppealUsageError,
            )

    def test_five_level_stack_16(self):
        command(five_level_stack)
        self.maxDiff=None
        self.assert_process_raises(
            "five_level_stack -a -d -g -j -m -behknp",
            # (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, True, False, False), True, False), True, False), True, False), True, False), True, False),
            appeal.AppealUsageError,
            )


    def test_mixed_groups_1(self):
        command(multiple_groups)
        self.assert_process(
            "multiple_groups abc",
            (multiple_groups,
                'abc',
                (multiple_groups_child, None, None, None, False, (0, 0.0, False)),
                (multiple_groups_child, None, None, None, False, (0, 0.0, False)),
                ''
                ),
            )

    def test_mixed_groups_2(self):
        command(multiple_groups)
        self.assert_process_raises(
            "multiple_groups abc def",
            appeal.AppealUsageError,
            )

    def test_mixed_groups_3(self):
        command(multiple_groups)
        self.assert_process_raises(
            "multiple_groups abc -v",
            appeal.AppealUsageError,
            )

    def test_mixed_groups_4(self):
        command(multiple_groups)
        self.assert_process(
            "multiple_groups a ba bb bc ca cb cc",
            (multiple_groups,
                'a',
                (multiple_groups_child, 'ba', 'bb', 'bc', False, (0, 0.0, False)),
                (multiple_groups_child, 'ca', 'cb', 'cc', False, (0, 0.0, False)),
                ''
                ),
            )

    def test_mixed_groups_5(self):
        command(multiple_groups)
        self.assert_process_raises(
            "multiple_groups -f a ba bb bc ca cb cc",
            appeal.AppealUsageError,
            )

    def test_mixed_groups_6(self):
        command(multiple_groups)
        self.assert_process(
            "multiple_groups a -f ba bb bc ca cb cc",
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
            "multiple_groups a ba -f bb bc ca cb cc",
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
            "multiple_groups a ba bb -f bc ca cb cc",
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
            "multiple_groups a ba bb bc -f ca cb cc",
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
            "multiple_groups a ba bb bc ca -f cb cc",
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
            "multiple_groups a ba bb bc ca cb -f cc",
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
            "multiple_groups a ba bb bc ca cb cc -f",
            (multiple_groups,
                'a',
                (multiple_groups_child, 'ba', 'bb', 'bc', False, (0, 0.0, False)),
                (multiple_groups_child, 'ca', 'cb', 'cc', True, (0, 0.0, False)),
                ''
                ),
            )


    def test_str_i_f_1(self):
        command(str_i_f)
        e = self.assert_process_raises(
            "str_i_f abc 1",
            appeal.AppealUsageError,
            )
        self.assertEqual(str(e), "str_i_f requires 2 arguments in this argument group.")

    def test_str_i_f_2(self):
        command(str_i_f)
        # regression test:
        # when printing usage, we used to print the name of the last program
        # we'd called, regardless of whether or not it was currently running.
        # so this error used to read
        #   str_i_f, -v, --verbose requires 2 arguments in this argument group.
        e = self.assert_process_raises(
            "str_i_f abc 1 --verbose",
            appeal.AppealUsageError,
            )
        self.assertEqual(str(e), "str_i_f requires 2 arguments in this argument group.")

    def test_str_i_f_1(self):
        command(str_i_f)
        e = self.assert_process_raises(
            "str_i_f abc 1 --option x",
            appeal.AppealUsageError,
            )
        self.assertEqual(str(e), "str_i_f requires 2 arguments in this argument group.")

    def test_str_i_f_4(self):
        command(str_i_f)
        e = self.assert_process_raises(
            "str_i_f abc 1 --option",
            appeal.AppealUsageError,
            )
        self.assertEqual(str(e), "str_i_f -o | --option requires 1 argument in this argument group.")


    def test_app_class(self):
        app = appeal.Appeal()
        app_class, command_method = app.app_class()

        instances = []
        @app_class()
        class MyApp:
            def __init__(self, *, verbose=False):
                self.verbose = verbose
                self.pattern = None
                self.filename = None
                self.context = None
                instances.append(self)

            @command_method()
            def fgrep(self, pattern, filename, *, context=0):
                self.pattern = pattern
                self.filename = filename
                self.context = context

            def dump(self):
                return self.verbose, self.pattern, self.filename, self.context

        result = app.process(shlex.split("-v fgrep patt -c 33 file"))
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

            def render(self):
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
        import sys
        import os.path
        app = appeal.Appeal()

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
        if 0:
            assertIn = self.assertIn
        else:
            def assertIn(needle, haystack):
                assert needle in haystack, f"{needle!r} not in {haystack!r}"

        assertIn(f"usage: {os.path.basename(sys.argv[0])} command", text)
        assertIn("Commands:", text)
        assertIn("nuttall", text)
        assertIn("Demo function, first line.", text)


##
## I got tired of the examples in README.md not working
## or being out of sync with the implementation.
## So now I ensure the examples in README.md are always
## working--because I run them.
##
## Here we parse README.md, pull out just the example tests,
## compile and execute, then run at least one test on each.
##
## Executable tests in README.md are always indented from
## the left margin, are per-documentation-section,
## and always take this form:
##
##     import appeal
##     ...
##     app.main([...]
##
## (As in, they start with a line that reads "import appeal",
## and ends with a line that starts with "app.main(".)
##
## They're stored in readme_tests, a dict mapping
## "section name" to a list of tests from that section.
## (Sections are lines in README.md that start with '## '.)
## Since there are sometimes multiple tests in the same
## section, they're stored in order of appearance.
##
## If you change the tests in README.md, you'll probably
## break the test suite!  But this is better than README.md
## bit-rotting.
##

readme = os.path.normpath(os.path.join(appeal_dir, "README.md"))
with open(readme, "rt") as f:
    lines = f.read()

in_code = False
readme_tests = collections.defaultdict(list)
readme_test = []
section = None

for line in lines.split("\n"):
    stripped = line.lstrip()
    if stripped == line:
        if line.startswith("##"):
            while line.startswith("#"):
                line = line[1:]
            section = line.strip()
            # if we had a malformed test, throw it away here
            readme_test.clear()
            continue
    stripped = stripped.rstrip()
    if stripped == "import appeal":
        readme_test.clear()
        readme_test.append(line)
    elif readme_test:
        if stripped.startswith(("app.main(", "p.main(")):
            # don't actually append the app.main(), we don't want it
            # but add a correctly-indented 'pass' so it still parses
            prefix = line.partition(stripped)[0]
            pass_line = prefix + "pass"
            readme_test.append(pass_line)

            t = "\n".join(readme_test)
            t = textwrap.dedent(t)
            readme_tests[section].append([t, 0])
            readme_test = []
            # print("[test]", section, len(readme_tests[section]), repr(t)[:30] + "[...]")
        else:
            readme_test.append(line)

# for line in lines.split("\n"):
#     if line.startswith("##"):
#         section = line.partition(' ')[2].strip()
#         continue
#     stripped = line.strip()
#     if stripped.startswith("```"):
#         in_code = not in_code
#     if in_code and (not readme_test) and (stripped == "import appeal"):
#         readme_test.append(line)
#     elif readme_test:
#         if stripped == "app.main()":
#             # don't append line, we don't want it anyway
#             # in fact, we probably want to lose the previous line too
#             if readme_test[-1] == 'if __name__ == "__main__":':
#                 readme_test.pop()
#             t = "\n".join(readme_test)
#             t = textwrap.dedent(t)
#             readme_tests[section].append([t, 0])
#             readme_test = []
#         else:
#             readme_test.append(line)

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
            nonlocal p
            if not p:
                p = app.processor()
            p(args)
            return p
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
            "fgrep xy" f"zzy '{appeal_dir}/tests/test_all.py'",
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
            "fgrep -p=weightless",
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
            "fgrep -p=8",
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
            "fgrep --pattern 2 -p 4 --pattern=6 -p=8 -p 10",
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
            "inception -o=1965 -v",
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
            "mixin -l=elective",
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

    def test_classes_instances_and_preparers_0_1(self):
        self.exec_readme(
            'Classes, Instances, And Preparers',
            0,
            "add f g h",
            "MyApp init verbose=False\nMyApp add self=<MyApp> a='f' b='g' c='h' self.verbose=False",
            )

    def test_classes_instances_and_preparers_0_2(self):
        self.exec_readme(
            'Classes, Instances, And Preparers',
            0,
            "-v add f g h",
            "MyApp init verbose=True\nMyApp add self=<MyApp> a='f' b='g' c='h' self.verbose=True",
            )

    def test_classes_instances_and_preparers_1_1(self):
        self.exec_readme(
            'Classes, Instances, And Preparers',
            1,
            "add f g h",
            "MyApp add self=<MyApp id='dingus'> a='f' b='g' c='h'",
            )


exit_code = 0
try:
    unittest.main()
# oh no you don't!
except SystemExit as e:
    exit_code = e

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
    exit_code = -1

sys.exit(exit_code)
