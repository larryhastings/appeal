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
    emit_standalone, emit_standalone_command_set,
    )
from .interpreter import dispatch as interpreter_dispatch
from .interpreter import parse as interpreter_parse
from .plan import Terminal, NO_DEFAULT, OptionRule, Plan, Slot
from .complete import complete, complete_set
from .read import read_csv, read_iterable, read_mapping
from .schema import schema, schema_set
from .runtime import (
    AppealConfigurationError, AppealError, MultiOption, Option, UsageError,
    accumulator, counter, mapping, run_main, split, validate, validate_range,
    )

# v1's exception names, kept
AppealUsageError = UsageError
ConfigurationError = AppealConfigurationError


import inspect as _inspect
import os as _os
import sys as _sys


class _SubRegistrar:
    "The object app.command('db') returns: registers db's subcommands."
    def __init__(self, app, parent):
        self.app = app
        self.parent = parent

    def command(self):
        def decorator(callable):
            self.app._subs.setdefault(self.parent, []).append(
                (callable.__name__, callable))
            self.app._invalidate()
            return callable
        return decorator


class _Processor:
    "v1 compat: app.processor() returns a callable execution object."
    def __init__(self, app):
        self.app = app
        self.result = None

    def __call__(self, args):
        self.result = self.app.process(list(args))
        return self.result


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
        if word == 'help' and 'help' not in table:
            def parse_help(argv):
                # `help` alone: the listing; `help CMD`: CMD's --help
                if argv and argv[0] != 'help':
                    if argv[0] not in table:
                        raise UsageError(
                            f"unknown command {argv[0]!r}", self.usage)
                    return self.app._parse_for(argv[0])(['--help'])
                print('usage: ' + self.usage)
            return parse_help
        if word not in table:
            return None
        return self.app._parse_for(word)


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
    def __init__(self, name=None, *, version=None,
                 usage_max_columns=79, usage_indent_definitions=2):
        self.name = name
        self.version = version                          # future --version
        self.usage_max_columns = usage_max_columns      # v1 knobs, held for
        self.usage_indent_definitions = usage_indent_definitions  # the formatter
        # the help templates: a plain dict, yours to overwrite
        # entry by entry (see runtime.default_templates).
        from .runtime import default_templates
        self.templates = dict(default_templates)
        self._commands = []       # (name, callable), in declaration order
        self._subs = {}           # parent name -> [(name, callable)]
        self._default = None      # v1's default_command
        self._global = None
        self._parse = None
        self._plans = None        # {command word: Plan}, filled per word
        self._parses = None       # {command word: parse fn}, ditto
        self._global_plan = None

    def _invalidate(self):
        self._parse = None
        self._plans = None
        self._parses = None
        self._global_plan = None

    def command(self, parent=None):
        """
        @app.command() registers a command.  app.command('db')
        names an existing command and returns a registrar whose
        .command() attaches subcommands to it: the parent runs
        first (like a global command of its own little set), then
        the subcommand.
        """
        if parent is not None:
            return _SubRegistrar(self, parent)
        def decorator(callable):
            self._commands.append((callable.__name__, callable))
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
        "v1 compat: a callable execution object delegating to process()."
        return _Processor(self)

    def global_command(self):
        def decorator(callable):
            self._global = callable
            self._invalidate()
            return callable
        return decorator

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
        return complete_set(self.plans, self.global_plan, words, prefix)

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
                                        'help' not in table)
            text = render_help_page(
                command_set_usage(self._prog(), self.global_plan),
                corpus, self.templates).rstrip('\n')
        else:
            from .help import merge_docs
            from .runtime import render_help_page
            plan = self.plan
            corpus = merge_docs(plan)
            text = render_help_page(plan.usage(), corpus, self.templates).rstrip('\n')
        print(text)
        return text

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
        if self._plans is None:
            self._plans = {}
        plan = self._plans.get(word)
        if plan is None:
            callable = self._table().get(word)
            if callable is None:
                raise AppealConfigurationError(f"no command named {word!r}")
            plan = self._plans[word] = build(callable)
        return plan

    def _parse_for(self, word):
        if self._parses is None:
            self._parses = {}
        parse = self._parses.get(word)
        if parse is None:
            if word in self._subs:
                # a parent with subcommands is a nested command set:
                # the parent is its global command, so parent options
                # come before the subcommand word and the parent runs
                # first--all machinery reused
                sub_plans = {name: build(fn)
                             for name, fn in self._subs[word]}
                parse = compile_command_set(
                    sub_plans, self.plan_for(word), prog=word,
                    templates=self.templates)
            else:
                parse = compile_plan(self.plan_for(word), templates=self.templates)
            self._parses[word] = parse
        return parse

    def _prog(self):
        return self.name or _os.path.basename(_sys.argv[0]) or 'program'

    def _compile(self):
        if self._parse is not None:
            return self._parse
        table = self._table()
        if not table:
            # a global command and nothing else: it owns the whole
            # line, options after operands and all
            self._parse = compile_plan(self.global_plan, templates=self.templates)
            return self._parse

        from .plan import command_set_usage
        from .runtime import run_command_set
        global_plan = self.global_plan
        command_words = frozenset(table) | (
            {'help'} if 'help' not in table else set())
        if global_plan is not None:
            parse_globals = compile_plan(
                global_plan, templates=self.templates,
                command_split=(global_plan.minimum, global_plan.maximum,
                               command_words))
        else:
            parse_globals = None
        from .help import summary, command_set_corpus
        from .runtime import render_command_listing
        entries = [(word, summary(callable))
                   for word, callable in table.items()]
        auto = 'help' not in table
        corpus = command_set_corpus(global_plan, entries, auto)
        usage = render_command_listing(
            command_set_usage(self._prog(), global_plan),
            corpus, self.templates)

        commands = _CompileOnDispatch(self, usage)
        auto_help = 'help' not in table

        default = (compile_plan(build(self._default), templates=self.templates)
                   if self._default is not None else None)

        def parse(argv):
            if auto_help and argv and argv[0] in ('-h', '--help'):
                print('usage: ' + usage)
                return
            return run_command_set(argv, parse_globals, commands, usage,
                                   default=default)
        self._parse = parse
        return parse

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
        if self._global_plan is None and self._global is not None:
            self._global_plan = build(self._global)
        return self._global_plan

    def process(self, argv):
        "Parse argv and invoke the command; returns its return value."
        return self._compile()(list(argv))

    def main(self, argv=None):
        "Parse-and-execute with polite error handling; returns an exit code."
        return run_main(self._compile(), argv)

    def standalone(self, *, argv0=None):
        """
        The text of a standalone script implementing this program.
        Necessarily eager: the script is a whole-program artifact,
        so every command is built and every ref rendered (refusals
        included--the north star's teeth bite here).
        """
        if self._subs:
            raise AppealConfigurationError(
                "nested subcommands aren't in standalone emission yet")
        if self._table():
            return emit_standalone_command_set(
                self.plans, self.global_plan,
                argv0=argv0 or self._prog())
        return emit_standalone(self.global_plan, argv0=argv0)

    def write_standalone(self, path, *, argv0=None):
        "Write the standalone script to path."
        text = self.standalone(argv0=argv0)
        with open(path, 'wt', encoding='utf-8') as f:
            f.write(text)
        return path
