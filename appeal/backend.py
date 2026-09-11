#!/usr/bin/env python3
#
# appeal/backend.py
# Part of Appeal 1.0.
#
# The back end: consumes the Plan.  build_converters turns Plans into the
# live Converter subclasses; the Engine runs a command line
# through them (parcel + convert + dispatch).  The front end
# (appeal/frontend.py) produces the Plan.

from . import (AppealConfigurationError, ConfigurationError,
               DataError, UsageError, did_you_mean)
from .converters import convert, Option, MultiOption, verbatim, boolean
from .frontend import Terminal, NO_DEFAULT


class ArgumentInstruction:
    """
    A positional operand.  A leading-dash token here is an OPTION.  A
    trailing Argument (a required leaf after an absorbing group) instead
    draws from its owner's end-pocket and is delivered as a keyword
    argument.
    """
    __slots__ = ('owner', 'name', 'converter', 'required', 'slot', 'trailing',
                 'default')
    def __init__(self, owner, name, converter, *, required, trailing=False,
                 default=None):
        self.owner = owner
        self.name = name
        self.converter = converter
        self.required = required
        self.slot = name
        self.trailing = trailing
        self.default = default          # the plan's default, for a skipped
                                        # optional slot (the backend reads only
                                        # the plan--never the callable)


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
    __slots__ = ('owner', 'name', 'converter', 'strings', 'rule')
    def __init__(self, owner, name, converter, strings, rule):
        self.owner = owner
        self.name = name
        self.converter = converter
        self.strings = strings
        self.rule = rule                # the plan's OptionRule: default,
                                        # presence value, fold shape
    def register(self, processor, overwrite=True):
        conv = self.converter
        owner = _Owner(self.owner)
        if conv is bool:
            binding = LiveBinding(owner, self.name, self.rule.present, self.rule)
        elif isinstance(conv, type) and issubclass(conv, Converter):
            binding = GroupBinding(self.owner, self.name, conv, self.strings,
                                   self.rule)
        elif isinstance(conv, type) and issubclass(conv, MultiOption):
            binding = MultiBinding(owner, self.name, self.rule)
        else:
            binding = ValueBinding(owner, self.name, conv, self.rule)
        for string in self.strings:
            if overwrite or string not in processor.handlers:
                processor.handlers[string] = binding


class PreOptionInstruction:
    "Registers a conjure: fire before the converter exists to summon one."
    __slots__ = ('owner', 'string', 'name', 'slot', 'converter_cls',
                 'converter', 'factory', 'chain', 'rule')
    def __init__(self, owner, string, name, slot, converter_cls, rule,
                 converter=None, factory=None, chain=None):
        self.owner = owner
        self.string = string
        self.name = name
        self.slot = slot
        self.converter_cls = converter_cls
        self.rule = rule                    # the child plan's OptionRule
        self.converter = converter          # set -> a value option (forward
                                            # oparg); None -> a flag (conjure)
        self.factory = factory              # set -> a fold option (counter/
                                            # accumulator/mapping on the group)
        self.chain = chain                  # set -> the option lives on a
                                            # NESTED positional-slot group; the
                                            # chain is [(slot, cls), ...] from
                                            # this converter down to that group
    def register(self, processor, overwrite=True):
        if not overwrite and self.string in processor.handlers:
            return                          # first-wins (announce-first)
        # the owner is conjured (a chain of them for a nested group); the
        # option itself is the ORDINARY binding of its kind (Astra R09)
        if self.chain is not None:
            target = _Chain(self.chain, processor.conjured)
        else:
            target = _Conjure(self.slot, self.converter_cls, processor.conjured)
        if self.factory is not None:
            binding = MultiBinding(target, self.name, self.rule)
        elif self.converter is not None:
            binding = ValueBinding(target, self.name, self.converter, self.rule)
        else:
            binding = LiveBinding(target, self.name, self.rule.present, self.rule)
        processor.handlers[self.string] = binding


class RepeatInstruction:
    "A *args template ([PreOption?, Argument]) re-laid before each element."
    __slots__ = ('items',)
    def __init__(self, items):
        self.items = items


def _own_shape(converter_cls):
    """
    A converter's own options and required leaf operands, off its plan.
    Returns (name -> option strings, [required operand name, ...]).
    """
    plan = converter_cls.plan
    options = {o.name: list(o.strings) for o in plan.options}
    operands = [s.name for s in plan.slots if s.required and not s.repeat]
    return options, operands


def _capacity(converter_cls):
    "How many operands a group option's converter takes: its plan's slots."
    return sum(1 for s in converter_cls.plan.slots if not s.repeat)


def _count_list(counts, unbounded_from=None):
    """
    Render valid counts as '1 or 3' / '1, 2, or 4'; an unbounded
    plan's as '2 or more' / '0, or 2 or more' (Larry's wording).
    """
    nums = sorted(counts)
    if unbounded_from is not None:
        tail = f"{unbounded_from} or more"
        if not nums:
            return tail
        return ', '.join(map(str, nums)) + f", or {tail}"
    if len(nums) == 1:
        return str(nums[0])
    if len(nums) == 2:
        return f"{nums[0]} or {nums[1]}"
    return ', '.join(map(str, nums[:-1])) + f", or {nums[-1]}"


def _takes_many(binding):
    "Does this option take more than one oparg (so it can't be attached)?"
    if isinstance(binding, GroupBinding):
        return _capacity(binding.converter_cls) > 1
    if isinstance(binding, ValueBinding) and isinstance(binding.converter, tuple):
        return len(binding.converter) - 1 > 1       # (constructor, *leaves)
    return False


