#!/usr/bin/env python3
#
# appeal/codegen3_processor_prototype.py
#
# NOT production code -- a prototype of Larry's data-driven back-end
# processor, rebuilt on his actual interface (2026-08-20) to settle the
# CONJURING semantics before implementation:
#
#   * Handlers subclass Command/Converter; register(processor) pushes work
#     via processor.prepend([...]); __call__(self) renders.
#   * work items come from the self.X factories (owner bound implicitly):
#     self.Argument(name, converter, required), self.Option(string, name),
#     self.PreOption(string, name, slot, converter_cls).  Repeat([items])
#     wraps a *args template.
#   * conjuring == what a PreOption does; Appeal emits a PreOption ONLY
#     for a conjurable converter (no required positional parameters).  A
#     PreOption sits at the earliest point the slot is reachable (after
#     the last required positional before it).  A non-conjurable repeated
#     group has NO PreOption, so its option binds to the last EXISTING
#     instance, and an option before any instance is an error.
#
# Run:  python -m appeal.codegen3_processor_prototype
import sys
sys.path.insert(0, '/home/larry/tron/src/appeal')
import collections
import appeal
from appeal.runtime import convert, UsageError

LEAVES = (str, int, float, bool)


# ---- work items (created via the self.X factories; owner bound) ------
class Argument:
    def __init__(self, owner, name, converter, required):
        self.owner = owner
        self.name = name
        self.converter = converter
        self.required = required
        self.slot = name

class Option:
    def __init__(self, owner, string, name):
        self.owner = owner
        self.string = string
        self.name = name
    def register(self, processor):
        processor.handlers[self.string] = LiveBinding(self.owner, self.name)

class PreOption:
    def __init__(self, owner, string, name, slot, converter_cls):
        self.owner = owner
        self.string = string
        self.name = name
        self.slot = slot
        self.converter_cls = converter_cls
    def register(self, processor):
        processor.handlers[self.string] = ConjureBinding(
            self.owner, self.name, self.slot, self.converter_cls)

class Repeat:
    "A *args template: [PreOption?, Argument].  Re-laid before each element."
    def __init__(self, items):
        self.items = items
        self.strings = [it.string for it in items if isinstance(it, PreOption)]


class LiveBinding:
    "Fire -> set the flag on an already-built instance."
    def __init__(self, instance, name):
        self.instance = instance
        self.name = name
    def invoke(self, processor):
        self.instance.kwargs[self.name] = True

class ConjureBinding:
    "Fire -> conjure a fresh converter on its defaults, stashed by slot."
    def __init__(self, owner, name, slot, converter_cls):
        self.owner = owner
        self.name = name
        self.slot = slot
        self.converter_cls = converter_cls
    def invoke(self, processor):
        obj = processor.conjured.get(self.slot)
        if obj is None:
            obj = self.converter_cls()
            processor.conjured[self.slot] = obj
        obj.kwargs[self.name] = True


# ---- handler base --------------------------------------------------
class Handler:
    def __init__(self):
        self.args = []
        self.kwargs = {}
    # factories: owner is self
    def Argument(self, name, converter, required):
        return Argument(self, name, converter, required)
    def Option(self, string, name):
        return Option(self, string, name)
    def PreOption(self, string, name, slot, converter_cls):
        return PreOption(self, string, name, slot, converter_cls)
    def render(self):
        args = [a.render() if isinstance(a, Handler) else a for a in self.args]
        return self.__call__(args)


# ---- the generic processor -----------------------------------------
class Processor:
    def __init__(self, argv, root):
        self.argv = list(argv)
        self.pos = 0
        self.queue = collections.deque()
        self.handlers = {}
        self.conjured = {}
        self.force_positional = False
        self.root = root

    def prepend(self, items):
        self.queue.extendleft(reversed(items))

    def peek(self):
        return self.argv[self.pos] if self.pos < len(self.argv) else None

    def advance(self):
        tok = self.argv[self.pos]; self.pos += 1; return tok

    def _is_option(self, tok):
        return tok.startswith('-') and tok not in ('-', '--')

    def run(self):
        self.root.register(self)
        self._loop()
        return self.root.render()

    def _loop(self):
        while True:
            while self.queue and isinstance(self.queue[0], (Option, PreOption)):
                self.queue.popleft().register(self)

            front = self.queue[0] if self.queue else None
            if isinstance(front, Repeat):
                self._handle_repeat(front); continue

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
        binding = self.handlers.get(tok)
        if binding is None:
            raise UsageError(f"unknown option {tok}", None)
        binding.invoke(self)

    def _handle_repeat(self, rep):
        self.queue.popleft()
        self.prepend(rep.items + [rep])

    def _fill_argument(self, arg):
        tok = self.peek()
        if arg.converter in LEAVES:
            if tok is None:
                if arg.required:
                    raise UsageError(f"missing argument {arg.name!r}", None)
                self.queue.popleft()                     # skip; signature default fills
                return
            self.advance()
            arg.owner.args.append(convert(arg.converter, tok, arg.name))
            self.queue.popleft()
            return
        # a converter slot: a conjured instance, or a fresh one from an operand
        obj = self.conjured.pop(arg.slot, None)
        if obj is None and (tok is None or self._is_option(tok)):
            self.queue.popleft()                         # nothing to build here
            if arg.required:
                raise UsageError(f"missing argument {arg.name!r}", None)
            if self.queue and isinstance(self.queue[0], Repeat):
                self.queue.popleft()                     # end the *args
            return
        if obj is None:
            obj = arg.converter()
        arg.owner.args.append(obj)
        self.queue.popleft()
        obj.register(self)                              # front-splice its rules


