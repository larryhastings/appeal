#!/usr/bin/env python3
#
# appeal -- Appeal v2, under construction.
# Copyright 2021-2026 by Larry Hastings
#
# This tree is the v2 rewrite; the shipping v1 lives in site-packages
# (and other checkouts).  v1's source is on this branch's history;
# argument_grouping.py stays on disk to serve as the grouping reference implementation.
#
# The spec of record is appeal.v2.grammar.md; the design rationale
# is appeal.v2.proposal.md.  North star: every command must be
# emittable as a *standalone*, dependency-free Python script
# (see codegen.emit_standalone).

"""
Appeal v2: give Appeal your function's signature, get a command-line
interface--in process, or as a generated standalone script.
"""

__version__ = '2.0a0'

from .build import (
    add_option_override, add_parameter_usage, build,
    strip_first_argument_from_signature, strip_self_from_signature,
    )
from .codegen import (
    compile_command_set, compile_plan, emit, emit_command_set,
    emit_standalone, emit_standalone_command_set, emit_standalone_mcp,
    )
from .interpreter import dispatch as interpreter_dispatch
from .interpreter import parse as interpreter_parse
from .plan import Terminal, NO_DEFAULT, OptionRule, Plan, Slot
from .complete import complete, complete_set
from .read import read_csv, read_iterable, read_mapping
from .schema import schema, schema_set
from .runtime import (
    AppealConfigurationError, AppealDataError, AppealError,
    MultiOption, Option, StrictOption, Theme,
    UsageError, accumulator, counter, file, mapping, run_main, split,
    validate, validate_range,
    )

# every exception, both spellings (the prefixed forms are the
# real names--they're what tracebacks show, v1's rendering kept)
from .runtime import (
    AppealBaseException, AppealUsageError, ConfigurationError, DataError,
    )


import inspect as _inspect
import os as _os
import sys as _sys
import threading as _threading


def _config_vet(plan, table_words, config, command_plan_for=None):
    """
    Config layering's stage 1 (strict keys--"either this is ours,
    or it isn't"): every key must name a global-command option.
    Returns {key: the OptionRule}, or raises naming the offender--
    a config file is end-user input, so loudness is UsageError.
    """
    from .build import all_options
    from .plan import Terminal
    options = {}
    scoped = set()
    for owner, o in all_options(plan):
        if o.name in options and options[o.name] is not o:
            scoped.add(o.name)
        options.setdefault(o.name, o)
    positionals = set()
    def gather(p):
        for s in p.slots:
            positionals.add(s.name)
            if not isinstance(s.child, Terminal):
                gather(s.child)
    gather(plan)
    vetted = {}
    for key, value in config.items():
        if key in scoped or (key in options
                             and options[key].key in plan.scoped_keys):
            # refused BY DESIGN (ruled 2026-07-09): position is
            # the essence of a scoped option, and a mapping has no
            # position--the two transports don't compose.  (The
            # relax-later shape, should a real need appear, is
            # nested addressing through the window's parameter:
            # {'b': {'flavor': ...}}.)
            raise AppealConfigurationError(
                f"config: {key!r} names a scoped option (several "
                f"windows declare it, and position decides which--"
                f"a mapping has no position).  Set it on the "
                f"command line, or give the uses distinct "
                f"parameter names (@app.option)")
        rule = options.get(key)
        if rule is not None:
            vetted[key] = rule
            continue
        if key in table_words:
            raise AppealDataError(
                f"config: {key!r} is a command; config supplies "
                f"only global-command options")
        if key in positionals:
            raise AppealDataError(
                f"config: {key!r} is a positional argument; config "
                f"supplies only options")
        # say where the key actually lives, if anywhere
        if command_plan_for is not None:
            for word in table_words:
                try:
                    p = command_plan_for(word)
                except Exception:
                    continue
                if any(s.name == key for s in p.slots):
                    raise AppealDataError(
                        f"config: {key!r} is a positional argument "
                        f"of {word!r}; config supplies only "
                        f"global-command options")
                if any(o.name == key
                       for owner, o in all_options(p)):
                    raise AppealDataError(
                        f"config: {key!r} is an option of {word!r}; "
                        f"config supplies only global-command "
                        f"options (no per-command sections)")
        raise AppealDataError(
            f"config: {key!r} isn't an option of this program")
    return vetted