def is_option_token(tok, classifiers):
    """
    The scanner's one rule for a dash token: is it an option?  A lone
    '-' and '--' aren't.  A dash+digit token is ambiguous--short options
    (-243 == -2 -4 -3) or a negative number (Larry's heuristic,
    2026-08-24): if it carves fully as a short-option bundle against
    `classifiers` it's options; else if it's a valid float it's an
    operand; else it's an option (so the real parse raises the honest
    "unknown option").  Shared by the engine and completion, so they
    can't disagree (Astra R11).
    """
    if not tok.startswith('-') or tok in ('-', '--'):
        return False
    if not tok.startswith('--') and tok[1].isdigit():
        try:
            for _ in parse_short_options(tok, classifiers):
                pass
            return True
        except UsageError:
            return not _is_float(tok)
    return True


def _is_float(text):
    "Does text parse as a Python float? (the negative-number heuristic's test.)"
    try:
        float(text)
        return True
    except ValueError:
        return False


def parse_short_options(s, classifiers):
    """
    Carve a short-option token (-v, -vd, -uFILE) into (option, arg)
    pairs, one per short option, yielded left to right.  classifiers
    is a sequence of three containers of option CHARS: the options
    taking no oparg (these bundle), exactly one, and two or more.

    arg is the option's attached oparg (a string), or None--meaning
    the oparg(s), if the option takes any, are the following words on
    the command line.  (A short option can't attach an EMPTY oparg;
    only --long= can spell that.)  An oparg option's arg is the whole
    rest of the token, ending the bundle; a multi-oparg option must be
    last in the bundle, its values all words of their own.

    classifiers is re-read for every char, and each pair is yielded
    before the next char is classified--on purpose: invoking one
    option can map the next (-me: -m enters a group that registers
    -e), so the caller may rewrite the contents of classifiers
    mid-carve.  A char in none of the three is an unknown option:
    UsageError, like the misplaced multi-oparg option.  (The up-front
    ValueErrors are caller bugs: this function only accepts a
    short-option token.)
    """
    l = list(s)
    l.reverse()
    if (l[-1] != '-') or (len(l) == 1):
        raise ValueError(f'parse_short_options: invalid short option {s!r}')
    l.pop()
    if l[-1] == '-':
        raise ValueError(f"parse_short_options: can't handle long option {s!r}")

    while l:
        option = l.pop()
        flag, oparg, opargs = classifiers
        if option in flag:
            arg = None
        elif option in oparg:
            l.reverse()
            arg = ''.join(l) or None
            l.clear()
        elif option in opargs:
            if l:
                raise UsageError(
                    f"option '-{option}' takes several values; it must be "
                    f"last in a bundle with its values as separate words")
            arg = None
        else:
            raise UsageError(f"unknown option '-{option}'")
        yield (option, arg)


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
    # the summon IS an option invoke, and every conjure binding writes
    # its own option's kwarg on the spot--so when kwargs is non-empty,
    # its first key is the option the user actually typed.  (A chain-
    # summoned middle level has no kwargs at all: generic message.)
    spoken = None
    for name in owner.kwargs:
        strings = options[name]
        spoken = next((s for s in strings if s.startswith('--')), strings[0])
        break
    ops = _operand_list(operands)
    if spoken:
        return f"{spoken} only becomes available if you specify {ops}"
    return f"expected {ops}" if ops else "expected an argument"


class _Raw:
    """
    A leaf operand or oparg as the scanner found it: the text and its
    converter.  Conversion happens at EXECUTE time, in token order (the
    engine's `pending` list)--the scan runs no user code.
    """
    __slots__ = ('converter', 'text', 'name', 'value', 'done')
    def __init__(self, converter, text, name):
        self.converter = converter
        self.text = text
        self.name = name
        self.done = False
    def resolve(self):
        if not self.done:
            self.value = convert(self.converter, self.text, self.name)
            self.done = True


class _Nullary:
    "A zero-argument converter as a flag (--north): CALLED at execute time."
    __slots__ = ('constructor', 'value', 'done')
    def __init__(self, constructor):
        self.constructor = constructor
        self.done = False
    def resolve(self):
        if not self.done:
            self.value = self.constructor()
            self.done = True


class _Multi:
    "A (constructor, *leaves) option value, built at execute time."
    __slots__ = ('constructor', 'leaves', 'name', 'value', 'done')
    def __init__(self, constructor, leaves, name):
        self.constructor = constructor
        self.leaves = leaves                    # _Raw records, already pending
        self.name = name
        self.done = False
    def resolve(self):
        if self.done:
            return
        for leaf in self.leaves:
            leaf.resolve()
        args = [leaf.value for leaf in self.leaves]
        if self.constructor is tuple:
            self.value = tuple(args)
        else:
            try:                                # the converter body's own
                self.value = self.constructor(*args)   # ValueError/TypeError
            except (ValueError, TypeError) as e:       # is a polite usage error
                name = getattr(self.constructor, '__name__', 'converter')
                raise UsageError(f"not a valid {name}: {str(e) or name}") from None
        self.done = True


class _Fold:
    """
    A fold option's value-in-waiting (counter/accumulator/mapping): the
    MultiOption is CONSTRUCTED at execute time, at its first occurrence,
    and fed one occurrence at a time in token order.  Rendered like any
    deferred value: calling it renders the MultiOption.
    """
    __slots__ = ('factory', 'default', 'name', 'instance', 'occurrences')
    def __init__(self, factory, default, name):
        self.factory = factory
        self.default = default
        self.name = name
        self.instance = None
        self.occurrences = []
    def __call__(self):
        for occurrence in self.occurrences:     # idempotent: a partial
            occurrence.resolve()                # resolve finishes here
        return self.instance()


