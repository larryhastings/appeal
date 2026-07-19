#!/usr/bin/env python3
#
# appeal -- Appeal 1.0, the ground-up rewrite of the 0.6 line.
# Copyright 2021-2026 by Larry Hastings
#
# (In the engineering docs the rewrite is nicknamed "v2" and the
# 0.6 line "v1".)  The 0.6 source--and the old argument_grouping.py
# parameter grouper it shipped with--lives on this branch's history.
#
# The spec of record is appeal.grammar.md; the design rationale
# is appeal.proposal.md.  North star: every command must be
# emittable as a *standalone*, dependency-free Python script
# (see codegen.emit_standalone).

"""
Appeal: give Appeal your function's signature, get a command-line
interface--in process, or as a generated standalone script.
"""

__version__ = '1.0'

from .build import (
    add_option_override, add_parameter_usage, build,
    default_options, default_long_option, default_short_option,
    strip_first_argument_from_signature, strip_self_from_signature,
    )
from .codegen import (
    compile_command_set, compile_plan, emit, emit_command_set,
    emit_standalone, emit_standalone_command_set, emit_standalone_mcp,
    )
from .interpreter import dispatch as interpreter_dispatch
from .interpreter import parse as interpreter_parse
from .plan import Terminal, NO_DEFAULT, OptionRule, Plan, Slot
from .plan import _validate_arg_format
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


