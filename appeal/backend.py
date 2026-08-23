#!/usr/bin/env python3
#
# appeal/backend.py
# Part of Appeal v2.
#
# The back end: consumes the Plan.  build_converters turns Plans into the
# live Converter subclasses; the Processor engine runs a command line
# through them (parcel + convert + dispatch).  The front end
# (appeal/frontend.py) produces the Plan.

import collections
from . import (AppealConfigurationError, ConfigurationError,
               DataError, UsageError, did_you_mean)
from .converters import convert, Option, MultiOption
from .frontend import Terminal, NO_DEFAULT, dereference_annotated


def _params_host(obj):
    "Where a callable keeps its parameters: itself, or __init__."
    if hasattr(obj, '__code__'):
        return obj
    init = getattr(obj, '__init__', None)
    if init is not None and hasattr(init, '__code__'):
        return init
    return None


class ArgumentInstruction:
    """
    A positional operand.  A leading-dash token here is an OPTION.  A
    trailing Argument (keyword-only-no-default parameter) instead draws
    from its owner's end-pocket and is delivered as a keyword argument.
    """
    __slots__ = ('owner', 'name', 'converter', 'required', 'slot', 'trailing')
    def __init__(self, owner, name, converter, *, required, trailing=False):
        self.owner = owner
        self.name = name
        self.converter = converter
        self.required = required
        self.slot = name
        self.trailing = trailing


class OpargInstruction:
    "An option's value: a raw grab (a leading-dash token is a literal)."
    __slots__ = ('owner', 'name', 'converter')
    def __init__(self, owner, name, converter):
        self.owner = owner
        self.name = name
        self.converter = converter


class OptionInstruction:
    """
    Registers all of an option's `strings` against an already-built owner.
    The converter is always explicit (pulled from the callable's annotation)
    and tells the runtime how the option behaves: `bool` -> a flag (presence,
    stores `not default`, no oparg); a MultiOption class -> accumulate; a
    Converter class -> a converter-group option; anything else -> a value
    option (`--units F`: raw-grab one oparg, convert).  One binding is shared
    across the strings (so -v and --verbose feed the same MultiOption).
    """
    __slots__ = ('owner', 'name', 'converter', 'strings')
    def __init__(self, owner, name, converter, strings):
        self.owner = owner
        self.name = name
        self.converter = converter
        self.strings = strings
    def register(self, processor):
        conv = self.converter
        if conv is bool:
            binding = LiveBinding(self.owner, self.name)
        elif isinstance(conv, type) and issubclass(conv, Converter):
            binding = GroupBinding(self.owner, self.name, conv, self.strings)
        elif isinstance(conv, type) and issubclass(conv, MultiOption):
            binding = MultiBinding(self.owner, self.name, conv)
        else:
            binding = ValueBinding(self.owner, self.name, conv)
        for string in self.strings:
            processor.handlers[string] = binding


class PreOptionInstruction:
    "Registers a conjure: fire before the converter exists to summon one."
    __slots__ = ('owner', 'string', 'name', 'slot', 'converter_cls',
                 'converter')
    def __init__(self, owner, string, name, slot, converter_cls,
                 converter=None):
        self.owner = owner
        self.string = string
        self.name = name
        self.slot = slot
        self.converter_cls = converter_cls
        self.converter = converter          # set -> a value option (forward
                                            # oparg); None -> a flag (conjure)
    def register(self, processor):
        if self.converter is not None:
            processor.handlers[self.string] = ConjureValueBinding(
                self.name, self.slot, self.converter_cls, self.converter)
        else:
            processor.handlers[self.string] = ConjureBinding(
                self.name, self.slot, self.converter_cls)


class RepeatInstruction:
    "A *args template ([PreOption?, Argument]) re-laid before each element."
    __slots__ = ('items',)
    def __init__(self, items):
        self.items = items


class _Collector:
    "A stand-in processor that just records a converter's laid instructions."
    __slots__ = ('items',)
    def __init__(self):
        self.items = []
    def prepend(self, items):
        self.items.extend(items)


def _own_shape(converter_cls):
    """
    A converter's own options and required leaf operands, read by dry-running
    its register (the SAME instructions the parser runs -- no plan needed).
    Returns (name -> option strings, [required operand name, ...]).
    """
    coll = _Collector()
    converter_cls().register(coll)
    options = {}
    operands = []
    for item in coll.items:
        if isinstance(item, RepeatInstruction):     # a *args template: its lone
            continue                                # element isn't THIS converter
        if isinstance(item, OptionInstruction):
            options[item.name] = list(item.strings)
        elif isinstance(item, PreOptionInstruction):
            options.setdefault(item.name, []).append(item.string)
        elif isinstance(item, ArgumentInstruction) and item.required:
            operands.append(item.name)
    return options, operands


_capacity_cache = {}


def _group_capacity(converter_cls):
    "How many operands a group option's converter takes (dry-run its register)."
    n = _capacity_cache.get(converter_cls)
    if n is None:
        coll = _Collector()
        converter_cls().register(coll)
        n = sum(1 for it in coll.items if isinstance(it, ArgumentInstruction))
        _capacity_cache[converter_cls] = n
    return n


def _valid_counts(converter_cls):
    """
    The set of valid TOTAL operand counts for a group option's converter --
    v1's "judge the count after grabbing".  A required leaf adds exactly 1; an
    optional leaf adds 0 or 1; a required group adds its own valid counts; an
    optional group adds those or 0.  grp2(a, i: inner=(p, q)) -> {1, 3}.
    """
    coll = _Collector()
    converter_cls().register(coll)
    counts = {0}
    for item in coll.items:
        if not isinstance(item, ArgumentInstruction):
            continue
        if isinstance(item.converter, type) and issubclass(item.converter,
                                                            Converter):
            sub = _valid_counts(item.converter)
            if not item.required:
                sub = sub | {0}
        elif item.required:
            sub = {1}
        else:
            sub = {0, 1}
        counts = {c + s for c in counts for s in sub}
    return counts


