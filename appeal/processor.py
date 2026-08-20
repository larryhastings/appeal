#!/usr/bin/env python3
#
# appeal/processor.py
# Part of Appeal -- the data-driven back-end processor (2026-08-20).
#
# The generated code for a command is a tiny set of Converter subclasses
# (one per command/converter) that push work items via register(), plus a
# __call__ that renders.  This module is the generic engine that consumes
# argv against that work:
#
#   * a single work deque, front-spliced as converters are entered;
#   * a flat handlers table (option string -> Binding), overwritten in
#     queue order -- no liveness checks;
#   * conjuring is what a PreOption does: fire it before its converter
#     exists and it brings one into being on its defaults.  The emitter
#     lays a PreOption ONLY for a conjurable converter (no required
#     positional parameters), at the earliest point its slot is reachable.
#     A non-conjurable repeated group has no PreOption, so its option
#     binds to the last EXISTING instance and an option before any
#     instance is an error.
#
# Fill is strictly left-to-right; an optional operand is never skipped to
# reach a later optional (see the fill-left-to-right ruling).  Trailing
# operands, *args opargs, MultiOptions, and multi-command dispatch land on
# top of this core incrementally.
#
# STATUS: validated vs the reference engine via appeal/codegen3.py's
# emitter -- operands (leaf/group/*args/scoped/windowed/trailing), flags,
# value + MultiOptions, sibling groups, option syntax (=, bundling, --),
# and basic command dispatch.  TODO: global command + cycling, mapping,
# converter-group options with opargs, source-string emission.

import collections
import inspect

from .runtime import convert, UsageError, MultiOption



# ---- work items ----------------------------------------------------
# Each is produced by a Handler factory (self.Argument(...) etc.), so its
# `owner` -- whose args/kwargs it fills -- is bound implicitly.

class Argument:
    """
    A positional operand.  A leading-dash token here is an OPTION.  A
    trailing Argument (keyword-only-no-default parameter) instead draws
    from its owner's end-pocket and is delivered as a keyword argument.
    """
    __slots__ = ('owner', 'name', 'converter', 'required', 'slot', 'trailing')
    def __init__(self, owner, name, converter, required, trailing=False):
        self.owner = owner
        self.name = name
        self.converter = converter
        self.required = required
        self.slot = name
        self.trailing = trailing

class Oparg:
    "An option's value: a raw grab (a leading-dash token is a literal)."
    __slots__ = ('owner', 'name', 'converter')
    def __init__(self, owner, name, converter):
        self.owner = owner
        self.name = name
        self.converter = converter

class Option:
    """
    Registers `string` against an already-built owner.  The converter is
    always explicit (pulled from the callable's annotation) and tells the
    runtime how the option behaves: `bool` -> a flag (presence, stores
    `not default`, no oparg); a MultiOption class -> accumulate; anything
    else -> a value option (`--units F`: raw-grab one oparg, convert).
    """
    __slots__ = ('owner', 'string', 'name', 'converter')
    def __init__(self, owner, string, name, converter):
        self.owner = owner
        self.string = string
        self.name = name
        self.converter = converter
    def register(self, processor):
        conv = self.converter
        if conv is bool:
            processor.handlers[self.string] = LiveBinding(self.owner, self.name)
        elif isinstance(conv, type) and issubclass(conv, MultiOption):
            processor.handlers[self.string] = MultiBinding(
                self.owner, self.name, conv)
        elif isinstance(conv, type) and issubclass(conv, Converter):
            processor.handlers[self.string] = GroupBinding(
                self.owner, self.name, conv)
        else:
            processor.handlers[self.string] = ValueBinding(
                self.owner, self.name, conv)

class PreOption:
    "Registers a conjure: fire before the converter exists to summon one."
    __slots__ = ('owner', 'string', 'name', 'slot', 'converter_cls')
    def __init__(self, owner, string, name, slot, converter_cls):
        self.owner = owner
        self.string = string
        self.name = name
        self.slot = slot
        self.converter_cls = converter_cls
    def register(self, processor):
        processor.handlers[self.string] = ConjureBinding(
            self.name, self.slot, self.converter_cls)

class Repeat:
    "A *args template ([PreOption?, Argument]) re-laid before each element."
    __slots__ = ('items',)
    def __init__(self, items):
        self.items = items


def _presence(instance, name):
    "A flag's presence value: `not default`, read live off the callable."
    default = (instance.converter.__kwdefaults__ or {}).get(name, False)
    return not default

def _default(instance, name):
    "The parameter's default, read live off the callable (for init)."
    return (instance.converter.__kwdefaults__ or {}).get(name)


class LiveBinding:
    "Invoke -> set the flag; `--flag=false` gives an explicit boolean."
    __slots__ = ('instance', 'name')
    def __init__(self, instance, name):
        self.instance = instance
        self.name = name
    def invoke(self, processor, value=None):
        self.instance.kwargs[self.name] = (
            _presence(self.instance, self.name) if value is None
            else value == 'true')