def _config_inject(vetted, config, given, usage):
    """
    The merge, atomic per option: an option argv mentioned wins
    whole; otherwise the config value enters `given` shaped like
    the command line would have shaped it, and stage 2 converts it
    through the ordinary pipeline.  Returns the injected keys.
    """
    from .read import _read_bool
    from .runtime import AppealError
    injected = {}
    for name, rule in vetted.items():
        key = rule.key
        if key in given:
            continue        # argv wins, whole
        value = config[name]
        kind = rule.kind
        if kind in ('flag', 'nullary'):
            try:
                wanted = _read_bool(value, f'config: {name}')
            except AppealError as e:
                # a config file is end-user input
                raise AppealDataError(str(e), usage) from None
            if wanted:
                given[key] = True if kind == 'flag' else ()
                injected[name] = key
            continue
        if kind in ('accumulate', 'fold'):
            if not isinstance(value, (list, tuple)):
                raise AppealDataError(
                    f"config: {name!r} repeats; give it a sequence "
                    f"(one entry per occurrence)", usage)
            given[key] = ([tuple(v) if isinstance(v, (list, tuple))
                           else (v,) for v in value]
                          if kind == 'fold' else list(value))
            injected[name] = key
            continue
        if kind == 'mapping':
            if not isinstance(value, dict):
                raise AppealDataError(
                    f"config: {name!r} collects KEY=VALUE pairs; "
                    f"give it a mapping", usage)
            given[key] = [f'{k}={v}' for k, v in value.items()]
            injected[name] = key
            continue
        if kind == 'group':
            if isinstance(value, dict):
                # by name, read_mapping style: order the child's
                # parameters
                ordered = []
                for s in rule.child.slots:
                    if s.name in value:
                        ordered.append(value[s.name])
                    else:
                        break
                extra = set(value) - {s.name for s in rule.child.slots}
                if extra:
                    raise AppealDataError(
                        f"config: {name!r}: unknown group "
                        f"argument(s) {sorted(extra)}", usage)
                given[key] = tuple(ordered)
            elif isinstance(value, (list, tuple)):
                given[key] = tuple(value)
            else:
                given[key] = (value,)
            injected[name] = key
            continue
        if kind == 'value' and len(rule.converters) > 1:
            if not isinstance(value, (list, tuple)):
                raise AppealDataError(
                    f"config: {name!r} takes "
                    f"{len(rule.converters) - 1} values; give it a "
                    f"sequence", usage)
            given[key] = list(value)
            injected[name] = key
            continue
        given[key] = value
        injected[name] = key
    return injected


def _is_class_command(obj):
    """
    A class, or a wrapped class (e.g. big's BoundInnerClass): the
    __wrapped__ convention is functools' and wrapt's, not any one
    library's.
    """
    return isinstance(obj, type) or isinstance(
        getattr(obj, '__wrapped__', None), type)


def _refuse_orphan_method(callable):
    """
    A registered command that looks like an undecorated class's
    method: first parameter self, dotted qualname, and no class
    claimed it.  Refuse by name--parsing a string into self helps
    nobody.
    """
    if _is_class_command(callable):
        return
    try:
        parameters = list(_inspect.signature(callable).parameters)
    except (ValueError, TypeError):
        return
    qualname = getattr(callable, '__qualname__', '')
    if parameters and parameters[0] == 'self' and '.' in qualname:
        raise AppealConfigurationError(
            f"{qualname}: first parameter is 'self' but no class "
            f"claims this command--did you forget to decorate the "
            f"class?")


class _SubRegistrar:
    "The object app.command('db') returns: registers db's subcommands."
    def __init__(self, app, parent, repeat=False):
        self.app = app
        self.parent = parent
        if repeat:
            app._sub_repeat[parent] = True

    def command(self, name=None):
        def decorator(callable):
            self.app._subs.setdefault(self.parent, []).append(
                (name or callable.__name__, callable))
            self.app._invalidate()
            return callable
        return decorator


class Processor:
    """
    One trip through one command line--v1's Processor returns,
    leaner.  app.parse(argv) builds one having run stage 1 only
    (the structural scan: zero user code, a malformed line dies
    there); execute() runs stage 2, the conversions and the
    commands themselves, left to right.  app.process() is both
    stages, fused.

    instances is the run's execution log, appended mechanically in
    execution order: one (command, instance) pair per command run.
    command is the registered callable--None for the global
    command--and instance is the object it constructed (None until
    class-based commands land).

    v1 compat: app.processor() returns an unparsed Processor;
    calling it with an argv runs both stages and returns the
    result (v1's callable execution object).
    """
    def __init__(self, app):
        self.app = app
        self.invocations = None    # stage 1's artifact: what would run
        self._tail = None
        self.instances = []
        self.result = None
        self._config = None        # vetted config layer, if any

    def __repr__(self):
        if self.invocations is None:
            return '<Processor (unparsed)>'
        parts = []
        for word, run, operands, given, positions in self.invocations:
            name = '(global)' if word is None else word
            text = f'{name} {" ".join(operands)}'.rstrip()
            if given:
                text += ' [' + ' '.join(sorted(given)) + ']'
            parts.append(text)
        if self._tail is not None:
            parts.append(f'({self._tail[0]})')
        return '<Processor: ' + '; '.join(parts) + '>'

    def _command_for(self, word):
        "The registered callable behind a word (None: the global)."
        if word is None:
            return None
        command = self.app._table().get(word)
        if command is not None:
            return command
        for subs in self.app._subs.values():
            for name, fn in subs:
                if name == word:
                    return fn
        return None   # pragma: no cover -- every dispatched word is
                      # registered or claimed in _subs; belt and braces

    def parse(self, argv, config=None):
        "Stage 1: scan argv.  Nothing executes.  Returns self."
        app = self.app
        app._compile()
        argv = list(argv)
        if config is not None:
            # strict keys, stage 1: a bad config does no work
            global_plan = app.global_plan
            if global_plan is None:
                for key in config:
                    raise AppealDataError(
                        f"config: {key!r} isn't an option of this "
                        f"program (it has no global command)")
            table = app._table()
            vetted = _config_vet(global_plan, frozenset(table), config,
                                 app.plan_for)
            self._config = (vetted, dict(config))
        kind = app._pieces[0]
        if kind == 'single':
            _, fused = app._pieces
            operands, given, rest, positions = fused.scan(argv)
            self.invocations = [(None, fused.run, operands, given,
                                 positions)]
            self._tail = None
            return self
        (_, parse_globals, commands, usage, default, auto_help,
         repeat, words) = app._pieces
        if auto_help and argv and argv[0] in ('-h', '--help'):
            self.invocations = []
            self._tail = ('listing',)
            return self
        from .runtime import scan_command_set
        self.invocations, self._tail = scan_command_set(
            argv, parse_globals, commands, usage, default, repeat, words)
        return self

    def execute(self):
        "Stage 2: conversions and commands, left to right."
        if self.invocations is None:
            raise AppealConfigurationError(
                "this Processor hasn't parsed anything yet")
        app = self.app
        app._last_processor = self
        if self._tail == ('bare',):
            # an empty command line: the listing, stdout, exit 1
            # (ruled 2026-07-09, git-style); nothing runs
            print('usage: ' + app._pieces[3])
            self.result = 1
            return 1
        result = None
        env = {}    # class-based commands: instances live here
        for word, run, operands, given, positions in self.invocations:
            injected = None
            if word is None and self._config is not None:
                # the config layer: defaults < config < argv,
                # atomic per option, global command only
                vetted, mapping = self._config
                usage = self.app.global_plan.usage()
                injected = _config_inject(vetted, mapping, given,
                                          usage)
            try:
                result = run(operands, given, positions, env)
            except UsageError as e:
                if injected and any(name in str(e) or key in str(e)
                                    for name, key in injected.items()):
                    raise AppealDataError(f"config: {e}") from None
                raise
            command = self._command_for(word)
            instance = result if _is_class_command(command) or (
                word is None and _is_class_command(app._global)) else None
            self.instances.append((command, instance))
            if (isinstance(result, int)
                    and not isinstance(result, bool) and result):
                # the early-exit contract, every command in a
                # cycle: a nonzero int halts dispatch
                self.result = result
                return result
        if self._tail is not None:
            kind = self._tail[0]
            if kind == 'listing':
                print('usage: ' + app._pieces[3])
                result = None
            elif kind == 'fused':
                _, word, fn, tokens = self._tail
                result = fn(tokens)
                command = app._table().get(word)
                if command is not None:   # pragma: no cover -- fused
                    # tails carry only the auto help/version words,
                    # which are never registered
                    self.instances.append((command, None))
            else:   # 'default'
                result = app._pieces[4]([])
                self.instances.append((app._default, None))
        self.result = result
        return result

    def __call__(self, args):
        "v1 compat: both stages in one call."
        self.parse(list(args))
        return self.execute()