def _count_list(counts):
    "Render valid counts as '1 or 3' / '1, 2, or 4'."
    nums = sorted(counts)
    if len(nums) == 1:
        return str(nums[0])
    if len(nums) == 2:
        return f"{nums[0]} or {nums[1]}"
    return ', '.join(map(str, nums[:-1])) + f", or {nums[-1]}"


def _takes_many(binding):
    "Does this option take more than one oparg (so it can't be attached)?"
    if isinstance(binding, GroupBinding):
        return _group_capacity(binding.converter_cls) > 1
    if isinstance(binding, ValueBinding) and isinstance(binding.converter, tuple):
        return len(binding.converter) - 1 > 1       # (constructor, *leaves)
    return False


def _operand_list(names):
    "Render required operand names as '<A> <B> and <C>' (usage's default form)."
    toks = [f'<{n.upper()}>' for n in names]
    if len(toks) <= 1:
        return ''.join(toks)
    return ' '.join(toks[:-1]) + ' and ' + toks[-1]


def _availability_message(owner):
    """
    A summoned converter starved for operands: the option that summoned it
    isn't available until its group's required operands are supplied.  Built
    from the starved converter itself -- it knows its own options and operands.
    """
    options, operands = _own_shape(type(owner))
    spoken = None                                   # the option string the user
    for name in owner.kwargs:                       # actually typed (set a kwarg)
        strings = options.get(name)
        if strings:
            spoken = next((s for s in strings if s.startswith('--')), strings[0])
            break
    ops = _operand_list(operands)
    if spoken and ops:
        return f"{spoken} only becomes available if you specify {ops}"
    if spoken:
        return f"{spoken} isn't available here"
    return f"expected {ops}" if ops else "expected an argument"


def _presence(instance, name):
    "A flag's presence value: `not default`, read live off the callable."
    host = _params_host(type(instance).converter)   # a class: its __init__
    default = (getattr(host, '__kwdefaults__', None) or {}).get(name, False)
    return not default


def _default(instance, name):
    "The parameter's default, read live off the callable (for init)."
    host = _params_host(type(instance).converter)
    return (getattr(host, '__kwdefaults__', None) or {}).get(name)


def _positional_default(instance, name):
    """
    A positional parameter's default, read live off the callable.  A skipped
    optional positional slot appends this so later slots (e.g. a conjured group)
    stay positionally aligned -- "signature default fills the tail" is false once
    conjuring can fill a slot to the right of a skipped one.
    """
    host = _params_host(type(instance).converter)
    code = host.__code__
    names = code.co_varnames[:code.co_argcount]
    defaults = host.__defaults__ or ()
    return defaults[names.index(name) - (code.co_argcount - len(defaults))]


class LiveBinding:
    "Invoke -> set the flag; `--flag=false` gives an explicit boolean."
    __slots__ = ('instance', 'name')
    def __init__(self, instance, name):
        self.instance = instance
        self.name = name
    def invoke(self, processor, value=None):
        if value is None:
            self.instance.kwargs[self.name] = _presence(self.instance, self.name)
            return
        if value not in ('true', 'false'):          # ruled: only these two
            raise UsageError(
                f"option {self.name!r} expected 'true' or 'false'", None)
        self.instance.kwargs[self.name] = (value == 'true')


class ValueBinding:
    """
    Invoke -> one leaf oparg (`--units F`), or a multi-oparg option whose
    converter is a `(constructor, leaf1, leaf2, ...)` tuple (`--where X Y`,
    `--coord 3 4`): raw-grab one oparg per leaf, convert each, then build the
    value -- `tuple(args)` for a tuple option, else `constructor(*args)`.
    """
    __slots__ = ('instance', 'name', 'converter')
    def __init__(self, instance, name, converter):
        self.instance = instance
        self.name = name
        self.converter = converter
    def invoke(self, processor, value=None):
        conv = self.converter
        if isinstance(conv, tuple):
            self.instance.kwargs[self.name] = self._multi(processor, conv, value)
            return
        if value is None:
            if processor.peek() is None:
                raise UsageError(f"option {self.name!r} requires a value", None)
            value = processor.advance()                 # raw: no option check
        # value options convert eagerly, per occurrence: a repeated option
        # validates EVERY value (ruled 2026-08-16, "not called validate for
        # nothing"), last wins.  (Positional leaves defer; options don't.)
        self.instance.kwargs[self.name] = processor._cv(conv, value, self.name)
    def _multi(self, processor, conv, value):
        constructor, leaves = conv[0], conv[1:]
        if not leaves:                                  # a nullary converter
            if value is not None:                       # (--north): presence IS
                raise UsageError(                       # the value; '=' is refused
                    f"option {self.name!r} doesn't take a value", None)
            return constructor()
        texts = [value] if value is not None else []    # =value/attached is first
        while len(texts) < len(leaves):
            if processor.peek() is None:
                raise UsageError(
                    f"option {self.name!r} requires {len(leaves)} values", None)
            texts.append(processor.advance())           # raw grab
        args = [processor._cv(leaf, text, self.name)
                for leaf, text in zip(leaves, texts)]
        if processor.dry:                           # structural pre-scan: opargs
            return None                             # counted, converter deferred
        if constructor is tuple:
            return tuple(args)
        try:                                        # the converter body's own
            return constructor(*args)               # ValueError/TypeError is a
        except (ValueError, TypeError) as e:        # polite usage error
            name = getattr(constructor, '__name__', 'converter')
            raise UsageError(f"not a valid {name}: {str(e) or name}",
                             None) from None


_oparg_converters_cache = {}


