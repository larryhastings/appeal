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
    AppealConfigurationError, UsageError, fingerprint,
    decoration_fingerprint, resolve_fingerprint_path, _stable_repr,
    run_main,
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
                if _stable_repr(value) != _stable_repr(baked):
                    self._staleness.append(
                        f"Appeal({knob}=...): compiled with "
                        f"{_stable_repr(baked)}, now {_stable_repr(value)}")
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

        # the shim walks the runtime Command TREE (ruled 2026-08-17):
        # the manifest names it, and each Command carries its own
        # fingerprint/options/arguments/converters.  These resolve it
        # against the module globals.

        def _cmd_table(self):
            "The top-level {word: Command} dispatch table."
            name = spec.get('commands')
            return namespace[name] if name else {}

        def _global_cmd(self):
            name = spec.get('global')
            return namespace[name] if name else None

        def _default_cmd(self):
            name = spec.get('default')
            return namespace[name] if name else None

        def _command_in(self, table, prefix, name, repeat, parent):
            if parent is not None:
                if name is not None:
                    raise AppealConfigurationError(
                        "command(): give a name or parent=, "
                        "not both")
                name = parent
            def fetch(word):
                cmd = table.get(word)
                if cmd is None or cmd.fingerprint is None:
                    known = ', '.join(sorted(
                        w for w, c in table.items()
                        if c.fingerprint is not None)) or '(none)'
                    where = (f"under {prefix!r}" if prefix
                             else "at the top level")
                    raise AppealConfigurationError(
                        f"this compiled parser has no command "
                        f"{word!r} {where} (it knows: {known}); "
                        f"regenerate the compiled module")
                if repeat and not cmd.repeat:
                    self._staleness.append(
                        f"command {word!r}: repeat=True now, but "
                        f"this parser was compiled without it")
                return cmd
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
            # a word path string or None; the baked Command tree IS
            # the resolved tree, so the path checks immediately
            if parent is None:
                return self.command(name, repeat=repeat)
            if not isinstance(parent, str):
                raise AppealConfigurationError(
                    f"subcommand: the parent is a command word "
                    f"path (a string) or None, not {parent!r}")
            table = self._cmd_table()
            label = ''
            for word in parent.split():
                cmd = table.get(word)
                if cmd is None:
                    raise AppealConfigurationError(
                        f"this compiled parser has no command at "
                        f"path {parent!r}; regenerate the "
                        f"compiled module")
                label = (label + ' ' + word).strip()
                table = cmd.subcommands
            return self._command_in(table, label, name, repeat,
                                    None)

        def command(self, name=None, *, repeat=False, parent=None):
            return self._command_in(self._cmd_table(), '',
                                    name, repeat, parent)

        def default_command(self):
            def register(fn):
                cmd = self._default_cmd()
                if cmd is None:
                    raise AppealConfigurationError(
                        "this compiled parser has no root default "
                        "command; regenerate the compiled module")
                self._bind(cmd, '<default>', fn)
                return fn
            return register

        def global_command(self):
            def register(fn):
                cmd = self._global_cmd()
                if cmd is None:
                    raise AppealConfigurationError(
                        "this compiled parser has no global "
                        "command; regenerate the compiled module")
                self._bind(cmd, '<global>', fn)
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
            """Every verifiable Command in the tree, with its label."""
            out = []
            g = self._global_cmd()
            if g is not None:
                out.append((g, 'the global command'))
            d = self._default_cmd()
            if d is not None:
                out.append((d, 'the default command'))
            def walk(table, prefix):
                for word, cmd in table.items():
                    if cmd.fingerprint is None:
                        continue    # the fused version/help autos
                    label = (prefix + ' ' + word).strip()
                    out.append((cmd, f'command {label!r}'))
                    if cmd.default is not None:
                        out.append((cmd.default,
                                    f'the default command of '
                                    f'{label!r}'))
                    walk(cmd.subcommands, label)
            walk(self._cmd_table(), '')
            return out

        def _verify_and_bind(self):
            problems = list(self._staleness)
            known = set()       # everything this parser resolves
            for cmd, what in self._walk_spec():
                binding = self._bound.get(id(cmd))
                if binding is None:
                    problems.append(
                        f"{what} was compiled in but never "
                        f"registered with @app.command()")
                    continue
                _, _, fn = binding
                known.add(fn)
                if fingerprint(fn) != cmd.fingerprint:
                    problems.append(
                        f"{what} has changed since this parser "
                        f"was compiled (signature, defaults, "
                        f"annotations, or docstring)")
                    continue
                if decoration_fingerprint(
                        fn, self._option_overrides,
                        self._parameter_usage) != (cmd.options,
                                                   cmd.arguments):
                    problems.append(
                        f"{what}: its @app.option/@app.argument "
                        f"decorations have changed since this "
                        f"parser was compiled")
                    continue
                known.add(fn)
                for ref_name, path, ref_fpr, ref_decor in cmd.converters:
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
                # bind the live function onto its Command (the entry
                # in the walk IS the Command object)
                cmd.callable = fn
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
            if _stable_repr(self.templates) != _stable_repr(spec['config']['templates']):
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
            # fallback=self: the compiled parser bakes no help/usage,
            # so run_main routes help (_CompiledHelp) and the error
            # family (tagged with their command) back here, and we
            # render them LIVE through full Appeal
            version = spec['config'].get('version')
            sys.exit(run_main(parse, args,
                              stylesheet=self.stylesheet,
                              completion=completion,
                              errors=self.errors,
                              version=None if version is None else str(version),
                              margin=spec['config']['margin'],
                              fallback=self))

        # -- rendering help and errors live, through full Appeal ---
        # the compiled module holds no help text; on a help request
        # or a help-rendering error, we reconstruct the real Appeal
        # from the registrations we recorded and let IT render.  We
        # never re-run the command line (a command may already have
        # had side effects before a later one failed).

        def _rebuild(self):
            "A real appeal.Appeal, replayed from the recorded registrations."
            import appeal
            from inspect import Parameter
            cfg = spec['config']
            real = appeal.Appeal(
                name=cfg.get('name'),
                version=cfg.get('version'),
                repeat=cfg.get('repeat', False),
                margin=cfg.get('margin', 79),
                positional_argument_usage_format=cfg.get(
                    'positional_argument_usage_format'),
                doc=cfg.get('doc'),
                stylesheet=self.stylesheet)

            def bound(entry):
                b = self._bound.get(id(entry)) if entry else None
                return b[2] if b else None

            def replay_decorations(fn):
                for param, decls in self._option_overrides.get(
                        fn, {}).items():
                    for decl in decls:
                        kw = {}
                        ann = decl['annotation']
                        kw['annotation'] = (None if ann is Parameter.empty
                                            else ann)
                        if decl['default'] is not Parameter.empty:
                            kw['default'] = decl['default']
                        real.option(param, *decl['strings'], **kw)(fn)
                for param, usage in self._parameter_usage.get(
                        fn, {}).items():
                    real.parameter(param, usage=usage)(fn)

            def register(fn, decorator):
                if fn is not None:
                    decorator(fn)
                    replay_decorations(fn)

            register(bound(self._global_cmd()), real.global_command())
            register(bound(self._default_cmd()), real.default_command())

            def walk(node, table):
                for word, cmd in table.items():
                    if cmd.fingerprint is None:
                        continue    # the fused version/help autos
                    child = node.command(word)
                    register(bound(cmd), child)
                    register(bound(cmd.default), child.default_command())
                    if cmd.subcommands:
                        walk(child, cmd.subcommands)
            walk(real, self._cmd_table())
            return real

        def _topic_for(self, fn):
            "The help topic (word path) of a bound command function."
            if fn is None:
                return ''
            for entry, label, bound in self._bound.values():
                if bound is fn:
                    # <global>/<default> are the program root
                    return '' if label.startswith('<') else label
            return ''

        def on_help(self, signal):
            real = self._rebuild()
            cmd = signal.command
            real.help(self._topic_for(cmd.callable if cmd else None))
            # 0 for requested help; 1 for a bare set line's listing
            return signal.code

        def _command_usage(self, real, fn, want_listing):
            "The usage of the reconstructed command whose callable is fn."
            if fn is None:
                return None
            # match by IDENTITY (not word): finds the node even for a
            # nested subcommand, and never trips bare-word ambiguity
            def walk(node):
                for word, child in node._children.items():
                    if child._command_callable() is fn:
                        return child, word
                    hit = walk(child)
                    if hit is not None:
                        return hit
                return None
            found = walk(real)
            if found is None:
                return None
            node, word = found
            parent_plan = real._plan_for_node(node, word)
            if not (node._children and want_listing):
                # the command's OWN usage line--a plain command, or a
                # parent that failed on its own operands/options (a
                # class command is both a constructor and a parent)
                return parent_plan.usage()
            # a "no command"/"unknown command" error UNDER this set:
            # its terse listing (usage line + the Commands table),
            # exactly as emit_command_set bakes it
            from .help import summary, command_set_corpus
            from .plan import command_set_usage
            from .render import listing_pieces
            sub_entries = [(w, summary(c._command_callable()))
                           for w, c in node._children.items()
                           if c._command_callable() is not None]
            # a nested set has no auto help/version row of its own
            # (those live at the root)--auto_help=False
            corpus = command_set_corpus(parent_plan, sub_entries,
                                        False)
            return listing_pieces(
                command_set_usage(word, parent_plan), corpus,
                real.templates)

        def on_usage(self, e):
            real = self._rebuild()
            cmd = e.command
            usage = self._command_usage(
                real, cmd.callable if cmd else None,
                getattr(e, 'want_listing', False))
            if usage is None:
                table = real._table()
                if not table:
                    # a bare app: the global command's own usage
                    usage = real.plan.usage()
                else:
                    # the program root of a set: the terse listing
                    from .help import summary, command_set_corpus
                    from .plan import command_set_usage
                    from .render import listing_pieces
                    entries = [(w, summary(c))
                               for w, c in table.items()]
                    corpus = command_set_corpus(
                        real.global_plan, entries, False,
                        auto_version=False)
                    usage = listing_pieces(
                        command_set_usage(real._prog(),
                                          real._display_global()),
                        corpus, real.templates)
            # reuse run_main's error formatting (colorized prefix +
            # usage) by handing it a stub that just re-raises--no
            # re-parse, no re-dispatch
            message = str(e)

            def _reraise(_argv):
                raise UsageError(message, usage, param=e.param)
            return run_main(_reraise, [],
                            stylesheet=self.stylesheet,
                            errors=self.errors,
                            margin=spec['config']['margin'])

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
                self._entry.subcommands, self._label,
                name, repeat, parent)

        def default_command(self):
            entry = self._entry.default
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