class _FoldOccurrence:
    "One occurrence of a fold option: its opargs, applied at execute time."
    __slots__ = ('fold', 'opargs', 'done')
    def __init__(self, fold, opargs):
        self.fold = fold
        self.opargs = opargs                    # _Raw records, already pending
        self.done = False
        fold.occurrences.append(self)
    def resolve(self):
        if self.done:
            return
        fold = self.fold
        if fold.instance is None:
            fold.instance = fold.factory()      # user code: execute time only
            fold.instance.init(fold.default)
        for r in self.opargs:
            r.resolve()
        try:
            fold.instance.option(*[r.value for r in self.opargs])
        except (ValueError, TypeError) as e:    # wrap the option body's error
            raise UsageError(f"{fold.name}: {e}")
        self.done = True


def finish(v):
    """
    A parsed value, made final: records convert (or are already
    converted), folds render their MultiOption, child converters call
    their callable.  Leaves and flags pass through.
    """
    if isinstance(v, (_Raw, _Nullary, _Multi)):
        v.resolve()
        return v.value
    if isinstance(v, _Fold):
        return v()
    if isinstance(v, Converter):
        # a group's constructor body raising ValueError/TypeError is a
        # POLITE usage error ('not a valid spot'); anything else passes
        try:
            return v()
        except (ValueError, TypeError) as e:
            name = getattr(type(v).converter, '__name__', 'converter')
            raise UsageError(f"not a valid {name}: {e or name}") from None
    return v


class _Owner:
    "An option's owner: a converter instance that already exists."
    __slots__ = ('instance',)
    def __init__(self, instance):
        self.instance = instance
    def resolve(self, processor):
        return self.instance


class _Conjure:
    """
    An option's owner, conjured on demand: the converter for `slot`,
    built on its defaults the first time one of its options is named
    (before any operand starts it) and stashed by slot so the slot's
    Argument picks it up.  Marked _summoned: if it then starves for
    operands, the option wasn't available yet.
    """
    __slots__ = ('slot', 'converter_cls', 'conjured')
    def __init__(self, slot, converter_cls, conjured):
        self.slot = slot
        self.converter_cls = converter_cls
        self.conjured = conjured        # the REGISTERING era's stash: an
                                        # option shared into another era
                                        # still conjures for its own era's
                                        # fill to pick up
    def resolve(self, processor):
        obj = self.conjured.get(self.slot)
        if obj is None:
            obj = self.converter_cls()
            obj._summoned = True
            self.conjured[self.slot] = obj
        return obj


class _Chain:
    """
    An option's owner on a NESTED positional-slot group (grandparent
    -> parent -> enfant_terrible.flag): conjure/get every level from
    the top, each stashed by its slot so each level's Argument picks
    its instance up during normal filling; the deepest is the owner.
    """
    __slots__ = ('chain', 'conjured')
    def __init__(self, chain, conjured):
        self.chain = chain                  # [(slot_name, converter_cls), ...]
        self.conjured = conjured            # the registering era's stash
    def resolve(self, processor):
        owner = None
        for slot_name, cls in self.chain:
            obj = self.conjured.get(slot_name)
            if obj is None:
                obj = cls()
                obj._summoned = True
                self.conjured[slot_name] = obj
            owner = obj
        return owner


class LiveBinding:
    """
    A flag: invoke -> set the plan's presence value on the owner;
    `--flag=true`/`false` sets that literal boolean, anything else
    is refused.  ONE implementation whoever the owner is--a live
    instance, or one conjured by naming the option (Astra R09: the
    conjured path once stored `value == 'true'` unchecked).
    """
    __slots__ = ('target', 'name', 'present', 'rule')
    def __init__(self, target, name, present, rule):
        self.target = target
        self.name = name
        self.present = present          # the plan's presence value (not default)
        self.rule = rule                # the OptionRule this binding is: its
                                        # identity outlives the spelling typed
    def invoke(self, processor, value=None, spelling=None):
        owner = self.target.resolve(processor)
        if value is None:
            owner.kwargs[self.name] = self.present
            return
        try:                                        # the boolean language
            owner.kwargs[self.name] = boolean(value)    # (Larry, 2026-09-11)
        except ValueError as e:
            raise UsageError(
                f"option {(spelling or self.name)!r}: {value!r} isn't a "
                f"boolean ({e})") from None


class ValueBinding:
    """
    Invoke -> one leaf oparg (`--units F`), or a multi-oparg option whose
    converter is a `(constructor, leaf1, leaf2, ...)` tuple (`--where X Y`,
    `--coord 3 4`): raw-grab one oparg per leaf, record each, and build the
    value at execute -- `tuple(args)` for a tuple option, else
    `constructor(*args)`.  A bare `(constructor,)` is a nullary converter:
    presence CALLS it, at execute.
    """
    __slots__ = ('target', 'name', 'converter', 'rule')
    def __init__(self, target, name, converter, rule):
        self.rule = rule
        self.target = target
        self.name = name
        self.converter = converter
    def invoke(self, processor, value=None, spelling=None):
        owner = self.target.resolve(processor)
        conv = self.converter
        name = spelling or self.name
        if isinstance(conv, tuple):
            owner.kwargs[self.name] = self._multi(processor, conv, value, name)
            return
        if value is None:
            if processor.peek() is None:
                raise UsageError(f"option {name!r} requires a value")
            value = processor.advance()                 # raw: no option check
        # value options convert per occurrence: a repeated option
        # validates EVERY value (ruled 2026-08-16, "not called validate for
        # nothing"), last wins.
        owner.kwargs[self.name] = processor._cv(conv, value, self.name)
    def _multi(self, processor, conv, value, name):
        constructor, leaves = conv[0], conv[1:]
        if not leaves:                                  # a nullary converter
            if value is not None:                       # (--north): presence IS
                raise UsageError(                       # the value; '=' is refused
                    f"option {name!r} doesn't take a value")
            return processor._record(_Nullary(constructor))
        texts = [value] if value is not None else []    # =value/attached is first
        while len(texts) < len(leaves):
            if processor.peek() is None:
                raise UsageError(
                    f"option {name!r} requires {len(leaves)} values")
            texts.append(processor.advance())           # raw grab
        leaves = [processor._cv(leaf, text, self.name)
                  for leaf, text in zip(leaves, texts)]
        return processor._record(_Multi(constructor, leaves, self.name))