def _oparg_converters(factory):
    """
    A MultiOption's per-occurrence oparg converters, from its option()
    parameters -- read off __code__/__annotations__ (no `inspect`, which
    costs ~7ms to import) and cached ONCE per type, not every parse.
    """
    cached = _oparg_converters_cache.get(factory)
    if cached is None:
        option = factory.option
        code = option.__code__
        names = code.co_varnames[1:code.co_argcount]    # skip self
        annotations = option.__annotations__
        converters = tuple(annotations.get(name, str) for name in names)
        minimum = len(names) - len(option.__defaults__ or ())   # optional tail
        cached = (converters, minimum)
        _oparg_converters_cache[factory] = cached
    return cached


class MultiBinding:
    """
    Invoke -> feed a persistent MultiOption (counter/accumulator/mapping).
    The instance is created lazily on first occurrence (so an unused
    option leaves the parameter's default untouched), init()'d with that
    default, and fed once per occurrence.  render() happens at finalize.
    """
    __slots__ = ('owner', 'name', 'factory', 'converters', 'minimum')
    def __init__(self, owner, name, factory):
        self.owner = owner
        self.name = name
        self.factory = factory
        self.converters, self.minimum = _oparg_converters(factory)
    def invoke(self, processor, value=None):
        instance = self.owner.kwargs.get(self.name)     # MultiOptions live in
        if instance is None:                            # kwargs now, rendered
            instance = self.factory()                   # like any deferred value
            if not processor.dry:                       # init is user code
                instance.init(_default(self.owner, self.name))
            self.owner.kwargs[self.name] = instance
        opargs = []
        if value is not None:                           # =value / attached
            if not self.converters:                     # a 0-arity fold (counter)
                raise UsageError(
                    f"option {self.name!r} doesn't take a value", None)
            opargs = [processor._cv(self.converters[0], value, self.name)]
        else:
            # grab the required opargs; then any OPTIONAL ones greedily, so long
            # as a token exists (an Option subclass's `option(x, y='Y')` -- y is
            # taken if present, defaulted if not).  -- and end-of-line decline.
            for k, converter in enumerate(self.converters):
                tok = processor.peek()
                if tok is None or tok == '--':
                    if k < self.minimum:
                        need = self.minimum
                        raise UsageError(
                            f"option {self.name!r} requires "
                            f"{'a value' if need == 1 else f'{need} values'}",
                            None)
                    break                               # optional tail: stop
                opargs.append(processor._cv(converter, processor.advance(),
                                            self.name))
        if processor.dry:                       # opargs counted; folding deferred
            return
        try:
            instance.option(*opargs)
        except (ValueError, TypeError) as e:    # wrap the option body's error
            raise UsageError(f"{self.name}: {e}", None)


class GroupBinding:
    """
    Invoke -> a converter-group option (`--e1: extras`).  Build the child
    converter, store it as the option's value, and register ITS options so
    the shared child options (--verbose/--label) now bind to this instance.
    Siblings fall out of the flat table: --e2 re-registers them at e2.  No
    summon -- a shared option before any --e1/--e2 has no handler (error).
    """
    __slots__ = ('owner', 'name', 'converter_cls', 'strings')
    def __init__(self, owner, name, converter_cls, strings=()):
        self.owner = owner
        self.name = name
        self.converter_cls = converter_cls
        self.strings = strings
    def invoke(self, processor, value=None):
        instance = self.converter_cls()
        instance._optarg = True                     # its operands are option
        if value is not None:                       # opargs (grab greedily); an
            instance._attached = value              # attached -j5/--jobs=5 feeds
        instance._optarg_root = self.converter_cls  # the first operand directly
        instance._opt_display = next(               # name the short form for the
            (s for s in self.strings if not s.startswith('--')),
            self.strings[0] if self.strings else self.name)
        self.owner.kwargs[self.name] = instance
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
            obj._summoned = True
            processor.conjured[self.slot] = obj
        obj.kwargs[self.name] = (_presence(obj, self.name) if value is None
                                 else value == 'true')


class ConjureValueBinding:
    """
    A windowed *args group's VALUE option (`--label up`) at a window boundary:
    grab one oparg, convert it, and set it on a conjured FORWARD instance that
    the next operand's Argument will pick up.  The mid-instance ValueBinding
    (registered when a window is entered) is overwritten by this at the
    boundary, exactly as a flag's ConjureBinding overwrites its LiveBinding.
    """
    __slots__ = ('name', 'slot', 'converter_cls', 'converter')
    def __init__(self, name, slot, converter_cls, converter):
        self.name = name
        self.slot = slot
        self.converter_cls = converter_cls
        self.converter = converter
    def invoke(self, processor, value=None):
        obj = processor.conjured.get(self.slot)
        if obj is None:
            obj = self.converter_cls()
            obj._summoned = True
            processor.conjured[self.slot] = obj
        if value is None:
            if processor.peek() is None:
                raise UsageError(f"option {self.name!r} requires a value", None)
            value = processor.advance()             # raw: no option check
        obj.kwargs[self.name] = processor._cv(self.converter, value, self.name)


