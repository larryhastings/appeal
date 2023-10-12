#!/usr/bin/env python3

"A powerful & Pythonic command-line parsing library.  Give your program Appeal!"
__version__ = "0.6.2"


# please leave this copyright notice in binary distributions.
license = """
appeal/__init__.py
part of the Appeal software package
Copyright 2021-2023 by Larry Hastings
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

# run appeal/want_prints.py to toggle debug prints on and off
want_prints = 0


from abc import abstractmethod, ABCMeta
import base64
import big.all as big
from big.itertools import PushbackIterator
import builtins
import collections.abc
from collections.abc import Iterable, Iterator, Mapping
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

from collections import defaultdict

try:
    from typing import Annotated
    AnnotatedType = type(Annotated[int, str])
    del Annotated
    def dereference_annotated(annotation):
        if isinstance(annotation, AnnotatedType):
            return annotation.__metadata__[-1]
        return annotation
except ImportError:
    def dereference_annotated(annotation):
        return annotation

from . import argument_grouping
from . import text

reversed_dict_values = argument_grouping.reversed_dict_values


POSITIONAL_ONLY = inspect.Parameter.POSITIONAL_ONLY
POSITIONAL_OR_KEYWORD = inspect.Parameter.POSITIONAL_OR_KEYWORD
VAR_POSITIONAL = inspect.Parameter.VAR_POSITIONAL
KEYWORD_ONLY = inspect.Parameter.KEYWORD_ONLY
VAR_KEYWORD = inspect.Parameter.VAR_KEYWORD
empty = inspect.Parameter.empty


# new in 3.8
shlex_join = getattr(shlex, 'join', None)
if not shlex_join:
    # note: this doesn't have to be bullet-proof,
    # we only use it for debug print statements.
    def shlex_join(split_command):
        quoted = []
        for s in split_command:
            fields = s.split()
            if len(fields) > 1:
                s = repr(s)
            quoted.append(s)
        return " ".join(quoted)


def update_wrapper(wrapped, wrapper):
    """
    update_wrapper() adds a '__wrapped__'
    attribute.  inspect.signature() then
    follows that attribute, which means it
    returns the wrong (original) signature
    for partial objects if we call
    update_wrapper on them.

    I don't need the __wrapped__ attribute for
    anything, so for now I just remove them.

    I filed an issue to ask about this:
        https://bugs.python.org/issue46761
    """
    functools.update_wrapper(wrapped, wrapper)
    if hasattr(wrapped, '__wrapped__'):
        delattr(wrapped, '__wrapped__')
    return wrapped



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

class ConfigurationError(AppealBaseException):
    """
    Raised when the Appeal API is used improperly.
    """
    pass


class UsageError(AppealBaseException):
    """
    Raised when Appeal processes an invalid command-line.
    """
    pass

class CommandError(AppealBaseException):
    """
    Raised when an Appeal command function returns a
    result indicating an error.
    """
    pass

# old names
AppealConfigurationError = ConfigurationError
AppealUsageError = UsageError
AppealCommandError = CommandError


class Preparer:
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
# this just adds a field we can check for, and if we find
# it we throw a helpful exception so the user can fix it.
def must_be_instance(callable):
    callable.__appeal_must_be_instance__ = True
    return callable


def is_legal_annotation(annotation):
    if getattr(annotation, "__appeal_must_be_instance__", False):
        result = not isinstance(annotation, types.FunctionType)
        return result
    return True


def _partial_rebind(partial, placeholder, instance, method):
    stack = []
    rebind = False

    if not isinstance(partial, functools.partial):
        raise ValueError("partial is not a functools.partial object")
    while isinstance(partial, functools.partial):
        stack.append(partial)
        func = partial = partial.func
    counter = 0
    while stack:
        counter += 1
        # print(f"*** {counter} stack={stack}\n*** partial={partial}")
        partial = stack.pop()
        if (   (len(partial.args) == 1)
            and (partial.args[0] == placeholder)
            and (not len(partial.keywords))):
                # if we try to use getattr, but it fails,
                # fail over to a functools partial
                use_getattr = method and (not counter)
                if use_getattr:
                    # print(f"*** using getattr method")
                    func2 = getattr(instance, func.__name__, None)
                    use_getattr = func2 is not None
                if not use_getattr:
                    # print(f"*** using new partial method")
                    func2 = functools.partial(func, instance)
                    update_wrapper(func2, func)
                    func = func2
                # print(f"*** func is now {func}")
                partial = func
                continue
        # print(f"*** partial.func={partial.func} != func={func} == rebind={rebind}")
        if partial.func != func:
            partial = functools.partial(func, *partial.args, **partial.keywords)
            update_wrapper(partial, func)

        func = partial
    # print(f"*** returning {partial!r}\n")
    return partial


def partial_rebind_method(partial, placeholder, instance):
    """
    Binds an unbound method curried with a placeholder
    object to an instance and returns the bound method.

    All these statements must be true:
        * "partial" must be a functools.partial() object
          with exactly one curried positional argument
          and zero curried keyword arguments.
        * The one curried positional argument must be
          equal to "placeholder".

    If any of those statements are false, raises ValueError.

    If all those statements are true, this function:
        * extracts the callable from the partial,
        * uses getattr(instance, callable.__name__) to
          bind callable to the instance.
    """
    return _partial_rebind(partial, placeholder, instance, True)

def partial_rebind_positional(partial, placeholder, instance):
    """
    Replaces the first positional argument of a
    functools.partial object with a different argument.

    All these statements must be true:
        * "partial" must be a functools.partial() object
          with exactly one curried positional argument
          and zero curried keyword arguments.
        * The one curried positional argument must be
          equal to "placeholder".

    If any of those statements are false, raises ValueError.

    If all those statements are true, this function:
        * extracts the callable from the partial,
        * uses getattr(instance, callable.__name__) to
          bind callable to instance.
    """
    return _partial_rebind(partial, placeholder, instance, False)


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
##    program
##        the program currently being run.
##    ip
##        the instruction pointer.  an integer, indexes into "program".
##    converters
##        a dict mapping converter "keys" to converters.
##        a converter "key" is any hashable; conceptually a
##        converter key represents a specific instance of a
##        converter being used in the annotation tree.
##        (if you have two parameters annotated with int_float,
##        these two instances get different converter keys.)
##    converter
##        the current converter context.  a reference to a converter (or None).
##        conceptually an indirect register like SP or a segment register.
##        you index through it to interact with the attributes of a converter,
##        specifically:
##              args
##                positional arguments, accessed with an index (-1 permitted).
##              kwargs
##                keyword-only arguments, accessed by name.
##          you can directly store str arguments in these attributes.
##          or, create converters and store (and possibly later retrieve)
##          converter objects in these attributes.
##    o
##        a general-purpose register.
##        a reference to a converter, a string, or None.
##    flag
##        a boolean register.
##        contains the result of o_is_* instructions.
##    group
##        argument counter object (or None).
##        local argument counts just for this argument group.
##    iterator
##        An iterator yielding objects (e.g. strings from the command-line).
##        You can get the next object using next_to_o.
##        There's also an iterator_stack, and you can push new iterators
##        (e.g. to iterate over a list from a Perky file).
##    mapping
##        A dict-like object mapping strings to value.
##        You can get a value with lookup_to_o.
##
## the interpreter has a stack.  it's used to push/pop registers
## when making a "call".


## argument counter objects have these fields:
##    count = how many arguments we've added to this group
##    minimum = the minimum "arguments" needed
##    maximum = the maximum "arguments" permissible
##    optional = flag, is this an optional group?
##    laden = flag, has anything

def serial_number_generator(*, prefix='', width=0, tuple=False):
    """
    Flexible serial number generator.
    """

    i = 1
    # yield prefix + base64.b32hexencode(i.to_bytes(5, 'big')).decode('ascii').lower().lstrip('0').rjust(3, '0')

    if tuple:
        # if prefix isn't a conventional iterable
        if not isinstance(prefix, (builtins.tuple, list)):
            while True:
                # yield 2-tuple
                yield (prefix, i)
                i += 1
        # yield n-tuple starting with prefix and appending i
        prefix = list(prefix)
        prefix.append(0)
        while True:
            prefix[-1] = i
            yield tuple(prefix)
            i += 1

    if width:
        while True:
            yield f"{prefix}{i:0{width}}"
            i += 1

    while True:
        yield f"{prefix}{i}"
        i += 1

class ArgumentGroup:
    next_serial_number = serial_number_generator(prefix="ag-").__next__

    def __init__(self, *, id=None, optional=True):
        if id is None:
            id = ArgumentGroup.next_serial_number()
        self.id = id
        self.optional = optional
        self.minimum = self.maximum = self.count = 0
        # a flag you should set when you trigger
        # an option in this group
        self.laden = False

    def satisfied(self):
        if self.optional and (not (self.laden or self.count)):
            return True
        return self.minimum <= self.count <= self.maximum

    def __repr__(self):
        return f"<ArgumentGroup {self.id} optional={self.optional} laden={self.laden} minimum {self.minimum} <= count {self.count} <= maximum {self.maximum} == {bool(self)}>"

    def copy(self):
        o = ArgumentGroup(id=self.id, optional=self.optional)
        o.minimum = self.minimum
        o.maximum = self.maximum
        o.count = self.count
        o.laden = self.laden
        return o

    def summary(self):
        satisfied = "yes" if self.satisfied() else "no "
        optional = "yes" if self.optional else "no "
        laden = "yes" if self.laden else "no "
        return f"['{self.id}' satisfied {satisfied} | optional {optional} | laden {laden} | min {self.minimum} <= cur {self.count} <= max {self.maximum}]"



"""
# cpp

# This is a preprocessor block.
# This Python code prints out the opcode enum.

def print_enum(names, i=0):
    for name in names.split():
        print(f"    {name.strip()} = {i}")
        i += 1

print('class opcode(enum.Enum):')

print_enum('''
    invalid
    end
    abort
    jump
    indirect_jump
    branch_on_flag
    branch_on_not_flag
    literal_to_o
    wrap_o_with_iterator
    test_is_o_true
    test_is_o_none
    test_is_o_empty
    test_is_o_iterable
    test_is_o_mapping
    test_is_o_str_or_bytes
    call
    create_converter
    load_converter
    load_o
    converter_to_o
    push_o
    pop_o
    peek_o
    push_flag
    pop_flag
    push_mapping
    pop_mapping
    push_iterator
    pushback_o_to_iterator
    pop_iterator
    append_to_converter_args
    set_in_converter_kwargs
    map_option
    next_to_o
    lookup_to_o
    flush_multioption
    remember_converters
    forget_converters
    set_group

''')

print('''
    # these are removed by the peephole optimizer.
    # the interpreter never sees them.
    # (well... unless you leave in comments during debugging.)
''')

print_enum('''
    no_op
    comment
    label
    jump_to_label
    branch_on_flag_to_label
    branch_on_not_flag_to_label
    label_to_o
''', i=200)

print()

