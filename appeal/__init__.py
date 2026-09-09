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
# is appeal.proposal.md.

"""
Appeal: give Appeal your function's signature, get a command-line
interface.
"""

__version__ = '1.0'

# build and render are imported LAZILY (see the module __getattr__ below
# and the local imports in the methods that use them): `import appeal`
# pulls in only the stdlib-only core, so it's near bare-Python speed;
# big and inspect load only when you actually build or render (Larry's
# ruling 2026-08-16).

# ============================================================
# The parse / convert / dispatch / execute core.  Everything
# needed to convert, dispatch, and execute a command line lives
# here; rendering, building, and completion stay lazy.
# ============================================================

import sys

# Appeal REQUIRES big (ruled 2026-08-06) for its help/usage rendering.
# NB: this core imports NOTHING from big--the parse/convert/dispatch
# core is stdlib-only, so plain `import appeal` is near bare-Python
# speed.  All big-backed rendering lives in appeal/render.py, imported
# lazily.


# an era's share (Larry's design, 2026-09-08): which neighboring eras
# recognize its options.  FORWARDS: the next era; BACKWARDS: the previous
# one; True: both; False: neither; PRECOMMAND: both, but only among the
# precommand eras--never across the first command word.  precommand()
# spells share=False as PRECOMMAND and share=True as True; command()
# spells share=True as FORWARDS.
FORWARDS = 'forwards'
BACKWARDS = 'backwards'
PRECOMMAND = 'precommand'


def no_subcommand():
    """
    A default subcommand that REQUIRES one: `error: no subcommand
    specified`, then the command's page with its subcommands listed.
    Subcommands are optional by default (the stock default_subcommand
    is None); Appeal(default_subcommand=no_subcommand) requires them
    program-wide, @app.command(default_subcommand=no_subcommand) for
    one command.
    """
    raise UsageError("no subcommand specified")


def _vet_default(callable, what):
    """
    A default command/subcommand handler must be callable with no
    arguments at all (Larry, 2026-09-09: a wrapper supplies any the
    real command needs); refused by name otherwise.  Never a string.
    """
    if not builtins_callable(callable):
        raise AppealConfigurationError(
            f"{what} must be a callable that takes no arguments, "
            f"not {callable!r}")
    from .frontend import signature, empty
    for p in signature(callable).parameters.values():
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD,
                      p.KEYWORD_ONLY) and p.default is empty:
            raise AppealConfigurationError(
                f"{what} must be callable with no arguments; "
                f"{getattr(callable, '__name__', callable)!r} requires "
                f"{p.name!r}")


builtins_callable = callable


def did_you_mean(word, candidates):
    """
    The suggestion tail for an unknown-name error: " (did you
    mean 'x'?)" when something in candidates is close, '' when
    nothing is.  difflib's gestalt matching--the stdlib's public
    answer (git hand-rolls Damerau-Levenshtein, clap uses
    Jaro-Winkler; on names this short they all agree).
    """
    import difflib
    matches = difflib.get_close_matches(word, list(candidates), n=2)
    if not matches:
        return ''
    if len(matches) == 1:
        return f" (did you mean {matches[0]!r}?)"
    return f" (did you mean {matches[0]!r} or {matches[1]!r}?)"


class AppealError(Exception):
    """
    The umbrella: every exception Appeal raises derives from it
    (v1's AppealBaseException role), so `except AppealError` means
    "anything Appeal raised".  It also has one job of its own:
    raise it FROM YOUR COMMAND for a runtime failure that should
    stop the program with a polite message but *without* usage
    (the command line was fine)--main() prints `error: ...` and
    exits 1.
    """


class ConfigurationError(AppealError):
    """
    Raised at *build* time: the program's signature doesn't make
    sense.  Always names the offender.  A bug, so main() lets it
    raise.
    """


class DataError(AppealError):
    """
    The data handed to the program is wrong--whatever its
    provenance: a config mapping, a mapping or CSV row being read,
    or (the most common data of all) the command line.  Carries
    the command's usage text when there is one to show.
    """
    def __init__(self, message, usage=None, param=None):
        super().__init__(message)
        # usage: the error's TRAILER--a callable usage(file) -> str that
        # renders what prints under the message (a `usage: <line>`, or a
        # base-help page) for that output stream, or None.  Attached at
        # the dispatch boundary (see _run_node); the catchers (run_main,
        # the REPL) call it with their error stream.
        self.usage = usage
        # the parameter/option name the error is ABOUT, when one
        # is knowable--structural provenance, so callers (the
        # config layer) can attribute errors by identity instead
        # of grepping the message
        self.param = param


class UsageError(DataError):
    """
    The command line specifically is wrong.  Printing it shows
    the message and the command's usage.
    """


class CommandError(AppealError):
    """
    Raise FROM YOUR COMMAND to fail with a message and a chosen
    exit code--parsing succeeded, the work itself went wrong.
    main() prints `error: message` (no usage: the command line
    was fine) and exits with .exit_code; process() lets it
    propagate.  (v1 documented exactly this contract for
    AppealCommandError but never wired the catch; restored and
    wired, ruled 2026-08-05.)
    """
    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


# the old prefixed names (0.6.4 called them exactly that--"old
# names"--and kept them as aliases; ruled again 2026-07-25:
# the unprefixed spellings are canonical, appeal.UsageError)
AppealUsageError = UsageError
AppealDataError = DataError
AppealConfigurationError = ConfigurationError
AppealCommandError = CommandError
AppealBaseException = AppealError


# the converter vocabulary (appeal/converters.py) -- the Option protocol,
# convert(), and the built-in factories.  Eager: it depends only on the
# exceptions above, so re-exporting here is cycle-free and gives the engine
# and the public API their names.
from .converters import (
    Option, MultiOption, is_option, is_multioption,
    split, validate, validate_range, counter, file, optional,
    accumulator, mapping,
    )


# v1's name for a repeatable Option--which is now every Option

# NOTE: StrictOption (an Option given AT MOST ONCE, else "specified more than
# once") was removed 2026-08-22 -- no demonstrable demand, no precedent in
# argparse/click (both last-wins by default).  To restore: re-add the
# `class StrictOption(Option)` + `is_strict_option`, make is_multioption exclude
# it, and set kind='fold1' in build for a strict Option (the fold1 handling
# downstream is still in place).


def run_main(parse, args=None, stylesheet=None, completion=None,
             errors=None, version=None, margin=None):
    """
    The main() driver: parse and execute, print errors the polite
    way, return the exit code.  stylesheet
    (a spec: None for auto, False for never-color, or a complete
    composed StyleSheet used verbatim) paints the 'error:' prefix
    and any attached usage/listing when the error stream wants
    color; the environment always wins (resolve_stylesheet via
    can_colorize).  completion, if
    given, is (table, prog): with an empty args and
    _APPEAL_COMPLETE in the environment, the invocation is a
    shell-completion reentry and is answered instead of parsed.

    errors is the file object error messages print to, default
    sys.stderr (the POSIX diagnostic convention, so pipelines
    reading this program's stdout stay clean; sys.stdout is v1's
    behavior).  Like print(file=None), the default is resolved
    at error time, so redirecting sys.stderr works.  Requested
    help always prints to stdout; this knob moves only the
    errors.

    version, if given, is the program's version string:
    `--version` as the first token prints it bare and exits 0,
    like -h/--help--program metadata outranks parsing.
    """
    if args is None:
        args = sys.argv[1:]
    if completion is not None and not args:
        from .completion import (complete_command, complete_command_set,
                                 completion_reentry)
        table, prog = completion
        if 'commands' in table:
            completer = lambda w, p: complete_command_set(table, w, p)
        else:
            completer = lambda w, p: complete_command(table, w, p)
        code = completion_reentry(completer, prog)
        if code is not None:
            return code
    # (--version/-V are ordinary precommand options now--Larry's
    # design, 2026-07-19--scanned in the pre-command-word era and
    # yielding to user declarations; no first-token special case)

    def error_stream():
        # None resolves at error time, not at call time (tests
        # and callers redirect sys.stderr)
        return errors if errors is not None else sys.stderr

    def error_prefix():
        # render is imported lazily--only when an error actually
        # prints, so the success path (and `import appeal`)
        # never pays big's ~40ms
        from .presentation import resolve_stylesheet, style
        sheet = resolve_stylesheet(stylesheet, error_stream())
        return sheet.render(style('error', 'error:'))

    def print_trailer(trailer):
        # trailer is a callable usage(file) -> str: it renders itself
        # (colored on a tty, plain otherwise, wrapped to the stream) for
        # the error stream -- errors ride the pipeline too (2026-08-06).
        # A blank line sets it off from the error (Larry, 2026-09-09)
        text = trailer(error_stream())
        if text:
            print(file=error_stream())
            print(text, file=error_stream())

    try:
        result = parse(list(args))
    except SystemExit as e:
        # the precommand exits (program metadata: -V, ...);
        # main()'s contract is to RETURN the exit code.  A non-int,
        # non-None code is a message -- Python prints it to stderr
        # and exits 1; reproduce that half of the contract too.
        code = e.code
        if isinstance(code, int):
            return code
        if code is None:
            return 0
        print(code, file=error_stream())
        return 1
    except KeyboardInterrupt:
        # a process ended by SIGINT dies quietly with 128+SIGINT
        # (the shell already echoed ^C).  ONLY here (ruled
        # 2026-07-09): run_main is the whole-program driver;
        # process()/parse() stay raw--Appeal is an argument
        # processor, not an environment
        return 130
    except AppealDataError as e:
        print(f"{error_prefix()} {e}", file=error_stream())
        if e.usage:
            print_trailer(e.usage)
        return 2
    except AppealConfigurationError:
        raise               # a bug in the program: traceback
    except CommandError as e:
        # the command failed on purpose: message, its chosen
        # code, no usage (the command line was fine)
        print(f"{error_prefix()} {e}", file=error_stream())
        return e.exit_code
    except AppealError as e:
        print(f"{error_prefix()} {e}", file=error_stream())
        return 1
    # the same reading as backend._halts: an exit status is an int that
    # isn't a bool.  True/False are answers, not verdicts--a command
    # returning True ("it worked") mustn't exit 1.  Any other value,
    # or none at all, is success.
    if isinstance(result, int) and not isinstance(result, bool):
        return result
    return 0


