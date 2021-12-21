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


should_fail = """

    example.py go2 --north --south

"""

should_succeed = """

    example.py help test
    example.py test -g gloopy abc def
    example.py test -g gloopy -i 1 3.0 -v abc def 336
    example.py test abc -g gloopy -i 1 3.0 -v def 336
    example.py test abc def -g gloopy -i 1 3.0 -v 336
    example.py test abc def 336 -g gloopy -i 1 3.0 -v
    example.py rip abc 1 2 3 4 5 6
    example.py rip abc 1 2 -v
    example.py rip abc 1 2 3 4 5 6 -v
    example.py rip abc 1 2 -v 3 4 5 6 -v
    example.py tear 3 -v -v -v
    example.py foosball xyz -d a -d b -d c
    example.py soccer abc -d 1 abc -d 2 xyz
    example.py pool -d a b duh  -d x z
    example.py snooker -d 1 x -d 2 y "bath salts" -d 3 z
    example.py go north
    example.py go south
    example.py go east
    example.py go west
    example.py go2 --north
    example.py go2 --south
    example.py go2 --east
    example.py go2 --west
    example.py pick30 5
    example.py pick30 25
    example.py pick60 22
    example.py pick60 -- -22
    example.py verbosity
    example.py verbosity -v
    example.py verbosity -v -v
    example.py verbosity -v -v
    example.py verbosity -v -v -v
    example.py verbosity -v -v -v -v
    example.py boolpos 3
    example.py boolpos 0
    example.py boolpos ''
    example.py eric
    example.py eric -v -v -v --log-dest stdout -v
    example.py eric -v -v --log-level error -v
    example.py eric -v -v --log-level error -v --verbose -v -v --log-dest stdout -v
    example.py eric2
    example.py eric2 -v -v -v --log-dest stdout -v
    example.py eric2 -v -v --log-level error -v
    example.py eric2 -v -v --log-level error -v --verbose -v -v --log-dest stdout -v greedo
    example.py make
    example.py make target1 target2
    example.py make -j 5
    example.py make -j
    example.py make target1 target2 target3 -j
    example.py two_or_more_files a b
    example.py two_or_more_files a b c d e f g
    example.py set_path a
    example.py set_path a:b:c
    example.py weird -v
    example.py weird --log-level mickey
    example.py weird -v --log-level goofy
    example.py weird --log-level donald -v

"""

###############################################################################
###############################################################################
###############################################################################
###############################################################################


import os.path
import shlex
import subprocess
import sys

seps = set((os.path.sep,))
if os.path.altsep:
    seps.add(os.path.altsep)

# find root of repo
while os.getcwd()[-1] not in seps:
    # print(os.getcwd())
    if ".git" in os.listdir():
        break
    os.chdir("..")

assert "appeal" in os.listdir(), "You need to run run_tests.py from inside the Appeal source tree."
appeal_root = os.getcwd()

# now chdir into tests directory
os.chdir("tests")

# and tell the tests to add the local version of appeal
# to the front of the path
os.environ['APPEAL_ROOT'] = appeal_root


# the cobbler's children have no shoes.
verbose = ("-v" in sys.argv) or ("--verbose" in sys.argv)


def yield_lines(s):
    for line in s.strip().split("\n"):
        yield line.strip()


test_count = 0

for name, expected in (
    ("should_fail", True),
    ("should_succeed", False),
    ):
    lines = eval(name)
    for line_number, line in enumerate(yield_lines(lines), 1):
        cmdline = ['python3']
        cmdline.extend(shlex.split(line))
        # cmdline[1] = "tests/" + cmdline[1]
        if verbose:
            print()
            print(cmdline)
        p = subprocess.run(cmdline, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        failed = bool(p.returncode) != expected
        if failed:
            print(f"Failed at {name!r} line {line_number}:")
            print("    " + line)
        if failed or verbose:
            print("Stdout + stderr:")
            print("----")
            sys.stdout.flush()
            os.write(sys.stdout.fileno(), p.stdout)
            os.write(sys.stdout.fileno(), p.stderr)
            print("----")
        if failed:
            sys.exit(-1)
        test_count += 1

print(f"All {test_count} tests passed.")
sys.exit(0)