def _config_inject(vetted, config, given, usage, scoped_keys=frozenset()):
    """
    The merge, atomic per option: an option argv mentioned wins
    whole; otherwise the config value enters `given` shaped like
    the command line would have shaped it, and stage 2 converts it
    through the ordinary pipeline.  A group's mapping value reads
    read_mapping style--its parameters by name AND its own options,
    recursively.  Returns the injected keys.
    """
    from .read import _read_bool
    from .runtime import AppealError
    injected = {}

    def shape(name, rule, value):
        key = rule.key
        if key in given:
            return          # argv wins, whole
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
            return
        if kind in ('accumulate', 'fold'):
            if not isinstance(value, (list, tuple)):
                raise AppealDataError(
                    f"config: {name!r} repeats; give it a sequence "
                    f"(one entry per occurrence)", usage)
            given[key] = ([tuple(v) if isinstance(v, (list, tuple))
                           else (v,) for v in value]
                          if kind == 'fold' else list(value))
            injected[name] = key
            return
        if kind == 'mapping':
            if not isinstance(value, dict):
                raise AppealDataError(
                    f"config: {name!r} collects KEY=VALUE pairs; "
                    f"give it a mapping", usage)
            given[key] = [f'{k}={v}' for k, v in value.items()]
            injected[name] = key
            return
        if kind == 'group':
            if isinstance(value, dict):
                # by name, read_mapping style: the child's
                # parameters in order, and its own options
                # recursively (each still atomic vs argv)
                option_rules = {o.name: o for o in rule.child.options}
                ordered = []
                for s in rule.child.slots:
                    if s.name in value:
                        ordered.append(value[s.name])
                        injected[f'{name}.{s.name}'] = key
                    else:
                        break
                extra = (set(value)
                         - {s.name for s in rule.child.slots}
                         - set(option_rules))
                if extra:
                    raise AppealDataError(
                        f"config: {name!r}: unknown group "
                        f"argument(s) {sorted(extra)}", usage)
                given[key] = tuple(ordered)
                injected[name] = key
                for inner_name, inner_rule in option_rules.items():
                    if inner_name not in value:
                        continue
                    if inner_rule.key in scoped_keys:
                        # same ruling as the top level: a scoped
                        # option has no position in a mapping
                        raise AppealConfigurationError(
                            f"config: {name!r}.{inner_name!r} names "
                            f"a scoped option; set it on the "
                            f"command line")
                    shape(f'{name}.{inner_name}', inner_rule,
                          value[inner_name])
            elif isinstance(value, (list, tuple)):
                given[key] = tuple(value)
                injected[name] = key
            else:
                given[key] = (value,)
                injected[name] = key
            return
        if kind == 'value' and len(rule.converters) > 1:
            if not isinstance(value, (list, tuple)):
                raise AppealDataError(
                    f"config: {name!r} takes "
                    f"{len(rule.converters) - 1} values; give it a "
                    f"sequence", usage)
            given[key] = list(value)
            injected[name] = key
            return
        given[key] = value
        injected[name] = key

    for name, rule in vetted.items():
        shape(name, rule, config[name])
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
        """
        The registered callable behind a word, for the instances
        log (None: the global).  A bare word naming DIFFERENT
        callables under different parents is ambiguous from here
        (invocations don't carry their parent), so the log
        answers None rather than guess wrong.
        """
        if word is None:
            return None
        command = self.app._table().get(word)
        if command is not None:
            return command
        matches = {id(fn): fn for subs in self.app._subs.values()
                   for name, fn in subs if name == word}
        if len(matches) == 1:
            (fn,) = matches.values()
            return fn
        return None

    def parse(self, argv, config=None):
        "Stage 1: scan argv.  Nothing executes.  Returns self."
        app = self.app
        app._compile()
        argv = list(argv)
        if config is not None:
            # strict keys, stage 1: a bad config does no work
            global_plan = app.global_plan
            if (global_plan is not None
                    and getattr(global_plan.callable,
                                'appeal_precommand', False)):
                # the precommand hosting the global plan isn't a
                # global COMMAND; config has nothing to configure
                global_plan = None
            if global_plan is None:
                # no global command: any key is a refusal by
                # name; an empty mapping lays nothing over
                # nothing, a no-op
                for key in config:
                    raise AppealDataError(
                        f"config: {key!r} isn't an option of this "
                        f"program (it has no global command)")
            else:
                table = app._table()
                vetted = _config_vet(global_plan, frozenset(table),
                                     config, app.plan_for)
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
        if (app._help_enabled and argv
                and not app.root._precommand_options.get('help')
                and argv[0] in ('-h', '--help')):
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
                injected = _config_inject(
                    vetted, mapping, given, usage,
                    self.app.global_plan.scoped_keys)
            injected_params = set()
            for injected_name, injected_key in (injected or {}).items():
                # 'where.deep' attributes errors about 'where'
                # OR 'deep'; keys ('--where') attribute count
                # errors, which name the option string
                injected_params.update(injected_name.split('.'))
                injected_params.add(injected_key)
            try:
                result = run(operands, given, positions, env)
            except UsageError as e:
                # provenance travels structurally: the error says
                # WHICH parameter it's about (e.param), and only
                # an error about something config supplied becomes
                # a config error--never a substring guess
                if getattr(e, 'param', None) in injected_params:
                    raise AppealDataError(
                        f"config: {e}", getattr(e, 'usage', None),
                        param=e.param) from None
                raise
            command = self._command_for(word)
            if word is None and app._global is None:
                # the precommand hosting the global slot: not a
                # command execution, keep the log clean
                continue
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
        if False and (word == 'version' and 'version' not in table
                and self.app.version is not None):
            def parse_version(argv):
                if argv:
                    raise UsageError(
                        'version takes no arguments', self.usage)
                print(self.app.version)
            return parse_version
        if False and word == 'help' and 'help' not in table:
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
                if False and (topic == 'version' and 'version' not in table
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
        node = app._children.get(word)
        if node is not None and node._children:
            return app._set_entry_for(word, node)
        parse = self.app._parse_for(word)
        if hasattr(parse, 'scan'):
            # two-stage dispatch (parse-before-execute)
            return (parse.scan, parse.run)
        # fused: _parse_for compiles every registered word two-stage
        # and nested parents are intercepted above; belt and braces
        return parse   # pragma: no cover


def _help_topic(topic=''):
    "The precommand help option's optional greedy oparg."
    return topic


def default_mappings(app):
    """
    The stock program-level defaults pass (Larry's design,
    2026-07-19).  Runs once, at first compile, after all
    registration.  Maps, each only if not already mapped:

      * the `version` command -> app.default_version, and
        -V/--version -> the precommand (when version= is set);
      * the `help` command -> app.default_help (when help=).

    Replace it (Appeal(default_mappings=...)) to change the
    policy; pass None for no default mappings at all.  This
    function is importable, so a custom policy can call it and
    then adjust.
    """
    # snapshot FIRST: the version/help COMMANDS only make sense
    # for a program that has commands (v1's rule; an ls-style
    # global-only program gets the options, never command words)
    has_commands = bool(app.commands)
    if app.version is not None:
        if has_commands and 'version' not in app.commands:
            app.command('version')(app.default_version)
        free = [s for s in ('-V', '--version')
                if s not in app.options]
        if free:
            app.precommand_option('version', *free)
    if app._help_enabled and has_commands:
        if 'help' not in app.commands:
            app.command('help')(app.default_help)
        free = [s for s in ('-h', '--help')
                if s not in app.options]
        if free:
            app.precommand_option('help', *free)


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
    def __init__(self, name=None, *, parent=None,
                 theme=None, version=None, repeat=False,
                 errors=None, script=_sys.argv[0],
                 margin=79, indent=4,
                 positional_argument_usage_format='{name}',
                 default_options=default_options,
                 default_mappings=default_mappings, help=True):
        self.name = name
        # the command tree (v1's model, restored 2026-07-18 by
        # Larry's ruling): a tree of Appeal instances, one per
        # command word, linked by .parent.  A node's _impl is its
        # command function--for a node with children, that
        # function is the global command of its own little set.
        # The flat structures the compiler consumes (_commands,
        # _subs, ...) are read-only views derived from this tree.
        self.parent = parent
        self._children = {}       # command word -> child Appeal
        self._impl = None         # this node's command function
        self._auto_impl = None    # synthesized fn for a pure dispatcher
        self._node_default = None # this node's default command
        self._node_repeat = False # this node's set cycles
        if parent is not None:
            # a subcommand node is a full Appeal; the program
            # knobs are the root's, copied as processed values
            for attr in ('_help_enabled', 'default_options',
                         'default_mappings',
                         'positional_argument_usage_format',
                         'script', 'errors', 'repeat', 'theme',
                         'margin', 'indent', 'templates'):
                setattr(self, attr, getattr(parent, attr))
            self.version = None
            self._finalized = True      # the ROOT runs the pass
            self._precommand_options = {}
            self._lock = _threading.Lock()
            self._method_owner = parent._method_owner
            self._init_caches()
            if name is not None:
                parent._children[name] = self
                parent._invalidate()
            return
        # whether Appeal supplies automatic help (v1's knob): the
        # per-command -h/--help option AND, for a program with
        # commands, the `help` command.  help=False suppresses all
        # of it--the program answers -h/--help only if it declares
        # them itself.  (A command that defines its own help still
        # wins even when help=True; this is the blanket off switch.)
        self._help_enabled = bool(help)
        # the option-string policy (v1's knob, restored): a callable
        # (name, annotation, default) -> list of option strings, run
        # at build time on every automatically-mapped keyword-only
        # parameter.  The stock policy adds a long and a short;
        # default_long_option drops the short, default_short_option
        # drops the long, or supply your own.  Its output--the
        # strings--is baked into the compiled parser, so a custom
        # policy never needs to ride into a standalone script.
        if default_options is not None and not callable(default_options):
            raise AppealConfigurationError(
                f"default_options must be callable or None, "
                f"not {default_options!r}")
        self.default_options = default_options
        # the program-level defaults pass (Larry's design,
        # 2026-07-19): default_mappings(app) runs ONCE, at first
        # compile, after all registration--the stock policy maps
        # the `help` and `version` commands and the precommand's
        # -V/--version, each only if not already mapped.  None:
        # no default semantics at all.  Rhymes with
        # default_options: that one derives a parameter's strings,
        # this one decides the program's default mappings.
        if default_mappings is not None and not callable(default_mappings):
            raise AppealConfigurationError(
                f"default_mappings must be callable or None, "
                f"not {default_mappings!r}")
        self.default_mappings = default_mappings
        self._finalized = False
        self._precommand_options = {}   # param -> (strings...)
        # how an operand renders in usage lines and help tables:
        # a format string over the parameter NAME (v1's knob,
        # restored).  '{name}' (default) shows the bare name; the
        # only interpolations are {name} and {name.upper()}, so
        # '<{name}>' gives <name> and '{name.upper()}' gives NAME.
        # Applies to positional operands AND option operands
        # (opargs) alike; an explicit @app.parameter usage= wins
        # outright over the format.
        _validate_arg_format(positional_argument_usage_format)
        self.positional_argument_usage_format = \
            positional_argument_usage_format
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
        self._method_owner = {}   # id(callable) -> owning class's env key
        self._init_caches()

    def _init_caches(self):
        self._parse = None
        self._pieces = None       # what a Processor drives, per shape
        self._set_entries = None  # nested set dicts, built per parent
        self._last_processor = None   # app.instances reads this
        self._plans = None        # {command word: Plan}, filled per word
        self._parses = None       # {command word: parse fn}, ditto
        self._global_plan = None

    def _invalidate(self):
        # registration under a node changes every ancestor's
        # compiled artifacts (they embed the descendants); a
        # node's own descendants embed nothing of it, so down
        # the tree nothing staling
        node = self
        while node is not None:
            with node._lock:
                node._parse = None
                node._pieces = None
                node._set_entries = None
                node._plans = None
                node._parses = None
                node._global_plan = None
            node = node.parent

    # ------------------------------------------------------------
    # the command tree: registration
    # ------------------------------------------------------------

    def _child(self, word):
        "Fetch-or-create the child Appeal for a command word."
        node = self._children.get(word)
        if node is None:
            node = Appeal(word, parent=self)
        return node

    def __call__(self, callable):
        """
        Calling an Appeal node with a callable sets the node's
        command function (v1): `@app.command('sync-all')`
        decorates through here, so the command word is the node's
        name and the function's own name is ignored.  On the root
        it sets the global command.  Decorating again replaces.
        """
        if _is_class_command(callable):
            if self.parent is None:
                self._adopt_class(callable, global_=True)
            else:
                self.parent._adopt_class(callable, name=self.name)
            return callable
        self._impl = callable
        self._invalidate()
        return callable

    @property
    def root(self):
        "The tree's root Appeal--the program."
        node = self
        while node.parent is not None:
            node = node.parent
        return node

    # ------------------------------------------------------------
    # the public introspection API (Larry's design, 2026-07-19):
    # queryable by default_mappings callbacks and anyone else
    # ------------------------------------------------------------

    @property
    def commands(self):
        """
        Read-only mapping: command word -> the child Appeal node,
        in definition order.  The node IS the configuration
        object: .handler is its function, .commands its
        subcommands, .options its option table, .default_handler
        its default command.
        """
        import types as _types
        return _types.MappingProxyType(self._children)

    @property
    def handler(self):
        """
        This node's command function.  On the root, the global
        command; on a child, the function bound to its word.
        None if never bound.
        """
        return self._impl

    @property
    def default_handler(self):
        "The default command's function (None if unset)."
        return self._node_default

    @property
    def options(self):
        """
        Read-only mapping: option string -> the OptionRule that
        owns it, declaration order, converters' nested options
        included.  On the root: every string mapped in the
        precommand+global era.  Compiles what it needs, lazily,
        like .plan and .plans.
        """
        import types as _types
        from .build import all_options
        table = {}
        plan = None
        if self._impl is not None:
            plan = (self.global_plan if self.parent is None
                    else self.plan_for_self())
        if plan is not None:
            for owner, o in all_options(plan):
                for s in o.strings:
                    table.setdefault(s, o)
        for param, strings in self.root._precommand_options.items():
            for s in strings:
                table.setdefault(s, None)
        return _types.MappingProxyType(table)

    def plan_for_self(self):
        "This node's own Plan (the root: the global plan)."
        if self.parent is None:
            return self.global_plan
        return self.root.plan_for(self.name)

    # ------------------------------------------------------------
    # the default mappings pass and its default implementations
    # ------------------------------------------------------------

    def _finalize(self):
        """
        Run the root's default_mappings pass exactly once, at
        first compile--whatever triggered it.  It never re-runs;
        mappings changed afterward are the changer's business.
        """
        root = self.root
        if root._finalized:
            return
        root._finalized = True      # first: registrations inside
                                    # must not recurse
        if root.default_mappings is not None:
            root.default_mappings(root)

    def default_version(self):
        "Print the program's version."
        print(self.root.version)

    def default_help(self, topic=''):
        "Print usage documentation on a specific command."
        root = self.root
        table = root._table()
        if not topic:
            root.help()
            return
        if topic == 'help':
            print('Print usage documentation on a specific command.')
            return
        fn = table.get(topic)
        if getattr(fn, '__func__', None) is Appeal.default_version:
            # a stock command describes itself with its summary
            print(_inspect.getdoc(fn))
            return
        if topic not in table:
            from .runtime import did_you_mean
            from .plan import command_set_usage
            raise UsageError(
                f"unknown command {topic!r}"
                f"{did_you_mean(topic, table)}",
                command_set_usage(root._prog(), root._display_global()))
        root._parse_for(topic)(['--help'])

    def precommand(self, *, version=False, help=None):
        """
        The stage ahead of the global command: program metadata.
        Its options live in the precommand+global era and unmap at
        the first command word.  Absent from the grammar entirely
        when default_mappings mapped nothing to it.
        """
        if version:
            _sys.exit(self.default_version())
        if help is not None:
            _sys.exit(self.default_help(help))

    def precommand_option(self, parameter_name, *strings):
        """
        Map option strings to a precommand parameter ('version' or
        'help').  The public mutation API default_mappings uses;
        strings must be free (the caller checks .options first).
        """
        if parameter_name not in ('version', 'help'):
            raise AppealConfigurationError(
                f"precommand_option: no precommand parameter "
                f"named {parameter_name!r} (only 'version' and "
                f"'help')")
        if not strings:
            raise AppealConfigurationError(
                "precommand_option: no option strings given")
        root = self.root
        root._precommand_options[parameter_name] = tuple(strings)
        root._invalidate()

    def _command_callable(self):
        """
        This node's command function--synthesized (a no-op taking
        nothing) for a pure dispatcher, a parent that was only
        ever chained through; None for a word that was named but
        never bound (not a command at all).
        """
        if self._impl is not None:
            return self._impl
        if not self._children:
            return None
        if self._auto_impl is None:
            def dispatcher():
                pass
            dispatcher.__name__ = self.name or 'command'
            dispatcher.__qualname__ = dispatcher.__name__
            dispatcher.__doc__ = None
            self._auto_impl = dispatcher
        return self._auto_impl

    def _iter_set_nodes(self):
        "Every descendant, any depth, that parents a nested set."
        for word, node in self._children.items():
            if node._children:
                yield word, node
                yield from node._iter_set_nodes()

    # -- the flat views the compiler consumes: read-only,
    # -- derived from the tree

    @property
    def _commands(self):
        "(word, callable) for this node's children, decl order."
        out = []
        for word, node in self._children.items():
            impl = node._command_callable()
            if impl is not None:
                out.append((word, impl))
        return out

    @property
    def _global(self):
        return self._impl

    @property
    def _default(self):
        return self._node_default

    @property
    def _subs(self):
        """
        Flat: parent word -> [(word, callable)] for every nested
        set at any depth.  Flatness means parent words must be
        unique tree-wide (restrictive; path-addressed sets can
        relax it later).
        """
        out = {}
        for word, node in self._iter_set_nodes():
            if word in out:
                raise AppealConfigurationError(
                    f"two nested command sets named {word!r}")
            out[word] = [(w, c._command_callable())
                         for w, c in node._children.items()
                         if c._command_callable() is not None]
        return out

    @property
    def _sub_repeat(self):
        return {word: True for word, node in self._iter_set_nodes()
                if node._node_repeat}

    @property
    def _sub_defaults(self):
        "Parent word -> its set's default command, where set."
        return {word: node._node_default
                for word, node in self._iter_set_nodes()
                if node._node_default is not None}

    def command(self, name=None, *, repeat=False, parent=None):
        """
        @app.command() registers a command under the callable's
        literal name (no mangling, ever).  app.command('db')
        returns the child Appeal for the word 'db', creating it
        if needed--the command tree is a tree of Appeal instances
        (v1).  Use the child as a decorator to set the command's
        function while saying the word out loud
        (`@app.command('sync-all')`: dashes welcome, the
        function's name is ignored), or keep going: `.command()`
        attaches subcommands (the parent runs first, like a
        global command of its own little set),
        `.default_command()` picks what runs when the line stops
        at the parent--and every other Appeal method is there,
        because the child IS an Appeal.  repeat=True makes the
        node's set cycle: after a subcommand's arguments, the
        next token may name another one.  parent= is the older
        v2 spelling of the same fetch: command(parent='db') ==
        command('db').
        """
        if parent is not None:
            if name is not None:
                raise AppealConfigurationError(
                    "command(): give a name or parent=, not both")
            name = parent
        if name is not None:
            if not isinstance(name, str):
                raise AppealConfigurationError(
                    f"command(): the command word must be a "
                    f"string, not {name!r}")
            node = self._child(name)
            if repeat and not node._node_repeat:
                node._node_repeat = True
                self._invalidate()
            return node
        def decorator(callable):
            if _is_class_command(callable):
                # a class as a command: its __init__ is the parent
                # of its own little set, its decorated methods are
                # the subcommands (see _adopt_class)
                self._adopt_class(callable, repeat=repeat)
                return callable
            node = self._child(callable.__name__)
            node._node_repeat = node._node_repeat or repeat
            return node(callable)
        return decorator

    def default_command(self):
        """
        v1's API: the command run when the line stops at this
        node--for the root, a line naming no command; for a
        subcommand node (`@app.command('db').default_command()`),
        a line ending at the parent.
        """
        def decorator(callable):
            self._node_default = callable
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
            self._impl = callable
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
        claimed = [(w, node) for w, node in self._children.items()
                   if node._impl is not None
                   and any(node._impl is m for m in members)]
        for w, node in claimed:
            # ownership is keyed by the CALLABLE, never the bare
            # word: two classes may each expose `run`, and each
            # method must bind to its own class's instance
            self._method_owner[id(node._impl)] = key
        if global_:
            # the class IS the app: its set is the top-level set;
            # the claimed commands stay top-level, now bound
            self._impl = cls
        else:
            # a command: parent of its own little set--reparent
            # the claimed nodes under it
            for w, node in claimed:
                del self._children[w]
            parent_node = self._child(word)
            parent_node._impl = cls
            if repeat:
                parent_node._node_repeat = True
            for w, node in claimed:
                node.parent = parent_node
                parent_node._children[w] = node
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
                    name: self._build(fn, name=name,
                                method_of=self._method_owner.get(id(fn)))
                    for name, fn in entries},
                'repeat': self._sub_repeat.get(parent, False),
            }
        # the real help/version commands ride the table; no
        # legacy synthesis (banishment must banish)
        return complete_set(self.plans, self.global_plan, words, prefix,
                            auto_version=False,
                            repeat=self.repeat, sets=sets or None,
                            help=False)

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
                                        False, auto_version=False)
            from .runtime import help_margin, resolve_theme
            text = render_help_page(
                command_set_usage(self._prog(), self._display_global()),
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
            self.global_plan, entries, False, auto_version=False)
        pages = [(word,
                  self.plan_for(word).usage(f'{prog} {word}'),
                  merge_docs(self.plan_for(word)))
                 for word in table]
        return man_page(prog, corpus,
                        command_set_usage(prog, self._display_global()),
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
        self._finalize()
        return read_mapping(callable, mapping)

    def read_iterable(self, callable, iterable):
        "v1's API: call `callable` once per row; returns the results."
        self._finalize()
        return read_iterable(callable, iterable)

    def read_csv(self, callable, reader, *, first_row_map=None):
        "v1's API: read_iterable for csv.reader input (see read_csv)."
        self._finalize()
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

    def app_class(self):
        """
        v1's class-based-commands API, as a compatibility layer
        over class-as-app: returns (app_class, command_method).
        Decorate the class with @app_class() and its methods with
        @command_method(); the class's __init__ is the global
        command, Appeal constructs the instance, and the methods
        late-bind to it--v1's documented contract, new machinery.
        """
        def app_class_decorator():
            def decorator(cls):
                self._adopt_class(cls, global_=True)
                return cls
            return decorator
        def command_method(name=None):
            # a method registers exactly like @app.command() in a
            # class body; adoption claims it when the class runs
            # through @app_class()
            return self.command(name)
        return app_class_decorator, command_method

    # ---- first use: build and compile, one command at a time ----
    #
    # Laziness extends *per command*: dispatching (or examining)
    # one command never builds the others.  A config error in
    # command B surfaces when B is first used, not before.

    def _table(self):
        "The {command word: callable} table.  Cheap: no inspection."
        self._finalize()
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

    def _build(self, callable, **kwargs):
        """
        build() a top plan and stamp it with the app's operand
        usage format (positional_argument_usage_format).  Every
        top plan the app renders funnels through here; child plans
        read the format off their root at render time.
        """
        policy = self.root.default_options
        if policy is None:
            bound = None
        else:
            # the public signature is (app, name, annotation,
            # default); build()'s internal contract stays 3-arg
            root = self.root
            def bound(name, annotation, default):
                return policy(root, name, annotation, default)
        plan = build(callable, default_options=bound, **kwargs)
        plan.arg_format = self.positional_argument_usage_format
        plan.auto_help = self._help_enabled
        return plan

    def _node_for(self, word):
        """
        The tree node a bare word means: a direct child, or the
        UNIQUE descendant with that word.  Ambiguous bare words
        refuse by name (path addressing--walk .commands--is the
        unambiguous spelling; dispatch itself resolves per-parent,
        deepest set first, and never comes through here).
        """
        node = self._children.get(word)
        if node is not None:
            return node
        matches = []
        def walk(parent):
            for w, child in parent._children.items():
                if w == word:
                    matches.append((parent, child))
                walk(child)
        walk(self)
        if len(matches) > 1:
            parents = ', '.join(sorted(repr(p.name or '(root)')
                                       for p, _ in matches))
            raise AppealConfigurationError(
                f"plan_for({word!r}): ambiguous--commands "
                f"named {word!r} exist under {parents}")
        if matches:
            return matches[0][1]
        return None

    def _plan_for_node(self, node, word):
        "The node's Plan, cached by NODE (words can repeat)."
        with self._lock:
            plan = (self._plans or {}).get(id(node))
        if plan is None:
            callable = node._command_callable()
            if callable is None:
                raise AppealConfigurationError(
                    f"no command named {word!r}")
            owner = self._method_owner.get(id(callable))
            if owner is None:
                _refuse_orphan_method(callable)
            plan = self._build(callable, name=word, method_of=owner)
            plan.prog = self.root._prog()
            with self._lock:
                if self._plans is None:
                    self._plans = {}
                plan = self._plans.setdefault(id(node), plan)
        return plan

    def plan_for(self, word):
        "The named command's Plan, built at first request."
        self._finalize()
        node = self._node_for(word)
        if node is None:
            raise AppealConfigurationError(f"no command named {word!r}")
        return self._plan_for_node(node, word)

    def _parse_for(self, word):
        node = self._node_for(word)
        if node is None:
            raise AppealConfigurationError(f"no command named {word!r}")
        with self._lock:
            parse = (self._parses or {}).get(id(node))
        if parse is None:
            if node._children:
                # a parent with subcommands is a nested command set:
                # the parent is its global command, so parent options
                # come before the subcommand word and the parent runs
                # first--all machinery reused.  Addressed by NODE
                # (deepest set wins at dispatch; words can repeat).
                sub_plans = {w: self._plan_for_node(c, w)
                             for w, c in node._children.items()
                             if c._command_callable() is not None}
                default_fn = node._node_default
                parse = compile_command_set(
                    sub_plans, self._plan_for_node(node, word),
                    prog=word,
                    templates=self.templates, theme=self.theme,
                    max_columns=self.margin, help=self._help_enabled,
                    default=(self._build(default_fn)
                             if default_fn is not None else None))
            else:
                parse = compile_plan(self._plan_for_node(node, word),
                                     templates=self.templates,
                                     theme=self.theme,
                                     max_columns=self.margin)
            with self._lock:
                if self._parses is None:
                    self._parses = {}
                parse = self._parses.setdefault(id(node), parse)
        return parse

    def _prog(self):
        return self.name or _os.path.basename(self.script) or 'program'

    def _set_entry_for(self, word, node=None):
        """
        The nested set dict behind a parent command: the parent
        compiled with the flexible boundary (a global command of
        its own little set), each subcommand a (scan, run) pair--
        or, recursively, another set dict.  Addressed by NODE
        (Larry's ruling, 2026-07-19: depth takes precedence, and
        words may repeat at different depths--`A X X` runs X's
        own subcommand X).
        """
        if node is None:
            node = self._children.get(word)
        with self._lock:
            entry = (self._set_entries or {}).get(id(node))
        if entry is not None:
            return entry
        from .plan import command_set_usage
        from .help import summary, command_set_corpus
        from .runtime import render_command_listing
        parent_plan = self._plan_for_node(node, word)
        parent = compile_plan(parent_plan, templates=self.templates,
                              theme=self.theme, boundary='flexible',
                              max_columns=self.margin)
        subs = {}
        listed = []
        for w, child in node._children.items():
            fn = child._command_callable()
            if fn is None:
                continue
            listed.append((w, summary(fn)))
            if child._children:
                subs[w] = self._set_entry_for(w, child)
                continue
            owner = self._method_owner.get(id(fn))
            if owner is None:
                _refuse_orphan_method(fn)
            sub = compile_plan(self._plan_for_node(child, w),
                               templates=self.templates, theme=self.theme,
                               max_columns=self.margin)
            subs[w] = (sub.scan, sub.run)
        entries = listed
        corpus = command_set_corpus(parent_plan, entries, False)
        sub_usage = render_command_listing(
            command_set_usage(word, parent_plan), corpus,
            self.templates, margin=self.margin)
        default_fn = node._node_default
        if default_fn is not None:
            compiled = compile_plan(self._build(default_fn),
                                    templates=self.templates,
                                    theme=self.theme,
                                    max_columns=self.margin)
            sub_default = (compiled.scan, compiled.run)
        else:
            sub_default = None
        entry = {'scan': parent.scan, 'run': parent.run,
                 'commands': subs,
                 'repeat': node._node_repeat,
                 'words': frozenset(subs),
                 'usage': sub_usage,
                 'default': sub_default}
        with self._lock:
            if self._set_entries is None:
                self._set_entries = {}
            entry = self._set_entries.setdefault(id(node), entry)
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
        command_words = frozenset(table)
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
        corpus = command_set_corpus(
            global_plan, entries, False, auto_version=False)
        usage = render_command_listing(
            command_set_usage(self._prog(), self._display_global()),
            corpus, self.templates, margin=self.margin)

        commands = _CompileOnDispatch(self, usage)
        auto_help = self._help_enabled and 'help' not in table

        default = (compile_plan(self._build(self._default), templates=self.templates,
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


    def _display_global(self):
        """
        The global plan for DISPLAY (usage lines, corpora): None
        when the precommand merely hosts the slot--v1 never
        advertised auto options in usage, and the corpus goldens
        pin those lines.
        """
        plan = self.global_plan
        if plan is not None and getattr(plan.callable,
                                        'appeal_precommand', False):
            return None
        return plan

    def _precommand_plan(self):
        """
        The precommand's mini plan, built from what
        default_mappings mapped to it.  None when nothing is.
        The closure is marked stock when the app doesn't override
        the precommand family, so standalone scripts can bake the
        behavior as literals (an override refuses by name--the
        north star's teeth).
        """
        mapped = self.root._precommand_options
        if not mapped:
            return None
        app = self.root
        want_v = 'version' in mapped
        want_h = 'help' in mapped
        if want_v and want_h:
            def precommand(*, version=False, help=None):
                app.precommand(version=version, help=help)
        elif want_v:
            def precommand(*, version=False):
                app.precommand(version=version)
        else:
            def precommand(*, help=None):
                app.precommand(help=help)
        if want_v:
            add_option_override(precommand, 'version',
                                mapped['version'], default=False)
        if want_h:
            add_option_override(precommand, 'help', mapped['help'],
                                annotation=_help_topic, default=None)
        cls = type(app)
        precommand.appeal_help = app.default_help
        precommand.appeal_precommand = True
        precommand.appeal_stock = (
            cls.precommand is Appeal.precommand
            and cls.default_version is Appeal.default_version
            and cls.default_help is Appeal.default_help)
        precommand.appeal_version = (str(app.version)
                                     if app.version is not None else None)
        return self._build(precommand, name=self.root._prog())

    @property
    def global_plan(self):
        self._finalize()
        with self._lock:
            plan = self._global_plan
        if plan is None:
            pre = self._precommand_plan() if self.parent is None else None
            if self._global is not None:
                plan = self._build(self._global)
                plan.pre_plan = pre
            elif pre is not None:
                # no global command: the precommand IS the global
                # plan--its options scan the pre-word segment and
                # it runs (a no-op when nothing was given) first
                plan = pre
            if plan is not None:
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
        """
        Parse-and-execute with polite error handling, then EXIT
        the process with the result--0.6.4's contract, restored
        2026-07-19 (Larry's ruling, review item J1): a script
        whose last line is bare `app.main()` reports its exit
        code to the shell.  Usage errors exit 2 (the getopt/
        argparse convention); a command's nonzero int return is
        the exit code; success exits 0.  Want the code returned
        instead?  That's process().
        """
        import os as _os
        if (args is None and '_APPEAL_COMPLETE' in _os.environ
                and not _sys.argv[1:]):
            # a shell-completion reentry: bare args, mode in the
            # environment.  Answer it instead of parsing.
            from .runtime import completion_reentry
            _sys.exit(completion_reentry(
                lambda words, prefix: self.complete(words, prefix),
                self._prog()))
        parse = self._compile()
        if config is not None:
            fused = parse
            def parse(args, _config=config):
                processor = Processor(self)
                processor.parse(list(args), _config)
                return processor.execute()
        _sys.exit(run_main(parse, args, theme=self.theme,
                           errors=self.errors))

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
            return self._build(getattr(instance, plan.name), name=plan.name)
        return self._build(plan.callable.__get__(instance), name=plan.name)

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
        self._finalize()
        table = self._table()
        # the stock help/version commands are bound methods of the
        # app--unimportable by a standalone script.  The generated
        # parse_help/parse_version machinery IS their standalone
        # rendering, so those words step aside here and the
        # emitter's auto commands cover them.
        cls = Appeal
        defaults = {w for w, fn in table.items()
                    if getattr(fn, '__func__', None) in
                    (cls.default_help, cls.default_version)}
        if set(table) - defaults:
            def sub_plan(name, fn):
                # nested parents are fine: self._subs is flat
                # (every parent maps its own children), and the
                # emitter reassembles the tree, deepest first.
                # _build stamps the app's arg_format, help, and
                # option policy so nested commands match the root.
                return self._build(fn, name=name,
                             method_of=self._method_owner.get(id(fn)))
            subs = {parent: {name: sub_plan(name, fn)
                             for name, fn in entries}
                    for parent, entries in self._subs.items()}
            return emit_standalone_command_set(
                {w: self.plan_for(w) for w in table
                 if w not in defaults},
                self.global_plan,
                argv0=argv0 or self._prog(),
                templates=self.templates, theme=self.theme,
                repeat=self.repeat, subs=subs or None,
                sub_repeat=dict(self._sub_repeat) or None,
                errors=self.errors, version=self.version,
                max_columns=self.margin, help=self._help_enabled,
                default=(self._build(self._default)
                         if self._default is not None else None),
                sub_defaults={w: self._build(fn)
                              for w, fn in self._sub_defaults.items()}
                             or None)
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
