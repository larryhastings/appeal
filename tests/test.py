#!/usr/bin/env python3


# part of the Appeal software package
# Copyright 2021 by Larry Hastings
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


# Ensure that this script tests the local appeal source code
# (rather than the installed version of appeal, if any)
# regardless of where it is run from.

import os.path
import sys

script_dir = os.path.abspath(os.path.dirname(sys.argv[0]))
appeal_dir = os.path.join(script_dir, "..")
sys_path_0 = os.path.abspath(sys.path[0])
assert sys_path_0 == script_dir
sys.path.insert(1, appeal_dir)

import appeal
import builtins
import collections
import math
import os.path
import shlex
import sys
import textwrap
import unittest

app = command = process = None


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
    print(f"make {jobs=} {targets=}")

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

def capture_stdout(cmdline):
    text = []
    def print_capture(*a, end="\n", sep=" "):
        t = sep.join([str(o) for o in a])
        t += end
        text.append(t)
    actual_print = builtins.print
    builtins.print = print_capture
    process(shlex.split(cmdline))
    builtins.print = actual_print
    text = "".join(text)
    return text


class SmokeTests(unittest.TestCase):
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
        process = app.process

    def assert_process(self, cmdline, result):
        self.assertEqual(process(shlex.split(cmdline)), result)

    def assert_process_raises(self, cmdline, exception):
        with self.assertRaises(exception):
            process(shlex.split(cmdline))

    def tearDown(self):
        global app
        global command
        app = command = None

    def test_test_usage(self):
        command(test)
        text = capture_stdout('help test')
        self.assertIn("Simple test command function.", text)
        self.assertIn(" str1 ", text)
        self.assertIn("A string!", text)
        self.assertIn("-g|--gloop", text)
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
            'rip abc 1 2 -v',
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
            'rip scooby 1 -v 2 3 4 --verbose 5 6 -v',
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
        app.argument("file2", usage="file")(two_or_more_files)
        app.argument("files", usage="file")(two_or_more_files)

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
            'undo 3',
            (undo, 3),
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

    def test_options_stack_2(self):
        command(options_stack)
        self.assert_process(
            'options_stack --option --nested -eca',
            (options_stack, 'abc', True, False, (nested_option, True, False, (inner_option, True, False)))
            )

    def test_options_stack_3(self):
        command(options_stack)
        self.assert_process(
            'options_stack --option --nested -ace',
            (options_stack, 'abc', True, False, (nested_option, True, False, (inner_option, True, False)))
            )

    def test_options_stack_4(self):
        command(options_stack)
        self.assert_process(
            'options_stack --option --nested -ace -b',
            (options_stack, 'abc', True, True, (nested_option, True, False, (inner_option, True, False)))
            )

    def test_options_stack_5(self):
        command(options_stack)
        self.assert_process_raises(
            'options_stack --option --nested -ace -bdf',
            appeal.AppealUsageError,
            )

    def test_options_stack_6(self):
        command(options_stack)
        self.assert_process_raises(
            'options_stack --option --nested -a -e',
            appeal.AppealUsageError,
            )

    def test_options_stack_7(self):
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
        self.assert_process(
            "five_level_stack -a -d -g -j -m -behknp",
            (five_level_stack, (five_a, (five_d, (five_g, (five_j, (five_m, True, False, False), True, False), True, False), True, False), True, False), True, False),
            )


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
## the left margin, and always look like this:
##
##     import appeal
##     ...
##     app.main()
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

readme = os.path.normpath(os.path.join(sys.argv[0], "../../README.md"))
with open(readme, "rt") as f:
    lines = f.read()
readme_tests = collections.defaultdict(list)
readme_test = []
section = None
for line in lines.split("\n"):
    stripped = line.lstrip()
    if stripped == line:
        if line.startswith("##"):
            section = line[2:].strip()
        continue
    stripped = stripped.rstrip()
    if (not readme_test) and (stripped == "import appeal"):
        readme_test.append(line)
    elif readme_test:
        if stripped == "app.main()":
            # don't append line, we don't want it anyway
            t = "\n".join(readme_test)
            t = textwrap.dedent(t)
            readme_tests[section].append([t, 0])
            readme_test = []
        else:
            readme_test.append(line)

# print the tests
if "-v" in sys.argv:
    print()
    for section, tests in readme_tests.items():
        print(repr(section))
        for i, l in enumerate(tests):
            t, counter = l
            print(f"    [ #{i}")
            for line in t.split("\n"):
                print("   ", line)
            print("    ]")


