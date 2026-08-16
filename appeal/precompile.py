#
# appeal/precompile.py
# The Appeal class a compiled parser module exports.
#
# A precompiled module (emitted by codegen.emit_precompiled_module)
# does `import appeal` for the runtime, bakes its parser tables and
# fingerprints, and ends with
#
#     _SPEC = {...}
#     Appeal = compiled_appeal(_SPEC, globals())
#
# so the program that uses it is the documented spelling, unchanged:
#
#     try:
#         import compiled as appeal
#     except ImportError:
#         import appeal
#
# Appeal() and the decorators here don't BUILD anything: they MATCH
# the live functions to the precompiled bits by fingerprint (all-or-
# nothing verification at main(); any drift is a loud regenerate
# error naming every offender).  The heavy import (inspect/build)
# never happens--this module is stdlib-plus-appeal.runtime only.
#
# This module is imported LAZILY, by the compiled module, never by
# appeal/__init__.py.

import sys

from .runtime import (
    AppealConfigurationError, fingerprint, decoration_fingerprint,
    resolve_fingerprint_path, _stable_repr, run_main,
    )

_OPTION_UNSET = object()      # option(default=...) omitted marker
_KNOB_UNSET = object()        # constructor knob omitted marker


def compiled_appeal(spec, namespace):
    "Build the Appeal class a compiled module exports."

    class _NotCompiled(AppealConfigurationError, AttributeError):
        # both: loud and named for the user, honest to the
        # attribute protocol (hasattr/getattr probes see an
        # AttributeError, not a lie)
        pass

    class Appeal:
        def __init__(self, name=None, *,
                     stylesheet=None, version=None, repeat=False,
                     errors=None, script=None, margin=79,
                     positional_argument_usage_format=None,
                     default_options=_KNOB_UNSET,
                     default_mappings=_KNOB_UNSET,
                     doc=None):
            # live knobs: these never touched the baked grammar
            # or pieces, so they simply apply, custom values and
            # all (stylesheet compositions and custom error
            # streams are LEGAL here--everything is runtime)
            self.stylesheet = stylesheet
            self.errors = errors
            self.templates = spec['templates']
            # baked knobs: the grammar and help were derived from
            # these; a different value now means a stale parser
            self._staleness = []
            for knob, value in (('name', name),
                                ('version', version),
                                ('repeat', repeat),
                                ('margin', margin),
                                ('positional_argument_usage_format',
                                 positional_argument_usage_format),
                                ('doc', doc)):
                baked = spec['config'][knob]
                if value is None and knob != 'repeat':
                    continue        # unspecified: the baked value
                if _stable_repr(value) != baked:
                    self._staleness.append(
                        f"Appeal({knob}=...): compiled with "
                        f"{baked}, now {_stable_repr(value)}")
            self._bound = {}        # id(spec entry) -> binding
            # what @app.option/@app.parameter expressed, keyed by
            # the decorated callable--recorded HERE, never on the
            # user's objects (ruled 2026-08-09)
            self._option_overrides = {}
            self._parameter_usage = {}

        # -- registration: match, don't build --------------------
        # the spec is the command TREE; command('db') returns a
        # child node over the baked subtree, wearing the same API
        # (the real facade's tree-of-Appeals shape)

        def _bind(self, entry, label, fn):
            # re-registration replaces (v1: the second wins)
            self._bound[id(entry)] = (entry, label, fn)

        def _command_in(self, table, prefix, name, repeat, parent):
            if parent is not None:
                if name is not None:
                    raise AppealConfigurationError(
                        "command(): give a name or parent=, "
                        "not both")
                name = parent
            def fetch(word):
                entry = table.get(word)
                if entry is None:
                    known = ', '.join(sorted(table)) or '(none)'
                    where = (f"under {prefix!r}" if prefix
                             else "at the top level")
                    raise AppealConfigurationError(
                        f"this compiled parser has no command "
                        f"{word!r} {where} (it knows: {known}); "
                        f"regenerate the compiled module")
                if repeat and not entry.get('repeat'):
                    self._staleness.append(
                        f"command {word!r}: repeat=True now, but "
                        f"this parser was compiled without it")
                return entry
            def label(word):
                return (prefix + ' ' + word).strip()
            if name is not None:
                return _Node(self, fetch(name), label(name))
            def decorator(fn):
                word = getattr(fn, '__name__', None)
                self._bind(fetch(word), label(word), fn)
                return fn
            return decorator

        def subcommand(self, parent, name=None, *, repeat=False):
            # the explicit spelling (ruled 2026-08-10): parent is
            # a word path string or None; the baked spec IS the
            # resolved tree, so the path checks immediately
            if parent is None:
                return self.command(name, repeat=repeat)
            if not isinstance(parent, str):
                raise AppealConfigurationError(
                    f"subcommand: the parent is a command word "
                    f"path (a string) or None, not {parent!r}")
            table = spec['commands']
            label = ''
            for word in parent.split():
                entry = table.get(word)
                if entry is None:
                    raise AppealConfigurationError(
                        f"this compiled parser has no command at "
                        f"path {parent!r}; regenerate the "
                        f"compiled module")
                label = (label + ' ' + word).strip()
                table = entry.get('commands') or {}
            return self._command_in(table, label, name, repeat,
                                    None)

        def command(self, name=None, *, repeat=False, parent=None):
            return self._command_in(spec['commands'], '',
                                    name, repeat, parent)

        def default_command(self):
            def register(fn):
                if spec.get('default') is None:
                    raise AppealConfigurationError(
                        "this compiled parser has no root default "
                        "command; regenerate the compiled module")
                self._bind(spec['default'], '<default>', fn)
                return fn
            return register

        def global_command(self):
            def register(fn):
                if spec['global'] is None:
                    raise AppealConfigurationError(
                        "this compiled parser has no global "
                        "command; regenerate the compiled module")
                self._bind(spec['global'], '<global>', fn)
                return fn
            return register

        def option(self, parameter_name, *strings,
                   annotation=None, default=_OPTION_UNSET):
            # records IN THE APP, exactly like the real facade
            # (ruled 2026-08-09: the decorated object is never
            # touched); the decoration fingerprint compares this
            # replay against what the parser was baked with, so a
            # changed @option call reads as staleness at main().
            # Sentinels mirror the facade: annotation=None means
            # "the parameter's own"; default omitted means
            # Parameter.empty; an explicit default=None is a real
            # None.
            from inspect import Parameter
            annotation = (Parameter.empty if annotation is None
                          else annotation)
            default = (Parameter.empty if default is _OPTION_UNSET
                       else default)
            def register(fn):
                declaration = {'strings': tuple(strings),
                               'annotation': annotation,
                               'default': default}
                overrides = self._option_overrides.setdefault(fn, {})
                declarations = overrides.setdefault(parameter_name,
                                                    [])
                if declaration not in declarations:
                    declarations.append(declaration)
                return fn
            return register

        def parameter(self, parameter_name, *, usage=None):
            def register(fn):
                names = self._parameter_usage.setdefault(fn, {})
                names[parameter_name] = usage
                return fn
            return register
        argument = parameter            # v1's deprecated alias

        # -- verification: all-or-nothing, at main() -------------

        def _walk_spec(self):
            """Every entry in the baked tree, with what to call it."""
            out = []
            if spec['global'] is not None:
                out.append((spec['global'], 'the global command'))
            if spec.get('default') is not None:
                out.append((spec['default'], 'the default command'))
            def walk(table, prefix):
                for word, entry in table.items():
                    label = (prefix + ' ' + word).strip()
                    out.append((entry, f'command {label!r}'))
                    if entry.get('default') is not None:
                        out.append((entry['default'],
                                    f'the default command of '
                                    f'{label!r}'))
                    walk(entry.get('commands') or {}, label)
            walk(spec['commands'], '')
            return out

        def _verify_and_bind(self):
            problems = list(self._staleness)
            known = set()       # everything this parser resolves
            for entry, what in self._walk_spec():
                binding = self._bound.get(id(entry))
                if binding is None:
                    problems.append(
                        f"{what} was compiled in but never "
                        f"registered with @app.command()")
                    continue
                _, _, fn = binding
                known.add(fn)
                if fingerprint(fn) != entry['fingerprint']:
                    problems.append(
                        f"{what} has changed since this parser "
                        f"was compiled (signature, defaults, "
                        f"annotations, or docstring)")
                    continue
                if decoration_fingerprint(
                        fn, self._option_overrides,
                        self._parameter_usage) != entry['decorations']:
                    problems.append(
                        f"{what}: its @app.option/@app.parameter "
                        f"decorations have changed since this "
                        f"parser was compiled")
                    continue
                known.add(fn)
                for ref_name, path, ref_fpr, ref_decor in entry['refs']:
                    try:
                        obj = resolve_fingerprint_path(
                            fn, path, self._option_overrides)
                    except Exception:
                        problems.append(
                            f"{what}: converter for {ref_name!r} "
                            f"can't be resolved from the live "
                            f"function")
                        continue
                    known.add(obj)
                    if (ref_fpr is not None
                            and fingerprint(obj) != ref_fpr):
                        problems.append(
                            f"{what}: converter "
                            f"{getattr(obj, '__name__', ref_name)!r} "
                            f"has changed since this parser was "
                            f"compiled")
                        continue
                    if decoration_fingerprint(
                            obj, self._option_overrides,
                            self._parameter_usage) != ref_decor:
                        problems.append(
                            f"{what}: the @app.option/@app.parameter "
                            f"decorations of converter "
                            f"{getattr(obj, '__name__', ref_name)!r} "
                            f"have changed since this parser was "
                            f"compiled")
                        continue
                    namespace[ref_name] = obj
                if entry['impl'] is not None:
                    namespace[entry['impl']] = fn
            # a decoration aimed at something this parser never
            # resolves is drift too--yell, don't ignore
            for registry in (self._option_overrides,
                             self._parameter_usage):
                for target in registry:
                    if target not in known:
                        problems.append(
                            f"@app.option/@app.parameter decorates "
                            f"{getattr(target, '__name__', target)!r}, "
                            f"which this compiled parser doesn't "
                            f"know")
            if _stable_repr(self.templates) != spec['config']['templates']:
                problems.append(
                    "app.templates has changed since this parser "
                    "was compiled")
            if problems:
                bullets = '\n'.join(f'  - {p}' for p in problems)
                raise AppealConfigurationError(
                    f"stale compiled parser "
                    f"({spec['program']}):\n{bullets}\n"
                    f"Regenerate it: run this program against "
                    f"installed appeal (delete or ignore the "
                    f"compiled module) and call "
                    f"app.precompile(path=...) again.")

        # -- running ---------------------------------------------

        def main(self, args=None):
            self._verify_and_bind()
            parse = namespace[spec['entry']]
            complete = spec.get('complete')
            # the table factory evaluates NOW, with every slot
            # bound--a module-exec-time table would hold the Nones
            completion = ((namespace[complete](), spec['program'])
                          if complete and complete in namespace
                          else None)
            sys.exit(run_main(parse, args,
                              stylesheet=self.stylesheet,
                              completion=completion,
                              errors=self.errors,
                              version=spec['config'].get('version_value'),
                              margin=spec['config']['margin_value']))

        # -- the honest refusals ---------------------------------

        def precompile(self, path=None, **kwargs):
            raise AppealConfigurationError(
                "this IS the compiled parser; to regenerate, run "
                "the program against installed appeal (the "
                "try/except import falls through when the "
                "compiled module is absent)")

        def __getattr__(self, name):
            raise _NotCompiled(
                f"Appeal.{name} isn't part of this compiled "
                f"parser; if the program needs it, regenerate "
                f"with a current appeal (in-process-only APIs "
                f"never compile)")

    class _Node:
        """
        A compiled subtree wearing the child-Appeal API: callable
        (registers the parent's own function), .command() for its
        children, .default_command(), and the decoration
        decorators delegating to the root (one registry per
        tree, like the real facade).
        """
        def __init__(self, shim, entry, label):
            self._shim = shim
            self._entry = entry
            self._label = label

        def __call__(self, fn):
            self._shim._bind(self._entry, self._label, fn)
            return fn

        def command(self, name=None, *, repeat=False, parent=None):
            return self._shim._command_in(
                self._entry.get('commands') or {}, self._label,
                name, repeat, parent)

        def default_command(self):
            entry = self._entry.get('default')
            def register(fn):
                if entry is None:
                    raise AppealConfigurationError(
                        f"this compiled parser has no default "
                        f"command under {self._label!r}; "
                        f"regenerate the compiled module")
                self._shim._bind(entry,
                                 f'{self._label} <default>', fn)
                return fn
            return register

        def option(self, *args, **kwargs):
            return self._shim.option(*args, **kwargs)

        def parameter(self, *args, **kwargs):
            return self._shim.parameter(*args, **kwargs)
        argument = parameter

    return Appeal