class Converter:
    """
    Base for a generated command or converter.  There is a 1:1 mapping
    between a user callable and its Converter subclass, so the callable is a
    class attribute `converter`, wired ONCE by fixup_converters at
    @command time (never at parse time).  It's reached via type(self).
    converter -- a bare function on a class binds as a method, so never
    self.converter.  A subclass adds register(self, processor), which pushes
    its work via processor.prepend([...]) using the self.X factories.
    register is called by the Argument that enters this converter -- never by
    a PreOption (conjuring builds the object but defers its work).
    """
    trailing = 0                        # count of this converter's own
                                        # trailing operands (emitter sets it)
    _iterable = False                   # tuple[...]/list[...] group: build from
                                        # the args iterable, don't splat them
    converter = None                    # the user's callable, wired by
                                        # fixup_converters at @command time
    binds = None                        # a method command: the env key of the
                                        # instance to pass as self (class-as-app)
    constructs = None                   # a class command: the env key to stash
                                        # the instance it builds under
    _bound_inner = False                # a BoundInnerClass: construct THROUGH
                                        # the bound parent instance, not plainly
    _window = False                     # set on an instance built as one element
                                        # of a *args window; a starved required
                                        # operand of a window is "left over", not
                                        # a plain missing argument
    _summoned = False                   # set on an instance CONJURED by naming one
                                        # of its options (not started by an
                                        # operand); if it then starves for lack of
                                        # operands, the option wasn't yet available
    _optarg = False                     # set on a group entered as an OPTION's
                                        # oparg (make -j): its operands grab the
                                        # next token unconditionally (even -5/-v),
                                        # only end-of-line or `--` declining
    _attached = None                    # an attached oparg value (-j5 / --jobs=5):
                                        # feeds the group's first operand directly
    _opt_display = None                 # the option string that summoned an oparg
                                        # group ('-g'), for its count-error message
    _optarg_root = None                 # the top oparg group's class, for computing
                                        # the valid operand counts (nested groups)

    @classmethod
    def fixup_converters(cls, converter):
        """
        Wire the callable onto the class, and wire every child converter it
        reaches (via the generated _fixup_children, guarded so a shared child
        wires once).  Called from @command; the 1:1 mapping makes the class the
        callable's home.
        """
        cls.converter = converter
        cls._fixup_children(converter)

    @classmethod
    def _fixup_children(cls, converter):
        "Wire this converter's child converters (generated override; base no-op)."

    def __init__(self):
        self.args = []                  # positional operands (eagerly converted),
                                        # plus child Converters for group slots;
                                        # groups/folds finalized in __call__
        self.kwargs = {}                # options by name -- the SOLE memory for
                                        # options (a MultiOption lives here too,
                                        # rendered like everything else)
        self.reserve = []               # this converter's end-pocket (trailing)
        self.bound = None               # a method command's instance (self)

    # work-item factories -- owner is self, bound implicitly.  A group
    # operand/option passes its child Converter subclass as its converter;
    # a leaf passes a plain callable.
    def Argument(self, name, converter, *, required, trailing=False):
        return ArgumentInstruction(self, name, converter, required=required,
                        trailing=trailing)
    def Oparg(self, name, converter):
        return OpargInstruction(self, name, converter)
    def Option(self, name, converter, *strings):
        return OptionInstruction(self, name, converter, strings)
    def PreOption(self, string, name, slot, converter_cls, converter=None):
        return PreOptionInstruction(self, string, name, slot, converter_cls,
                                    converter)
    def Repeat(self, items):
        return RepeatInstruction(items)

    def __call__(self):
        "Render (phase 2): finalize every deferred value, then call the callable."
        def render(v):
            # the deferred values are zero-arg callables: a child Converter (a
            # group) or a MultiOption (a fold, living in kwargs); leaves were
            # converted eagerly and are already final.  A group's constructor
            # body raising ValueError/TypeError is a POLITE usage error ('not a
            # valid spot'); anything else passes through.
            if isinstance(v, Converter):
                try:
                    return v()
                except (ValueError, TypeError) as e:
                    name = getattr(type(v).converter, '__name__', 'converter')
                    raise UsageError(
                        f"not a valid {name}: {e or name}", None) from None
            if isinstance(v, Option):
                return v()
            return v
        for name in list(self.kwargs):
            self.kwargs[name] = render(self.kwargs[name])
        args = [render(a) for a in self.args]
        conv = type(self).converter
        if type(self)._iterable:            # tuple[...]/list[...]: build from the iterable
            return conv(args)
        if type(self)._bound_inner:
            # a BoundInnerClass: the compiled converter is bound to a throwaway
            # probe (build only needed its grammar).  Construct through the REAL
            # parent instance's attribute, which re-binds the descriptor.
            inner = type(self).constructs.rpartition('.')[2]
            return getattr(self.bound, inner)(*args, **self.kwargs)
        if type(self).binds is not None and type(self).constructs is None:
            # a method command: self is the instance a parent constructed.  A
            # nested CLASS command also has binds (it's a subcommand) but must
            # construct plainly -- it doesn't take the outer instance as self.
            # (A BoundInnerClass, which DOES construct through the outer, is a
            # known-unported edge -- build hands the compiler a _Probe-bound
            # grammar, not the real descriptor.)
            return conv(self.bound, *args, **self.kwargs)
        return conv(*args, **self.kwargs)


