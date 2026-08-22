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
# (see codegen.emit_standalone_module).

"""
Appeal: give Appeal your function's signature, get a command-line
interface--in process, or as a generated standalone script.
"""

__version__ = '1.0'

# build / codegen / interpreter / render are imported LAZILY (see the
# module __getattr__ below and the local imports in the methods that
# use them): `import appeal` pulls in only the stdlib-only runtime
# core, so a precompiled program's `import appeal` is near bare-Python
# speed; big and inspect load only when you actually build, compile,
# or render (Larry's ruling 2026-08-16).
from .plan import Terminal, NO_DEFAULT, OptionRule, Plan, Slot
from .plan import _validate_arg_format
from .runtime import (
    AppealConfigurationError, AppealDataError, AppealError,
    CommandError, MultiOption, Option, StrictOption,
    UsageError, accumulator, counter, file, mapping, optional,
    run_main, split,
    validate, validate_range,
    )

# theming (themes are DATA--resolve_stylesheet composes them) and
# the build/codegen surface are re-exported LAZILY via __getattr__
# below, so accessing appeal.appeal_theme / appeal.build / etc. still
# works but doesn't cost anything until you touch it.

# every exception, both spellings (the prefixed forms are the
# real names--they're what tracebacks show, v1's rendering kept)
from .runtime import (
    AppealBaseException, AppealCommandError, AppealUsageError,
    ConfigurationError, DataError,
    )
from .runtime import Command as _Command


import os as _os
import sys as _sys


# LAZY RE-EXPORTS: appeal.build / appeal.compile_plan / appeal.appeal_theme
# / ... still work, but import their (heavy) home module only on first
# access, so plain `import appeal` stays stdlib-only.  (Internal uses
# take a local import at the call site.)
_LAZY_REEXPORTS = {
    'Decorations': 'build', 'build_plan': 'build',
    'default_options': 'build', 'default_long_option': 'build',
    'default_short_option': 'build',
    'strip_first_argument_from_signature': 'build',
    'strip_self_from_signature': 'build',
    'compile_command_set': 'codegen', 'compile_plan': 'codegen',
    'emit': 'codegen', 'emit_command_set': 'codegen',
    'appeal_markdown_defaults': 'render', 'appeal_theme': 'render',
    'uncolored_theme': 'render', 'plain_theme': 'render',
    'dark_cool_theme': 'render', 'dark_warm_theme': 'render',
    'light_cool_theme': 'render', 'light_warm_theme': 'render',
    'resolve_stylesheet': 'render', 'help_stylesheet': 'render',
    'completions': 'complete', 'completions_set': 'complete',
    'read_csv': 'read', 'read_iterable': 'read', 'read_mapping': 'read',
    'describe': 'schema', 'describe_set': 'schema',
    'interpreter_dispatch': ('interpreter', 'dispatch'),
    'interpreter_parse': ('interpreter', 'parse'),
}


def __getattr__(name):
    spec = _LAZY_REEXPORTS.get(name)
    if spec is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}")
    import importlib
    modname, attr = spec if isinstance(spec, tuple) else (spec, name)
    value = getattr(importlib.import_module('.' + modname, __name__), attr)
    globals()[name] = value    # cache: future access is a plain global
    return value


# "not supplied" sentinel for Appeal(default_options=...)--distinct
# from an explicit None (which means "no default options at all"),
# and lets the signature default stay lazy (no build import at load)
_DEFAULT_OPTIONS = object()

# @app.option(default=...) "not supplied" sentinel--stands in for
# inspect.Parameter.empty in the SIGNATURE default, so `import appeal`
# needn't import inspect (~7ms); option() converts it back at call
# time (build's convention is inspect.Parameter.empty).
_UNSET = object()


class _LazyInspect:
    "inspect, imported on first attribute access--keeps it off `import appeal`."
    def __getattr__(self, name):
        import inspect
        globals()['_inspect'] = inspect     # replace the proxy: real from now on
        return getattr(inspect, name)


_inspect = _LazyInspect()

# featherweight stand-ins for the fast path: cheapsig (microsecond signature)
# and MethodType (types is always already loaded), so registration + dispatch
# never trip the lazy real-inspect proxy.  getdoc etc. stay on _inspect (help).
from . import cheapsig as _cheapsig
from types import MethodType as _MethodType


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
        if kind == 'fold' and getattr(rule.converters[0],
                                      '__appeal_mapping__', False):
            # the mapping MultiOption (dict[K, V]'s mechanism): config
            # gives a whole dict; each pair becomes one KEY=VALUE
            # occurrence, exactly as the command line spells it
            if not isinstance(value, dict):
                raise AppealDataError(
                    f"config: {name!r} collects KEY=VALUE pairs; "
                    f"give it a mapping", usage)
            given[key] = [(f'{k}={v}',) for k, v in value.items()]
            injected[name] = key
            return
        if kind == 'fold':
            if not isinstance(value, (list, tuple)):
                raise AppealDataError(
                    f"config: {name!r} repeats; give it a sequence "
                    f"(one entry per occurrence)", usage)
            given[key] = ([tuple(v) if isinstance(v, (list, tuple))
                           else (v,) for v in value]
                          if kind == 'fold' else list(value))
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
        # a value option's `given` entry is the list of occurrences
        # (last wins, all validated); config supplies one occurrence
        if kind == 'value' and len(rule.converters) > 1:
            if not isinstance(value, (list, tuple)):
                raise AppealDataError(
                    f"config: {name!r} takes "
                    f"{len(rule.converters) - 1} values; give it a "
                    f"sequence", usage)
            given[key] = [tuple(value)]
            injected[name] = key
            return
        given[key] = [value]
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
        parameters = list(_cheapsig.signature(callable).parameters)
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
        for word, run, operands, handoff, positions in self.invocations:
            name = '(global)' if word is None else word
            text = f'{name} {" ".join(operands)}'.rstrip()
            # the handoff is either the mature `given` dict or the
            # eager IR (a token list); show the option keys either way
            if isinstance(handoff, dict):
                keys = sorted(handoff)
            else:
                keys = sorted({t[0] for t in handoff if t and t[0]})
            if keys:
                text += ' [' + ' '.join(keys) + ']'
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
            _, cmd = app._pieces
            operands, given, rest, positions = cmd.scan(argv)
            self.invocations = [(None, cmd, operands, given, positions)]
            self._tail = None
            return self
        (_, parse_globals, commands, usage, default, auto_help,
         repeat, words) = app._pieces
        from .runtime import scan_command_set
        self.invocations, self._tail = scan_command_set(
            argv, parse_globals, commands, usage, default, repeat, words)
        return self

    def _print_listing(self):
        "The set listing: baked pieces, rendered at the real margin."
        from .render import help_margin, render_baked_help
        app = self.app
        print(render_baked_help(app._pieces[3],
                                margin=help_margin(app.margin)),
              end='')

    def execute(self):
        "Stage 2: conversions and commands, left to right."
        if self.invocations is None:
            raise AppealConfigurationError(
                "this Processor hasn't parsed anything yet")
        app = self.app
        app._last_processor = self
        if self._tail == ('bare',):
            # an empty command line: the listing, stdout, exit 1
            # (ruled 2026-07-09, git-style); nothing runs.  The
            # listing is baked pieces, finished at print time
            # (errors and orientation ride the pipeline, ruled
            # 2026-08-06)
            self._print_listing()
            self.result = 1
            return 1
        result = None
        env = {}    # class-based commands: instances live here
        for word, cmd, operands, given, positions in self.invocations:
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
                result = cmd.run(operands, given, positions, env, cmd)
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
            # every invocation that ran is logged, precommand eras included
            # (they're commands that run first, not a special case); the
            # instance is this invocation's own class-as-app object, if any
            instance = result if _is_class_command(cmd.callable) else None
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
                self._print_listing()
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
                d = app._pieces[4]
                operands, given, rest, positions = d.scan([])
                result = d.run(operands, given, positions, {}, d)
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
        fn = node._command_callable() if node is not None else None
        if hasattr(parse, 'scan'):
            # two-stage dispatch (parse-before-execute): the shared
            # Command record--run() calls command.callable
            return _Command(word, callable=fn, scan=parse.scan,
                            run=parse.run)
        # fused: _parse_for compiles every registered word two-stage
        # and nested parents are intercepted above; belt and braces
        return _Command(word, fused=parse)   # pragma: no cover