# ====================================================================
#  1 -- SCOPED (conjurable child): scoped_cmd(a, b:child=None, c:child=None)
# ====================================================================
def child(p=0, q=1, *, flag=False):
    return ('child', p, q, flag)

def scoped_cmd(a, b: child = None, c: child = None):
    return ('scoped_cmd', a, b, c)

class Converter_child(Handler):
    def __call__(self, args):
        return child(*args, **self.kwargs)
    def register(self, processor):
        processor.prepend([
            self.Option('--flag', 'flag'),
            self.Argument('p', int, False),
            self.Argument('q', int, False),
        ])

class Command_scoped(Handler):
    def __call__(self, args):
        return scoped_cmd(*args, **self.kwargs)
    def register(self, processor):
        processor.prepend([
            self.Argument('a', str, True),
            self.PreOption('--flag', 'flag', 'b', Converter_child),
            self.Argument('b', Converter_child, False),
            self.PreOption('--flag', 'flag', 'c', Converter_child),
            self.Argument('c', Converter_child, False),
        ])


# ====================================================================
#  2 -- WINDOWED, CONJURABLE: size(width=0.0, *, bold), draw_w(shape, *sizes)
# ====================================================================
def size_opt(width=0.0, *, bold=False):
    return ('size', width, bold)

def draw_conj(shape, *sizes: size_opt):
    return ('draw', shape, sizes)

class Converter_size_opt(Handler):
    def __call__(self, args):
        return size_opt(*args, **self.kwargs)
    def register(self, processor):
        processor.prepend([
            self.Option('--bold', 'bold'),
            self.Argument('width', float, False),
        ])

class Command_draw_conj(Handler):
    def __call__(self, args):
        return draw_conj(*args, **self.kwargs)
    def register(self, processor):
        processor.prepend([
            self.Argument('shape', str, True),
            Repeat([
                self.PreOption('--bold', 'bold', 'sizes', Converter_size_opt),
                self.Argument('sizes', Converter_size_opt, False),
            ]),
        ])


# ====================================================================
#  3 -- WINDOWED, NON-CONJURABLE: size(width, *, bold) -- width REQUIRED
# ====================================================================
def size_req(width: float, *, bold=False):     # width REQUIRED (no default)
    return ('size', width, bold)

def draw_req(shape, *sizes: size_req):
    return ('draw', shape, sizes)

class Converter_size_req(Handler):
    def __call__(self, args):
        return size_req(*args, **self.kwargs)
    def register(self, processor):
        processor.prepend([
            self.Option('--bold', 'bold'),
            self.Argument('width', float, True),
        ])

class Command_draw_req(Handler):
    def __call__(self, args):
        return draw_req(*args, **self.kwargs)
    def register(self, processor):
        processor.prepend([
            self.Argument('shape', str, True),
            Repeat([                                    # NO PreOption: not conjurable
                self.Argument('sizes', Converter_size_req, False),
            ]),
        ])


# ====================================================================
PROGRAMS = {
    'scoped_cmd': (Command_scoped, scoped_cmd),
    'draw_conj': (Command_draw_conj, draw_conj),
    'draw_req': (Command_draw_req, draw_req),
}

# cases where we deliberately diverge from Appeal (Appeal's own quirks)
DIVERGE = {
    ('scoped_cmd', ('A', '1', '2', '--flag')),       # #4: conjures c, not flag->b
    ('draw_conj', ('a', '1', '2', '3', '--bold')),   # conjures a 4th size
    ('draw_conj', ('a', '--bold')),                  # conjures one size
    ('draw_req',  ('a', '1', '--bold', '2', '3')),   # --bold on the FIRST size
    ('draw_req',  ('a', '--bold', '1')),             # error: nothing to bind
}


def compare(name, argv):
    cls, fn = PROGRAMS[name]
    app = appeal.Appeal(); app.command(name)(fn)
    try:
        mine = ('ok', Processor(argv, cls()).run())
    except appeal.AppealError as e:
        mine = ('err', str(e)[:26])
    try:
        ref = ('ok', app.process([name] + argv))
    except appeal.AppealError as e:
        ref = ('err', str(e)[:26])
    agree = mine == ref or (mine[0] == 'err' and ref[0] == 'err')
    diverge = (name, tuple(argv)) in DIVERGE
    tag = '~~ ' if diverge else ('OK ' if agree else 'XX ')
    print(f"  {tag}{name} {argv}")
    print(f"        mine={mine}")
    print(f"        ref ={ref}")
    return diverge or agree


def main():
    total = passed = 0
    suite = {
        'scoped_cmd': [
            ['A'], ['A', '--flag'], ['A', '--flag', '1', '2'],
            ['A', '1', '--flag', '2'], ['A', '1', '2', '--flag'],
            ['A', '1', '2', '--flag', '3', '4'],
        ],
        'draw_conj': [
            ['a', '1', '2', '3'], ['a', '1', '--bold', '2', '3'],
            ['a', '--bold', '1', '2', '3'],
            ['a', '1', '2', '3', '--bold'], ['a', '--bold'],
        ],
        'draw_req': [
            ['a', '1', '2', '3'], ['a', '1', '--bold', '2', '3'],
            ['a', '1', '2', '3', '--bold'], ['a', '--bold', '1'],
        ],
    }
    for name, cases in suite.items():
        print(f"=== {name} ===")
        for argv in cases:
            total += 1; passed += compare(name, argv)
    print(f"\n{passed}/{total} OK  (~~ = deliberate divergence from Appeal)")


if __name__ == '__main__':
    main()