class Processor:
    def __init__(self, argv, root, commands=(), dry=False):
        self.argv = list(argv)
        self.pos = 0
        self.end = len(self.argv)       # exclusive: trailing pockets shrink it
        self.queue = collections.deque()
        self.handlers = {}
        self.conjured = {}
        self.force_positional = False
        self.reserved = 0               # trailing operands lifted out of the
                                        # stream (option-aware pocket); counted
                                        # in `consumed` since they never hit pos
        self.root = root
        self.commands = commands        # command words: the saturation boundary
        self.dry = dry                  # the whole-line STRUCTURAL pre-scan:
                                        # parcel + validate arity, running NO
                                        # converter/fold/callable (so a bad value
                                        # or a converter side effect is deferred
                                        # to the live pass).  Structural errors
                                        # -- counts, unknown options, an option
                                        # missing its oparg -- still raise here.

    def _cv(self, converter, text, name):
        "convert(), or a raw passthrough during the dry structural pre-scan."
        return text if self.dry else convert(converter, text, name)

    def prepend(self, items):
        "Push work onto the FRONT, preserving order (a la rextend)."
        # flat recognition (Larry's v2 ruling): an option is recognized anywhere
        # on the line, even before its converter is entered.  Register a batch's
        # options into the handlers table eagerly, not only when they reach the
        # queue front, so `--dashed dot 2.5` knows --dashed before `dot` fills.
        for item in items:
            if isinstance(item, (OptionInstruction, PreOptionInstruction)):
                item.register(self)
            elif isinstance(item, RepeatInstruction):
                # a *args window's options live inside its Repeat; register them
                # too so `--dashed 1 2 3` knows --dashed before the window lays.
                for inner in item.items:
                    if isinstance(inner, (OptionInstruction,
                                          PreOptionInstruction)):
                        inner.register(self)
        self.queue.extendleft(reversed(items))

    def peek(self):
        return self.argv[self.pos] if self.pos < self.end else None

    def advance(self):
        tok = self.argv[self.pos]; self.pos += 1; return tok

    def _is_option(self, tok):
        if not tok.startswith('-') or tok in ('-', '--'):
            return False
        # a negative number ('-2', '-2.5') is an OPERAND, not an option --
        # unless a matching short option is actually registered (v1's rule,
        # parse_tokens)
        if tok[1].isdigit() and ('-' + tok[1]) not in self.handlers:
            return False
        return True

    def _owns_option(self, tok):
        "Does this option token name one of the converter's registered options?"
        if tok.startswith('--'):
            return tok.partition('=')[0] in self.handlers
        return len(tok) > 1 and ('-' + tok[1]) in self.handlers

    def _span_arity(self, binding):
        "How many space-separated opargs an option consumes (for the pocket scan)."
        if binding is None:
            return None
        if isinstance(binding, (LiveBinding, ConjureBinding)):
            return 0
        if isinstance(binding, MultiBinding):
            return len(binding.converters)
        if isinstance(binding, GroupBinding):
            return _group_capacity(binding.converter_cls)
        if isinstance(binding, ValueBinding) and isinstance(binding.converter,
                                                            tuple):
            return len(binding.converter) - 1
        return 1                        # ValueBinding leaf / ConjureValueBinding

    def _option_span(self, i):
        """
        How many argv tokens the option at index i occupies (itself plus its
        space-separated opargs), or None when it's an option we don't own (a
        boundary).  Used only by the trailing-reservation scan to tell operands
        apart from option machinery, so an attached/bundled approximation is
        fine -- the live loop still does the real parse.
        """
        tok = self.argv[i]
        if tok.startswith('--'):
            name, eq, _ = tok.partition('=')
            binding = self.handlers.get(name)
            if binding is None:
                return None
            if eq:
                return 1                # --opt=value: no following opargs
            return 1 + self._span_arity(binding)
        binding = self.handlers.get('-' + tok[1])
        if binding is None:
            return None
        arity = self._span_arity(binding)
        if arity == 0 or len(tok) > 2:
            return 1                    # a flag bundle (-vd) or attached (-j5)
        return 1 + arity

    def enter(self, converter):
        """
        Register the converter's options, then reserve its trailing operands:
        the LAST N OPERANDS still in the stream, skipping options and their
        opargs.  Operand-aware (unlike a blind end-pocket), so `cp a b dst
        --verbose` reserves `dst`, not `--verbose`.  Reserved operands are
        lifted out of the [pos:end) window (the front/*args fill skips them)
        and delivered to the trailing Arguments by keyword.
        """
        converter.register(self)
        if not converter.trailing:
            return
        operand_indices = []
        i = self.pos
        forced = self.force_positional
        while i < self.end:
            tok = self.argv[i]
            if not forced and tok == '--':
                forced = True; i += 1; continue
            if forced or not self._is_option(tok):
                operand_indices.append(i); i += 1; continue
            span = self._option_span(i)
            if span is None:
                break                   # an option we don't own: our boundary
            i += span
        take = operand_indices[-converter.trailing:]
        if not take:
            return
        reserved = set(take)
        converter.reserve.extend(self.argv[j] for j in take)
        middle = [self.argv[j] for j in range(self.pos, self.end)
                  if j not in reserved]
        self.argv = self.argv[:self.pos] + middle + self.argv[self.end:]
        self.end = self.pos + len(middle)
        self.reserved += len(take)

    @property
    def consumed(self):
        "Tokens this processor claimed: the front it advanced through, the"
        " untouched tail past `end`, and the trailing operands it lifted out."
        return self.pos + (len(self.argv) - self.end) + self.reserved

    def run(self):
        self.enter(self.root)
        self._loop()
        if self.dry:                    # structural pre-scan: no render, no call
            return None
        return self.root()

    def _loop(self):
        while True:
            # advance past non-Argument items, registering their options
            while self.queue and isinstance(self.queue[0], (OptionInstruction, PreOptionInstruction)):
                self.queue.popleft().register(self)

            front = self.queue[0] if self.queue else None
            if isinstance(front, RepeatInstruction):
                self.queue.popleft()
                self.prepend(front.items + [front])       # re-lay for one element
                continue

            # an option's greedy oparg (make -j): fill it directly, so a
            # dash-looking token (-5, -v) becomes its value instead of being
            # parsed as an option.  _fill_argument declines on `--`/end-of-line.
            if (isinstance(front, ArgumentInstruction) and front.owner._optarg
                    and not (isinstance(front.converter, type)
                             and issubclass(front.converter, Converter))):
                self._fill_argument(front)
                continue

            tok = self.peek()
            if tok == '--' and not self.force_positional:
                if not self.queue:
                    # saturated: a `--` is only meaningful to a consumer with
                    # operands left to force positional.  An options-only era
                    # (a help/version precommand) must leave it for the command
                    # that follows, or the `--` is lost and its operands parse
                    # as options (test_double_dash_state_never_leaks).
                    return
                self.advance(); self.force_positional = True; continue

            if (tok is not None and not self.force_positional
                    and self._is_option(tok)):
                if not self.queue and not self._owns_option(tok):
                    return          # saturated + an option we don't own: this
                                    # era/command's boundary is over -- yield it
                self._invoke_option(); continue

            if front is None:
                # saturated: this era/command owns nothing more here.  Yield
                # whatever's next (a command word, a later era's option or
                # argument, or a stray) to execute, which diagnoses it --
                # commands never start with a dash.
                return

            self._fill_argument(front)

    def _invoke_option(self):
        tok = self.advance()
        if tok.startswith('--'):                        # long: --foo or --foo=bar
            value = None
            if '=' in tok:
                tok, _, value = tok.partition('=')
            binding = self.handlers.get(tok)
            if binding is None:
                longs = [k for k in self.handlers if k.startswith('--')]
                raise UsageError(
                    f"unknown option {tok!r}{did_you_mean(tok, longs)}", None)
            if value is not None and _takes_many(binding):
                raise UsageError(
                    f"option {tok!r} takes several values; separate them with "
                    f"spaces, not '='", None)
            binding.invoke(self, value)
            return
        # short: -x, a flag bundle -vd, or an attached value -uF
        chars = tok[1:]
        i = 0
        while i < len(chars):
            opt = '-' + chars[i]
            binding = self.handlers.get(opt)
            if binding is None:
                raise UsageError(f"unknown option {opt!r}", None)
            if chars[i + 1:i + 2] == '=':               # -v=false / -n=5: explicit
                if _takes_many(binding):
                    raise UsageError(
                        f"option {opt!r} takes several values; separate them "
                        f"with spaces, not '='", None)
                binding.invoke(self, chars[i + 2:])      # value for THIS option
                return
            if self._nullary(binding):                  # no oparg: keep bundling
                binding.invoke(self)
                i += 1
            elif chars[i + 1:] and _takes_many(binding):  # -gp: an attached value,
                raise UsageError(                         # but this option needs
                    f"option {opt!r} takes several values; it must be last in "  # several -- it must
                    f"a bundle with its values as separate words", None)         # be last, words apart
            else:                                       # takes a value: rest is it
                binding.invoke(self, chars[i + 1:] or None)
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
        if arg.trailing:                            # a required trailing operand
            self.queue.popleft()                    # reserved off the end, keyword
            if not arg.owner.reserve:               # too few operands
                if arg.required:
                    raise UsageError(f"missing argument {arg.name!r}", None)
                return
            raw = arg.owner.reserve.pop(0)
            arg.owner.kwargs[arg.name] = self._cv(arg.converter, raw, arg.name)
            return
        tok = self.peek()
        # a leaf converter is any one-string-in callable (str, int, split(':'),
        # ...); a group is a Converter subclass (a nested command tree)
        if not (isinstance(arg.converter, type)
                and issubclass(arg.converter, Converter)):
            if arg.owner._optarg:
                # an option's operand (make -j): an attached value feeds it; else
                # grab the next token unconditionally -- only end-of-line or `--`
                # declines it ("optional" just means running out is legal).
                if arg.owner._attached is not None:
                    raw = arg.owner._attached
                    arg.owner._attached = None
                    self.queue.popleft()
                    arg.owner.args.append(self._cv(arg.converter, raw, arg.name))
                    return
                if tok is None or tok == '--':
                    if arg.required:
                        # a required oparg starved mid-group: the grabbed count
                        # isn't a valid one -- name the option and its counts
                        raise UsageError(
                            f"option {arg.owner._opt_display} takes "
                            f"{_count_list(_valid_counts(arg.owner._optarg_root))}",
                            None)
                    self.queue.popleft()
                    arg.owner.args.append(
                        _positional_default(arg.owner, arg.name))
                    return
                self.advance()
                self.queue.popleft()
                arg.owner.args.append(self._cv(arg.converter, tok, arg.name))
                return
            if tok is None or (not self.force_positional and self._is_option(tok)):
                if arg.required:
                    if arg.owner._summoned and not arg.owner.args:
                        # an option summoned this converter but no operand ever
                        # arrived: the option wasn't available yet (Case A/B)
                        raise UsageError(_availability_message(arg.owner), None)
                    if arg.owner._window and arg.owner.args:
                        raise UsageError(
                            f"wrong number of arguments: "
                            f"{len(arg.owner.args)} left over", None)
                    raise UsageError(f"missing argument {arg.name!r}", None)
                self.queue.popleft()
                if self.queue and isinstance(self.queue[0], RepeatInstruction):
                    self.queue.popleft()             # end a *args of leaves -- no
                else:                                # phantom element; a plain
                    arg.owner.args.append(           # optional keeps its position
                        _positional_default(arg.owner, arg.name))
                return
            self.advance()
            arg.owner.args.append(self._cv(arg.converter, tok, arg.name))
            self.queue.popleft()
            return
        # a converter slot: a conjured instance, or a fresh one from an operand
        obj = self.conjured.pop(arg.slot, None)
        if obj is not None and (tok is None or self._is_option(tok)):
            # an option summoned a group but no operand arrived to start a fresh
            # element.  queue[0] is THIS Argument; a *args window has the Repeat
            # behind it.
            if len(self.queue) > 1 and isinstance(self.queue[1], RepeatInstruction):
                # a *args window: an option past the last operand binds to the
                # NEAREST built instance, not a new window (never-rejects rule).
                for built in reversed(arg.owner.args):
                    if isinstance(built, arg.converter):
                        built.kwargs.update(obj.kwargs)
                        self.queue.popleft()        # the Argument
                        self.queue.popleft()        # the Repeat -- end the *args
                        return
            # no instance to bind to: the summoned shell falls through and becomes
            # the element.  If it needs operands and none arrive, its own fill
            # reports it (invoke-always; no conjurable flag).
        if obj is None and (tok is None or self._is_option(tok)):
            if tok is None and arg.required:
                obj = arg.converter()                # invoke-always (ruled): a
                                                     # required slot builds its
                                                     # shell; a starved required
                                                     # operand surfaces in its fill
            else:
                self.queue.popleft()                 # nothing to build here
                if arg.required:
                    raise UsageError(f"missing argument {arg.name!r}", None)
                if self.queue and isinstance(self.queue[0], RepeatInstruction):
                    self.queue.popleft()             # end the *args -- no phantom
                else:                                # element; a plain optional
                    arg.owner.args.append(           # group keeps its position
                        _positional_default(arg.owner, arg.name))
                return
        if obj is None:
            obj = arg.converter()
        if len(self.queue) > 1 and isinstance(self.queue[1], RepeatInstruction):
            obj._window = True                      # a *args window element: a
                                                    # starved required operand of
                                                    # it is a leftover shortfall
        if arg.owner._optarg:                       # a nested group under an
            obj._optarg = True                      # option's oparg is part of the
            obj._optarg_root = arg.owner._optarg_root   # same greedy grab; a starve
            obj._opt_display = arg.owner._opt_display   # names the top option/counts
        arg.owner.args.append(obj)
        self.queue.popleft()
        self.enter(obj)                             # pocket + front-splice