class _CompileOnDispatch:
    """
    The commands mapping handed to run_command_set: .get(word)
    builds and compiles that one command at its first dispatch.
    The other commands stay untouched.  Also supplies the automatic
    `help` command, unless the program defines its own.
    """
    def __init__(self, app, usage):
        self.app = app
        self.usage = usage

    def get(self, word):
        table = self.app._table()
        if (word == 'version' and 'version' not in table
                and self.app.version is not None):
            def parse_version(argv):
                if argv:
                    raise UsageError(
                        'version takes no arguments', self.usage)
                print(self.app.version)
            return parse_version
        if word == 'help' and 'help' not in table:
            def parse_help(argv):
                # `help` alone: the listing; `help CMD`: CMD's
                # --help; the auto commands describe themselves
                # (help should DESCRIBE a topic, not run it)
                if not argv:
                    print('usage: ' + self.usage)
                    return
                topic = argv[0]
                if topic == 'help':
                    print('Print usage documentation on a '
                          'specific command.')
                    return
                if (topic == 'version' and 'version' not in table
                        and self.app.version is not None):
                    print("Print the program's version.")
                    return
                if topic not in table:
                    from .runtime import did_you_mean
                    raise UsageError(
                        f"unknown command {topic!r}"
                        f"{did_you_mean(topic, table)}",
                        self.usage)
                return self.app._parse_for(topic)(['--help'])
            return parse_help
        if word not in table:
            return None
        app = self.app
        if word in app._subs:
            return app._set_entry_for(word)
        parse = self.app._parse_for(word)
        if hasattr(parse, 'scan'):
            # two-stage dispatch (parse-before-execute)
            return (parse.scan, parse.run)
        # fused: _parse_for compiles every registered word two-stage
        # and nested parents are intercepted above; belt and braces
        return parse   # pragma: no cover