import os
import sys


##
## Themes (Larry's design, 2026-08-06): a theme is DATA--a dict
## of StyleSheet entries covering the Markdown concepts and
## appeal's role vocabulary (program, command, option, argument,
## oparg, summary, error).  Colors are palette-independent NAMES
## (red, dark_red, light_red, orange, ...); the palette maps
## them to escapes, so every theme works over every palette.  A
## COMPLETE help stylesheet is the composition
##
##     markdown_defaults | transforms | palette | StyleSheet(theme)
##
## (resolve_stylesheet builds exactly that), and
## Appeal(stylesheet=) takes such a composition and uses it
## VERBATIM.  Painting happens strictly AFTER layout: roles ride
## the baked pieces as style markup, measured styles-stripped,
## resolved by whichever sheet the destination stream deserves.
##
## The colors below are the theme lab's strawmen--Larry's red
## pen has the last word (tools/theme_lab.py is the bench).
##

# appeal_theme: the default.  Designed against the ANSI 16
# (terminal light/dark modes remap those for legibility, so it
# looks right on both).
# the four corners: warm = red/orange/yellow, cool =
# blue/green/cyan, purple in both.  light_* themes use dark_
# colors (dark ink on a light page); dark_* themes use light_.

# (big format_definition_list requires removed 2026-08-15: the
# runtime deflist path is wrap_words' own render_deflist; nothing
# calls format_definition_list any more.  Appeal still imports it
# for the borrowed-trio tests.)
##
## Templates (Larry's single-template model, ruled 2026-08-01).
## ONE template string defines the help page: six {sections}--
## usage, summary, doc, options, arguments, commands--all
## required.  The template establishes the ORDER of the page
## (the Markdown pivot, ruled 2026-08-05); the text between
## placeholders is each section's header, written in MARKDOWN
## ('## Options' by default)--the docstring's own heading
## decoration is input spelling, stripped by the scanner; the
## template dresses the page.  usage is special: not Markdown,
## rendered and wrapped separately.  Empty sections are
## suppressed, header and all.
##

##
## help machinery: assembles and lays out the page (imports big).
##

##
## the converter vocabulary
##
## v1's converter vocabulary: split, validate, validate_range,
## counter, accumulator, mapping.  All semantics probed against
## shipping v1 0.6.4.  Factory *products* carry a `recipe = True`
## marker so build.py recognizes them as vocabulary terminals.
##


split.factory = "split(':')"


validate.factory = "validate('red', 'green')"


validate_range.factory = "validate_range(0, 10)"


counter.factory = "counter()"


file.factory = "file()"


# ====================================================================
#  The data-driven back-end engine.  The work-item
#  classes wear an `Instruction` suffix; the self.X factory methods on
#  Converter keep the bare names.
# ====================================================================


# ---- the converter base --------------------------------------------


# ---- the engine ----------------------------------------------------


# theming (themes are DATA--resolve_stylesheet composes them) and
# the build surface is re-exported LAZILY via __getattr__
# below, so accessing appeal.appeal_theme / appeal.build / etc. still
# works but doesn't cost anything until you touch it.

# every exception, both spellings (the prefixed forms are the
# real names--they're what tracebacks show, v1's rendering kept)


import os as _os
import sys as _sys


# LAZY RE-EXPORTS: appeal.build / appeal.compile_plan / appeal.appeal_theme
# / ... still work, but import their (heavy) home module only on first
# access, so plain `import appeal` stays stdlib-only.  (Internal uses
# take a local import at the call site.)
_LAZY_REEXPORTS = {
    'Decorations': 'frontend', 'build_plan': 'frontend',
    'default_options': 'frontend', 'default_long_option': 'frontend',
    'default_short_option': 'frontend',
    'strip_first_argument_from_signature': 'frontend',
    'strip_self_from_signature': 'frontend',
    'appeal_markdown_defaults': 'presentation', 'appeal_theme': 'presentation',
    'uncolored_theme': 'presentation', 'plain_theme': 'presentation',
    'dark_cool_theme': 'presentation', 'dark_warm_theme': 'presentation',
    'light_cool_theme': 'presentation', 'light_warm_theme': 'presentation',
    'resolve_stylesheet': 'presentation', 'help_stylesheet': 'presentation',
    'completions': 'completion', 'completions_set': 'completion',
    'complete_command': 'completion', 'complete_command_set': 'completion',
    'completion_reentry': 'completion', 'completion_script': 'completion',
    '_split_arg_string': 'completion',
    'read_csv': 'load', 'read_iterable': 'load', 'read_mapping': 'load',
    'describe': 'schema', 'describe_set': 'schema',
    'mcp_input_schema': 'schema', 'run_mcp': 'mcp',
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

# featherweight stand-ins for the fast path: signature (microsecond) and
# MethodType minted from a bound method (`types` would cost the fast path
# an import), so registration + dispatch never trip the lazy real-inspect
# proxy.  getdoc etc. stay on _inspect (help).
_MethodType = type((lambda self: None).__get__(object()))


def _stamp_decoration(plan, entry):
    "Stamp the operand-placeholder shape onto a plan tree (see _build)."
    from .frontend import Terminal
    plan.decoration = entry
    for s in plan.slots:
        if not isinstance(s.child, Terminal):
            _stamp_decoration(s.child, entry)
    for o in plan.options:
        if o.child is not None:
            _stamp_decoration(o.child, entry)


def _merge_era_options(plans):
    """
    One owner per option string across the whole head: the precommand
    eras share their options with each other (`foo -q --version` works
    no matter which precommand maps which), and a string two eras both
    owned would bind by position--refused (Larry, 2026-09-03; the rule
    outlives the merged era).  A string
    the user WROTE twice--a long (from a parameter name) or an explicit
    @app.option claim--in two precommands is a build error naming both.
    An AUTO-proposed short yields instead: explicit claims trump it,
    and among autos the first-declared era keeps the letter and later
    ones simply go without--the same rule the letters already follow
    inside one plan.
    """
    written = {}                        # string the user wrote -> plan
    autos = []                          # (plan, option, short), era order
    for plan in plans:
        seen = set()
        for owner, option in plan.all_options():
            if id(option) in seen:
                # a shared rule revisited (all_options dedupes plans,
                # but belt and braces--the frontend twin's rule)
                continue   # pragma: no cover
            seen.add(id(option))
            auto = () if option.explicit else option.auto_shorts
            for s in tuple(option.strings):
                if s in auto:
                    autos.append((plan, option, s))
                    continue
                prior = written.get(s)
                if prior is None:
                    written[s] = plan
                    continue
                if prior is plan:       # scoped within one era: ruled there
                    continue
                raise AppealConfigurationError(
                    f"option {s!r} is declared by two precommands "
                    f"({prior.name!r} and {plan.name!r}); an option string "
                    f"needs one owner across the precommands")
    claimed = dict(written)
    for plan, option, s in autos:
        prior = claimed.get(s)
        if prior is None or prior is plan:
            claimed[s] = plan
            continue
        option.strings = tuple(t for t in option.strings if t != s)
    return plans


def _line_trailer(stylesheet, usage_markup):
    """
    A UsageError trailer (see UsageError.usage): renders `usage: <line>`
    for the output stream it's handed--stylesheet is the app's spec, so
    color is decided against that stream (a tty gets color, a pipe plain).
    """
    def trailer(file):
        from .presentation import resolve_stylesheet
        return 'usage: ' + resolve_stylesheet(stylesheet, file).render(
            usage_markup)
    return trailer


def _global_trailer(node):
    """
    What an error outside any command's era wears (Larry's rule,
    2026-09-09): the program's usage, then the command summary when
    the program has commands--the overview page; the usage line
    alone for a program without them.
    """
    root = node.root
    if root._table():
        return _overview_trailer(root)
    return _line_trailer(root.stylesheet, root._program_usage_markup())


def _overview_trailer(node):
    """
    A UsageError trailer: renders `node`'s base help page (the command
    overview, as `help` with no topic) for the output stream--what an
    unknown command earns (ruled 2026-09-06).
    """
    def trailer(file):
        return node._overview_text(file)
    return trailer


def _config_vet(plan, table_words, config, command_plan_for,
                strict=True):
    """
    Config layering's stage 1 (strict keys--"either this is ours,
    or it isn't"): every key must name a global-command option.
    Returns {key: the OptionRule}, or raises naming the offender--
    a config file is end-user input, so loudness is UsageError.
    strict=False (ruled 2026-08-29, the rc-file-adaptation case)
    instead SKIPS every key that can't layer: take what's mine,
    ignore the rest.
    """
    from .frontend import Terminal
    options = {}
    scoped = set()
    for owner, o in plan.all_options():
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
    from collections.abc import Mapping
    def scoped_in(rule, value):
        "the key--or a nested key of a group's mapping--that is scoped"
        if rule.name in scoped or rule.key in plan.scoped_keys:
            return rule.name
        if rule.child is not None and isinstance(value, Mapping):
            inner = {o.name: o for o in rule.child.options}
            for k, v in value.items():
                if k in inner:
                    hit = scoped_in(inner[k], v)
                    if hit:
                        return hit
        return None
    vetted = {}
    for key, value in config.items():
        rule = options.get(key)
        hit = rule and scoped_in(rule, value)
        if hit:
            if not strict:
                continue
            # refused BY DESIGN (ruled 2026-07-09): position is
            # the essence of a scoped option, and a mapping has no
            # position--the two transports don't compose.  A scoped
            # option INSIDE a group's mapping refuses the same way.
            raise AppealConfigurationError(
                f"config: {hit!r} names a scoped option (several "
                f"windows declare it, and position decides which--"
                f"a mapping has no position).  Set it on the "
                f"command line, or give the uses distinct "
                f"parameter names (@app.option)")
        if rule is not None:
            vetted[key] = rule
            continue
        if not strict:
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
                   for owner, o in p.all_options()):
                raise AppealDataError(
                    f"config: {key!r} is an option of {word!r}; "
                    f"config supplies only global-command "
                    f"options (no per-command sections)")
        raise AppealDataError(
            f"config: {key!r} isn't an option of this program")
    return vetted