def _halts(result):
    "The early-exit contract: a nonzero non-bool int result halts dispatch."
    return isinstance(result, int) and not isinstance(result, bool) and result


def _unexpected(token, candidates=()):
    """
    Diagnose a token nobody claimed.  Commands never start with a dash, so a
    leading-dash leftover is a mistyped option (--verison), not a mystery
    command -- a friendlier, truthful error than "unknown command".
    `candidates` is the pool to suggest from: option strings for a dash token
    (long ones only), command words otherwise.
    """
    if token.startswith('-') and token not in ('-', '--'):
        longs = [c for c in candidates if c.startswith('--')]
        return UsageError(
            f"unknown option {token!r}{did_you_mean(token, longs)}", None)
    return UsageError(
        f"unknown command {token!r}{did_you_mean(token, candidates)}", None)


def execute(commands, argv, *, precommands=(), repeat=False):
    """
    Run a program left to right.  Any precommand ERAS run first, in order --
    each saturates its own arguments and binds its own options from the head of
    the line, then yields at the first token it doesn't own (a truthy int
    return from any era halts).  Then each command word names a command whose
    arguments follow; with `repeat`, that cycles until the line runs out.  A
    converter runs greedily to saturation, keeps binding its options (the
    window), and yields at the next command word or an option it doesn't own.
    commands maps command-word -> Converter class.
    """
    result = None
    pos = 0
    for era_cls in precommands:                 # the head eras, in order
        processor = Processor(argv[pos:], era_cls(), commands)
        result = processor.run()
        if _halts(result):
            return result
        pos += processor.consumed
    if not precommands and not argv:
        raise UsageError("no command given", None)
    while pos < len(argv):
        word = argv[pos]
        converter_cls = commands.get(word)
        if converter_cls is None:
            raise _unexpected(word, commands)
        pos += 1
        processor = Processor(argv[pos:], converter_cls(), commands)
        result = processor.run()
        pos += processor.consumed
        # validate leftover BEFORE the early-exit contract: a command that
        # returns a truthy int (an exit code -- or just an int result, like a
        # verbosity level) must not mask an unclaimed trailing token.
        if not repeat and pos < len(argv):
            # a leftover option suggests from this command's options; a leftover
            # word from the command table
            tok = argv[pos]
            pool = processor.handlers if tok.startswith('-') else commands
            raise _unexpected(tok, pool)
        if _halts(result):
            return result
    return result




