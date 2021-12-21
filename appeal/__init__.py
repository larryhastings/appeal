#!/usr/bin/env python3

"A powerful & Pythonic command-line parsing library.  Give your program Appeal!"
__version__ = "0.5"


# please leave this copyright notice in binary distributions.
license = """
appeal/__init__.py
part of the Appeal software package
Copyright 2021 by Larry Hastings
All rights reserved.

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

want_prints = 1
want_prints = 0


from abc import abstractmethod, ABCMeta
import builtins
import collections
import enum
import functools
import inspect
import itertools
import math
import os.path
from os.path import basename
import pprint
import shlex
import string
import sys
import textwrap
import time
import types

from . import argument_grouping
from . import text

POSITIONAL_ONLY = inspect.Parameter.POSITIONAL_ONLY
POSITIONAL_OR_KEYWORD = inspect.Parameter.POSITIONAL_OR_KEYWORD
VAR_POSITIONAL = inspect.Parameter.VAR_POSITIONAL
KEYWORD_ONLY = inspect.Parameter.KEYWORD_ONLY
VAR_KEYWORD = inspect.Parameter.VAR_KEYWORD
empty = inspect.Parameter.empty



def multisplit(s, separators):
    """
    Like str.split(), but separators is an iterable
    of strings to separate on.  (separators can be a
    string, in which case multisplit separates on each
    character.)

    multsplit('ab:cd,ef', ':,') => ["ab", "cd", "ef"]
    """
    if not s or not separators:
        return [s]
    splits = []
    while s:
        candidates = []
        for separator in separators:
            split, found, trailing = s.partition(separator)
            if found:
                candidates.append((len(split), split, trailing))
        if not candidates:
            break
        candidates.sort()
        _, fragment, s = candidates[0]
        splits.append(fragment)
    splits.append(s)
    return splits


# which PushBackIterator do you want?

if 0:
    # totally legit high-performance version with racing stripes
    # where debugging is annoying
    class PushbackIterator:
        def __init__(self, iterable=None):
            if iterable:
                self.iterators = [iter(iterable)]
            else:
                self.iterators = []

        def __iter__(self):
            return self

        def __next__(self):
            if not self.iterators:
                raise StopIteration
            i = self.iterators[-1]
            try:
                return next(i)
            except StopIteration:
                self.iterators.pop()
                return self.__next__()

        def next(self, default=None):
            # like next(self), but safe.
            try:
                return next(self)
            except StopIteration:
                return default

        def push(self, o):
            self.iterators.append(iter((o,)))

        def __bool__(self):
            if not self.iterators:
                return False
            try:
                o = next(self)
                self.push(o)
                return True
            except StopIteration:
                return False

        def __repr__(self):
            return f"<{self.__class__.__name__} {len(self.iterators)} iterators>"

else:
    # debug-friendly low-performance version that makes kittens sad
    class PushbackIterator:
        def __init__(self, iterable=None):
            if iterable:
                self.values = collections.deque(iterable)
            else:
                self.values = collections.deque(iterable)

        def __iter__(self):
            return self

        def __next__(self):
            if not self.values:
                raise StopIteration
            return self.values.popleft()

        def next(self, default=None):
            # like next(self), but safe.
            if not self.values:
                return default
            return self.values.popleft()

        def push(self, o):
            self.values.appendleft(o)

        def __bool__(self):
            return bool(self.values)

        def __repr__(self):
            return f"<{self.__class__.__name__} {self.values}>"




class DictGetattrProxy:
    def __init__(self, d, repr_string):
        self.__d__ = d
        self.__repr_string__ = repr_string

    def __repr__(self):
        return self.__repr_string__

    def __getattr__(self, attr):
        return self.__d__.get(attr)


def parameter_name_to_short_option(s):
    assert s and isinstance(s, str)
    return f"-{s[0]}"

def parameter_name_to_long_option(s):
    assert s and isinstance(s, str)
    return f"--{s.lower().replace('_', '-')}"

##
## Options are stored internally in a "normalized" format.
##
##     * For long options, it's the full string (e.g. "--verbose").
##     * For short options, it's just the single character (e.g. "v").
##
## Why bother?  Normalizing them like this makes it lots easier
## to process short options that are glued together (e.g. "-avc").
##
def normalize_option(option):
    assert option and isinstance(option, str)
    assert len(option) != 1
    assert len(option) != 3
    assert option.startswith("-")
    if len(option) == 2:
        return option[1]
    assert option.startswith("--")
    return option

def denormalize_option(option):
    assert option and isinstance(option, str)
    if len(option) == 1:
        return "-" + option
    return option



class AppealBaseException(Exception):
    pass

class AppealConfigurationError(AppealBaseException):
    """
    Raised when the Appeal API is used improperly.
    """
    pass

class AppealUsageError(AppealBaseException):
    """
    Raised when Appeal processes an invalid command-line.
    """
    pass

class AppealCommandError(AppealBaseException):
    """
    Raised when an Appeal command function returns a
    result indicating an error.
    """
    pass



#
# used to ensure that the user doesn't use an uncalled
# converter creator
#
# e.g.
#
#    @app.command()
#    def my_command(a:appeal.split):
#        ...
#
# is wrong, the user must call appeal.split:
#
#    @app.command()
#    def my_command(a:appeal.split()):
#        ...
#
def must_be_instance(callable):
    callable.__appeal_must_be_instance__ = True
    return callable


def is_legal_annotation(annotation):
    if getattr(annotation, "__appeal_must_be_instance__", False):
        result = not isinstance(annotation, types.FunctionType)
        return result
    return True


##
## charm
##
## Charm is a simple "bytecode" language.
## Appeal uses Charm to represent mapping
## an Appeal "command" function to the command-line.
##
## See appeal/notes/charm.txt for lots more information.
## Unfortunately that document is out of date.
##

## goal with bytecode design:
##   * no "if" statements inside implementation of any bytecode
##     (sadly, there's one, due to "option" on create_converter)
##
## the interpreter has registers:
##    ip
##        the instruction pointer.  an integer, indexes into "program".
##    converter
##        a reference to a converter (or None).
##        the current converter context.
##        conceptually an indirect register like SP or a segment register,
##          you index through it to reference things.
##          specifically:
##              args
##                positional arguments, accessed with an index (-1 permitted).
##              kwargs
##                keyword-only arguments, accessed by name.
##          you can directly store str arguments in these attributes.
##          or, create converters and store (and possibly later retrieve)
##          converter objects in these attributes.
##    o
##        a reference to a converter, a string, or None.
##        a general-purpose register.
##        contains the result of create_converter, pop_converter,
##         consume_argument, and load_converter.
##    total
##        argument counter object (or None).
##        argument counts for this entire command function (so far).
##    group
##        argument counter object (or None).
##        local argument counts just for this argument group.
##
## the interpreter has a stack.  it's used to push/pop all registers
## except ip (which is pushed/popped separately).


## argument counter objects have these fields:
##    count = how many arguments we've consumed
##    minimum = the minimum "arguments" needed
##    maximum = the maximum "arguments" permissible

class ArgumentCounter:
    def __init__(self, minimum=0, maximum=0, optional=True):
        self.minimum = minimum
        self.maximum = maximum
        self.count = 0
        self.optional = optional

    def satisfied(self):
        return self.minimum <= self.count <= self.maximum

    def __repr__(self):
        return f"<ArgumentCounter minimum {self.minimum} <= count {self.count} <= maximum {self.maximum} == {bool(self)}>"

    def copy(self):
        return ArgumentCounter(self.minimum, self.maximum)

    def summary(self):
        ok_no = "(ok)" if self.satisfied() else "(no)"
        return f"[min {self.minimum} <= cur {self.count} <= max {self.maximum} {ok_no}]"


class CharmProgram:

    id_counter = 1

    def __init__(self, name=None, minimum=0, maximum=0):
        self.name = name

        self.id = CharmProgram.id_counter
        CharmProgram.id_counter += 1

        self.opcodes = []

        self.total = ArgumentCounter(minimum, maximum, False)
        self.converter_key = 0

    def __repr__(self):
        s = f" {self.name!r}" if self.name else ""
        return f"<CharmProgram {self.id:02}{s}>"

    def __len__(self):
        return len(self.opcodes)

    def __iter__(self):
        return iter(self.opcodes)

    def __getitem__(self, index):
        return self.opcodes[index]


class opcode(enum.Enum):
    invalid = 0
    jump = 1
    jump_relative = 2
    branch_on_o = 3
    call = 4
    create_converter = 5
    load_converter = 6
    load_o = 7
    append_args = 8
    store_kwargs = 9
    map_option = 10
    consume_argument = 11
    flush_multioption = 12
    set_group = 13
    push_context = 14
    pop_context = 15
    end = 16

    # these are removed by the peephole optimizer.
    # the interpreter never sees them.
    # (unless you leave in comments during debugging.)
    label = 100,
    jump_to_label = 101,
    branch_on_o_to_label = 102
    no_op = 103
    comment = 104

class CharmInstruction:
    op = opcode.invalid

    def copy(self):
        kwargs = {attr: getattr(self, attr) for attr in dir(self) if not (attr.startswith("_") or (attr in ("copy", "op"))) }
        return self.__class__(**kwargs)

class CharmInstructionNoArgBase(CharmInstruction):
    # __slots__ = []
    def __repr__(self):
        return f"<{str(self.op).partition('.')[2]}>"

class CharmInstructionAddressBase(CharmInstruction):
    # __slots__ = ['address']

    def __init__(self, address):
        self.address = address

    def __repr__(self):
        return f"<{str(self.op).partition('.')[2]} address={self.address}>"

class CharmInstructionKeyBase(CharmInstruction):
    # __slots__ = ['key']

    def __init__(self, key):
        self.key = key

    def __repr__(self):
        return f"<{str(self.op).partition('.')[2]} key={self.key}>"

class CharmInstructionLabelBase(CharmInstruction):
    # __slots__ = ['label']

    def __init__(self, label):
        self.label = label

    def __repr__(self):
        return f"<{str(self.op).partition('.')[2]} label={self.label!r}>"

class CharmInstructionComment(CharmInstruction):
    # __slots__ = ['comment']
    op = opcode.comment

    def __init__(self, comment):
        self.comment = comment

    def __repr__(self):
        return f"<comment {self.comment!r}>"

class CharmInstructionNoOp(CharmInstructionNoArgBase):
    op = opcode.no_op

class CharmInstructionJumpRelative(CharmInstruction):
    """
    jump_relative <offset>

    Adds <offset> to the 'ip' register.
    <offset> is an integer, and may be negative.
    """
    op = opcode.jump_relative
    # __slots__ = ['offset']

    def __init__(self, offset):
        self.offset = offset

    def __repr__(self):
        return f"<jump_relative offset={self.offset}>"

class CharmInstructionJump(CharmInstructionAddressBase):
    """
    jump <address>

    Sets the 'ip' register to <address>.
    <address> is an integer.
    """
    op = opcode.jump

class CharmInstructionBranchOnO(CharmInstructionAddressBase):
    """
    branch_on_o <address>

    If the 'o' register is a true value,
    sets the 'ip' register to <address>.
    <address> is an integer.
    """
    op = opcode.branch_on_o

class CharmInstructionLabel(CharmInstruction):
    """
    label <name>

    Sets a destination in the program that can be
    jumped to by the jump_to_label instruction.

    <name> may be nearly any Python value; the value
    must support basic mathematical properties:
    reflexive, symmetric, transitive, substitution, etc.

    label and *_to_label are both pseudo-instructions.
    They're removed by a pass in the peephole optimizer.
    """
    op = opcode.label
    # __slots__ = ['id', 'name']

    label_id_counter = 0

    def __init__(self, name=''):
        CharmInstructionLabel.label_id_counter += 1
        self.id = CharmInstructionLabel.label_id_counter
        self.name = name

    def __repr__(self):
        print_name = f" name={self.name!r}" if self.name else ""
        return f"<label id={self.id}{print_name}>"

    def __hash__(self):
        return id(CharmInstructionLabel) ^ self.id

class CharmInstructionJumpToLabel(CharmInstructionLabelBase):
    """
    jump_to_label <label>

    Sets the 'ip' register to point to the instruction
    after the instance of the <label> instruction in the
    current program.

    label and *_to_label are both pseudo-instructions.
    They're removed by a pass in the peephole optimizer.
    """
    op = opcode.jump_to_label

class CharmInstructionBranchOnOToLabel(CharmInstructionLabelBase):
    """
    branch_on_o_to_label <label>

    If the 'o' register is a true value,
    sets the 'ip' register to point to the instruction
    after the instance of the <label> instruction in the
    current program.

    label and *_to_label are both pseudo-instructions.
    They're removed by a pass in the peephole optimizer.
    """
    op = opcode.branch_on_o_to_label

class CharmInstructionCreateConverter(CharmInstruction):
    """
    create_converter <parameter> <key>

    Creates a Converter object using <parameter>,
    an inspect.Parameter object.

    Stores the resulting converter object
    in 'converters[key]' and in the 'o' register.
    """
    op = opcode.create_converter
    # __slots__ = ['parameter', 'key']

    def __init__(self, parameter, key):
        self.parameter = parameter
        self.key = key

    def __repr__(self):
        return f"<create_converter parameter={parameter!r} key={self.key}>"

class CharmInstructionLoadConverter(CharmInstructionKeyBase):
    """
    load_converter <key>

    Loads a Converter object from 'converters[key]' and
    stores a reference in the 'converter' register.
    """
    op = opcode.load_converter

class CharmInstructionLoadO(CharmInstructionKeyBase):
    """
    load_o <key>

    Loads a Converter object from 'converters[key]' and
    stores a reference in the 'o' register.
    """
    op = opcode.load_o

class CharmInstructionAppendArgs(CharmInstruction):
    """
    append_args <parameter> <usage>

    Takes a reference to the value in the 'o' register
    and appends it to 'converter.args'.

    <callable> is a callable object.
    <parameter> and <usage> are strings identifying
    the name of the parameter.  These are all used in
    generating usage information and documentation.
    """
    op = opcode.append_args

    def __init__(self, callable, parameter, usage, usage_callable, usage_parameter):
        self.callable = callable
        self.parameter = parameter
        self.usage = usage
        self.usage_callable = usage_callable
        self.usage_parameter = usage_parameter

    def __repr__(self):
        return f"<append_args callable={self.callable} parameter={self.parameter} usage={self.usage} usage_callable={self.usage_callable} usage_parameter={self.usage_parameter}>"

class CharmInstructionStoreKwargs(CharmInstructionNoArgBase):
    """
    store_kwargs <name>

    Takes a reference to the object currently in
    the 'o' register and stores it in 'converter.kwargs[<name>]'.
    (Here 'converter' is the 'converter' register.)

    <name> is a string.
    """
    op = opcode.store_kwargs
    # __slots__ = ['name']

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<store_kwargs name={self.name}>"

class CharmInstructionPushContext(CharmInstructionNoArgBase):
    """
    push_context

    Pushes the current 'converter', 'total', and 'group'
    registers on the stack.
    """
    op = opcode.push_context

class CharmInstructionPopContext(CharmInstructionNoArgBase):
    """
    pop_context

    Pops the top value from the stack, restoring
    the previous values of the 'converter', 'total',
    and 'group' registers.
    """
    op = opcode.pop_context

class CharmInstructionMapOption(CharmInstruction):
    """
    map_option <option> <program>

    Maps the option <option> to the program <program>.

    <program> is self-contained; if the option is invoked
    on the command-line, you may simply 'push' the new
    program on your current CharmInterpreter.

    <key> and <parameter> are used in generating
    usage information.  <key> is the converter key
    for the converter, and <parameter> is the
    parameter on that converter, that this option
    maps to.
    """
    op = opcode.map_option
    # __slots__ = ['option', 'program']

    def __init__(self, option, program, callable, parameter, key):
        self.option = option
        self.program = program
        self.callable = callable
        self.parameter = parameter
        self.key = key

    def __repr__(self):
        return f"<map_option option={self.option!r} program={self.program} key={self.key} parameter={self.parameter} key={self.key}>"

class CharmInstructionConsumeArgument(CharmInstruction):
    """
    consume_argument <is_oparg>

    Consumes an argument from the command-line,
    and stores it in the 'o' register.

    <is_oparg> is a boolean flag:
        * If <is_oparg> is True, you are consuming an oparg.
          You should consume the next command-line argument
          no matter what it is--even if it starts with a
          dash, which would normally indicate a command-line
          option.
        * If <is_oparg> is False, you are consuming a top-level
          command-line positional argument.  You should process
          command-line arguments normally, including
          processing options.  Continue processing until
          you find a command-line argument that isn't
          an option, nor is consumed by any options that
          you might have encountered while processing,
          and then consume that argument to satisfy this
          instruction.  (Also, is_oparg being False has some
          effect on the "option stack".)
    """
    op = opcode.consume_argument
    # __slots__ = ['is_oparg']

    def __init__(self, is_oparg):
        self.is_oparg = is_oparg

    def __repr__(self):
        return f"<consume_argument is_oparg={self.is_oparg}>"

class CharmInstructionFlushMultioption(CharmInstructionNoArgBase):
    """
    flush_multioption

    Calls the flush() method on the object stored in
    the 'o' register.
    """
    op = opcode.flush_multioption


class CharmInstructionSetGroup(CharmInstruction):
    """
    set_group <minimum> <maximum>

    Indicates that the program has entered a new argument
    group, and specifies the minimum and maximum arguments
    accepted by that group.  These numbers are stored as
    an ArgumentCount object in the 'group' register.
    """
    op = opcode.set_group
    # __slots__ = ['group', 'optional', 'repeating']

    def __init__(self, minimum, maximum, optional, repeating):
        self.group = ArgumentCounter(minimum, maximum, optional)
        self.optional = optional
        self.repeating = repeating

    def __repr__(self):
        return f"<set_group group={self.group.summary()} optional={self.optional} repeating={self.repeating}>"

class CharmInstructionEnd(CharmInstruction):
    """
    end

    Marks the end of a program.  A no-op, exists only
    to provide some context when reading the trace from
    a running interpreter.
    """
    op = opcode.end

    def __init__(self, id, name):
        self.id = id
        self.name = name

    def __repr__(self):
        return f"<{str(self.op).partition('.')[2]} id={self.id} name={self.name!r}>"


class CharmAssembler:
    def __init__(self, compiler):
        self.compiler = compiler
        self.opcodes = []

    def append(self, opcode):
        self.opcodes.append(opcode)
        return opcode

    def extend(self, opcodes):
        self.opcodes.extend(opcodes)

    def no_op(self):
        op = CharmInstructionNoOp()
        return self.append(op)

    def comment(self, comment):
        op = CharmInstructionComment(comment)
        return self.append(op)

    def label(self, name):
        op = CharmInstructionLabel(name)
        return self.append(op)

    def jump_to_label(self, label):
        op = CharmInstructionJumpToLabel(label)
        return self.append(op)

    def call(self, program):
        op = CharmInstructionCall(program)
        return self.append(op)

    def create_converter(self, parameter):
        key = self.compiler.program.converter_key
        self.compiler.program.converter_key += 1
        op = CharmInstructionCreateConverter(
            parameter=parameter,
            key=key,
            )
        return self.append(op)

    def load_converter(self, key):
        op = CharmInstructionLoadConverter(
            key=key,
            )
        return self.append(op)

    def load_o(self, key):
        op = CharmInstructionLoadO(
            key=key,
            )
        return self.append(op)

    def append_args(self, callable, parameter, usage, usage_callable, usage_parameter):
        op = CharmInstructionAppendArgs(
            callable = callable,
            parameter = parameter,
            usage = usage,
            usage_callable = usage_callable,
            usage_parameter = usage_parameter,
            )
        return self.append(op)

    def store_kwargs(self, name):
        op = CharmInstructionStoreKwargs(
            name=name,
            )
        return self.append(op)

    def push_context(self):
        op = CharmInstructionPushContext()
        return self.append(op)

    def pop_context(self):
        op = CharmInstructionPopContext()
        return self.append(op)

    def map_option(self, option, program, callable, parameter, key):
        op = CharmInstructionMapOption(
            option = option,
            program = program,
            callable = callable,
            parameter = parameter,
            key = key,
            )
        return self.append(op)

    def consume_argument(self, is_oparg=False):
        op = CharmInstructionConsumeArgument(
            is_oparg=is_oparg,
            )
        return self.append(op)

    def flush_multioption(self):
        op = CharmInstructionFlushMultioption()
        return self.append(op)

    def branch_on_o_to_label(self, label):
        op = CharmInstructionBranchOnOToLabel(label=label)
        return self.append(op)

    def set_group(self, minimum=0, maximum=0, optional=True, repeating=False):
        op = CharmInstructionSetGroup(minimum=minimum, maximum=maximum, optional=optional, repeating=repeating)
        return self.append(op)

    def end(self, id, name):
        op = CharmInstructionEnd(id=id, name=name)
        return self.append(op)



class CharmCompiler:
    def __init__(self, appeal, *, name=None, converter_key=0):
        self.appeal = appeal
        self.name = name

        self.program = CharmProgram(name)
        self.program.converter_key = converter_key

        self.root = appeal.root

        self.total = ArgumentCounter()
        self.group = ArgumentCounter()

        self.initial_a = CharmAssembler(self)
        self.final_a = CharmAssembler(self)

        self.waiting_argument_group_options = None

        self.assemblers = [self.initial_a]

        self.option_depth = 0

        # options defined in the current argument group
        self.argument_group_options = set()
        self.argument_group_counter = 0

        # options defined since the last consume_argument
        self.consume_argument_options = set()
        self.consume_argument_counter = 0

        self.new_argument_group_assemblers(optional=False)
        self.after_consume_argument()

        self.name_to_callable = {}

    def new_consume_argument_assemblers(self):
        if want_prints:
            print(f"[cc]     -- new 'consume argument options' and 'body' assemblers")
        self.consume_argument_options.clear()

        self.consume_argument_options_a = a = CharmAssembler(self)
        a.comment(f"{self.program.name} consume_argument {self.consume_argument_counter} options")
        self.assemblers.append(a)

        self.a = a = CharmAssembler(self)
        a.comment(f"{self.program.name} body {self.consume_argument_counter}")
        self.assemblers.append(a)

        self.consume_argument_counter += 1

    def new_argument_group_assemblers(self, *, optional):
        if want_prints:
            print(f"[cc]     -- new argument group 'converters' and 'options' assemblers")

        self.flush_argument_group_options()

        self.argument_group_options.clear()

        self.converters_a = a = CharmAssembler(self)
        a.comment(f"{self.program.name} argument group {self.argument_group_counter} converters")
        self.assemblers.append(a)

        self.argument_group_options_a = a = CharmAssembler(self)
        assert not self.waiting_argument_group_options
        self.waiting_argument_group_options = a
        a.comment(f"{self.program.name} argument group {self.argument_group_counter} options")

        self.argument_group_counter += 1

        return self.converters_a.set_group(optional=optional)

    def new_argument_group(self, *, optional):
        return_value = self.new_argument_group_assemblers(optional=optional)
        self.new_consume_argument_assemblers()
        return return_value

    def flush_argument_group_options(self):
        if self.waiting_argument_group_options:
            if want_prints:
                print(f"[cc]     -- flushing argument group options")
            self.assemblers.append(self.waiting_argument_group_options)
            self.waiting_argument_group_options = None

    def after_consume_argument(self):
        self.flush_argument_group_options()
        self.new_consume_argument_assemblers()

    # def ensure_callables_have_unique_names(self, callable):
    #     assert hasattr(callable, '__name__'), "{callable} has no __name__ attribute, how do we track it?"
    #     name = callable.__name__
    #     existing = self.name_to_callable.get(name)
    #     if existing and (existing != callable):
    #         raise AppealConfigurationError("multiple annotation functions with the same name {name!r}: {callable} and {existing}")

    def compile_options(self, parent_callable, key, parameter, options, depth):
        if want_prints:
            indent = "  " * depth
            print(f"[cc] {indent}compile_options {options=} {key=} {parameter=} {parameter.kind=}")

        cls = self.appeal.root.map_to_converter(parameter)

        assert options
        strings = [f"{parent_callable.__name__}"]
        strings.extend(denormalize_option(o) for o in options)
        program_name = ", ".join(strings)
        if cls is SimpleTypeConverterStr:
            if want_prints:
                print(f"[cc] {indent}(hand-coded str option)")
            program = CharmProgram(name=program_name, minimum=1, maximum=1)
            a = CharmAssembler(self)
            a.push_context()
            a.set_group(1, 1, optional=False)
            a.load_converter(key)
            a.consume_argument(is_oparg=True)
            a.store_kwargs(parameter.name)
            a.pop_context()
            a.end(name=program_name, id=program.id)
            program.opcodes = a.opcodes
        else:
            if not is_legal_annotation(parameter.annotation):
                raise AppealConfigurationError(f"{parent_callable.__name__}: parameter {parameter.name!r} annotation is {parameter.annotation}, which you can't use directly, you must call it")

            # self.ensure_callables_have_unique_names(callable)
            multioption = issubclass(cls, MultiOption)

            cc = CharmCompiler(self.appeal, name=program_name, converter_key=self.program.converter_key)
            a = cc.initial_a
            a.push_context()

            a = cc.final_a
            a.pop_context()

            store_kwargs = key, parameter.name
            program = cc.compile(parameter.annotation, parameter.default, is_option=True, multioption=multioption, depth=depth+1, store_kwargs=store_kwargs)
            assert self.program.converter_key != cc.program.converter_key
            self.program.converter_key = cc.program.converter_key

        for option in options:
            # option doesn't have to be unique in this argument group,
            # but it must be unique per consumed argument.
            # (you can't define the same option twice without at least one consume_argument between.)
            if option in self.consume_argument_options:
                raise AppealConfigurationError(f"multiple definitions of option {denormalize_option(option)} are ambiguous (no arguments consumed in between definitions)")
            self.consume_argument_options.add(option)

            if option not in self.argument_group_options:
                self.argument_group_options.add(option)
                destination = self.argument_group_options_a
            else:
                destination = self.consume_argument_options_a

            if want_prints:
                indent = "  " * depth
                print(f"[cc] {indent}compile_options {option=} {program=} callable={parent_callable} {parameter=} {key=} {destination=}")
            destination.map_option(option, program, parent_callable, parameter, key)

    def map_options(self, callable, parameter, signature, key, depth=0):
        if want_prints:
            indent = "  " * depth
            print(f"[cc] {indent}{callable.__name__} map_options {parameter=} {key=} {signature=}")
        _, kw_parameters, _ = self.appeal.fn_database_lookup(callable)
        mappings = kw_parameters.get(parameter.name, ())

        if not mappings:
            p = signature.parameters.get(parameter.name)
            assert p
            annotation = p.annotation
            default = p.default
            default_options = self.appeal.root.default_options
            assert builtins.callable(default_options)
            default_options(self.appeal, callable, parameter.name, annotation, default)

        parameter_index_to_options = collections.defaultdict(list)
        parameters = []
        for option_entry in kw_parameters[parameter.name]:
            option, callable2, parameter = option_entry
            # assert callable is not empty
            assert callable == callable2

            # not all parameters are hashable.  (default might be a list, etc.)
            try:
                parameter_index = parameters.index(parameter)
            except ValueError:
                parameter_index = len(parameters)
                parameters.append(parameter)

            parameter_index_to_options[parameter_index].append(option)

        for parameter_index, options in parameter_index_to_options.items():
            parameter = parameters[parameter_index]
            self.compile_options(callable, key, parameter, options, depth)


    def _compile(self, depth, parameter, pgi, usage_callable, usage_parameter, multioption=False, append=None, store_kwargs=None):
        """
        returns is_degenerate, a boolean, True if this entire subtree is "degenerate".
        """

        if want_prints:
            indent = "  " * depth
            print(f"[cc] {indent}compiling '{parameter=}' {depth=}, pgi, {multioption=} {append=}")

        maps_to_positional = set((POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD, VAR_POSITIONAL))
        tracked_by_argument_grouping = set((POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD, VAR_POSITIONAL))

        callable = parameter.annotation
        cls = self.root.map_to_converter(parameter)
        signature = cls.get_signature(parameter)
        parameters = signature.parameters
        if want_prints:
            print(f"[cc] {indent}{cls=} {signature=}")

        if depth == 0:
            # degenerate only applies to depth > 1.
            is_degenerate = False
        else:
            is_degenerate = len(parameters) < 2
        if want_prints:
            print(f"[cc] {indent}{is_degenerate=}, {len(parameters)=} < 2")

        if multioption:
            assert not append
            label_flush_multioption = CharmInstructionLabel("flush_multioption")
            label_after_multioption = CharmInstructionLabel("after_multioption")

            load_o_op = self.converters_a.load_o(0)
            self.converters_a.branch_on_o_to_label(label_flush_multioption)

        op = self.converters_a.create_converter(parameter=parameter)
        converter_key = op.key

        append_op = None
        if append:
            self.a.load_o(key=converter_key)
            append_op = self.a.append_args(**append)
        elif store_kwargs:
            parent_key, parameter_name = store_kwargs
            self.a.load_converter(key=parent_key)
            self.a.load_o(key=converter_key)
            self.a.store_kwargs(name=parameter_name)

        if multioption:
            load_o_op.key = converter_key
            self.converters_a.jump_to_label(label_after_multioption)
            self.converters_a.append(label_flush_multioption)
            op = self.converters_a.flush_multioption()
            self.converters_a.append(label_after_multioption)

        var_keyword = None
        kw_parameters_seen = set()
        _, kw_parameters, positionals = self.appeal.fn_database_lookup(callable)

        for i, (parameter_name, p) in enumerate(parameters.items()):
            # populate options, and find var_keyword (if present)
            if p.kind == KEYWORD_ONLY:
                if p.default == empty:
                    raise AppealConfigurationError(f"{usage_callable}: keyword-only argument {parameter_name} doesn't have a default value")
                kw_parameters_seen.add(parameter_name)
                self.map_options(callable, p, signature, converter_key, depth=depth)
                continue
            if p.kind == VAR_KEYWORD:
                var_keyword = parameter_name
                continue

        # step 2: populate **kwargs-only options
        # (options created with appeal.option(), where the parameter_name doesn't
        #  appear in the function, so the output goes into **kwargs)
        kw_parameters_unseen = set(kw_parameters) - kw_parameters_seen
        if kw_parameters_unseen:
            if not var_keyword:
                raise AppealConfigurationError(f"{usage_callable}: there are options that must go into **kwargs, but this callable doesn't accept **kwargs.  options={parameters_unseen}")
            for parameter_name in kw_parameters_unseen:
                parameter = inspect.Parameter(parameter_name, KEYWORD_ONLY)
                self.map_options(callable, parameter, signature, converter_key, depth=depth)

        group = None

        # Consider this:
        #
        #  def my_int(s): return int(s)
        #  @app.command()
        #  def foo(abc:my_int): ...
        #
        # In usage we'd rather see "abc" than "s".  So this is special-cased.
        # Appeal calls this a "degenerate converter tree"; it's a tree of converter
        # functions that only have one positional parameter each.  Appeal will by
        # default use the usage information from the parameter from the root parameter
        # of that degenerate converter tree--in this case, the parameter "abc" from
        # the function "foo".

        for i, (parameter_name, p) in enumerate(parameters.items()):
            if not p.kind in maps_to_positional:
                continue

            if not is_legal_annotation(p.annotation):
                raise AppealConfigurationError(f"{callable.__name__}: parameter {parameter.name!r} annotation is {parameter.annotation}, which you can't use directly, you must call it")

            # FIXME it's lame to do this here,
            # you need to rewrite _compile so it
            # always recurses for positional parameters
            cls = self.root.map_to_converter(p)

            if p.kind == VAR_POSITIONAL:
                label = self.a.label("var_positional")
                index = -1
            else:
                index = i

            if is_degenerate:
                usage_parameter = usage = None
            else:
                usage_callable = callable
                usage = usage_parameter = parameter_name

            usage = positionals.get(parameter_name, usage)

            if want_prints:
                printable_default = "(empty)" if p.default is empty else repr(p.default)
                print(f"[cc] {indent}{callable.__name__} positional parameter {i}: {p=} {p.kind=!s} annotation={p.annotation.__name__} default={printable_default} {cls=}")

            # only create new groups here if it's an optional group
            # (we pre-create the initial, required group)
            pgi_parameter = next(pgi)
            if want_prints:
                print(f"[cc] {indent}{pgi_parameter=}")
            if pgi_parameter.first_in_group and (not pgi_parameter.in_required_group):
                if want_prints:
                    print(f"[cc] {indent}{callable.__name__} new argument group optional=True")
                group = self.new_argument_group(optional=True)

            self.a.load_converter(key=converter_key)
            if cls is SimpleTypeConverterStr: # or (isinstance(callable, type) and issubclass(callable, Option)):
                # if want_prints:
                #     print(f"{indent_str}       LEAF {pgi_parameter} {is_degenerate=}")
                # ends_group = pgi_parameter.last_in_group
                # starts_optional_group = pgi_parameter.first_in_group and not pgi_parameter.in_required_group
                if want_prints:
                    print(f"[cc] {indent}{parameter} consume_argument and append")
                self.a.consume_argument(is_oparg=bool(self.option_depth))
                op = self.a.append_args(callable=callable, parameter=p, usage=usage, usage_callable=usage_callable, usage_parameter=usage_parameter)
                self.after_consume_argument()
            else:
                if want_prints:
                    print(f"[cc] {indent}{callable.__name__} recurse into {parameter_name} {p=}")
                # self.ensure_callables_have_unique_names(callable)
                append = {'callable': callable, 'parameter': parameter_name, "usage": usage, 'usage_callable': usage_callable, 'usage_parameter': usage_parameter }
                is_degenerate_subtree = self._compile(depth + 1, p, pgi, usage_callable, usage_parameter, None, append=append)
                is_degenerate = is_degenerate and is_degenerate_subtree

            if p.kind == VAR_POSITIONAL:
                group.repeating = True
                self.a.jump_to_label(label)

        # if want_prints:
        #     print(f"{indent_str}<< {callable=}")

        if append_op and not is_degenerate:
            if want_prints:
                print(f"[cc] {indent}suppress usage for non-leaf parameter {append_op.usage}")
            append_op.usage = None

        return is_degenerate


    def compile(self, callable, default, is_option=False, multioption=None, store_kwargs=None, depth=0):
        if self.name is None:
            self.name = callable.__name__
            self.program.name = self.name

        parameter_name = callable.__name__
        while True:
            if parameter_name.startswith('<'):
                parameter_name = parameter_name[1:-1]
                continue
            if parameter_name.endswith("()"):
                parameter_name = parameter_name[:-2]
                continue
            break

        def signature(p):
            cls = self.appeal.map_to_converter(p)
            signature = cls.get_signature(p)
            return strip_self_from_signature(signature)
        pg = argument_grouping.ParameterGrouper(callable, default, signature=signature)
        pgi = pg.iter_all()

        # self.initial_a.metadata(self.program_id, self.name)
        # self.initial_a.set_total()
        kind = KEYWORD_ONLY if is_option else POSITIONAL_ONLY
        if is_option:
            self.option_depth += 1
        parameter = inspect.Parameter(parameter_name, kind, annotation=callable, default=default)
        self._compile(depth, parameter, pgi, usage_callable=None, usage_parameter=None, multioption=multioption, store_kwargs=store_kwargs)
        self.final_a.end(self.program.id, self.name)
        self.assemblers.append(self.final_a)

        opcodes = self.finalize()
        self.program.opcodes = opcodes

        if is_option:
            self.option_depth -= 1

        return self.program


    def finalize(self):
        """
        Performs a finalization pass on program:

        * Computes total and group min/max values.
        * Convert label/jump_to_label pseudo-ops into
          absolute jump ops.
        * Simple peephole optimizer to remove redundant
          load_* ops.
        """

        program = self.program.opcodes
        for a in self.assemblers:
            opcodes = a.opcodes
            if not opcodes:
                continue
            if (len(opcodes) == 1) and (opcodes[0].op == opcode.comment):
                continue
            program.extend(opcodes)

        p = program

        labels = {}
        jump_fixups = []
        total = self.program.total
        group = None
        converter = None
        o = None
        stack = []

        optional = False

        i = 0

        while i < len(p):
            op = p[i]

            # remove labels
            if op.op == opcode.label:
                if op in labels:
                    raise AppealConfigurationError(f"label used twice: {op}")
                labels[op] = i
                del p[i]
                # forget current registers,
                # who knows what state the interpreter
                # will be in when we jump here.
                converter = o = None
                continue
            if op.op in (opcode.jump_to_label, opcode.branch_on_o_to_label):
                jump_fixups.append(i)

            # remove no_ops
            if op.op == opcode.no_op:
                del p[i]
                continue
            if op.op == opcode.comment:
                # if 1:
                if not want_prints:
                    del p[i]
                    continue

            # compute total and group values
            if op.op == opcode.set_group:
                group = op.group
                optional = op.optional
                if op.repeating:
                    if total:
                        total.maximum = math.inf
            if op.op == opcode.consume_argument:
                if total:
                    if not optional:
                        total.minimum += 1
                    total.maximum += 1
                if group:
                    group.minimum += 1
                    group.maximum += 1

            # discard redundant load_converter and load_o ops
            if op.op == opcode.load_converter:
                if converter == op.key:
                    del p[i]
                    continue
                converter = op.key
            if op.op == opcode.load_o:
                if o == op.key:
                    del p[i]
                    continue
                o = op.key
            if op.op == opcode.create_converter:
                o = op.key
            if op.op == opcode.consume_argument:
                o = '(string value)'
            if op.op == opcode.push_context:
                stack.append((converter, o, total, group))
            if op.op == opcode.pop_context:
                converter, o, total, group = stack.pop()

            i += 1

        # now process jump fixups:
        # replace *_to_label ops with absolute jump ops
        opcode_map = {
            opcode.jump_to_label: CharmInstructionJump,
            opcode.branch_on_o_to_label: CharmInstructionBranchOnO,
        }
        for i in jump_fixups:
            op = p[i]
            new_instruction_cls = opcode_map.get(op.op)
            assert new_instruction_cls
            address = labels.get(op.label)
            if address is None:
                raise AppealConfigurationError(f"unknown label {op.label}")
            p[i] = new_instruction_cls(address)

        return p


def charm_compile(appeal, callable, default=empty, name=None, *, is_option=False):
    if name is None:
        name = callable.__name__
    cc = CharmCompiler(appeal, name=name)
    program = cc.compile(callable, default, is_option=is_option)
    return program


def charm_print(program, indent=''):
    programs = collections.deque((program,))
    print_divider = False
    seen = set((program.id,))
    while programs:
        if print_divider:
            print("________________")
            print()
        else:
            print_divider = True
        program = programs.popleft()
        width = 2
        padding = " " * width
        indent2 = indent + f" {padding}|   "
        print(program)
        for i, op in enumerate(program):
            suffix = ""
            printable_op = str(op.op).rpartition(".")[2]
            print(f"{indent}[{i:0{width}}] {printable_op}{suffix}")
            # for slot in op.__class__.__slots__:
            for slot in dir(op):
                if slot.startswith("_") or slot in ("copy", "op"):
                    continue
                value = getattr(op, slot, None)
                if slot == "program":
                    print(f"{indent2}program={value}")
                    value_id = value.id
                    if value_id not in seen:
                        programs.append(value)
                        seen.add(value_id)
                    continue
                if slot == "callable":
                    value = value.__name__
                elif value == empty:
                    value = "(empty)"
                elif isinstance(value, ArgumentCounter):
                    value = value.summary()
                else:
                    value = repr(value)
                print(f"{indent2}{slot}={value}")
    print()



class CharmProgramIterator:
    def __init__(self, program):
        self.program = program
        self.opcodes = program.opcodes
        self.length = len(program)
        self.ip = 0

    def __next__(self):
        if not bool(self):
            raise StopIteration
        op = self.opcodes[self.ip]
        self.ip += 1
        if 0:
            print(f">> {hex(id(self))} ip -> ", op)
        return op

    def __bool__(self):
        return 0 <= self.ip < self.length

    def jump(self, address):
        self.ip = address

    def jump_relative(self, delta):
        self.ip += delta



class CharmInterpreter:
    def __init__(self, program, *, name=''):
        self.name = name
        self.stack = []
        self.context_stack = []

        assert program

        # registers
        self.converter = None
        self.o = None
        self.total = None
        self.group = None
        self.converters = {}

        self.op = None

        # ip register actually lives inside the iterator
        self.i = CharmProgramIterator(program)

    def repr_ip(self):
        ip = "--"
        if self.i:
            width = math.floor(math.log10(len(self.i.program)) + 1)
            ip = f"{self.i.ip:0{width}}"
        return ip

    def __repr__(self):
        ip = self.repr_ip()
        total = self.total and self.total.summary()
        group = self.group and self.group.summary()
        converter = self.repr_converter(self.converter)
        o = self.repr_converter(self.o)
        return f"<CharmInterpreter [{ip}] converter={converter!s} o={o!s} group={group!s} total={total!s}>"

    def repr_converter(self, converter):
        if self.converters:
            width = math.floor(math.log10(len(self.converters)) + 1)
            for key, value in self.converters.items():
                if converter == value:
                    return [key]
        return converter

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            if not (self.i or self.stack):
                raise StopIteration
            try:
                op = self.i.__next__()
                if 0:
                    print("()>", op)
                return op
            except StopIteration as e:
                self.finish()
                continue

    def __bool__(self):
        return bool(self.i) or any(bool(i) for i in self.stack)

    def rewind(self):
        if self.i is None:
            raise StopIteration
        self.i.jump_relative(-1)

    def call(self, program):
        self.stack.append([self.i, 0])
        self.i = CharmProgramIterator(program)

    def push_context(self):
        context = [self.converter, self.o, self.total, self.group]
        self.context_stack.append(context)
        if self.stack:
            self.stack[-1][-1] += 1

    def _pop_context(self):
        context = self.context_stack.pop()
        self.converter, self.o, self.total, self.group = context

    def pop_context(self):
        self._pop_context()
        if self.stack:
            self.stack[-1][-1] -= 1

    def finish(self):
        if self.stack:
            self.i, context_pops = self.stack.pop()
            for i in range(context_pops):
                self._pop_context()
        else:
            self.i = None

    def abort(self):
        self.i = None
        self.stack.clear()
        self.context_stack.clear()



def _charm_usage(program, usage, closing_brackets, formatter, arguments_values, option_values):
    ci = CharmInterpreter(program)
    program_id_to_option = collections.defaultdict(list)

    def add_option(op):
        program_id_to_option[op.program.id].append(op)

    def flush_options():
        for program_id, op_list in program_id_to_option.items():
            options = []
            for op in op_list:
                options.append(denormalize_option(op.option))
            full_name = f"{op.callable.__name__}.{op.parameter.name}"
            option_value = "|".join(options)
            option_values[full_name] = option_value

            usage.append(" [")
            usage.append(option_value)

            usage.append(" ")
            old_len_usage = len(usage)
            _charm_usage(op.program, usage, closing_brackets, formatter, arguments_values, option_values)
            if len(usage) == old_len_usage:
                # this option had no arguments, we don't want the space
                usage.pop()

            usage.append("]")

    last_op = None
    first_argument_in_group = True
    for op in ci:
        # print(f"{op=}")
        if ((last_op == opcode.map_option)
            and (op.op != last_op)):
            flush_options()

        if op.op == opcode.map_option:
            add_option(op)
        elif op.op == opcode.set_group:
            if op.optional:
                usage.append(" [")
                closing_brackets.append("]")
                if op.repeating:
                    closing_brackets.append("... ")
            first_argument_in_group = True
        elif op.op == opcode.append_args:
            # append_args can only be after one of those two opcodes!
            # if last_op.op in (opcode.consume_argument, opcode.load_o):
                if op.usage:
                    if first_argument_in_group:
                        first_argument_in_group = False
                    else:
                        usage.append(" ")
                    full_name = f"{op.usage_callable.__name__}.{op.usage_parameter}"
                    arguments_values[full_name] = op.usage
                    usage.append(formatter(op.usage))
        last_op = op

    flush_options()


def charm_usage(program, *, formatter=str):
    usage = []
    closing_brackets = []
    arguments_values = {}
    option_values = {}
    _charm_usage(program, usage, closing_brackets, formatter, arguments_values, option_values)
    usage.extend(closing_brackets)
    # print(f"{arguments_values=}")
    # print(f"{option_values=}")
    return "".join(usage).strip(), arguments_values, option_values



def charm_parse(appeal, program, argi):
    (
    option_space_oparg,

    short_option_equals_oparg,
    short_option_concatenated_oparg,
    ) = appeal.root.option_parsing_semantics

    ci = CharmInterpreter(program)
    root = None

    token_to_bucket = {}
    bucket_to_token = {}

    next_options_token = 0
    options_stack = []
    options_bucket = {}

    options_bucket = None
    options_token = None

    def push_options():
        nonlocal options_bucket
        nonlocal options_token
        nonlocal next_options_token
        options_stack.append(options_bucket)
        options_bucket = {}
        options_token = next_options_token
        next_options_token += 1
        token_to_bucket[options_token] = options_bucket
        bucket_to_token[id(options_bucket)] = options_token
        if want_prints:
            print(f"##       push_options {options_token=}")

    # create our first actual options bucket
    push_options()
    # ... but remove the useless None in the stack
    options_stack.clear()

    def pop_options():
        nonlocal options_bucket
        nonlocal options_token

        token = bucket_to_token[id(options_bucket)]
        del bucket_to_token[id(options_bucket)]
        del token_to_bucket[token]

        options_bucket = options_stack.pop()
        options_token = bucket_to_token[id(options_bucket)]

        if want_prints:
            print(f"##       pop_options, now at token {options_token}, popped {options_bucket=}")

    def pop_options_to_token(token):
        bucket = token_to_bucket.get(token)
        if bucket is None:
            return
        pop_count = 0
        while options_bucket != bucket:
            pop_count += 1
            pop_options()
        if want_prints:
            print(f"##     pop_options_to_token {token=} took {pop_count} pops")

    def pop_options_to_base():
        if want_prints:
            print(f"##     pop_options_to_base, popping {len(options_stack)} times")
        for _ in range(len(options_stack)):
            pop_options()

    def find_option(option):
        depth = 0
        bucket = options_bucket
        bucket_iter = reversed(options_stack)
        while True:
            program = bucket.get(option, None)
            if program is not None:
                break
            try:
                bucket = next(bucket_iter)
            except StopIteration:
                raise AppealUsageError(f"unknown option {denormalize_option(option)}") from None

        token = bucket_to_token[id(bucket)]
        return program, program.total.maximum, token

    ##
    ## It can be hard to tell in advance whether or not
    ## we have positional parameters waiting.  For example:
    ##   * the first actual positional argument is optional
    ##   * it's nested three levels deep in the annotation tree
    ##   * we have command-line arguments waiting in argi but
    ##     they're all options
    ## In this scenario, the easiest thing is to just run the
    ## program, create the converters, then discover we never
    ## consumed any positional arguments and just remove &
    ## destroy the converters.  This "undo" functionality
    ## does exactly that: you write down all the positional
    ## converters you create, and if you don't consume a
    ## positional argument, you undo them.
    ##
    ## Observe that:
    ##   * we're only talking about optional groups, and
    ##   * optional groups are only created if we consume arguments, and
    ##   * we don't create options in an optional group until
    ##     after we've consumed the first argument that ensures
    ##     we've really entered that group.
    ##
    ## This means we only need to undo the creation of
    ## positional converters.  And those are only ever
    ## appended to args_converters.  So the undo stack
    ## is easy: just note each parent converter, and
    ## for each one,
    ##     parent_converter.arg_converters.pop()
    ##
    ## Note, however, that we need to push/pop the undo
    ## converters state when we push/pop the context
    ## (when we start/finish parsing an option).

    converters_are_undoable = True
    undo_converters_list = []
    undo_converters_stack = []

    def reset_undo_converters():
        nonlocal converters_are_undoable
        converters_are_undoable = True
        undo_converters_list.clear()
        if want_prints:
            print(f"##     reset_undo_converters()")

    def forget_undo_converters():
        nonlocal converters_are_undoable
        undo_converters_list.clear()
        converters_are_undoable = False
        if want_prints:
            print(f"##     forget_undo_converters()")

    def add_undoable_converter(parent):
        if converters_are_undoable:
            undo_converters_list.append(parent)
            if want_prints:
                print(f"##     add_undoable_converter({parent=})")

    def push_undo_converters():
        nonlocal undo_converters_list
        nonlocal converters_are_undoable
        undo_converters_stack.append((undo_converters_list, converters_are_undoable))
        undo_converters_list = []
        converters_are_undoable = True

    def pop_undo_converters():
        nonlocal undo_converters_list
        nonlocal converters_are_undoable
        undo_converters_list, converters_are_undoable = undo_converters_stack.pop()

    def undo_converters():
        print_spacer=True
        for parent in reversed(undo_converters_list):
            o = parent.args_converters.pop()
            if want_prints:
                if print_spacer:
                    print(f"##")
                    print_spacer = False
                print(f"##     undo converter")
                print(f"##         {parent=}")
                print(f"##         popped {o=}")
                print(f"##         arg_converters={parent.args_converters}")
        undo_converters_list.clear()

    first_print_string = ""
    waiting_op = None
    prev_op = None
    while ci or argi:
        if want_prints:
            print(first_print_string)
            first_print_string = "##"
            print(f"############################################################")
            print(f"## cmdline {list(argi.values)}")

        # first, run ci until we either
        #    * finish the program, or
        #    * must consume a command-line argument
        for op in ci:
            prev_op = waiting_op
            waiting_op = op

            if want_prints:
                ip = f"[{ci.repr_ip()}]"
                ip_spacer = " " * len(ip)
                converter = ci.repr_converter(ci.converter)
                o = ci.repr_converter(ci.o)
                _total = ci.total and ci.total.summary()
                _group = ci.group and ci.group.summary()
                print(f"##")
                print(f"## {ip} {converter=} {o=}")
                print(f"## {ip_spacer} total={_total}")
                print(f"## {ip_spacer} group={_group}")

            if op.op == opcode.create_converter:
                r = None if op.parameter.kind == KEYWORD_ONLY else root
                cls = appeal.map_to_converter(op.parameter)
                converter = cls(op.parameter, appeal)
                ci.converters[op.key] = ci.o = converter
                if not root:
                    root = converter
                if want_prints:
                    print(f"##     create_converter key={op.key} parameter={op.parameter}")
                    print(f"##         {converter=}")
                continue

            if op.op == opcode.load_converter:
                ci.converter = ci.converters.get(op.key, None)
                converter = ci.repr_converter(ci.converter)
                if want_prints:
                    print(f"##     load_converter {op.key=} {converter=!s}")
                continue

            if op.op == opcode.load_o:
                ci.o = ci.converters.get(op.key, None)
                if want_prints:
                    o = ci.repr_converter(ci.o)
                    print(f"##     load_o {op.key=} {o=!s}")
                continue

            if op.op == opcode.map_option:
                options_bucket[op.option] = op.program
                if want_prints:
                    print(f"##     map_option {op.option=} {op.program=} token {options_token}")
                continue

            if op.op == opcode.append_args:
                ci.converter.args_converters.append(ci.o)
                add_undoable_converter(ci.converter)
                if want_prints:
                    o = ci.repr_converter(ci.o)
                    print(f"##     append_args {o=}")
                continue

            if op.op == opcode.store_kwargs:
                converter = ci.o
                if op.name in ci.converter.kwargs_converters:
                    existing = ci.converter.kwargs_converters[op.name]
                    if not ((existing == converter) and isinstance(existing, MultiOption)):
                        # TODO: this is terrible UI, must fix.
                        raise AppealUsageError(f"option is illegal, kwarg already set, {existing=} {hex(id(existing))} {converter=} {hex(id(converter))}")
                    # we're setting the kwarg to the value it's already set to,
                    # and it's a multioption, so this is fine.
                    continue
                ci.converter.kwargs_converters[op.name] = ci.o
                if want_prints:
                    o = ci.repr_converter(ci.o)
                    print(f"##     store_kwargs name={op.name} {o=}")
                continue

            if op.op == opcode.consume_argument:
                if want_prints:
                    print(f"##     consume_argument is_oparg={op.is_oparg}")
                if not argi:
                    if want_prints:
                        print(f"##     no more arguments, aborting program")
                    ci.abort()
                break

            if op.op == opcode.push_context:
                ci.push_context()
                push_undo_converters()
                if want_prints:
                    print(f"##     push_context")
                continue

            if op.op == opcode.pop_context:
                pop_undo_converters()
                ci.pop_context()
                if want_prints:
                    print(f"##     pop_context")
                continue

            if op.op == opcode.set_group:
                ci.group = op.group.copy()
                reset_undo_converters()
                if want_prints:
                    print(f"##     set_group {ci.group.summary()}")
                continue

            if op.op == opcode.flush_multioption:
                assert isinstance(ci.o, MultiOption), f"expected instance of MultiOption but {ci.o=}"
                ci.o.flush()
                if want_prints:
                    o = ci.repr_converter(ci.o)
                    print(f"##     flush_multioption {o=}")
                continue

            if op.op == opcode.jump:
                if want_prints:
                    print(f"##     jump {op.address=}")
                ci.i.jump(op.address)
                continue

            if op.op == opcode.jump_relative:
                if want_prints:
                    print(f"##     jump_relative {op.delta=}")
                ci.i.jump_relative(op.delta)
                continue

            if op.op == opcode.branch_on_o:
                if want_prints:
                    print(f"##     branch_on_o o={ci.o} {op.delta=}")
                if ci.o:
                    ci.i.jump(op.address)
                continue

            if op.op == opcode.comment:
                if want_prints:
                    print(f"##     comment {op.comment!r}")
                continue

            if op.op == opcode.end:
                if want_prints:
                    name = str(op.op).partition(".")[2]
                    print(f"##     {name} id={op.id} name={op.name!r}")
                continue

            raise AppealConfigurationError(f"unhandled opcode {op=}")

        else:
            # we finished the program
            if want_prints:
                print(f"##")
                print(f"## program finished.")
                print(f"##")
            op = None
            forget_undo_converters()

        assert (op == None) or (op.op == opcode.consume_argument)

        # it's time to consume arguments.
        # we've either paused or finished the program.
        #   if we've paused, it's because the program wants us
        #     to consume an argument.  in that case op
        #     will be a 'consume_argument' op.
        #   if we've finished the program, op will be None.
        #
        # technically this is a for loop over argi, but
        # we usually only consume one argument at a time.
        #
        # for a in argi:
        #    * if a is an option (or options),
        #      push that program (programs) and resume
        #      the charm interpreter.
        #    * if a is the special value '--', remember
        #      that all subsequent command-line arguments
        #      can no longer be options, and continue to
        #      the next a in argi.  (this is the only case
        #      in which we'll consume more than one argument
        #      in this loop.)
        #    * else a is a positional argument.
        #      * if op is consume_argument, consume it and
        #        resume the charm interpreter.
        #      * else, hmm, we have a positional argument
        #        we don't know what to do with.  the program
        #        is done, and we don't have a consume_argument
        #        to give it to.  so push it back onto argi
        #        and exit.  (hopefully the argument is the
        #        name of a command/subcomand.)

        for a in argi:
            if want_prints:
                print("#]")

            is_oparg = op and (op.op == opcode.consume_argument) and op.is_oparg
            # if this is true, we're consuming a top-level command-line argument.
            # if this is false, we're processing an oparg.
            # what's the difference? opargs can't be options.
            is_positional_argument = (
                appeal.root.force_positional
                or ((not a.startswith("-")) or (a == "-"))
                or is_oparg
                )

            if want_prints:
                # print_op = "consume_argument" if op else None
                print_op = op
                print(f"#] process argument {a!r} {list(argi.values)}")
                print(f"#] op={print_op}")

            if is_positional_argument:
                if not op:
                    if want_prints:
                        print(f"#]     positional argument we can't handle.  exit.")
                    argi.push(a)
                    return ci.converters[0]

                ci.o = a
                forget_undo_converters()
                if ci.group:
                    ci.group.count += 1
                if ci.total:
                    ci.total.count += 1
                if not is_oparg:
                    pop_options_to_base()
                if want_prints:
                    print(f"#]     positional argument.  o={ci.o!r}")
                # return to the interpreter
                break

            # it's an option!  or "--".

            if not option_space_oparg:
                raise AppealConfigurationError("oops, option_space_oparg must currently be True")

            queue = []
            option_stack_tokens = []

            # split_value is the value we "split" from the option string.
            #  --option=X
            #  -o=X
            #  -oX
            # it's set to X if the user specifies an X, otherwise it's None.
            split_value = None

            if a.startswith("--"):
                if a == "--":
                    appeal.root.force_positional = True
                    if want_prints:
                        print(f"#]     '--', force_positional=True")
                    continue

                option, equals, _split_value = a.partition("=")
                if equals:
                    split_value = _split_value

                program, maximum_arguments, token = find_option(option)
                option_stack_tokens.append(token)
                if want_prints:
                    print(f"#]     option {denormalize_option(option)} {program=}")
                queue.append((option, program, maximum_arguments, split_value, True))
            else:
                options = collections.deque(a[1:])

                while options:
                    option = options.popleft()
                    equals = short_option_equals_oparg and options and (options[0] == '=')
                    if equals:
                        options.popleft()
                        split_value = "".join(options)
                        options = ()
                    program, maximum_arguments, token = find_option(option)
                    option_stack_tokens.append(token)
                    # if it takes no arguments, proceed to the next option
                    if not maximum_arguments:
                        if want_prints:
                            print(f"#]     option {denormalize_option(option)}")
                        queue.append([denormalize_option(option), program, maximum_arguments, split_value, False])
                        continue
                    # this eats arguments.  if there are more characters waiting,
                    # they must be the split value.
                    if options:
                        assert not split_value
                        split_value = "".join(options)
                        options = ()
                        if not short_option_concatenated_oparg:
                            raise AppealUsageError(f"'-{option}{split_value}' is not allowed, use '-{option} {split_value}'")
                    if want_prints:
                        print(f"#]     option {denormalize_option(option)}")
                    queue.append([denormalize_option(option), program, maximum_arguments, split_value, False])

                # mark the last entry in the queue as last
                queue[-1][-1] = True

            assert queue and option_stack_tokens

            # we have options to run.
            # so the existing consume_argument op will have to wait.
            if op:
                ci.rewind()
                op = None

            # pop to the *lowest* bucket!
            option_stack_tokens.sort()
            pop_options_to_token(option_stack_tokens[0])

            # and now push on a new bucket.
            push_options()

            # process options in reverse here!
            # that's because we push each program on the interpreter.  so, LIFO.
            for error_option, program, maximum_arguments, split_value, is_last in reversed(queue):
                if want_prints:
                    print(f"#]     call program={program=} {split_value=}")

                if not is_last:
                    total = program.total
                    assert maximum_arguments == 0

                if split_value is not None:
                    assert is_last
                    if maximum_arguments != 1:
                        if maximum_arguments == 0:
                            raise AppealUsageError(f"{error_option} doesn't take an argument")
                        if maximum_arguments >= 2:
                            raise AppealUsageError(f"{error_option} given a single argument but it requires multiple arguments, you must separate the arguments with spaces")
                    argi.push(split_value)
                    if want_prints:
                        print(f"#]     pushing split value {split_value!r} on argi")

                ci.call(program)
            break

    undo_converters()
    satisfied = True
    if ci.total and not ci.total.satisfied():
        satisfied = False
        ag = ci.total
    if ci.group and not ci.group.satisfied():
        if (not ci.group.optional) or ci.group.count:
            satisfied = False
            ag = ci.group
    if not satisfied:
        if not ci.group.satisfied():
            which = "in this argument group"
            ag = ci.group
        else:
            which = "total"
            ag = ci.total
        if ag.minimum == ag.maximum:
            middle = f"{ag.minimum} arguments"
        else:
            middle = f"at least {ag.minimum} arguments but no more than {ag.maximum} arguments"
        message = f"{program.name} requires {middle} {which}."

        raise AppealUsageError(message)

    if want_prints:
        print(f"##")
        print(f"## ending parse.")
        finished_state = "not finished" if ci else "finished"
        print(f"##   program was {finished_state}.")
        if argi:
            print(f"##   remaining cmdline {list(argi.values)}")
        else:
            print(f"##   cmdline was consumed.")
        print(f"############################################################")
        print()

    return ci.converters[0]


class SpecialSection:
    def __init__(self, name, topic_names, topic_values, topic_definitions, topics_desired):
        self.name = name

        # {"short_name": "fn_name.parameter_name" }
        self.topic_names = topic_names
        # {"fn_name.parameter_name": "usage_name"}
        self.topic_values = topic_values
        # {"fn_name.parameter_name": docs... }
        self.topic_definitions = topic_definitions

        # Appeal's "composable documentation" feature means that it merges
        # up the docs for arguments and options from child converters.
        # But what about opargs?  Those are "arguments" from the child
        # converter tree, but you probably don't want them merged.
        #
        # So topics_desired lets you specify which topics Appeal should
        # merge.
        self.topics_desired = topics_desired

        self.topics = {}
        self.topics_seen = set()



class Converter:
    """
    A Converter object calls a Python function, filling
    in its parameters using command-line arguments.
    It introspects the function passed in, creating
    a tree of sub-Converter objects underneath it.

    A Converter
    """
    def __init__(self, parameter, appeal):
        callable = parameter.annotation
        default = parameter.default

        # self.fn = callable
        self.callable = callable

        if not hasattr(self, 'signature'):
            self.signature = self.get_signature(parameter)

        self.appeal = appeal
        # self.root = root or self
        self.default = default

        # output of analyze().  input of parse() and usage().
        # self.program = None

        self.docstring = self.callable.__doc__

        self.usage_str = None
        self.summary_str = None
        self.doc_str = None

        self.reset()

    @classmethod
    def get_signature(cls, parameter):
        if hasattr(cls, "signature"):
            return cls.signature
        return inspect.signature(parameter.annotation)

    def reset(self):
        # collections of converters we'll use to compute *args and **kwargs.
        # contains either raw strings or Converter
        # objects which we'll call.  these are the
        # output of parse() and the input of convert().
        self.args_converters = []
        self.kwargs_converters = {}

        # the output of convert(), and the input
        # for execute().
        self.args = []
        self.kwargs = {}


    def __repr__(self):
        return f"<{self.__class__.__name__} callable={self.callable.__name__}>"

    def convert(self):
        # print(f"{self=} {self.args_converters=} {self.kwargs_converters=}")
        for iterable in (self.args_converters, self.kwargs_converters.values()):
            for converter in iterable:
                if converter and not isinstance(converter, str):
                    # print(f"{self=}.convert, {converter=}")
                    converter.convert()

        for converter in self.args_converters:
            if converter and not isinstance(converter, str):
                converter = converter.execute()
            self.args.append(converter)
        for name, converter in self.kwargs_converters.items():
            if converter and not isinstance(converter, str):
                converter = converter.execute()
            self.kwargs[name] = converter

    def execute(self):
        # print(f"calling {self.callable}(*{self.args}, **{self.kwargs})")
        return self.callable(*self.args, **self.kwargs)




class InferredConverter(Converter):
    def __init__(self, parameter, appeal):
        if not parameter.default:
            raise AppealConfigurationError(f"empty {type(parameter.default)} used as default, so we can't infer types")
        p2 = inspect.Parameter(parameter.name, kind=parameter.kind, annotation=type(parameter.default), default=parameter.default)
        super().__init__(p2, appeal)

    @classmethod
    def get_signature(cls, parameter):
        if hasattr(cls, "signature"):
            return cls.signature
        return inspect.signature(type(parameter.default))

class InferredSequenceConverter(InferredConverter):
    @classmethod
    def get_signature(cls, parameter):
        if hasattr(cls, "signature"):
            return cls.signature
        parameters = []
        if not parameter.default:
            width = 0
        else:
            width = math.floor(math.log10(len(parameter.default))) + 1
        separator = "_" if parameter.name[-1].isdigit() else ""
        for i, value in enumerate(parameter.default):
            name = f"{parameter.name}{separator}{i:0{width}}"
            p = inspect.Parameter(name, inspect.Parameter.POSITIONAL_ONLY, annotation=type(value))
            parameters.append(p)
        return inspect.Signature(parameters)

    def execute(self):
        return self.callable(self.args)



class SimpleTypeConverter(Converter):
    def __init__(self, parameter, appeal):
        self.appeal = appeal
        self.default = parameter.default

        self.value = None

        self.args_converters = []
        # don't set kwargs_converters, let it esplody!

        self.options_values = {}
        self.help_options = {}
        self.help_arguments = {}

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.callable} args_converters={self.args_converters} value={self.value}>"

    def convert(self):
        if not self.args_converters:
            # explicitly allow "make -j"
            if self.default is not empty:
                return self.default
            raise AppealUsageError(f"no argument supplied for {self}, we should have raised an error earlier huh.")
        self.value = self.callable(self.args_converters[0])

    def execute(self):
        return self.value


simple_type_signatures = {}

def parse_bool(bool) -> bool: pass
class SimpleTypeConverterBool(SimpleTypeConverter):
    signature = inspect.signature(parse_bool)
    callable = bool
simple_type_signatures[bool] = SimpleTypeConverterBool

def parse_complex(complex) -> complex: pass
class SimpleTypeConverterComplex(SimpleTypeConverter):
    signature = inspect.signature(parse_complex)
    callable = complex
simple_type_signatures[complex] = SimpleTypeConverterComplex

def parse_float(float) -> float: pass
class SimpleTypeConverterFloat(SimpleTypeConverter):
    signature = inspect.signature(parse_float)
    callable = float
simple_type_signatures[float] = SimpleTypeConverterFloat

def parse_int(int) -> int: pass
class SimpleTypeConverterInt(SimpleTypeConverter):
    signature = inspect.signature(parse_int)
    callable = int
simple_type_signatures[int] = SimpleTypeConverterInt

def parse_str(str) -> str: pass
class SimpleTypeConverterStr(SimpleTypeConverter):
    signature = inspect.signature(parse_str)
    callable = str
simple_type_signatures[str] = SimpleTypeConverterStr


class Option(Converter):
    pass

class InferredOption(Option):
    def __init__(self, parameter, appeal):
        if not parameter.default:
            raise AppealConfigurationError(f"empty {type(parameter.default)} used as default, so we can't infer types")
        p2 = inspect.Parameter(parameter.name, kind=parameter.kind, annotation=type(parameter.default), default=parameter.default)
        super().__init__(p2, appeal)

    @classmethod
    def get_signature(cls, parameter):
        if hasattr(cls, "signature"):
            return cls.signature
        return inspect.signature(type(parameter.default))

class InferredSequenceOption(InferredOption):
    @classmethod
    def get_signature(cls, parameter):
        if hasattr(cls, "signature"):
            return cls.signature
        parameters = []
        if not parameter.default:
            width = 0
        else:
            width = math.floor(math.log10(len(parameter.default))) + 1
        separator = "_" if parameter.name[-1].isdigit() else ""
        for i, value in enumerate(parameter.default):
            name = f"{parameter.name}{separator}{i:0{width}}"
            p = inspect.Parameter(name, inspect.Parameter.POSITIONAL_ONLY, annotation=type(value))
            parameters.append(p)
        return inspect.Signature(parameters)

    def execute(self):
        return self.callable(self.args)


def strip_self_from_signature(signature):
    parameters = collections.OrderedDict(signature.parameters)
    if not 'self' in parameters:
        return signature
    del parameters['self']
    if 'return' in parameters:
        return_annotation = parameters['return']
    else:
        return_annotation = empty
    return inspect.Signature(parameters.values(), return_annotation=return_annotation)


class SingleOption(Option):
    def __init__(self, parameter, appeal):
        # the callable passed in is ignored
        p2 = inspect.Parameter(parameter.name, kind=parameter.kind, annotation=self.option, default=parameter.default)
        super().__init__(p2, appeal)
        self.init(parameter.default)

    def __repr__(self):
        return f"<{self.__class__.__name__}>"

    @classmethod
    def get_signature(cls, parameter):
        if hasattr(cls, "signature"):
            return cls.signature
        # we need the signature of cls.option
        # but *without self*
        signature = inspect.signature(cls.option)
        return strip_self_from_signature(signature)

    def execute(self):
        self.option(*self.args, **self.kwargs)
        return self.render()

    # Your subclass of Option or MultiOption is required
    # to define its own option() and render() methods.
    # init() is optional.

    # init() is called at initialization time.
    # This is a convenience; you can overload __init__
    # if you like.  But that means staying in sync with
    # the parameters to __init__ and those aren't
    # settled yet.
    def init(self, default):
        pass

    # option() is called every time your option is specified
    # on the command-line.  For an Option, this will be exactly
    # one time.  For a MultiOption, this will be one or more
    # times.  (If your option is never specified on the
    # command-line, your Option subclass will never be created.)
    #
    # option() can take parameters, and these are translated
    # to command-line positional parameters or options in the
    # same way converters are.
    #
    # Note:
    #   * If option() takes no parameters, your option will
    #     consume no opargs or options, like a boolean option.
    #     It'll still be called every time, your option is
    #     specified.
    #   * You may (and are encouraged to!) specify annotations for
    #     the parameters to option().
    @abstractmethod
    def option(self):
        pass

    # render() is called exactly once, after option() has been
    # called for the last time.  it should return the "value"
    # for the option.
    @abstractmethod
    def render(self):
        pass


def parse_bool_option() -> bool: pass
class BooleanOptionConverter(SingleOption):
    signature = inspect.signature(parse_bool_option)

    def init(self, default):
        self.value = default

    def option(self):
        self.value = not self.value

    def render(self):
        return self.value


class MultiOption(SingleOption):
    def __init__(self, parameter, appeal):
        self.multi_converters = []
        self.multi_args = []
        # the callable passed in is ignored
        p2 = inspect.Parameter(parameter.name, kind=parameter.kind, annotation=self.option, default=parameter.default)
        super().__init__(p2, appeal)

    def flush(self):
        self.multi_converters.append((self.args_converters, self.kwargs_converters))
        self.reset()

    def convert(self):
        self.flush()
        for args, kwargs in self.multi_converters:
            self.args = []
            self.kwargs = {}
            self.args_converters = args
            self.kwargs_converters = kwargs
            super().convert()
            self.multi_args.append((self.args, self.kwargs))

    def execute(self):
        for args, kwargs in self.multi_args:
            self.option(*args, **kwargs)
        return self.render()


@must_be_instance
def counter(*, max=None, step=1):
    class Counter(MultiOption):
        def init(self, default):
            nonlocal max
            self.count = default
            if not step:
                raise AssertInternalError("counter(): step value cannot be 0")
            if max == None:
                max = math.inf if step > 0 else (-math.inf)
            self.max = max
            self.step = step

        def option(self):
            callable = min if self.step > 0 else max
            self.count = callable(self.count + step, self.max)

        def render(self):
            return self.count

    return Counter


class AccumulatorMeta(ABCMeta):
    def __getitem__(cls, t):
        if not isinstance(t, (tuple, list)):
            t = (t,)
        t_names = "_".join(ti.__name__ for ti in t)
        name = f"{cls.__name__}_{t_names}"
        parameters = ", ".join(f"p{i}:{ti.__name__}" for i, ti in enumerate(t))
        if len(t) == 1:
            arguments = "p0"
        else:
            arguments = "(" + ", ".join(f"p{i}" for i in range(len(t))) + ")"
        types = "(" + ", ".join(ti.__name__ for ti in t) + ")"
        text = f"""
