# See "notes/how.to.group.arguments.txt" for an explanation
# of what problem this library solves and how it works.
#
# We keep it as a standalone library instead of folding it
# in to Appeal proper just to ensure it remains easy to test.

# please leave this copyright notice in binary distributions.
license = """
appeal/argument_grouping.py
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

import collections
import inspect

POSITIONAL_ONLY = inspect.Parameter.POSITIONAL_ONLY
POSITIONAL_OR_KEYWORD = inspect.Parameter.POSITIONAL_OR_KEYWORD
VAR_POSITIONAL = inspect.Parameter.VAR_POSITIONAL

empty = inspect.Parameter.empty

def parse_str(str) -> str: pass
def parse_int(int) -> int: pass
def parse_float(float) -> float: pass
def parse_complex(complex) -> complex: pass
def parse_bool(bool) -> bool: pass

simple_type_signatures = {
    str: inspect.signature(parse_str),
    int: inspect.signature(parse_int),
    float: inspect.signature(parse_float),
    complex: inspect.signature(parse_complex),
    bool: inspect.signature(parse_bool),
    }

def default_signature(parameter):
    # a simple simulation of Appeal's full-on pluggable converters
    callable = parameter.annotation
    default = parameter.default
    if callable == empty:
        if isinstance(default, (str, type(None))):
            callable = str
        elif default == empty:
            callable = str
        else:
            callable = type(default)
    simple_signature = simple_type_signatures.get(callable)
    if simple_signature:
        return simple_signature
    return inspect.signature(callable)


def is_degenerate(p, signature=default_signature):
    parameters = signature(p).parameters
    if len(parameters) == 0:
        return True
    if len(parameters) > 1:
        return False
    for name, p in parameters.items():
        if (p.annotation == str) or (p.annotation == empty and (isinstance(p.default, str) or p.default in (None, empty))):
            return True
        return is_degenerate(p, signature)


class Parameter:
    __slots__ = [
        "name",
        "default",
        "var_positional",
        "required",
        "converter",
        "optionality",
        "leaf",
        ]

    def __init__(self, name, p, *,collapse_degenerate=False, signature=default_signature):
        self.name = name
        self.default = p.default
        self.var_positional = (p.kind == VAR_POSITIONAL)
        self.required = (p.default == empty) and (not self.var_positional)

        # if p.annotation != empty:
        #     converter = p.annotation
        #     assert callable(converter)
        # elif p.default != empty:
        #     # assert p.default is not None, "you can only use a default of None if you have an annotation"
        #     if p.default is None:
        #         converter = str
        #     else:
        #         converter = type(p.default)
        # else:
        #     converter = str
        callable = p.annotation
        default = p.default

        if (callable == str) or (callable == empty and (isinstance(default, str) or default == empty)):
            self.converter = None
            self.leaf = True
        else:
            if collapse_degenerate and is_degenerate(p, signature):
                self.converter = None
                self.leaf = True
            else:
                self.converter = Function(callable, default, name, collapse_degenerate=collapse_degenerate, signature=signature)
                self.leaf = False

        self.optionality = None

    def __repr__(self):
        required_str = "+" if self.required else "-"
        var_positional_str = " var_positional" if self.var_positional else ""
        return f"<Parameter {self.name} {self.optionality}{required_str} {self.converter}{var_positional_str}>"

    def __str__(self):
        return self.name



interesting_kinds = set((POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD, VAR_POSITIONAL))

class Function:
    def __init__(self, fn, default=empty, name=None, collapse_degenerate=False, signature=default_signature):
        self.fn = fn
        self.name = name or fn.__name__
        if self.name.startswith('<'):
            self.name = self.name[1:-1]
        self.parameters = {}

        # assert fn != str
        # if fn in {int, float, complex, bool, str}:
        #     _fake_inspect_parameter = inspect.Parameter(self.name, POSITIONAL_ONLY)
        #     self.parameters[self.name] = Parameter(self.name, _fake_inspect_parameter, signature)
        # else:

        parameter = inspect.Parameter(self.name, POSITIONAL_ONLY, annotation=fn, default=default)
        for name, p in signature(parameter).parameters.items():
            # print(f"FUNCTION {fn=} {default=} {name=} {p=}")
            if p.kind in interesting_kinds:
                parameter = Parameter(name, p, collapse_degenerate=collapse_degenerate, signature=signature)
                self.parameters[name] = parameter

    def __repr__(self):
        return f"<Function {self.name}>"
        # return f"<Function {self.name} {self.parameters}>"

    def first_pass(self, parent_optionality=0):
        for p in self.parameters.values():
            # print(f"first pass {self=} {p=}")
            p.optionality = parent_optionality + int(not p.required)
            if p.converter:
                p.converter.first_pass(parent_optionality=p.optionality)

    def second_pass(self, parent_optionality=0, lowest_required_optionality=2**29):
        for p in reversed(self.parameters.values()):
            # print(f"second pass {self=} {p=} {parent_optionality=} {lowest_required_optionality=}")
            if p.converter:
                returned_required_optionality = p.converter.second_pass(p.optionality, lowest_required_optionality)
                if returned_required_optionality == parent_optionality:
                    lowest_required_optionality = returned_required_optionality

            if p.optionality > lowest_required_optionality:
                p.optionality = lowest_required_optionality
                # print("changing required on", p)
                p.required = True
            elif p.required:
                lowest_required_optionality=min(lowest_required_optionality, p.optionality)
        return lowest_required_optionality

    def print_after_second_pass(self):
        for p in self.parameters.values():
            print(repr(p))
            if p.converter:
                p.converter.print_after_second_pass()


    def third_pass(self):
        def argument_generator(fn, breadcrumb):
            for i, (name, p) in enumerate(fn.parameters.items()):
                # p is our local Parameter object
                # fn is the actual Python callable this is a parameter of
                # i is the (0-based!) index of the parameter in the function's parameter list
                #
                # e.g. for
                #   def foo(a, b, c, d)
                # if we were returning 'c', we'd return the tuple
                #     (breadcrumb, Parameter("c", inspect.Parameter(...)), foo, 2)
                yield (breadcrumb, p, fn, i)
                # if p.converter and (not p.var_positional):
                if p.converter:
                    child_breadcrumb = f"{breadcrumb}.{name}" if breadcrumb else name
                    yield from argument_generator(p.converter, child_breadcrumb)

        groups = []
        group = []
        def finish():
            nonlocal group
            if group:
                # print(f"  pinching off {group=}")
                groups.append(group)
                # print(f"  groups is now {groups=}")
                group = []

        last_tuple = None
        found_var_positional = None
        under_var_positional = {}

        for b_p_fn_i in argument_generator(self, self.fn.__name__):
            breadcrumb, p, fn, i = b_p_fn_i
            if found_var_positional:
                current_breadcrumb = f"{breadcrumb}.{p}"
                if p.required and (not current_breadcrumb.startswith(found_var_positional)):
                    raise ValueError(f'Required parameter "{breadcrumb}.{p}" found after VAR_POSITIONAL parameter "{found_var_positional}"')
            # t = (p.optionality, p.required)
            t = p.optionality
            if (last_tuple != t) or (not p.required):
                # print(f"finishing because {p}, {group=}")
                finish()
                last_tuple = t
            group.append((p, fn, i))
            # once we hit the first var_positional parameter, we are donezo!
            if p.var_positional:
                found_var_positional = f"{breadcrumb}.{p.name}."

        finish()
        required = None
        if groups:
            assert groups[0]
            first_option = groups[0][0][0]
            if first_option.required and first_option.optionality == 0:
                required = groups.pop(0)
        if not required:
            required = []
        # print(f"returning ({required=}, {groups=})")
        return (required, groups)


    def analyze(self):
        self.first_pass()
        self.second_pass()
        if 0:
            print("after second pass:")
            self.print_after_second_pass()
        return self.third_pass()



class GroupedParameter:

    __slots__ = [
        "name",
        "fn",
        "index",
        "optionality",
        "required",
        "in_required_group",
        "first_in_group",
        "last_in_group",
        "leaf",
        "var_positional",
        ]

    def __init__(self,
            name = None,
            fn = None,
            index = None,
            optionality = 0,
            required = False,
            in_required_group = False,
            first_in_group = False,
            last_in_group = False,
            leaf = False,
            var_positional = False,
        ):
        self.name = name
        self.fn = fn
        self.index = index
        self.optionality = optionality
        self.required = required
        self.in_required_group = in_required_group
        self.first_in_group = first_in_group
        self.last_in_group = last_in_group
        self.leaf = leaf
        self.var_positional = var_positional

    def __repr__(self):
        strings = ["<GroupedParameter"]
        for attr in "name fn index optionality required in_required_group first_in_group last_in_group leaf var_positional".split():
            value = getattr(self, attr)
            if attr == "fn":
                value = value.__name__
            if value or (isinstance(value, int) and not isinstance(value, bool)):
                if value is True:
                    strings.append(attr)
                else:
                    strings.append(f"{attr}={value!r}")
        strings[-1] += ">"
        return " ".join(strings)


class ParameterGrouperIterator:
    def __init__(self, required, optional, *, only_leaves=True):
        if required:
            required = collections.deque(required)
        if optional:
            assert all(o and isinstance(o, list) for o in optional)
            optional = collections.deque(collections.deque(o) for o in optional)

        self.current = None

        self.only_leaves = only_leaves
        self.queue = optional
        if required:
            self.current_group = required
            self.required = required
        elif optional:
            self.current_group = optional.popleft()
            self.required = False
        else:
            self.current_group = ()
            self.required = False
        self.first = True
        self.var_positional = None

    def __repr__(self):
        hex_id = hex(id(self))[2:]
        return f"<ParameterGrouperIterator {hex_id} current_group={self.current_group} queue={self.queue} required={self.required}>"

    def __iter__(self):
        return self

    def __next__(self):
        # print(f"\n()() pgi.next({self=}) {self.only_leaves=}\n")
        while True:
            if self.var_positional:
                return self.var_positional
            elif self.current_group:
                in_required_group = bool(self.required)
                value = self.current_group.popleft()
            elif self.queue:
                in_required_group = False
                self.current_group = self.queue.popleft()
                assert self.current_group # every sublist of queue should start non-empty
                self.first = True
                value = self.current_group.popleft()
            else:
                raise StopIteration

            parameter, fn, index = value
            if (self.only_leaves and not (parameter.leaf or parameter.var_positional)):
                continue

            last = not self.current_group
            gp = GroupedParameter(
                    name = parameter.name,
                    fn = fn.fn,
                    index = index,
                    optionality = str(parameter.optionality) + ("+" if parameter.required else "-"),
                    required = parameter.required,
                    in_required_group = in_required_group,
                    first_in_group = self.first,
                    last_in_group = last,
                    leaf = parameter.leaf,
                    var_positional = parameter.var_positional,
                )
            self.first = False
            # if parameter.var_positional:
            #     self.var_positional = gp
            self.current = gp
            return gp

    def __bool__(self):
        return bool(self.current_group or self.queue)




class ParameterGrouper:
    """
    To use: run
        pg = ParameterGrouper(fn)
    on the Python callable that your Command or Option maps to.

    Then iterate over it:
        pgi = iter(pg)

    Every time you parse a leaf parameter, next(pgi).
    You can now use pgi to answer questions:

      Q: Have we exhausted the current parameter group?
      A: bool(pgi.current_group)
         pg.current_group is a list of the parameters remaining
         in the current group.

      Q: Does the current parameter group contain *required* parameters?
         Or, are there more *required* parameters waiting?
      A: bool(pgi.required)
         pg.required is a list of the remaining required parameters.
         Note that, if you evaluate next(pgi) and it yields the *last*
         parameter from the required group, internally the iterator has
         already moved to the next group and pgi.required is now False.

      Q: Are there more parameters waiting?
      A: bool(pgi)

    You should also examine the object yielded by the iterator:
        parameter = next(pgi)
    This tells you everything about the parameter:
        parameter.parameter        # name
        parameter.fn               # Python function it is a parameter to
        parameter.index            # 0-based index position for this parameter
        parameter.required         # was this parameter (locally) required?  (aka "does it lack a default value?")
        parameter.first_in_group   # is this parameter the first in a new parameter group?
        parameter.last_in_group    # is this parameter the last in a new parameter group?
        parameter.var_positional   # is this a *args parameter?

    As a safety precaution, you can examine the value returned
    by next(pg) to ensure that you and it are in sync.  next(pg)
    will return information on each leaf parameter of the linearized
    tree of converters under fn.

    One quirk of this design (which is easier to live with than to re-think):
    if you encounter a VAR_POSITIONAL (*args) parameter, it behaves a little strangely.
        * bool(pgi) will be False.
        * next(pgi) will always return the VAR_POSITIONAL parameter, no matter how
          many times you call it.
        * VAR_POSITIONAL parameters are never required.  However, they *may* have
          converters!
    """
    def __init__(self, fn, default=empty, signature=default_signature):
        pgf = Function(fn, default, signature=signature)
        self.required, self.optional = pgf.analyze()

    def __repr__(self):
        return f"<ParameterGrouper required={self.required} optional={self.optional}>"

    # this will yield only the leaf arguments
    def __iter__(self):
        return ParameterGrouperIterator(self.required, self.optional, only_leaves=True)

    # this will yield both the leaf arguments *and* the interior arguments
    def iter_all(self):
        return ParameterGrouperIterator(self.required, self.optional, only_leaves=False)



if __name__ == "__main__":
    import sys
    want_output = "-v" in sys.argv

    test_counter = 0
    fail_counter = 0
    def test(base, expected, *, want_value_error=False):
        if want_output:
            print()
            print()
        global test_counter
        global fail_counter
        try:
            base_command = Function(base, collapse_degenerate=True)
            required, optional = base_command.analyze()
            # print(f"we got back ({required=}, {optional=})")
            optional2 = [[str(p) for p, fn, i in l if (p.leaf or p.var_positional)] for l in optional]
            optional3 = [o for o in optional2 if o]
            # for p, fn, i in required:
            #     print(f">> {str(p)=} {p=} {fn=} {i=} {p.leaf=} {p.var_positional=}")
            stringized = f"required={[str(p) for p, fn, i in required if (p.leaf or p.var_positional)]} optional={optional3}"
            if want_output:
                print()
                print(stringized)
                print()
                pg = ParameterGrouper(base)
                for parameter in pg:
                    print(parameter)
                    if parameter.var_positional:
                        print("  (*explicit break, last parameter is var_positional*)")
                        break
                print()
                print("all parameters, including both leaf and interior nodes:")
                pg = ParameterGrouper(base)
                for p in pg.iter_all():
                    print("   ", p)
                    if p.var_positional:
                        break
            failed = stringized != expected

        except ValueError as e:
            failed = not want_value_error
            stringized = str(e)

        result = "E" if failed else "."
        test_counter += 1
        print(result, end='')
        if failed:
            fail_counter += 1
            print(f"\ntest {test_counter} failed:")
            if want_value_error:
                print("    expected a ValueError, didn't get one.")
            else:
                print("    expected:", expected)
                print("         got:", stringized)

    def int_float(a:int, b=0.0): pass
    def base(i_f:int_float, s): pass
    test(base, "required=['a', 'b', 's'] optional=[]")

    def int_float(i, f): return (i, f)
    def base(s1="", a:int_float=(0, 0.0), s2=""): pass
    test(base, "required=[] optional=[['s1'], ['i', 'f'], ['s2']]")

    def int_float(i, f, *, verbose=False): return (i, f, verbose)
    def base(s1="", a:int_float=(0, 0.0, False), s2=""): pass
    test(base, "required=[] optional=[['s1'], ['i', 'f'], ['s2']]")

    def int_int(i1: int, i2:int): return (i1, i2)
    def int_int_float(ii:int_int=(0,0), f="", *, verbose=False): return (ii, f, verbose)
    def base(s1="", a:int_int_float=(0, 0.0, False), s2=""): pass
    test(base, "required=[] optional=[['s1'], ['i1', 'i2'], ['f'], ['s2']]")


    def     conv_a1(a1w, a1x, a1y="x"): pass
    def     conv_a2(a2w, a2x, a2y="x"): pass
    def     conv_a3(a3w, a3x, a3y="x"): pass

    def   conv_a(a1: conv_a1, a2:conv_a2, a3:conv_a3="x"): pass

    def     conv_b1(b1w, b1x, b1y="x"): pass
    def     conv_b2(b2w, b2x, b2y="x"): pass
    def     conv_b3(b3w, b3x, b3y="x"): pass

    def   conv_b(b1: conv_b1, b2:conv_b2, b3:conv_b3="x"): pass

    def     conv_c1(c1w, c1x, c1y="x"): pass
    def     conv_c2(c2w, c2x, c2y="x"): pass
    def     conv_c3(c3w, c3x, c3y="x"): pass
    def     conv_c4(c4w, c4x, c4y="x", c4z="x"): pass

    def   conv_c(c1: conv_c1, c2:conv_c2, c3:conv_c3="x", c4:conv_c4="x"): pass

    def base(a:conv_a, b:conv_b, c:conv_c="x"): pass
    test(base,
        "required=['a1w', 'a1x', 'a1y', 'a2w', 'a2x', 'a2y', 'a3w', 'a3x', 'a3y', 'b1w', 'b1x', 'b1y', 'b2w', 'b2x'] optional=[['b2y'], ['b3w', 'b3x'], ['b3y'], ['c1w', 'c1x', 'c1y', 'c2w', 'c2x'], ['c2y'], ['c3w', 'c3x'], ['c3y'], ['c4w', 'c4x'], ['c4y'], ['c4z']]")


    def y_conv(e, q=0): pass

    def suspicious_configuration(x, y:y_conv, *args): pass

    def base(a, b, c:suspicious_configuration, you_wont_see_me): pass
    test(base,
        "xyz", want_value_error=True)

    def base(a, b, c:suspicious_configuration): pass
    test(base,
        "required=['a', 'b', 'x', 'e'] optional=[['q'], ['args']]")


    def int_float(i:int, f:float, *, verbose=False):
        return (i, f, "verbose" if verbose else "silent")
    def base(a="(a default)", s:int_float="(s default)", *args:int_float):
        print(f"{a=} {s=} {args=}")
    test(base,
        "required=[] optional=[['a'], ['i', 'f'], ['args', 'i', 'f']]")


    def int_float(i:int, f:float):
        return (i, f, "verbose" if verbose else "silent")
    def rip(a:int_float, b:int_float="(s default)", s:int_float="(s default)"): pass
    test(rip,
        "required=['i', 'f'] optional=[['i', 'f'], ['i', 'f']]")


    print("")
    print(f"{test_counter - fail_counter} out of {test_counter} tests succeeded.")
