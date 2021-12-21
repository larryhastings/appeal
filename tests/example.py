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

import os
import sys

appeal_root = os.environ.get("APPEAL_ROOT")
if appeal_root:
    # print("insertin'", appeal_root)
    sys.path.insert(0, appeal_root)

import appeal
import math


app = appeal.Appeal(
    usage_max_columns=80,
    usage_indent_definitions=2,
    # positional_argument_usage_format="<{name.upper()}>",
    version="0.5",
    )

command = app.command
argument = app.argument
option = app.option

# app.log_event("populate commands")

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
    return (x_int, y_float, "verbose" if verbose else "silent")

def gloopfn(gloopstr, *, intfloat:int_float_verbose="(default value for intfloat)"):
    """
    [[arguments]]
    {gloopstr} A pointless string, we don't even care about it.
    [[end]]

    [[options]]
    {intfloat} An optional pair of int float, with verbosity.
    [[end]]

    """
    return (gloopstr, intfloat)

@command()
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

    print(f"{str1=} {str2=} {optional_int=} {gloop=}")

@command()
@option("imaginary", "-i", "--imaginary")
def rip(s, a:int_float_verbose, b:int_float_verbose="(b default)", c:int_float_verbose="(c default)", **kwargs:float):
    print(f"rip: {s=} {a=} {b=} {c=} {kwargs=}")

@command()
def tear(s, *, verbose:appeal.counter()=0):
    print(f"tear: {s=} {verbose=}")

@command()
def foosball(s, *, define:appeal.accumulator=[]):
    print(f"foosball: {s=} {define=}")

@command()
def soccer(s, *, define:appeal.accumulator[int, str]=[]):
    print(f"soccer: {s=} {define=}")

@command()
def pool(s, *, define:appeal.mapping={}):
    print(f"pool: {s=} {define=}")

@command()
def snooker(s, *, define:appeal.mapping[int,str]={}):
    print(f"snooker: {s=} {define=}")

@command()
def go(direction:appeal.validate("north", "south", "east", "west")):
    print(f"go {direction} young man!")

def north(): return 'north'
def south(): return 'south'
def east(): return 'east'
def west(): return 'west'

@command()
@option("direction", "--north", annotation=north)
@option("direction", "--south", annotation=south)
@option("direction", "--east", annotation=east)
@option("direction", "--west", annotation=west)
def go2(*, direction='north'):
    print(f"go2 {direction}")

@command()
def pick30(number:appeal.validate_range(30)):
    print(f"pick30 {number}")

@command()
def pick60(number:appeal.validate_range(-30, 30)):
    print(f"pick60 {number}")

@command()
def verbosity(*, verbose:appeal.counter(max=5, step=2)=0):
    print(f"verbosity {verbose=}")

@command()
def boolpos(v:bool):
    print(f"boolpos {v=}")


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

@command()
def eric(l:logging, *args):
    print(f"eric {l=} {args=}")


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

@command()
def eric2(l:Logging, s='(default)'):
    print(f"eric2 {l=} {s=}")


# @command()
# def invalid_logging(l:Logging, l2:Logging):
#     print(f"invalid_logging {l=} {l2=}")


def jobs(jobs:int=math.inf):
    return jobs

@command()
def make(*targets, jobs:jobs=1):
    print(f"make {jobs=} {targets=}")

@command()
@argument("file2", usage="file")
@argument("files", usage="file")
def two_or_more_files(file, file2, *files):
    print(f"two_or_more_files {file} {file2} {files}")

@command()
def set_path(path:appeal.split(":")):
    print(f"set_path {path=}")


@command()
def fgrep(pattern, filename=None, *, color="", id=0, verbose=False):
    return (pattern, filename, color, id, verbose)


def weird_log_info(*, verbose=False, log_level='info'):
    return (verbose, log_level)

@command()
def weird(log:weird_log_info):
    print(f"weird {log}")

def witness1_my_converter(a: int, *, verbose=False):
    return [a, verbose]
@app.command()
def witness1(*args:witness1_my_converter):
    print(f"witness1 {args=}")


@app.command()
def charm_torture_test(a:int, l:Logging):
    print(f"charm_torture_test {a=} {l=}")


def nested_inner(c:int):
    return c

def nested_outer(b:nested_inner):
    return b

@app.command()
def charm_undo_test(a:nested_outer=0):
    print(f"charm_undo_test {a=}")

@app.command()
def number(*, i:int=0):
    print(f"number {i=}")



app.main()