class {name}(cls):
    __name__ = '{cls.__name__}[{t_names}]'

    def option(self, {parameters}):
        # print("accumulator meta got", {arguments})
        self.values.append({arguments})

    __types__ = {types}
"""
        globals = {'cls': cls, 't': t}
        # print("TEXT", text)
        exec(text, globals)
        return globals[name]

    def __repr__(cls):
        return f'<{cls.__name__}>'


class accumulator(MultiOption, metaclass=AccumulatorMeta):
    def init(self, default):
        self.values = []
        if default is not empty:
            self.values.extend(default)

    def option(self, s:str):
        self.values.append(s)

    def render(self):
        return self.values


class MappingMeta(ABCMeta):
    def __getitem__(cls, t):
        if not ((isinstance(t, (tuple, list))) and (len(t) >= 2)):
            raise AppealConfigurationError("MappingMeta[] must have at least two types")
        t_names = "_".join(ti.__name__ for ti in t)
        name = f"{cls.__name__}_{t_names}"
        key = "key"
        parameters0 = f"key:{t[0].__name__}, "
        if len(t) == 2:
            parameters = parameters0 + f"value:{t[1].__name__}"
            value = "value"
        else:
            parameters = parameters0 + ", ".join(f"value{i}:{ti.__name__}" for i, ti in enumerate(t[1:], 1))
            value = "(" + ", ".join(f"value{i}" for i in range(1, len(t))) + ")"
        types = "(" + ", ".join(ti.__name__ for ti in t) + ")"
        text = f"""