class ValueBinding:
    "Invoke -> the oparg from `=value`/attached, else raw-grab one; convert."
    __slots__ = ('instance', 'name', 'converter')
    def __init__(self, instance, name, converter):
        self.instance = instance
        self.name = name
        self.converter = converter
    def invoke(self, processor, value=None):
        if value is None:
            if processor.peek() is None:
                raise UsageError(f"option {self.name!r} needs a value", None)
            value = processor.advance()                 # raw: no option check
        self.instance.kwargs[self.name] = convert(
            self.converter, value, self.name)

class MultiBinding:
    """
    Invoke -> feed a persistent MultiOption (counter/accumulator/mapping).
    The instance is created lazily on first occurrence (so an unused
    option leaves the parameter's default untouched), init()'d with that
    default, and fed once per occurrence.  render() happens at finalize.
    Arity and element converters are introspected from the type's option().
    """
    __slots__ = ('owner', 'name', 'factory', 'converters')
    def __init__(self, owner, name, factory):
        self.owner = owner
        self.name = name
        self.factory = factory
        params = list(inspect.signature(factory.option).parameters.values())[1:]
        self.converters = [p.annotation
                           if p.annotation is not inspect.Parameter.empty
                           else str for p in params]
    def invoke(self, processor, value=None):
        instance = self.owner.multis.get(self.name)
        if instance is None:
            instance = self.factory()
            instance.init(_default(self.owner, self.name))
            self.owner.multis[self.name] = instance
        opargs = []
        if value is not None:                           # =value / attached
            if self.converters:
                opargs = [convert(self.converters[0], value, self.name)]
        else:
            for converter in self.converters:
                if processor.peek() is None:
                    raise UsageError(f"option {self.name!r} needs a value", None)
                opargs.append(convert(converter, processor.advance(), self.name))
        instance.option(*opargs)

class GroupBinding:
    """
    Invoke -> a converter-group option (`--e1: extras`).  Build the child
    converter, store it as the option's value, and register ITS options so
    the shared child options (--verbose/--label) now bind to this instance.
    Siblings fall out of the flat table: --e2 re-registers them at e2.  No
    summon -- a shared option before any --e1/--e2 has no handler (error).
    """
    __slots__ = ('owner', 'name', 'converter_cls')
    def __init__(self, owner, name, converter_cls):
        self.owner = owner
        self.name = name
        self.converter_cls = converter_cls
    def invoke(self, processor, value=None):
        instance = self.converter_cls()
        self.owner.kwargs[self.name] = instance     # rendered at finalize
        processor.enter(instance)                   # register its options

class ConjureBinding:
    "Invoke -> conjure a fresh converter on its defaults, stashed by slot."
    __slots__ = ('name', 'slot', 'converter_cls')
    def __init__(self, name, slot, converter_cls):
        self.name = name
        self.slot = slot
        self.converter_cls = converter_cls
    def invoke(self, processor, value=None):
        obj = processor.conjured.get(self.slot)
        if obj is None:
            obj = self.converter_cls()
            processor.conjured[self.slot] = obj
        obj.kwargs[self.name] = (_presence(obj, self.name) if value is None
                                 else value == 'true')


# ---- the converter base --------------------------------------------
class Converter:
    """
    Base for a generated command or converter.  `super().__init__(fn)`
    stores the user's callable as self.converter (no staticmethod).  A
    subclass adds register(self, processor), which pushes its work via
    processor.prepend([...]) using the self.X factories.  register is
    called by the Argument that enters this converter -- never by a
    PreOption (conjuring builds the object but defers its work).
    """
    trailing = 0                        # count of this converter's own
                                        # trailing operands (emitter sets it)

    def __init__(self, converter):
        self.converter = converter
        self.args = []
        self.kwargs = {}
        self.multis = {}                # name -> live MultiOption, finalized last
        self.reserve = []               # this converter's end-pocket

    # work-item factories -- owner is self, bound implicitly
    def Argument(self, name, converter, required, trailing=False):
        return Argument(self, name, converter, required, trailing)
    def Oparg(self, name, converter):
        return Oparg(self, name, converter)
    def Option(self, string, name, converter):
        return Option(self, string, name, converter)
    def PreOption(self, string, name, slot, converter_cls):
        return PreOption(self, string, name, slot, converter_cls)

    def __call__(self):
        "Render: finalize MultiOptions, resolve child converters, call."
        for name, instance in self.multis.items():
            self.kwargs[name] = instance.render()
        for name, value in self.kwargs.items():         # group-option values
            if isinstance(value, Converter):
                self.kwargs[name] = value()
        args = [a() if isinstance(a, Converter) else a for a in self.args]
        return self.converter(*args, **self.kwargs)