# builtins we hard-code as a literal (the fingerprint guarantees the shape,
# so there's no need to read them off the callable at runtime)
_BUILTIN_CONVERTERS = (str, bool, int, float, complex)


def _converter_key(plan):
    """
    A converter's identity for deduplication.  Normally the callable (one class
    per callable, per Larry's rule).  But a builtin iterable-constructor group --
    tuple[int, str] -- has callable `tuple` for EVERY parameterization, so those
    collide onto one class unless keyed by their element shape too.  Without this,
    tuple[int, str] and tuple[str, int] would share a class and mis-convert.
    """
    if plan.callable in (tuple, list):
        return (plan.callable, _group_shape(plan))
    return plan.callable


def _group_shape(plan):
    "A hashable signature of a builtin group's element slots (converter + shape)."
    return tuple(
        ((slot.child.converter if isinstance(slot.child, Terminal)
          else _converter_key(slot.child)),
         slot.required, slot.repeat, slot.trailing)
        for slot in plan.slots)


def _converters(plans):
    """
    The distinct converters reachable from the command Plans, as an ordered
    map callable -> representative Plan (each converter's own children before
    it).  A converter is identified by its callable, so a callable reached
    from several slots/options appears once -- one converter, one entry.
    """
    found = {}

    def visit(plan):
        key = _converter_key(plan)
        if key in found:
            return
        for slot in plan.slots:                 # its own converters first
            if not isinstance(slot.child, Terminal):
                visit(slot.child)
        for option in plan.options:
            if option.kind == 'group':
                visit(option.child)
        found[key] = plan
    for plan in plans:
        visit(plan)
    return found


def _child_converters(plan):
    """
    The distinct child converters this converter references (group operands
    and group options), as callable -> a representative parameter name.  Feeds
    fixup_converters: one wire-up per child, its callable read off `annotations`.
    """
    annotations = getattr(_params_host(plan.callable), '__annotations__', {}) or {}
    children = {}
    for slot in plan.slots:
        if not isinstance(slot.child, Terminal):
            children.setdefault(_converter_key(slot.child), slot.name)
    for option in plan.options:
        # fixup wires a child off annotations[name]; a decoration-supplied group
        # option (e.g. the help precommand's -h/--help) isn't a signature
        # parameter, so it can't be wired that way -- skip it here.
        if option.kind == 'group' and option.name in annotations:
            children.setdefault(_converter_key(option.child), option.name)
    return children