class Appeal:
    """
    The v2 API surface, matching v1's shape:

      * @app.command() functions are *subcommands*: the first
        operand on the line names one (literally--the function's
        name, no mangling), even when only one is registered.
      * @app.global_command() is the command with no name: its
        options and operands come before the command word.  With
        no @app.command()s at all, it owns the whole line--that's
        how you spell a program without subcommands.

    Decoration only records; the plans are built and compiled at
    first use (see "Laziness and late binding" in the grammar doc).
    """
    def __init__(self, name=None, *, theme=None, version=None, repeat=False,
                 errors=None, script=_sys.argv[0],
                 margin=79, indent=4):
        self.name = name
        # argv[0], captured HERE at the outer edge (its default is
        # read once, when this module is imported) rather than
        # sniffed from sys.argv deep in the machinery--so the
        # program name is a controllable input, not ambient state.
        # _prog() derives the displayed name from its basename;
        # name=, if given, overrides it outright.
        self.script = script
        # the file object main() prints error messages to,
        # default sys.stderr (the POSIX diagnostic convention, so
        # pipelines reading this program's stdout stay clean;
        # sys.stdout is v1's behavior).  Like print(file=None),
        # None resolves at error time.  Requested help always
        # prints to stdout.
        if errors is not None and not hasattr(errors, 'write'):
            raise AppealConfigurationError(
                f"errors= must be a writable file object "
                f"(sys.stderr, sys.stdout, ...), not {errors!r}")
        self.errors = errors
        # cycling: when a command's arguments are satisfied--all of
        # them, optional included--the next token may name another
        # command, and the line starts over (v1's repeat, rebuilt)
        self.repeat = repeat
        # None = auto (stock theme on a tty), False = never, or a
        # runtime.Theme; the environment always wins (resolve_theme).
        self.theme = theme
        self.version = version
        # the help formatter's knobs (v1's, wired 2026-07-09):
        # margin caps the wrap width (narrow terminals re-wrap
        # below it; pipes get the cap itself), and indent is the
        # left indent of the help tables--applied by re-indenting
        # the section templates, which own layout; overwrite
        # app.templates to go further
        for knob, value in (('margin', margin), ('indent', indent)):
            if not isinstance(value, int) or value <= 0:
                raise AppealConfigurationError(
                    f"{knob} must be a positive int, not {value!r}")
        self.margin = margin
        self.indent = indent
        # the help templates: a plain dict, yours to overwrite
        # entry by entry (see runtime.default_templates).
        from .runtime import default_templates
        self.templates = dict(default_templates)
        if indent != 4:
            import re as _re
            pad = ' ' * indent
            for key in ('arguments', 'options', 'commands'):
                template = self.templates.get(key)
                if template:
                    self.templates[key] = _re.sub(
                        r'(?m)^[ \t]+(?=\{argument\})', pad, template)
        # concurrency (ruled 2026-07-11): compilation runs LOCK-
        # FREE (it inspects user code, and we never hold a lock
        # over foreign code); this plain Lock guards only the
        # test-and-set installs into the caches below, so racing
        # first parses each build, one wins, the rest discard
        self._lock = _threading.Lock()
        self._commands = []       # (name, callable), in declaration order
        self._subs = {}           # parent name -> [(name, callable)]
        self._sub_repeat = {}     # parent name -> its set cycles
        self._method_owner = {}   # command word -> owning class's env key
        self._class_parents = {}  # command word -> the class itself
        self._default = None      # v1's default_command
        self._global = None
        self._parse = None
        self._pieces = None       # what a Processor drives, per shape
        self._set_entries = None  # nested set dicts, built per parent
        self._last_processor = None   # app.instances reads this
        self._plans = None        # {command word: Plan}, filled per word
        self._parses = None       # {command word: parse fn}, ditto
        self._global_plan = None

    def _invalidate(self):
        with self._lock:
            self._parse = None
            self._pieces = None
            self._set_entries = None
            self._plans = None
            self._parses = None
            self._global_plan = None

    def command(self, parent=None, repeat=False, name=None):
        """
        @app.command() registers a command.  app.command('db')
        names an existing command and returns a registrar whose
        .command() attaches subcommands to it: the parent runs
        first (like a global command of its own little set), then
        the subcommand.  repeat=True makes the parent's set cycle:
        after a subcommand's arguments, the next token may name
        another one.  name= overrides the command word (the
        default is the callable's literal name--no mangling, ever;
        name= is you saying the word out loud: dashes welcome,
        `Db` becomes `db`).
        """
        if parent is not None:
            return _SubRegistrar(self, parent, repeat)
        def decorator(callable):
            if _is_class_command(callable):
                # a class as a command: its __init__ is the parent
                # of its own little set, its decorated methods are
                # the subcommands (see _adopt_class)
                self._adopt_class(callable, repeat=repeat, name=name)
                return callable
            self._commands.append((name or callable.__name__, callable))
            self._invalidate()
            return callable
        return decorator

    def default_command(self):
        "v1's API: the command run when the line names no command."
        def decorator(callable):
            self._default = callable
            self._invalidate()
            return callable
        return decorator

    def processor(self):
        "v1's API: an unparsed Processor; call it with an argv."
        return Processor(self)

    def global_command(self):
        def decorator(callable):
            if _is_class_command(callable):
                # class-as-app (§8.6): the class's __init__ is the
                # global command; its decorated methods are the
                # program's commands.  Nothing is automatic--
                # decorate a method to expose it.
                self._adopt_class(callable, global_=True)
                return callable
            self._global = callable
            self._invalidate()
            return callable
        return decorator

    def _adopt_class(self, cls, global_=False, repeat=False, name=None):
        """
        §8.6's reclaim, by identity: @app.command() in the class
        body registered plain functions (and nested classes--their
        own decorators ran first); here they become this class's.
        Methods bind to the instance the class-command constructs;
        nested classes construct from the parent instance, so a
        bound inner class composes without Appeal knowing.
        """
        word = name or cls.__name__
        key = cls.__qualname__
        target = getattr(cls, '__wrapped__', cls)
        members = list(target.__dict__.values())
        claimed = [(name, fn) for name, fn in self._commands
                   if any(fn is m for m in members)]
        for name, fn in claimed:
            self._method_owner[name] = key
        if global_:
            # the class IS the app: its set is the top-level set;
            # the claimed commands stay top-level, now bound
            self._global = cls
        else:
            # a command: parent of its own little set
            claimed_ids = {id(fn) for _, fn in claimed}
            self._commands = [(n, f) for n, f in self._commands
                              if id(f) not in claimed_ids]
            self._commands.append((word, cls))
            if claimed:
                self._subs[word] = claimed
                self._class_parents[word] = cls
                if repeat:
                    self._sub_repeat[word] = True
            # no decorated methods: a leaf command that constructs
            # (the class-as-namespace pattern)
        self._invalidate()

    def option(self, parameter_name, *options,
               annotation=_inspect.Parameter.empty,
               default=_inspect.Parameter.empty):
        """
        Additional decorator for @command functions: blows away all
        default mappings for one keyword-only parameter and maps
        only the strings you specify (so naming just the long
        suppresses the auto short).  A fresh declaration, v1
        semantics: annotation/default here declare the option's
        grammar (converter, flag-ness); the parameter's own
        annotation and default don't leak in, and the parameter's
        default still fills when the option is absent.  Stack
        several to accumulate strings.
        """
        def decorator(callable):
            add_option_override(callable, parameter_name, options,
                                annotation=annotation, default=default)
            self._invalidate()
            return callable
        return decorator

    def complete(self, words, prefix=''):
        """
        Candidate completions for the partial word `prefix`, given
        the `words` already typed.  Shell integration scripts call
        this; an empty list means "no opinion" (operand values are
        the shell's business).
        """
        table = self._table()
        if not table:
            return complete(self.plan, words, prefix)
        sets = {}
        for parent, entries in self._subs.items():
            sets[parent] = {
                'commands': {
                    name: build(fn, name=name,
                                method_of=self._method_owner.get(name))
                    for name, fn in entries},
                'repeat': self._sub_repeat.get(parent, False),
            }
        auto_version = (self.version is not None
                        and 'version' not in table)
        return complete_set(self.plans, self.global_plan, words, prefix,
                            auto_version=auto_version,
                            repeat=self.repeat, sets=sets or None)

    def help(self):
        """
        Print the --help text (bare apps) or the command listing
        (sets), v1-style; also returns it.
        """
        table = self._table()
        if table:
            from .plan import command_set_usage
            from .help import summary, command_set_corpus
            from .runtime import render_help_page
            entries = [(w, summary(c)) for w, c in table.items()]
            corpus = command_set_corpus(self.global_plan, entries,
                                        'help' not in table,
                                        auto_version=self.version is not None
                                        and 'version' not in table)
            from .runtime import help_margin, resolve_theme
            text = render_help_page(
                command_set_usage(self._prog(), self.global_plan),
                corpus, self.templates,
                margin=help_margin(self.margin),
                theme=resolve_theme(self.theme, _sys.stdout)).rstrip('\n')
        else:
            from .help import merge_docs
            from .runtime import help_margin, render_help_page, resolve_theme
            plan = self.plan
            corpus = merge_docs(plan)
            text = render_help_page(
                plan.usage(), corpus, self.templates,
                margin=help_margin(self.margin),
                theme=resolve_theme(self.theme, _sys.stdout)).rstrip('\n')
        print(text)
        return text

    def documentation(self, format):
        """
        The program's documentation rendered in the named format--
        the grammar describing itself in one more dialect, like
        completion(shell).  Only 'man' (a troff man(1) page) for
        now; unknown formats refuse by name.  Returns the text;
        where it gets installed is packaging's business.
        """
        if format != 'man':
            raise AppealConfigurationError(
                f"documentation format {format!r} isn't supported "
                f"(only 'man', for now)")
        from .help import command_set_corpus, man_page, merge_docs, summary
        from .plan import command_set_usage
        prog = self._prog()
        version = str(self.version) if self.version is not None else None
        table = self._table()
        if not table:
            plan = self.plan
            return man_page(prog, merge_docs(plan), plan.usage(prog),
                            version=version)
        entries = [(w, summary(c)) for w, c in table.items()]
        corpus = command_set_corpus(
            self.global_plan, entries, 'help' not in table,
            auto_version=self.version is not None
            and 'version' not in table)
        pages = [(word,
                  self.plan_for(word).usage(f'{prog} {word}'),
                  merge_docs(self.plan_for(word)))
                 for word in table]
        return man_page(prog, corpus,
                        command_set_usage(prog, self.global_plan),
                        command_pages=pages, version=version)

    def schema(self):
        """
        The program described as JSON-safe data--the machine-
        readable twin of --help.  Pairs with read_mapping() to run
        a command from a JSON object.
        """
        table = self._table()
        if not table:
            return schema(self.plan)
        return schema_set(self.plans, self.global_plan, self._prog())

    def read_mapping(self, callable, mapping):
        "v1's API: call `callable` with values pulled from `mapping`."
        return read_mapping(callable, mapping)

    def read_iterable(self, callable, iterable):
        "v1's API: call `callable` once per row; returns the results."
        return read_iterable(callable, iterable)

    def read_csv(self, callable, reader, *, first_row_map=None):
        "v1's API: read_iterable for csv.reader input (see read_csv)."
        return read_csv(callable, reader, first_row_map=first_row_map)

    def unnested(self):
        """
        v1 compat marker: the decorated converter reads its keys
        from the enclosing mapping level.  v2 reads both the nested
        and flat spellings anyway, so this is a no-op.
        """
        def decorator(callable):
            return callable
        return decorator

    def parameter(self, parameter_name, *, usage):
        """
        Additional decorator for @command functions: renames one
        parameter in usage lines and help tables.  On an operand,
        the shown name; on an option, the metavar
        (`[-t|--times <COUNT>]`).  v1's API, extended: v1's
        @app.parameter only reached operands.
        """
        def decorator(callable):
            add_parameter_usage(callable, parameter_name, usage)
            self._invalidate()
            return callable
        return decorator

    argument = parameter    # v1's deprecated alias, kept

    # ---- first use: build and compile, one command at a time ----
    #
    # Laziness extends *per command*: dispatching (or examining)
    # one command never builds the others.  A config error in
    # command B surfaces when B is first used, not before.

    def _table(self):
        "The {command word: callable} table.  Cheap: no inspection."
        table = {}
        for name, callable in self._commands:
            if name in table:
                raise AppealConfigurationError(
                    f"two commands named {name!r}")
            table[name] = callable
        if self._global is not None and self._global.__name__ in table:
            raise AppealConfigurationError(
                f"the global command {self._global.__name__!r} has the "
                f"same name as a command")
        if not table and self._global is None:
            raise AppealConfigurationError(
                "no commands: use @app.command() or @app.global_command()")
        return table

    def plan_for(self, word):
        "The named command's Plan, built at first request."
        with self._lock:
            plan = (self._plans or {}).get(word)
        if plan is None:
            callable = self._table().get(word)
            if callable is None:
                # a nested parent lives only in ITS parent's
                # children (self._subs is flat, one entry per
                # parent at any depth)
                callable = next(
                    (fn for entries in self._subs.values()
                     for name, fn in entries if name == word),
                    None)
            if callable is None:
                raise AppealConfigurationError(f"no command named {word!r}")
            owner = self._method_owner.get(word)
            if owner is None:
                _refuse_orphan_method(callable)
            plan = build(callable, name=word, method_of=owner)
            with self._lock:
                if self._plans is None:
                    self._plans = {}
                plan = self._plans.setdefault(word, plan)
        return plan

    def _parse_for(self, word):
        with self._lock:
            parse = (self._parses or {}).get(word)
        if parse is None:
            if word in self._subs:
                # a parent with subcommands is a nested command set:
                # the parent is its global command, so parent options
                # come before the subcommand word and the parent runs
                # first--all machinery reused
                sub_plans = {name: build(fn, name=name,
                                         method_of=self._method_owner.get(name))
                             for name, fn in self._subs[word]}
                parse = compile_command_set(
                    sub_plans, self.plan_for(word), prog=word,
                    templates=self.templates, theme=self.theme,
                    max_columns=self.margin)
            else:
                parse = compile_plan(self.plan_for(word), templates=self.templates,
                                     theme=self.theme,
                                     max_columns=self.margin)
            with self._lock:
                if self._parses is None:
                    self._parses = {}
                parse = self._parses.setdefault(word, parse)
        return parse

    def _prog(self):
        return self.name or _os.path.basename(self.script) or 'program'

    def _set_entry_for(self, word):
        """
        The nested set dict behind a parent command: the parent
        compiled with the flexible boundary (a global command of
        its own little set), each subcommand a (scan, run) pair--
        or, recursively, another set dict.
        """
        with self._lock:
            entry = (self._set_entries or {}).get(word)
        if entry is not None:
            return entry
        from .plan import command_set_usage
        from .help import summary, command_set_corpus
        from .runtime import render_command_listing
        parent_plan = self.plan_for(word)
        parent = compile_plan(parent_plan, templates=self.templates,
                              theme=self.theme, boundary='flexible',
                              max_columns=self.margin)
        subs = {}
        for name, fn in self._subs[word]:
            if name in self._subs:
                subs[name] = self._set_entry_for(name)
                continue
            owner = self._method_owner.get(name)
            if owner is None:
                _refuse_orphan_method(fn)
            sub = compile_plan(build(fn, name=name, method_of=owner),
                               templates=self.templates, theme=self.theme,
                               max_columns=self.margin)
            subs[name] = (sub.scan, sub.run)
        entries = [(name, summary(fn)) for name, fn in self._subs[word]]
        corpus = command_set_corpus(parent_plan, entries, False)
        sub_usage = render_command_listing(
            command_set_usage(word, parent_plan), corpus,
            self.templates, margin=self.margin)
        entry = {'scan': parent.scan, 'run': parent.run,
                 'commands': subs,
                 'repeat': self._sub_repeat.get(word, False),
                 'words': frozenset(subs),
                 'usage': sub_usage}
        with self._lock:
            if self._set_entries is None:
                self._set_entries = {}
            entry = self._set_entries.setdefault(word, entry)
        return entry

    def _compile(self):
        with self._lock:
            if self._parse is not None:
                return self._parse
        table = self._table()
        if not table:
            # a global command and nothing else: it owns the whole
            # line, options after operands and all
            fused = compile_plan(self.global_plan, templates=self.templates,
                                 theme=self.theme,
                                 max_columns=self.margin)
            def parse(argv):
                processor = Processor(self)
                processor.parse(argv)
                return processor.execute()
            parse.scan = fused.scan
            parse.run = fused.run
            parse.source = fused.source
            with self._lock:
                if self._parse is None:
                    self._pieces = ('single', fused)
                    self._parse = parse
                return self._parse

        from .plan import command_set_usage
        from .runtime import run_command_set
        global_plan = self.global_plan
        command_words = frozenset(table) | (
            {'help'} if 'help' not in table else set()) | (
            {'version'} if self.version is not None
            and 'version' not in table else set())
        if global_plan is not None:
            fused = compile_plan(
                global_plan, templates=self.templates, theme=self.theme,
                max_columns=self.margin,
                command_split=(global_plan.minimum, global_plan.maximum,
                               command_words))
            parse_globals = (fused.scan, fused.run)
        else:
            parse_globals = None
        from .help import summary, command_set_corpus
        from .runtime import render_command_listing
        entries = [(word, summary(callable))
                   for word, callable in table.items()]
        auto = 'help' not in table
        corpus = command_set_corpus(
            global_plan, entries, auto,
            auto_version=self.version is not None
            and 'version' not in table)
        usage = render_command_listing(
            command_set_usage(self._prog(), global_plan),
            corpus, self.templates, margin=self.margin)

        commands = _CompileOnDispatch(self, usage)
        auto_help = 'help' not in table

        default = (compile_plan(build(self._default), templates=self.templates,
                                theme=self.theme,
                                max_columns=self.margin)
                   if self._default is not None else None)

        pieces = ('set', parse_globals, commands, usage, default,
                  auto_help, self.repeat, command_words)

        def parse(argv):
            processor = Processor(self)
            processor.parse(argv)
            return processor.execute()
        with self._lock:
            if self._parse is None:
                self._pieces = pieces
                self._parse = parse
            return self._parse

    @property
    def plan(self):
        "The lone plan of a global-command-only app."
        if self._table():
            raise AppealConfigurationError(
                "this program has subcommands; use .plan_for(name)")
        return self.global_plan

    @property
    def plans(self):
        "Every command's Plan.  Deliberately eager: builds them all."
        return {word: self.plan_for(word) for word in self._table()}

    @property
    def global_plan(self):
        with self._lock:
            plan = self._global_plan
        if plan is None and self._global is not None:
            plan = build(self._global)
            with self._lock:
                if self._global_plan is None:
                    self._global_plan = plan
                plan = self._global_plan
        return plan

    def parse(self, args=None, config=None):
        """
        Stage 1 only: scan args (default: sys.argv[1:]) into a
        Processor.  The structural parse runs--zero user code; a
        malformed line raises here--and nothing executes.  Call the
        Processor's execute() for stage 2; printing it is a dry
        run.  config, if given, is ONE mapping layering the global
        command's options: defaults < config < args (see the
        grammar's config layering section).
        """
        processor = Processor(self)
        return processor.parse(
            _sys.argv[1:] if args is None else list(args), config)

    def process(self, args=None, config=None):
        """
        Parse args (default: sys.argv[1:]) and invoke the command;
        returns its return value.
        """
        processor = Processor(self)
        processor.parse(
            _sys.argv[1:] if args is None else list(args), config)
        return processor.execute()

    @property
    def instances(self):
        """
        The most recent run's execution log: (command, instance)
        pairs in execution order (see Processor).
        """
        processor = self._last_processor
        return processor.instances if processor is not None else []

    def main(self, args=None, config=None):
        "Parse-and-execute with polite error handling; returns an exit code."
        import os as _os
        if (args is None and '_APPEAL_COMPLETE' in _os.environ
                and not _sys.argv[1:]):
            # a shell-completion reentry: bare args, mode in the
            # environment.  Answer it instead of parsing.
            from .runtime import completion_reentry
            return completion_reentry(
                lambda words, prefix: self.complete(words, prefix),
                self._prog())
        parse = self._compile()
        if config is not None:
            fused = parse
            def parse(args, _config=config):
                processor = Processor(self)
                processor.parse(list(args), _config)
                return processor.execute()
        version = str(self.version) if self.version is not None else None
        return run_main(parse, args, theme=self.theme, errors=self.errors,
                        version=version)

    def _mcp_instance(self, config):
        """
        Construct-once, the class-as-app half of MCP: the global
        class's __init__ runs at server startup, fed by config
        under the layering rules (strict keys, global-command
        options only), and every method tool binds to the one
        instance.  Returns the instance, or None when the global
        command isn't a class.  A required __init__ positional has
        no coverage (config supplies only options), so it refuses
        here--at startup, not mid-call.
        """
        table = self._table()
        global_plan = self.global_plan
        if not (table and global_plan is not None
                and global_plan.constructs is not None):
            if config is not None:
                raise AppealConfigurationError(
                    "mcp(): config feeds a class-based program's "
                    "__init__ at server startup; this program has "
                    "no class to construct")
            return None
        _config_vet(global_plan, frozenset(table), config or {},
                    command_plan_for=self.plan_for)
        return read_mapping(global_plan, config or {})

    def _mcp_bound_plan(self, word, plan, instance):
        """
        The plan a tool call reads through.  A method command
        rebuilds from the method bound to the startup instance
        (self is gone from the signature, so read_mapping drives
        it like any function); everything else reads as-is.
        """
        if plan.binds is None:
            return plan
        if instance is None:   # pragma: no cover -- method commands
            # exist only under a class global, whose instance always
            # constructs at startup; belt and braces
            raise AppealConfigurationError(
                f"{word!r}: bound to {plan.binds!r}, and no startup "
                f"instance provides it")
        if plan.constructs is not None:
            # a bound inner class: construction goes through the
            # parent instance's attribute (BIC composes)
            return build(getattr(instance, plan.name), name=plan.name)
        return build(plan.callable.__get__(instance), name=plan.name)

    def mcp(self, *, config=None, version=None):
        """
        Serve this program's commands as MCP tools--the Model
        Context Protocol's stdio transport, stdlib only.  Each
        command becomes a tool: its docstring summary is the
        description, its signature the input schema, and calls
        arrive as mappings through the read driver (the same
        rules as read_mapping: converters always apply, defaults
        fill absences).  A class-based program constructs its
        instance ONCE, at server startup: config feeds __init__
        (the layering rules), and method tools dispatch bound.
        Runs until stdin closes.
        """
        from .runtime import run_mcp
        from .schema import mcp_input_schema
        from .help import summary
        table = self._table()
        if self._subs:
            raise AppealConfigurationError(
                "nested subcommands aren't in mcp(); give the "
                "command a flat name instead (name='db add')")
        instance = self._mcp_instance(config)
        if not table:
            commands = {self._prog(): self.global_plan}
        else:
            commands = {word: self.plan_for(word) for word in table}
        tools = {}
        for word, plan in commands.items():
            bound = self._mcp_bound_plan(word, plan, instance)
            tools[word] = (summary(plan.callable) or '',
                           mcp_input_schema(bound),
                           lambda arguments, p=bound:
                               read_mapping(p, arguments))
        return run_mcp(tools, self._prog(),
                       str(version or self.version or '0'))

    def repl(self, *, prompt=None, banner=None):
        """
        §8.9: an interactive mode for any Appeal program--read a
        line, split it, feed it through the parser exactly as a
        command line, execute, loop.  Tab completion is the same
        machinery the shells use.  EOF (^D) or `quit` leaves.
        Returns None.
        """
        import shlex as _shlex
        prog = self._prog()
        prompt = prompt if prompt is not None else f'{prog}> '
        try:
            import readline as _readline

            def completer(text, state):
                buffer = _readline.get_line_buffer()
                try:
                    words = _shlex.split(buffer[:_readline.get_begidx()])
                except ValueError:
                    words = buffer[:_readline.get_begidx()].split()
                try:
                    candidates = self.complete(words, text)
                except Exception:
                    candidates = []
                return candidates[state] if state < len(candidates) else None

            _readline.set_completer(completer)
            _readline.set_completer_delims(' \t')
            _readline.parse_and_bind('tab: complete')
        except ImportError:      # pragma: no cover -- no readline
            pass
        if banner is not None:
            print(banner)
        from .runtime import AppealDataError
        while True:
            try:
                line = input(prompt)
            except EOFError:
                print()
                return
            except KeyboardInterrupt:
                print()
                continue
            try:
                words = _shlex.split(line)
            except ValueError as e:
                print(f'error: {e}')
                continue
            if not words:
                continue
            if words == ['quit'] or words == ['exit']:
                return
            try:
                result = self.process(words)
            except AppealDataError as e:
                print(f'error: {e}')
                usage = getattr(e, 'usage', None)
                if usage:
                    print(f'usage: {usage}')
            except AppealConfigurationError as e:
                print(f'configuration error: {e}')
            else:
                if result is not None:
                    print(result)

    def completion(self, shell):
        """
        The shell function text that wires this program's name to
        tab completion--source it, or install it in the shell's
        completion directory.  The zero-effort spelling sources it
        directly:

            eval "$(env _APPEAL_COMPLETE=source_bash mytool)"
        """
        from .runtime import completion_script
        return completion_script(shell, self._prog())

    def standalone(self, *, argv0=None):
        """
        The text of a standalone script implementing this program.
        Necessarily eager: the script is a whole-program artifact,
        so every command is built and every ref rendered (refusals
        included--the north star's teeth bite here).
        """
        if self._table():
            def sub_plan(name, fn):
                # nested parents are fine: self._subs is flat
                # (every parent maps its own children), and the
                # emitter reassembles the tree, deepest first
                return build(fn, name=name,
                             method_of=self._method_owner.get(name))
            subs = {parent: {name: sub_plan(name, fn)
                             for name, fn in entries}
                    for parent, entries in self._subs.items()}
            return emit_standalone_command_set(
                self.plans, self.global_plan,
                argv0=argv0 or self._prog(),
                templates=self.templates, theme=self.theme,
                repeat=self.repeat, subs=subs or None,
                sub_repeat=dict(self._sub_repeat) or None,
                errors=self.errors, version=self.version,
                max_columns=self.margin)
        return emit_standalone(self.global_plan, argv0=argv0,
                               templates=self.templates, theme=self.theme,
                               errors=self.errors, version=self.version,
                               max_columns=self.margin)

    def standalone_mcp(self, *, argv0=None, config=None, version=None):
        """
        The text of a standalone MCP server implementing this
        program's commands as tools.  Same north star, different
        transport.  A class-based program bakes config into the
        script as a literal; the script constructs the instance
        at startup.
        """
        table = self._table()
        if self._subs:
            raise AppealConfigurationError(
                "nested subcommands aren't in mcp(); give the "
                "command a flat name instead (name='db add')")
        if not table:
            commands = {self._prog(): self.global_plan}
        else:
            commands = self.plans
        global_plan = self.global_plan
        constructs = (table and global_plan is not None
                      and global_plan.constructs is not None)
        if constructs:
            _config_vet(global_plan, frozenset(table), config or {},
                        command_plan_for=self.plan_for)
            # required-without-coverage refuses at EMISSION, not
            # in the deployed script (the north star's teeth)
            for slot in global_plan.slots:
                if slot.required and slot.default is NO_DEFAULT:
                    raise AppealConfigurationError(
                        f"can't emit a standalone MCP server: "
                        f"{global_plan.name}.__init__ requires "
                        f"{slot.name!r}, and config supplies only "
                        f"options")
        elif config is not None:
            raise AppealConfigurationError(
                "standalone_mcp(): config feeds a class-based "
                "program's __init__ at server startup; this "
                "program has no class to construct")
        return emit_standalone_mcp(
            commands, argv0=argv0 or self._prog(),
            version=str(version or self.version or '0'),
            global_plan=global_plan if constructs else None,
            config=config)

    def write_standalone(self, path, *, argv0=None):
        "Write the standalone script to path."
        text = self.standalone(argv0=argv0)
        with open(path, 'wt', encoding='utf-8') as f:
            f.write(text)
        return path