# ---- the engine ----------------------------------------------------
class Processor:
    def __init__(self, argv, root):
        self.argv = list(argv)
        self.pos = 0
        self.end = len(self.argv)       # exclusive: trailing pockets shrink it
        self.queue = collections.deque()
        self.handlers = {}
        self.conjured = {}
        self.force_positional = False
        self.root = root

    def prepend(self, items):
        "Push work onto the FRONT, preserving order (a la rextend)."
        self.queue.extendleft(reversed(items))

    def peek(self):
        return self.argv[self.pos] if self.pos < self.end else None

    def advance(self):
        tok = self.argv[self.pos]; self.pos += 1; return tok

    def _is_option(self, tok):
        return tok.startswith('-') and tok not in ('-', '--')

    def enter(self, converter):
        "Pocket the converter's trailing operands from the end, then register."
        for _ in range(converter.trailing):
            self.end -= 1
            converter.reserve.insert(0, self.argv[self.end])
        converter.register(self)

    def run(self):
        self.enter(self.root)
        self._loop()
        return self.root()

    def _loop(self):
        while True:
            # advance past non-Argument items, registering their options
            while self.queue and isinstance(self.queue[0], (Option, PreOption)):
                self.queue.popleft().register(self)

            front = self.queue[0] if self.queue else None
            if isinstance(front, Repeat):
                self.queue.popleft()
                self.prepend(front.items + [front])       # re-lay for one element
                continue

            tok = self.peek()
            if tok == '--' and not self.force_positional:
                self.advance(); self.force_positional = True; continue

            if (tok is not None and not self.force_positional
                    and self._is_option(tok)):
                self._invoke_option(); continue

            if front is None:
                if tok is None:
                    return
                raise UsageError(f"too many arguments (unexpected {tok!r})", None)

            self._fill_argument(front)

    def _invoke_option(self):
        tok = self.advance()
        if tok.startswith('--'):                        # long: --foo or --foo=bar
            value = None
            if '=' in tok:
                tok, _, value = tok.partition('=')
            binding = self.handlers.get(tok)
            if binding is None:
                raise UsageError(f"unknown option {tok}", None)
            binding.invoke(self, value)
            return
        # short: -x, a flag bundle -vd, or an attached value -uF
        chars = tok[1:]
        i = 0
        while i < len(chars):
            opt = '-' + chars[i]
            binding = self.handlers.get(opt)
            if binding is None:
                raise UsageError(f"unknown option {opt}", None)
            if self._nullary(binding):                  # no oparg: keep bundling
                binding.invoke(self)
                i += 1
            else:                                       # takes a value: rest is it
                rest = chars[i + 1:]
                binding.invoke(self, rest or None)
                return

    @staticmethod
    def _nullary(binding):
        "Does this option take no oparg (so it can bundle: -vd)?"
        if isinstance(binding, (LiveBinding, ConjureBinding)):
            return True
        if isinstance(binding, MultiBinding):
            return not binding.converters
        return False

    def _fill_argument(self, arg):
        if arg.trailing:                            # drawn from the end-pocket,
            self.queue.popleft()                    # delivered as a keyword arg
            raw = arg.owner.reserve.pop(0)
            arg.owner.kwargs[arg.name] = convert(arg.converter, raw, arg.name)
            return
        tok = self.peek()
        # a leaf converter is any one-string-in callable (str, int, split(':'),
        # ...); a group is a Converter subclass (a nested command tree)
        if not (isinstance(arg.converter, type)
                and issubclass(arg.converter, Converter)):
            if tok is None or (not self.force_positional and self._is_option(tok)):
                if arg.required:
                    raise UsageError(f"missing argument {arg.name!r}", None)
                self.queue.popleft()                 # skip; signature default fills
                if self.queue and isinstance(self.queue[0], Repeat):
                    self.queue.popleft()             # end a *args of leaves
                return
            self.advance()
            arg.owner.args.append(convert(arg.converter, tok, arg.name))
            self.queue.popleft()
            return
        # a converter slot: a conjured instance, or a fresh one from an operand
        obj = self.conjured.pop(arg.slot, None)
        if obj is None and (tok is None or self._is_option(tok)):
            self.queue.popleft()                     # nothing to build here
            if arg.required:
                raise UsageError(f"missing argument {arg.name!r}", None)
            if self.queue and isinstance(self.queue[0], Repeat):
                self.queue.popleft()                 # end the *args
            return
        if obj is None:
            obj = arg.converter()
        arg.owner.args.append(obj)
        self.queue.popleft()
        self.enter(obj)                             # pocket + front-splice


def dispatch(commands, argv):
    """
    Run a multi-command program: the first token names the command, the
    rest are its arguments.  commands maps command-word -> Converter class.
    (Global command + cycling land on top of this.)
    """
    if not argv:
        raise UsageError("no command given", None)
    word = argv[0]
    converter_cls = commands.get(word)
    if converter_cls is None:
        raise UsageError(f"unknown command {word!r}", None)
    return Processor(argv[1:], converter_cls()).run()