class {name}(cls):
    __name__ = '{cls.__name__}[{t_names}]'

    def option(self, {parameters}):
        # print("mapping meta got", {key}, "=", {value})
        self.dict[{key}] = {value}

    __types__ = {types}
"""
        globals = {'cls': cls, 't': t}
        # print("TEXT", text)
        exec(text, globals)
        return globals[name]

    def __repr__(cls):
        return f'<{cls.__name__}>'


class mapping(MultiOption, metaclass=MappingMeta):
    def init(self, default):
        self.dict = {}
        if default is not empty:
            self.dict.update(dict(default))

    def option(self, k:str, v:str):
        if k in self.dict:
            raise AppealUsageError("defined {k} more than once")
        self.dict[k] = v

    def render(self):
        return self.dict


@must_be_instance
def split(*separators, strip=False):
    """
    Creates a converter function that splits a string
    based on one or more separator strings.

    If you don't supply any separators, splits on
    any whitespace.

    If strip is True, also calls strip() on the
    strings after splitting.
    """
    if not separators:
        def split(str):
            return str.split()
        return split

    if not all((s and isinstance(s, str)) for s in separators):
        raise AppealConfigurationError("split(): every separator must be a non-empty string")

    def split(str):
        values = multisplit(str, separators)
        if strip:
            values = [s.strip() for s in values]
        return values
    return split



@must_be_instance
def validate(*values, type=None):
    """
    Creates a converter function that validates a value
    from the command-line.

        values is a list of permissible values.
        type is the type for the value.  If not specified,
          type defaults to builtins.type(values[0]).

    If the value from the command-line is one of the values,
    returns value.  Otherwise reports a usage error.
    """
    if not values:
        raise AppealConfigurationError("validate() called without any values.")
    if type == None:
        type = builtins.type(values[0])
    failed = []
    for value in values:
        if not isinstance(value, type):
            failed.append(value)
    if failed:
        failed = " ".join(repr(x) for x in failed)
        raise AppealConfigurationError("validate() called with these non-homogeneous values {failed}")

    values_set = set(values)
    def validate(value:type):
        if value not in values_set:
            raise AppealUsageError(f"illegal value {value!r}, should be one of {' '.join(repr(v) for v in values)}")
        return value
    return validate

@must_be_instance
def validate_range(start, stop=None, *, type=None, clamp=False):
    """
    Creates a converter function that validates that
    a value from the command-line is within a range.

        start and stop are like the start and stop
            arguments for range().

        type is the type for the value.  If unspecified,
            it defaults to builtins.type(start).

    If the value from the command-line is within the
    range established by start and stop, returns value.

    If value is not inside the range of start and stop,
    and clamp=True, returns either start or stop,
    whichever is nearest.

    If value is not inside the range of start and stop,
    and clamp=False, raise a usage error.
    """
    if type is None:
        type = builtins.type(start)

    if stop is None:
        stop = start
        start = type()
        # ensure start is < stop
        if start > stop:
            start, stop = stop, start
    def validate_range(value:type):
        in_range = start <= value <= stop
        if not in_range:
            if not clamp:
                raise AppealUsageError(f"illegal value {value}, should be {start} <= value < {stop}")
            if value >= stop:
                value = stop
            else:
                value = start
        return value
    return validate_range



def no_arguments_callable(): pass
no_arguments_signature = inspect.signature(no_arguments_callable)





# this function isn't published as one of the _to_converter callables
def simple_type_to_converter(parameter, callable):
    cls = simple_type_signatures.get(callable)
    if not cls:
        return None
    if (callable == bool) and (parameter.kind == KEYWORD_ONLY):
        return BooleanOptionConverter
    return cls

none_and_empty = ((None, empty))
def unannotated_to_converter(parameter):
    if (parameter.annotation in none_and_empty) and (parameter.default in none_and_empty):
        return SimpleTypeConverterStr


def type_to_converter(parameter):
    if not isinstance(parameter.annotation, type):
        return None
    cls = simple_type_to_converter(parameter, parameter.annotation)
    if cls:
        return cls
    if issubclass(parameter.annotation, SingleOption):
        return parameter.annotation
    return None

def callable_to_converter(parameter):
    if (parameter.annotation is empty) or (not builtins.callable(parameter.annotation)):
        return None
    if parameter.kind == KEYWORD_ONLY:
        return Option
    return Converter

illegal_inferred_types = {dict, set, tuple, list}

def inferred_type_to_converter(parameter):
    if (parameter.annotation is not empty) or (parameter.default is empty):
        return None
    inferred_type = type(parameter.default)
    # print(f"inferred_type_to_converter({parameter=})")
    cls = simple_type_to_converter(parameter, inferred_type)
    # print(f"  {inferred_type=} {cls=}")
    if cls:
        return cls
    if issubclass(inferred_type, SingleOption):
        return inferred_type
    if inferred_type in illegal_inferred_types:
        return None
    if parameter.kind == KEYWORD_ONLY:
        return InferredOption
    return InferredConverter

sequence_types = {tuple, list}
def sequence_to_converter(parameter):
    if (parameter.annotation is not empty) or (parameter.default is empty):
        return None
    inferred_type = type(parameter.default)
    if inferred_type not in sequence_types:
        return None
    if parameter.kind == KEYWORD_ONLY:
        return InferredSequenceOption
    return InferredSequenceConverter



def _default_option(option, appeal, callable, parameter_name, annotation, default):
    if appeal.option_signature(option):
        return False
    appeal.option(parameter_name, option, annotation=annotation, default=default)(callable)
    return True


def default_short_option(appeal, callable, parameter_name, annotation, default):
    option = parameter_name_to_short_option(parameter_name)
    if not _default_option(option, appeal, callable, parameter_name, annotation, default):
        raise AppealConfigurationError(f"couldn't add default option {option} for {callable} parameter {parameter_name}")


def default_long_option(appeal, callable, parameter_name, annotation, default):
    if len(parameter_name) < 2:
        return
    option = parameter_name_to_long_option(parameter_name)
    if not _default_option(option,
        appeal, callable, parameter_name, annotation, default):
        raise AppealConfigurationError(f"couldn't add default option {option} for {callable} parameter {parameter_name}")

def default_options(appeal, callable, parameter_name, annotation, default):
    # print(f"default_options({appeal=}, {callable=}, {parameter_name=}, {annotation=}, {default=})")
    added_an_option = False
    options = [parameter_name_to_short_option(parameter_name)]
    if len(parameter_name) > 1:
        options.append(parameter_name_to_long_option(parameter_name))
    for option in options:
        worked = _default_option(option,
            appeal, callable, parameter_name, annotation, default)
        added_an_option = added_an_option or worked
    if not added_an_option:
        raise AppealConfigurationError(f"Couldn't add any default options for {callable} parameter {parameter_name}")


def unbound_callable(callable):
    """
    Unbinds a callable.
    If the callable is bound to an object (a "method"),
    returns the unbound callable.  Otherwise returns callable.
    """
    return callable.__func__ if isinstance(callable, types.MethodType) else callable


event_clock = time.monotonic_ns
event_start = event_clock()

unspecified = object()

class Appeal:
    """
    An Appeal object can only process a single command-line.
    Once you have called main() or process() on an Appeal object,
    you can't call either of those methods again.
    """

    def __init__(self,
        name=None,
        *,
        default_options=default_options,
        repeat=False,
        parent=None,

        option_space_oparg = True,              # '--long OPARG' and '-s OPARG'

        short_option_equals_oparg = True,       # -s=OPARG
        short_option_concatenated_oparg = True, # -sOPARG

        positional_argument_usage_format = "{name}",

        # if true:
        #   * adds a "help" command (if your program supports commands)
        #   * supports lone "-h" and "--help" options which behave like the "help" command without arguments
        help=True,

        # if set to a non-empty string,
        #   * adds a "version" command (if your program has commands)
        #   * supports lone "-v" and "--version" options which behave like the "version" command without arguments
        version=None,

        # when printing docstrings: should Appeal add in missing arguments?
        usage_append_missing_options = True,
        usage_append_missing_arguments = True,

        usage_indent_definitions = 4,

        # when printing docstrings, how should we sort the options and arguments?
        #
        # valid options:
        #    None:     don't change order
        #    "sorted": sort lexigraphically.  note that options sort by the first long option.
        #    "usage":  reorder into the order they appear in usage.
        #
        # note that when sorting, options that appear multiple times will only be shown
        # once.  the second and subsequent appearances will be discarded.
        usage_sort_options = None,
        usage_sort_arguments = None,

        usage_max_columns = 80,

        log_events = bool(want_prints),

        ):
        self.parent = parent
        self.repeat = repeat

        self.name = name

        self.commands = {}
        self._global = None
        self._global_program = None
        self._global_command = None
        self._default = None
        self._default_program = None
        self._default_command = None
        self.full_name = ""
        self.depth = -1

        self.usage_str = self.summary_str = self.doc_str = None

        # in root Appeal instance, self.root == self, self.parent == None
        # in child Appeal instance, self.root != self, self.parent != None (and != self)
        #
        # only accept settings parameters if we're the root Appeal instance
        if parent is None:
            self.root = self

            name = name or os.path.basename(sys.argv[0])
            self.name = self.full_name = name
            self.force_positional = False
            self.parsing_option = 0

            self.default_options = default_options

            self.option_parsing_semantics = (
                option_space_oparg,

                short_option_equals_oparg,
                short_option_concatenated_oparg,
                )

            self.usage_append_missing_options = usage_append_missing_options
            self.usage_append_missing_arguments = usage_append_missing_arguments
            self.usage_sort_options = usage_sort_options
            self.usage_sort_arguments = usage_sort_arguments
            self.usage_max_columns = usage_max_columns
            self.usage_indent_definitions = usage_indent_definitions

            # slightly hacky and limited!  sorry!
            self.positional_argument_usage_format = positional_argument_usage_format.replace("name.upper()", "__NAME__")

            # an "option entry" is:
            #   (option, callable, parameter, annotation, default)
            #
            #    option is the normalized option string
            #    callable is the unbound Python function/method
            #        note that if callable is a bound method object, we store that.
            #        we don't unbind it for this application.
            #    parameter is the string name of the parameter
            #    annotation is the annotation of the parameter (can be "empty")
            #    default is the default value of the parameter  (can be "empty")

            # self.fn_database[callable] = options, parameters, positionals
            # options = { option: option_entry }
            # kw_parameters = {parameter_name: [ option_entry, option_entry2, ...] )
            # positionals = {parameter_name: usage_presentation_name}
            #
            # if option is short option, it's just the single letter (e.g. "-v" -> "v")
            # if option is long option, it's the full string (e.g. "--verbose" -> "--verbose")
            # converter must be either a function or inspect.Parameter.empty
            #
            # You should *set* things in the *local* fn_database.
            # You should *look up* things using fn_database_lookup().
            self.fn_database = collections.defaultdict(lambda: ({}, {}, {}))

            self.support_help = help
            self.support_version = version

            self.program_id = 1

            # How does Appeal turn an inspect.Parameter into a Converter?
            #
            # It used to be simple: Appeal would examine the parameter's
            # callable (annotation), and its default, and use those to
            # produce a "callable" we'll use in a minute:
            #    if there's an annotation, return annotation.
            #    elif there's a default ('default' is neither empty nor None),
            #       return type(default).
            #    else return str.
            # (This function was called analyze_parameter.)
            #
            # Next we analyze the "callable" we just produced:
            #    if callable already a subclass of Converter, instantiate "callable".
            #    if callable is a basic type (str/int/float/complex/bool),
            #       instantiate the appropriate subclass of
            #       SimpleTypeConverter.
            #       (Special case for "bool" when is_option=True.)
            #    else wrap it with Option if it's an option, Converter
            #       if it isn't.
            # (This function was called create_converter.)
            #
            # This worked fine for what Appeal did at the time.  But there
            # was a snazzy new feature I wanted to add:
            #     def foo(a=[1, 2.0])
            # would *infer* that we should consume two command-line arguments,
            # and run int() on the first one and float() on the second.
            # In order to do that, we had a bit of a rewrite.
            #
            # Below we define converter_factories, a first stab at a
            # plugin system.  converter_factories is an iterable of
            # callables; each callable has the signature
            #       foo(callable, default, is_option)
            # The callable should return one of two things: either
            #    * a (proper) subclass of Converter, or
            #    * None.
            #
            # For this to work, we also had to adjust the signature of
            # Converter slightly.
            #
            # First, the constructors had to become consistent.  Every
            # subclass of Converter must strictly define its __init__ thus:
            #    def __init__(self, callable, default, appeal):
            #
            # Second, you now ask the Converter class or instance for the
            # signature.  You can no longer call
            #    inspect.signature(converter_instance.callable)
            #
            # How do you get the signature? two ways.
            #
            # 1) a Converter class must always have a "get_signature" classmethod:
            #       @classmethod
            #       def get_signature(cls, callable, default):
            #    Naturally, that works on classes and instances.
            #       a = ConverterSubclass.get_signature(callable, default)
            # 2) A Converter instance must always have a "signature" attribute.
            #       converter = cls(...)
            #       b = converter.signature
            #
            # (cls.signature may be defined on some Converter subclasses!
            #  But you can't rely on that.)

            self.converter_factories = [
                unannotated_to_converter,
                type_to_converter,
                callable_to_converter,
                inferred_type_to_converter,
                sequence_to_converter,
                ]
        else:
            self.root = self.parent.root

        # self.option_signature_database[option] = [signature, option_entry1, ...]
        #
        # stores the signature of the converter function for
        # this option.  stores an option_entry for each
        # place the option is defined on a converter, though
        # this is only used for error reporting, and we probably
        # only need one.
        #
        # note: is per-Appeal object.
        self.option_signature_database = {}

        self._calculate_full_name()

        self.log_events = log_events
        self.events = []

    def log_event(self, event):
        if not self.log_events:
            return
        self.events.append((event, event_clock()))

    def dump_log(self):
        if not self.log_events:
            return
        def format_time(t):
            seconds = t // 1000000000
            nanoseconds = t - seconds
            return f"[{seconds:02}.{nanoseconds:09}]"

        print()
        print("event log")
        print(f"  elapsed time   per event      event")
        print(f"  -------------- -------------- -------------")

        previous = 0
        for event, t in self.events:
            elapsed = t - event_start
            delta = elapsed - previous
            print(f"  {format_time(elapsed)} {format_time(delta)} {event}")
            previous = elapsed

    def format_positional_parameter(self, name):
        return self.root.positional_argument_usage_format.format(
            name=name, __NAME__=name.upper())

    def _calculate_full_name(self):
        if not self.name:
            return
        names = []
        appeal = self
        while appeal:
            names.append(appeal.name)
            appeal = appeal.parent
        self.full_name = " ".join([name for name in reversed(names)])
        self.depth = len(names) - 1

    def fn_database_lookup(self, callable):
        callable = unbound_callable(callable)
        # appeal = self
        # while appeal:
        #     if name in appeal.fn_database:
        #         return appeal.fn_database[fn]
        #     appeal = appeal.parent
        # raise KeyError, the lazy way
        x = self.root.fn_database[callable]
        # print(f"fn_database_lookup({callable=} -> {x}")
        return x

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.full_name!r} depth={self.depth}>"

    def command(self, name=None):
        a = None
        if name is not None:
            a = self.commands.get(name)
        if not a:
            a = Appeal(name=name, parent=self)
        return a

    def argument(self, parameter, *, usage=None):
        def argument(callable):
            _, _, positionals = self.fn_database_lookup(callable)
            positionals[parameter] = usage
            return callable
        return argument

    def option_signature(self, option):
        """
        Returns the option_signature_database entry for that option.
        if defined, the return value is a list:
            [option_signature, option_entry1, ...]

        The option should be "denormalized", as in, it should be
        passed in how it would appear on the command-line.  e.g.
            '-v'
            '--verbose'
        """
        option = normalize_option(option)
        return self.option_signature_database.get(option)


    def option(self, parameter_name, *options, annotation=empty, default=empty):
        """
        Additional decorator for @command functions.  Explicitly adds
        one or more options mapped to a parameter on the @command function,
        specifying an explicit annotation and/or default value.

        Notes:

        * The parameter must be a keyword-only parameter.

        * If the @command function accepts a **kwargs argument,
          @option can be used to create arguments passed in via **kwargs.

        * The parameters to @option *always override*
          the annotation and default of the original parameter.

        * It may seem like there's no point to the "default" parameter;
          keyword-only parameters must have a default already.  So why
          make the user pass in a "default" here?  Two reasons:
            * The "default" is passed in to Option.init() and may be
              useful there.
            * The user may skip the annotation, in which case the
              annotation will likely be inferred from the default
              (e.g. type(default)).
        """

        if not options:
            raise AppealConfigurationError(f"Appeal.option: no options specified")

        normalized_options = []
        for option in options:
            if not (isinstance(option, str)
                and option.startswith("-")
                and (((len(option) == 2) and option[1].isalnum())
                    or ((len(option) >= 4) and option.startswith("--")))):
                raise AppealConfigurationError(f"Appeal.option: {option!r} is not a legal option")
            normalized = normalize_option(option)
            normalized_options.append((normalized, option))

        parameter = inspect.Parameter(parameter_name, KEYWORD_ONLY, annotation=annotation, default=default)

        # print(f"@option {annotation=} {default=}")
        cls = self.root.map_to_converter(parameter)
        if cls is None:
            raise AppealConfigurationError(f"Appeal.option: could not determine Converter for {annotation=} {default=}")
        annotation_signature = cls.get_signature(parameter)
        # annotation_signature = callable_signature(annotation)

        def option(callable):
            options, kw_parameters, _ = self.fn_database_lookup(callable)
            mappings = kw_parameters.get(parameter_name)
            if mappings is None:
                mappings = kw_parameters[parameter_name] = []

            for option, denormalized_option in normalized_options:
                entry = (option, callable, parameter)
                # option is already normalized, so let's just access the dict directly.
                existing_entry = self.option_signature_database.get(option)
                if existing_entry:
                    existing_signature = existing_entry[0]
                    if annotation_signature != existing_signature:
                        option2, callable2, parameter2, = existing_entry[1]
                        raise AppealConfigurationError(f"{denormalized_option} is already defined on {callable2} parameter {parameter2!r} with a different signature!")
                options[option] = entry
                mappings.append(entry)
                option_signature_entry = [annotation_signature, entry]
                self.option_signature_database[option] = option_signature_entry
            return callable
        return option


    def map_to_converter(self, parameter):
        # print(f"map_to_converter({parameter=})")
        for factory in self.root.converter_factories:
            c = factory(parameter)
            # print(f"  * {factory=} -> {c=}")
            if c:
                break
        return c

    def compute_usage(self, commands=None, override_doc=None):
        #
        # This function is pretty ugly.  The top half is glue code mating the
        # new Charm model to the bottom half; the bottom half is a pile of legacy
        # code written assuming the old Charm model, which is the tip of the iceberg
        # for a whole pile of complex code computing usage.
        #
        # For now this hack job is easier than rewriting the usage code.
        # (Which TBH I'm not 100% sure is the approach I want anyway).
        #

        if self.usage_str:
            return self.usage_str, self.split_summary, self.doc_sections

        if not self._global_program:
            self.analyze()

        callable = self._global
        fn_name = callable.__name__

        formatter = self.root.format_positional_parameter
        usage_str, arguments_values, options_values = charm_usage(self._global_program, formatter=formatter)

        if commands:
            usage_str += formatter("command")

        # {"{command_name}" : "help string"}
        # summary text parsed from docstring on using that command
        commands_definitions = {}

        if commands:
            for name, child in commands.items():
                child.analyze()
                child_usage_str, child_split_summary, child_doc_sections = child.compute_usage()
                commands_definitions[name] = child_split_summary

        # it's a little inconvenient to do this with Charm
        # but we'll give it a go.
        #
        # what we want:
        # build a list of all the functions in the annotations
        # tree underneath our main function, sorted deepest
        # first.
        #
        # however! note that a Charm annotation function always
        # has the same subtree underneath it.  so let's not
        # bother re-creating and re-parsing the same function
        # multiple times.
        #
        # this isn't too hard.  the only complication is that
        # we should use the deepest version of each function.
        # (so we do the max(depth) thing.)
        #
        # step 1:
        # produce a list of annotation functions in the tree
        # underneath us, in deepest-to-shallowest order.

        # signature = callable_signature(callable)
        # positional_children = set()
        # option_children = set()

        # info = [self.callable, signature, 0, positional_children, option_children]
        ci = CharmInterpreter(self._global_program, name=fn_name)

        last_op = None
        option_depth = 0
        programs = {}

        two_lists = lambda: ([], [])
        mapped_options = collections.defaultdict(two_lists)

        for op in ci:
            # print(f"## {op=}")
            if op.op == opcode.create_converter:
                c = {'parameter': op.parameter, 'parameters': {}, 'options': collections.defaultdict(list)}
                ci.converters[op.key] = ci.o = c
                continue

            if op.op == opcode.load_converter:
                ci.converter = ci.converters[op.key]
                continue

            if (op.op == opcode.append_args) and last_op and (last_op.op == opcode.consume_argument):
                ci.converter['parameters'][op.parameter] = op.usage
                continue

            if op.op == opcode.map_option:
                parameter = c['parameter']
                program = op.program

                # def __init__(self, option, program, callable, parameter, key):
                options, full_names = mapped_options[program.id]
                options.append(denormalize_option(op.option))

                full_name = f"{op.parameter.name}"
                full_names.append(full_name)

                converter = ci.converters[op.key]
                option_depth += 1
                ci.call(op.program)
                continue

            if op.op == opcode.end:
                option_depth -= 1
                continue

        children = {}
        values = []
        values_callable_index = {}

        positional_parameter_kinds = set((POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD, VAR_POSITIONAL))

        for c in reversed(ci.converters.values()):
            parameter = c['parameter']
            callable = parameter.annotation

            positional_children = set()
            option_children = set()
            cls = self.root.map_to_converter(parameter)
            signature = cls.get_signature(parameter)
            for p in signature.parameters.values():
                annotation = p.annotation
                cls2 = self.root.map_to_converter(p)
                if not issubclass(cls2, SimpleTypeConverter):
                    if p.kind in positional_parameter_kinds:
                        positional_children.add(annotation)
                    elif p.kind == KEYWORD_ONLY:
                        option_children.add(annotation)
            values_callable_index[callable] = len(values)
            #              callable, signature, depth, positional_children, option_children
            values.append([callable, signature, 0,     positional_children, option_children])
            kids = (positional_children | option_children)
            children[callable] = kids

        # since we iterated over reversed, the last value is c[0]
        # which means callable is already the root of the whole tree
        # do dfs to calculate depths

        def assign_depth(callable, depth):
            value = values[values_callable_index[callable]]
            value[2] = max(value[2], depth)

            for child in children[callable]:
                assign_depth(child, depth + 1)

        assign_depth(callable, 0)

        #
        # Above this line is the glue code mating the new Charm model
        # to the old unmodified code.
        #
        # Below this line is the old unmodified code.
        ################################################################
        #

        values.sort(key=lambda o: o[2], reverse=True)
        if want_prints:
            for current, signature, depth, positional_children, option_children in values:
                if current in simple_type_signatures:
                    continue
                print(f"{current=}\n    {depth=}\n    {positional_children=}\n    {option_children=}\n    {signature=}\n")

        # step 2:
        # process the docstrings of those annotation functions, deepest to shallowest.
        # when we process a function, also merge up from its children.

        fn_to_docs = {}

        if want_prints:
            print(f"[] {arguments_values=}")
            print(f"[] {options_values=}")

        # again! complicated.
        #
        # the "topic" is the thing in the left column in curly braces:
        #   {foo}
        # this literal string "foo" represents a parameter of some kind.  it gets
        # looked up and substituted a couple different ways.
        #
        # First, the literal string is formatted using the "*_topic_names" dict
        # (e.g. options_topic_names), which maps it to a "full name"
        # ("fn_name.parameter_name") which is the internal canonical name
        # for the parameter.  e.g.:
        #     foo      -> "myfn.foo"
        #     myfn.foo -> "myfn.foo"
        # The "*_topic_names" must contain, in order of highest to lowest
        # priority:
        #    the name of the current function: getattrproxy, mapping parameter name to full name
        #    the name of every child function: getattrproxy, mapping that function's parameter names to full names
        #    the name of the parameters of the current function: full name
        #    every unique parameter_name for any child function: full name for that parameter
        #
        # Second, at rendering time, this full name is looked up in "*_topic_values"
        # (e.g. optioncs_topic_values)  to produce the actual value for the left column.
        # so "*_topic_values" doesn't use proxy objects.  it maps full names to what you
        # want presented in the docs for that parameter.
        #
        # Third, the values from the right column are stored in "*_topic_definitions".
        # That dict maps full names to the text of the definition, which is a list of
        # strings representing individual lines.
        #
        # Fourth, in the right column, anything in curly braces is looked up
        # in "all_definitions".  This must contain, in order of highest to lowest
        # priority:
        #    the name of the current function: getattrproxy, mapping parameter name to definition
        #    the name of every child function: getattrproxy, mapping that function's parameter names to definitions
        #    every parameter_name on the current function: definition for that parameter
        #    every unique parameter_name for any child function: definition for that parameter
        #
        # Finally, "*_desired" (e.g. options_desired) is a set of full names of parameters
        # that we want defined in that special section (e.g. options).  If the user hasn't
        # defined one in the current docstring, but they defined one in a child docstring,
        # we'll merge up the child definition and add it.

        for callable, signature, depth, positional_children, option_children in values:
            if callable in simple_type_signatures:
                continue

            # print("_" * 79)
            # print(f"{callable=} {signature=} {depth=} {positional_children=} {positional_children=}")

            fn_name = callable.__name__
            prefix = f"{fn_name}."

            # if callable == self.callable:
            #     doc = self.docstring or ""
            # else:
            #     doc = callable.__doc__ or ""
            doc = callable.__doc__ or ""
            if not doc and callable == self._global and override_doc:
                doc = override_doc
            doc.expandtabs()
            doc = textwrap.dedent(doc)

            arguments_topic_values = {k: v for k, v in arguments_values.items() if k.startswith(prefix)}
            # arguments_and_opargs_topic_values = {k: v for k, v in arguments_values.items() if k.startswith(prefix)}
            options_topic_values = {k: v for k, v in options_values.items() if k.startswith(prefix)}

            arguments_topic_definitions = {}
            # arguments_and_opargs_topic_definitions = {}
            options_topic_definitions = {}

            # merge up all the info from our children
            for child in tuple(positional_children):
                for container, child_container in zip(
                    (
                        arguments_topic_definitions,
                        arguments_topic_values,
                        # arguments_and_opargs_topic_definitions,
                        # arguments_and_opargs_topic_values,
                        options_topic_definitions,
                        options_topic_values,
                        positional_children,
                        option_children,
                    ),
                    fn_to_docs[child]
                    ):
                    container.update(child_container)

            for child in tuple(option_children):
                for container, child_container in zip(
                    (
                        arguments_topic_definitions,
                        arguments_topic_values,
                        # arguments_and_opargs_topic_definitions,
                        # arguments_and_opargs_topic_values,
                        # arguments_and_opargs_topic_definitions,
                        # arguments_and_opargs_topic_values,
                        options_topic_definitions,
                        options_topic_values,
                        option_children,
                        option_children,
                    ),
                    fn_to_docs[child]
                    ):
                    container.update(child_container)

            all_values= {}
            # all_values.update(arguments_and_opargs_topic_values)
            all_values.update(arguments_topic_values)
            all_values.update(options_topic_values)

            arguments_topic_names = {}
            # arguments_and_opargs_topic_names = {}
            options_topic_names = {}
            all_definitions = {}

            for i, (d, values, desired_field) in enumerate((
                (arguments_topic_names, arguments_topic_values, "name"),
                # (arguments_and_opargs_topic_names, arguments_and_opargs_topic_values, "name"),
                (options_topic_names, options_topic_values, "name"),
                (all_definitions, all_values, "value"),
                ), 1):
                # build up proxy dicts
                proxy_dicts = collections.defaultdict(dict)
                for name, value in values.items():
                    before_dot, dot, after_dot = name.partition(".")
                    assert dot
                    desired = value if desired_field == "value" else name
                    proxy_dicts[before_dot][after_dot] = desired

                # print(f">>> pass {i} underlying proxy_dicts")
                # pprint.pprint(proxy_dicts)
                # print()

                # priority 1: fn_name -> proxy
                d.update( {name: DictGetattrProxy(value, name) for name, value in proxy_dicts.items() } )

                # priority 2: parameters of current function
                if fn_name in proxy_dicts:
                    # remove it from proxy dicts to obviate
                    # processing it a second time in priority 3 below
                    parameters = proxy_dicts.pop(fn_name)
                    for name, value in parameters.items():
                        # print(f"priority 2 {name=} {value=}")
                        if name not in d:
                            if desired == "name":
                                value = f"{fn_name}.{name}"
                            d[name] = value

                # priority 3: parameters of all child functions,
                # as long as they don't collide
                discarded = set()
                child_parameters = {}
                for child_name, parameters in proxy_dicts.items():
                    if (name not in d) and (name not in discarded):
                        if name in child_parameters:
                            discarded.add(name)
                            del child_parameters[name]
                            continue
                        if desired == "name":
                            value = f"{fn_name}.{name}"
                        child_parameters[name] = value
                d.update(child_parameters)

            arguments_desired = set(arguments_topic_values)
            options_desired = set(options_topic_values)

            if want_prints:
                print("_"*79)
                l = locals()

                # arguments_and_opargs_topic_names
                # arguments_and_opargs_topic_values
                # arguments_and_opargs_topic_definitions

                for name in """
                    callable

                    arguments_topic_names
                    arguments_topic_values
                    arguments_topic_definitions
                    arguments_desired

                    options_topic_names
                    options_topic_values
                    options_topic_definitions

                    options_desired

                    commands_definitions

                    all_definitions

                    doc
                    """.strip().split():
                    print(f">>> {name}:")
                    pprint.pprint(l[name])
                    print()

            ##
            ## parse docstring
            ##

            arguments_section = SpecialSection("[[arguments]]", arguments_topic_names, arguments_topic_values, arguments_topic_definitions, arguments_desired)
            options_section = SpecialSection("[[options]]", options_topic_names, options_topic_values, options_topic_definitions, options_desired)
            # commands are kind of a degenerate form, we don't reference anything from the converter tree
            command_identity = {k:k for k in commands_definitions}
            commands_section = SpecialSection("[[commands]]", command_identity, command_identity, commands_definitions, set(command_identity))

            special_sections_available = {section.name: section for section in (arguments_section, options_section, commands_section)}
            special_sections_used = {}


            summary = None
            special_section = None
            section = None

            topic = None
            definition = None

            sections = []

            def discard_trailing_empty_lines(l):
                while l and not l[-1]:
                    l.pop()

            def next(new_state, line=None):
                nonlocal state
                # print(f">>>> next state={state.__name__.rpartition('.')[2]} {line=}")
                state = new_state
                if line is not None:
                    state(line)

            def determine_next_section_type(line):
                nonlocal special_section
                nonlocal section
                if (not line) and section:
                    section.append(line)
                    return
                if line in special_sections_used:
                    raise AssertInternalError(f"{self.callable.__name__}: can't use {line} special section twice")
                # [[special section name]] must be at the dedented left column
                if is_special_section(line):
                    finish_section()
                    next(start_special_section, line)
                else:
                    if special_section:
                        finish_section()
                    next(start_body_section, line)

            initial_state = determine_next_section_type

            def start_body_section(line):
                nonlocal section
                if section is None:
                    section = []
                    sections.append(section)
                next(in_body_section, line)

            def in_body_section(line):
                section.append(line.format_map(all_definitions))
                if not line:
                    next(maybe_after_body_section)

            # if we continue the non-special-section,
            # we'll just fall through and continue appending
            # to the current body section.
            def maybe_after_body_section(line):
                if not line:
                    section.append(line)
                else:
                    next(determine_next_section_type, line)

            def finish_body_section():
                nonlocal section
                if section:
                    # discard_trailing_empty_lines(section)
                    section = None

            def is_special_section(line):
                return special_sections_available.get(line)

            def start_special_section(line):
                nonlocal special_section
                special_section = special_sections_available[line]
                sections.append(special_section)
                next(in_special_section)

            def in_special_section(line):
                nonlocal topic
                nonlocal definition

                # [[end]] or [[arguments]] etc
                # must be at the dedented left column
                if line.startswith("[["):
                    # if it's not [[end]], we'll pass it in below
                    if line == "[[end]]":
                        line = None
                    next(determine_next_section_type, line)
                    return

                # topics must be at the (dedented) left column
                topic_line = line.startswith("{") and (not line.startswith("{{"))
                if not (topic or topic_line):
                    raise AppealConfigurationError(f"{self.callable}: docstring section {special_section.name} didn't start with a topic line (one starting with {{parameter/command}})")

                if not topic_line:
                    # definition line
                    lstripped = line.lstrip()
                    if (len(line) - len(lstripped)) < 4:
                        definition.append(lstripped)
                    else:
                        definition.append(line.format_map(all_definitions))
                    return

                # topic line
                key, curly, trailing = line.partition('}')
                assert curly
                key = key + curly
                if self.name:
                    name = self.name
                elif self._global:
                    name = self._global.__name__
                else:
                    name = "(unknown callable)"

                try:
                    topic = key.format_map(special_section.topic_names)
                except KeyError as e:
                    raise AppealConfigurationError(f"{name}: docstring section {special_section.name} has unknown topic {key!r}")
                if topic in special_section.topics_seen:
                    raise AppealConfigurationError(f"{name}: docstring section {special_section.name} topic {key!r} defined twice")
                special_section.topics_seen.add(topic)
                definition = []
                if trailing:
                    trailing = trailing.lstrip().format_map(all_definitions)
                    if trailing:
                        definition.append(trailing)
                special_section.topics[topic] = definition

            def finish_special_section():
                nonlocal special_section

                topics2 = {}
                for topic, definition in special_section.topic_definitions.items():
                    if topic not in special_section.topics_desired:
                        continue
                    if topic not in special_section.topics_seen:
                        topics2[topic] = definition
                for topic, definition in special_section.topics.items():
                    discard_trailing_empty_lines(definition)
                    if not definition:
                        existing_definition = special_section.topic_definitions.get(topic)
                        if existing_definition:
                            definition = existing_definition
                    topics2[topic] = definition
                special_section.topics = topics2
                special_section.topic_definitions.update(topics2)

                special_section = None


            def finish_section():
                nonlocal special_section
                nonlocal section
                if special_section:
                    finish_special_section()
                else:
                    finish_body_section()

            state = initial_state

            for line in doc.split("\n"):
                line = line.rstrip()
                # print(f">> state={state.__name__.rpartition('.')[2]} {line=}")
                state(line)
            finish_section()

            # print("JUST FINISHED.  SECTIONS:")
            # pprint.pprint(sections)

            if sections:
                if isinstance(sections[0], list):
                    first_section = sections[0]

                    # ignore leading blank lines
                    while first_section:
                        if first_section[0]:
                            break
                        first_section.pop(0)

                    # strip off leading non-blank lines for summary
                    summary_lines = []
                    while first_section:
                        if not first_section[0]:
                            break
                        summary_lines.append(first_section.pop(0))

                    # strip leading blank lines
                    while first_section:
                        if first_section[0]:
                            break
                        first_section.pop(0)

                    # print("processed summary:")
                    # print(f"   {summary_lines=}")
                    # print(f"   {first_section=}")

                    split_summary = text.fancy_text_split("\n".join(summary_lines), allow_code=False)

            if want_prints:
                print(f"[] {arguments_topic_names=}")
                print(f"[] {arguments_topic_values=}")
                print(f"[] {arguments_topic_definitions=}")
                # print(f"[] {arguments_and_opargs_topic_names=}")
                # print(f"[] {arguments_and_opargs_topic_values=}")
                # print(f"[] {arguments_and_opargs_topic_definitions=}")
                print(f"[] {arguments_desired=}")
                print(f"[] {options_topic_names=}")
                print(f"[] {options_topic_values=}")
                print(f"[] {options_topic_definitions=}")
                print(f"[] {options_desired=}")

            fn_to_docs[callable] = (
                arguments_topic_definitions,
                arguments_topic_values,
                # arguments_and_opargs_topic_definitions,
                # arguments_and_opargs_topic_values,
                options_topic_definitions,
                options_topic_values,
                positional_children,
                option_children,
                )
            continue

        self.usage_str = usage_str
        self.split_summary = split_summary
        self.doc_sections = sections

        return usage_str, split_summary, sections

    def render_docstring(self, commands=None, override_doc=None):
        """
        returns usage_str, summary_str, doc_str
        """
        if self.doc_str is not None:
            return self.usage_str, self.summary_str, self.doc_str

        usage_str, split_summary, doc_sections = self.compute_usage(commands=commands, override_doc=override_doc)
        # print(f"{doc_sections=}")

        if split_summary:
            summary_str = text.presplit_textwrap(split_summary)
        else:
            summary_str = ""

        # doc
        lines = []
        usage_sections = {}

        # print("\n\n")
        # print("DOC SECTIONS")
        # pprint.pprint(doc_sections)

        for section_number, section in enumerate(doc_sections):
            if not section:
                continue

            if isinstance(section, list):
                # print(f"section #{section_number}: verbatim\n{section!r}\n")
                for line in section:
                    lines.append(line)
                continue

            assert isinstance(section, SpecialSection)
            # print(f"section #{section_number}: special section: {section.name}")
            # pprint.pprint(section.topics)
            # print()

            shortest_topic = math.inf
            longest_topic = -1
            subsections = []
            for topic, definition in section.topics.items():
                topic = section.topic_values[topic]
                shortest_topic = min(shortest_topic, len(topic))
                longest_topic = max(longest_topic, len(topic))
                words = text.fancy_text_split("\n".join(definition))
                subsections.append((topic, words))

            # print(subsections)

            try:
                columns, rows = os.get_terminal_size()
            except OSError:
                rows = 25
                columns = 80
            columns = min(columns, self.root.usage_max_columns)

            column0width = self.root.usage_indent_definitions

            column1width = min((columns // 4) - 4, max(12, longest_topic))
            column1width += 4

            column2width = columns - (column0width + column1width)

            for topic, words in subsections:
                # print("TOPIC", topic, "WORDS", words)
                column0 = ''
                column1 = topic
                column2 = text.presplit_textwrap(words, margin=column2width)
                final = text.merge_columns(
                    (column0, column0width, column0width),
                    (column1, column1width, column1width),
                    (column2, column2width, column2width),
                    )
                # print("FINAL", repr(final))
                lines.append(final)
            # lines.append('')

        doc_str = "\n".join(lines).rstrip()
        # print(f"render_doctstring returning {usage_str=} {summary_str=} {doc_str=}")

        self.summary_str = summary_str
        self.doc_str = doc_str

        return usage_str, summary_str, doc_str

    def usage(self, *, usage=False, summary=False, doc=False):
        # print(f"yoooo sage: {self} {self._global}")
        if self._global:
            docstring = self._global.__doc__
        else:
            def no_op(): pass
            self._global = no_op
            docstring = ""
        self.analyze()
        # print(f"FOO-USAGE {self._global=} {self._global_program=}")
        # usage_str = charm_usage(self._global_program)
        # print(self.name, usage_str)
        # return
        if not docstring:
            docstring = []
            # if self._global_command.args_converters:
            #     docstring.append("Arguments:\n\n[[arguments]]\n[[end]]\n")
            # if self._global_command.kwargs_converters:
            #     docstring.append("Options:\n\n[[options]]\n[[end]]\n")
            if self.commands:
                docstring.append("Commands:\n\n[[commands]]\n[[end]]\n")
            docstring = "\n".join(docstring).rstrip()
            # self._global_command.docstring = docstring
            # print(f"self._global_command.docstring = {docstring!r}")
            # print(f"{self.commands=}")
        usage_str, summary_str, doc_str = self.render_docstring(commands=self.commands, override_doc=docstring)
        if want_prints:
            print(f">> usage from {self}:")
            print(">> usage")
            print(usage_str)
            print(">> summary")
            print(summary_str)
            print(">> doc")
            print(doc_str)
        spacer = False
        if usage:
            print("usage:", self.full_name, usage_str)
            spacer = True
        if summary and summary_str:
            if spacer:
                print()
            print(summary_str)
            spacer = True
        if doc and doc_str:
            if spacer:
                print()
            print(doc_str)

    def error(self, s):
        raise AppealUsageError("error: " + s)
        print("error:", s)
        print()
        return self.usage(usage=True, summary=True, doc=True)

    def version(self):
        print(self.support_version)

    def help(self, *command):
        """
        Print help on a thingy.

        Prints lots and lots of help.
        """
        commands = " ".join(command)
        appeal = self
        for name in command:
            appeal = appeal.commands.get(name)
            if not appeal:
                raise AppealUsageError(f'"{name}" is not a legal command.')
        appeal.usage(usage=True, summary=True, doc=True)

    def __call__(self, callable):
        assert callable and builtins.callable(callable)
        self._global = callable
        if self.root != self:
            if self.name is None:
                self.name = callable.__name__
                self._calculate_full_name()
            self.parent.commands[self.name] = self
        return callable

    def global_command(self):
        if self.root != self:
            raise AppealConfigurationError("only the root Appeal instance can have a global command")
        return self.__call__

    def default_command(self):
        def closure(callable):
            assert callable and builtins.callable(callable)
            self._default = callable
            return callable
        return closure

    def _analyze_attribute(self, name):
        if not getattr(self, name):
            return None
        program_attr = name + "_program"
        program = getattr(self, program_attr)
        if not program:
            callable = getattr(self, name)
            program = charm_compile(self, callable)
            if want_prints:
                print()
            setattr(self, program_attr, program)
            # print(f"compiled program for {name}, {program}")
        return program

    def analyze(self):
        self._analyze_attribute("_global")

    def _parse_attribute(self, name, argi, commands):
        program = self._analyze_attribute(name)
        if not program:
            return None
        if want_prints:
            charm_print(program)
        converter = charm_parse(self, program, argi)
        commands.append(converter)
        return converter

    def parse(self, argi, commands):
        self._parse_attribute("_global", argi, commands)

        if not argi:
            # if there are no arguments waiting here,
            # then they didn't want to run a command.
            # if any commands are defined, and they didn't specify one,
            # if there's a default command, run it.
            # otherwise, that's an error.
            default_converter = self._parse_attribute("_default", argi, commands)
            if (not default_converter) and self.commands:
                raise AppealUsageError("no command specified.")
            return

        if self.commands:
            # okay, we have arguments waiting, and there are commands defined.
            for command_name in argi:
                sub_appeal = self.commands.get(command_name)
                if not sub_appeal:
                    # partial spelling check would go here, e.g. "sta" being short for "status"
                    self.error(f"unknown command {command_name}")
                # don't append! just parse.
                # the recursive Appeal.parse call will append.
                sub_appeal.analyze()
                sub_appeal.parse(argi, commands)
                if not (self.repeat and argi):
                    break

        if argi:
            leftovers = " ".join(shlex.quote(s) for s in argi)
            raise AppealUsageError(f"leftover cmdline arguments! {leftovers!r}")

    def ___parse(self, argi, commands):

        if self._global_command:
            append_and_parse(self._global_command)

        # okay, we have arguments waiting.
        # (if we don't have commands, then idk what to do with this.)
        if self.commands:
            # okay, we have arguments waiting, and there are commands defined.
            while argi:
                command_name = next(argi)
                sub_appeal = self.commands.get(command_name)
                if not sub_appeal:
                    # partial spelling check would go here, e.g. "sta" being short for "status"
                    self.error(f"unknown command {command_name}")
                # don't append! just parse.
                # the recursive Appeal.parse call will append.
                sub_appeal.analyze()
                sub_appeal.parse(argi, commands)
                if not (self.repeat and argi):
                    break

        if argi:
            leftovers = " ".join(shlex.quote(s) for s in argi)
            raise AppealUsageError(f"leftover cmdline arguments! {leftovers!r}")

    def convert(self, commands):
        for command in commands:
            command.convert()

    def execute(self, commands):
        result = None
        for command in commands:
            result = command.execute()
            if result:
                break
        return result

    def process(self, args=None):
        self.log_event("process start")
        if args is None:
            args = sys.argv[1:]

        if self.support_version:
            if (len(args) == 1) and args[0] in ("-v", "--version"):
                return self.version()
            if self.commands and (not "version" in self.commands):
                self.command()(self.version)

        if self.support_help:
            if (len(args) == 1) and args[0] in ("-h", "--help"):
                return self.help()
            if self.commands and (not "help" in self.commands):
                self.command()(self.help)

        self.log_event("analyze start")
        self.analyze()

        self.log_event("parse start")
        argi = PushbackIterator(args)
        commands = []
        self.parse(argi, commands)

        self.log_event("convert start")
        self.convert(commands)

        self.log_event("execute start")
        result = self.execute(commands)
        self.dump_log()
        return result


    def main(self, args=None):
        self.log_event("main start")
        try:
            sys.exit(self.process(args))
        except AppealUsageError as e:
            print("Error:", str(e))
            self.usage(usage=True)
            sys.exit(-1)