def _fold_shape(rule):
    """
    A fold option's shape, off its OptionRule: (the MultiOption class,
    its per-occurrence oparg converters, how many are required, the
    parameter's default).  converters is (cls, *leaves) on the rule.
    """
    leaves = rule.converters[1:]
    minimum = (rule.fold_minimum if rule.fold_minimum is not None
               else len(leaves))
    return rule.converters[0], leaves, minimum, rule.default


class MultiBinding:
    """
    Invoke -> feed a persistent fold (counter/accumulator/mapping): a
    _Fold holder in the owner's kwargs, one occurrence record per
    invocation; the MultiOption is constructed and fed at execute.
    """
    __slots__ = ('target', 'name', 'factory', 'converters', 'minimum',
                 'default', 'rule')
    def __init__(self, target, name, rule):
        self.target = target
        self.name = name
        self.rule = rule
        self.factory, self.converters, self.minimum, self.default = \
            _fold_shape(rule)
    def invoke(self, processor, value=None, spelling=None):
        owner = self.target.resolve(processor)
        name = spelling or self.name
        fold = owner.kwargs.get(self.name)              # the fold lives in
        if fold is None:                                # kwargs, rendered like
            fold = _Fold(self.factory, self.default, self.name)   # any value
            owner.kwargs[self.name] = fold
        opargs = []
        if value is not None:                           # =value / attached
            if not self.converters:                     # a 0-arity fold (counter)
                raise UsageError(
                    f"option {name!r} doesn't take a value")
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
                            f"option {name!r} requires "
                            f"{'a value' if need == 1 else f'{need} values'}")
                    break                               # optional tail: stop
                opargs.append(processor._cv(converter, processor.advance(),
                                            self.name))
        processor._record(_FoldOccurrence(fold, opargs))


class GroupBinding:
    """
    Invoke -> a converter-group option (`--e1: extras`).  Build the child
    converter, store it as the option's value, and register ITS options so
    the shared child options (--verbose/--label) now bind to this instance.
    Siblings fall out of the flat table: --e2 re-registers them at e2.  No
    summon -- a shared option before any --e1/--e2 has no handler (error).
    """
    __slots__ = ('owner', 'name', 'converter_cls', 'strings', 'rule')
    def __init__(self, owner, name, converter_cls, strings=(), rule=None):
        self.rule = rule
        self.owner = owner
        self.name = name
        self.converter_cls = converter_cls
        self.strings = strings
    def invoke(self, processor, value=None, spelling=None):
        instance = self.converter_cls()
        instance._optarg = True                     # its operands are option
        if value is not None:                       # opargs (grab greedily); an
            instance._attached = value              # attached -j5/--jobs=5 feeds
        instance._optarg_root = self.converter_cls  # the first operand directly
        instance._opt_display = spelling or next(   # name what the user typed;
            (s for s in self.strings if not s.startswith('--')),  # else fall back
            self.strings[0] if self.strings else self.name)       # to a short form
        self.owner.kwargs[self.name] = instance
        processor.enter(instance)                   # register its options


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
                                        # trailing operands (build sets it)
    converter = None                    # the user's callable, wired by
                                        # fixup_converters at @command time
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
    def Argument(self, name, converter, *, required, trailing=False,
                 default=None):
        return ArgumentInstruction(self, name, converter, required=required,
                                   trailing=trailing, default=default)
    def Option(self, name, converter, rule, *strings):
        return OptionInstruction(self, name, converter, strings, rule)
    def PreOption(self, string, name, slot, converter_cls, rule,
                  converter=None, factory=None, chain=None):
        return PreOptionInstruction(self, string, name, slot, converter_cls,
                                    rule, converter, factory, chain)
    def Repeat(self, items):
        return RepeatInstruction(items)

    def __call__(self):
        """
        Render (execute): finish every deferred value, then invoke the
        callable--the plan's calling convention (a method takes its
        instance, an iterable group builds its container, a bound inner
        class constructs through its parent).
        """
        for name in list(self.kwargs):
            self.kwargs[name] = finish(self.kwargs[name])
        args = [finish(a) for a in self.args]
        return type(self).plan(args, self.kwargs, self.bound)