"""

# Don't modify this stuff directly!
# Everything from here to the
#         # cpp
# line below is generated.
#
# Modify the code in the quotes above and run
#         % python3 cpp.py appeal/__init__.py
# to regenerate.

class opcode(enum.Enum):
    invalid = 0
    end = 1
    abort = 2
    jump = 3
    indirect_jump = 4
    branch_on_flag = 5
    branch_on_not_flag = 6
    literal_to_o = 7
    wrap_o_with_iterator = 8
    test_is_o_true = 9
    test_is_o_none = 10
    test_is_o_empty = 11
    test_is_o_iterable = 12
    test_is_o_mapping = 13
    test_is_o_str_or_bytes = 14
    call = 15
    create_converter = 16
    load_converter = 17
    load_o = 18
    converter_to_o = 19
    push_o = 20
    pop_o = 21
    peek_o = 22
    push_flag = 23
    pop_flag = 24
    push_mapping = 25
    pop_mapping = 26
    push_iterator = 27
    pushback_o_to_iterator = 28
    pop_iterator = 29
    append_to_converter_args = 30
    set_in_converter_kwargs = 31
    map_option = 32
    next_to_o = 33
    lookup_to_o = 34
    flush_multioption = 35
    remember_converters = 36
    forget_converters = 37
    set_group = 38

    # these are removed by the peephole optimizer.
    # the interpreter never sees them.
    # (well... unless you leave in comments during debugging.)

    no_op = 200
    comment = 201
    label = 202
    jump_to_label = 203
    branch_on_flag_to_label = 204
    branch_on_not_flag_to_label = 205
    label_to_o = 206

# cpp


class CharmInstruction:
    __slots__ = ['op']

    def copy(self):
        kwargs = {attr: getattr(self, attr) for attr in dir(self) if not (attr.startswith("_") or (attr in ("copy", "op"))) }
        return self.__class__(**kwargs)



class CharmInstructionComment(CharmInstruction):
    __slots__ = ['comment']

    def __init__(self, comment):
        self.op = opcode.comment
        self.comment = comment

    def __repr__(self):
        return f"<comment {self.comment!r}>"


class CharmInstructionNoOp(CharmInstruction): # CharmInstructionNoArgBase

    def __init__(self):
        self.op = opcode.no_op

    def __repr__(self):
        return f"<no_op>"


class CharmInstructionEnd(CharmInstruction):
    """
    end

    Exits the current program.
    """

    __slots__ = ['name', 'id']

    def __init__(self):
        self.op = opcode.end

    def __repr__(self):
        return f"<end>"


class CharmInstructionAbort(CharmInstruction): # CharmInstructionNoArgBase
    """
    abort

    Aborts processing with an error.
    """

    __slots__ = ['message']

    def __init__(self, message):
        self.op = opcode.abort
        self.message = message

    def __repr__(self):
        return f"<abort>"


class CharmInstructionJump(CharmInstruction): # CharmInstructionAddressBase
    """
    jump <address>

    Sets the 'ip' register to <address>.
    <address> is an integer.
    """

    __slots__ = ['address']

    def __init__(self, address):
        self.op = opcode.jump
        self.address = address

    def __repr__(self):
        return f"<jump address={self.address}>"


class CharmInstructionIndirectJump(CharmInstruction): # CharmInstructionAddressBase
    """
    indirect_jump

    Sets the 'ip' register to the value in the 'o' register.
    The value must be an integer.
    """

    def __init__(self):
        self.op = opcode.indirect_jump

    def __repr__(self):
        return f"<indirect_jump>"


class CharmInstructionBranchOnFlag(CharmInstruction): # CharmInstructionAddressBase
    """
    branch_on_flag <address>

    If the 'flag' register is True,
    sets the 'ip' register to <address>.
    <address> is an integer.
    """

    __slots__ = ['address']

    def __init__(self, address):
        self.op = opcode.branch_on_flag
        self.address = address

    def __repr__(self):
        return f"<branch_on_flag address={self.address}>"

class CharmInstructionBranchOnNotFlag(CharmInstruction): # CharmInstructionAddressBase
    """
    branch_on_not_flag <address>

    If the 'flag' register is False,
    sets the 'ip' register to <address>.
    <address> is an integer.
    """

    __slots__ = ['address']

    def __init__(self, address):
        self.op = opcode.branch_on_not_flag
        self.address = address

    def __repr__(self):
        return f"<branch_on_not_flag address={self.address}>"

class CharmInstructionLiteralToO(CharmInstruction): # CharmInstructionAddressBase
    """
    literal_to_o

    Sets the 'o' register to a precompiled literal value.
    """

    __slots__ = ['value']

    def __init__(self, value):
        self.op = opcode.literal_to_o
        self.value = value

    def __repr__(self):
        return f"<literal_to_o value={repr(self.value)}>"

class CharmInstructionWrapOWithIterator(CharmInstruction): # CharmInstructionAddressBase
    """
    wrap_o_with_iterator

    Replaces the value in the 'o' register with an iterator
    which yields that value.

    In other words:
        o = iter( (o,) )
    """

    def __init__(self):
        self.op = opcode.wrap_o_with_iterator

    def __repr__(self):
        return f"<wrap_o_with_iterator>"

class CharmInstructionTestIsOTrue(CharmInstruction): # CharmInstructionNoArgBase
    """
    test_is_o_true

    If the 'o' register contains a true value,
    set the 'flag' register to True,
    otherwise set the 'flag' register to False.

    In other words:
        flag = bool(o)
    """

    def __init__(self):
        self.op = opcode.test_is_o_true

    def __repr__(self):
        return f"<test_is_o_true>"

class CharmInstructionTestIsONone(CharmInstruction): # CharmInstructionNoArgBase
    """
    test_is_o_none

    If the 'o' register contains None,
    set the 'flag' register to True,
    otherwise set the 'flag' register to False.

    In other words:
        flag = o == None
    """

    def __init__(self):
        self.op = opcode.test_is_o_none

    def __repr__(self):
        return f"<test_is_o_none>"

class CharmInstructionTestIsOEmpty(CharmInstruction): # CharmInstructionNoArgBase
    """
    test_is_o_empty

    If the 'o' register contains inspect.Parameter.Empty,
    set the 'flag' register to True,
    otherwise set the 'flag' register to False.

    In other words:
        flag = o == inspect.Parameter.empty
    """

    def __init__(self):
        self.op = opcode.test_is_o_empty

    def __repr__(self):
        return f"<test_is_o_empty>"

class CharmInstructionTestIsOIterable(CharmInstruction): # CharmInstructionNoArgBase
    """
    test_is_o_iterable

    If the 'o' register contains an instance
    of an collections.abc.Iterable object, set the 'flag'
    register to True, otherwise set it to False.

    In other words:
        flag = isinstance(o, collections.abc.Iterable)

    Note that str objects and most Mapping objects (e.g. dict)
    are iterable.  You may want to test for those first.
    """

    def __init__(self):
        self.op = opcode.test_is_o_iterable

    def __repr__(self):
        return f"<test_is_o_iterable>"

class CharmInstructionTestIsOMapping(CharmInstruction): # CharmInstructionNoArgBase
    """
    test_is_o_mapping

    If the 'o' register contains an instance
    of an collections.abc.Mapping object, set the 'flag'
    register to True, otherwise set it to False.

    In other words:
        flag = isinstance(o, collections.abc.Mapping)
    """

    def __init__(self):
        self.op = opcode.test_is_o_mapping

    def __repr__(self):
        return f"<test_is_o_mapping>"

class CharmInstructionTestIsOStrOrBytes(CharmInstruction): # CharmInstructionNoArgBase
    """
    test_is_o_str_or_bytes

    If the 'o' register contains an instance
    of a str or bytes object, set the 'flag'
    register to True, otherwise set it to False.

    In other words:
        flag = isinstance(o, (str, bytes))
    """

    def __init__(self):
        self.op = opcode.test_is_o_str_or_bytes

    def __repr__(self):
        return f"<test_is_o_str_or_bytes>"

next_label_id = serial_number_generator(prefix='label-').__next__

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
    __slots__ = ['id', 'label']

    def __init__(self, label):
        self.op = opcode.label
        self.id = next_label_id()
        self.label = label

    def __repr__(self):
        label = f" label={self.label!r}" if self.label else ""
        return f"<label id={self.id}{label}>"

    def __hash__(self):
        return hash(self.id)

class CharmInstructionJumpToLabel(CharmInstruction): # CharmInstructionLabelBase
    """
    jump_to_label <label>

    Sets the 'ip' register to point to the instruction
    after the instance of the <label> instruction in the
    current program.

    label and *_to_label are both pseudo-instructions.
    They're removed by a pass in the peephole optimizer.
    """

    __slots__ = ['label']

    def __init__(self, label):
        self.op = opcode.jump_to_label
        self.label = label

    def __repr__(self):
        label = f" label={self.label!r}" if self.label else ""
        return f"<jump_to_label{label}>"


class CharmInstructionBranchOnFlagToLabel(CharmInstruction):
    """
    branch_on_flag_to_label <label>

    If the 'flag' register is True,
    sets the 'ip' register to point to the instruction
    after the instance of the <label> instruction in the
    current program.

    label and *_to_label are both pseudo-instructions.
    They're removed by a pass in the peephole optimizer.
    """

    __slots__ = ['label']

    def __init__(self, label):
        self.op = opcode.branch_on_flag_to_label
        self.label = label

    def __repr__(self):
        label = f" label={self.label!r}" if self.label else ""
        return f"<branch_on_flag_to_label {label}>"


class CharmInstructionBranchOnNotFlagToLabel(CharmInstruction):
    """
    branch_on_not_flag_to_label <label>

    If the 'flag' register is False,
    sets the 'ip' register to point to the instruction
    after the instance of the <label> instruction in the
    current program.

    label and *_to_label are both pseudo-instructions.
    They're removed by a pass in the peephole optimizer.
    """

    __slots__ = ['label']

    def __init__(self, label):
        self.op = opcode.branch_on_not_flag_to_label
        self.label = label

    def __repr__(self):
        label = f" label={self.label!r}" if self.label else ""
        return f"<branch_on_not_flag_to_label {label}>"

class CharmInstructionLabelToO(CharmInstruction): # CharmInstructionAddressBase
    """
    label_to_o

    Sets the 'o' register to the address of a label.
    Is converted by the assembler into a literal_to_o instruction.
    """

    __slots__ = ['label']

    def __init__(self, label):
        self.op = opcode.label_to_o
        self.label = label

    def __repr__(self):
        label = f" label={self.label!r}" if self.label else ""
        return f"<label_to_o {label}>"

class CharmInstructionCreateConverter(CharmInstruction):
    """
    create_converter <parameter> <key>

    Creates a Converter object using <parameter>,
    an inspect.Parameter object.

    Stores the resulting converter object
    in 'converters[key]' and in the 'o' register.
    """
    __slots__ = ['parameter', 'key']

    def __init__(self, parameter, key):
        self.op = opcode.create_converter
        self.parameter = parameter
        self.key = key

    def __repr__(self):
        return f"<create_converter parameter={self.parameter!r} key={self.key}>"

class CharmInstructionLoadConverter(CharmInstruction): # CharmInstructionKeyBase
    """
    load_converter <key>

    Loads a Converter object from 'converters[key]' and
    stores a reference in the 'converter' register.
    """

    __slots__ = ['key']

    def __init__(self, key):
        self.op = opcode.load_converter
        self.key = key

    def __repr__(self):
        return f"<load_converter key={self.key}>"


class CharmInstructionLoadO(CharmInstruction): # CharmInstructionKeyBase
    """
    load_o <key>

    Loads a Converter object from 'converters[key]' and
    stores a reference in the 'o' register.
    """
    __slots__ = ['key']

    def __init__(self, key):
        self.op = opcode.load_o
        self.key = key

    def __repr__(self):
        return f"<load_o key={self.key}>"

class CharmInstructionConverterToO(CharmInstruction): # CharmInstructionKeyBase
    """
    converter_to_o

    Sets the 'o' register to the contents of the 'converter'
    register.
    """
    def __init__(self):
        self.op = opcode.converter_to_o

    def __repr__(self):
        return f"<converter_to_o>"

class CharmInstructionAppendToConverterArgs(CharmInstruction):
    """
    append_to_converter_args <parameter> <discretionary> <usage>

    Takes a reference to the value in the 'o' register
    and appends it to 'converter.args'.

    <parameter> is an inspect.Parameter representing
    the positional parameter being filled here.
    It's used to generate usage.

    <discretionary> is a boolean value.
    If True, this argument may or may not be used,
    depending on what values we process at runtime.
    If False, this argument is mandatory.

    <usage> is a 2-tuple:
        (usage_full_name, usage_name)

        usage_full_name is a string of the form:
            "{callable}.{parameter_name}"
        This is the actual name of the parameter from
        the actual callable.

        usage_name is a string, the name of the
        parameter as it should appear in usage documentation.
    """

    __slots__ = ['parameter', 'discretionary', 'usage']

    def __init__(self, parameter, usage, discretionary):
        self.op = opcode.append_to_converter_args
        self.parameter = parameter
        self.usage = usage
        self.discretionary = discretionary

    def __repr__(self):
        return f"<append_to_converter_args parameter={self.parameter} usage={self.usage} discretionary={self.discretionary}>"

class CharmInstructionSetInConverterKwargs(CharmInstruction):
    """
    set_in_converter_kwargs <name>

    Takes a reference to the object currently in
    the 'o' register and stores it in 'converter.kwargs[<name>]'.
    (Here 'converter' is the 'converter' register.)

    <parameter> and <usage> are the same as for
    CharmInstructionAppendToConverterArgs.
    """

    __slots__ = ['parameter', 'usage']

    def __init__(self, parameter, usage):
        self.op = opcode.set_in_converter_kwargs
        self.parameter = parameter
        self.usage = usage

    def __repr__(self):
        return f"<set_in_converter_kwargs parameter={self.parameter} usage={self.usage}>"

class CharmInstructionPushO(CharmInstruction):
    """
    push_o

    Pushes the value currently in the 'o' register
    onto to the 'data' stack.
    """

    def __init__(self):
        self.op = opcode.push_o

    def __repr__(self):
        return f"<push_o>"

class CharmInstructionPopO(CharmInstruction):
    """
    pop_o

    Pops the top value from the 'data' stack and
    sets it as the value of 'o''.

    If the 'data' stack is empty,
    you must abort processing and produce an error.
    """

    def __init__(self):
        self.op = opcode.pop_o

    def __repr__(self):
        return f"<pop_o>"

class CharmInstructionPeekO(CharmInstruction):
    """
    peek_o

    Gets the top value from the 'data' stack (without popping)
    and sets it as the value of 'o'.

    If the 'data' stack is empty,
    you must abort processing and produce an error.
    """

    def __init__(self):
        self.op = opcode.peek_o

    def __repr__(self):
        return f"<peek_o>"

class CharmInstructionPushFlag(CharmInstruction):
    """
    push_o

    Pushes the value currently in the 'flag' register
    onto to the 'data' stack.
    """

    def __init__(self):
        self.op = opcode.push_flag

    def __repr__(self):
        return f"<push_flag>"

class CharmInstructionPopFlag(CharmInstruction):
    """
    pop_o

    Pops the top value from the 'data' stack and
    sets it as the value of 'flag'.

    If the 'data' stack is empty,
    you must abort processing and produce an error.
    """

    def __init__(self):
        self.op = opcode.pop_flag

    def __repr__(self):
        return f"<pop_flag>"

class CharmInstructionPushMapping(CharmInstruction):
    """
    push_mapping

    Pushes the current mapping dict onto the mapping
    stack, then takes a reference to the object currently
    in the 'o' register and sets it as the new mapping dict.

    If 'o' is not isinstance(o, collections.abc.Mapping)
    you must abort processing and produce an error.
    """

    def __init__(self):
        self.op = opcode.push_mapping

    def __repr__(self):
        return f"<push_mapping>"

class CharmInstructionPopMapping(CharmInstruction):
    """
    pop_mapping

    Pops the top value from the mapping stack and
    sets it as the current mapping dict, overwriting
    the reference to the current mapping dict.

    If the mapping stack is empty,
    you must abort processing and produce an error.
    """

    def __init__(self):
        self.op = opcode.pop_mapping

    def __repr__(self):
        return f"<pop_mapping>"

class CharmInstructionPushIterator(CharmInstruction):
    """
    push_iterator

    Pushes the current iterator onto the iterator stack,
    then takes a reference to the object currently in the
    'o' register and sets it as the new iterator.

    If 'o' is not isinstance(o, collections.abc.Iterator)
    you must abort processing and produce an error.
    """

    def __init__(self):
        self.op = opcode.push_iterator

    def __repr__(self):
        return f"<push_iterator>"

class CharmInstructionPushbackOToIterator(CharmInstruction):
    """
    pushback_o_to_iterator

    Pushes the value in the 'o' register back on the
    iterator.
    """

    def __init__(self):
        self.op = opcode.pushback_o_to_iterator

    def __repr__(self):
        return f"<pushback_o_to_iterator>"

class CharmInstructionPopIterator(CharmInstruction):
    """
    pop_iterator

    Pops the top value from the iterator stack and
    sets it as the current iterator, overwriting
    the reference to the current iterator.

    If the iterator stack is empty,
    you must abort processing and produce an error.
    """

    def __init__(self):
        self.op = opcode.pop_iterator

    def __repr__(self):
        return f"<pop_iterator>"

class CharmInstructionMapOption(CharmInstruction):
    """
    map_option <group> <option> <program> <key> <parameter>

    Maps the option <option> to the program <program>.

    <group> is the id of the ArgumentGroup this is mapped in.

    <program> is self-contained; if the option is invoked
    on the command-line, you may run it with
    CharmInterpreter.call(program).

    <key> and <parameter> are used in generating
    usage information.  <key> is the converter key
    for the converter (callable) which accepts a keyword-only
    parameter that will be filled by this option,
    and <parameter> is the keyword-only
    parameter accepted by that converter which this
    option fills.  (The value returned by this program
    becomes the argument for <parameter> when calling
    the callable in <parameter.annotation>.)
    """
    __slots__ = ['option', 'program', 'key', 'parameter', 'group']

    def __init__(self, group, option, program, key, parameter):
        self.op = opcode.map_option
        self.group = group
        self.option = option
        self.program = program
        self.key = key
        self.parameter = parameter

    def __repr__(self):
        return f"<map_option option={self.option!r} program={self.program} key={self.key} parameter={self.parameter} key={self.key} group={self.group}>"

class CharmInstructionNextToO(CharmInstruction):
    """
    next_to_o <required> <is_oparg>

    Consume the next value from the iterator
    and store it in the 'o' register.

    <is_oparg> is a boolean flag:
        * If <is_oparg> is True, you're consuming an oparg.
          You should consume the next command-line argument
          no matter what it is--even if it starts with a
          dash, which would normally indicate a command-line
          option.
        * If <is_oparg> is False, you're consuming a top-level
          command-line positional argument.  You should process
          command-line arguments normally, including
          processing options.  Continue processing until
          you find a command-line argument that isn't
          an option, nor is consumed by any options that
          you might have encountered while processing,
          and then consume that argument to satisfy this
          instruction.

    <required> is also a boolean flag, and is only considered
    when the iterator is exhausted.
        * If <required> is True, this argument is required.
          If the iterator is exhausted, abort processing and
          raise a usage exception.
        * If <required> is False, this argument isn't
          required.  If the iterator is exhausted, 'o'
          is set to None and 'flag' to False.

    'flag' is set to True if this consumed a value and set
    it in the 'o' register, and False if it did not (and
    <required> is false).
    """
    __slots__ = ['required', 'is_oparg']

    def __init__(self, required, is_oparg):
        self.op = opcode.next_to_o
        self.required = required
        self.is_oparg = is_oparg

    def __repr__(self):
        return f"<next_to_o required={self.required} is_oparg={self.is_oparg}>"

class CharmInstructionLookupToO(CharmInstruction):
    """
    lookup_to_o <key> <required>

    Retrieves the value named <key> from the mapping
    and stores it in the 'o' register.

    <key> is the key used to look up the value.

    <required> is a boolean flag:
        * If <required> is True, this value is required.
          If <key> is not currently mapped, you must abort
          processing and raise a usage exception.
        * If <required> is False, this value isn't
          required.  'o' will be set to empty.

    'flag' is set to True if the lookup succeeded,
    and False if the lookup failed (and <required> is false).
    """
    __slots__ = ['key', 'required']

    def __init__(self, key, required):
        self.op = opcode.lookup_to_o
        self.key = key
        self.required = required

    def __repr__(self):
        return f"<lookup_to_o key={self.key} required={self.required}>"


class CharmInstructionFlushMultioption(CharmInstruction): # CharmInstructionNoArgBase
    """
    flush_multioption

    Calls the flush() method on the object stored in
    the 'o' register.
    """

    def __init__(self):
        self.op = opcode.flush_multioption

    def __repr__(self):
        return f"<flush_multioption>"

class CharmInstructionRememberConverters(CharmInstruction): # CharmInstructionNoArgBase
    """
    remember_converters

    Start tracking the converters created from this
    point forward, until flush_mulitoption is invoked.

    A bit of a hack--a bugfix for a design flaw.
    The problem is with MappingCompiler: when you flush a
    multioption, we should start fresh and recreate
    all its child converters.  Most of the time we'll
    create fresh converters automatically.  But if a child
    converter is also a multioption, the bytecode we
    generate detects whether or not the converter already
    exists.

    The correct fix for this will probably involve maintaining
    a converter *stack*.  Either that, or, make MappingCompiler
    use separate programs for multioptions, like AppealCompiler
    does.
    """

    def __init__(self):
        self.op = opcode.remember_converters

    def __repr__(self):
        return f"<remember_converters>"


class CharmInstructionForgetConverters(CharmInstruction): # CharmInstructionNoArgBase
    """
    forget_converters

    "forget" all remembered
    converters--delete them from "converters".
    """

    def __init__(self):
        self.op = opcode.forget_converters

    def __repr__(self):
        return f"<forget_converters>"

class CharmInstructionSetGroup(CharmInstruction):
    """
    set_group <id> <minimum> <maximum> <optional> <repeating>

    Indicates that the program has entered a new argument
    group, and specifies the minimum and maximum arguments
    accepted by that group.  These numbers are stored as
    an ArgumentCount object in the 'group' register.
    """

    __slots__ = ['group', 'id', 'optional', 'repeating']

    def __init__(self, id, optional, repeating):
        self.op = opcode.set_group
        self.group = ArgumentGroup(optional=optional, id=id)
        self.id = id
        self.optional = optional
        self.repeating = repeating

    def __repr__(self):
        return f"<set_group id={self.id} group={self.group.summary()} optional={self.optional} repeating={self.repeating}>"

class CharmProgram:

    next_id = serial_number_generator(prefix="program-").__next__

    def __init__(self, name):
        self.name = name

        self.id = CharmProgram.next_id()

        self.opcodes = []

        # maps line number to list of comments
        self.comments = {}

        # same as self.comments, but for labels
        # (presentation is slightly different)
        self.labels = {}

        # maps option to its parent option (if any)
        # used for usage
        self.options = None

        self.total = ArgumentGroup(optional=False)

    def __repr__(self):
        s = f" {self.name!r}" if self.name else ""
        return f"<CharmProgram {self.id}{s} minimum={self.total.minimum} maximum={self.total.maximum}>"

    def __len__(self):
        return len(self.opcodes)

    def __iter__(self):
        return iter(self.opcodes)

    def __getitem__(self, index):
        return self.opcodes[index]


class CharmAssembler:
    """
    Assembles CharmInstruction objects into a CharmProgram.
    Has a function call for every instruction; calling
    the function appends one of those instructions.
    When you're done assembling, call finalize(), and
    it will return the finished program.

    You can also append a CharmAssembler.  That
    indeed appends the assembler at that point in
    the stream of instructions, and when it finalizes,
    it flattens all the CharmAssemblers inline.
    Every CharmAssembler you appended will be replaced
    with the instructions appended to it, recursively.
    """

    PRESERVE = "_preserve_comment_instructions"
    STRIP = "_strip_comments_completely"
    EXTERNAL = "_move_comments_to_external_table"


    next_id = serial_number_generator(prefix="asm-").__next__

    def __init__(self, name=None):
        self.name = name
        self.id = CharmAssembler.next_id()
        self.clear()

    def clear(self):
        self.opcodes = opcodes = []
        # first entry in contents is there for "spilling names"
        self.contents = [[], opcodes]
        self._append_opcode = opcodes.append

        self.option_to_child_options = defaultdict(set)
        self.option_to_parent_options = defaultdict(set)

    def __repr__(self):
        name = f"name={self.name} " if self.name else ""
        return f"<CharmAssembler {name}id={self.id}>"

    def append(self, o):
        if isinstance(o, CharmAssembler):
            if not len(self.opcodes):
                opcodes = self.contents.pop()
            else:
                self.opcodes = opcodes = []
                self._append_opcode = opcodes.append
            self.contents.append(o)
            self.contents.append(opcodes)
            return o
        if isinstance(o, CharmInstruction):
            self._append_opcode(o)
            return o
        raise TypeError('o must be CharmAssembler or CharmInstruction')

    def __len__(self):
        return sum(len(o) for o in self.contents)

    def __bool__(self):
        return any(self.contents)

    # opcodes

    def abort(self, message):
        op = CharmInstructionAbort(message)
        self._append_opcode(op)
        return op

    def end(self):
        op = CharmInstructionEnd()
        self._append_opcode(op)
        return op

    def no_op(self):
        op = CharmInstructionNoOp()
        self._append_opcode(op)
        return op

    def comment(self, comment):
        op = CharmInstructionComment(comment)
        self._append_opcode(op)
        return op

    def label(self, name):
        op = CharmInstructionLabel(name)
        self._append_opcode(op)
        return op

    def jump_to_label(self, label):
        op = CharmInstructionJumpToLabel(label)
        self._append_opcode(op)
        return op

    def indirect_jump(self):
        op = CharmInstructionIndirectJump()
        self._append_opcode(op)
        return op

    def literal_to_o(self, value):
        op = CharmInstructionLiteralToO(value)
        self._append_opcode(op)
        return op

    def wrap_o_with_iterator(self):
        op = CharmInstructionWrapOWithIterator()
        self._append_opcode(op)
        return op

    def label_to_o(self, label):
        op = CharmInstructionLabelToO(label)
        self._append_opcode(op)
        return op

    def call(self, program):
        op = CharmInstructionCall(program)
        self._append_opcode(op)
        return op

    def create_converter(self, parameter, key):
        op = CharmInstructionCreateConverter(
            parameter=parameter,
            key=key,
            )
        self._append_opcode(op)
        return op

    def load_converter(self, key):
        op = CharmInstructionLoadConverter(
            key=key,
            )
        self._append_opcode(op)
        return op

    def load_o(self, key):
        op = CharmInstructionLoadO(
            key=key,
            )
        self._append_opcode(op)
        return op

    def converter_to_o(self):
        op = CharmInstructionConverterToO()
        self._append_opcode(op)
        return op

    def append_to_converter_args(self, parameter, discretionary, usage):
        op = CharmInstructionAppendToConverterArgs(
            parameter = parameter,
            discretionary = discretionary,
            usage = usage,
            )
        self._append_opcode(op)
        return op

    def set_in_converter_kwargs(self, parameter, usage):
        op = CharmInstructionSetInConverterKwargs(
            parameter = parameter,
            usage = usage,
            )
        self._append_opcode(op)
        return op

    def push_o(self):
        op = CharmInstructionPushO()
        self._append_opcode(op)
        return op

    def pop_o(self):
        op = CharmInstructionPopO()
        self._append_opcode(op)
        return op

    def peek_o(self):
        op = CharmInstructionPeekO()
        self._append_opcode(op)
        return op

    def push_flag(self):
        op = CharmInstructionPushFlag()
        self._append_opcode(op)
        return op

    def pop_flag(self):
        op = CharmInstructionPopFlag()
        self._append_opcode(op)
        return op

    def push_mapping(self):
        op = CharmInstructionPushMapping()
        self._append_opcode(op)
        return op

    def pop_mapping(self):
        op = CharmInstructionPopMapping()
        self._append_opcode(op)
        return op

    def push_iterator(self):
        op = CharmInstructionPushIterator()
        self._append_opcode(op)
        return op

    def pushback_o_to_iterator(self):
        op = CharmInstructionPushbackOToIterator()
        self._append_opcode(op)
        return op

    def pop_iterator(self):
        op = CharmInstructionPopIterator()
        self._append_opcode(op)
        return op

    def map_option(self, group, option, program, key, parameter):
        self.option_to_child_options[option].update(program.option_to_child_options)

        self.option_to_parent_options.update(program.option_to_parent_options)
        for child_option in program.option_to_child_options:
            self.option_to_parent_options[child_option].add(option)

        op = CharmInstructionMapOption(
            group = group,
            option = option,
            program = program,
            key = key,
            parameter = parameter,
            )
        self._append_opcode(op)
        return op

    def next_to_o(self, required=False, is_oparg=False):
        op = CharmInstructionNextToO(
            required=required,
            is_oparg=is_oparg,
            )
        self._append_opcode(op)
        return op

    def lookup_to_o(self, key, required=False):
        op = CharmInstructionLookupToO(
            key=key,
            required=required,
            )
        self._append_opcode(op)
        return op

    def flush_multioption(self):
        op = CharmInstructionFlushMultioption()
        self._append_opcode(op)
        return op

    def remember_converters(self):
        op = CharmInstructionRememberConverters()
        self._append_opcode(op)
        return op

    def forget_converters(self):
        op = CharmInstructionForgetConverters()
        self._append_opcode(op)
        return op

    def branch_on_flag_to_label(self, label):
        op = CharmInstructionBranchOnFlagToLabel(label=label)
        self._append_opcode(op)
        return op

    def branch_on_not_flag_to_label(self, label):
        op = CharmInstructionBranchOnNotFlagToLabel(label=label)
        self._append_opcode(op)
        return op

    def test_is_o_true(self):
        op = CharmInstructionTestIsOTrue()
        self._append_opcode(op)
        return op

    def test_is_o_none(self):
        op = CharmInstructionTestIsONone()
        self._append_opcode(op)
        return op

    def test_is_o_empty(self):
        op = CharmInstructionTestIsOEmpty()
        self._append_opcode(op)
        return op

    def test_is_o_iterable(self):
        op = CharmInstructionTestIsOIterable()
        self._append_opcode(op)
        return op

    def test_is_o_mapping(self):
        op = CharmInstructionTestIsOMapping()
        self._append_opcode(op)
        return op

    def test_is_o_str_or_bytes(self):
        op = CharmInstructionTestIsOStrOrBytes()
        self._append_opcode(op)
        return op

    def set_group(self, id=None, optional=True, repeating=False):
        op = CharmInstructionSetGroup(id=id, optional=optional, repeating=repeating)
        self._append_opcode(op)
        return op

    def spill_names(self):
        """
        Recursively walk the tree of opcodes and assemblers
        inside self.  If self has *any* opcodes, insert a
        comment opcode at the front of this assembler's
        opcode stream.  Also perform these insertions
        for all the child assemblers.
        """
        stack = []
        def new(assembler):
            return assembler, iter(assembler.contents), 0
        def push(assembler, iterator, count):
            stack.append((assembler, iterator, count))
        pop = stack.pop

        push(*new(self))

        while stack:
            assembler, iterator, count = stack.pop()

            for o in iterator:
                if isinstance(o, list):
                    count += len(o)
                    continue
                push(assembler, iterator, count)
                push(*new(o))
                break
            else:
                if count:
                    # spill!
                    first_opcodes = assembler.contents[0]
                    assert isinstance(first_opcodes, list)
                    assert len(first_opcodes) == 0, f"expected first_opcodes to be empty, but it's {first_opcodes}"
                    first_opcodes.append(CharmInstructionComment(self.name))


    def lists(self):
        for o in self.contents:
            if isinstance(o, list):
                yield o
                continue
            yield from o.lists()

    def __getitem__(self, index):
        if not isinstance(index, int):
            raise TypeError(f"CharmAssembler indices must be integers, not {type(index).__name__}")

        for l in self.lists():
            length = len(l)
            if index >= length:
                index -= length
                continue
            return l[index]

        raise IndexError(f"CharmAssembler index out of range")

    def assemble(self, *, comments=EXTERNAL):
        """
        Merges all the opcodes and the nested assemblers
        into a single list, and processes

        * Computes total and group min/max values.
        * Convert label/jump_to_label pseudo-ops into
          absolute jump ops (and removes labels).
        * Handles comment instructions based on the 'comments'
          argument:
            * EXTERNAL means delete the instructions, but move
              the comments themselves into an external table
              (a la CPython's "lnotab").
            * STRIP means simply delete the instructions.
            * PRESERVE means preserve the comments.
        * Removes no-ops.
        * Removes redundant load_converter, load_o,
          and converter_to_o instructions based on
          rudimentary dataflow analysis.
        * Simple jump-to-jumps peephole optimizer.
        """

        self.spill_names()

        opcodes = []
        for sublist in self.lists():
            opcodes.extend(sublist)

        if not (opcodes and (opcodes[-1].op == opcode.end)):
            opcodes.append(CharmInstructionEnd())
        end_op = opcodes[-1]

        labels = {}
        fixups = []

        total = ArgumentGroup()
        group = None
        optional = False

        # we store these as [index, s] lists initially
        # then boil them down into dicts mapping index to lists of strings
        # why? so we can fixup the indexes when we delete opcodes later
        external_comments = []
        external_labels = []

        labels_seen = set()

        # if 1:
        #     print()
        #     print("[assemble step 1 - {len(opcodes)} opcodes]")
        #     for i, op in enumerate(opcodes):
        #         print(f">> {i:02} | {op}")

        index = 0
        while index < len(opcodes):
            op = opcodes[index]

            # handle comments
            if op.op == opcode.comment:
                if comments == CharmAssembler.EXTERNAL:
                    external_comments.append([index, op.comment])
                    del opcodes[index]
                elif comments == CharmAssembler.STRIP:
                    del opcodes[index]
                else:
                    assert comments == CharmAssembler.PRESERVE
                continue

            # remove labels
            if op.op == opcode.label:
                if op in labels:
                    raise ConfigurationError(f"label instruction used twice: {op}")
                if op.label in labels_seen:
                    raise ConfigurationError(f"label description used twice: '{op.label}'")
                labels_seen.add(op.label)
                labels[op] = index
                external_labels.append([index, op.label])
                del opcodes[index]
                continue
            elif op.op == opcode.jump_to_label:
                fixups.append(index)
            elif op.op == opcode.branch_on_flag_to_label:
                fixups.append(index)
            elif op.op == opcode.branch_on_not_flag_to_label:
                fixups.append(index)
            elif op.op == opcode.label_to_o:
                fixups.append(index)

            # remove no_ops
            elif op.op == opcode.no_op:
                del opcodes[index]
                continue

            index += 1

        # if 1:
        #     print()
        #     print("[assemble step 2 - {len(opcodes)} opcodes]")
        #     for i, op in enumerate(opcodes):
        #         print(f">> {i:02} | {op}")

        # now process jump fixups:
        # replace *_to_label ops with absolute jump ops
        replacement_op = {
            opcode.jump_to_label: CharmInstructionJump,
            opcode.branch_on_flag_to_label: CharmInstructionBranchOnFlag,
            opcode.branch_on_not_flag_to_label: CharmInstructionBranchOnNotFlag,
            opcode.label_to_o: CharmInstructionLiteralToO,
        }
        jump_ops = { opcode.jump_to_label, opcode.jump }
        fixup_ops = []
        for index in fixups:
            op = opcodes[index]
            address = labels.get(op.label)
            if address is None:
                raise ConfigurationError(f"unknown label {op.label}")
            replacement = replacement_op[op.op](address)
            opcodes[index] = replacement
            fixup_ops.append(replacement)

        # if 1:
        #     print()
        #     print("[assemble step 3 - {len(opcodes)} opcodes]")
        #     for i, op in enumerate(opcodes):
        #         print(f">> {i:02} | {op}")

        jump_targets = set()
        # and *now* do a jump-to-jump peephole optimization
        # (I don't know if Appeal *can* actually generate jump-to-jumps)
        for index in fixups:
            while True:
                op = opcodes[index]
                op_attr = 'value' if op.op == opcode.literal_to_o else 'address'
                address = getattr(op, op_attr)
                # print(f"fixing up {op=}, at {index=}, {address=}")
                op2 = opcodes[address]
                if op2.op not in jump_ops:
                    jump_targets.add(address)
                    break
                setattr(op, op_attr, op2.address)

        jump_targets = list(jump_targets)
        jump_targets.sort(reverse=True)

        # remove redundant load_converter,
        # load_o, and converter_to_o ops

        class Unknown:
            def __repr__(self):
                return "<Unknown>"

        unknown = Unknown()
        converter = unknown
        o = unknown
        stack = []
        groups = []

        def reset_registers():
            # if 1 and want_prints:
            #     print("*reset!* jump target!")
            nonlocal converter
            nonlocal o

            converter = unknown
            o = unknown

        # if 1 and want_prints:
        #     print("\nremoving redundant loads\n")
        #     def print_op():
        #         print(f"[{index:02}] {converter=} {o=} {op}", end=' ')

        #     def nc():
        #         print("no change")

        def remove_op():
            # if 1 and want_prints:
            #     print(f" >>> deleting! redundant. <<<")
            del opcodes[index]
            for i in range(len(jump_targets)):
                assert jump_targets[i] > index
                jump_targets[i] -= 1
            for op in fixup_ops:
                op_attr = 'value' if op.op == opcode.literal_to_o else 'address'
                address = getattr(op, op_attr)
                if address > index:
                    setattr(op, op_attr, address - 1)
            for parent in (external_comments, external_labels):
                for l in parent:
                    if l[0] > index:
                        l[0] -= 1

        # if 1 and want_prints:
        #     print("jump targets ", jump_targets)
        index = 0
        while index < len(opcodes):
            if jump_targets and (jump_targets[-1] == index):
                jump_targets.pop()
                reset_registers()

            op = opcodes[index]

            # compute total and group values
            # if 1 and want_prints:
            #     print_op()
            if op.op == opcode.set_group:
                group = op.group
                optional = op.optional
                if op.repeating:
                    total.maximum = math.inf
                # if 1 and want_prints:
                #     nc()
            elif op.op == opcode.next_to_o:
                if not optional:
                    total.minimum += 1
                total.maximum += 1

                if group:
                    group.minimum += 1
                    group.maximum += 1

                o = '(string value)'
                # if 1 and want_prints:
                #     print(f"o -> {o}")
            elif op.op == opcode.lookup_to_o:
                o = unknown
                # if 1 and want_prints:
                #     print(f"o -> {o}")
            # discard redundant load_converter and load_o ops
            # using dataflow analysis
            elif op.op == opcode.load_converter:
                if converter == op.key:
                    remove_op()
                    continue
                converter = op.key
                # if 1 and want_prints:
                #     print(f"converter -> {converter}")
            elif op.op == opcode.load_o:
                if o == op.key:
                    remove_op()
                    continue
                o = op.key
                # if 1 and want_prints:
                #     print(f"o -> {o}")
            elif op.op == opcode.converter_to_o:
                if o == converter:
                    remove_op()
                    continue
                o = converter
                # if 1 and want_prints:
                #     print(f"o -> {o}")
            elif op.op == opcode.create_converter:
                o = op.key
                # if 1 and want_prints:
                #     print(f"o -> {o}")
            else:
                pass
                # if 1 and want_prints:
                #     nc()

            index += 1

        program = CharmProgram(self.name)
        program.opcodes = opcodes
        end_op.id = program.id
        total.id = program.total.id
        program.total = total
        program.option_to_child_options = self.option_to_child_options
        program.option_to_parent_options = self.option_to_parent_options

        comments = defaultdict(list)
        for index, comment in external_comments:
            comments[index].append(comment)
        program.comments = comments

        labels = defaultdict(list)
        for index, label in external_labels:
            labels[index].append(label)
        program.labels = labels

        return program


class CharmCompiler:
    """
    Base compiler class.
    You don't want to use this directly; you want one of the subclasses:

    * CharmCommandCompiler compiles an Appeal "command".
    * CharmOptionCompiler compiles an "option" for an Appeal "command".
    * CharmMappingCompiler compiles a reader for a mapping.
    * CharmIteratorCompiler compiles a reader for an iterator.

    In general the workflow is like this:

    * Construct the Compiler.  All compilers take the same arguments
      in their constructor.
    * Compile the thing, whatever it is, by calling the compiler object
      (aka __call__).  These calls take context-specific arguments.
    * Call the assemble method on the compiler.  This takes no arguments
      and returns the finalized program.

    (Why not have __call__ return the finalized program?  Because we might
    want to modify the program after compliation but before final assembly.)
    """

    next_compilation_id = serial_number_generator(prefix="c-").__next__

    def __init__(self, appeal, processor, *, indent='', name=''):
        self.appeal = appeal
        self.root = appeal.root
        self.processor = processor

        self.name = name
        self.indent = indent

        self.root_a = CharmAssembler(self.name)

        self.next_converter_key = serial_number_generator(prefix=self.next_compilation_id() + '_k-').__next__

    @staticmethod
    def fake_parameter(kind, callable, default=empty):
        parameter_name = callable.__name__
        while True:
            if parameter_name.startswith('<'):
                parameter_name = parameter_name[1:-1]
                continue
            if parameter_name.endswith("()"):
                parameter_name = parameter_name[:-2]
                continue
            break

        # in Python 3.11, inspect.Parameter won't allow you to use
        # 'lambda' (or '<lambda>') as a parameter name.  And we aren't
        # doing that... not *really*.  It's not a *real* Parameter,
        # we just use one of those because of the way _compile recurses.
        # But if we're compiling a lambda function, we create a
        # Parameter out of the function's name, which is '<lambda>',
        # and, well... we gotta use *something*.  (hope this works!)

        parameter = inspect.Parameter("___fake_name___", kind, annotation=callable, default=default)
        parameter._name = parameter_name
        return parameter

    def clean_up_argument_group(self):
        pass


    def assemble(self):
        """
        Assembles all the instructions together to produce a final program.
        """
        if self.processor and self.processor.log:
            self.processor.log.enter(f"assemble {self.name}")

        self.clean_up_argument_group()

        self.program = self.root_a.assemble()

        if self.processor and self.processor.log:
            self.processor.log.exit()

        return self.program


class CharmAppealCompiler(CharmCompiler):

    def __init__(self, appeal, processor, parameter, *, indent='', name=''):
        name = name or parameter.name
        super().__init__(appeal, processor, indent=indent, name=name)
        self.command_converter_key = None

        # The compiler is effectively two passes.
        #
        # First, we iterate over the annotation tree generating instructions.
        # These go into discrete "assemblers" which are carefully ordered in
        # the self.assemblers list.
        #
        # Second, we root_a.finalize(), which assembles the final program.

        callable = dereference_annotated(parameter.annotation)
        default = parameter.default

        indent = self.indent

        # options defined in the current argument group
        self.ag_a = self.ag_initialize_a = None
        self.ag_options_a = self.ag_duplicate_options_a = None
        self.ag_options = set()
        self.ag_duplicate_options = set()
        self.next_argument_group_id = serial_number_generator(prefix=f"{name} ag-").__next__

        self.new_argument_group(optional=False, indent=indent)

        if self.processor:
            self.processor.log.enter(f"compile {callable}")

        # if want_prints:
        #     print(f"[cc]")
        #     print(f"[cc] {indent}Compiling '{self.name}'")
        #     print(f"[cc]")

        if self.processor:
            self.processor.log("parameter grouper")
        def signature(p):
            cls = self.appeal.map_to_converter(p)
            signature = cls.get_signature(p)
            return signature
        pg = argument_grouping.ParameterGrouper(callable, default, signature=signature)
        pgi = pg.iter_all()

        add_to_parent_a, degenerate_append_op = self.compile_parameter(parameter, pgi, 0, indent, name)

        # if want_prints:
        #     print(f"[cc] {indent}compilation of {parameter} complete.")
        #     print(f"[cc]")

        if self.processor:
            self.processor.log.exit()

        self.add_to_parent_a = add_to_parent_a

    def clean_up_argument_group(self, indent=''):
        if self.ag_a:
            if self.ag_options:
                # if want_prints:
                #     print(f"[cc]")
                #     print(f"[cc] {indent}flushing previous argument group's options.")
                #     print(f"[cc]")
                self.ag_initialize_a.append(self.ag_options_a)
                self.ag_options.clear()

            uninteresting_opcodes = set((opcode.comment, opcode.label))
            # if we didn't put anything in one of our assemblers,
            # clear it so we don't have the needless comment lying around
            def maybe_clear_a(a):
                # if length is 0, we don't need to bother clearing, it's already empty
                # if length > 1, it has stuff in it
                if a is None:
                    return
                if len(a) == 1:
                    if a[0].op in uninteresting_opcodes:
                        a.clear()

            maybe_clear_a(self.ag_initialize_a)
            maybe_clear_a(self.ag_options_a)
            # is this redundant? maybe.
            # but ag_duplicate_options_a is in body_a,
            # so we should clear ag_duplicate_options_a
            # before we try to clear body_a.
            maybe_clear_a(self.ag_duplicate_options_a)
            maybe_clear_a(self.body_a)

    def new_argument_group(self, *, optional, indent=''):
        #
        # Every argument group adds at least three assemblers:
        #
        #   * ag_initialize_a, the assembler for initialization code for
        #     this argument group.  starts with a set_group instruction,
        #     then has all the create_converter instructions.
        #   * ag_options_a, the assembler for map_option instructions
        #     for options that *haven't* been mapped before in this
        #     argument group.
        #   * ag_duplicate_options_a, the assembler for map_option
        #     instructions for options that *have* been mapped before
        #     in this argument group.  Initially this is None, and
        #     then we create a fresh one after emitting every
        #     next_to_o opcode.
        #
        # What's this about duplicate options?  It's Appeal trying
        # to be a nice guy, to bend over backwards and allow crazy
        # command-lines.
        #
        # Normally an option is mapped purely based on its membership
        # in an optional group.  Consider this command:
        #
        #     def three_strs(d, e, f, *, o=False): ...
        #
        #     @app.command()
        #     def base(a, b, c, three_strs: d_e_f=None, *, v=False): ...
        #
        # Its usage would look like this:
        #
        #     base [-v] a b c [ [-o] d e f ]
        #
        # -v is mapped the whole time, but -o is only mapped after
        # you have three parameters.
        #
        # Now what if you change it to be like this?
        #
        #     def three_strs(d, e, f, *, o=False): ...
        #
        #     @app.command()
        #     def base(a, b, c, d_e_f:three_strs=None, g_h_i:three_strs=None, *, v=False): ...
        #
        # Since the two mappings of -o are in different groups, it's okay.
        # Usage looks like this:
        #
        #     base [-v] a b c [ [-o] d e f [ [-o] d e f ] ]
        #
        # Still not ambiguous.  But what if you do *this*?
        #
        #     def three_strs(d, e, f, *, o=False): ...
        #
        #     @app.command()
        #     def base(a, b, c, d_e_f:three_strs, g_h_i:three_strs, *, v=False): ...
        #
        # Now everybody's in one big argument group.  And that means
        # we map -o twice in the same group.
        #
        # Appeal permits this because it isn't actually ambiguous.
        # It permits you to map the same option twice in one argument
        # group *provided that* it can intelligently map the duplicate
        # option after a next_to_o opcode--between positional
        # parameters.  So usage looks like this:
        #
        #     base [-v] a b c [-o] d e f [-o] d e f
        #
        # It looks a little strange, but hey man, you're the one who
        # asked Appeal to turn *that* into a command-line.  It's doing
        # its best!

        self.clean_up_argument_group(indent=indent)

        self.group_id = group_id = self.next_argument_group_id()

        # if want_prints:
        #     print(f"[cc] {indent}new argument group '{group_id}'")
        #     indent += "  "

        self.ag_a = ag_a = CharmAssembler(group_id)
        self.root_a.append(ag_a)

        # "converters" represent functions we're going to fill with arguments and call.
        # The top-level command is a converter, all the functions we call to convert
        # arguments are converters.
        self.ag_initialize_a = a = CharmAssembler(f"'{group_id}' initialize")
        a.comment(f"{self.name} argument group '{group_id}' initialization")
        ag_a.append(a)

        self.ag_options_a = a = CharmAssembler(f"'{group_id}' options")
        a.comment(f"{self.name} argument group '{group_id}' options")

        self.body_a = a = CharmAssembler(f"'{group_id}' body")
        a.comment(f"{self.name} argument group '{group_id}' body")
        ag_a.append(a)

        # initially in an argument group we don't allow duplicate options.
        # you can only have duplicates after the first next_to_o
        # opcode in an argument group.
        self.ag_duplicate_options_a = None
        self.ag_duplicate_options.clear()

        self.group = self.ag_initialize_a.set_group(id=group_id, optional=optional)
        return self.group

    def reset_duplicate_options_a(self):
        """
        Clear the "duplicate options" state, so that additional duplicates
        can get mapped.

        Call this immediately after you append a positional argument
        (append_to_converter_args).  Every time.
        """
        if self.ag_duplicate_options:
            self.ag_duplicate_options.clear()
        elif self.ag_duplicate_options_a:
            self.ag_duplicate_options_a.clear()

        group_id = self.group_id
        self.ag_duplicate_options_a = a = CharmAssembler(f"{group_id} duplicate options")
        a.comment(f"{self.name} argument group {group_id} duplicate options")
        self.body_a.append(a)

    def is_converter_discretionary(self, parameter, converter_class):
        optional = (
            # *args and **kwargs are not required
            (parameter.kind in (VAR_POSITIONAL, VAR_KEYWORD))
            or
            # parameters of other types with a default are not required
            (parameter.default is not empty)
            )

        # only actual Converter objects can be discretionary
        is_converter = issubclass(converter_class, Converter)

        return is_converter and optional

    def compile_option(self, program_name, parameter, indent):
        """
        This compiles everything in the option except for the
        last little bit where we store the result in the parent
        converter's kwargs.
        """

        # if want_prints:
        #     print(f"[cc] {indent}compile_option")
        #     indent += "  "
        #     print(f"[cc] {indent}program_name={program_name}")
        #     print(f"[cc] {indent}parameter={parameter}")
        #     print(f"[cc]")

        cls = self.appeal.root.map_to_converter(parameter)
        converter = cls(parameter, self.appeal)
        callable = converter.callable

        if cls is SimpleTypeConverterStr:
            # hand-coded program to handle this option that takes
            # a single required str argument.
            # if want_prints:
            #     print(f"[cc] {indent}hand-coded program for simple str")
            a = CharmAssembler(program_name)
            a.set_group(self.next_argument_group_id(), optional=False)
            a.next_to_o(required=True, is_oparg=True)
            add_to_self_a = cc = a
        else:
            annotation = dereference_annotated(parameter.annotation)
            if not is_legal_annotation(annotation):
                raise ConfigurationError(f"precompile_option(): parameter {parameter.name!r} annotation is {parameter.annotation}, which you can't use directly, you must call it")

            # if want_prints:
            #     print(f"[cc] {indent}<< recurse on option >>")

            cc = CharmOptionCompiler(self.appeal, self.processor, parameter, indent=indent, name=program_name)
            add_to_self_a = cc.add_to_parent_a

        return cc, add_to_self_a

    def map_options(self, callable, key, parameters, depth, indent):
        # if want_prints:
        #     print(f"[cc] {indent}map_options")
        #     indent += "    "
        #     print(f"[cc] {indent}automatically map keyword-only parameters to options")

        _, kw_parameters, _ = self.appeal.fn_database_lookup(callable)

        all_kwonly_names = []

        # step 1: populate explicit keyword-only options
        #
        # we have to iterate over parameters, because we need
        # to force mapping with default_options.
        var_keyword = None
        for parameter in parameters.values():
            if parameter.kind == KEYWORD_ONLY:
                if parameter.default == empty:
                    raise ConfigurationError(f"x: keyword-only argument {parameter.name} doesn't have a default value")
                mappings = kw_parameters.get(parameter.name, None)
                if not mappings:
                    annotation = dereference_annotated(parameter.annotation)
                    default_options = self.appeal.root.default_options
                    assert builtins.callable(default_options)
                    default_options(self.appeal, callable, parameter.name, annotation, parameter.default)
                all_kwonly_names.append(parameter.name)
                continue
            if parameter.kind == VAR_KEYWORD:
                var_keyword = parameter.name
                continue

        # step 2: populate **kwargs-only options
        # (options created with appeal.option(), where the parameter_name doesn't
        #  appear in the function, so the output goes into **kwargs)

        # if want_prints:
        #     print(f"[cc] {indent}map user-defined options")

        kw_parameters_unseen = set(kw_parameters) - set(all_kwonly_names)
        if kw_parameters_unseen:
            if not var_keyword:
                raise ConfigurationError(f"x: there are options that must go into **kwargs, but this callable doesn't accept **kwargs.  options={kw_parameters_unseen}")
            # avoid randomness
            kw_parameters_unseen = list(kw_parameters_unseen)
            kw_parameters_unseen.sort()
            all_kwonly_names.extend(kw_parameters_unseen)

        # if want_prints:
        #     print(f"[cc] {indent}all keyword only parameters: {all_kwonly_names}")

        mapped_options = []
        for name in all_kwonly_names:
            # Group together all options for an individual Parameter object.
            #
            # There may be multiple *different* Parameter objects for a
            # single parameter, because that's how we represent mapping multiple
            # different converters to a single keyword-only parameter.
            #
            #     https://github.com/larryhastings/appeal#multiple-options-for-the-same-parameter
            #
            # But Parameter objects aren't necessarily hashable.
            # (the default might be a list, etc.)
            # Also!  You also can't rely on Parameter objects
            # being unique.  There can be two Parameter objects
            # with the same value.
            #
            # And it gets worse!  The official way to turn a parameter into a callable
            # is through the converter factories via map_to_converter.  But the only way
            # to get the converter out is to instantiate it.  And that might give you a
            # bound method of a dynamically-generated class, which means nothing will
            # compare the same because the instances are different.
            #
            # So we correlate them together by converter and default value.  And if the
            # converter is an instance of a bound method, we yank out the class and the
            # unbound method and use those.

            work = []
            parameter_ids = []
            for option_entry in kw_parameters[name]:
                option, redundant_callable, parameter = option_entry
                assert callable == redundant_callable
                mapped_options.append(option)

                cls = self.root.map_to_converter(parameter)
                converter = cls(parameter, self.appeal)
                parameter_callable = converter.callable

                # I can't believe it's come to this
                if isinstance(parameter_callable, types.MethodType):
                    parameter_callable = (parameter_callable.__self__.__class__, parameter_callable.__func__)

                parameter_key = (parameter_callable, parameter.default)
                for parameter_id, parameter_key2 in enumerate(parameter_ids):
                    if parameter_key == parameter_key2:
                        break
                else:
                    parameter_id = len(parameter_ids)
                    parameter_ids.append(parameter_key)
                    work.append((parameter, []))

                options = work[parameter_id][1]
                options.append(option)

            for parameter, options in work:
                # if want_prints:
                #     print(f"[cc] {indent}work: {parameter=} {options=}")

                option_names = [denormalize_option(o) for o in options]
                assert option_names
                option_names = " | ".join(option_names)
                program_name = f"{callable.__name__} {option_names}"

                cc, add_to_self_a = self.compile_option(program_name, parameter, indent)
                add_to_self_a.load_converter(key=key)

                # usage = (f"{callable.__name__}.{parameter.name}", parameter.name)
                usage = None
                add_to_self_a.set_in_converter_kwargs(parameter=parameter, usage=usage)
                program = cc.assemble()

                for option in options:
                    # option doesn't have to be unique in this argument group,
                    # but it must be unique per command-line argument.
                    # (you can't define the same option twice without at least one next_to_o between.)

                    if option not in self.ag_options:
                        self.ag_options.add(option)
                        self.ag_duplicate_options.add(option)
                        destination = self.ag_options_a
                    elif self.ag_duplicate_options_a is not None:
                        if option in self.ag_duplicate_options:
                            raise ConfigurationError(f"multiple definitions of option {denormalize_option(option)} are ambiguous (no command-line arguments between definitions)")
                        destination = self.ag_duplicate_options_a
                        self.ag_duplicate_options.add(option)
                    else:
                        raise ConfigurationError(f"argument group initialized with multiple definitions of option {denormalize_option(option)}, ambiguous")

                    # if want_prints:
                    #     print(f"[cc] {indent}option={option}")
                    #     print(f"[cc] {indent}    program={program}")
                    #     print(f"[cc] {indent}    destination={destination}")
                    destination.map_option(self.group_id, option, program, key, parameter)

        return mapped_options

    def compile_parameter(self, parameter, pgi, depth, indent, usage_name):
        """
        returns add_to_self_a, is_degenerate

        add_to_self_a is an assembler inserted immediately after
          the value for this parameter is read in / created.
          (the value is either a string from the command-line
          or a converter). at that moment, the value has loaded
          in the 'o' register.
        is_degenerate is a boolean, True if this entire subtree is "degenerate".
        """
        if self.processor:
            self.processor.log.enter(f"compile parameter {parameter.name}")

        # if want_prints:
        #     print(f"[cc] {indent}compile_parameter {parameter}")
        #     indent += "    "
        #     required = "yes" if parameter.default is empty else "no"
        #     print(f"[cc] {indent}required? {required}")
        #     print(f"[cc] {indent}depth={depth}")
        #     print(f"[cc]")

        maps_to_positional = set((POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD, VAR_POSITIONAL))
        tracked_by_argument_grouping = set((POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD, VAR_POSITIONAL))

        # the official and *only correct* way
        # to produce a converter from a parameter.
        cls = self.root.map_to_converter(parameter)
        converter = cls(parameter, self.appeal)
        callable = converter.callable

        signature = cls.get_signature(parameter)
        parameters = signature.parameters

        _, _, positionals = self.appeal.root.fn_database_lookup(callable)

        # if want_prints:
        #     print(f"[cc] {indent}cls={cls}")
        #     if not parameters:
        #         print(f"[cc] {indent}signature=()")
        #     else:
        #         print(f"[cc] {indent}signature=(")
        #         for _k, _v in parameters.items():
        #             print(f"[cc] {indent}    {_v},")
        #         print(f"[cc] {indent}    )")
        #     print(f"[cc]")

        # is *this* function degenerate?
        # we're gonna build that up from a bunch of tests, starting with:
        zero_or_one_parameters = (len(parameters) < 2)

        # if want_prints:
        #     print(f"[cc] {indent}zero_or_one_parameters={zero_or_one_parameters}")
        #     print(f"[cc] {indent}len(parameters)={len(parameters)}")
        #     print(f"[cc]")

        # fix chicken-and-egg problem:
        # create converter key here, so we can use it in multioption block
        converter_key = self.next_converter_key()
        if not self.command_converter_key:
            # guarantee that the root converter has a special key
            self.command_converter_key = converter_key

        multioption = issubclass(cls, MultiOption)
        if multioption:
            # assert issubclass(cls, MultiOption), f"{cls=} is not a MultiOption"
            label_flush_multioption = CharmInstructionLabel("flush_multioption")
            label_after_multioption = CharmInstructionLabel("after_multioption")

            assert self.command_converter_key
            load_o_op = self.ag_initialize_a.load_o(key=self.command_converter_key)
            self.ag_initialize_a.test_is_o_true()
            self.ag_initialize_a.branch_on_flag_to_label(label_flush_multioption)
        # else:
        #     assert not issubclass(cls, MultiOption), f"{cls=} IS a MultiOption"

        self.ag_initialize_a.create_converter(parameter=parameter, key=converter_key)
        add_to_parent_a = CharmAssembler()
        add_to_parent_a.load_o(key=converter_key)
        self.body_a.append(add_to_parent_a)

        if multioption:
            load_o_op.key = converter_key
            self.ag_initialize_a.jump_to_label(label_after_multioption)
            self.ag_initialize_a.append(label_flush_multioption)
            self.ag_initialize_a.flush_multioption()
            self.ag_initialize_a.forget_converters()
            self.ag_initialize_a.append(label_after_multioption)
            self.ag_initialize_a.remember_converters()

        # we need to delay mapping options sometimes.
        #
        # if depth=0, we're in the command function (or the root option function).
        # all options go into the first argument group.
        #
        # if depth>0, we're in a child annotation function. all options go into
        # the same group as the first argument (if any)
        spilled_options = False
        def spill_options():
            nonlocal spilled_options
            if spilled_options:
                return
            spilled_options = True
            self.map_options(callable, converter_key, parameters, depth, indent)

        if not depth:
            spill_options()

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
        #
        # The new modern way Appeal deals with this: it only sets usage on
        # append_to_converter_args opcodes that should appear in usage.
        # either that parameter is a leaf node, or it's the top of a
        # degenerate converter tree.

        # if want_prints:
        #     print(f"[cc] {indent}compile positional parameters")
        #     print(f"[cc]")
        #     indent += "    "

        parameter_is_degenerate = True
        degenerate_append_op = None
        op = None

        for i, (parameter_name, p) in enumerate(parameters.items()):
            if not p.kind in maps_to_positional:
                continue

            annotation = dereference_annotated(p.annotation)
            if not is_legal_annotation(annotation):
                raise ConfigurationError(f"{callable.__name__}: parameter {p.name!r} annotation is {p.annotation}, which you can't use directly, you must call it")

            # FIXME it's lame to do this here,
            # you need to rewrite compile_parameter so it
            # always recurses for positional parameters
            cls = self.root.map_to_converter(p)

            if p.kind == VAR_POSITIONAL:
                label = self.body_a.label("var_positional")
                self.body_a.remember_converters()
                index = -1
            else:
                index = i

            # handle @app.parameter name override
            usage_name = positionals.get(parameter_name) or parameter_name

            # only create new groups here if it's an optional group
            # (we pre-create the initial, required group)
            pgi_parameter = next(pgi)

            # if want_prints:
            #     printable_default = "(empty)" if p.default is empty else repr(p.default)
            #
            #     print(f"[cc] {indent}positional parameter {i}: p={p}")
            #     print(f"[cc] {indent}    p.name={p.name!r}")
            #     print(f"[cc] {indent}    usage_name={usage_name!r}")
            #     print(f"[cc] {indent}    p.kind={p.kind!s}")
            #     print(f"[cc] {indent}    annotation={annotation.__name__}")
            #     print(f"[cc] {indent}    default={printable_default} cls={cls}")
            #     print(f"[cc] {indent}    cls={cls}")
            #     print(f"[cc] {indent}    pgi_parameter={pgi_parameter}")

            if pgi_parameter.first_in_group and (not pgi_parameter.in_required_group):
                group = self.group = self.new_argument_group(optional=True, indent=indent + "    ")

            spill_options()

            if cls is SimpleTypeConverterStr:
                # if want_prints:
                #     print(f"[cc] {indent}    simple str converter, next_to_o and append.")
                required = pgi_parameter.required
                self.body_a.next_to_o(required=required, is_oparg=isinstance(self, CharmOptionCompiler))
                if not required:
                    label2 = CharmInstructionLabel(f'{callable.__name__}.{parameter_name}: exit after optional argument')
                    self.body_a.branch_on_flag_to_label(label2)
                    self.body_a.end()
                    self.body_a.append(label2)

                discretionary = False
                add_to_self_a = self.body_a
                self.reset_duplicate_options_a()
            else:
                # if want_prints:
                #     print(f"[cc] {indent}    << recurse on parameter >>")
                discretionary = self.is_converter_discretionary(p, cls)
                add_to_self_a, degenerate_append_op = self.compile_parameter(p, pgi, depth + 1, indent + "    ", usage_name)
                parameter_is_degenerate = (degenerate_append_op != None)

            add_to_self_a.load_converter(key=converter_key)

            # if this parameter is degenerate, let it have usage
            # (but remove the usage from its child, if any)
            if parameter_is_degenerate:
                usage_full_name = f"{callable.__name__}.{p.name}"
                usage = (usage_full_name, usage_name)
                if degenerate_append_op:
                    degenerate_append_op.usage = None
            else:
                usage = None

            op = add_to_self_a.append_to_converter_args(
                parameter=parameter_name,
                usage=usage,
                discretionary=discretionary,
                )

            if p.kind == VAR_POSITIONAL:
                group.repeating = True
                self.body_a.forget_converters()
                self.body_a.jump_to_label(label)

            # if want_prints:
            #     print(f"[cc]")

        spill_options()

        if self.processor:
            self.processor.log.exit()

        degenerate_append_op = op if (zero_or_one_parameters and parameter_is_degenerate) else None
        return add_to_parent_a, degenerate_append_op


class CharmCommandCompiler(CharmAppealCompiler):

    def __init__(self, appeal, processor, callable, *, indent='', name=''):
        parameter = self.fake_parameter(POSITIONAL_ONLY, callable, empty)
        super().__init__(appeal, processor, parameter, indent=indent, name=name)


def charm_compile_command(appeal, processor, callable):
    cc = CharmCommandCompiler(appeal, processor, callable)
    return cc.assemble()



class CharmOptionCompiler(CharmAppealCompiler):
    pass



class CharmMappingCompiler(CharmCompiler):

    def __init__(self, appeal, processor, callable, *, indent='', name=''):
        name = name or callable.__name__
        super().__init__(appeal, processor, indent=indent, name=name)

        if self.processor:
            self.processor.log.enter(f"compile {name}")

        # if want_prints:
        #     print(f"[cm]")
        #     print(f"[cm] {indent}Compiling '{self.name}'")
        #     print(f"[cm]")

        parameter = self.fake_parameter(POSITIONAL_ONLY, callable, empty)
        self.compile_parameter(parameter, indent, force_unnested=True)

        self.root_a.end()

        if self.processor:
            self.processor.log.exit()

    # a "parent's name" can be a special value CONSUME

    def compile_parameter(self, parameter, indent, *, by_name=True, degenerate_name=None, depth=0, degenerate_multioption=False, force_unnested=False):
        """
        returns 2-tuple
            (child_converter_key, is_degenerate)
        """
        if self.processor:
            self.processor.log.enter(f"compile parameter {parameter.name}")

        # if want_prints:
        #     print(f"[cm] {indent}compile_parameter {parameter=}")
        #     indent += "    "
        #     required = "yes" if parameter.default is empty else "no"
        #     print(f"[cm] {indent}by_name {by_name!r}")
        #     print(f"[cm] {indent}degenerate_name {degenerate_name!r}")
        #     print(f"[cm] {indent}degenerate_multioption {degenerate_multioption!r}")
        #     print(f"[cm] {indent}depth {depth}")
        #     print(f"[cm] {indent}required? {required}")
        #     print(f"[cm]")

        # the official and *only correct* way
        # to produce a converter from a parameter.
        cls = self.root.map_to_converter(parameter)
        converter = cls(parameter, self.appeal)
        callable = converter.callable

        signature = cls.get_signature(parameter)
        parameters = signature.parameters

        if not len(parameters):
            raise ConfigurationError("Sorry, can't process a converter that takes no parameters here")

        converter_key = self.next_converter_key()
        a = self.root_a

        only_one_parameter = len(parameters) == 1
        is_degenerate = (depth > 0) and only_one_parameter
        multioption = issubclass(cls, MultiOption)
        unnested_requested = callable in self.root.unnested_converters
        nested = not (force_unnested or unnested_requested or multioption)
        required = parameter.default is empty
        force_non_recursive = False

        if not is_degenerate:
            degenerate_name = None

        # if want_prints:
        #     print(f"[cm] {indent}cls={cls}")
        #     if not parameters:
        #         print(f"[cm] {indent}signature=()")
        #     else:
        #         print(f"[cm] {indent}signature=(")
        #         for _k, _v in parameters.items():
        #             print(f"[cm] {indent}    {_v},")
        #         print(f"[cm] {indent}    )")
        #     print(f"[cm] {indent}is_degenerate={is_degenerate}")
        #     print(f"[cm] {indent}multioption={multioption}")
        #     print(f"[cm] {indent}nested={nested} (force_unnested={force_unnested}, callable in self.root.unnested_converters = {callable in self.root.unnested_converters})")
        #     print(f"[cm] {indent}only_one_parameter={only_one_parameter}")
        #     print(f"[cm] {indent}required={required}")
        #     print(f"[cm]")

        def get_argument_to_o(a, name, required):
            if by_name:
                a.lookup_to_o(name, required=required)
            else:
                a.next_to_o(required=required, is_oparg=True, usage_name=name)

        if multioption:
            by_name = False

            label_prefix = f'[mo] {parameter.name}: '
            def Label(s):
                return CharmInstructionLabel(label_prefix + s)

            label_next = Label('multioption, next')
            label_done = Label(f'multioption, done')
            label_flush = Label('multioption, flush')

            # do we have a value to process with the multioption?
            # if not, goto done.
            a.append(label_next)
            a.next_to_o(required=False, is_oparg=True, usage_name=parameter.name)
            a.branch_on_not_flag_to_label(label_done)

            # we do.  push o, so we can examine it later.
            # has the converter been created?  if so, goto flush,
            # else create it.
            a.push_o()
            a.load_o(key=converter_key)
            a.branch_on_flag_to_label(label_flush)

            # converter hasn't been created yet.
            # and... why, lookee here!

        a.create_converter(parameter=parameter, key=converter_key)

        if multioption:
            assert not nested
            label_analyze_iterated_value = Label(f'{parameter.name}: multioption, analyze iterated value')
            label_o_is_a_mapping = Label(f'{parameter.name}: multioption, o is a mapping')
            label_pop_mapping = Label(f'{parameter.name}: multioption, pop mapping')
            label_pop_iterator = Label(f'{parameter.name}: multioption, pop iterator')

            # created converter, goto test is o a mapping.
            a.jump_to_label(label_analyze_iterated_value)

            # converter already existed, flush.
            a.append(label_flush)
            a.flush_multioption()
            a.forget_converters()

            a.append(label_analyze_iterated_value)
            a.pop_o()
            a.remember_converters()

            if only_one_parameter:
                a.wrap_o_with_iterator()
                a.push_iterator()
                by_name = False
            else:
                # is o a mapping?
                a.test_is_o_mapping()
                a.branch_on_flag_to_label(label_o_is_a_mapping)
                a.abort("MultiOption {callable.__name__} requires multiple parameters, iterable only yielded an individual object")

                # o is a mapping.  push it to mapping stack and process arguments.
                a.append(label_o_is_a_mapping)
                by_name = True
                a.push_mapping()

        elif nested:
            label_o_is_a_mapping = CharmInstructionLabel(f"{parameter.name}: nested, o is a mapping")
            label_process_arguments = CharmInstructionLabel(f"{parameter.name}: nested, process arguments")
            name = degenerate_name or parameter.name
            get_argument_to_o(a, name, required)
            a.test_is_o_mapping()
            a.push_flag()
            a.branch_on_flag_to_label(label_o_is_a_mapping)
            if not by_name:
                a.pushback_o_to_iterator()
            a.jump_to_label(label_process_arguments)
            a.append(label_o_is_a_mapping)
            a.push_mapping()
            a.append(label_process_arguments)

        for child in parameters.values():
            if child.kind is VAR_POSITIONAL:
                raise ConfigurationError(f"{callable.__name__}: parameter *{child.name} is unsupported for CharmMappingCompiler")
            if child.kind is VAR_KEYWORD:
                raise ConfigurationError(f"{callable.__name__}: parameter **{child.name} is unsupported for CharmMappingCompiler")

            child_annotation = dereference_annotated(child.annotation)
            if not is_legal_annotation(child_annotation):
                raise ConfigurationError(f"{callable.__name__}: parameter {child.name} annotation is {child_annotation}, which you can't use directly, you must call it")

            # FIXME it's lame to do this again here,
            # you should rewrite compile_parameter so it
            # always recurses for positional parameters
            child_cls = self.root.map_to_converter(child)
            child_converter = child_cls(child, self.appeal)
            child_callable = child_converter.callable
            child_multioption = issubclass(child_cls, MultiOption)

            child_required = child.default is empty
            child_discretionary = not child_required
            child_write_to_kwargs = (child.kind is KEYWORD_ONLY) or ((child.kind is POSITIONAL_OR_KEYWORD) and child_discretionary)

            label_got_value = CharmInstructionLabel(f"child {child.name}, got value")

            # if want_prints:
            #     print(f"[cm] {indent} {child=} {child_cls=} {child_converter=} {child_callable=} {child_multioption=}")
            if child_cls is SimpleTypeConverterStr:
                name = degenerate_name or child.name
                get_argument_to_o(a, name, child_required)

                label_got_value = CharmInstructionLabel(f"child {child.name}, got value")
                a.branch_on_flag_to_label(label_got_value)
                if child_discretionary:
                    a.literal_to_o(child.default)
                else:
                    a.abort("{child.name} is required but was not set in the mapping")
                a.append(label_got_value)

            else:
                if child_multioption:
                    name = degenerate_name or child.name
                    label_o_is_iterable = CharmInstructionLabel(f"child {child.name}, o is iterable")
                    get_argument_to_o(a, name, child_required)

                    a.branch_on_flag_to_label(label_got_value)
                    if child_discretionary:
                        a.literal_to_o(child.default)
                    else:
                        a.abort("{child.name} is required but was not available")
                    a.append(label_got_value)

                    a.test_is_o_iterable()
                    a.branch_on_flag_to_label(label_o_is_iterable)
                    a.abort("value in o is not iterable")
                    a.append(label_o_is_iterable)
                    a.push_iterator()

                child_key, child_is_degenerate = self.compile_parameter(child, indent + "    ", depth=depth + 1, by_name=by_name, degenerate_name=degenerate_name or child.name)
                # if want_prints:
                #     print(f"[cm] {indent} {child_key=} {child_is_degenerate=}")

                if child_multioption:
                    # if want_prints:
                    #     print(f"[cm] {indent} generate push / pop iterator (child_multioption)")
                    a.pop_iterator()
                    # I think multioptions can't be degenerate.
                    is_degenerate = False

                a.load_o(child_key)
                is_degenerate = is_degenerate and child_is_degenerate

            a.load_converter(converter_key)
            if child_write_to_kwargs:
                a.set_in_converter_kwargs(parameter=child, usage=None)
            else:
                a.append_to_converter_args(parameter=child, usage=None, discretionary=False)

            # if want_prints:
            #     print(f"[cm]")

        if multioption:
            # okay.  we processed arguments.
            if only_one_parameter:
                a.pop_iterator()
            else:
                a.pop_mapping()
            a.jump_to_label(label_next)
            a.append(label_done)
            a.forget_converters()
        elif nested:
            label_done = CharmInstructionLabel(f"{parameter.name}: nested, done")
            a.pop_flag()
            a.branch_on_not_flag_to_label(label_done)
            a.pop_mapping()
            a.append(label_done)

        if self.processor:
            self.processor.log.exit()

        # if want_prints:
        #     print(f"[cm] {indent}compile_parameter({parameter}) returning {converter_key=} {is_degenerate=}")
        return converter_key, is_degenerate



class CharmIteratorCompiler(CharmCompiler):

    def __init__(self, appeal, processor, callable, *, indent='', name=''):
        name = name or callable.__name__
        super().__init__(appeal, processor, indent=indent, name=name)

        if self.processor:
            self.processor.log.enter(f"compile {name}")

        # if want_prints:
        #     print(f"[cm]")
        #     print(f"[cm] {indent}Compiling '{self.name}'")
        #     print(f"[cm]")

        parameter = self.fake_parameter(POSITIONAL_ONLY, callable, empty)
        self.root_a = CharmAssembler(name)

        cls = self.root.map_to_converter(parameter)
        converter = cls(parameter, self.appeal)
        callable = converter.callable
        self.label_done = CharmInstructionLabel('done')

        child_key = self.compile_parameter(parameter, indent)

        self.root_a.append(self.label_done)
        self.root_a.end()

        if self.processor:
            self.processor.log.exit()

    def compile_parameter(self, parameter, indent, *, depth=0, force_not_required = False):
        """
        returns 2-tuple
            (child_converter_key, is_degenerate)
        """
        if self.processor:
            self.processor.log.enter(f"compile parameter {parameter.name}")

        # if want_prints:
        #     print(f"[cm] {indent}compile_parameter {parameter=}")
        #     indent += "    "
        #     required = "yes" if parameter.default is empty else "no"
        #     print(f"[cm] {indent}required? {required}")
        #     print(f"[cm] {indent}depth {depth}")
        #     print(f"[cm]")

        # the official and *only correct* way
        # to produce a converter from a parameter.
        cls = self.root.map_to_converter(parameter)
        converter = cls(parameter, self.appeal)
        callable = converter.callable

        signature = cls.get_signature(parameter)
        parameters = signature.parameters

        # if want_prints:
        #     print(f"[cm] {indent}cls={cls}")
        #     if not parameters:
        #         print(f"[cm] {indent}signature=()")
        #     else:
        #         print(f"[cm] {indent}signature=(")
        #         for _k, _v in parameters.items():
        #             print(f"[cm] {indent}    {_v},")
        #         print(f"[cm] {indent}    )")
        #     print(f"[cm]")

        # if want_prints:
        #     print(f"[cm] {indent}len(parameters)={len(parameters)}")
        #     print(f"[cm]")

        converter_key = self.next_converter_key()

        a = self.root_a

        a.create_converter(parameter=parameter, key=converter_key)
        is_degenerate = (not depth) and (len(parameters) < 2)

        for child in parameters.values():
            child_annotation = dereference_annotated(child.annotation)
            if not is_legal_annotation(child_annotation):
                raise ConfigurationError(f"{callable.__name__}: parameter {p.name!r} annotation is {p.annotation}, which you can't use directly, you must call it")

            if child.kind is KEYWORD_ONLY:
                raise ConfigurationError("{callable.__name__}: keyword-only parameter {parameter.name!r} is unsupported for CharmIteratorCompiler")
            if child.kind is VAR_KEYWORD:
                raise ConfigurationError("{callable.__name__}: parameter **{parameter.name!r} is unsupported for CharmIteratorCompiler")
            var_positional = child.kind is VAR_POSITIONAL


            # FIXME it's lame to do this here,
            # you need to rewrite compile_parameter so it
            # always recurses for positional parameters
            child_cls = self.root.map_to_converter(child)
            child_converter = child_cls(child, self.appeal)
            child_callable = child_converter.callable

            if var_positional:
                required = False
                label_remember = CharmInstructionLabel('remember')
                a.jump_to_label(label_remember)
                label_again = a.label('again')
                a.forget_converters()
                a.append(label_remember)
                a.remember_converters()
            else:
                required = (child.default is empty) and (not force_not_required)

            # if want_prints:
            #     print(f"[cm] {indent} {child=} {child_cls=} {child_converter=} {child_callable=}")
            if child_cls is SimpleTypeConverterStr:
                a.next_to_o(required=required, is_oparg=True, usage_name=child.name)
                if not required:
                    a.branch_on_not_flag_to_label(self.label_done)
            else:
                child_key, child_is_degenerate = self.compile_parameter(child, indent + "    ", depth=depth + 1, force_not_required=not required)
                a.load_o(child_key)

            a.load_converter(converter_key)
            a.append_to_converter_args(parameter=child, usage=None, discretionary=False)

            is_degenerate = is_degenerate and child_is_degenerate

            if var_positional:
                a.jump_to_label(label_again)

            # if want_prints:
            #     print(f"[cm]")

        if self.processor:
            self.processor.log.exit()

        return converter_key, is_degenerate



def charm_print(program, indent=''):
    programs = collections.deque((program,))
    print_divider = False
    seen = set((program.id,))
    specially_formatted_opcodes = set((opcode.comment, opcode.label))

    comments = program.comments
    labels = program.labels

    while programs:
        if print_divider:
            print("________________")
            print()
        else:
            print_divider = True
        program = programs.popleft()
        width = math.floor(math.log10(len(program))) + 1
        padding = " " * width
        indent2 = indent + f"{padding}|   "
        empty_line = indent2.rstrip()
        print(program)
        print_leading_blank_line = False
        comment_prefix = f"{indent}{' ':{width}}# "
        label_prefix = f"{indent}{' ':{width}}: "
        for i, op in enumerate(program):
            prefix = f"{indent}{i:0{width}}| "

            print_blank = True
            c = comments.get(i, ())
            for comment in c:
                if print_blank:
                    print(indent2)
                    print_blank = False
                print(f"{comment_prefix}{comment}")
                print_leading_blank_line = False

            print_blank = True
            l = labels.get(i, ())
            for label in l:
                if print_blank:
                    print(indent2)
                    print_blank = False
                print(f"{label_prefix}{label}")
                print_leading_blank_line = False

            # specialized opcode printers
            if op.op in specially_formatted_opcodes:
                if print_leading_blank_line:
                    print(empty_line)
                    print_leading_blank_line = False
                if op.op == opcode.comment:
                    print(f"{prefix}# {op.comment}")
                else:
                    print(f"{prefix}{op.label}:")
                print(empty_line)
                continue

            # generic opcode printer
            if print_leading_blank_line:
                print(empty_line)
            print_leading_blank_line = True
            suffix = ""
            printable_op = str(op.op).rpartition(".")[2]
            print(f"{prefix}{printable_op}{suffix}")
            for slot in op.__class__.__slots__:
            # for slot in dir(op):
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
                    value = value.__name__ if value is not None else value
                elif slot == "address":
                    assert value is not None
                    label_names = ", ".join(f"'{s}'" for s in labels.get(value, ()))
                    assert label_names, f"didn't have any labels for index value={value!r}, labels={labels!r}"
                    value = f"{value} # {label_names}"
                elif value == empty:
                    value = "(empty)"
                elif isinstance(value, ArgumentGroup):
                    value = value.summary()
                else:
                    value = repr(value)
                print(f"{indent2}{slot}={value}")
    print()



class CharmProgramIterator:
    __slots__ = ['program', 'opcodes', 'length', 'ip']

    def __init__(self, program):
        self.program = program
        self.opcodes = program.opcodes
        self.length = len(program)
        self.ip = 0

    def __repr__(self):
        return f"<{self.__class__.__name__} program={self.program} ip={self.ip}>"

    def __repr__(self):
        return f"[{self.program}:{self.ip}]"

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
        if not self:
            raise RuntimeError(f"Jumped outside current program, ip={self.ip}, len(program)={self.length}")

    def jump_relative(self, delta):
        self.ip += delta
        if not self:
            raise RuntimeError(f"Jumped outside current program, ip={self.ip}, len(program)={self.length}")

    def end(self):
        self.ip = self.length


class CharmBaseInterpreter:
    """
    A bare-bones interpreter for Charm programs.
    Doesn't actually interpret anything;
    it just provides the registers and the iterator
    and some utility functions like jump, push, and pop.
    Actually interpreting the instructions is up to
    the user.
    """
    def __init__(self, program, *, name=''):
        self.name = name
        self.call_stack = []

        self.iterator = None
        self.iterator_stack = []

        self.mapping = None
        self.mapping_stack = []

        assert program

        # registers

        self.program = program
        # shh, don't tell anybody,
        # the ip register technically lives *inside* the iterator.
        self.ip = CharmProgramIterator(program)

        self.converter = None
        self.o = None
        self.data_stack = []

        self.flag = False
        self.group = None

        self.converters = {}
        self.converter_keys = None
        self.converter_keys_stack = []

        self.groups = []

        self.aborted = False

    def repr_ip(self, ip=None):
        s = "--"

        if self.ip is not None:
            if ip is None:
                ip = self.ip.ip
            length = len(self.ip.program)
            if 0 <= ip < length:
                width = math.floor(math.log10(length) + 1)
                s = f"{ip:0{width}}"
        return s

    def __repr__(self):
        ip = self.repr_ip()
        group = self.group and self.group.summary()
        converter = self.repr_converter(self.converter)
        o = self.repr_converter(self.o)
        return f"<{self.__class__.__name__} [{ip}] converter={converter!s} o={o!s} group={group!s}>"

    def converter_to_key(self, converter):
        if self.converters:
            for key, value in self.converters.items():
                if converter == value:
                    return key
        return None

    def repr_converter(self, converter):
        if isinstance(converter, str) and self.converters:
            key = self.converter_to_key(converter)
            if key:
                width = math.floor(math.log10(len(self.converters)) + 1)
                return f"[{key}]={converter!r}"
        return repr(converter)

    def remember_converters(self):
        self.converter_keys_stack.append(self.converter_keys)
        self.converter_keys = set()

    def forget_converters(self):
        for key in self.converter_keys:
            assert key in self.converters
            del self.converters[key]
        self.converter_keys = self.converter_keys_stack.pop()

    @big.BoundInnerClass
    class CharmProgramStackEntry:
        __slots__ = ['interpreter', 'ip', 'program', 'converter', 'o', 'flag', 'group', 'groups']

        def __init__(self, interpreter):
            self.interpreter = interpreter
            self.ip = interpreter.ip
            self.program = interpreter.program
            self.converter = interpreter.converter
            self.o = interpreter.o
            self.flag = interpreter.flag
            self.group = interpreter.group
            self.groups = interpreter.groups

        def restore(self):
            interpreter = self.interpreter
            interpreter.ip = self.ip
            interpreter.program = self.program
            interpreter.converter = self.converter
            interpreter.o = self.o
            interpreter.flag = self.flag
            interpreter.group = self.group
            interpreter.groups = self.groups

        def __repr__(self):
            return f"<CharmProgramStackEntry ip={self.ip} program={self.program.name!r} converter={self.converter} o={self.o} flag={self.flag} group={self.group.summary() if self.group else 'None'} groups=[{len(self.groups)}]>"

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            if not (self.ip or self.call_stack):
                raise StopIteration
            try:
                ip = self.ip.ip
                op = self.ip.__next__()
                return ip, op
            except StopIteration as e:
                self.end()
                continue

    # def __bool__(self):
    #     return bool(self.ip) or any(bool(cse.ip) for cse in self.call_stack)
    def running(self):
        return bool(self.ip) or any(bool(cse.ip) for cse in self.call_stack)

    def rewind_one_instruction(self):
        if self.ip is None:
            raise StopIteration
        self.ip.jump_relative(-1)

    def call(self, program):
        cpse = self.CharmProgramStackEntry()
        self.call_stack.append(cpse)

        self.program = program
        self.ip = CharmProgramIterator(program)
        self.groups = []
        self.converter = self.o = self.group = None
        self.flag = False

    def end(self):
        if self.call_stack:
            cpse = self.call_stack.pop()
            cpse.restore()
        else:
            self.ip = None

    def _stop(self):
        self.ip = None
        self.call_stack.clear()

    def abort(self, message=''):
        # print("ABORT MESSAGE:", message)
        raise RuntimeError("ABORT MESSAGE: " + message)
        self._stop()
        self.aborted = True
        return None

    def unwind(self):
        while self.call_stack:
            self.end()
        self._stop()


def _charm_usage(appeal, program, usage, closing_brackets, formatter, arguments_values, option_values):
    ci = CharmBaseInterpreter(program)
    program_id_to_option = collections.defaultdict(list)

    key_to_callable = {}

    def add_option(op):
        program_id_to_option[op.program.id].append(op)

    def flush_options():
        for program_id, ops in program_id_to_option.items():
            options = []
            key = None
            for op in ops:
                options.append(denormalize_option(op.option))
                # these are grouped by program_id, so, op.key will
                # be the same for all of them
                if key is None:
                    key = op.key
                else:
                    assert key == op.key, f"expected identical keys, but key {key!r} != op.key {op.key!r}"
            callable = key_to_callable[key]
            full_name = f"{callable.__name__}.{op.parameter.name}"
            option_value = "|".join(options)
            option_values[full_name] = option_value

            usage.append("[")
            usage.append(option_value)

            usage.append(" ")
            old_len_usage = len(usage)
            _charm_usage(appeal, op.program, usage, closing_brackets, formatter, arguments_values, option_values)
            if len(usage) == old_len_usage:
                # this option had no arguments, we don't want the space
                usage.pop()

            usage.append("] ")
        program_id_to_option.clear()

    first_argument_in_group = True

    branches_taken = set()

    for ip, op in ci:
        # if want_prints:
        #     print(f"_charm_usage [{ip:03}] {op}")

        if op.op == opcode.create_converter:
            # the official and *only correct* way
            # to produce a converter from a parameter.
            cls = appeal.map_to_converter(op.parameter)
            converter = cls(op.parameter, appeal)
            callable = converter.callable
            key_to_callable[op.key] = callable
            continue

        if op.op == opcode.map_option:
            add_option(op)
            continue
        if program_id_to_option:
            flush_options()

        if op.op == opcode.set_group:
            if op.optional:
                usage.append(" [")
                closing_brackets.append("]")
                if op.repeating:
                    closing_brackets.append("... ")
            first_argument_in_group = True
            continue

        # This hard-coded strategy for how to handle
        # branching in the usage interpreter works
        # because we don't do much branching.  If we
        # start using it more, we'll need to do something
        # more sophisticated.
        #
        # Ideas:
        #   * make custom opcodes for specific branches
        #     (multioption, var_positional)
        #   * add a hint to the opcode that says "branch"
        #     or "don't branch" when doing usage / docs.
        #     or maybe two flags:
        #         * branch first time? true/false
        #         * branch second and all subsequent times? true/false

        if op.op == opcode.branch_on_flag:
            # branch the first time,
            # don't branch afterwards
            if ip not in branches_taken:
                branches_taken.add(ip)
                ci.ip.jump(op.address)
            continue

        if op.op == opcode.branch_on_not_flag:
            # don't branch the first time,
            # branch thereafter
            if ip not in branches_taken:
                branches_taken.add(ip)
            else:
                ci.ip.jump(op.address)
            continue

        if op.op in (opcode.append_to_converter_args, opcode.set_in_converter_kwargs):
            if op.usage:
                usage_full_name, usage_name = op.usage
                # if want_prints:
                #     print(f">>>> arguments_values[{usage_full_name!r}] = {usage_name!r}")
                arguments_values[usage_full_name] = usage_name
                if op.op == opcode.append_to_converter_args:
                    if first_argument_in_group:
                        first_argument_in_group = False
                    else:
                        usage.append(" ")
                usage.append(formatter(usage_name))
            continue

    if program_id_to_option:
        flush_options()


def charm_usage(appeal, program, *, formatter=str):
    usage = []
    closing_brackets = []
    arguments_values = {}
    option_values = {}
    _charm_usage(appeal, program, usage, closing_brackets, formatter, arguments_values, option_values)
    usage.extend(closing_brackets)
    # if want_prints:
    #     print(f"arguments_values={arguments_values}")
    #     print(f"option_values={option_values}")
    return "".join(usage).strip(), arguments_values, option_values



from abc import ABCMeta, abstractmethod
def _check_methods(C, *methods):
    mro = C.__mro__
    for method in methods:
        for B in mro:
            if method in B.__dict__:
                if B.__dict__[method] is None:
                    return NotImplemented
                break
        else:
            return NotImplemented
    return True


class CharmInterpreter(CharmBaseInterpreter):
    def __init__(self, processor, program, *, name=''):
        super().__init__(program, name=name)
        self.processor = processor
        self.program = program

        self.appeal = processor.appeal
        i = processor.iterator
        if i and not isinstance(i, big.PushbackIterator):
            i = big.PushbackIterator(i)
        self.iterator = i
        self.mapping = processor.mapping

        self.command_converter_key = None

        # The first part of the __call__ loop consumes *opcodes.*
        self.opcodes_prefix = "#---"
        # The second part of the __call__ loop consumes *cmdline arguments.*
        self.cmdline_prefix = "####"

        self.options = self.Options()


    ##
    ## "options"
    ##
    ## Options in Appeal can be hierarchical.
    ## One option can map in child options.
    ## These child options have a limited lifespan.
    ##
    ## Example:
    ##
    ##     def color_option(color, *, brightness=0, hue=0): ...
    ##     def position_option(x, y, z, *, polar=False): ...
    ##
    ##     @app.command()
    ##     def frobnicate(a, b, c, *, color:color_option=Color('BLUE'), position:position_option=Position(0, 0, 0)): ...
    ##
    ## If you then run this command-line:
    ##
    ##     % python3 myscript frobnicate A --color red ...
    ##                                                 ^
    ##                                                 |
    ##      +------------------------------------------+
    ##      |
    ## At THIS point. we've run the Charm program associated
    ## with "--color".  It's mapped in two new options,
    ## "--brightness" (and probably "-b") and "--hue" (and maybe "-h").
    ## These are "child options"; they're children of "--color".
    ##
    ## If the next thing on the command-line is "--brightness"
    ## or "--hue", we handle that option.  But if the next thing
    ## is a positional argument to frobnicate (which will be
    ## the argument supplied to parameter "b"), or the option
    ## "--position", those two child options are *unmapped*.
    ##
    ## We manage these options lifetimes with a *stack* of options dicts.
    ##
    ##  self.options is the options dict at the top of the stack.
    ##  self.stack is a stack of the remaining options dicts,
    ##     with the bottom of the stack at self.options_stack[0].
    ##
    ## An "options token" represents a particular options dict in
    ## the stack.  Each entry in the stack gets a token.  We then
    ## store the toke on the option.  This is how we unmap the
    ## children of a sibling's option; when the user executes an
    ## option on the command-line, we pop the options stack until
    ## the options dict mapped to that token is at the top of the
    ## stack.
    ##

    @big.BoundInnerClass
    class Options:

        def __init__(self, interpreter):
            self.interpreter = interpreter
            self.stack = []
            self.token_to_dict = {}
            self.dict_id_to_token = {}

            # We want to sort options tokens.
            # But serial_number_generator(tuple=True) is ugly and verbose.
            # This seems nicer.

            class OptionsToken:
                def __init__(self, i):
                    self.i = i
                    self.repr = f"<options-{self.i}>"
                def __repr__(self):
                    return self.repr
                def __lt__(self, other):
                    return self.i < other.i
                def __eq__(self, other):
                    return self.i == other.i
                def __hash__(self):
                    return self.i

            def token_generator():
                i = 1
                while True:
                    yield OptionsToken(i)
                    i += 1

            self.next_token = token_generator().__next__
            self.reset()

        def reset(self):
            self.options = options = {}
            self.token = token = self.next_token()
            self.token_to_dict[token] = options
            self.dict_id_to_token[id(options)] = token
            return token

        def push(self):
            self.stack.append((self.options, self.token))
            token = self.reset()

            # if want_prints:
            #     print(f"{self.interpreter.cmdline_prefix} {self.interpreter.ip_spacer} Options.push, new options group {token}")

        def pop(self):
            options_id = id(self.options)
            token = self.dict_id_to_token[options_id]
            del self.dict_id_to_token[options_id]
            del self.token_to_dict[token]

            options, token = self.stack.pop()
            self.options = options
            self.token = token

            # if want_prints:
            #     options = [denormalize_option(option) for option in options]
            #     options.sort(key=lambda s: s.lstrip('-'))
            #     options = "{" + " ".join(options) + "}"
            #     print(f"{self.interpreter.cmdline_prefix} {self.interpreter.ip_spacer} Options.pop: popped to options group {token}, options={options}")

        def pop_until_group(self, token):
            if self.token == token:
                # if want_prints:
                #     print(f"{self.interpreter.cmdline_prefix} {self.interpreter.ip_spacer} Options.pop_until_group: current group has token {token}.  popped 0 times.")
                return
            options_to_stop_at = self.token_to_dict.get(token)
            if not options_to_stop_at:
                raise ValueError(f"Options.pop_until_token: specified non-existent options group token={token}")

            count = 0
            while self.stack and (self.options != options_to_stop_at):
                count += 1
                self.pop()

            if self.options != options_to_stop_at:
                raise ValueError(f"Options.pop_until_token: couldn't find options group with token={token}")

            # if want_prints:
            #     print(f"{self.interpreter.cmdline_prefix} {self.interpreter.ip_spacer} Options.pop_until_group: popped {count} times, down to options group {token}.")

        def unmap_all_child_options(self):
            """
            This unmaps all the *child* options.

            Note that we're only emptying the stack.
            self.options is the top of the stack, and we aren't
            blowing that away.  So when we empty self.stack,
            there's still one options dict left, which was at the
            bottom of the stack; this is the bottom options dict,
            where all the permanently-mapped options live.
            """
            count = len(self.stack)
            for _ in range(count):
                self.pop()

            # if want_prints:
            #     print(f"{self.interpreter.cmdline_prefix} Options.unmap_all_child_options: popped {count} times.")

        def __getitem__(self, option):
            depth = 0
            options = self.options
            token = self.token
            i = reversed(self.stack)
            while True:
                t = options.get(option, None)
                if t is not None:
                    break
                try:
                    options, token = next(i)
                except StopIteration:
                    parent_options = self.interpreter.program.option_to_parent_options.get(option)
                    if parent_options:
                        # parent_options = parent_options.replace("|", "or")
                        parent_options = ", ".join(denormalize_option(o) for o in parent_options)
                        fields = parent_options.rpartition(", ")
                        if fields[1]:
                            fields[1] = ' or '
                        parent_options = "".join(fields)
                        message = f"{denormalize_option(option)} can't be used here, it must be used immediately after {parent_options}"
                    else:
                        message = f"unknown option {denormalize_option(option)}"
                    raise UsageError(message) from None

            program, group_id = t
            total = program.total
            return program, group_id, total.minimum, total.maximum, token

        def __setitem__(self, option, value):
            self.options[option] = value




    def __call__(self):
        (
        option_space_oparg,

        short_option_equals_oparg,
        short_option_concatenated_oparg,
        ) = self.appeal.root.option_parsing_semantics

        # def print(s=None):
        #     if not s:
        #         return
        #     with open("/tmp/xyz", "at") as f:
        #         f.write(s + "\n")

        if self.processor:
            self.processor.log.enter(f"interpreter {self.program}")

        iterator = self.iterator
        mapping = self.mapping

        id_to_group = {}

        command_converter = None

        force_positional = self.appeal.root.force_positional

        # if want_prints:
        #     self.ip_spacer = '    '

        # if want_prints:
        #     charm_separator_line = f"{self.opcodes_prefix}{'-' * 58}"
        #     print(charm_separator_line)
        #     print(f"{self.opcodes_prefix}")
        #     print(f'{self.opcodes_prefix} CharmInterpreter start')
        #     print(f"{self.opcodes_prefix}")
        #     all_options = list(denormalize_option(o) for o in self.program.option_to_child_options)
        #     all_options.sort(key=lambda s: s.lstrip('-'))
        #     all_options = " ".join(all_options)
        #     print(f"{self.opcodes_prefix} all options supported: {all_options}")

        waiting_op = None
        prev_op = None

        ip_zero = f"[{self.repr_ip(0)}]"
        self.ip_spacer = " " * len(ip_zero)
        self.register_spacer = " " * len(ip_zero)

        sentinel = object()

        register_width = 20
        total_register_width = len(ip_zero) + 1 + register_width

        def print_registers(**kwargs):
            """
            Call this *after* changing registers.
            Pass in the *old value* of every registers
            you changed, as a keyword argument named
            for the register.

            If you don't change any registers,
            you should still call this, for
            visual consistency.

            (Why *changed* instead of *changing*?
            That made it easier to copy with the
            two-phase loop and printing next_to_o.)
            """
            nonlocal register_width

            def format_options_group(token):
                l = list(self.options.stack)
                l.append((self.options.options, self.options.token))
                l2 = list()
                for t in l:
                    l2.append(t)
                    if t[1] == token:
                        break
                l2.reverse()
                strings = [f"{token} {list(group)}" for group, token in l2]
                s = " ".join(strings)
                return s

            for t in (
                ('converter', None, self.repr_converter),
                ('o', None, self.repr_converter),
                ('flag', None, lambda value: str(bool(value))),
                ('group', None, lambda value: value.summary() if value else repr(value) ),
                ('groups', None, lambda value: str([group.id for group in value])),
                ('iterator', None, str),
                ('mapping', None, lambda value: 'None' if value is None else " ".join(k for k in value.keys())),
                ('options group', 'options.token', format_options_group),
                ):
                name, attr, format = t
                attr = attr or name

                value = self

                for field in attr.split('.'):
                    value = getattr(value, field)

                fields = [name.rjust(register_width), "|   "]

                old = kwargs.pop(name, sentinel)
                changed = (old != sentinel) and (value != old)
                if changed:
                    fields.append(format(old))
                    fields.append("->")

                fields.append(format(value))

                result = " ".join(fields)
                if len(result) < 50:
                    print(f"{self.opcodes_prefix} {self.register_spacer} {result}")
                else:
                    # split it across two lines
                    print(f"{self.opcodes_prefix} {name:>{total_register_width}} |    {fields[2]}")
                    if changed:
                        print(f"{self.opcodes_prefix} {'':{total_register_width}} | -> {fields[-1]}")

            extras = kwargs.pop('extras', ())
            assert not kwargs
            # if they passed in *extra* stuff, just print it normal
            for name, old, new in extras:
                if (old != sentinel) and (old != new):
                    print(f"{self.opcodes_prefix} {name:>{total_register_width}} |    {old}")
                    print(f"{self.opcodes_prefix} {'  ':>{total_register_width}} | -> {new}")
                else:
                    print(f"{self.opcodes_prefix} {name:>{total_register_width}} |    {new}")

        while self.running() or iterator:
            # if want_prints:
            #     print(f'{self.opcodes_prefix}')
            #     print(charm_separator_line)
            #     if iterator is not None:
            #         try:
            #             printable_iterator = shlex_join(list(reversed(iterator.stack)))
            #         except TypeError:
            #             printable_iterator = repr(iterator)
            #     else:
            #         printable_iterator = "None"
            #     print(f"{self.opcodes_prefix} iterator: {printable_iterator}")

            # The main interpreter loop.
            #
            # This loop has two "parts".
            #
            # In the first part, we iterate over bytecodes until either
            #    * we finish the program, or
            #    * we must consume a command-line argument
            #      (we encounter a "next_to_o" bytecode).
            # If we finish the program, obviously, we're done.
            # If we must consume a command-line argument, we proceed
            # to the second "part".
            #
            # In the second part, we consume a command-line argument.
            # If it's an option, we process the option, and loop.
            # If it's not an option, we consume it as normal and continue.

            # First "part" of the loop: iterate over bytecodes.
            # if want_prints:
            #     print(f"{self.opcodes_prefix}")
            #     print(f"{self.opcodes_prefix} loop part one: execute program {self.program}")
            #     program_printed = self.program

            for ip, op in self:
                prev_op = waiting_op
                waiting_op = op

                # if want_prints:
                #     print(f"{self.opcodes_prefix} ")
                #     if program_printed != self.program:
                #         print(f"{self.opcodes_prefix} now running program {self.program}")
                #         program_printed = self.program
                #         print(f"{self.opcodes_prefix} ")
                #
                #     prefix = f"[{self.repr_ip(ip)}]"

                if op.op == opcode.load_converter:
                    # if want_prints:
                    #     old_converter = self.converter
                    #     old_flag = self.flag
                    converter = self.converters.get(op.key, sentinel)
                    if converter is sentinel:
                        converter = None
                        self.flag = False
                    else:
                        self.flag = True
                    self.converter = converter
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} load_converter | key {op.key}")
                    #     print_registers(converter=old_converter, flag=old_flag)
                    continue

                if op.op == opcode.load_o:
                    # if want_prints:
                    #     old_o = self.o
                    #     old_flag = self.flag
                    o = self.converters.get(op.key, sentinel)
                    if o is sentinel:
                        o = None
                        self.flag = False
                    else:
                        self.flag = True
                    self.o = o
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} load_o | key {op.key}")
                    #     print_registers(o=old_o, flag=old_flag)
                    continue

                if op.op == opcode.converter_to_o:
                    # if want_prints:
                    #     old_o = self.o
                    self.o = self.converter
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} converter_to_o")
                    #     print_registers(o=old_o)
                    continue

                if op.op == opcode.next_to_o:
                    # proceed to second part of interpreter loop
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} next_to_o | switching from loop part 1 to loop part 2")
                    break

                if op.op == opcode.append_to_converter_args:
                    o = self.o
                    converter = self.converter
                    # if want_prints:
                    #     if op.discretionary:
                    #         field_name = "args_queue"
                    #         new = converter.args_queue
                    #     else:
                    #         field_name = "args_converters"
                    #         new = converter.args_converters
                    #     old = list(new.copy())

                    # either queue or append o as indicated
                    (converter.queue_converter if op.discretionary else converter.append_converter)(o)

                    # if want_prints:
                    #     discretionary = "yes" if op.discretionary else "no"
                    #     print(f"{self.opcodes_prefix} {prefix} append_to_converter_args | parameter {op.parameter} | discretionary? {discretionary}")
                    #     new = list(new)
                    #     print_registers(extras = [
                    #         (f'{self.converter_to_key(converter)}.{field_name}', old, new),
                    #         ])
                    continue

                if op.op == opcode.set_in_converter_kwargs:
                    name = op.parameter.name
                    converter = self.converter
                    o = self.o

                    existing = converter.kwargs_converters.get(name, sentinel)
                    if existing is not sentinel:
                        if not ((existing == o) and isinstance(existing, MultiOption)):
                            raise UsageError(f"{program.name} specified more than once.")
                        # we're setting the kwarg to the value it's already set to,
                        # and it's a multioption.  it's fine, we just ignore it.
                        continue

                    # if want_prints:
                    #     new = converter.kwargs_converters
                    #     old = new.copy()

                    converter.unqueue()
                    converter.kwargs_converters[name] = o
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} set_in_converter_kwargs | parameter {op.parameter} | usage {op.usage}")
                    #     print_registers(extras = [
                    #         (f'{self.converter_to_key(converter)}.kwargs_converters', old, new),
                    #         ])

                    continue

                if op.op == opcode.lookup_to_o:
                    # if want_prints:
                    #     old_o = self.o
                    #     old_flag = self.flag

                    value = mapping.get(op.key, sentinel)
                    if value != sentinel:
                        self.flag = True
                        abort = False
                    else:
                        self.flag = False
                        abort = op.required
                        value = None

                    self.o = value
                    # if want_prints:
                    #     required_yes_no = "yes" if op.required else "no"
                    #     print(f"{self.opcodes_prefix} {prefix} lookup_to_o | key='{op.key}' | required? {required_yes_no}")
                    #     abort_yes_no = "yes" if abort else "no"
                    #     print_registers(o=old_o, flag=old_flag, extras=[('abort?', sentinel, abort_yes_no)])

                    if abort:
                        return self.abort()
                    continue

                if op.op == opcode.flush_multioption:
                    assert isinstance(self.o, MultiOption), f"expected o to contain instance of MultiOption but o={self.o}"
                    self.o.flush()
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} flush_multioption")
                    #     print_registers()
                    continue

                if op.op == opcode.remember_converters:
                    self.remember_converters()
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} remember_converters")
                    #     print_registers()
                    continue

                if op.op == opcode.forget_converters:
                    self.forget_converters()
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} forget_converters")
                    #     print_registers()
                    continue

                if op.op == opcode.map_option:
                    # go-faster stripe!
                    # map_option opcodes tend to clump together.
                    # so, run a mini-interpreter loop here to
                    # process all the map instructions at once.
                    self.rewind_one_instruction()
                    for ip, op in self:
                        if op.op != opcode.map_option:
                            break
                        self.options[op.option] = (op.program, op.group)
                        # if want_prints:
                        #     print(f"{self.opcodes_prefix} {prefix} map_option | '{denormalize_option(op.option)}' -> {op.program} | options group {self.options.token}")
                        #     print_registers()
                    self.rewind_one_instruction()
                    continue

                if op.op == opcode.create_converter:
                    # go-faster stripe!
                    # create_converter opcodes tend to clump together.
                    # so, run a mini-interpreter loop here to
                    # process all the map instructions at once.
                    self.rewind_one_instruction()
                    for ip, op in self:
                        if op.op != opcode.create_converter:
                            break

                        cls = self.appeal.map_to_converter(op.parameter)
                        converter = cls(op.parameter, self.appeal)
                        old_o = self.o
                        self.converters[op.key] = self.o = converter
                        if not command_converter:
                            command_converter = converter
                            self.command_converter_key = op.key

                        if self.converter_keys is not None:
                            self.converter_keys.add(op.key)

                        # if want_prints:
                        #     print(f"{self.opcodes_prefix} {prefix} create_converter | cls {cls.__name__} | parameter {op.parameter.name} | key {op.key}")
                        #     print_registers(o=old_o)
                    self.rewind_one_instruction()
                    continue

                if op.op == opcode.set_group:
                    # if want_prints:
                    #     if self.group is None:
                    #         old_group = None
                    #     else:
                    #         old_group = self.group
                    #     old_groups = self.groups.copy()
                    self.group = group = op.group.copy()
                    self.groups.append(group)
                    id_to_group[group.id] = group
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} set_group")
                    #     print_registers(group=old_group, groups=old_groups)
                    continue

                if op.op == opcode.jump:
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} jump | op.address {op.address}")
                    #     print_registers()
                    self.ip.jump(op.address)
                    continue

                if op.op == opcode.indirect_jump:
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} indirect_jump")
                    #     print_registers()
                    self.ip.jump(self.o)
                    continue

                if op.op == opcode.branch_on_flag:
                    # if want_prints:
                    #     branch = "yes" if self.flag else "no"
                    #     print(f"{self.opcodes_prefix} {prefix} branch_on_flag | branch? {branch} | address {op.address}")
                    #     print_registers()
                    if self.flag:
                        self.ip.jump(op.address)
                    continue

                if op.op == opcode.branch_on_not_flag:
                    # if want_prints:
                    #     branch = "yes" if (not self.flag) else "no"
                    #     print(f"{self.opcodes_prefix} {prefix} branch_on_not_flag | branch? {branch} | address {op.address}")
                    #     print_registers()
                    if not self.flag:
                        self.ip.jump(op.address)
                    continue

                if op.op == opcode.test_is_o_true:
                    # if want_prints:
                    #     old_flag = self.flag
                    self.flag = bool(self.o)
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} test_is_o_true")
                    #     print_registers(flag=old_flag)
                    continue

                if op.op == opcode.test_is_o_none:
                    # if want_prints:
                    #     old_flag = self.flag
                    self.flag = self.o == None
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} test_is_o_none")
                    #     print_registers(flag=old_flag)
                    continue

                if op.op == opcode.test_is_o_empty:
                    # if want_prints:
                    #     old_flag = self.flag
                    self.flag = self.o == empty
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} test_is_o_empty")
                    #     print_registers(flag=old_flag)
                    continue

                if op.op == opcode.test_is_o_iterable:
                    # if want_prints:
                    #     old_flag = self.flag

                    # self.flag = isinstance(self.o, Iterator)
                    self.flag = isinstance(self.o, Iterable)
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} test_is_o_iterable")
                    #     print_registers(flag=old_flag)
                    continue

                if op.op == opcode.test_is_o_mapping:
                    # if want_prints:
                    #     old_flag = self.flag
                    self.flag = isinstance(self.o, Mapping)
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} test_is_o_mapping")
                    #     print_registers(flag=old_flag)
                    continue

                if op.op == opcode.test_is_o_str_or_bytes:
                    # if want_prints:
                    #     old_flag = self.flag
                    self.flag = isinstance(self.o, (str, bytes))
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} test_is_o_str_or_bytes")
                    #     print_registers(flag=old_flag)
                    continue

                if op.op == opcode.push_o:
                    # if want_prints:
                    #     old_data_stack = self.data_stack.copy()
                    self.data_stack.append(self.o)
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} push_o")
                    #     print_registers(extras=[('data stack', old_data_stack, self.data_stack)])
                    continue

                if op.op == opcode.pop_o:
                    # if want_prints:
                    #     old_o = self.o
                    #     old_data_stack = self.data_stack.copy()
                    self.o = self.data_stack.pop()
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} pop_o")
                    #     print_registers(o=old_o, extras=[('data stack', old_data_stack, self.data_stack)])
                    continue

                if op.op == opcode.peek_o:
                    # if want_prints:
                    #     old_o = self.o
                    self.o = self.data_stack[-1]
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} peek_o")
                    #     print_registers(o=old_o)
                    continue

                if op.op == opcode.push_flag:
                    # if want_prints:
                    #     old_data_stack = self.data_stack.copy()
                    self.data_stack.append(self.flag)
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} push_flag")
                    #     print_registers(extras=[('data stack', old_data_stack, self.data_stack)])
                    continue

                if op.op == opcode.pop_flag:
                    # if want_prints:
                    #     old_flag = self.flag
                    #     old_data_stack = self.data_stack.copy()
                    self.flag = self.data_stack.pop()
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} pop_flag")
                    #     print_registers(o=old_o, extras=[('data stack', old_data_stack, self.data_stack)])
                    continue

                if op.op == opcode.literal_to_o:
                    # if want_prints:
                    #     old_o = self.o
                    self.o = op.value
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} literal_to_o | value={repr(op.value)}")
                    #     print_registers(o=old_o)
                    continue

                if op.op == opcode.wrap_o_with_iterator:
                    # if want_prints:
                    #     old_o = self.o
                    self.o = iter((self.o,))
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} wrap_o_with_iterator")
                    #     print_registers(o=old_o)
                    continue

                if op.op == opcode.push_mapping:
                    if not isinstance(self.o, Mapping):
                        self.abort(f'object in o is not a Mapping, o={o}')
                    # if want_prints:
                    #     old_mapping_stack = self.mapping_stack.copy()
                    self.mapping_stack.append(self.mapping)
                    self.mapping = mapping = self.o
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} push_mapping")
                    #     print_registers(extras=[('mapping stack', old_mapping_stack, self.mapping_stack)])
                    continue

                if op.op == opcode.pop_mapping:
                    # if want_prints:
                    #     old_mapping = self.mapping
                    #     old_mapping_stack = self.mapping_stack.copy()
                    self.mapping = mapping = self.mapping_stack.pop()
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} pop_mapping")
                    #     print_registers(mapping=old_mapping, extras=[('mapping stack', old_mapping_stack, self.mapping_stack)])
                    continue

                if op.op == opcode.push_iterator:
                    if not isinstance(self.o, Iterable):
                        self.abort(f'object in o is not an Iterator, o={self.o}')
                    # if want_prints:
                    #     old_iterator = self.iterator
                    #     old_iterator_stack = self.iterator_stack.copy()
                    self.iterator_stack.append(self.iterator)
                    iterator = big.PushbackIterator(self.o)

                    # if want_prints:
                    #     # allow us to print the remaining contents of the iterator
                    #     # by examining its stack
                    #     l = list(iterator)
                    #     l.reverse()
                    #     iterator.stack.extend(l)
                    #     iterator.i = None

                    self.iterator = iterator
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} push_iterator")
                    #     print_registers(iterator=old_iterator, extras=[('iterator stack', old_iterator_stack, self.iterator_stack)])
                    continue

                if op.op == opcode.pushback_o_to_iterator:
                    if self.iterator is None:
                        self.abort(f'iterator not set')
                    self.iterator.push(self.o)
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} pushback_o_to_iterator")
                    #     print_registers()
                    continue

                if op.op == opcode.pop_iterator:
                    # if want_prints:
                    #     old_iterator = self.iterator
                    #     old_iterator_stack = self.iterator_stack.copy()
                    self.iterator = iterator = self.iterator_stack.pop()
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} pop_iterator")
                    #     print_registers(iterator=old_iterator, extras=[('iterator stack', old_iterator_stack, self.iterator_stack)])
                    continue

                if op.op == opcode.comment:
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} # {op.comment!r}")
                    continue

                if op.op == opcode.no_op:
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} no_op")
                    continue

                if op.op == opcode.end:
                    # if want_prints:
                    #     cpse = self.CharmProgramStackEntry()
                    self.end()
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} end")
                    #     print_registers(
                    #         converter=cpse.converter,
                    #         o=cpse.o,
                    #         flag=cpse.flag,
                    #         group=cpse.group,
                    #         groups=cpse.groups,
                    #         )
                    continue

                if op.op == opcode.abort:
                    # if want_prints:
                    #     print(f"{self.opcodes_prefix} {prefix} abort | message '{op.message}'")
                    #     print_registers()
                    self.abort(op.message)
                    continue

                if op.op == opcode.create_converter:
                    raise RuntimeError("huh? we should have handled all create_converter opcodes already.")

                raise ConfigurationError(f"unhandled opcode | op {op}")

            else:
                # we finished the program
                # if want_prints:
                #     print(f"{self.opcodes_prefix} ")
                #     if self.aborted:
                #         print(f"{self.opcodes_prefix} aborted!")
                #     else:
                #         print(f"{self.opcodes_prefix} ended.")
                #     print(f"{self.opcodes_prefix} ")
                op = None

            if self.aborted:
                break


            # Second "part" of the loop: consume a positional argument from the command-line.
            #
            # We've either paused or finished the program.
            #   If we've paused, it's because the program wants us
            #     to consume an argument.  In that case op
            #     will be a 'next_to_o' op.
            #   If we've finished the program, op will be None.
            assert (op == None) or (op.op == opcode.next_to_o), f"op={op}, expected either None or next_to_o"

            # Technically we *loop* over iterator.
            # But in practice we usually only consume one argument at a time.
            #
            # for a in iterator:
            #    * if a is an option (or options),
            #      push that program (programs) and resume
            #      the charm interpreter.
            #    * if a is the special value '--', remember
            #      that all subsequent command-line arguments
            #      can no longer be options, and continue to
            #      the next a in iterator.  (this is the only case
            #      in which we'll consume more than one argument
            #      in this loop.)
            #    * else a is a positional argument.
            #      * if op is next_to_o, consume it and
            #        resume the charm interpreter.
            #      * else, hmm, we have a positional argument
            #        we don't know what to do with.  the program
            #        is done, and we don't have a next_to_o
            #        to give it to.  so push it back onto iterator
            #        and exit.  (hopefully the argument is the
            #        name of a command/subcomand.)

            # if want_prints:
            #     print_loop_start = True

            stay_in_loop_two = True

            while stay_in_loop_two:
                # if want_prints:
                #     if print_loop_start:
                #         if iterator is not None:
                #             try:
                #                 printable_iterator = shlex_join(list(reversed(iterator.stack)))
                #             except TypeError:
                #                 printable_iterator = repr(iterator)
                #         else:
                #             printable_iterator = "None"
                #         print(f"{self.cmdline_prefix} ")
                #         print(f"{self.cmdline_prefix} loop part 2: consume argument(s): op={op} iterator: {printable_iterator}")
                #         print(f"{self.cmdline_prefix} ")
                #         print_loop_start = False

                if not iterator:
                    # we need a positional argument, but we don't have one.
                    # stop processing; we'll figure out if there was an error below.
                    if self.ip:
                        self.ip.end()
                    self.call_stack.clear()
                    break

                for a in iterator:
                    # if want_prints:
                    #     try:
                    #         printable_iterator = shlex_join(list(reversed(iterator.stack)))
                    #     except TypeError:
                    #         printable_iterator = repr(iterator)
                    #     print(f"{self.cmdline_prefix} argument: {a!r}  remaining: {printable_iterator}")
                    #     print(f"{self.cmdline_prefix}")

                    # Is this command-line argument a "positional argument", or an "option"?
                    # In this context, a "positional argument" can be either a conventional
                    # positional argument on the command-line, or an "oparg".

                    # If force_positional is true, we encountered "--" on the command-line.
                    # This forces Appeal to ignore dashes and process all subsequent
                    # arguments as positional arguments.

                    # If we're consuming opargs, we ignore leading dashes,
                    # and all arguments are forced to be opargs
                    # until we've consume all the opargs we need.
                    is_oparg = op and (op.op == opcode.next_to_o) and op.is_oparg

                    is_positional_argument = (
                        force_positional
                        or is_oparg
                        )

                    if (not is_positional_argument) and isinstance(a, str):
                        # Only do these checks if a is actually a str.
                        # (If it's a float from a TOML file or something,
                        # it can't be an option, now can it!)

                        # If the argument doesn't start with a dash,
                        # it can't be an option, therefore it must be a positional argument.
                        doesnt_start_with_a_dash = not a.startswith("-")

                        # If the argument is a single dash, it isn't an option,
                        # it's a positional argument.  This is an old UNIX idiom;
                        # if you were expecting a filename and you got "-", you should
                        # use the appropriate stdio file (stdin/stdout) there.
                        is_a_single_dash = a == "-"

                        is_positional_argument = (
                            doesnt_start_with_a_dash
                            or is_a_single_dash
                            )

                    if is_positional_argument:
                        if not op:
                            # if want_prints:
                            #     print(f"{self.cmdline_prefix}  positional argument we don't want.")
                            #     print(f"{self.cmdline_prefix}  maybe somebody else will consume it someday.")
                            #     print(f"{self.cmdline_prefix}  exit.")
                            iterator.push(a)
                            return self.converters[self.command_converter_key]

                        # set register "o" to our string and return to running bytecodes.
                        # if want_prints:
                        #     old_o = self.o
                        #     if self.group is None:
                        #         old_group = None
                        #     else:
                        #         old_group = self.group.copy()
                        self.o = a
                        self.flag = True
                        if self.group:
                            self.group.count += 1
                            self.group.laden = True

                        if not is_oparg:
                            self.options.unmap_all_child_options()

                        # if want_prints:
                        #     print(f"{self.cmdline_prefix}")
                        #     print(f"{self.opcodes_prefix} {prefix} next_to_o | required={op.required} | is_oparg={op.is_oparg}")
                        #     print(f"{self.opcodes_prefix} {prefix} got '{a}'")
                        #     print_registers(o=old_o, group=old_group)

                        stay_in_loop_two = False
                        break

                    if not option_space_oparg:
                        raise ConfigurationError("oops, currently the only supported value of option_space_oparg is True")

                    if a == "--":
                        # we shouldn't be able to reach this twice.
                        # if the user specifies -- twice on the command-line,
                        # the first time turns of option processing, which means
                        # it should be impossible to get here.
                        assert not force_positional
                        force_positional = self.appeal.root.force_positional = True
                        # if want_prints:
                        #     print(f"{self.cmdline_prefix}  '--', force_positional=True")
                        continue

                    # it's an option!
                    double_dash = a.startswith("--")
                    pushed_remainder = False

                    # split_value is the value we "split" from the option string.
                    # In these example, split_value is 'X':
                    #     --option=X
                    #     -o=X
                    # and, if o takes exactly one optional argument,
                    # and short_option_concatenated_oparg is true:
                    #     -oX
                    # If none of these syntaxes (syntices?) is used,
                    # split_value is None.
                    #
                    # Literally we handle it by splitting it off,
                    # then pushing it *back* onto iterator, so the option
                    # program can consume it.  Thus we actually transform
                    # all the above examples into
                    #     -o X
                    #
                    # Note: split_value can be an empty string!
                    #     -f=
                    # So, simply checking truthiness is insufficient.
                    # You *must* check "if split_value is None".
                    split_value = None

                    try_to_split_value = double_dash or short_option_equals_oparg
                    if try_to_split_value:
                        a, equals, _split_value = a.partition("=")
                        if equals:
                            split_value = _split_value
                    else:
                        split_value = None

                    if double_dash:
                        option = a
                        program, group_id, minimum_arguments, maximum_arguments, token = self.options[option]
                    else:
                        ## In Appeal,
                        ##      % python3 myscript foo -abcde
                        ## must be EXACTLY EQUIVALENT TO
                        ##      % python3 myscript foo -a -b -c -d -e
                        ##
                        ## The best way to handle this is to transform the former
                        ## into the latter.  Every time we encounter a single-dash
                        ## option, consume just the first letter, and if the rest
                        ## is more options, reconstruct the remaining short options
                        ## and push it onto the 'iterator' pushback iterator.
                        ## For example, if -a is an option that accepts no opargs,
                        ## we transform
                        ##      % python3 myscript foo -abcde
                        ## into
                        ##      % python3 myscript foo -a -bcde
                        ## and then handle "-a".
                        ##
                        ## What about options that take opargs?  Except for
                        ## the special case of short_option_concatenated_oparg,
                        ## options that take opargs have to be the last short option.

                        # strip off this short option by itself:
                        option = a[1]
                        program, group_id, minimum_arguments, maximum_arguments, token = self.options[option]

                        # handle the remainder.
                        remainder = a[2:]
                        if remainder:
                            if maximum_arguments == 0:
                                # more short options.  push them back onto iterator.
                                pushed_remainder = True
                                remainder = "-" + remainder
                                iterator.push(remainder)
                                # if want_prints:
                                #     print(f"{self.cmdline_prefix} isolating '-{option}', pushing remainder '{remainder}' back onto iterator")
                            elif maximum_arguments >= 2:
                                if minimum_arguments == maximum_arguments:
                                    number_of_arguments = maximum_arguments
                                else:
                                    number_of_arguments = f"{minimum_arguments} to {maximum_arguments}"
                                raise UsageError(f"-{option}{remainder} isn't allowed, -{option} takes {number_of_arguments} arguments, it must be last")
                            # in the remaining cases, we know maximum_arguments is 1
                            elif short_option_concatenated_oparg and (minimum_arguments == 0):
                                # Support short_option_concatenated_oparg.
                                #
                                # If a short option takes *exactly* one *optional*
                                # oparg, you can smash the option and the oparg together.
                                # For example, if short option "-f" takes exactly one
                                # optional oparg, and you want to supplythe oparg "guava",
                                # you can do
                                #    -f=guava
                                #    -f guava
                                # and in ONLY THIS CASE
                                #    -fguava
                                #
                                # Technically POSIX doesn't allow us to support this:
                                #    -f guava
                                #
                                # On the other hand, there's a *long list* of things
                                # POSIX doesn't allow us to support:
                                #
                                #    * short options with '=' (split_value, e.g. '-f=guava')
                                #    * long options
                                #    * subcommands
                                #    * options that take multiple opargs
                                #
                                # So, clearly, exact POSIX compliance is not of
                                # paramount importance to Appeal.
                                #
                                # Get with the times, you musty old fogeys!

                                if split_value is not None:
                                    raise UsageError(f"-{option}{remainder}={split_value} isn't allowed, -{option} must be last because it takes an argument")
                                split_value = remainder
                            else:
                                assert minimum_arguments == maximum_arguments == 1
                                raise UsageError(f"-{option}{remainder} isn't allowed, -{option} must be last because it takes an argument")

                    laden_group = id_to_group[group_id]

                    denormalized_option = denormalize_option(option)
                    # if want_prints:
                    #     print(f"{self.cmdline_prefix} option {denormalized_option}")
                    #     print(f"{self.cmdline_prefix} {self.ip_spacer} program={program}")
                    #     print(f"{self.cmdline_prefix} {self.ip_spacer} group={laden_group.summary()}")
                    #     print(f"{self.cmdline_prefix}")

                    # mark argument group as having had stuff done in it.
                    laden_group.laden = True

                    # we have an option to run.
                    # the existing next_to_o op will have to wait.
                    if op:
                        assert op.op == opcode.next_to_o
                        self.rewind_one_instruction()
                        op = None

                    # throw away child options mapped below our option's sibling.
                    self.options.pop_until_group(token)

                    # and push a fresh options dict.
                    self.options.push()

                    if split_value is not None:
                        if maximum_arguments != 1:
                            if maximum_arguments == 0:
                                raise UsageError(f"{denormalized_option}={split_value} isn't allowed, because {denormalize_option} doesn't take an argument")
                            if maximum_arguments >= 2:
                                raise UsageError(f"{denormalized_option}={split_value} isn't allowed, because {denormalize_option} takes multiple arguments")
                        iterator.push(split_value)
                        # if want_prints:
                        #     print(f"{self.cmdline_prefix} {self.ip_spacer} pushing split value {split_value!r} back onto iterator")

                    # self.push_context()
                    self.call(program)
                    stay_in_loop_two = False

                    # if want_prints:
                    #     print(f"{self.cmdline_prefix}")
                    #     print(f"{self.cmdline_prefix} call program={program}")
                    #     print_registers(extras=[('pushed context', sentinel, self.call_stack[-1])])

                    break

        satisfied = True
        if self.group:
            ag = self.group
            assert ag

            if not ag.satisfied():
                if ag.minimum == ag.maximum:
                    plural = "" if ag.minimum == 1 else "s"
                    middle = f"{ag.minimum} argument{plural}"
                else:
                    middle = f"at least {ag.minimum} arguments but no more than {ag.maximum} arguments"
                program = self.program
                message = f"{program.name} requires {middle} in this argument group."
                raise UsageError(message)

        # if want_prints:
        #     print(f"{self.opcodes_prefix}")
        #     print(f"{self.opcodes_prefix} ending parse.")
        #     finished_state = "did not finish" if self else "finished"
        #     print(f"{self.opcodes_prefix}      program {finished_state}.")
        #     if iterator:
        #         print(f"{self.opcodes_prefix}      remaining cmdline: {list(reversed(iterator.stack))}")
        #     else:
        #         print(f"{self.opcodes_prefix}      cmdline was completely consumed.")

        # if want_prints:
        #     print(charm_separator_line)
        #     print()

        if self.processor:
            self.processor.log.exit()

        if self.aborted:
            return None

        return self.converters[self.command_converter_key]



class Converter:
    """
    A Converter object calls a Python function, filling
    in its parameters using command-line arguments.
    It introspects the function passed in, creating
    a tree of sub-Converter objects underneath it.

    A Converter
    """
    callable = None
    def __init__(self, parameter, appeal):
        self.parameter = parameter
        self.appeal = appeal

        callable = dereference_annotated(parameter.annotation)
        default = parameter.default

        # self.fn = callable
        self.callable = callable

        if not hasattr(self, '__signature__'):
            self.__signature__ = self.get_signature(parameter)

        # self.root = root or self
        self.default = default
        self.name = parameter.name

        # output of analyze().  input of parse() and usage().
        # self.program = None

        self.docstring = self.callable.__doc__

        self.usage_str = None
        self.summary_str = None
        self.doc_str = None

        self.reset()

    def __repr__(self):
        return f"<{self.__class__.__name__} callable={self.callable.__name__}>"

    @classmethod
    def get_signature(cls, parameter):
        if hasattr(cls, "__signature__"):
            return cls.__signature__
        return inspect.signature(dereference_annotated(parameter.annotation), follow_wrapped=False)

    def reset(self):
        """
        Called to reset the mutable state of a converter.

        Note that MultiOption is a subclass of Converter,
        and calls reset() each time it's invoked.
        """

        # names of parameters that we filled with strings
        # from the command-line.  if this converter throws
        # a ValueError or TypeError when executed, and it's
        # an interior node in the annotations tree, we'll know
        # exactly which command-line argument failed to convert
        # and can display more pertinent usage information.
        self.string_parameters = []

        # queued and args_queue are used to manage converters
        # that might not actually be needed.  some converters
        # are created proactively but are never actually used.
        # Appeal used to queue them, then remove them; queueing
        # them and not flushing them is easier.
        #
        # queued is a flag.  if queued is not None, it points to our
        #     parent converter, and it represents a request to notify
        #     the parent when you get a cmdline argument added to
        #     you--either positional or keyword-only.
        # args_queue is a list of child converters that are waiting to be
        #     flushed into our args_converters.
        #
        # Note that self.queued may be set to our parent even when we
        # *aren't* in our parent's args_queue.  Our converter might be
        # a required argument to our parent, but our parent is optional and
        # has been queued.  (Or our parent's parent, etc.  It's a tree.)
        self.queued = None
        self.args_queue = collections.deque()

        # collections of converters we'll use to compute *args and **kwargs.
        # contains either raw strings or Converter objects which we'll call.
        #
        # these are the output of parse(), specifically the CharmInterpreter,
        # and the input of convert().
        self.args_converters = []
        self.kwargs_converters = {}

        # these are the output of convert(), and the input for execute().
        self.args = []
        self.kwargs = {}


    ## "discretionary" converters, and queueing and unqueueing
    ##
    ## If a group is "optional", that means there's at least
    ## one parameter with a default value.  If that parameter
    ## has a converter, we don't know in advance whether or not
    ## we're actually gonna call it.  We'll only call it if we
    ## fill one of its parameters with a positional argument,
    ## and we can't really predict in advance whether or not
    ## that's gonna happen.
    ##
    ## For example:
    ##   * the first actual positional argument is optional
    ##   * it's nested three levels deep in the annotation tree
    ##   * we have command-line arguments waiting in iterator
    ##     but the next argument is an option, and we don't know
    ##     how many opargs it wants to consume until we run it
    ##
    ## Or:
    ##   * all the parameters to the converter are optional
    ##   * the converter maps an option
    ##   * sometime in the deep future the user invokes
    ##     that option on the command-line
    ##
    ## So... when should we create the converter?  The best
    ## possible time would be "just-in-time", at the moment
    ## we know we need it and no sooner.  But, the way Appeal
    ## works internally, it makes things a lot smoother to
    ## just pre-allocate a converter, then eventually throw it
    ## away if we don't need it.
    ##
    ## Observe that:
    ##   * First, we're only talking about optional groups.
    ##     So this only applies to converters that get appended
    ##     to args.
    ##       * Converters that handle options get set in kwargs,
    ##         so there's no mystery about whether or not they're
    ##         getting used.  Appeal already creates *those*
    ##         only on demand.
    ##   * Second, optional groups only become required once we
    ##     consume an argument in that group, or invoke one of
    ##     the options mapped in that group.
    ##
    ## Here's what Appeal does.  In a nutshell, converters mapped
    ## in optional groups get created early, but they don't get
    ## appended to their parent's args_converters right away.
    ## These converters that we might not need are called
    ## "discretionary" converters.  Converters that aren't
    ## discretionary are "mandatory" converters.  A "discretionary"
    ## converter becomes mandatory at the moment it (or a
    ## converter below it in the annotations tree) gets a string
    ## argument from the command-line appended to it, or the user
    ## invokes an option that maps to one of its (or one of
    ## its children's) options.
    ##
    ## When the CharmInterpreter creates a mandatory converter
    ## for a positional argument, that converter is immediately
    ## queued in its parent converter's args_converters list.
    ## But discretionary converters get "queued", which means
    ## it goes to a different place: the parent converter's other
    ## list, args_queue.
    ##
    ## At the moment that a discretionary converter becomes
    ## mandatory--a string from the command-line gets appended
    ## to that converter, or one of the options it maps gets
    ## invoked--we "unqueue" that queued converter, moving it
    ## from its parent's args_queue list to its parent's
    ## args_converters list.
    ##
    ## Two complexities arise from this:
    ##     * If there's a converter B queued in front of
    ##       converter A in same parent, and B becomes mandatory,
    ##       A becomes mandatory too.  And you need to flush
    ##       it first, so that the parent gets its positional
    ##       arguments in the right order.  So, when we want to
    ##       flush a particular converter, we flush all the entries
    ##       in the queue before it too.
    ##     * An optional argument group can have an entire tree
    ##       of converters underneath it, themselves variously
    ##       optional or required.  So, when a converter has been
    ##       queued, and it gets a child converter appended to it
    ##       (or queued to it) it also tells its children "Tell me
    ##       when I need to unqueue".  If one of these children
    ##       gets a positional argument that is a string, or gets
    ##       one of its options invoked, it'll tell its *parent*
    ##       to unqueue.
    ##
    ## Internally, we only use one field in the child converter
    ## for all this: "queued".
    ##     * If "queued" is None, the converter is mandatory,
    ##       and all its parents are mandatory.
    ##     * If "queued" is not None, either the converter
    ##       is optional, or one of its parents is optional,
    ##       and "queued" points to its parent.
    ##
    ## -----
    ##
    ## One final note.  When I was testing this code against the
    ## test suite, I was quite surprised to see the same converter
    ## queued and flushed multiple times.  I investigated, and
    ## found it wasn't actually the *same* converter, but it had
    ## the same name and was going in the same place.  It was a
    ## converter for *args, and the test case looped five times.
    ## So it actually was five identical but different converters,
    ## going so far as to use the same converter key.
    ## (It might be nice to be able to tell them apart in the log.)

    def append_converter(self, o):
        """
        Append o directly to our args_converters list.

        If o is not a Converter, also unqueue ourselves
        (and recursively all our parents too).

        If o is a Converter, and we or one of our parents
        is discretionary, ask o to notify us if it gets
        a string positional argument appended to it,
        or if one of its options is invoked.
        """
        # print(f">> {self=} appended to {parent=}\n")
        self.args_converters.append(o)

        if not isinstance(o, Converter):
            self.unqueue()
        else:
            assert not o.queued
            # ask
            if self.queued:
                o.queued = self

    def queue_converter(self, o):
        """
        Append o to our args_queue list.
        o must be a discretionary Converter object.
        """
        # print(f">> {self=} queued for {parent=}\n")
        assert not o.queued
        o.queued = self
        self.args_queue.append(o)

    def unqueue(self, converter=None):
        """
        Unqueue ourselves from our parent.

        Also tells our parent to unqueue itself,
        recursively back up to the root of this
        discretionary converter subtree.

        Also, if converter is not None,
        and converter is in our args_queue,
        converter is a discretionary converter
        in our args_queue, and we flush the
        args_queue until converter is unqueued
        (aka flushed).  If converter isn't in
        args_queue, args_queue doesn't change.
        """
        if self.queued:
            self.queued.unqueue(self)
            self.queued = None

        if not converter:
            return

        try:
            # if converter isn't in args_queue, this will throw ValueError
            self.args_queue.index(converter)

            while True:
                child = self.args_queue.popleft()
                self.args_converters.append(child)
                child.queued = None
                if child == converter:
                    break
        except ValueError:
            pass

    def convert(self, processor):
        for iterable in (self.args_converters, self.kwargs_converters.values()):
            for converter in iterable:
                if converter and isinstance(converter, Converter):
                    converter.convert(processor)

        try:
            for converter in self.args_converters:
                if converter and isinstance(converter, Converter):
                    converter = converter.execute(processor)
                self.args.append(converter)
            for name, converter in self.kwargs_converters.items():
                if converter and isinstance(converter, Converter):
                    converter = converter.execute(processor)
                self.kwargs[name] = converter
        except ValueError as e:
            # we can examine "converter", the exception must have
            # happened in an execute call.
            raise UsageError(f"invalid value something something converter {converter!r}, converter.args={converter.args!r}")

    def execute(self, processor):
        executor = processor.execute_preparers(self.callable)
        return executor(*self.args, **self.kwargs)


class InferredConverter(Converter):
    def __init__(self, parameter, appeal):
        if not parameter.default:
            raise ConfigurationError(f"empty {type(parameter.default)} used as default, so we can't infer types")
        p2 = inspect.Parameter(parameter.name, kind=parameter.kind, annotation=type(parameter.default), default=parameter.default)
        super().__init__(p2, appeal)

    @classmethod
    def get_signature(cls, parameter):
        if hasattr(cls, "__signature__"):
            return cls.__signature__
        return inspect.signature(type(parameter.default), follow_wrapped=False)

class InferredSequenceConverter(InferredConverter):
    @classmethod
    def get_signature(cls, parameter):
        if hasattr(cls, "__signature__"):
            return cls.__signature__
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

    def execute(self, processor):
        return self.callable(self.args)



class SimpleTypeConverter(Converter):
    def __init__(self, parameter, appeal):
        self.appeal = appeal
        self.default = parameter.default

        self.name = parameter.name

        self.string_parameters = []

        self.value = None

        self.queued = None
        self.args_queue = collections.deque()
        self.args_converters = []
        self.kwargs_converters = {}

        self.options_values = {}
        self.help_options = {}
        self.help_arguments = {}

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.callable} args_converters={self.args_converters} kwargs_converters={self.kwargs_converters} value={self.value}>"

    def convert(self, processor):
        # if 1:
        if self.value is not None:
            raise RuntimeError("why a second time, fool")
        argument_count = (len(self.args_converters) + len(self.kwargs_converters))
        assert 0 <= argument_count <= 1, f"{self.__class__.__name__}: argument_count={argument_count!r}, should be 0 or 1, self.args_converters={self.args_converters!r} self.kwargs_converters={self.kwargs_converters!r}"
        if not argument_count:
            # explicitly allow "make -j"
            if self.default is not empty:
                return self.default
            raise UsageError(f"no argument supplied for {self}, we should have raised an error earlier huh.")
        try:
            if self.kwargs_converters:
                for v in self.kwargs_converters.values():
                    self.args_converters.append(v)
                self.kwargs_converters.clear()
            self.value = self.callable(self.args_converters[0])
        except ValueError as e:
            raise UsageError(f"invalid value {self.args_converters[0]} for {self.name}, must be {self.callable.__name__}")


    def execute(self, processor):
        return self.value


simple_type_signatures = {}

def parse_bool(bool) -> bool: pass
class SimpleTypeConverterBool(SimpleTypeConverter):
    __signature__ = inspect.signature(parse_bool)
    callable = bool
simple_type_signatures[bool] = SimpleTypeConverterBool

def parse_complex(complex) -> complex: pass
class SimpleTypeConverterComplex(SimpleTypeConverter):
    __signature__ = inspect.signature(parse_complex)
    callable = complex
simple_type_signatures[complex] = SimpleTypeConverterComplex

def parse_float(float) -> float: pass
class SimpleTypeConverterFloat(SimpleTypeConverter):
    __signature__ = inspect.signature(parse_float)
    callable = float
simple_type_signatures[float] = SimpleTypeConverterFloat

def parse_int(int) -> int: pass
class SimpleTypeConverterInt(SimpleTypeConverter):
    __signature__ = inspect.signature(parse_int)
    callable = int
simple_type_signatures[int] = SimpleTypeConverterInt

def parse_str(str) -> str: pass
class SimpleTypeConverterStr(SimpleTypeConverter):
    __signature__ = inspect.signature(parse_str)
    callable = str
simple_type_signatures[str] = SimpleTypeConverterStr


class BaseOption(Converter):
    pass

class InferredOption(BaseOption):
    def __init__(self, parameter, appeal):
        if not parameter.default:
            raise ConfigurationError(f"empty {type(parameter.default)} used as default, so we can't infer types")
        p2 = inspect.Parameter(parameter.name, kind=parameter.kind, annotation=type(parameter.default), default=parameter.default)
        super().__init__(p2, appeal)

    @classmethod
    def get_signature(cls, parameter):
        if hasattr(cls, "__signature__"):
            return cls.__signature__
        return inspect.signature(type(parameter.default), follow_wrapped=False)

class InferredSequenceOption(InferredOption):
    @classmethod
    def get_signature(cls, parameter):
        if hasattr(cls, "__signature__"):
            return cls.__signature__
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

    def execute(self, processor):
        return self.callable(self.args)


def strip_first_argument_from_signature(signature):
    # suppresses the first argument from the signature,
    # regardless of its name.
    # (the name "self" is traditional, but it's mostly not enforced
    # by the language.  though I think no-argument super() might depend on it.)
    parameters = collections.OrderedDict(signature.parameters)
    if not parameters:
        raise ConfigurationError(f"strip_first_argument_from_signature: was passed zero-argument signature {signature}")
    for name, p in parameters.items():
        break
    del parameters[name]
    if 'return' in parameters:
        return_annotation = parameters['return']
        del parameters['return']
    else:
        return_annotation = empty
    return inspect.Signature(parameters.values(), return_annotation=return_annotation)


def strip_self_from_signature(signature):
    # suppresses self from the signature.
    parameters = collections.OrderedDict(signature.parameters)
    if not parameters:
        return signature
    # the self parameter must be first
    for name, p in parameters.items():
        break
    if name != "self":
        return signature
    del parameters['self']
    if 'return' in parameters:
        return_annotation = parameters['return']
        del parameters['return']
    else:
        return_annotation = empty
    return inspect.Signature(parameters.values(), return_annotation=return_annotation)


class Option(BaseOption):
    def __init__(self, parameter, appeal):
        # the callable passed in is ignored
        p2 = inspect.Parameter(parameter.name, kind=parameter.kind, annotation=self.option, default=parameter.default)
        super().__init__(p2, appeal)
        self.init(parameter.default)

    def __repr__(self):
        return f"<{self.__class__.__name__}>"

    @classmethod
    def get_signature(cls, parameter):
        if hasattr(cls, "__signature__"):
            return cls.__signature__
        # we need the signature of cls.option
        # but *without self*
        signature = inspect.signature(cls.option, follow_wrapped=False)
        signature = strip_first_argument_from_signature(signature)
        return signature

    def execute(self, processor):
        self.option(*self.args, **self.kwargs)
        return self.render()

    # Your subclass of SingleOption or MultiOption is required
    # to define its own option() and render() methods.
    # init() is optional.

    # init() is called at initialization time.
    # This is a convenience; you can also overload __init__
    # if you like.  But that means staying in sync with
    # the parameters to __init__ and still change sometimes.
    def init(self, default):
        pass

    # option() is called every time your option is specified
    # on the command-line.  For an Option, this will be exactly
    # one time.  For a MultiOption, this will be one or more times.
    # (Appeal will never construct your Option object unless
    # it's going to call your option method at least once.)
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
    #   * If your option method only has *optional* parameters,
    #     it's possible Appeal will call it with zero arguments.
    #     (This is how you implement "make -j" for example.)
    @abstractmethod
    def option(self):
        pass

    # render() is called exactly once, after option() has been
    # called for the last time.  it should return the "value"
    # for the option.
    @abstractmethod
    def render(self):
        pass


# the old name, now deprecated
SingleOption = Option


def parse_bool_option() -> bool: pass
class BooleanOptionConverter(Option):
    __signature__ = inspect.signature(parse_bool_option)

    def init(self, default):
        self.value = default

    def option(self):
        self.value = not self.value

    def render(self):
        return self.value

class MultiOption(Option):
    def __init__(self, parameter, appeal):
        self.multi_converters = []
        self.multi_args = []
        # the callable passed in is ignored
        p2 = inspect.Parameter(parameter.name, kind=parameter.kind, annotation=self.option, default=parameter.default)
        super().__init__(p2, appeal)

    def flush(self):
        self.multi_converters.append((self.args_converters, self.kwargs_converters))
        self.reset()

    def convert(self, processor):
        self.flush()
        for args, kwargs in self.multi_converters:
            self.args = []
            self.kwargs = {}
            self.args_converters = args
            self.kwargs_converters = kwargs
            super().convert(processor)
            self.multi_args.append((self.args, self.kwargs))

    def execute(self, processor):
        for args, kwargs in self.multi_args:
            # print(f"CALLING self.option={self.option} args={args} kwargs={kwargs}")
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
            return cls.__getitem_single__(t)
        return cls.__getitem_iterable__(t)


    def __getitem_single__(cls, t):
        class accumulator(cls):
            __name__ = f'{cls.__name__}[{t.__name__}]'

            def option(self, arg:t):
                self.values.append(arg)
        return accumulator

    def __getitem_iterable__(cls, t):
        iterable_type = type(t)
        t_names = "_".join(ti.__name__ for ti in t)

        class accumulator(cls):
            __name__ = f'{cls.__name__}[{t_names}]'

            def option(self, *args):
                if type(args) != iterable_type:
                    args = iterable_type(args)
                self.values.append(args)

        parameters = []
        padding = math.ceil(math.log10(len(t)))
        for i, value in enumerate(t):
            p = inspect.Parameter(
                name = f'arg{i:0{padding}}',
                default = inspect.Parameter.empty,
                annotation = value,
                kind = inspect.Parameter.POSITIONAL_ONLY,
                )
            parameters.append(p)

        signature = inspect.signature(accumulator.option)
        updated_signature = signature.replace(
            parameters=parameters,
            return_annotation=inspect.Signature.empty,
            )

        accumulator.__signature__ = updated_signature

        return accumulator

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
            raise ConfigurationError("MappingMeta[] must have at least two types")
        if len(t) == 2:
            return cls.__getitem_key_single__(t[0], t[1])
        return cls.__getitem_key_iterable__(t[0], t[1:])

    def __getitem_key_single__(cls, k, v):
        class accumulator(cls):
            __name__ = f'{cls.__name__}[{k.__name__}_{v.__name__}]'

            def option(self, key:k, value:v):
                if key in self.dict:
                    raise UsageError("defined {key} more than once")
                self.dict[key] = value
        return accumulator

    def __getitem_key_iterable__(cls, key, values):
        iterable_type = type(values)
        values_names = "_".join(ti.__name__ for ti in values)

        class accumulator(cls):
            __name__ = f'{cls.__name__}[{key.__name__}_{values_names}]'

            def option(self, key, *values):
                if key in self.dict:
                    raise UsageError("defined {key} more than once")
                if type(values) != iterable_type:
                    values = iterable_type(values)
                self.dict[key] = values

        parameters = [
            inspect.Parameter(
                name = 'key',
                default = inspect.Parameter.empty,
                annotation = key,
                kind = inspect.Parameter.POSITIONAL_ONLY,
                )]

        padding = math.ceil(math.log10(len(values)))
        for i, value in enumerate(values):
            p = inspect.Parameter(
                name = f'value{i:0{padding}}',
                default = inspect.Parameter.empty,
                annotation = value,
                kind = inspect.Parameter.POSITIONAL_ONLY,
                )
            parameters.append(p)

        signature = inspect.signature(accumulator.option)
        updated_signature = signature.replace(
            parameters=parameters,
            return_annotation=inspect.Signature.empty,
            )

        accumulator.__signature__ = updated_signature

        return accumulator


class mapping(MultiOption, metaclass=MappingMeta):
    def init(self, default):
        self.dict = {}
        if default is not empty:
            self.dict.update(dict(default))

    def option(self, key:str, value:str):
        if key in self.dict:
            raise UsageError("defined {key} more than once")
        self.dict[key] = value

    def render(self):
        return self.dict


@must_be_instance
def split(*separators, strip=False):
    """
    Creates a converter function that splits a string
    based on one or more separator strings.

    If you don't supply any separators, splits on
    any whitespace.

    If strip is True, also strips the separators
    from the beginning and end of the string.
    """
    if not all((s and isinstance(s, str)) for s in separators):
        raise ConfigurationError("split(): every separator must be a non-empty string")

    def split(str):
        return list(big.multisplit(str, separators, strip=strip))
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
        raise ConfigurationError("validate() called without any values.")
    if type == None:
        type = builtins.type(values[0])
    failed = []
    for value in values:
        if not isinstance(value, type):
            failed.append(value)
    if failed:
        failed = " ".join(repr(x) for x in failed)
        raise ConfigurationError("validate() called with these non-homogeneous values {failed}")

    values_set = set(values)
    def validate(value:type):
        if value not in values_set:
            raise UsageError(f"illegal value {value!r}, should be one of {' '.join(repr(v) for v in values)}")
        return value
    return validate

@must_be_instance
def validate_range(start, stop=None, *, type=None, clamp=False):
    """
    Creates a converter function that validates that
    a value from the command-line is within a range.

        start and stop are like the start and stop
            arguments for range(), except values
            can be less-than *or equal to* stop.

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
                raise UsageError(f"illegal value {value}, should be {start} <= value < {stop}")
            if value >= stop:
                value = stop
            else:
                value = start
        return value
    return validate_range



# utility function, not published as one of the _to_converter callables
def _simple_type_to_converter(parameter, callable):
    cls = simple_type_signatures.get(callable)
    if not cls:
        return None
    if (callable == bool) and (parameter.kind == KEYWORD_ONLY):
        return BooleanOptionConverter
    return cls

none_and_empty = ((None, empty))
def unannotated_to_converter(parameter):
    if (dereference_annotated(parameter.annotation) in none_and_empty) and (parameter.default in none_and_empty):
        return SimpleTypeConverterStr


def type_to_converter(parameter):
    annotation = dereference_annotated(parameter.annotation)
    if not isinstance(annotation, type):
        return None
    cls = _simple_type_to_converter(parameter, annotation)
    if cls:
        return cls
    if issubclass(annotation, SingleOption):
        return annotation
    return None

def callable_to_converter(parameter):
    annotation = dereference_annotated(parameter.annotation)
    if (annotation is empty) or (not builtins.callable(annotation)):
        return None
    if parameter.kind == KEYWORD_ONLY:
        return BaseOption
    return Converter

illegal_inferred_types = {dict, set, tuple, list}

def inferred_type_to_converter(parameter):
    annotation = dereference_annotated(parameter.annotation)
    if (annotation is not empty) or (parameter.default is empty):
        return None
    inferred_type = type(parameter.default)
    # print(f"inferred_type_to_converter(parameter={parameter})")
    cls = _simple_type_to_converter(parameter, inferred_type)
    # print(f"  inferred_type={inferred_type} cls={cls}")
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
    annotation = dereference_annotated(parameter.annotation)
    if (annotation is not empty) or (parameter.default is empty):
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
        raise ConfigurationError(f"couldn't add default option {option} for {callable} parameter {parameter_name}")


def default_long_option(appeal, callable, parameter_name, annotation, default):
    if len(parameter_name) < 2:
        return
    option = parameter_name_to_long_option(parameter_name)
    if not _default_option(option,
        appeal, callable, parameter_name, annotation, default):
        raise ConfigurationError(f"couldn't add default option {option} for {callable} parameter {parameter_name}")

def default_options(appeal, callable, parameter_name, annotation, default):
    # print(f"default_options(appeal={appeal}, callable={callable}, parameter_name={parameter_name}, annotation={annotation}, default={default})")
    added_an_option = False
    options = [parameter_name_to_short_option(parameter_name)]
    if len(parameter_name) > 1:
        options.append(parameter_name_to_long_option(parameter_name))
    for option in options:
        worked = _default_option(option,
            appeal, callable, parameter_name, annotation, default)
        added_an_option = added_an_option or worked
    if not added_an_option:
        raise ConfigurationError(f"Couldn't add any default options for {callable} parameter {parameter_name}")


def unbound_callable(callable):
    """
    Unbinds a callable.
    If the callable is bound to an object (a "method"),
    returns the unbound callable.  Otherwise returns callable.
    """
    return callable.__func__ if isinstance(callable, types.MethodType) else callable



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

    def __repr__(self):
        fields = [f"{key}={value!r}" for key, value in self.__dict__.items()]
        contents = " ".join(fields)
        return f"<SpecialSection {contents}>"


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
        short_option_concatenated_oparg = True, # -sOPARG, only supported if -s takes *exactly* one *optional* oparg

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

        log_events = True,

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

        self.processor_preparer = None
        self.appeal_preparer = None

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
            # 2) A Converter instance must always have a "__signature__" attribute.
            #       converter = cls(...)
            #       b = converter.__signature__
            #
            # (cls.__signature__ may be predefined on some Converter subclasses!
            #  But you can't rely on that.)

            self.converter_factories = [
                unannotated_to_converter,
                type_to_converter,
                callable_to_converter,
                inferred_type_to_converter,
                sequence_to_converter,
                ]

            self.unnested_converters = set()
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
        # print(f"fn_database_lookup(callable={callable} -> {x}")
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

    def unnested(self):
        def unnested(fn):
            self.root.unnested_converters.add(fn)
            return fn
        return unnested

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
            raise ConfigurationError("only the root Appeal instance can have a global command")
        return self.__call__

    def default_command(self):
        def closure(callable):
            assert callable and builtins.callable(callable)
            self._default = callable
            return callable
        return closure

    class Rebinder(Preparer):
        def __init__(self, *, bind_method=True):
            self.bind_method = bind_method
            self.placeholder = f"_r_{hex(id(object()))}"

        def wrap(self, fn):
            fn2 = functools.partial(fn, self.placeholder)
            update_wrapper(fn2, fn)
            return fn2

        def __call__(self, fn):
            return self.wrap(fn)

        def bind(self, instance):
            rebinder = partial_rebind_method if self.bind_method else partial_rebind_positional
            def prepare(fn):
                try:
                    # print(f"\nattempting rebind of\n    fn={fn}\n    self.placeholder={self.placeholder}\n    instance={instance}\n    rebinder={rebinder}\n")
                    return rebinder(fn, self.placeholder, instance)
                except ValueError:
                    return fn
            return prepare

    class CommandMethodPreparer(Rebinder):
        def __init__(self, appeal, *, bind_method=True):
            super().__init__(bind_method=bind_method)
            self.appeal = appeal
            self.placeholder = f"_commandmethodpreparer_placeholder_{hex(id(object()))}"

        def command(self, name=None):
            def command(fn):
                fn2 = self.wrap(fn)
                self.appeal.command(name=name)(fn2)
                return fn
            return command

        def __call__(self, name=None):
            return self.command(name=name)

        def global_command(self):
            def global_command(fn):
                # print(f"global_command wrapped fn={fn} with partial for self.placeholder={self.placeholder}")
                fn2 = self.wrap(fn)
                self.appeal.global_command()(fn2)
                return fn
            return global_command

        def default_command(self):
            def default_command(fn):
                # print(f"default_command wrapped fn={fn} with partial for self.placeholder={self.placeholder}")
                fn2 = self.wrap(fn)
                self.appeal.default_command()(fn2)
                return fn
            return global_command

        def bind(self, instance):
            rebinder = partial_rebind_method if self.bind_method else partial_rebind_positional
            def prepare(fn):
                try:
                    # print(f"\nattempting rebind of\n    fn={fn}\n    self.placeholder={self.placeholder}\n    instance={instance}\n    rebinder={rebinder}\n")
                    return rebinder(fn, self.placeholder, instance)
                except ValueError:
                    return fn
            return prepare

    def command_method(self, bind_method=True):
        return self.CommandMethodPreparer(self, bind_method=bind_method)

    def bind_processor(self):
        if not self.processor_preparer:
            self.processor_preparer = self.Rebinder(bind_method=False)
        return self.processor_preparer

    def bind_appeal(self):
        if not self.appeal_preparer:
            self.appeal_preparer = self.Rebinder(bind_method=False)
        return self.appeal_preparer

    def app_class(self, bind_method=True):
        command_method = self.CommandMethodPreparer(self, bind_method=bind_method)

        def app_class():
            def app_class(cls):
                # print("\n<app_class d> 0 in decorator, called on", cls)
                assert isinstance(cls, type)
                signature = inspect.signature(cls)
                bind_processor = self.bind_processor()
                # print(f"<app_class d> 1 bind_processor={bind_processor}")
                # print(f"<app_class d> 2 bind_processor.placeholder={bind_processor.placeholder}")

                def fn(processor, *args, **kwargs):
                    # print(f"\n[app_class gc] in global command, cls={cls} processor={processor}\n")
                    # print(f"\n[app_class gc] args={args}\n")
                    # print(f"\n[app_class gc] kwargs={kwargs}\n")
                    o = cls(*args, **kwargs)
                    # print(f"\n[app_class gc] binding o={o}\n")
                    processor.preparer(command_method.bind(o))
                    return None
                # print(f"<app_class d> 3 inspect.signature(fn)={inspect.signature(fn)}")
                # print(f"<app_class d> 4 inspect.signature(bind_processor)={inspect.signature(bind_processor)}")
                # print(f"    fn={fn}")
                # print(f"    isinstance(fn, functools.partial)={isinstance(fn, functools.partial)}")
                fn = bind_processor(fn)

                # print(f"<app_class d> 6 inspect.signature(fn)={inspect.signature(fn)}")
                # print(f"    fn={fn}")
                # print(f"    isinstance(fn, functools.partial)={isinstance(fn, functools.partial)}")
                fn.__signature__ = signature
                # print(f"<app_class d> 7 inspect.signature(fn)={inspect.signature(fn)}")

                self.global_command()(fn)
                # print(f"<app_class d> 8 self._global={self._global}")
                return cls
            return app_class

        # print("appeal.app_class returning", app_class, command_method)
        return app_class, command_method


    def parameter(self, parameter, *, usage=None):
        p = parameter
        def parameter(callable):
            _, _, positionals = self.fn_database_lookup(callable)
            positionals[p] = usage
            return callable
        return parameter

    # old--and incorrect!--name
    argument = parameter

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
            raise ConfigurationError(f"Appeal.option: no options specified")

        normalized_options = []
        for option in options:
            if not (isinstance(option, str)
                and option.startswith("-")
                and (((len(option) == 2) and option[1].isalnum())
                    or ((len(option) >= 4) and option.startswith("--")))):
                raise ConfigurationError(f"Appeal.option: {option!r} is not a legal option")
            normalized = normalize_option(option)
            normalized_options.append((normalized, option))

        parameter = inspect.Parameter(parameter_name, KEYWORD_ONLY, annotation=annotation, default=default)

        # print(f"@option annotation={annotation} default={default}")
        cls = self.root.map_to_converter(parameter)
        if cls is None:
            raise ConfigurationError(f"Appeal.option: could not determine Converter for annotation={annotation} default={default}")
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
                        raise ConfigurationError(f"{denormalized_option} is already defined on {callable2} parameter {parameter2!r} with a different signature!")
                options[option] = entry
                mappings.append(entry)
                option_signature_entry = [annotation_signature, entry]
                self.option_signature_database[option] = option_signature_entry
            return callable
        return option


    def map_to_converter(self, parameter):
        # print(f"map_to_converter(parameter={parameter})")
        for factory in self.root.converter_factories:
            c = factory(parameter)
            # print(f"  * factory={factory} -> c={c}")
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
            self.analyze(None)

        callable = self._global
        fn_name = callable.__name__

        formatter = self.root.format_positional_parameter
        usage_str, arguments_values, options_values = charm_usage(self.root, self._global_program, formatter=formatter)

        if commands:
            if usage_str and (not usage_str[-1].isspace()):
                usage_str += ' '
            usage_str += formatter("command")

        # {"{command_name}" : "help string"}
        # summary text parsed from docstring on using that command
        commands_definitions = {}

        if commands:
            for name, child in commands.items():
                child.analyze(None)
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
        # so we do the max(depth) thing.
        #
        # step 1:
        # produce a list of annotation functions in the tree
        # underneath us, in deepest-to-shallowest order.

        # signature = callable_signature(callable)
        # positional_children = set()
        # option_children = set()

        # info = [self.callable, signature, 0, positional_children, option_children]
        ci = CharmBaseInterpreter(self._global_program, name=fn_name)

        last_op = None
        option_depth = 0
        programs = {}

        two_lists = lambda: ([], [])
        mapped_options = collections.defaultdict(two_lists)
        branches_taken = set()
        converter = None

        spacer = ''
        for ip, op in ci:
            # if want_prints:
            #     print(f"## {spacer}[{ip:>3}] op={op}")
            if op.op == opcode.create_converter:
                converter = {'parameter': op.parameter, 'parameters': {}, 'options': collections.defaultdict(list)}
                ci.converters[op.key] = ci.o = converter
                continue

            if op.op == opcode.load_converter:
                ci.converter = ci.converters[op.key]
                continue

            if op.op in (opcode.append_to_converter_args, opcode.set_in_converter_kwargs):
                ci.converter['parameters'][op.parameter] = op.usage
                continue

            # see comment in charm_usage about
            # the hard-coded branching strategies
            # used here.
            if op.op == opcode.branch_on_flag:
                # branch the first time,
                # don't branch afterwards
                if ip not in branches_taken:
                    branches_taken.add(ip)
                    ci.ip.jump(op.address)
                continue

            if op.op == opcode.branch_on_not_flag:
                # don't branch the first time,
                # branch thereafter
                if ip not in branches_taken:
                    branches_taken.add(ip)
                else:
                    ci.ip.jump(op.address)
                continue

            if op.op == opcode.map_option:
                parameter = converter['parameter']
                program = op.program

                # def __init__(self, option, program, callable, parameter, key):
                options, full_names = mapped_options[program.id]
                options.append(denormalize_option(op.option))

                full_name = f"{op.parameter.name}"
                full_names.append(full_name)

                converter = ci.converters[op.key]
                option_depth += 1
                spacer = '  ' * option_depth
                ci.call(op.program)
                continue

            if op.op == opcode.end:
                option_depth -= 1
                spacer = '  ' * option_depth
                continue

        children = {}
        values = []
        values_callable_index = {}

        positional_parameter_kinds = set((POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD, VAR_POSITIONAL))

        for converter in reversed_dict_values(ci.converters.values()):
            parameter = converter['parameter']
            callable = dereference_annotated(parameter.annotation)

            positional_children = set()
            option_children = set()
            cls = self.root.map_to_converter(parameter)
            signature = cls.get_signature(parameter)
            for p in signature.parameters.values():
                annotation = dereference_annotated(p.annotation)
                cls2 = self.root.map_to_converter(p)
                if not issubclass(cls2, SimpleTypeConverter):
                    if p.kind in positional_parameter_kinds:
                        positional_children.add(annotation)
                    elif p.kind == KEYWORD_ONLY:
                        option_children.add(annotation)
            values_callable_index[callable] = len(values)
            default_depth = 0
            values.append([callable, signature, default_depth, positional_children, option_children])
            kids = (positional_children | option_children)
            children[callable] = kids

        # since we iterated over the DFS tree in reversed order,
        # callable is already the root of the whole tree.
        # do dfs to calculate depths.

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
        # if want_prints:
        #     for current, signature, depth, positional_children, option_children in values:
        #         if current in simple_type_signatures:
        #             continue
        #         print(f"current={current}\n    depth={depth}\n    positional_children={positional_children}\n    option_children={option_children}\n    signature={signature}\n")

        # step 2:
        # process the docstrings of those annotation functions, deepest to shallowest.
        # when we process a function, also merge up from its children.

        fn_to_docs = {}

        # if want_prints:
        #     print(f"[] arguments_values={arguments_values}")
        #     print(f"[] options_values={options_values}")

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
            # print("_" * 79)
            # print(f"callable={callable} signature={signature} depth={depth} positional_children={positional_children} positional_children={positional_children}")

            fn_name = callable.__name__
            prefix = f"{fn_name}."

            arguments_topic_values = {k: v for k, v in arguments_values.items() if k.startswith(prefix)}
            # arguments_and_opargs_topic_values = {k: v for k, v in arguments_values.items() if k.startswith(prefix)}
            options_topic_values = {k: v for k, v in options_values.items() if k.startswith(prefix)}

            arguments_topic_definitions = {}
            # arguments_and_opargs_topic_definitions = {}
            options_topic_definitions = {}


            if callable in simple_type_signatures:
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

            # if callable == self.callable:
            #     doc = self.docstring or ""
            # else:
            #     doc = callable.__doc__ or ""
            doc = callable.__doc__ or ""
            if not doc and callable == self._global and override_doc:
                doc = override_doc
            doc.expandtabs()
            doc = textwrap.dedent(doc)

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

            # print(f"{option_children=}")
            # print(f"{fn_to_docs=}")
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
                        # print(f"priority 2 name={name} value={value}")
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

            # if want_prints:
            #     print("_"*79)
            #     l = locals()
            #
            #     # arguments_and_opargs_topic_names
            #     # arguments_and_opargs_topic_values
            #     # arguments_and_opargs_topic_definitions
            #
            #     for name in """
            #         callable
            #
            #         arguments_topic_names
            #         arguments_topic_values
            #         arguments_topic_definitions
            #         arguments_desired
            #
            #         options_topic_names
            #         options_topic_values
            #         options_topic_definitions
            #
            #         options_desired
            #
            #         commands_definitions
            #
            #         all_definitions
            #
            #         doc
            #         """.strip().split():
            #         print(f">>> {name}:")
            #         print(l[name])
            #         print()

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
                # print(f">>>> next state={state.__name__.rpartition('.')[2]} line={line}")
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
                    raise ConfigurationError(f"{self.callable}: docstring section {special_section.name} didn't start with a topic line (one starting with {{parameter/command}})")

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
                    # if want_prints:
                    #     print()
                    #     print(f"{special_section=}")
                    #     print()
                    #     print(f"{special_section.topic_names=}")
                    #     print()
                    topic = key.format_map(special_section.topic_names)
                except KeyError as e:
                    raise ConfigurationError(f"{name}: docstring section {special_section.name} has unknown topic {key!r}") from None
                if topic in special_section.topics_seen:
                    raise ConfigurationError(f"{name}: docstring section {special_section.name} topic {key!r} defined twice") from None
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
                # print(f">> state={state.__name__.rpartition('.')[2]} line={line}")
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
                    # print(f"   summary_lines={summary_lines}")
                    # print(f"   first_section={first_section}")

                    split_summary = text.fancy_text_split("\n".join(summary_lines), allow_code=False)

            # if want_prints:
            #     print(f"[] arguments_topic_names={arguments_topic_names}")
            #     print(f"[] arguments_topic_values={arguments_topic_values}")
            #     print(f"[] arguments_topic_definitions={arguments_topic_definitions}")
            #     # print(f"[] arguments_and_opargs_topic_names={arguments_and_opargs_topic_names}")
            #     # print(f"[] arguments_and_opargs_topic_values={arguments_and_opargs_topic_values}")
            #     # print(f"[] arguments_and_opargs_topic_definitions={arguments_and_opargs_topic_definitions}")
            #     print(f"[] arguments_desired={arguments_desired}")
            #     print(f"[] options_topic_names={options_topic_names}")
            #     print(f"[] options_topic_values={options_topic_values}")
            #     print(f"[] options_topic_definitions={options_topic_definitions}")
            #     print(f"[] options_desired={options_desired}")

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
        # print(f"doc_sections={doc_sections}")

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
        # print(f"render_doctstring returning usage_str={usage_str} summary_str={summary_str} doc_str={doc_str}")

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
        self.analyze(None)
        # print(f"FOO-USAGE self._global={self._global} self._global_program={self._global_program}")
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
            # print(f"self.commands={self.commands}")
        usage_str, summary_str, doc_str = self.render_docstring(commands=self.commands, override_doc=docstring)
        # if want_prints:
        #     print(f">> usage from {self}:")
        #     print(">> usage")
        #     print(usage_str)
        #     print(">> summary")
        #     print(summary_str)
        #     print(">> doc")
        #     print(doc_str)
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
        raise UsageError("error: " + s)
        print("error:", s)
        print()
        return self.usage(usage=True, summary=True, doc=True)

    def version(self):
        print(self.support_version)

    def help(self, *command):
        """
        Print usage documentation on a specific command.
        """
        commands = " ".join(command)
        appeal = self
        for name in command:
            appeal = appeal.commands.get(name)
            if not appeal:
                raise UsageError(f'"{name}" is not a legal command.')
        appeal.usage(usage=True, summary=True, doc=True)

    def _analyze_attribute(self, name, processor):
        if not getattr(self, name):
            return None
        program_attr = name + "_program"
        program = getattr(self, program_attr)
        if not program:
            callable = getattr(self, name)
            program = charm_compile_command(self, processor, callable)
            # if want_prints:
            #     print()
            setattr(self, program_attr, program)
        return program

    def analyze(self, processor):
        if processor:
            processor.log(f"analyze _global")
        self._analyze_attribute("_global", processor)

    def _parse_attribute(self, name, processor):
        program = self._analyze_attribute(name, processor)
        if not program:
            return None
        # if want_prints:
        #     charm_print(program)

        interpreter = CharmInterpreter(processor, program)
        converter = interpreter()
        if converter == None:
            raise UsageError("unknown error")
        processor.commands.append(converter)
        return converter

    def parse(self, processor):
        callable = getattr(self, "_global")
        processor.log(f"parse _global")

        self._parse_attribute("_global", processor)

        if not processor.iterator:
            # if there are no arguments waiting here,
            # then they didn't want to run a command.
            # if any commands are defined, and they didn't specify one,
            # if there's a default command, run it.
            # otherwise, that's an error.
            default_converter = self._parse_attribute("_default", processor)
            if (not default_converter) and self.commands:
                raise UsageError("no command specified.")
            return

        processor.log.enter(f"parsing commands")
        if self.commands:
            # okay, we have arguments waiting, and there are commands defined.
            for command_name in processor.iterator:
                sub_appeal = self.commands.get(command_name)
                if not sub_appeal:
                    # partial spelling check would go here, e.g. "sta" being short for "status"
                    self.error(f"unknown command {command_name}")
                # don't append! just parse.
                # the recursive Appeal.parse call will append.
                sub_appeal.analyze(processor)
                sub_appeal.parse(processor)
                if not (self.repeat and processor.iterator):
                    break

        if processor.iterator:
            leftovers = " ".join(shlex.quote(s) for s in processor.iterator)
            raise UsageError(f"leftover cmdline arguments! {leftovers!r}")

        processor.log.exit()

    def convert(self, processor):
        processor.log("convert start")
        for command in processor.commands:
            command.convert(processor)

    def execute(self, processor):
        processor.log("execute start")
        result = None
        for command in processor.commands:
            result = command.execute(processor)
            if result:
                break
        return result

    def processor(self):
        return Processor(self)

    def process(self, args=None, kwargs=None):
        processor = self.processor()
        result = processor(args, kwargs)
        return result

    def main(self, args=None, kwargs=None):
        if args is None:
            args = sys.argv[1:]
        processor = self.processor()
        processor.main(args, kwargs)

    def read_mapping(self, callable, mapping):
        processor = self.processor()

        cc = CharmMappingCompiler(self, processor, callable)
        program = cc.assemble()

        # why permit a Sequence here?
        # if callable is a MultiOption,
        # we start with iteration
        if isinstance(mapping, Mapping):
            processor.mapping = mapping
        elif isinstance(mapping, Iterable):
            processor.iterator = mapping
        else:
            raise TypeError("mapping must be a Mapping (or an Iterable)")
        interpreter = CharmInterpreter(processor, program)

        converter = interpreter()

        converter.convert(processor)
        return converter.execute(processor)

    def read_iterable(self, callable, iterable):
        processor = self.processor()

        compiler = CharmIteratorCompiler
        cc = compiler(self, processor, callable)
        program = cc.assemble()

        processor.log.enter("iterable parse")
        results = []
        for row in iterable:
            if not row:
                continue

            processor.iterator = row
            interpreter = CharmInterpreter(processor, program)
            converter = interpreter()

            converter.convert(processor)
            result = converter.execute(processor)
            results.append(result)
        processor.log.exit()

        return results

    def read_csv(self, callable, csv_reader, *, first_row_map=None):
        processor = self.processor()

        if first_row_map:
            compiler = CharmMappingCompiler
        else:
            compiler = CharmIteratorCompiler
        cc = compiler(self, processor, callable)
        program = cc.assemble()

        headings = next(csv_reader)
        if first_row_map:
            keys = [first_row_map.get(key, key) for key in headings]

        processor.log.enter("csv parse")
        results = []
        for row in csv_reader:
            if not row:
                continue

            if first_row_map:
                d = {key: value for key, value in zip(keys, row)}
                processor.mapping = d
            else:
                processor.iterator = row
            interpreter = CharmInterpreter(processor, program)
            converter = interpreter()

            converter.convert(processor)
            result = converter.execute(processor)
            results.append(result)
        processor.log.exit()

        return results


class Processor:
    def __init__(self, appeal):
        self.appeal = appeal
        self.preparers = []
        self.reset()

    def reset(self):
        self.events = []
        self.iterator = None
        self.mapping = None
        self.commands = []
        self.result = None
        self.log = big.Log()

    def preparer(self, preparer):
        if not callable(preparer):
            raise ValueError(f"{preparer} is not callable")
        # print(f"((( adding preparer={preparer}")
        self.preparers.append(preparer)

    def execute_preparers(self, fn):
        for preparer in self.preparers:
            try:
                fn = preparer(fn)
            except ValueError:
                pass
        return fn

    def __call__(self, sequence=None, mapping=None):
        self.reset()
        self.log("process start")

        self.sequence = sequence
        iterator = sequence
        if (iterator is not None) and (not isinstance(sequence, PushbackIterator)):
            iterator = PushbackIterator(iterator)
        self.iterator = iterator

        self.mapping = mapping

        # if want_prints:
        #     # allow us to print the remaining contents of the iterator
        #     # by examining its stack
        #     l = list(iterator)
        #     l.reverse()
        #     iterator.stack.extend(l)
        #     iterator.i = None

        appeal = self.appeal
        if appeal.support_version:
            if (len(sequence) == 1) and sequence[0] in ("-v", "--version"):
                return appeal.version()
            if appeal.commands and (not "version" in appeal.commands):
                appeal.command()(appeal.version)

        if appeal.support_help:
            if (len(sequence) == 1) and sequence[0] in ("-h", "--help"):
                return appeal.help()
            if appeal.commands and (not "help" in appeal.commands):
                appeal.command()(appeal.help)

        if appeal.appeal_preparer:
            # print(f"bind appeal.appeal_preparer to self.appeal={self.appeal}")
            self.preparer(appeal.appeal_preparer.bind(self.appeal))
        if appeal.processor_preparer:
            # print(f"bind appeal.processor_preparer to self={self}")
            self.preparer(appeal.processor_preparer.bind(self))

        appeal.analyze(self)
        appeal.parse(self)
        appeal.convert(self)
        result = self.result = appeal.execute(self)
        self.log("process complete")
        # if want_prints:
        #     self.log.print()
        return result

    def main(self, args=None, kwargs=None):
        try:
            sys.exit(self(sequence=args, mapping=kwargs))
        except UsageError as e:
            # print("Error:", str(e))
            # self.appeal.usage(usage=True)
            self.appeal.help()
            sys.exit(-1)