# ====================================================================
#  in-memory build -- one live Converter subclass per converter
# ====================================================================
def build_converters(plans):
    """
    The compiled Converter subclasses, in memory, as a map callable -> class.
    One class per converter; group operands/options reference their child
    class.  Callables are wired by fixup_converters, exactly as @command does
    for a generated module.
    """
    converters = _converters(plans)
    classes = {}
    for key, plan in converters.items():
        classes[key] = _build_class(plan, classes)
    for plan in plans:                          # wire each command's reachable tree
        classes[_converter_key(plan)].fixup_converters(plan.callable)
    return classes


def build_converter(plan):
    "The single command's live Converter subclass (convenience over build_converters)."
    return build_converters([plan])[_converter_key(plan)]


def _build_class(plan, classes):
    "One Converter subclass; group refs and fixup resolve through `classes`."
    option_specs = []
    for o in plan.options:
        if o.kind == 'flag':
            kind, extra = 'flag', None
        elif o.kind == 'value' and len(o.converters) == 1:
            kind, extra = 'value', o.converters[0]
        elif o.kind == 'value':                 # multi-oparg: (constructor, *leaves)
            kind, extra = 'multi', o.converters
        elif o.kind in ('fold', 'fold1'):       # counter/accumulator/mapping
            kind, extra = 'fold', o.converters[0]
        elif o.kind == 'group':                 # sibling converter-group option
            kind, extra = 'group', _converter_key(o.child)
        elif o.kind == 'nullary':               # a zero-arg converter as a flag:
            # presence CALLS it (--north -> north()).  Spelled as a value option
            # with a (constructor,) tuple and no leaves -- ValueBinding._multi
            # grabs zero opargs and returns constructor().  (In-memory only: the
            # live converter needs no source spelling.)
            kind, extra = 'multi', (o.converters[0],)
        else:                                   # the rest: later
            continue
        option_specs.append((o.name, kind, extra, o.strings))

    # spellings the enclosing command owns: a sub-converter option with the
    # same spelling is SHADOWED -- the command's own option is used, and it
    # never conjures the sub (conjuring is only for UNBOUND options, ruled
    # 2026-08-22).  The sub still gets the option once it's entered by an operand.
    own_strings = {s for o in plan.options for s in o.strings}

    def register(self, processor):
        # a tuple[...]/list[...] group's converter is the builtin tuple/list,
        # which has no __annotations__ (and no options to look up anyway)
        annotations = getattr(type(self).converter, '__annotations__', {})
        items = []
        for name, kind, extra, strings in option_specs:
            if kind == 'flag':
                conv = bool
            elif kind == 'group':
                conv = classes[extra]                   # the child Converter class
            elif kind == 'multi':
                conv = extra                            # (constructor, *leaves)
            elif kind == 'fold':
                conv = extra                            # the MultiOption (build's rewrite)
            else:                                       # value(single)
                conv = dereference_annotated(annotations.get(name, extra))
            items.append(self.Option(name, conv, *strings))
        boundary = len(items)   # a conjurable slot's PreOption inserts here:
                                # after the last required-or-group slot (so it
                                # leaps over optional leaves to reach its slot)
        for slot in plan.slots:
            req = slot.default is NO_DEFAULT        # intrinsic, not promoted
            if isinstance(slot.child, Terminal):
                conv = dereference_annotated(
                    annotations.get(slot.name, slot.child.converter))
                if slot.repeat:                         # *args of a leaf
                    items.append(self.Repeat([
                        self.Argument(slot.name, conv, required=False)]))
                    boundary = len(items)
                    continue
                items.append(self.Argument(slot.name, conv,
                                           required=req,
                                           trailing=slot.trailing))
                if req:
                    boundary = len(items)
                continue
            # a converter group -- reference the child class
            childcls = classes[_converter_key(slot.child)]
            preopts = []
            for o in slot.child.options:        # flat recognition
                if o.kind == 'flag':
                    for s in o.strings:
                        if s in own_strings:    # shadowed: the command owns it
                            continue
                        preopts.append(
                            self.PreOption(s, o.name, slot.name, childcls))
                elif o.kind == 'value' and len(o.converters) == 1:
                    # a single-oparg VALUE option that conjures its group: the
                    # "mix-in" pattern (--log-level debug on a converter that
                    # consumes no operands), and a windowed group's forward-
                    # binding option at a window boundary (--label up).  Carry
                    # the oparg converter so the PreOption grabs and converts it.
                    for s in o.strings:
                        if s in own_strings:
                            continue
                        preopts.append(self.PreOption(
                            s, o.name, slot.name, childcls, o.converters[0]))
            if slot.repeat:                             # windowed *args
                items.append(self.Repeat(
                    preopts + [self.Argument(slot.name, childcls, required=False)]))
                boundary = len(items)
                continue
            for k, pre in enumerate(preopts):           # leap over optionals
                items.insert(boundary + k, pre)
            items.append(self.Argument(slot.name, childcls, required=req))
            boundary = len(items)

        processor.prepend(items)

    children = _child_converters(plan)

    def fixup_children(cls, converter):
        # a child's callable IS its converter key: a plain callable, or
        # (tuple/list, shape) for a builtin iterable group.  (Reading it off
        # converter.__annotations__ crashed on a tuple[...] group -- tuple has
        # none -- and wouldn't honor build's Annotated dereferencing anyway.)
        for child_key in children:
            child_cls = classes[child_key]
            if not child_cls.converter:
                child_conv = (child_key[0] if isinstance(child_key, tuple)
                              else child_key)
                child_cls.fixup_converters(child_conv)

    dct = {'register': register, 'trailing': _n_trailing(plan),
           'binds': plan.binds, '_iterable': plan.callable in (tuple, list),
           'constructs': plan.constructs, '_bound_inner': plan.bound_inner,
           '__module__': __name__}
    if children:                                # else the base no-op suffices
        dct['_fixup_children'] = classmethod(fixup_children)
    return type(f'Converter_{plan.name}', (Converter,), dct)


def _n_trailing(plan):
    return sum(1 for slot in plan.slots if slot.trailing)