class Engine:
    """
    One era's scanner and executor.  parse() parcels the era's tokens
    onto its converters--structural errors (counts, unknown options, a
    starved oparg) raise here, and NO user code runs: every leaf is a
    _Raw record, every fold an occurrence list, every nullary a deferred
    call.  execute() then converts the records in token order and calls
    the root.  (Larry's three passes, 2026-09-07: parcel, scan, execute
    --the first two fused per era, since a structural error ends the
    parcel either way.)
    """
    def __init__(self, argv, root, commands=(), dashdash=False, conjured=None):
        self.argv = list(argv)
        self.pos = 0
        self.end = len(self.argv)       # exclusive: trailing pockets shrink it
        self.stack = []                 # instructions, top at the END:
                                        # push = extend(reversed(items)),
                                        # pop = pop(); only the top (and
                                        # the one beneath) is ever examined
        self.handlers = {}
        self.conjured = {} if conjured is None else conjured    # slot -> the
                                        # instance an option summoned early;
                                        # shared with the handlers harvested
                                        # for an era before this one
        self.force_positional = dashdash    # `--` seen: no later token in
                                        # THIS era is an option.  Era-scoped
                                        # (Larry, 2026-09-08, click-style);
                                        # it reaches the next era only by
                                        # bleed, like the era's options
        self.verbatim_run = False       # a verbatim *args has taken its first
                                        # token: from here on nothing in this
                                        # era is an option, `--` included
        self.reserved = 0               # trailing operands lifted out of the
                                        # stream (option-aware pocket); counted
                                        # in `consumed` since they never hit pos
        self.root = root
        self.commands = commands        # command words: the saturation boundary
        self.pending = []               # the records to resolve, in token order
        self.entered = []               # every converter entered, in order:
                                        # each owes its required options
        self.invoked = []               # every option invocation: (the string
                                        # as typed, the binding it selected)--
                                        # the binding's rule says whether it's
                                        # deprecated (Astra D05: a spelling
                                        # isn't an identity; scope and sharing
                                        # change its owner)

    def seed(self, bled):
        """
        Options BLED from the previous era: still recognized here, still
        bound to their own era's converter.  This era's own options win a
        collision (registered first; a bled string only fills a gap).
        """
        for string, binding in bled.items():
            self.handlers.setdefault(string, binding)

    def _record(self, record):
        self.pending.append(record)
        return record

    def _cv(self, converter, text, name):
        "A leaf's record: converted at execute time, in token order."
        return self._record(_Raw(converter, text, name))

    def resolve(self):
        "Convert every record, in token order (a bad value raises here)."
        for record in self.pending:
            record.resolve()

    def prepend(self, items):
        "Push work onto the top of the stack, in order (items[0] on top)."
        # flat recognition (Larry's v2 ruling): an option is recognized anywhere
        # on the line, even before its converter is entered.  Register a batch's
        # options into the handlers table eagerly, not only when they reach the
        # top of the stack, so `--dashed dot 2.5` knows --dashed before `dot` fills.
        # FIRST-wins here (overwrite=False): when sibling windows share an
        # option string, a LEADING occurrence (before any is built) binds to
        # the FIRST window -- announce-first, the interval model.  As the loop
        # advances, each window's pushed option re-registers (overwrite=True),
        # so mid/trailing occurrences track the current region.
        for item in items:
            if isinstance(item, (OptionInstruction, PreOptionInstruction)):
                item.register(self, overwrite=False)
            elif isinstance(item, RepeatInstruction):
                # a *args window's options live inside its Repeat; register them
                # too so `--dashed 1 2 3` knows --dashed before the window lays.
                for inner in item.items:
                    if isinstance(inner, (OptionInstruction,
                                          PreOptionInstruction)):
                        inner.register(self, overwrite=False)
        self.stack.extend(reversed(items))

    def peek(self):
        return self.argv[self.pos] if self.pos < self.end else None

    def advance(self):
        tok = self.argv[self.pos]; self.pos += 1; return tok

    def _is_option(self, tok):
        return is_option_token(tok, self._short_classifiers())

    def _short_classifiers(self):
        """
        The classifiers for parse_short_options, from the handler
        table as it stands RIGHT NOW: the short-option chars taking no
        opargs, exactly one, and two or more.
        """
        flag = set()
        oparg = set()
        opargs = set()
        for string, binding in self.handlers.items():
            if len(string) != 2:
                continue
            if self._nullary(binding):
                flag.add(string[1])
            elif _takes_many(binding):
                opargs.add(string[1])
            else:
                oparg.add(string[1])
        return [flag, oparg, opargs]

    def _owns_option(self, tok):
        "Does this option token name one of the converter's registered options?"
        if tok.startswith('--'):
            return tok.partition('=')[0] in self.handlers
        return len(tok) > 1 and ('-' + tok[1]) in self.handlers

    def _span_arity(self, binding):
        "How many space-separated opargs an option consumes (for the pocket scan)."
        assert binding is not None      # both callers null-check first
        if isinstance(binding, LiveBinding):
            return 0
        if isinstance(binding, MultiBinding):
            return len(binding.converters)
        if isinstance(binding, GroupBinding):
            return _capacity(binding.converter_cls)
        if isinstance(binding, ValueBinding) and isinstance(binding.converter,
                                                            tuple):
            return len(binding.converter) - 1
        return 1                        # a ValueBinding leaf

    def _option_span(self, i):
        """
        How many argv tokens the option at index i occupies (itself plus its
        space-separated opargs), or None when it's an option we don't own (a
        boundary).  The trailing-reservation scan uses it to tell operands
        apart from option machinery, so it must read a token EXACTLY as the
        parse will: a short bundle is carved by parse_short_options, and only
        its LAST option can take the following words (Astra R05: `-vo out`
        once read as a flag bundle, and `out` got reserved as the destination).
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
        try:
            carved = list(parse_short_options(tok, self._short_classifiers()))
        except UsageError:
            return None                 # a letter we don't own: our boundary
        option, arg = carved[-1]        # only the last option can take words
        arity = self._span_arity(self.handlers['-' + option])
        if arity == 0 or arg is not None:
            return 1                    # a flag bundle, or the value attached
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
        self.entered.append(converter)
        if not converter.trailing:
            return
        operand_indices = []
        i = self.pos
        forced = self.force_positional
        singles, run = type(converter).plan.verbatim_sites()
        while i < self.end:
            tok = self.argv[i]
            count = len(operand_indices)
            # a verbatim position takes a dash token; `--` is still the
            # marker there, until a verbatim *args has taken its first
            if not forced and tok == '--' and not (run is not None and count > run):
                forced = True; i += 1; continue
            if (forced or count in singles or (run is not None and count >= run)
                    or not self._is_option(tok)):
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

    def parse(self):
        "Parcel the era's tokens onto the root (structural errors raise)."
        self.enter(self.root)
        self._loop()
        self.check_required()

    def check_required(self, supplied=frozenset()):
        """
        Every converter entered in this era owes its required options
        (Larry, 2026-09-10): a keyword-only parameter with no default.
        Structural--pass 1, before anything runs--like a missing
        argument.  `supplied`: the ids of the OptionRules a bound
        config supplies an occurrence of--resolved by the vet, so a
        config key mapped under another name, or aimed at a nested
        owner, counts (Astra D03).
        """
        for converter in self.entered:
            missing = {}
            for rule in type(converter).plan.options:
                if (rule.required and rule.name not in converter.kwargs
                        and id(rule) not in supplied):
                    missing.setdefault(rule.name, []).append(rule.key)
            for keys in missing.values():
                # several strings feeding one parameter: name them all
                raise UsageError(
                    f"missing option {' or '.join(map(repr, keys))}")

    def execute(self):
        "Convert in token order, then call the root."
        self.resolve()
        return self.root()

    def _loop(self):
        while True:
            # advance past non-Argument items, registering their options
            while self.stack and isinstance(self.stack[-1], (OptionInstruction, PreOptionInstruction)):
                self.stack.pop().register(self)

            front = self.stack[-1] if self.stack else None
            if isinstance(front, RepeatInstruction):
                self.stack.pop()
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
            if tok == '--' and not self.force_positional and not self.verbatim_run:
                # from here on nothing in this era is an option (the
                # dispatcher relays the state onward iff the era bleeds)
                self.advance(); self.force_positional = True; continue

            if isinstance(front, ArgumentInstruction) and front.converter is verbatim:
                # a verbatim slot: the next token, whatever it looks like
                self._fill_argument(front)
                continue

            if (tok is not None and not self.force_positional
                    and self._is_option(tok)):
                if not self.stack and not self._owns_option(tok):
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
                    f"unknown option {tok!r}{did_you_mean(tok, longs)}")
            self.invoked.append((tok, binding))
            if value is not None and _takes_many(binding):
                raise UsageError(
                    f"option {tok!r} takes several values; separate them with "
                    f"spaces, not '='")
            binding.invoke(self, value, tok)
            return
        # short: -x, a flag bundle -vd, or an attached value -uF.
        # invoking a flag can map new options (-me: -m enters a group
        # that registers -e), so after each one we rewrite the
        # classifiers' contents; the carve reads them fresh per char
        classifiers = self._short_classifiers()
        for option, arg in parse_short_options(tok, classifiers):
            opt = '-' + option
            binding = self.handlers[opt]
            self.invoked.append((opt, binding))
            if self._nullary(binding):                  # no oparg: a flag
                binding.invoke(self, spelling=opt)
                classifiers[:] = self._short_classifiers()
            else:                                       # arg attached, or None:
                binding.invoke(self, arg, opt)          # oparg(s) are next words

    @staticmethod
    def _nullary(binding):
        "Does this option take no oparg (so it can bundle: -vd)?"
        if isinstance(binding, LiveBinding):
            return True
        if isinstance(binding, MultiBinding):
            return not binding.converters
        if isinstance(binding, GroupBinding):
            # a group option whose converter consumes NO operands (all its
            # params are options -- the Logging/five_a mixin) is nullary: it
            # conjures + enters, then the bundle continues (-me == -m then -e,
            # -e now registered by entering the group)
            return _capacity(binding.converter_cls) == 0
        return False

    def _fill_argument(self, arg):
        if arg.trailing:                            # a required trailing operand
            assert arg.required                     # trailing == keyword-only,
                                                    # no default == required
            self.stack.pop()                    # reserved off the end, keyword
            if not arg.owner.reserve:               # too few operands
                raise UsageError(f"missing argument {arg.name!r}")
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
                    self.stack.pop()
                    arg.owner.args.append(self._cv(arg.converter, raw, arg.name))
                    return
                if tok is None or (tok == '--' and arg.converter is not verbatim):
                    if arg.required:
                        # a required oparg starved mid-group: the grabbed count
                        # isn't a valid one -- name the option and its counts
                        root = arg.owner._optarg_root.plan
                        raise UsageError(
                            f"option {arg.owner._opt_display} takes "
                            f"{_count_list(root.valid_counts, root.unbounded_from)}")
                    self.stack.pop()
                    arg.owner.args.append(
                        arg.default)
                    return
                self.advance()
                self.stack.pop()
                arg.owner.args.append(self._cv(arg.converter, tok, arg.name))
                return
            if tok is None or (not self.force_positional
                               and arg.converter is not verbatim
                               and self._is_option(tok)):
                if arg.required:
                    if arg.owner._summoned and not arg.owner.args:
                        # an option summoned this converter but no operand ever
                        # arrived: the option wasn't available yet (Case A/B)
                        raise UsageError(_availability_message(arg.owner))
                    if arg.owner._window and arg.owner.args:
                        raise UsageError(
                            f"wrong number of arguments: "
                            f"{len(arg.owner.args)} left over")
                    raise UsageError(f"missing argument {arg.name!r}")
                self.stack.pop()
                if self.stack and isinstance(self.stack[-1], RepeatInstruction):
                    self.stack.pop()             # end a *args of leaves -- no
                else:                                # phantom element; a plain
                    arg.owner.args.append(           # optional keeps its position
                        arg.default)
                return
            self.advance()
            arg.owner.args.append(self._cv(arg.converter, tok, arg.name))
            self.stack.pop()
            if (arg.converter is verbatim and self.stack
                    and isinstance(self.stack[-1], RepeatInstruction)):
                self.verbatim_run = True        # a verbatim *args has begun
            return
        # a converter slot: a conjured instance, or a fresh one from an operand
        obj = self.conjured.pop(arg.slot, None)
        # (the loop invokes options before any fill sees them, so tok here
        # is an operand or None--after `--` a dash token is an operand too)
        if obj is not None and tok is None:
            # an option summoned a group but no operand arrived to start a fresh
            # element.  the top is THIS Argument; a *args window has the Repeat
            # behind it.
            if len(self.stack) > 1 and isinstance(self.stack[-2], RepeatInstruction):
                # a *args window: an option past the last operand binds to the
                # NEAREST built instance, not a new window (never-rejects rule).
                for built in reversed(arg.owner.args):
                    if isinstance(built, arg.converter):
                        built.kwargs.update(obj.kwargs)
                        self.stack.pop()        # the Argument
                        self.stack.pop()        # the Repeat -- end the *args
                        return
            # no instance to bind to: the summoned shell falls through and becomes
            # the element.  If it needs operands and none arrive, its own fill
            # reports it (invoke-always; no conjurable flag).
        if obj is None and tok is None:
            if arg.required:
                obj = arg.converter()                # invoke-always (ruled): a
                                                     # required slot builds its
                                                     # shell; a starved required
                                                     # operand surfaces in its fill
            else:
                self.stack.pop()                 # nothing to build here
                if self.stack and isinstance(self.stack[-1], RepeatInstruction):
                    self.stack.pop()             # end the *args -- no phantom
                else:                                # element; a plain optional
                    arg.owner.args.append(           # group keeps its position
                        arg.default)
                return
        if obj is None:
            obj = arg.converter()
        if len(self.stack) > 1 and isinstance(self.stack[-2], RepeatInstruction):
            obj._window = True                      # a *args window element: a
                                                    # starved required operand of
                                                    # it is a leftover shortfall
        if arg.owner._optarg:                       # a nested group under an
            obj._optarg = True                      # option's oparg is part of the
            obj._optarg_root = arg.owner._optarg_root   # same greedy grab; a starve
            obj._opt_display = arg.owner._opt_display   # names the top option/counts
        arg.owner.args.append(obj)
        self.stack.pop()
        self.enter(obj)                             # pocket + front-splice


def handlers_of(converter, conjured):
    """
    A converter's option handlers, registered ahead of its era--for an
    earlier era that shares BACKWARDS (Larry, 2026-09-08: `prog --b-opt
    aval` recognizes B's option while A's operand is still owed).  The
    bindings target the converter itself, and conjure into `conjured`,
    the stash the converter's own era will read from.
    """
    scratch = Engine([], converter, conjured=conjured)
    converter.register(scratch)         # registers the options (prepend
    return scratch.handlers             # does that eagerly); the stack is
                                        # discarded with the scratch engine


def _halts(result):
    "The early-exit contract: a nonzero non-bool int result halts dispatch."
    return isinstance(result, int) and not isinstance(result, bool) and result


def _unexpected(token, candidates=(), dashdash=False, owners=None):
    """
    Diagnose a token nobody claimed.  Commands never start with a dash, so a
    leading-dash leftover is a mistyped option (--verison), not a mystery
    command -- a friendlier, truthful error than "unknown command".
    `candidates` is the pool to suggest from: option strings for a dash token
    (long ones only), command words otherwise.  After `--` (dashdash) nothing
    is an option, so a dash token is an unknown command like any other.

    Two hints, never both (Larry's ruling, 2026-09-08): a MISSPELLED option
    suggests from the scope that was tried ("did you mean '--verbose'?");
    failing that, a MISPLACED one--the exact string is an option somewhere
    else in the program--says where it goes ("it goes after 'build'").
    `owners` maps every option string in the program to that placement.
    Misspelled AND misplaced is trying too hard: no hint.
    """
    if not dashdash and token.startswith('-') and token not in ('-', '--'):
        longs = [c for c in candidates if c.startswith('--')]
        hint = did_you_mean(token, longs)
        if not hint and owners and token in owners:
            return UsageError(f"option {token!r} can't be used here; "
                              f"it goes {owners[token]}")
        return UsageError(f"unknown option {token!r}{hint}")
    return UsageError(
        f"unknown command {token!r}{did_you_mean(token, candidates)}")


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
    # pass 1+2: parcel and scan the whole line (structural errors raise)
    steps = []
    pos = 0
    for era_cls in precommands:                 # the head eras, in order
        processor = Engine(argv[pos:], era_cls(), commands)
        processor.parse()
        steps.append(processor)
        pos += processor.consumed
    if not precommands and not argv:
        raise UsageError("no command given")
    while pos < len(argv):
        word = argv[pos]
        converter_cls = commands.get(word)
        if converter_cls is None:
            raise _unexpected(word, commands)
        pos += 1
        processor = Engine(argv[pos:], converter_cls(), commands)
        processor.parse()
        pos += processor.consumed
        if not repeat and pos < len(argv):
            # a leftover option suggests from this command's options; a leftover
            # word from the command table
            tok = argv[pos]
            pool = processor.handlers if tok.startswith('-') else commands
            raise _unexpected(tok, pool)
        steps.append(processor)
    # pass 3: execute left to right; a truthy int result halts the rest
    result = None
    for processor in steps:
        result = processor.execute()
        if _halts(result):
            return result
    return result




# builtins we hard-code as a literal (the fingerprint guarantees the shape,
# so there's no need to read them off the callable at runtime)
_BUILTIN_CONVERTERS = (str, bool, int, float, complex)


def _converters(plans):
    """
    The distinct converters reachable from the command Plans, as an ordered
    map callable -> representative Plan (each converter's own children before
    it).  A converter is identified by its callable, so a callable reached
    from several slots/options appears once -- one converter, one entry.
    """
    found = {}

    def visit(plan):
        key = plan.key
        if key in found:
            return
        for slot in plan.slots:                 # its own converters first
            if not isinstance(slot.child, Terminal):
                visit(slot.child)
        for option in plan.options:
            if option.child is not None:
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
    children = {}
    for slot in plan.slots:
        if not isinstance(slot.child, Terminal):
            children.setdefault(slot.child.key, slot.name)
    for option in plan.options:
        if option.child is not None:
            children.setdefault(option.child.key, option.name)
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
        classes[plan.key].fixup_converters(plan.callable)
    return classes


def build_converter(plan):
    "The single command's live Converter subclass (convenience over build_converters)."
    return build_converters([plan])[plan.key]


def converter_for(plan):
    """
    The plan's compiled Converter subclass, built ONCE: a pure function
    of the plan (the backend reads nothing else), so it lives on the
    plan and is rebuilt only when the plan is (any decoration change
    invalidates the plan).  Ruled 2026-09-07 (Astra R10).
    """
    if plan.compiled is None:
        plan.compiled = build_converter(plan)
    return plan.compiled


def _build_class(plan, classes):
    "One Converter subclass; group refs and fixup resolve through `classes`."
    # what each option's strings bind to: the rule says (its
    # engine_converter), except a GroupOption, which binds to its
    # child's Converter class--resolved through `classes`
    option_specs = [
        (o.name,
         classes[o.child.key] if o.child is not None
         else o.engine_converter,
         o.strings, o)
        for o in plan.options]

    # spellings the enclosing command owns: a sub-converter option with the
    # same spelling is SHADOWED -- the command's own option is used, and it
    # never conjures the sub (conjuring is only for UNBOUND options, ruled
    # 2026-08-22).  The sub still gets the option once it's entered by an operand.
    own_strings = {s for o in plan.options for s in o.strings}

    def register(self, processor):
        items = []
        for name, conv, strings, rule in option_specs:
            items.append(self.Option(name, conv, rule, *strings))
        boundary = len(items)   # a conjurable slot's PreOption inserts here:
                                # after the last required-or-group slot (so it
                                # leaps over optional leaves to reach its slot)
        for slot in plan.slots:
            req = slot.default is NO_DEFAULT        # intrinsic, not promoted
            if isinstance(slot.child, Terminal):
                conv = slot.child.converter             # the plan's, resolved
                if slot.repeat:                         # *args of a leaf
                    items.append(self.Repeat([
                        self.Argument(slot.name, conv, required=False)]))
                    boundary = len(items)
                    continue
                items.append(self.Argument(slot.name, conv,
                                           required=req,
                                           trailing=slot.trailing,
                                           default=slot.default))
                if req:
                    boundary = len(items)
                continue
            # a converter group -- reference the child class
            childcls = classes[slot.child.key]
            preopts = []
            for o in slot.child.options:        # flat recognition: the rule
                conjure = o.conjure             # says whether naming it may
                if conjure is None:             # conjure the group, and how
                    continue
                for s in o.strings:
                    if s in own_strings:        # shadowed: the command owns it
                        continue
                    preopts.append(self.PreOption(
                        s, o.name, slot.name, childcls, o, **conjure))
            if slot.repeat:                             # windowed *args
                items.append(self.Repeat(
                    preopts + [self.Argument(slot.name, childcls, required=False)]))
                boundary = len(items)
                continue
            # recurse into the child's OWN positional-slot groups, so a
            # grandchild's option (--flag buried under positional slots) is
            # advertised up front -- with a chain that conjures the whole path
            # when it fires (discretionary).
            def _nest(child_plan, chain):
                for sub in child_plan.slots:
                    if isinstance(sub.child, Terminal) or sub.repeat:
                        continue
                    subcls = classes[sub.child.key]
                    subchain = chain + [(sub.name, subcls)]
                    for o in sub.child.options:
                        conjure = o.conjure
                        if conjure is None:
                            continue
                        for s in o.strings:
                            if s in own_strings:
                                continue
                            preopts.append(self.PreOption(
                                s, o.name, sub.name, subcls, o,
                                chain=subchain, **conjure))
                    _nest(sub.child, subchain)
            _nest(slot.child, [(slot.name, childcls)])
            for k, pre in enumerate(preopts):           # leap over optionals
                items.insert(boundary + k, pre)
            items.append(self.Argument(slot.name, childcls, required=req,
                                       default=slot.default))
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
           '__module__': __name__}
    if children:                                # else the base no-op suffices
        dct['_fixup_children'] = classmethod(fixup_children)
    dct['plan'] = plan          # the class knows its plan: counts, shape
    return type(f'Converter_{plan.name}', (Converter,), dct)


def _n_trailing(plan):
    return sum(1 for slot in plan.slots if slot.trailing)