def _config_apply(conv, table, plan, config, plan_for, strict=True):
    """
    Layer a precommand era's BOUND config mapping onto its parsed
    converter: defaults < config < argv, atomic per option.  Keys are
    vetted strictly (_config_vet).  Each vetted option argv did NOT set
    is read the way a mapping is read--load's by-name reader, the same
    converters--and ASSIGNED TO ITS OWNER: the era's converter for its
    own options; the argv-built nested instance for a nested converter's
    option; else that nested converter is built from the mapping, with
    defaults for the rest.  Nothing is re-serialized as a command line
    (Astra R02: that replay lost values--an option living on a positional
    converter, a by-name group mapping that skipped an optional operand).
    A false flag is ABSENT (the documented policy); conversion failures
    carry 'config:' provenance and name the key.
    """
    from .frontend import Nullary
    from .load import _option_value, _read_group, _read_bool
    vetted = _config_vet(plan, frozenset(table), config, plan_for, strict)
    owners = {id(rule): owner for owner, rule in plan.all_options()}
    for name, rule in vetted.items():
        raw = config[name]
        try:
            if rule.is_flag:
                if not _read_bool(raw, name):
                    continue                    # false: absent, default stays
                value = rule.present
            elif isinstance(rule, Nullary):
                if not _read_bool(raw, name):
                    continue
                value = rule.converters[0]()
            else:
                value = _option_value(rule, raw, name, strict)
            _config_assign(conv, plan, owners[id(rule)], name, raw, value,
                           strict, _read_group)
        except (AppealDataError, ValueError, TypeError) as e:
            # the reader names the innermost parameter it blamed
            param = e.param if isinstance(e, AppealDataError) else None
            raise AppealDataError(f"config: {e}",
                                  param=param or name) from None


def _config_assign(conv, plan, owner_plan, name, raw, value, strict,
                   read_group):
    """
    Put a config value on its owner, argv winning per option.  The owner
    is reached by the plan path from the era's converter: through
    argv-built nested instances where they exist, else by BUILDING the
    nested converter from a by-name mapping of the config value (its
    other parameters default).
    """
    holder = conv
    path = _owner_path(plan, owner_plan)
    for k, step in enumerate(path):
        node = step[1]
        if step[0] == 'option':
            instance = holder.kwargs.get(node.name)
            slot_key = node.name
        else:
            index = step[2]
            if index is None:
                raise AppealConfigurationError(
                    f"config: {name!r} lives on a converter behind a *args "
                    f"slot; a mapping can't say which window")
            instance = holder.args[index]
            slot_key = index
        if isinstance(instance, Converter):
            holder = instance                   # argv built it: descend
            continue
        # argv didn't build this level: build the rest from config, by
        # name, defaults for everything else
        mapping = {name: raw}
        for later in reversed(path[k + 1:]):
            mapping = {later[1].name: mapping}
        built = read_group(node.child, mapping, node.name, strict)
        if step[0] == 'option':
            holder.kwargs[slot_key] = built
        else:
            holder.args[slot_key] = built
        return
    if name not in holder.kwargs:               # argv wins, whole
        holder.kwargs[name] = value


def _owner_path(plan, target):
    """
    The steps from `plan` down to the plan `target`: ('slot', slot,
    index-in-args) through positional groups (index None for a *args
    window and everything after it), ('option', rule) through group
    options.  [] when
    target is plan; None when unreachable.
    """
    from .frontend import Terminal
    if plan is target:
        return []
    index = 0
    for slot in plan.slots:
        if slot.repeat or index is None:
            here = None                     # a *args window, or after one
        else:
            here = index
            index += 1
        if not isinstance(slot.child, Terminal):
            rest = _owner_path(slot.child, target)
            if rest is not None:
                return [('slot', slot, here)] + rest
    for option in plan.options:
        if option.child is not None:
            rest = _owner_path(option.child, target)
            if rest is not None:
                return [('option', option)] + rest
    return None


def _whole_program():
    "The help topic a bare -h means on a program with no commands."
    return ''


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
        from .frontend import signature
        parameters = list(signature(callable).parameters)
    except (ValueError, TypeError):
        return
    qualname = getattr(callable, '__qualname__', '')
    if parameters and parameters[0] == 'self' and '.' in qualname:
        raise AppealConfigurationError(
            f"{qualname}: first parameter is 'self' but no class "
            f"claims this command--did you forget to decorate the "
            f"class?")


class _Line:
    "One command line's parse in progress: the steps, and the state eras relay."
    __slots__ = ('argv', 'steps', 'dashdash', 'bled', 'tried', 'forced', 'path')
    def __init__(self, argv):
        self.argv = argv
        self.steps = []
        self.path = []                  # the command words dispatched, root
                                        # down: what a misplaced option's
                                        # placement is judged against
        self.dashdash = False           # `--` seen in the last era, and that
                                        # era bled it: the next era starts
                                        # with nothing an option (era-scoped,
                                        # Larry 2026-09-08; relayed by bleed)
        self.bled = {}                  # option handlers bled into the next era
        self.tried = {}                 # the last era's option strings (its own
                                        # and what bled into it): the scope a
                                        # mistyped option in command position
                                        # suggests from, wherever it surfaces
        self.forced = False             # ...and whether that era had seen `--`:
                                        # a dash token it declined was an
                                        # operand to it, so it's diagnosed as
                                        # a word, not an option


class _Step:
    """
    One unit of execution the parcel/scan pass listed: an era (a
    precommand's converter), a command, or a set's default command.
    execute() converts its
    records in token order and calls the converter; the holder's log
    and the class-as-app env are threaded through.
    """
    __slots__ = ('kind', 'node', 'cls', 'conv', 'proc', 'plan', 'config',
                 'immediate', 'word', 'callable', 'done')
    def __init__(self, kind, node, cls, conv, proc, plan=None, config=None,
                 immediate=False, word=None, callable=None):
        self.kind = kind
        self.node = node
        self.cls = cls
        self.conv = conv
        self.proc = proc
        self.plan = plan
        self.config = config
        self.immediate = immediate
        self.word = word
        self.callable = callable
        self.done = False

    def attach_usage(self, e):
        "A command's error wears its usage line (deepest-command-wins)."
        if e.usage is None:
            node = self.node
            markup = node._children[self.word]._head_usage_markup()
            e.usage = _line_trailer(node.stylesheet, markup)

    def execute(self, holder, env):
        assert not self.done
        self.done = True
        node = self.node
        cls, conv = self.cls, self.conv
        plan = cls.plan
        if plan.binds is not None:              # a method/BIC command: self is
            conv.bound = env.get(plan.binds)    # its class's instance, built by
                                                # an earlier step
        if self.kind == 'era':
            try:
                if self.config:
                    # a BOUND config mapping: argv parsed already; merge config
                    # for options argv didn't set, THEN invoke (argv wins, whole)
                    _config_apply(conv, node._table(), self.plan,
                                  self.config[0], node.plan_for, self.config[1])
                self.proc.resolve()             # the merged era's records
                result = conv()
            except AppealDataError as e:
                if e.usage is None:             # outside any command's era:
                    e.usage = _global_trailer(node)     # global usage
                raise
            if plan.constructs is not None:     # a global class-as-app: its
                env[plan.constructs] = result   # methods bind to this
            holder.instances.append(            # eras log (None,
                (None, result                   #  instance-or-None)
                 if plan.constructs is not None else None))
            return result
        if self.kind == 'default':
            # a set's default handler answers a line that stopped at the
            # set: a usage error it raises wears the set's page--the
            # command summary at the root, the command's page (its
            # subcommands listed) below (Larry's rule, 2026-09-09)
            try:
                result = self.proc.execute()
            except AppealDataError as e:
                if e.usage is None:
                    e.usage = _overview_trailer(node)
                raise
            holder.instances.append((None, None))
            return result
        if self.kind == 'help':
            # a command's help era (immediate): -h present prints the
            # command's page and exits; absent, nothing--and nothing logged
            return self.proc.execute()
        try:
            result = self.proc.execute()
        except AppealDataError as e:
            self.attach_usage(e)
            raise
        if plan.constructs is not None:         # a class command: stash instance
            env[plan.constructs] = result
        instance = result if _is_class_command(self.callable) else None
        holder.instances.append((holder._command_for(self.word), instance))
        return result