class ReadmeTests(unittest.TestCase):

    def exec_readme(self, section, index, cmdline, expected):
        global app
        global process

        l = readme_tests[section][index]
        text, count = l
        code = compile(text, "-", "exec")
        globals = {}
        # print(section, index)
        # print(repr(text))
        result = exec(code, globals, globals)
        app = globals['app']
        process = app.process

        result = capture_stdout(cmdline)
        self.assertEqual(result.strip(), expected)
        app = process = None
        l[1] = count + 1


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
            'Our First Example',
            0,
            "fgrep WM_CREATE window.c",
            "fgrep WM_CREATE window.c",
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
            "fgrep WM_CREATE --color red a b c --id 33 -v",
            "fgrep WM_CREATE ('a', 'b', 'c') 'red' 33 True",
            )

    def test_options_opargs_and_kwonly_0_2(self):
        self.exec_readme(
            'Options, Opargs, And Keyword-Only Parameters',
            0,
            "fgrep poodle -v x --id 88 y --color blue z",
            "fgrep poodle ('x', 'y', 'z') 'blue' 88 True",
            )

    def test_options_opargs_and_kwonly_0_3(self):
        self.exec_readme(
            'Options, Opargs, And Keyword-Only Parameters',
            0,
            "fgrep poodle x --id 88 y --color blue z",
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
    #         print(f"fgrep {verbose=}")
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
    #         print(f"fgrep {pattern=}")
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
    #         print(f"fgrep {pattern=}")
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
            "fgrep -p8",
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
            "fgrep --pattern 2 -p 4 --pattern=6 -p=8 -p10",
            "fgrep pattern=[2, 4, 6, 8, 10]",
            )

    # 'Data Validation'
    #     [ #0
    #     import appeal
    #     app = appeal.Appeal()
    #     @app.command()
    #     def go(direction:appeal.validate('up', 'down', 'left', 'right', 'forward', 'back')):
    #         print(f"go {direction=}")
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
    #         print(f"go {direction=}")
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
    #         print(f"recurse {a=} {b=}")
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
    #       print(f"weird {a=} {b=}")
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
    #         print(f"inception {args=}")
    #
    #     app.main()
    #     ]
    def test_now_witness_the_power_of_this_etc_0_1(self):
        self.exec_readme(
            'Now Witness The Power Of This Fully Armed And Operational Battle Station',
            0,
            "inception",
            "inception option=[0, False]",
            )

    def test_now_witness_the_power_of_this_etc_0_2(self):
        self.exec_readme(
            'Now Witness The Power Of This Fully Armed And Operational Battle Station',
            0,
            "inception -o 33",
            "inception option=[33, False]",
            )

    def test_now_witness_the_power_of_this_etc_0_3(self):
        self.exec_readme(
            'Now Witness The Power Of This Fully Armed And Operational Battle Station',
            0,
            "inception -o1965 -v",
            "inception option=[1965, True]",
            )

    def test_now_witness_the_power_of_this_etc_0_4(self):
        self.exec_readme(
            'Now Witness The Power Of This Fully Armed And Operational Battle Station',
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
    #         print(f"weird {args=}")
    #     ]
    def test_now_witness_the_power_of_this_etc_1_1(self):
        self.exec_readme(
            'Now Witness The Power Of This Fully Armed And Operational Battle Station',
            1,
            "repetition",
            "repetition args=()",
            )

    def test_now_witness_the_power_of_this_etc_1_2(self):
        self.exec_readme(
            'Now Witness The Power Of This Fully Armed And Operational Battle Station',
            1,
            "repetition 0",
            "repetition args=([0, False],)",
            )

    def test_now_witness_the_power_of_this_etc_1_2(self):
        self.exec_readme(
            'Now Witness The Power Of This Fully Armed And Operational Battle Station',
            1,
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
    #         print(f"mixin {log=}")
    #
    #     app.main()
    #     ]
    def test_now_witness_the_power_of_this_etc_2_1(self):
        self.exec_readme(
            'Now Witness The Power Of This Fully Armed And Operational Battle Station',
            2,
            "mixin",
            "mixin log=<Logging verbose=False log_level=info>",
            )

    def test_now_witness_the_power_of_this_etc_2_2(self):
        self.exec_readme(
            'Now Witness The Power Of This Fully Armed And Operational Battle Station',
            2,
            "mixin --log-level ascerbic -v",
            "mixin log=<Logging verbose=True log_level=ascerbic>",
            )

    def test_now_witness_the_power_of_this_etc_2_3(self):
        self.exec_readme(
            'Now Witness The Power Of This Fully Armed And Operational Battle Station',
            2,
            "mixin -ldidactic  --verbose",
            "mixin log=<Logging verbose=True log_level=didactic>",
            )

    def test_now_witness_the_power_of_this_etc_2_4(self):
        self.exec_readme(
            'Now Witness The Power Of This Fully Armed And Operational Battle Station',
            2,
            "mixin -lelective",
            "mixin log=<Logging verbose=False log_level=elective>",
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