# the default_mappings menu, importable (spell your subset with
# these: default_mappings(*default_mappings_help))
default_mappings_help = ('-h', '--help', 'help')
default_mappings_version = ('-V', '--version', 'version')


def default_mappings(*options):
    """
    The FACTORY for the stock program-level defaults policy
    (Larry's design, 2026-07-25).  List the mappings you want:
    '-h', '--help', '-V', '--version' (precommand options),
    'help', 'version' (commands); empty means all of them.
    Returns the policy callable--the constructor default is
    default_mappings=default_mappings().  Pass
    default_mappings=None for no default mappings at all.

    Order is insignificant (the listing keeps v1's order, version
    before help).  Version mappings apply only when the app has a
    version string.  Each mapping lands only if not already
    mapped--user declarations always win.
    """
    if not options:
        options = default_mappings_help + default_mappings_version
    valid = set(default_mappings_help + default_mappings_version)
    for o in options:
        if o in valid:
            continue
        if isinstance(o, str):
            near = {'-v': '-V', '--h': '--help', '-help': '--help',
                    '-version': '--version'}.get(o)
            hint = f" (did you mean {near!r}?)" if near else ''
            raise AppealConfigurationError(
                f"default_mappings: unknown mapping {o!r}{hint}; "
                f"the menu is {sorted(valid)}")
        raise AppealConfigurationError(
            f"default_mappings: {o!r} isn't a mapping name.  "
            f"default_mappings is a factory--pass the constructor "
            f"default_mappings=default_mappings(), not the "
            f"factory itself")
    requested = frozenset(options)

    def default_mappings_policy(app):
        # snapshot FIRST: the version/help COMMANDS only make
        # sense for a program that has commands (v1's rule; an
        # ls-style global-only program gets the options, never
        # command words)
        has_commands = bool(app.commands)
        if app.version is not None:
            if ('version' in requested and has_commands
                    and 'version' not in app.commands):
                app.command('version')(app.print_version)
            free = [s for s in ('-V', '--version')
                    if s in requested and s not in app.options]
            if free:
                app.option('version', *free)(app.help_and_version_precommand)
        if has_commands:
            if 'help' in requested and 'help' not in app.commands:
                app.command('help')(app.help)
                # help()'s usage=/summary=/doc= knobs are API,
                # not command-line surface: the zero-string
                # option() is the explicit unmap (ruled
                # 2026-08-05)
                app.option('usage')(app.help)
                app.option('summary')(app.help)
                app.option('doc')(app.help)
        # the -h/--help OPTION rides for EVERY app, global-only included
        # (like --version above): a program with no commands still answers
        # -h/--help through its precommand.
        free = [s for s in ('-h', '--help')
                if s in requested and s not in app.options]
        if free:
            app.option('help', *free)(app.help_and_version_precommand)

    # _finalize reads this to drive the legacy help machinery
    # (per-command --help, bare-app -h) until the era unification
    # retires it: the requested tokens are the truth
    default_mappings_policy.appeal_requested = requested
    return default_mappings_policy


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
                 stylesheet=None, version=None, repeat=False,
                 errors=None, script=_sys.argv[0],
                 margin=79,
                 positional_argument_usage_format='<{name.upper()}>',
                 default_options=_DEFAULT_OPTIONS,
                 default_mappings=default_mappings(), doc=None):
        from .build import Decorations
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
        self._precommands = []    # ordered precommand eras (the head; _impl
                                  # tracks the primary until dispatch runs them all)
        self._auto_impl = None    # synthesized fn for a pure dispatcher
        self._node_default = None # this node's default command
        self._node_repeat = False # this node's set cycles
        if parent is not None:
            # a subcommand node is a full Appeal; the program
            # knobs are the root's, copied as processed values
            for attr in ('_help_enabled', 'default_options',
                         'default_mappings',
                         'positional_argument_usage_format',
                         'script', 'errors', 'repeat', 'stylesheet',
                         'margin', '_templates'):    # the BACKING field, not the
                setattr(self, attr, getattr(parent, attr))  # `templates` property
                                                    # -- copying the property would
                                                    # force render's lazy import
            self.version = None
            self._finalized = True      # the ROOT runs the pass
            self._precommand_options = {}
            self._decorations = parent.root._decorations
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
        # the v1 help= knob is dead (ruled 2026-07-25):
        # default_mappings is the policy switch.  The legacy
        # bare-app help machinery still keys off this flag;
        # approximate it until the era unification lands
        self._help_enabled = default_mappings is not None
        # the option-string policy (v1's knob, restored): a callable
        # (name, annotation, default) -> list of option strings, run
        # at build time on every automatically-mapped keyword-only
        # parameter.  The stock policy adds a long and a short;
        # default_long_option drops the short, default_short_option
        # drops the long, or supply your own.  Its output--the
        # strings--is baked into the compiled parser, so a custom
        # policy never needs to ride into a standalone script.
        if default_options is _DEFAULT_OPTIONS:
            # not supplied -> the stock policy (lazy: importing build
            # is deferred until an Appeal is actually constructed)
            from .build import default_options as default_options
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
        # EVERYTHING @app.option/@app.parameter expressed, keyed
        # by the decorated callable (ruled 2026-08-09: Appeal
        # never modifies objects the user owns--decoration writes
        # it down HERE and moves on).  One registry per tree.
        self._decorations = Decorations()
        # @app.subcommand('path') declarations, written down at
        # decoration and resolved lazily (ruled 2026-08-10:
        # explicit parentage, registration order free)
        self._pending_subcommands = []
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
        # cycling is PER NODE (ruled 2026-08-22): `repeat` on a node means its
        # own set may cycle -- run more than one command from it.  The root's
        # set is the top-level commands; a command's set is its subcommands.
        # Not inherited: each node's repeat governs only its own set.  The root
        # seeds its _node_repeat from the program-level repeat= here.
        self.repeat = repeat
        self._node_repeat = repeat
        # None = auto (appeal_theme when the stream wants color),
        # False = never any color, or a complete composed
        # StyleSheet, used VERBATIM (ruled 2026-08-06); the
        # environment always wins (resolve_stylesheet's palette).
        self.stylesheet = stylesheet
        self.version = version
        # the program's documentation, tier 1 of the doc chain
        # (ruled 2026-08-01): doc= beats the global command's
        # docstring beats the shared module's docstring
        self.doc = doc
        # the help formatter's knob (v1's, wired 2026-07-09):
        # margin caps the wrap width (narrow terminals re-wrap
        # below it; pipes get the cap itself).  indent= died
        # unshipped with the Markdown pivot (ruled 2026-08-06):
        # big's renderer owns the definition-list layout
        if not isinstance(margin, int) or margin <= 0:
            raise AppealConfigurationError(
                f"margin must be a positive int, not {margin!r}")
        self.margin = margin
        # the help template: ONE string, six {sections}, its headings
        # Markdown, yours to replace.  Loaded lazily (it lives in render, which
        # pulls big/markdown) so a successful dispatch never imports render.
        self._templates = None
        # NO lock (ruled 2026-08-22): builds are idempotent and cache installs
        # are atomic (setdefault / attribute assignment), so racing first-parses
        # each build and one install wins -- see _init_caches.
        self._method_owner = {}   # id(callable) -> owning class's env key
        self._init_caches()

    @property
    def templates(self):
        "The help template; loaded from render lazily (off the fast path)."
        if self._templates is None:
            from .render import default_template
            self._templates = default_template
        return self._templates

    @templates.setter
    def templates(self, value):
        self._templates = value

    def _init_caches(self):
        # lock-free lazy caches (ruled 2026-08-22: no Lock).  Builds are
        # idempotent (same callable -> equivalent artifact), and dict.setdefault
        # / attribute assignment are atomic (GIL, and PEP 703 free-threaded), so
        # racing first-parses each build and one install wins -- the rest
        # harmlessly discard.  The dict caches are eager-{} so there's no
        # None-then-{} check-and-set to race.
        self._parse = None        # scalar: the single-command parse fn
        self._pieces = None       # scalar: what a Processor drives, paired w/parse
        self._set_entries = {}    # nested set dicts, built per parent node
        self._last_processor = None   # app.instances reads this
        self._plans = {}          # {id(node): Plan}, filled per word
        self._parses = {}         # {id(node): parse fn}, ditto
        self._global_plan = None  # scalar

    def _invalidate(self):
        # registration under a node changes every ancestor's
        # compiled artifacts (they embed the descendants); a
        # node's own descendants embed nothing of it, so down
        # the tree nothing staling
        node = self
        while node is not None:
            node._parse = None
            node._pieces = None
            node._set_entries = {}
            node._plans = {}
            node._parses = {}
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
        object: .callable is its function, .commands its
        subcommands, .options its option table, .default_callable
        its default command.
        """
        import types as _types
        return _types.MappingProxyType(self._children)

    @property
    def callable(self):
        """
        This node's command function (spelled like plan.callable
        one layer down; ruled 2026-07-25).  On the root, the
        global command; on a child, the function bound to its
        word.  None if never bound.
        """
        return self._impl

    @property
    def default_callable(self):
        "The default command's function (None if unset; ruled 2026-08-04)."
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
                    else self._plan())
        if plan is not None:
            for owner, o in all_options(plan):
                for s in o.strings:
                    table.setdefault(s, o)
        for param, strings in self.root._precommand_options.items():
            for s in strings:
                table.setdefault(s, None)
        return _types.MappingProxyType(table)

    def _plan(self):
        "This node's own Plan (the root: the global plan; ruled private 2026-08-04)."
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
            requested = getattr(root.default_mappings,
                                'appeal_requested', None)
            if requested is not None:
                # the stock factory says what was asked for
                root._help_enabled = bool(
                    requested & {'-h', '--help', 'help'})
            else:
                # a custom policy: judge by what it actually mapped
                root._help_enabled = bool(
                    root._precommand_options.get('help')
                    or 'help' in root._children)
        root._resolve_subcommands()
        root._derive_method_owners()

    def print_version(self):
        "Print the program's version."
        print(self.root.version)

    def _help_topic_page(self, topic, suppress=frozenset()):
        "help(topic)'s command-page path, split for readability."
        root = self.root
        table = root._table()
        if topic == 'help':
            print('Print usage documentation on a specific command.')
            return
        fn = table.get(topic)
        if getattr(fn, '__func__', None) is Appeal.print_version:
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
        # render the topic's page directly from plans (the one engine has no
        # baked-help compile step).  A topic that is itself a command SET shows
        # its subcommand listing (like `prog topic --help`); a leaf shows its
        # command page.
        node = root._node_for(topic)
        from .render import help_margin, render_help_page
        if node is not None and node._table():
            from .plan import command_set_usage
            from .help import summary as _summary, command_set_corpus
            node_table = node._table()
            entries = [(w, _summary(c)) for w, c in node_table.items()]
            # add the auto `help` row unless the set already registers one (the
            # codegen listing did this via auto_help; the bare-root path gets it
            # from the root's own table instead)
            auto_help = node._help_enabled and 'help' not in node_table
            corpus = command_set_corpus(
                node.global_plan, entries, auto_help, auto_version=False,
                doc=node._program_doc_override())
            text = render_help_page(
                command_set_usage(node._prog(), node._display_global()),
                corpus, node.templates, margin=help_margin(node.margin),
                file=_sys.stdout, stylesheet=node.stylesheet,
                suppress=suppress).rstrip('\n')
        else:
            from .help import merge_docs
            plan = root.plan_for(topic)
            text = render_help_page(
                plan.usage(), merge_docs(plan), root.templates,
                margin=help_margin(root.margin),
                file=_sys.stdout, stylesheet=root.stylesheet,
                suppress=suppress).rstrip('\n')
        print(text)

    def help_and_version_precommand(self, *, help: optional[str] = None,
                   version=False):
        """
        The stage ahead of the global command: program metadata.
        Its options live in the precommand+global era and unmap at
        the first command word.  Absent from the grammar entirely
        when default_mappings mapped nothing to it.  Map options
        onto it the ordinary way:
        app.option('help', '-h', '--help')(app.help_and_version_precommand).
        """
        if version:
            _sys.exit(self.print_version())
        if help is not None:
            _sys.exit(self.help(help))

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
        self.root._finalize()   # drain the subcommand ledger
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

    @staticmethod
    def _command_word(name, callable=None):
        """
        The command word for a registration: an explicit name verbatim, else
        the function name with underscores turned to dashes (upload_database
        -> upload-database, like git's format-patch/range-diff).  A command
        word can never start with a dash -- that's an option's shape -- so a
        leading dash, whether from name='--foo' or a function named _command
        (-> -command), is a configuration error.
        """
        word = name if name is not None else callable.__name__.replace('_', '-')
        if word.startswith('-'):
            raise AppealConfigurationError(
                f"a command name can't start with a dash: {word!r} "
                f"(commands are words, not options)")
        return word

    def command(self, name=None, *, repeat=False, parent=None):
        """
        @app.command() registers a command under the callable's name with
        underscores turned to dashes (upload_database -> upload-database).
        app.command('db')
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
        return self.subcommand(None, name, repeat=repeat)

    def default(self):
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
    default_command = default           # transitional alias for the old name

    def processor(self):
        "v1's API: an unparsed Processor; call it with an argv."
        return Processor(self)

    def precommand(self, *, index=-1):
        def decorator(callable):
            # a class here is class-as-app (§8.6): its __init__
            # is the global command's grammar; its methods
            # register themselves explicitly and membership
            # derivation binds them (ruled 2026-08-10).  precommand is
            # REPEATABLE (Larry, 2026-08-21): each call inserts an era into
            # the ordered list (index -1 = append, 0 = head); they run
            # front-to-back before the commands, each its own era.
            if index == -1:
                self._precommands.append(callable)
            else:
                self._precommands.insert(index, callable)
            self._impl = self._precommands[-1]
            self._invalidate()
            return callable
        return decorator
    global_command = precommand         # transitional alias for the old name

    def subcommand(self, parent, name=None, *, repeat=False):
        """
        Register a command under `parent`--a command word PATH
        string, root-relative: subcommand('db') for a child of
        db, subcommand('db migrate') for depth.  EXPLICIT by
        ruling (2026-08-10): Appeal never infers subcommand-ness;
        you say what the thing is a subcommand of, or it's a
        top-level command.  parent=None IS the top level--
        command() is exactly subcommand(None).

        The decoration writes down what was said and moves on;
        the path resolves at first use, so registration order is
        free (declare the child before the parent, fine).  A path
        nothing ever registers is a loud error naming it.  The
        returned decorator is REUSABLE--a tear-off:

            dbcmd = app.subcommand('db')
            @dbcmd
            def add(...): ...
            @dbcmd
            def remove(...): ...

        A method command may mount only at its class's own mount
        or under another method of the same class (the same-world
        rule, ruled 2026-08-10: commands are sentences about the
        object; once a path leaves the object's world it doesn't
        come back).
        """
        if parent is None:
            # the top level: the tree registration, eager
            # (nothing to resolve).  With a name, the node comes
            # back--decorator AND chaining handle, v1's shape.
            if name is not None:
                if not isinstance(name, str):
                    raise AppealConfigurationError(
                        f"command(): the command word must be a "
                        f"string, not {name!r}")
                node = self._child(self._command_word(name))
                if repeat and not node._node_repeat:
                    node._node_repeat = True
                    self._invalidate()
                return node
            def decorator(callable):
                node = self._child(self._command_word(None, callable))
                node._node_repeat = node._node_repeat or repeat
                return node(callable)
            return decorator
        if not isinstance(parent, str):
            raise AppealConfigurationError(
                f"subcommand: the parent is a command word path "
                f"(a string) or None, not {parent!r}")
        root = self.root
        def decorator(callable):
            if root._finalized:
                # late registration: the tree exists, attach now
                root._attach_subcommand(parent, name, repeat,
                                        callable)
            else:
                root._pending_subcommands.append(
                    (parent, name, repeat, callable))
            root._invalidate()
            return callable
        return decorator

    def _node_at_path(self, path):
        "The COMMAND node at a word path, or None while unresolved."
        node = self.root
        for word in path.split():
            child = node._children.get(word)
            if child is None or child._command_callable() is None:
                return None
            node = child
        return node

    def _attach_subcommand(self, parent, name, repeat, callable):
        node = self._node_at_path(parent)
        if node is None:
            raise AppealConfigurationError(
                f"subcommand: no command at path {parent!r} (for "
                f"{getattr(callable, '__name__', callable)!r})")
        child = node._child(self._command_word(name, callable))
        child._node_repeat = child._node_repeat or repeat
        child(callable)

    def _resolve_subcommands(self):
        """
        Drain the subcommand ledger to a fixpoint--a parent may
        itself arrive by subcommand--and refuse, naming paths,
        anything left unresolvable.
        """
        pending = self._pending_subcommands
        while pending:
            remaining = []
            progressed = False
            for item in pending:
                if self._node_at_path(item[0]) is None:
                    remaining.append(item)
                    continue
                self._attach_subcommand(*item)
                progressed = True
            if not progressed:
                paths = sorted({item[0] for item in remaining})
                raise AppealConfigurationError(
                    f"subcommand: no command was ever registered "
                    f"at path{'s' if len(paths) > 1 else ''} "
                    f"{', '.join(map(repr, paths))}")
            pending[:] = remaining

    def _derive_method_owners(self):
        """
        Membership derivation (ruled 2026-08-10): a registered
        command function found--by IDENTITY--in the __dict__ of a
        mounted class is that class's method command; self binds
        to the instance constructed at the class's mount.  Not
        signature-sniffing: two explicit declarations (the class
        mounted, the function registered) plus membership,
        deterministically combined.  Enforces SAME-WORLD (Larry's
        formulation, 2026-08-10): a method is either (a) a
        top-level command, its class being the GLOBAL command, or
        (b) a direct subcommand of its class's own mount, the
        class being a command (or subcommand) itself.  Nowhere
        else--methods don't hang off each other.
        """
        owners = self._method_owner
        classes = []                    # (cls, mount node)
        if self._impl is not None and _is_class_command(self._impl):
            classes.append((self._impl, self))
        def find(node):
            for child in node._children.values():
                impl = child._impl
                if impl is not None and _is_class_command(impl):
                    classes.append((impl, child))
                find(child)
        find(self)
        for cls, mount in classes:
            target = getattr(cls, '__wrapped__', cls)
            members = {id(m) for m in target.__dict__.values()}
            key = cls.__qualname__
            def claim(node):
                for child in node._children.values():
                    fn = child._impl
                    # class members too: a nested (or bound
                    # inner) class found in the parent's dict
                    # constructs through the parent instance's
                    # attribute--BIC composes without Appeal
                    # knowing
                    if fn is not None and id(fn) in members:
                        owners[id(fn)] = key
                        # class members too: a nested class
                        # constructs from its owner's instance,
                        # which exists only at the owner's mount
                        if child.parent is not mount:
                            where = (child.parent.name
                                     or '<the top level>')
                            place = ('the top level'
                                     if mount is self else
                                     f"{key!r}'s own mount")
                            raise AppealConfigurationError(
                                f"{fn.__name__!r} is a method of "
                                f"{key!r}, but it's mounted under "
                                f"{where!r}; a method mounts only "
                                f"at {place} (the same-world "
                                f"rule)")
                    claim(child)
            claim(self)

    def option(self, name, *options, annotation=None,
               default=_UNSET):
        """
        Additional decorator for @command functions: maps only the
        strings you specify for one keyword-only parameter,
        blowing away the default mappings (so naming just the long
        suppresses the auto short).  The option's grammar--
        converter, flag-ness--comes from the PARAMETER (ruled
        2026-07-25, arglet style): its annotation, else
        type(default), else str.  annotation=/default= override
        that when the option should genuinely differ from the
        parameter.  Stack several to accumulate strings; each call
        is its own rule.
        """
        # build's "not specified" marker is cheapsig.empty (the same singleton
        # build compares against); convert here at call time.
        from . import cheapsig
        if default is _UNSET:
            default = cheapsig.empty
        if annotation is None:
            annotation = cheapsig.empty
        def decorator(callable):
            if (isinstance(callable, _MethodType)
                    and isinstance(callable.__self__, Appeal)
                    and callable.__func__
                        is type(callable.__self__).help_and_version_precommand):
                # the bound precommand: Python mints a fresh bound
                # object per attribute access, so attribute-marking
                # can't stick--record in the app's own table
                if name not in ('version', 'help'):
                    raise AppealConfigurationError(
                        f"option: the precommand has no parameter "
                        f"{name!r} (only 'help' and 'version')")
                callable.__self__.root._precommand_options[name] = \
                    tuple(options)
                callable.__self__.root._invalidate()
                return callable
            if (isinstance(callable, _MethodType)
                    and isinstance(callable.__self__, Appeal)):
                # a bound app method registered as a command
                # (help's knobs, ruled 2026-08-05): bound methods
                # mint a fresh object per attribute access, but
                # they hash by (instance, function), so the
                # registry's key still finds them.  Cheap
                # validation off the code object (rule 2: no
                # inspect.signature at decoration time).
                code = callable.__func__.__code__
                named = code.co_argcount + code.co_kwonlyargcount
                if name not in code.co_varnames[1:named]:
                    raise AppealConfigurationError(
                        f"option: {callable.__func__.__name__} has "
                        f"no parameter {name!r}")
                root = callable.__self__.root
                root._decorations.add_option(
                    callable, name, options,
                    annotation=annotation, default=default)
                root._invalidate()
                return callable
            self.root._decorations.add_option(
                callable, name, options,
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
        from .complete import completions, completions_set
        table = self._table()
        if not table:
            return completions(self.plan, words, prefix)
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
        return completions_set(self.plans, self.global_plan, words, prefix,
                            auto_version=False,
                            repeat=self.repeat, sets=sets or None,
                            help=False)

    def help(self, topic='', *, usage=True, summary=True, doc=True):
        """
        Print usage documentation on a specific command.
        (That summary line doubles as the help command's listing
        row.)  Bare: the --help text (bare apps) or the command
        listing (sets), v1-style--also returned.  With a topic:
        that command's help page.  This method IS the help
        command (and -h/--help, via the precommand); subclass and
        override to customize every spelling at once.

        The knobs (Larry's design, 2026-08-05; v1's usage()
        folded in): usage=False suppresses the usage line,
        summary=False the summary line, doc=False the doc AND the
        arguments/options/commands sections--each with the
        template text before it.  help(summary=False, doc=False)
        is just the usage line.  As the help command the knobs
        stay API-only: default_mappings unmaps them (zero-string
        app.option()).
        """
        suppress = set()
        if not usage:
            suppress.add('usage')
        if not summary:
            suppress.add('summary')
        if not doc:
            suppress.update(('doc', 'arguments', 'options',
                             'commands'))
        suppress = frozenset(suppress)
        if topic:
            return self._help_topic_page(topic, suppress)
        table = self._table()
        if table:
            from .plan import command_set_usage
            from .help import summary, command_set_corpus
            from .render import render_help_page
            entries = [(w, summary(c)) for w, c in table.items()]
            corpus = command_set_corpus(
                self.global_plan, entries, False, auto_version=False,
                doc=self._program_doc_override())
            from .render import help_margin
            text = render_help_page(
                command_set_usage(self._prog(), self._display_global()),
                corpus, self.templates,
                margin=help_margin(self.margin),
                file=_sys.stdout, stylesheet=self.stylesheet,
                suppress=suppress).rstrip('\n')
        else:
            from .help import merge_docs, parse_docstring
            from .render import help_margin, render_help_page
            plan = self.plan
            corpus = merge_docs(plan)
            override = self.root.doc
            if override is not None:
                # tier 1 overrides a bare app's prose too; the
                # signature-bound sections stay with the command
                parsed = parse_docstring(override, '<program documentation>')
                corpus['summary'] = parsed['summary']
                corpus['documentation'] = parsed['documentation']
            text = render_help_page(
                plan.usage(), corpus, self.templates,
                margin=help_margin(self.margin),
                file=_sys.stdout, stylesheet=self.stylesheet,
                suppress=suppress).rstrip('\n')
        print(text)
        # returns None: help is a COMMAND implementation now
        # (ruled 2026-07-25), and a command's return value is its
        # exit status--text would sys.exit(text).  Capture stdout
        # for the text.

    def documentation(self, format):
        """
        The program's documentation rendered in the named format--
        the grammar describing itself in one more dialect, like
        completion(shell).  Formats (ruled 2026-08-05): 'gfm'
        (GitHub-flavored Markdown: definition lists as
        inline-HTML <dl>, everything else GitHub renders
        natively), 'commonmark' (pure CommonMark: definition
        lists as bold term + blockquote, strikethrough stripped,
        alerts as bold-labelled blockquotes), 'troff' (a man(1)
        page).  Unknown formats refuse by name.  Returns the
        text; where it goes is the caller's business--there is
        deliberately NO command-line switch for this: wire it up
        yourself if you want one.
        """
        if format in ('gfm', 'commonmark'):
            from .markdown import to_commonmark, to_github
            transform = (to_github if format == 'gfm'
                         else to_commonmark)
            prog = self._prog()
            table = self._table()
            doc = self._program_doc()
            if not table:
                return transform(doc or '')
            parts = [f'# {prog}']
            if doc:
                parts.append(doc)
            for word, fn in table.items():
                f = getattr(fn, '__func__', fn)
                if f in (Appeal.help, Appeal.print_version):
                    continue        # stock commands document
                                    # themselves in help, not READMEs
                parts.append(f'## {prog} {word}')
                d = _inspect.getdoc(fn)
                if d and d.strip():
                    parts.append(d)
            return transform('\n\n'.join(parts))
        if format != 'troff':
            raise AppealConfigurationError(
                f"documentation format {format!r} isn't supported "
                f"(only 'gfm', 'commonmark', and 'troff', for now)")
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
            self.global_plan, entries, False, auto_version=False,
            doc=self._program_doc_override(), listing=False)
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
        from .schema import describe, describe_set
        table = self._table()
        if not table:
            return describe(self.plan)
        return describe_set(self.plans, self.global_plan, self._prog())

    def read_mapping(self, callable, mapping):
        "v1's API: call `callable` with values pulled from `mapping`."
        from .read import read_mapping
        self._finalize()
        return read_mapping(callable, mapping)

    def read_iterable(self, callable, iterable):
        "v1's API: call `callable` once per row; returns the results."
        from .read import read_iterable
        self._finalize()
        return read_iterable(callable, iterable)

    def read_csv(self, callable, reader, *, first_row_map=None):
        "v1's API: read_iterable for csv.reader input (see read_csv)."
        from .read import read_csv
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

    def argument(self, parameter_name, *, usage):
        """
        Additional decorator for @command functions: renames one
        parameter in usage lines and help tables.  On an operand,
        the shown name; on an option, the metavar
        (`[-t|--times <COUNT>]`).  Reaches both, despite the name.
        """
        def decorator(callable):
            self.root._decorations.add_usage(callable,
                                             parameter_name, usage)
            self._invalidate()
            return callable
        return decorator

    parameter = argument    # the older spelling, kept as an alias

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
                self.precommand()(cls)
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
                "no commands: use @app.command() or @app.precommand()")
        return table

    def _build(self, callable, **kwargs):
        """
        build_plan() a top plan and stamp it with the app's operand
        usage format (positional_argument_usage_format).  Every
        top plan the app renders funnels through here; child plans
        read the format off their root at render time.
        """
        from .build import build_plan
        # the policy registers via the registrar-proxy's
        # app.option() (arglet style, Larry's design 2026-07-22);
        # build constructs the proxy around the real app
        plan = build_plan(callable,
                     default_options=self.root.default_options,
                     app=self.root,
                     decorations=self.root._decorations, **kwargs)
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
        plan = self._plans.get(id(node))
        if plan is None:
            callable = node._command_callable()
            if callable is None:
                raise AppealConfigurationError(
                    f"no command named {word!r}")
            owner = self._method_owner.get(id(callable))
            if owner is None:
                _refuse_orphan_method(callable)
            plan = self._build(callable, name=word, method_of=owner)
            plan.argv0 = self.root._prog()
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
        from .codegen import compile_command_set, compile_plan
        self.root._finalize()   # drain the subcommand ledger
        node = self._node_for(word)
        if node is None:
            raise AppealConfigurationError(f"no command named {word!r}")
        parse = self._parses.get(id(node))
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
                    templates=self.templates, stylesheet=self.stylesheet,
                    max_columns=self.margin, help=self._help_enabled,
                    default=(self._build(default_fn)
                             if default_fn is not None else None))
            else:
                parse = compile_plan(self._plan_for_node(node, word),
                                     templates=self.templates,
                                     stylesheet=self.stylesheet,
                                     max_columns=self.margin)
            parse = self._parses.setdefault(id(node), parse)
        return parse

    def _program_doc(self):
        """
        The program's documentation, three tiers (ruled
        2026-08-01), highest first: the doc= constructor
        argument; the global command's docstring; and--the
        pleasant magic--the module docstring, when every user
        command lives in one module.  Returns None when nobody
        has anything to say.
        """
        root = self.root
        if root.doc is not None:
            return root.doc
        if root._global is not None:
            d = _inspect.getdoc(root._global)
            if d and d.strip():
                return d
        modules = set()
        for word, node in root._children.items():
            fn = node._command_callable()
            if fn is None:
                continue
            f = getattr(fn, '__func__', fn)
            if f in (Appeal.help, Appeal.print_version):
                continue    # the stock commands live in appeal;
                            # they don't get a vote
            m = getattr(fn, '__module__', None)
            if m is None:
                return None
            modules.add(m)
        if len(modules) == 1:
            module = _sys.modules.get(modules.pop())
            d = getattr(module, '__doc__', None)
            if d and d.strip():
                import textwrap as _textwrap
                return _textwrap.dedent(d).strip('\n')
        return None

    def _program_doc_override(self):
        """
        Tiers 1 and 3 of the doc chain--the sources that
        OVERRIDE what merge_docs would read from the global
        command.  Tier 2 (the global docstring) returns None
        here: the existing merge path already honors it, with
        its fuller validation.
        """
        root = self.root
        if root.doc is not None:
            return root.doc
        if root._global is not None:
            d = _inspect.getdoc(root._global)
            if d and d.strip():
                return None         # tier 2: merge_docs' job
        return self._program_doc() if root.doc is None else root.doc

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
        from .codegen import compile_plan
        if node is None:
            node = self._children.get(word)
        entry = self._set_entries.get(id(node))
        if entry is not None:
            return entry
        from .plan import command_set_usage
        from .help import summary, command_set_corpus
        from .render import listing_pieces
        parent_plan = self._plan_for_node(node, word)
        parent = compile_plan(parent_plan, templates=self.templates,
                              stylesheet=self.stylesheet, boundary='flexible',
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
                               templates=self.templates, stylesheet=self.stylesheet,
                               max_columns=self.margin)
            subs[w] = _Command(w, callable=fn, scan=sub.scan, run=sub.run)
        entries = listed
        corpus = command_set_corpus(parent_plan, entries, False)
        sub_usage = listing_pieces(
            command_set_usage(word, parent_plan), corpus,
            self.templates)
        default_fn = node._node_default
        if default_fn is not None:
            compiled = compile_plan(self._build(default_fn),
                                    templates=self.templates,
                                    stylesheet=self.stylesheet,
                                    max_columns=self.margin)
            sub_default = _Command(callable=default_fn,
                                   scan=compiled.scan, run=compiled.run)
        else:
            sub_default = None
        entry = _Command(word, callable=node._command_callable(),
                         scan=parent.scan, run=parent.run,
                         subcommands=subs, words=frozenset(subs),
                         repeat=node._node_repeat, usage=sub_usage,
                         default=sub_default)
        entry = self._set_entries.setdefault(id(node), entry)
        return entry

    def _compile(self):
        from .codegen import compile_plan
        if self._parse is not None:
            return self._parse
        table = self._table()
        if not table:
            # a global command and nothing else: it owns the whole
            # line, options after operands and all
            fused = compile_plan(self.global_plan, templates=self.templates,
                                 stylesheet=self.stylesheet,
                                 max_columns=self.margin, is_global=True)
            def parse(argv):
                processor = Processor(self)
                processor.parse(argv)
                return processor.execute()
            parse.scan = fused.scan
            parse.run = fused.run
            parse.source = fused.source
            single = _Command(callable=self.global_plan.callable,
                              scan=fused.scan, run=fused.run)
            if self._parse is None:
                self._pieces = ('single', single)
                self._parse = parse
            return self._parse

        from .plan import command_set_usage
        from .runtime import run_command_set
        global_plan = self.global_plan
        command_words = frozenset(table)
        parse_globals = []              # an ordered list of head eras
        for era_plan in self.global_plans():
            fused = compile_plan(
                era_plan, templates=self.templates, stylesheet=self.stylesheet,
                max_columns=self.margin, is_global=True,
                command_split=(era_plan.minimum, era_plan.maximum,
                               command_words))
            parse_globals.append(_Command(callable=era_plan.callable,
                                          scan=fused.scan, run=fused.run))
        from .help import summary, command_set_corpus
        from .render import listing_pieces
        entries = [(word, summary(callable))
                   for word, callable in table.items()]
        corpus = command_set_corpus(
            global_plan, entries, False, auto_version=False,
            doc=self._program_doc_override())
        usage = listing_pieces(
            command_set_usage(self._prog(), self._display_global()),
            corpus, self.templates)

        commands = _CompileOnDispatch(self, usage)
        auto_help = self._help_enabled and 'help' not in table

        if self._default is not None:
            d_plan = self._build(self._default)
            d_fused = compile_plan(d_plan, templates=self.templates,
                                   stylesheet=self.stylesheet,
                                   max_columns=self.margin)
            default = _Command(callable=d_plan.callable,
                               scan=d_fused.scan, run=d_fused.run)
        else:
            default = None

        pieces = ('set', parse_globals, commands, usage, default,
                  auto_help, self.repeat, command_words)

        def parse(argv):
            processor = Processor(self)
            processor.parse(argv)
            return processor.execute()
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
        # empty strings = the explicit unmap (ruled
        # 2026-07-25): treated as not mapped at all
        want_v = bool(mapped.get('version'))
        want_h = bool(mapped.get('help'))
        # the closures mirror Appeal.precommand's signature:
        # optional[str] marks the topic's oparg optional (bare -h
        # gives ''), version=False is a flag
        if want_v and want_h:
            def precommand(*, help: optional[str] = None,
                           version=False):
                app.help_and_version_precommand(help=help, version=version)
        elif want_v:
            def precommand(*, version=False):
                app.help_and_version_precommand(version=version)
        else:
            def precommand(*, help: optional[str] = None):
                app.help_and_version_precommand(help=help)
        if want_v:
            app.root._decorations.add_option(precommand, 'version',
                                             mapped['version'])
        if want_h:
            app.root._decorations.add_option(precommand, 'help',
                                             mapped['help'])
        cls = type(app)
        precommand.appeal_help = app.help
        precommand.appeal_precommand = True
        precommand.appeal_stock = (
            cls.help_and_version_precommand is Appeal.help_and_version_precommand
            and cls.print_version is Appeal.print_version
            and cls.help is Appeal.help)
        precommand.appeal_version = (str(app.version)
                                     if app.version is not None else None)
        return self._build(precommand, name=self.root._prog())

    @property
    def global_plan(self):
        self._finalize()
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
                if self._global_plan is None:
                    self._global_plan = plan
                plan = self._global_plan
        return plan

    def global_plans(self):
        """
        The ordered head eras' plans (Larry's repeatable precommand, 2026-08-21):
        the help/version precommand at the head (when default_mappings mapped
        anything to it), then each precommand the user registered, front-to-back.
        Empty when there's no head at all.  scan_command_set scans them in order.
        """
        self._finalize()
        plans = []
        if self.parent is None:
            pre = self._precommand_plan()
            if pre is not None:
                plans.append(pre)
        for era in self._precommands:
            plans.append(self._build(era))
        return plans

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
        argv = _sys.argv[1:] if args is None else list(args)
        if config is not None:
            # config layering isn't ported to the Processor yet -- old path
            processor = Processor(self)
            processor.parse(argv, config)
            return processor.execute()
        return self._compiled_dispatch(argv)

    def _compiled_dispatch(self, argv):
        """
        The one path (Larry, 2026-08-21): compile the parser in memory (same as
        a precompiled module, minus the fingerprint check) and run the Processor.
        Dispatches the head eras then the command words -- recursing into a
        command's subcommand node when it has one -- and logs the instances the
        old two-stage execute() did (app.instances reads _last_processor).
        """
        holder = Processor(self)
        self._last_processor = holder
        result, _ = self._run_node(list(argv), 0, holder, top=True)
        holder.result = result
        return result

    def _run_node(self, argv, pos, holder, top, env=None):
        "Dispatch one set node's eras + command words; recurse for subcommands."
        if env is None:
            env = {}                                # class-as-app instance store
        from .compile import build_converters, _converter_key
        from . import runtime
        self._finalize()
        table = self._table()
        era_plans = self.global_plans()             # carries the global as a head era
        # laziness is per command (build_converters compiles independently): the
        # head eras always run, so build them now; each command word builds ITS
        # OWN converter only when dispatched -- a broken sibling costs nothing
        # until it's used.
        era_classes = build_converters(era_plans) if era_plans else {}
        precommands = [era_classes[_converter_key(p)] for p in era_plans]

        result = None
        for cls in precommands:                     # head eras, in order
            proc = runtime.Processor(argv[pos:], cls(), table)
            result = proc.run()
            if cls.constructs is not None:          # a global class-as-app: its
                env[cls.constructs] = result        # methods bind to this instance
            holder.instances.append(               # eras log (None, instance-or-None)
                (None, result if cls.constructs is not None else None))
            if runtime._halts(result):
                return result, pos
            pos += proc.consumed
        dispatched = False              # did a command word of THIS node run?
        while pos < len(argv):
            word = argv[pos]
            if word not in table:
                if not top:
                    return result, pos          # pop back: a parent may own it
                raise runtime._unexpected(word, table)
            c = table[word]
            owner = self._method_owner.get(id(c))
            if owner is None:                       # a self-method with no class
                _refuse_orphan_method(c)            # that claimed it: refuse by name
            plan = self._build(c, method_of=owner)  # method_of -> binds
            cls = build_converters([plan])[_converter_key(plan)]  # this cmd only
            pos += 1
            conv = cls()
            if cls.binds is not None:               # a method command: self is the
                conv.bound = env.get(cls.binds)     # instance a parent constructed
            proc = runtime.Processor(argv[pos:], conv, table)
            result = proc.run()
            if cls.constructs is not None:          # a class command: stash instance
                env[cls.constructs] = result
            instance = result if _is_class_command(c) else None
            holder.instances.append((holder._command_for(word), instance))
            dispatched = True
            if runtime._halts(result):
                return result, pos
            pos += proc.consumed
            # recurse into the command's subcommand node: it may dispatch a
            # subcommand OR (the line stops at the parent) run that node's
            # default command -- so recurse even at end-of-line when a default
            # is waiting.  Every command has a child node (lazy registration);
            # only enter one that actually has subcommands or a default.
            child = self._children.get(word)
            if child is not None and (child._commands
                                      or child._default is not None):
                result, pos = child._run_node(argv, pos, holder,
                                              top=False, env=env)
            if not self._node_repeat and pos < len(argv):
                # this set doesn't cycle: pop the leftover word up to an
                # ancestor whose set does (the parent's loop re-dispatches it);
                # at the top with nothing to claim it, it's unexpected
                if not top:
                    return result, pos
                tok = argv[pos]
                pool = proc.handlers if tok.startswith('-') else table
                raise runtime._unexpected(tok, pool)

        if not dispatched:
            # the line stopped at this node without naming a subcommand of it.
            # Run this node's default command; or, for a top-level set with no
            # default, print the listing for orientation and exit 1 (git-style,
            # ruled 2026-07-09).  A global command runs as a head era regardless
            # -- it processes pre-command options; it doesn't answer a bare line.
            if self._default is not None:
                d_plan = self._build(self._default)
                dcls = build_converters([d_plan])[_converter_key(d_plan)]
                dconv = dcls()
                if dcls.binds is not None:
                    dconv.bound = env.get(dcls.binds)
                dproc = runtime.Processor(argv[pos:], dconv, table)
                result = dproc.run()
                holder.instances.append((None, None))
                pos += dproc.consumed
            elif top and self._commands:
                self.help()                         # the set listing, to stdout
                result = 1
        return result, pos

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
        if config is not None:
            # config layering isn't ported to the Processor yet -- old path
            def parse(args, _config=config):
                processor = Processor(self)
                processor.parse(list(args), _config)
                return processor.execute()
        else:
            # the one engine (2026-08-22): main() drives the same in-memory
            # dispatch process() does.  A help/version precommand prints then
            # sys.exit()s; run_main catches that and converts it to a code.
            parse = lambda argv: self._compiled_dispatch(list(argv))
        _sys.exit(run_main(parse, args, stylesheet=self.stylesheet,
                           errors=self.errors, margin=self.margin))

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
        from .read import read_mapping
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

    def precompile(self, path=None, *, argv0=None):
        """
        The text of a compiled parser MODULE implementing this
        program: an importable file WEARING THE APPEAL API (ruled
        2026-08-09), so the program that imports it--

            try:
                import compiled as appeal
            except ImportError:
                import appeal

        --runs unchanged, decorators and all.  The module does
        `import appeal` for the runtime (the stdlib-only core; no
        inspect/build/render on the fast path), bakes its parser
        tables and fingerprints, and its Appeal() matches the live
        functions to the precompiled bits by fingerprint.  Any
        drift (an edited signature, docstring, decoration, or
        converter, on ANY registered function) raises a loud
        regenerate error at main().  path, if given, also writes
        the text there.  Necessarily eager: the module is a
        whole-program artifact, so every command is built and every
        ref rendered (refusals included--the north star's teeth
        bite here).
        """
        from .runtime import _stable_repr
        from .codegen import emit_precompiled_module
        self._finalize()
        table = self._table()
        # the stock help/version commands are covered by the
        # emitter's auto commands; those words step aside here
        cls = Appeal
        defaults = {w for w, fn in table.items()
                    if getattr(fn, '__func__', None) in
                    (cls.help, cls.print_version)}
        words = [w for w in table if w not in defaults]
        def sub_plan(name, fn):
            # nested parents are fine: self._subs is flat (every
            # parent maps its own children), and the emitter
            # reassembles the tree, deepest first.  argv0 matches
            # _plan_for_node's: error usage says `tool add <X>`
            plan = self._build(fn, name=name,
                               method_of=self._method_owner.get(id(fn)))
            plan.argv0 = self._prog()
            return plan
        subs = {parent: {name: sub_plan(name, fn)
                         for name, fn in entries}
                for parent, entries in self._subs.items()}
        # the baked knobs, each stored ONCE as its real value: the
        # shim compares them for staleness (repr'ing both sides at
        # compare time) AND replays them to reconstruct a real
        # Appeal for help/errors--no repr-string/value split
        config = {
            'name': self.name,
            'version': self.version,
            'repeat': self.repeat,
            'margin': self.margin,
            'positional_argument_usage_format':
                self.positional_argument_usage_format,
            'doc': self.doc,
            'templates': self.templates,
        }
        if words:
            text = emit_precompiled_module(
                {w: self.plan_for(w) for w in words},
                self.global_plan,
                argv0=argv0 or self._prog(),
                templates=self.templates, repeat=self.repeat,
                subs=subs or None,
                sub_repeat=dict(self._sub_repeat) or None,
                version=self.version, max_columns=self.margin,
                help=self._help_enabled,
                doc=self._program_doc_override(),
                default=(self._build(self._default)
                         if self._default is not None else None),
                sub_defaults={w: self._build(fn)
                              for w, fn in self._sub_defaults.items()}
                             or None,
                config=config, decorations=self._decorations,
                global_is_user=(self._impl is not None))
        else:
            text = emit_precompiled_module(
                {}, self.global_plan,
                argv0=argv0 or self._prog(),
                templates=self.templates,
                version=self.version, max_columns=self.margin,
                config=config, decorations=self._decorations,
                global_is_user=True)
        if path is not None:
            with open(path, 'wt', encoding='utf-8') as f:
                f.write(text)
        return text

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
        from .read import read_mapping
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