class Processor:
    """
    One execution of one command line--the object app.process() hands
    back (like a subprocess.Popen: not reusable, it represents a run).
    Construct it with the app and call it with an argv to run the line
    (streaming: parse, convert, and dispatch left to right); afterward
    it carries the outcome.  app.process() is the shortcut that builds
    one, calls it, and returns it.

    result is the invoked command's return value.  instances is the
    run's execution log, appended mechanically in execution order: one
    (command, instance) pair per command run.  command is the
    registered callable--None for the global command--and instance is
    the object it constructed (None for a plain function command; the
    built instance for a class-based one).
    """
    def __init__(self, app):
        self.app = app
        self.result = None
        self.instances = []

    def __call__(self, args=None):
        argv = _sys.argv[1:] if args is None else list(args)
        if not self.app.root._lazy:         # eager: build every command's plan
            self.app._compile_all()         # up front (once) so config errors
                                            # surface at startup, not on invoke
        # Larry's three passes (2026-09-07).  PARCEL+SCAN: _run_node walks
        # the whole line, parceling each era onto its converters and
        # checking structure--running NO user code--and lists the steps to
        # execute.  A structural problem anywhere ends the parcel: it's
        # NOTED, not raised yet.  Then the IMMEDIATE eras that scanned
        # clean (the metadata precommand: -h/--version) execute--they
        # outrank a problem later on the line.  Then the problem, if any,
        # is raised; else EXECUTE the rest left to right, converting each
        # step's records in token order as it runs (a later conversion
        # error doesn't un-run an earlier command; [[streaming-dispatch]]).
        line = _Line(list(argv))
        steps = line.steps
        problem = None
        try:
            self.app._run_node(line, 0, top=True)
        except AppealDataError as e:
            problem = e
        env = {}                            # class-as-app instance store
        self.result = None
        for step in steps:                  # the immediate eras, wherever
            if not step.immediate:          # they sit (a command's help era
                continue                    # follows waiting eras), in order
            self.result = step.execute(self, env)
            if _halts(self.result):         # an immediate era halted: done
                return self.result
        if problem is not None:
            raise problem
        for step in steps:
            if step.immediate:
                continue                    # already ran
            self.result = step.execute(self, env)
            if _halts(self.result):
                break
        return self.result

    def __repr__(self):
        return (f'<Processor result={self.result!r} '
                f'({len(self.instances)} commands run)>')

    def _command_for(self, word):
        """
        The registered callable behind a word, for the instances
        log (None: the global).  A bare word naming DIFFERENT
        callables under different parents is ambiguous from here
        (the log doesn't carry their parent), so it answers None
        rather than guess wrong.
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

# the default_global_mappings menu, importable (spell your subset with
# these: default_global_mappings(*default_global_mappings_help))
default_global_mappings_help = ('-h', '--help', 'help')
default_global_mappings_version = ('-V', '--version', 'version')
# the default_command_mappings menu
default_command_mappings_help = ('-h', '--help')


def default_command_mappings(*options):
    """
    The FACTORY for the stock per-command defaults policy (Larry's
    design, 2026-09-08): the option strings a command's help era maps
    when the command hasn't claimed them--'-h', '--help'; empty means
    both.  Returns the policy callable--@app.command()'s default is
    default_mappings=default_command_mappings().  Pass
    default_mappings=None for no per-command help.  `tool build -h`
    prints build's page and exits; a command with subcommands lists
    them (`tool db -h`), and `tool db stop -h` is stop's page--the
    path is the topic, so the option takes no oparg.
    """
    if not options:
        options = default_command_mappings_help
    for o in options:
        if o not in default_command_mappings_help:
            raise AppealConfigurationError(
                f"default_command_mappings: unknown mapping {o!r}; the "
                f"menu is {sorted(default_command_mappings_help)}")
    requested = frozenset(options)

    def default_command_mappings_policy(node, claimed):
        "The strings to map, given what the command already claims."
        return [s for s in default_command_mappings_help
                if s in requested and s not in claimed]
    default_command_mappings_policy.requested = requested
    return default_command_mappings_policy


_STOCK_COMMAND_MAPPINGS = default_command_mappings()


def default_global_mappings(*options):
    """
    The FACTORY for the stock program-level defaults policy
    (Larry's design, 2026-07-25).  List the mappings you want:
    '-h', '--help', '-V', '--version' (precommand options),
    'help', 'version' (commands); empty means all of them.
    Returns the policy callable--the constructor default is
    default_mappings=default_global_mappings().  Pass
    default_mappings=None for no default mappings at all.

    Order is insignificant (the listing keeps v1's order, version
    before help).  Version mappings apply only when the app has a
    version string.  Each mapping lands only if not already
    mapped--user declarations always win.
    """
    if not options:
        options = default_global_mappings_help + default_global_mappings_version
    valid = set(default_global_mappings_help + default_global_mappings_version)
    for o in options:
        if o in valid:
            continue
        if isinstance(o, str):
            near = {'-v': '-V', '--h': '--help', '-help': '--help',
                    '-version': '--version'}.get(o)
            hint = f" (did you mean {near!r}?)" if near else ''
            raise AppealConfigurationError(
                f"default_global_mappings: unknown mapping {o!r}{hint}; "
                f"the menu is {sorted(valid)}")
        raise AppealConfigurationError(
            f"default_global_mappings: {o!r} isn't a mapping name.  "
            f"default_global_mappings is a factory--pass the constructor "
            f"default_mappings=default_global_mappings(), not the "
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
    default_mappings_policy.requested = requested
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
    def __init__(self, name=None, *,
                 default_mappings=default_global_mappings(),
                 default_options=_DEFAULT_OPTIONS,
                 default_subcommand=None,
                 doc=None,
                 errors=None,
                 lazy=False,
                 margin=None,
                 repeat=False,
                 script=_sys.argv[0],
                 stylesheet=None,
                 version=None):
        from .frontend import Decorations
        self.name = name
        # the command tree (v1's model, restored 2026-07-18 by
        # Larry's ruling): a tree of Appeal instances, one per
        # command word, linked by .parent.  A node's _impl is its
        # command function--for a node with children, that
        # function is the global command of its own little set.
        # The flat views (_commands, _subs, ...) are read-only,
        # derived from this tree.
        self.parent = None        # set by _child(): the node's parent
        self._children = {}       # command word -> child Appeal
        self._impl = None         # this node's command function
        self._precommands = []    # ordered precommand eras (the head; _impl
                                  # tracks the primary until dispatch runs them all)
        self._precommand_explicit = set()  # ids given an explicit index=
        self._precommand_config = {}       # id(callable) -> the bound config
        self._precommand_flags = {}        # id(callable) -> (share,
                                           #   immediate), the era flags
                                           # given to subcommand(), applied
                                           # when the path resolves
                                           # mapping (filled by the user later)
        self._node_default = None # this node's default command
        self._node_repeat = False # this node's set cycles
        self._node_share = False  # this command's options are shared FORWARDS
        self._command_mappings = _STOCK_COMMAND_MAPPINGS   # the help era's policy
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
        # eager plan compilation (Larry, 2026-08-24): unless lazy, the first
        # process()/main() builds every command's plan across the tree, so a
        # ConfigurationError anywhere surfaces at startup rather than only when
        # that command happens to be invoked.  _compiled_all guards the once.
        self._lazy = lazy
        self._compiled_all = False
        # the option-string policy (v1's knob, restored): a callable
        # (name, annotation, default) -> list of option strings, run
        # at build time on every automatically-mapped keyword-only
        # parameter.  The stock policy adds a long and a short;
        # default_long_option drops the short, default_short_option
        # drops the long, or supply your own.  Its output--the
        # strings--are computed at build time.
        if default_options is _DEFAULT_OPTIONS:
            # not supplied -> the stock policy (lazy: importing build
            # is deferred until an Appeal is actually constructed)
            from .frontend import default_options as default_options
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
        # the program-wide default subcommand (Larry's design, 2026-09-09):
        # what runs when a line stops at a command that has subcommands,
        # after the command's body.  Stock None: nothing--subcommands are
        # optional; no_subcommand requires them.  A callable that takes no
        # arguments.  @node.default() overrides it per command, and sets
        # the root's default command (stock: print the program's usage).
        if default_subcommand is not None:
            _vet_default(default_subcommand, 'default_subcommand')
        self._default_subcommand = default_subcommand
        self._finalized = False
        self._precommand_options = {}   # param -> (strings...)
        self._precommand_overrides = {} # param -> (annotation, default) from
                                        # app.option(annotation=, default=)
        # EVERYTHING @app.option/@app.parameter expressed, keyed
        # by the decorated callable (ruled 2026-08-09: Appeal
        # never modifies objects the user owns--decoration writes
        # it down HERE and moves on).  One registry per tree.
        self._decorations = Decorations()
        # how an operand renders in usage lines and help tables is
        # the `argument_decoration` stylesheet transform (host ->
        # <HOST>), not a constructor knob--restyle it there.
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
        # margin is the wrap width.  None (the default) measures: a
        # tty gets its real width, a non-tty (pipe/capture) gets 79,
        # so redirected output is stable.  A positive int wraps at
        # exactly that width, tty or not.  (indent= died unshipped
        # with the Markdown pivot, 2026-08-06: big's renderer owns
        # the definition-list layout.)
        if margin is not None and (not isinstance(margin, int)
                                   or margin <= 0):
            raise AppealConfigurationError(
                f"margin must be None or a positive int, not {margin!r}")
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
            from .presentation import default_template
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
        self._plans = {}          # {id(node): Plan}, filled per word
        self._global_plan = None  # scalar
        self._era_plans = None    # the head eras' plans, built once (the
                                  # compiled class lives on each plan)

    def _invalidate(self):
        # registration under a node changes every ancestor's
        # compiled artifacts (they embed the descendants); a
        # node's own descendants embed nothing of it, so down
        # the tree nothing staling
        node = self
        while node is not None:
            node._plans = {}
            node._global_plan = None
            node._era_plans = None
            node = node.parent

    # ------------------------------------------------------------
    # the command tree: registration
    # ------------------------------------------------------------

    def _child(self, word):
        "Fetch-or-create the child Appeal for a command word."
        node = self._children.get(word)
        if node is None:
            # a subcommand node is a full Appeal; the program knobs are
            # the root's, copied as processed values
            node = Appeal(word)
            node.parent = self
            for attr in ('_help_enabled', 'default_options',
                         'default_mappings',
                         'script', 'errors', 'repeat', 'stylesheet',
                         'margin', '_templates'):    # the BACKING field, not the
                setattr(node, attr, getattr(self, attr))  # `templates` property
                                                    # -- copying the property would
                                                    # force render's lazy import
            node.version = None
            node._finalized = True      # the ROOT runs the pass
            node._decorations = self.root._decorations
            node._method_owner = self._method_owner
            self._children[word] = node
            self._invalidate()
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
        table = {}
        plan = None
        if self._impl is not None:
            plan = (self.global_plan if self.parent is None
                    else self._plan())
        if plan is not None:
            for owner, o in plan.all_options():
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
                                'requested', None)
            if requested is not None:
                # the stock factory says what was asked for
                root._help_enabled = bool(
                    requested & {'-h', '--help', 'help'})
            else:
                # a custom policy: judge by what it actually mapped
                root._help_enabled = bool(
                    root._precommand_options.get('help')
                    or 'help' in root._children)
        root._derive_method_owners()
        root._refuse_bodyless_parents()

    def _refuse_bodyless_parents(self):
        """
        `db_app = app.command('db')` may be decorated with subcommands
        and a default before `db` itself is bodied--but by the time the
        program runs, it must be (Larry, 2026-09-09; the no-pure-
        dispatcher ruling of 2026-08-22: Appeal never synthesizes the
        parent).
        """
        for word, node in self._iter_nodes():
            if (node._impl is None
                    and (node._children or node._node_default is not None)):
                raise AppealConfigurationError(
                    f"command {node._prog()!r} has subcommands or a default "
                    f"but no body: decorate a function with "
                    f"@app.command({word!r}) (Appeal never synthesizes one)")

    def print_version(self):
        "Print the program's version."
        print(self.root.version)

    def _help_topic_page(self, words, suppress=frozenset()):
        """
        help(*words)'s command-page path.  One word names a command
        (a direct one, or a unique deeper one--_node_for); several
        walk the tree from the root, `help db stop`.  A word that
        isn't a command where it stands refuses naming that place,
        with a suggestion from its table; the trailer is that set's
        overview.
        """
        root = self.root
        table = root._table()
        topic = words[0]
        if len(words) == 1:
            if topic == 'help':
                print('Print usage documentation on a specific command.')
                return
            fn = table.get(topic)
            if fn is None and topic.replace('_', '-') in table:
                topic = topic.replace('_', '-')     # accept the underscore spelling
                fn = table.get(topic)
            if getattr(fn, '__func__', None) is Appeal.print_version:
                # a stock command describes itself with its summary
                print(_inspect.getdoc(fn))
                return
            if topic not in table:
                err = UsageError(f"unknown command {topic!r}"
                                 f"{did_you_mean(topic, table)}")
                err.usage = _overview_trailer(root)     # the overview page
                raise err
            node = root._node_for(topic)
            parent = node.parent
        else:
            # a word path: each word must be a command of the set before it
            node = root
            for word in words:
                parent = node
                node_table = parent._table()
                if word not in node_table:
                    alt = word.replace('_', '-')
                    if alt in node_table:
                        word = alt
                    else:
                        where = (f" of {parent._prog()!r}" if parent is not root
                                 else '')
                        err = UsageError(f"unknown command {word!r}{where}"
                                         f"{did_you_mean(word, node_table)}")
                        err.usage = _overview_trailer(parent)
                        raise err
                node = parent._children[word]
            topic = word
        # render the topic's page directly from plans (the one engine has no
        # baked-help compile step).  A topic that is itself a command SET shows
        # its subcommand listing (like `prog topic --help`); a leaf shows its
        # command page.
        from .presentation import help_margin, render_help_page
        if node is not None and node._table():
            from .presentation import summary as _summary, command_set_corpus
            node_table = node._table()
            entries = [(w, _summary(c)) for w, c in node_table.items()]
            # add the auto `help` row unless the set already registers one
            corpus = command_set_corpus(
                node.global_plan, entries,
                doc=node._program_doc_override())
            text = render_help_page(
                node._head_usage_markup(),
                corpus, node.templates, margin=help_margin(node.margin, _sys.stdout),
                file=_sys.stdout, stylesheet=node.stylesheet,
                suppress=suppress).rstrip('\n')
        else:
            from .presentation import merge_docs
            plan = root._plan_for_node(node, topic)
            text = render_help_page(
                node._head_usage_markup(), merge_docs(plan),
                root.templates,
                margin=help_margin(root.margin, _sys.stdout),
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
        if help is not None:                # '' is the bare page; a word
            _sys.exit(self.help(*([help] if help else [])))   # is one topic

    def _command_callable(self):
        """
        This node's command function, or None for a word that was
        named but never bodied.  Never synthesized: a parent that was
        only ever chained through is refused at finalize (Larry's
        no-pure-dispatcher ruling, 2026-08-22).
        """
        return self._impl

    def _iter_set_nodes(self):
        "Every descendant, any depth, that parents a nested set."
        for word, node in self._iter_nodes():
            if node._children:
                yield word, node

    def _iter_nodes(self):
        "Every descendant, any depth, with the word that names it."
        for word, node in self._children.items():
            yield word, node
            yield from node._iter_nodes()

    # -- the flat views: read-only, derived from the tree

    @property
    def _has_commands(self):
        "Does this node have at least one command (a child with a body)?"
        return any(node._command_callable() is not None
                   for node in self._children.values())

    @property
    def _global(self):
        return self._impl

    @property
    def _default(self):
        """
        The handler for a line that stops at this node: the node's own
        (@node.default()), else the stock--at the root, print the
        program's usage (Larry, 2026-09-09: a bare line is a request for
        orientation, not an error); below, the program's
        default_subcommand.  None: nothing runs.
        """
        if self._node_default is not None:
            return self._node_default
        root = self.root
        return self._print_usage if self is root else root._default_subcommand

    def _print_usage(self):
        "The stock default command: the program's usage and command summary."
        self.help()
        return 1                        # orientation, git-style: not success

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

    def command(self, name=None, *, repeat=False, share=False,
                default_mappings=_UNSET):
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
        `.default()` picks what runs when the line stops at the
        parent--and every other Appeal method is there,
        because the child IS an Appeal.  repeat=True makes the
        node's set cycle: after a subcommand's arguments, the
        next token may name another one.  default_mappings= is the
        command's help-era
        policy (Larry, 2026-09-08): the stock
        default_command_mappings() maps -h/--help onto the command
        when it hasn't claimed them; None turns the command's help
        off.
        """
        if name is not None:
            # the node comes back--decorator AND chaining handle
            if not isinstance(name, str):
                raise AppealConfigurationError(
                    f"command(): the command word must be a "
                    f"string, not {name!r}")
            node = self._child(self._command_word(name))
            if repeat and not node._node_repeat:
                node._node_repeat = True
                self._invalidate()
            if share and not node._node_share:
                node._node_share = True
                self._invalidate()
            if default_mappings is not _UNSET:
                node._command_mappings = default_mappings
                self._invalidate()
            return node
        def decorator(callable):
            node = self._child(self._command_word(None, callable))
            node._node_repeat = node._node_repeat or repeat
            node._node_share = node._node_share or share
            if default_mappings is not _UNSET:
                node._command_mappings = default_mappings
            return node(callable)
        return decorator

    def default(self):
        """
        The command run when the line stops at this node--for the
        root, a line naming no command (replacing the stock one,
        which prints the program's usage and command summary); for
        a subcommand node (db_app = app.command('db');
        @db_app.default()), a line ending at the parent (replacing
        Appeal(default_subcommand=)).  Any callable that runs with
        no arguments; never a string.
        """
        def decorator(callable):
            self._node_default = callable   # vetted when its plan builds:
            self._invalidate()              # a method's self isn't an argument
            return callable
        return decorator
    default_command = default           # the old spelling (deprecated)

    def precommand(self, *, index=-1, config=None, strict=None,
                   share=False, immediate=False):
        """
        Register a precommand era.  A class here is class-as-app (its __init__
        is the era's grammar; its methods/inner classes bind to the instance).
        REPEATABLE (Larry, 2026-08-21): each call inserts an era; index -1
        appends, 0 heads, they run front-to-back before the commands.

        config= (Larry, 2026-08-25): BIND a mapping to this precommand and its
        option values layer from it (defaults < config < argv) at dispatch.
        You bind the dict at decoration and fill it before main() -- Appeal
        holds the SAME object, so an empty dict you .update() later is seen.
        None (the default) means this era takes no config -- the -h/--help/
        --version precommand simply leaves it None and opts out for free.

        strict= (Larry, 2026-08-29) governs the bound mapping's key vetting:
        True (the default) raises on any key that isn't one of this era's
        options; strict=False takes the keys that are and ignores the rest
        (adapting an existing rc file that also holds non-CLI junk).  It
        only means something with config=, and is refused without it.

        The era flags (Larry's design, 2026-09-08).  Each precommand is
        an era of its own, and the precommand eras share their options
        with each other: every precommand's options are recognized in
        the precommand eras before and after it, so `foo -q --version`
        and `foo --b-opt aval` both work whichever precommand owns
        which string--and none of them reach the first command.  That
        is share=False, the default (PRECOMMAND on the era).
        share=True shares the era's options FORWARDS as well--into the
        first command's eras (and, via a command's own share=True, on
        through it).  immediate=True: the era executes before the line
        is judged--how -h/--help/--version outrank a malformed line.
        Appeal's own metadata precommand is immediate=True.
        """
        if self.parent is not None:
            # it would even work--a node's body is the first precommand of
            # its own little set, and more could follow it--but it's daffy
            # from the user's side (Larry, 2026-09-09).  In the back pocket.
            raise AppealConfigurationError(
                f"precommand(): only the program has precommands, not the "
                f"command {self._prog()!r}")
        if strict is not None and config is None:
            raise AppealConfigurationError(
                "precommand(): strict= only means something with config=")
        if not isinstance(share, bool):
            raise AppealConfigurationError(
                f"precommand(): share= is True or False, not {share!r}")
        def decorator(callable):
            if index == -1:
                self._precommands.append(callable)
            else:
                self._precommands.insert(index, callable)
                self._precommand_explicit.add(id(callable))
            if config is not None:
                self._precommand_config[id(callable)] = (
                    config, True if strict is None else strict)
            self._precommand_flags[id(callable)] = (
                True if share else PRECOMMAND, immediate)
            self._impl = self._precommands[-1]
            self._invalidate()
            return callable
        return decorator
    global_command = precommand         # transitional alias for the old name

    def _node_at(self, path):
        "The node at a word path (a sequence of command words)."
        node = self.root
        for word in path:
            node = node._children[word]
        return node

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
        seen = set()
        def add_class(fn, mount):
            # a class mounted in ANY slot (precommand era, command, default);
            # dedup so a class that is both _impl and a precommand counts once
            if (fn is not None and _is_class_command(fn)
                    and id(fn) not in seen):
                seen.add(id(fn))
                classes.append((fn, mount))
        add_class(self._impl, self)     # the global command (incl bare @app)
        def find(node):
            for pre in node._precommands:
                add_class(pre, node)    # a class precommand era
            add_class(node._node_default, node)
            for child in node._children.values():
                add_class(child._impl, child)
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
                # a node's DEFAULT command may itself be a method of the
                # class (same slot-agnostic membership as a command)
                dfn = node._node_default
                if dfn is not None and id(dfn) in members:
                    owners[id(dfn)] = key
                # a precommand ERA may also be a method/inner class of the
                # class (the class's own precommand isn't in its own __dict__,
                # so it never self-claims)
                for pre in node._precommands:
                    if pre is not None and id(pre) in members:
                        owners[id(pre)] = key
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
        # build's "not specified" marker is frontend.empty (the same singleton
        # build compares against); convert here at call time.
        from .frontend import empty
        if default is _UNSET:
            default = empty
        if annotation is None:
            annotation = empty
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
                root = callable.__self__.root
                root._precommand_options[name] = tuple(options)
                root._precommand_overrides[name] = (annotation, default)
                root._invalidate()
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
        from .completion import completions, completions_set
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
                            repeat=self.repeat, sets=sets or None)

    def _overview_text(self, file, suppress=frozenset()):
        """
        The base help page rendered for `file` (color and width decided
        against it) and RETURNED, not printed--the command overview for
        a set, the --help page for a bare app.  help() prints it to
        stdout; an unknown command's trailer renders it to the error
        stream (see _overview_trailer).
        """
        from .presentation import (render_help_page, help_margin)
        table = self._table()
        if table:
            from .presentation import summary, command_set_corpus
            entries = [(w, summary(c)) for w, c in table.items()]
            corpus = command_set_corpus(
                self.global_plan, entries,
                doc=self._program_doc_override())
            return render_help_page(
                self._head_usage_markup(),
                corpus, self.templates,
                margin=help_margin(self.margin, file),
                file=file, stylesheet=self.stylesheet,
                suppress=suppress).rstrip('\n')
        from .presentation import merge_docs, parse_docstring
        plan = self.plan
        corpus = merge_docs(plan)
        override = self.root.doc
        if override is not None:
            # tier 1 overrides a bare app's prose too; the
            # signature-bound sections stay with the command
            parsed = parse_docstring(override, '<program documentation>')
            corpus['summary'] = parsed['summary']
            corpus['documentation'] = parsed['documentation']
        return render_help_page(
            self._head_usage_markup(), corpus, self.templates,
            margin=help_margin(self.margin, file),
            file=file, stylesheet=self.stylesheet,
            suppress=suppress).rstrip('\n')

    def _program_usage_markup(self):
        """
        The program's usage LINE markup--the trailer content for an
        era-level error (a bad program-wide option) and an unknown
        option: the command-set line for a set, the plan's usage for a
        bare app.
        """
        return self._head_usage_markup()

    def help(self, *topic, usage=True, summary=True, doc=True):
        """
        Print usage documentation on a specific command.

        (That first paragraph doubles as the help command's listing
        row, so it stays one sentence.)  Bare: the --help text (bare
        apps) or the command listing (sets), v1-style--also returned.
        With a topic: that command's help page; a subcommand's page is
        reached by its word path, `help db stop` (Larry, 2026-09-08--
        the words as they're typed on the line, never split out of one
        string).  This method IS the help command (and -h/--help, via
        the precommand, which passes one word); subclass and override
        to customize every spelling at once.

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
            return self._help_topic_page(list(topic), suppress)
        print(self._overview_text(_sys.stdout, suppress))
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
            from .presentation import to_commonmark, to_github
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
        from .presentation import command_set_corpus, man_page, merge_docs, summary
        prog = self._prog()
        version = str(self.version) if self.version is not None else None
        table = self._table()
        if not table:
            plan = self.plan
            return man_page(prog, merge_docs(plan), self._head_usage_markup(),
                            version=version)
        entries = [(w, summary(c)) for w, c in table.items()]
        corpus = command_set_corpus(
            self.global_plan, entries,
            doc=self._program_doc_override(), listing=False)
        pages = [(word,
                  self._children[word]._head_usage_markup(),
                  merge_docs(self.plan_for(word)))
                 for word in table]
        return man_page(prog, corpus, self._head_usage_markup(),
                        command_pages=pages, version=version)

    def schema(self, format, version):
        """
        The program's machine-readable schema.  Both parameters are
        required--name the format AND the version of it you can
        consume (they're plain strings):

            app.schema('appeal', '1.0')
            app.schema('mcp', '2024-11-05')

        'appeal' is the full description: every command's usage,
        operands, options with their spellings, arities, defaults,
        and docs--the machine-readable twin of --help, pairing with
        read_mapping().  Its version is Appeal's own (additions may
        not bump it; breaking changes will).

        'mcp' is standard JSON Schema (type/properties/required),
        one per command keyed by command word, the projection an MCP
        client validates tool arguments against; its versions are
        the MCP protocol's date-stamped revisions.  Lossy on
        purpose: JSON Schema can't spell option strings or arity
        windows--'appeal' is the lossless form.
        """
        from .schema import (_APPEAL_VERSIONS, _MCP_VERSIONS,
                             describe, describe_set, mcp_input_schema)
        table = self._table()
        if format == 'appeal':
            if version not in _APPEAL_VERSIONS:
                raise AppealConfigurationError(
                    f"schema(): unknown 'appeal' schema version "
                    f"{version!r}; this Appeal renders "
                    f"{', '.join(map(repr, _APPEAL_VERSIONS))}")
            if not table:
                return describe(self.plan)
            return describe_set(self.plans, self.global_plan,
                                self._prog())
        if format == 'mcp':
            if version not in _MCP_VERSIONS:
                raise AppealConfigurationError(
                    f"schema(): unknown 'mcp' schema version "
                    f"{version!r}; this Appeal renders "
                    f"{', '.join(map(repr, _MCP_VERSIONS))}")
            if not table:
                return {self._prog(): mcp_input_schema(self.plan)}
            return {word: mcp_input_schema(self.plan_for(word))
                    for word in table}
        raise AppealConfigurationError(
            f"schema(): unknown format {format!r}; the formats are "
            f"'appeal' and 'mcp'")

    def read_mapping(self, callable, mapping, *, strict=True):
        "v1's API: call `callable` with values pulled from `mapping`."
        from .load import read_mapping
        self._finalize()
        return read_mapping(callable, mapping, strict=strict)

    def read_iterable(self, callable, iterable, *, strict=True):
        "v1's API: call `callable` once per row; returns the results."
        from .load import read_iterable
        self._finalize()
        return read_iterable(callable, iterable, strict=strict)

    def read_csv(self, callable, reader, *, first_row_map=None, strict=True):
        "v1's API: read_iterable for csv.reader input (see read_csv)."
        from .load import read_csv
        self._finalize()
        return read_csv(callable, reader, first_row_map=first_row_map,
                        strict=strict)

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
        # words are unique by construction (keys of self._children), so no
        # collision check is needed; a bodyless node contributes nothing
        table = {word: node._command_callable()
                 for word, node in self._children.items()
                 if node._command_callable() is not None}
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
        build_plan() a top plan and stamp it with the app's help
        policy.  Every top plan the app renders funnels through here.
        """
        from .frontend import build_plan
        # the policy registers via the registrar-proxy's
        # app.option() (arglet style, Larry's design 2026-07-22);
        # build constructs the proxy around the real app
        plan = build_plan(callable,
                     default_options=self.root.default_options,
                     app=self.root,
                     decorations=self.root._decorations, **kwargs)
        plan.auto_help = self._help_enabled
        entry = self._decoration_entry()
        if entry is not None:
            _stamp_decoration(plan, entry)
        return plan

    def _decoration_entry(self):
        """
        The operand-placeholder SHAPE this app renders (ruled Larry,
        2026-09-03: the argument_decoration stylesheet entry is
        tweakable--"it's in the stylesheet precisely so users can
        tweak it").  An explicitly-given sheet's entry wins; the
        automatic default (stylesheet=None) and stylesheet=False
        keep the stock <NAME>, because the shape bakes into layout
        at build time and must not vary per stream.
        """
        sheet = self.stylesheet
        if sheet is None or sheet is False:
            return None
        return sheet.get('argument_decoration')

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
            plan.share = FORWARDS if node._node_share else False
            plan = self._plans.setdefault(id(node), plan)
        return plan

    def _default_plan(self):
        "This set's default command's Plan, cached with the node's plans."
        plan = self._plans.get('default')
        if plan is None:
            owner = self._method_owner.get(id(self._default))
            if owner is None:                   # a self-method no class
                _refuse_orphan_method(self._default)   # claimed: refuse
            plan = self._build(self._default, method_of=owner)
            if plan.minimum:
                # a default handler runs with no arguments (Larry,
                # 2026-09-09); a wrapper supplies any the real command needs
                names = ', '.join(repr(s.name) for s in plan.slots
                                  if s.required)
                raise AppealConfigurationError(
                    f"the default command must be callable with no "
                    f"arguments; {plan.name!r} requires {names}")
            plan = self._plans.setdefault('default', plan)
        return plan

    def plan_for(self, word):
        "The named command's Plan, built at first request."
        self._finalize()
        node = self._node_for(word)
        if node is None:
            raise AppealConfigurationError(f"no command named {word!r}")
        return self._plan_for_node(node, word)

    def _compile_all(self):
        """
        Eagerly build every command's plan across the whole tree (unless the
        app is lazy), so a ConfigurationError anywhere -- a bad annotation, an
        option a later one stomps on -- surfaces now, at first process()/
        main(), instead of lurking until someone invokes that command.  Runs
        once per root.
        """
        from .backend import converter_for
        root = self.root
        if root._compiled_all:
            return
        root._compiled_all = True
        root._finalize()
        for plan in root.global_plans():                # the head eras
            converter_for(plan)
        def visit(node):
            for word, child in list(node._children.items()):
                if child._command_callable() is not None:
                    converter_for(node._plan_for_node(child, word))
                visit(child)
        visit(root)

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
        "The program name; for a subcommand set, the word path to it (tool db)."
        if self.parent is not None:
            return f'{self.parent._prog()} {self.name}'
        return self.name or _os.path.basename(self.script) or 'program'

    def _option_placements(self, path):
        """
        Every option string in the program -> where it goes, in words,
        for the misplaced-option error ("option '--jobs' can't be used
        here; it goes after 'build'").  The head eras' options go before
        the command; a command's (subcommands included, by their word
        path) go after it--and when that command is an ancestor of where
        the option surfaced (`path`, the words dispatched so far), the
        option went too far: "after 'db', but before any subcommand"
        (Larry, 2026-09-08).  Built on demand--only an error asks.
        """
        places = {}                     # string -> {where: True}, in order
        def claim(plan, where):
            for owner, o in plan.all_options():
                for s in o.strings:
                    places.setdefault(s, {})[where] = True
        for plan in self.global_plans():
            claim(plan, None)                       # None: a program option
        def walk(node, path):
            for word, child in node._children.items():
                if child._command_callable() is None:
                    continue
                claim(self._plan_for_node(child, word), path + word)
                walk(child, path + word + ' ')
        walk(self, '')
        owners = {}
        for s, where in places.items():
            phrases = []
            if None in where:
                phrases.append('before the command')
            commands = [w.split() for w in where if w is not None]
            # the option's command was dispatched and the line went on into
            # one of its subcommands: the option went too far
            deep = [w for w in commands
                    if path[:len(w)] == w and len(path) > len(w)]
            flat = [repr(' '.join(w)) for w in commands if w not in deep]
            phrases.extend(f"after {' '.join(w)!r}, but before any subcommand"
                           for w in deep)
            if flat:
                phrases.append('after ' + ' or '.join(flat))
            owners[s] = '; or '.join(phrases) if deep else ', or '.join(phrases)
        return owners

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


    def _head_usage_markup(self):
        """
        The usage LINE for this node's program: the program name, then
        everything typed before a command word--every head era's options
        and operands in era order, the metadata precommand's -h/--help/
        --version first (Larry, 2026-09-08: they aren't special enough
        to break the rules; v1 hid them)--then the <COMMAND> placeholder
        when this node dispatches commands.  A subcommand set's line
        shows its own command's options and operands.
        """
        from big.stylesheet import style, escape_styles
        parts = [style('program', escape_styles(self._prog()))]
        if self.parent is None:
            plans = self.global_plans()
        else:
            plans = [p for p in (self._help_plan(), self.global_plan)
                     if p is not None]
        for plan in plans:
            body = plan.usage_body()
            if body:
                parts.append(body)
        if self._table():
            # the placeholder keeps the argument DECORATION (<COMMAND>: a
            # hole to fill) but wears the command ROLE--the words that can
            # fill it are printed in that same style in the listing below,
            # so the paint cross-references them (Larry's ruling, 2026-09-07)
            from .presentation import decorate_argument
            parts.append(style('command',
                               decorate_argument('command',
                                                 self._decoration_entry())))
        return ' '.join(parts)

    def _precommand_plan(self):
        """
        The precommand's mini plan, built from what
        default_mappings mapped to it.  None when nothing is.
        The closure is marked stock when the app doesn't override
        the precommand family.
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
        # gives ''), version=False is a flag.  The parameter is named
        # `topic` for the usage line ([-h|--help [<TOPIC>]]); the
        # user-facing mapping name stays 'help'
        if want_v and want_h:
            def precommand(*, topic: optional[str] = None,
                           version=False):
                app.help_and_version_precommand(help=topic, version=version)
        elif want_v:
            def precommand(*, version=False):
                app.help_and_version_precommand(version=version)
        else:
            def precommand(*, topic: optional[str] = None):
                app.help_and_version_precommand(help=topic)
        from .frontend import empty
        overrides = app.root._precommand_overrides
        if want_v:
            annotation, default = overrides.get('version', (empty, empty))
            app.root._decorations.add_option(precommand, 'version',
                                             mapped['version'],
                                             annotation=annotation,
                                             default=default)
        if want_h:
            annotation, default = overrides.get('help', (empty, empty))
            if annotation is empty and not app.root._has_commands:
                # a program with no commands (grep): -h/--help take no
                # topic.  A zero-argument converter makes the option a
                # flag whose presence yields the whole-program topic
                # (Larry, 2026-09-08); absent, help stays None
                annotation, default = _whole_program, None
            app.root._decorations.add_option(precommand, 'topic',
                                             mapped['help'],
                                             annotation=annotation,
                                             default=default)
        app.root._precommand_flags[id(precommand)] = (PRECOMMAND, True)
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
        Empty when there's no head at all.  Each precommand is an era of its
        own; its share (PRECOMMAND by default) resolves against its
        neighbors when the eras are built (_head_eras).  One owner per
        option string across the whole head--see _merge_era_options.
        """
        self._finalize()
        if self._era_plans is not None:
            return self._era_plans
        plans = []
        if self.parent is None:
            pre = self._precommand_plan()
            if pre is not None:
                plans.append(pre)
        for era in self._ordered_precommands():
            owner = self._method_owner.get(id(era))
            if owner is None:                       # a self-method no class
                _refuse_orphan_method(era)          # claimed: refuse by name
            plans.append(self._build(era, method_of=owner))
        flags = self.root._precommand_flags
        for plan in plans:
            plan.share, plan.immediate = flags.get(
                id(plan.callable), (PRECOMMAND, False))
        _merge_era_options(plans)               # one owner per string
        self._era_plans = plans
        return self._era_plans

    def _ordered_precommands(self):
        """
        Registration order, with one adjustment (Larry's wand, 2026-08-25):
        a class precommand runs before any precommand that is a member (method,
        inner class, or BIC) of that class -- a member can't get its instance
        until the class has built it.  Minimal perturbation of registration
        order: a class currently sitting after one of its own members is moved
        to just before its first member.
        """
        order = list(self._precommands)
        owners = self._method_owner
        explicit = self._precommand_explicit
        name = lambda p: getattr(p, '__name__', repr(p))
        for cls in [p for p in order if _is_class_command(p)]:
            key = getattr(cls, '__qualname__', None)
            members = [i for i, p in enumerate(order)
                       if p is not cls and owners.get(id(p)) == key]
            if not members:
                continue
            first = min(members)
            ci = order.index(cls)
            if ci > first:
                # the wand must move the class ahead of its member.  If an
                # explicit index= asked for that member (or the class) to sit
                # where it is, we can't honor both -- nobody wins, we raise
                # (Larry, 2026-08-25).  A purely registration-order clash (no
                # explicit index) hoists silently.
                if (id(cls) in explicit
                        or any(id(order[i]) in explicit
                               for i in members if i < ci)):
                    raise AppealConfigurationError(
                        f"precommand ordering conflict: {name(cls)!r} builds "
                        f"the instance its member {name(order[first])!r} "
                        f"needs, so it must run first, but an explicit index= "
                        f"places the member ahead of it")
                order.pop(ci)
                order.insert(first, cls)
        return order

    def process(self, args=None):
        """
        Parse args (default: sys.argv[1:]) and invoke the command.
        Returns the Processor for this run: its .result is the command's
        return value, .instances the execution log.  (A shortcut for
        Processor(app)(args); see Processor.)  Config is no longer passed
        here -- bind it per precommand via @app.precommand(config=...).
        """
        processor = Processor(self)
        processor(args)
        return processor

    class Era:
        """
        One era of a command line--the private API the dispatcher
        parcels by (Larry, 2026-09-08).  kind: 'head' (a precommand
        era), 'command' (the command word itself--no plan when the
        command takes something; the command's plan when it takes
        nothing), 'help' (the command's help era: -h/--help), 'aoo'
        (the command's arguments-options-opargs era).  plan: the one
        plan the era parses, or None.  share: which neighboring eras
        recognize this era's options--FORWARDS, BACKWARDS, True (both),
        False (neither), or PRECOMMAND (both, but only among
        precommand eras); resolve() settles it against the actual
        neighbors into .forwards/.backwards.  A share with no neighbor
        on that side is harmless.  immediate: executes first, before
        the line is judged.  word/callable: the command, for the step
        and its usage trailer.
        """
        __slots__ = ('kind', 'plan', 'share', 'immediate', 'forwards',
                     'backwards', 'word', 'callable')
        def __init__(self, kind, plan=None, *, share=False, immediate=False,
                     word=None, callable=None):
            self.kind = kind
            self.plan = plan
            self.share = share
            self.immediate = immediate
            self.forwards = self.backwards = False
            self.word = word
            self.callable = callable

        def resolve(self, precommand_before, precommand_after):
            "Settle share against the neighbors: which precommand eras exist."
            share = self.share
            if share == PRECOMMAND:
                self.forwards = precommand_after
                self.backwards = precommand_before
            else:
                self.forwards = share is True or share == FORWARDS
                self.backwards = share is True or share == BACKWARDS
            return self

    def _head_eras(self):
        "The head eras, one per precommand plan, their shares resolved."
        plans = self.global_plans()
        return [self.Era('head', plan, share=plan.share,
                         immediate=plan.immediate)
                .resolve(i > 0, i < len(plans) - 1)
                for i, plan in enumerate(plans)]

    def _help_plan(self):
        """
        This command node's help-era plan: a flag whose presence prints
        the node's page (the node's word path is the topic, so no
        oparg) and exits.  None when the node's policy maps nothing:
        default_mappings=None on the command, the app's
        default_mappings=None, or
        every string already claimed by the command's own options.
        """
        plan = self._plans.get('help')
        if plan is None:
            root = self.root
            if self.parent is None or not root._help_enabled:
                return None
            own = root._plan_for_node(self, self.name)
            claimed = {s for owner, o in own.all_options() for s in o.strings}
            policy = self._command_mappings
            strings = policy(self, claimed) if policy is not None else []
            if not strings:
                return None
            node = self
            path = self._prog().split()[1:]
            def help(*, help=False):
                if help:
                    _sys.exit(root.help(*path))
            help.__qualname__ = f'help({" ".join(path)})'
            root._decorations.add_option(help, 'help', strings)
            plan = self._build(help, name=self._prog())
            plan.share, plan.immediate = FORWARDS, True
            plan = self._plans.setdefault('help', plan)
        return plan

    def _command_eras(self, word):
        """
        The eras a command word opens: the command era (the word; the
        command's plan too when it takes nothing), its help era, and
        its arguments-options-opargs era when it takes something.
        Relay (Larry's rules): a bare command era relays what was
        shared into it iff the command's own share says so; a command
        taking something always relays into its help and
        arguments-options-opargs eras, and that last era shares onward
        iff the command's share says so.
        """
        node = self._children[word]
        callable = self._table()[word]
        plan = self._plan_for_node(node, word)
        takes = bool(plan.slots or plan.options)
        eras = [self.Era('command', None if takes else plan,
                         share=FORWARDS if takes else plan.share,
                         word=word, callable=callable)]
        help_plan = node._help_plan()
        if help_plan is not None:
            eras.append(self.Era('help', help_plan, immediate=True,
                                 share=FORWARDS, word=word, callable=callable))
        if takes:
            eras.append(self.Era('aoo', plan, share=plan.share,
                                 word=word, callable=callable))
        return [era.resolve(False, False) for era in eras]

    def _parcel_era(self, era, line, pos, table, conv=None, conjured=None,
                    ahead=None):
        """
        Parcel one era's tokens (from pos) onto its converter, list its
        step, relay what it shares FORWARDS (its options, and the `--`
        state); returns the new pos.  conv/conjured: the head builds its
        converters and conjure stashes ahead of time, so that `ahead`--
        the next era's (converter, stash), given when this era shares
        BACKWARDS--can have its handlers registered here before this
        era parses.  A structural error raises wearing the right usage
        trailer.
        """
        argv = line.argv
        if era.plan is None:                    # the word of a command that
            return pos                          # takes something: no tokens of
                                                # its own, and it always relays
                                                # into its help/aoo eras
        cls = converter_for(era.plan)
        if conv is None:
            conv = cls()
        proc = backend.Engine(argv[pos:], conv, table,
                              dashdash=line.dashdash, conjured=conjured)
        if era.kind == 'head':
            step = _Step('era', self, cls, conv, proc, plan=era.plan,
                         config=self._precommand_config.get(
                             id(era.plan.callable)),
                         immediate=era.immediate)
        elif era.kind == 'help':
            step = _Step('help', self, cls, conv, proc, immediate=True,
                         word=era.word)
        else:
            step = _Step('command', self, cls, conv, proc,
                         word=era.word, callable=era.callable)
        try:
            proc.enter(conv)
            proc.seed(line.bled)                # shared forwards into here
            if ahead is not None:               # the next era, shared back
                proc.seed(backend.handlers_of(*ahead))
            proc._loop()
            if step.config:
                # config KEY vetting is structural -- fire its refusals
                # in the scan (the value merge is at execute)
                _config_vet(step.plan, frozenset(table), step.config[0],
                            self.plan_for, step.config[1])
        except AppealDataError as e:
            if era.kind == 'head':
                # an era-level error (a bad program-wide option, a config
                # value) wears global usage: the program's usage and its
                # command summary.  It's born in the scan, before any
                # deeper site can speak--help's own errors happen at
                # execute, in pass 2
                assert e.usage is None
                e.usage = _global_trailer(self)
            else:
                step.attach_usage(e)
            raise
        pos += proc.consumed                    # the whole era's tokens
        line.dashdash = proc.force_positional if era.forwards else False
        line.bled = proc.handlers if era.forwards else {}
        line.tried = proc.handlers
        line.forced = proc.force_positional
        line.steps.append(step)
        return pos

    def _run_node(self, line, pos, top):
        """
        Parcel and scan one set node's eras--its head eras, then the
        eras each command word opens--appending the steps to execute;
        recurse for subcommands.  Runs no user code: converters are
        instantiated (Appeal's classes) and tokens are parceled onto
        them as records.  Returns (dispatched, pos): whether a command
        word of THIS node was parceled, and where the node's tokens end.
        A structural error raises (the caller notes it: pass 2 raises it
        after the immediate eras run).
        """
        self._finalize()
        argv = line.argv
        steps = line.steps
        table = self._table()

        # the head: converters and conjure stashes built ahead, so an era
        # whose successor shares BACKWARDS can register the successor's
        # handlers before it parses
        eras = self._head_eras()
        convs = [converter_for(era.plan)() for era in eras]
        stashes = [{} for era in eras]
        for i, era in enumerate(eras):
            ahead = None
            if i + 1 < len(eras) and eras[i + 1].backwards:
                ahead = (convs[i + 1], stashes[i + 1])
            pos = self._parcel_era(era, line, pos, table, convs[i],
                                   stashes[i], ahead)

        dispatched = False              # did a command word of THIS node run?
        while pos < len(argv):
            word = argv[pos]
            if word == '--' and not line.dashdash:
                # `--` where a command word goes (a line with no head era,
                # say): consumed, and the next token is the word.  It forces
                # nothing beyond that--click's rule: the word's own eras
                # start fresh (Larry, 2026-09-08)
                pos += 1
                continue
            if word not in table:
                # command names are dash-form (my_cmd -> my-cmd); accept the
                # underscore spelling too, exact match first so an explicitly
                # underscore-named command still wins.
                alt = word.replace('_', '-')
                if alt != word and alt in table:
                    word = alt
                elif not top:
                    return dispatched, pos      # pop back: a parent may own it
                else:
                    dash = word.startswith('-') and not line.forced
                    if not dash and not table:
                        # a program with no commands: one word too many
                        err = UsageError(f"unexpected argument {word!r}")
                    else:
                        err = _unexpected(word, line.tried if dash else table,
                                          line.forced,
                                          self.root._option_placements(line.path))
                    # outside any command's era: global usage (the overview
                    # page when there are commands, the usage line alone
                    # when there aren't)
                    err.usage = _global_trailer(self)
                    raise err
            depth = len(self._prog().split()) - 1     # root: 0
            line.path[depth:] = [word]                # this set's word, replacing
                                                      # a cycling set's previous
            pos += 1                                  # the word itself
            for era in self._command_eras(word):
                pos = self._parcel_era(era, line, pos, table)
            dispatched = True

            # recurse into the command's subcommand node: it may dispatch a
            # subcommand OR (the line stops at the parent) run that node's
            # default command -- so recurse even at end-of-line when a default
            # is waiting.  Every command has a child node (lazy registration);
            # only enter one that actually has subcommands or a default.
            child = self._children.get(word)
            if child is not None and (child._has_commands
                                      or child._default is not None):
                _, pos = child._run_node(line, pos, top=False)
            if not self._node_repeat and pos < len(argv):
                # this set doesn't cycle: pop the leftover word up to an
                # ancestor whose set does (the parent's loop re-dispatches it);
                # at the top with nothing to claim it, it's unexpected
                if not top:
                    return dispatched, pos
                tok = argv[pos]
                dash = tok.startswith('-') and not line.forced
                deepest = self.root._node_at(line.path)
                if dash:
                    # an option nobody owned: the last era's strings suggest
                    err = _unexpected(tok, line.tried, line.forced,
                                      self.root._option_placements(line.path))
                    err.usage = _global_trailer(self)
                elif deepest._has_commands:
                    # the deepest command dispatched has subcommands, and this
                    # isn't one of them
                    err = UsageError(
                        f"unknown command {tok!r} of {deepest._prog()!r}"
                        f"{did_you_mean(tok, deepest._table())}")
                    err.usage = _overview_trailer(deepest)
                else:
                    # the deepest command took all it can: one word too many
                    # (that the word happens to be a command's is no help to
                    # the user--Larry, 2026-09-08)
                    err = UsageError(f"unexpected argument {tok!r}")
                    err.usage = _line_trailer(self.stylesheet,
                                              deepest._head_usage_markup())
                raise err

        if not dispatched:
            # the line stopped at this node without naming a subcommand of
            # it: the node's default handler runs, after the head eras (the
            # root) or after the parent's body (below)--the stock root one
            # prints the program's usage and command summary (Larry,
            # 2026-09-09); below, the stock is Appeal(default_subcommand=),
            # None: subcommands are optional (ruled 2026-08-22).
            # A handler applies only where there are commands to be missing.
            # (It runs in pass 3 like any command, so what came before it
            # on the line--the global command included--has already run.)
            handler = self._default
            if handler is not None and self._has_commands:
                dcls = converter_for(self._default_plan())
                dconv = dcls()
                dproc = backend.Engine(argv[pos:], dconv, table,
                                       dashdash=line.dashdash)
                dproc.parse()
                pos += dproc.consumed
                steps.append(_Step('default', self, dcls, dconv, dproc))
        return dispatched, pos

    def main(self, args=None):
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
            from .completion import completion_reentry
            _sys.exit(completion_reentry(
                lambda words, prefix: self.complete(words, prefix),
                self._prog()))
        # the one engine (2026-08-22): main() drives the same in-memory dispatch
        # process() does (config layering included).  A help/version precommand
        # prints then sys.exit()s; run_main catches that and converts to a code.
        parse = lambda argv: Processor(self)(list(argv))
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
        from .load import read_mapping
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
        from .load import read_mapping
        from .mcp import run_mcp
        from .presentation import summary
        from .schema import mcp_input_schema
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
        # errors ride the pipeline here too: color them (or strip, off a tty)
        # exactly as main() does, against stdout (where the REPL prints)
        from .presentation import resolve_stylesheet, style
        sheet = resolve_stylesheet(self.stylesheet, _sys.stdout)
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
            # the session survives everything except the user leaving
            # (ruled 2026-09-03, the Sol review): a command's failure,
            # its bugs, and the help machinery's sys.exit all print
            # and CONTINUE--quit and ^D are the only doors out.
            try:
                result = self.process(words).result
            except AppealDataError as e:
                print(f"{sheet.render(style('error', 'error:'))} {e}")
                # the dispatch boundary attaches a trailer to every
                # escaping data error (restored 2026-09-06); a falsy
                # one here is an off-contract usage= from user code,
                # and that crashes loudly rather than being absorbed
                assert e.usage is not None
                text = e.usage(_sys.stdout)         # the REPL prints to stdout
                if text:
                    print(text)
            except AppealConfigurationError as e:
                print(f"{sheet.render(style('error', 'configuration error:'))} {e}")
            except AppealError as e:
                # CommandError and kin: the command said no.  Its exit
                # code means nothing to a session that isn't exiting.
                print(f"{sheet.render(style('error', 'error:'))} {e}")
            except SystemExit:
                pass        # -h/--version printed their page already
            except KeyboardInterrupt:
                print()
            except Exception:
                # an ordinary bug in a command: the traceback is the
                # useful part--print it like Python's own REPL would
                import traceback
                traceback.print_exc()
            else:
                # a value the command RETURNED, printed exactly as
                # print() would (ruled: this is the program's REPL,
                # not Python's--no repr, no quotes)
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
        from .completion import completion_script
        return completion_script(shell, self._prog())


# the back end (appeal/backend.py): the Plan consumer -- build_converters
# and the Engine.  Imported here at the end so its `from . import`
# of the exceptions/vocabulary above resolves; gives the dispatch methods
# their names.  The Engine is NOT re-exported (it's internal machinery, not
# public API); the dispatch methods reach it as backend.Engine.
from . import backend
from .backend import (
    execute, build_converters, converter_for,
    _halts, _unexpected, Converter,
    )


# these lines run ONLY on Python 3.6, where coverage never runs; the
# real-3.6 test runs exercise them (hence the pragma on the if below)
if _sys.version_info < (3, 7):      # pragma: no cover
    # module-level __getattr__ (PEP 562) is 3.7+; on 3.6 it's never called,
    # so `from appeal import build_plan` (and the other lazy re-exports) would
    # fail.  Bind them eagerly here instead--at the cost of importing their
    # modules now, which on this legacy interpreter is a fair trade.
    for _name in _LAZY_REEXPORTS:
        globals()[_name] = __getattr__(_name)
