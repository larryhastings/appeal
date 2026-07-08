#!/usr/bin/env python3
#
# appeal/help.py
# Part of Appeal v2.
# Copyright 2021-2026 by Larry Hastings
#
# The build-time half of help: docstring parsing and resolving
# entries against the plan.  The run-time half--render_help_page
# and friends--lives in runtime.py, the file streamed into standalone
# scripts.

import inspect as _inspect
import re as _re

from .plan import Terminal
from .runtime import AppealConfigurationError


##
## The docstring parser (proposal §8.7, as ruled in §8.7.1).
##
## A docstring is input, never output: section headings and their
## entries are slurped out, the remaining prose coalesces into one
## blob, and the templates own the output's structure entirely.
##

# heading line -> corpus section.  Exact spellings, whole line.
# Commands: reads right on a global command, Subcommands: on a
# command with subcommands; the doc machinery treats them
# identically.
_HEADINGS = {
    'Arguments:': 'arguments',
    'Options:': 'options',
    'Commands:': 'commands',
    'Subcommands:': 'commands',
}

_FORBIDDEN_HEADINGS = {
    'Sub-commands:': 'Subcommands:',
}

# the first line of an entry: 'name:' or 'name: text'
_ENTRY_START_RE = _re.compile(r'^([A-Za-z_][A-Za-z0-9_]*):(?:\s+(\S.*))?$')


def _dedent_lines(lines):
    """
    Strips the common leading whitespace from lines, preserving
    their relative indentation (kid gloves: an indented code line
    stays indented relative to its siblings).  Ignores blank lines
    when measuring--though the parser never sends any; a blank
    line ends a section.
    """
    margin = None
    for line in lines:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if (margin is None) or (indent < margin):
            margin = indent
    if not margin:
        return list(lines)
    return [line[margin:] if line.strip() else line for line in lines]


def parse_docstring(doc, where):
    """
    Parses one docstring into corpus pieces.  Returns a dict:

        summary        the first paragraph of the prose, as lines
        documentation  the rest of the prose, as lines--the
                       coalesced blob, in source order, with the
                       sections slurped out
        arguments      dict of name -> documentation lines
        options        dict of name -> documentation lines
        commands       dict of name -> documentation lines

    The grammar: a section heading is a line that is exactly
    'Arguments:', 'Options:', 'Commands:', or 'Subcommands:'.
    A section runs from its heading to the first blank line.
    Entries are 'name: text' lines indented under the heading, all
    at the same indent; deeper-indented lines continue an entry.
    Entry text is kept as dedented lines, never flattened.  One
    section per kind.  Everything else is prose.

    'where' names the docstring's owner, for error messages.
    Grammar violations raise AppealConfigurationError; validation
    against the plan (unknown names, kinds, visibility) happens
    later, in the merge.
    """
    sections = {'arguments': {}, 'options': {}, 'commands': {}}
    seen = {}
    prose = []

    lines = (doc or '').splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        replacement = _FORBIDDEN_HEADINGS.get(line.strip())
        if replacement:
            raise AppealConfigurationError(
                f"{where}: use {replacement!r}, not {line.strip()!r}")
        kind = _HEADINGS.get(line)
        if kind is None:
            stripped = line.strip()
            if stripped in _HEADINGS:
                raise AppealConfigurationError(
                    f"{where}: section headings must be exact: "
                    f"{stripped!r} on a line by itself, no extra whitespace")
            prose.append(line)
            i += 1
            continue

        # a section heading.
        if kind in seen:
            raise AppealConfigurationError(
                f"{where}: duplicate {line!r} section"
                + (f" ({seen[kind]!r} is the same section)"
                   if seen[kind] != line else ""))
        seen[kind] = line
        entries = sections[kind]
        i += 1

        # the section runs to the first blank line.
        entry_indent = None
        name = None
        first_text = None
        continuation = None

        def close_entry():
            if name is None:
                return
            text = [first_text] if first_text else []
            text.extend(_dedent_lines(continuation))
            entries[name] = text

        while i < n:
            body_line = lines[i]
            if not body_line.strip():
                break
            stripped = body_line.lstrip()
            indent = len(body_line) - len(stripped)
            if not indent:
                raise AppealConfigurationError(
                    f"{where}: in the {line!r} section: {body_line!r} "
                    f"isn't indented.  Entries are indented under the "
                    f"heading; only a blank line ends the section.")
            m = _ENTRY_START_RE.match(stripped)
            if entry_indent is None:
                if not m:
                    raise AppealConfigurationError(
                        f"{where}: in the {line!r} section: expected a "
                        f"'name: documentation' entry, got {body_line!r}")
                entry_indent = indent
            if indent > entry_indent:
                continuation.append(body_line)
            elif (indent == entry_indent) and m:
                close_entry()
                name = m.group(1)
                if name in entries:
                    raise AppealConfigurationError(
                        f"{where}: in the {line!r} section: "
                        f"{name!r} is documented twice")
                first_text = m.group(2)
                continuation = []
            else:
                raise AppealConfigurationError(
                    f"{where}: in the {line!r} section: expected a "
                    f"'name: documentation' entry or a deeper-indented "
                    f"continuation, got {body_line!r}")
            i += 1
        close_entry()

    # the prose coalesces into one blob: collapse the runs of
    # blank lines the slurped-out sections left behind, and strip
    # the ends.
    blob = []
    for line in prose:
        if line.strip():
            blob.append(line)
        elif blob and blob[-1].strip():
            blob.append('')
    while blob and not blob[-1].strip():
        blob.pop()

    # the summary is the first paragraph; the documentation is
    # the rest.
    try:
        split = blob.index('')
        summary, documentation = blob[:split], blob[split + 1:]
    except ValueError:
        summary, documentation = blob, []

    return {
        'summary': summary,
        'documentation': documentation,
        'arguments': sections['arguments'],
        'options': sections['options'],
        'commands': sections['commands'],
    }


def merge_docs(plan, command_names=None):
    """
    The merge (proposal §8.7.1): walks the plan tree, parses every
    callable's docstring, and layers the entries--only entries
    merge; prose and summaries stay home--with the nearest
    enclosing scope winning, so a command overrides what it
    inherits from its converters.

    Each docstring's entries are validated against the subtree of
    the callable that wrote them: an entry must name a visible
    argument (a terminal--including a keyword-only-no-default
    parameter, which is a trailing operand), an option, or (for
    'Commands:' entries, only when command_names is given) a
    command word.  Violations raise AppealConfigurationError.

    Returns the corpus, predigested and ready to format:

        summary        the command's own summary, as lines
        documentation  the command's own prose blob, as lines
        arguments      [(display, lines), ...] in plan order
        options        [(display, lines), ...] in plan order
        commands       [(word, lines), ...] if command_names,
                       else []

    Every visible surface gets a row; undocumented rows carry
    empty lines.  command_names, if given, is an iterable of the
    command words this plan can dispatch to.
    """
    # Per node, gather: its own surfaces, and its subtree's
    # namespaces (name -> ('argument'|'option'|'internal', display)),
    # deepest first so shallower scopes override.
    argument_rows = []     # (name, display) in plan order
    option_rows = []       # (name, display) in plan order
    docs = {}              # name -> lines, post-merge
    command_names = tuple(command_names) if command_names else ()

    def walk(p):
        # returns the subtree namespace for p
        namespace = {}
        for o in p.options:
            if o.name not in namespace:
                namespace[o.name] = ('option', _option_display(o))
                option_rows.append((o.name, _option_display(o)))
        for s in p.slots:
            if isinstance(s.child, Terminal):
                namespace.setdefault(s.name, ('argument', s.usage_name))
                argument_rows.append((s.name, s.usage_name))
            else:
                child_namespace = walk(s.child)
                for name, value in child_namespace.items():
                    namespace.setdefault(name, value)
                namespace.setdefault(s.name, ('internal', s.usage_name))
        return namespace

    def apply(p, namespace):
        # deepest first: children's entries land, then ours
        # overwrite (nearest enclosing scope wins).
        for s in p.slots:
            if not isinstance(s.child, Terminal):
                apply(s.child, namespace)
        where = getattr(p.callable, '__name__', repr(p.callable))
        parsed = parse_docstring(_inspect.getdoc(p.callable), where)
        for kind, heading in (('arguments', 'Arguments:'),
                              ('options', 'Options:')):
            for name, lines in parsed[kind].items():
                entry = namespace.get(name)
                if entry is None:
                    raise AppealConfigurationError(
                        f"{where}: {name!r} (in the {heading} section) "
                        f"is not a parameter of {where!r} or its "
                        f"converters")
                found = entry[0]
                if found == 'internal':
                    raise AppealConfigurationError(
                        f"{where}: {name!r} is not one of the visible "
                        f"command-line arguments of {where!r}")
                if found != kind[:-1]:
                    raise AppealConfigurationError(
                        f"{where}: {name!r} (in the {heading} section) "
                        f"is an {found}, not an {kind[:-1]}")
                docs[name] = lines
        if parsed['commands']:
            if p is not plan or not command_names:
                raise AppealConfigurationError(
                    f"{where}: a Commands: section, but {where!r} "
                    f"doesn't dispatch to commands")
            for word, lines in parsed['commands'].items():
                if word not in command_names:
                    raise AppealConfigurationError(
                        f"{where}: {word!r} (in the Commands: section) "
                        f"is not one of the command words")
                docs[word] = lines
        return parsed

    namespace = walk(plan)
    parsed = apply(plan, namespace)

    return {
        'summary': parsed['summary'],
        'documentation': parsed['documentation'],
        'arguments': [(display, docs.get(name, []))
                      for name, display in argument_rows],
        'options': [(display, docs.get(name, []))
                    for name, display in option_rows],
        'commands': [(word, docs.get(word, []))
                     for word in command_names],
    }


def command_set_corpus(global_plan, entries, auto_help=True):
    """
    The corpus for a multi-command program's listing.  entries is
    a sequence of (word, summary) pairs in declaration order.  The
    command rows' documentation comes from the global command's
    Commands: entries, falling back to each command's own summary;
    the auto help command documents itself.
    """
    words = [word for word, _ in entries]
    if auto_help:
        words.append('help')
    if global_plan is not None:
        corpus = merge_docs(global_plan, command_names=words)
    else:
        corpus = {'summary': [], 'documentation': [],
                  'arguments': [], 'options': [],
                  'commands': [(word, []) for word in words]}
    fallback = dict(entries)
    if auto_help:
        fallback['help'] = 'Print usage documentation on a specific command.'
    corpus['commands'] = [
        (word, lines or ([fallback[word]] if fallback.get(word) else []))
        for word, lines in corpus['commands']]
    return corpus


def summary(callable):
    "The first line of the callable's docstring, for command listings."
    doc = _inspect.getdoc(callable)
    return doc.splitlines()[0] if doc else ''


def _option_display(o):
    "The option as shown in help tables: '-t|--times <int>'."
    bits = ['|'.join(o.strings)]
    if o.kind == 'group':
        bits.append('...')
    elif o.kind not in ('flag', 'nullary'):
        if o.usage_name is not None:
            bits.append(f'<{o.usage_name}>')
        else:
            converters = o.converters[1:] if len(o.converters) > 1 else o.converters
            for c in converters:
                if c is str or c is tuple:
                    bits.append(f'<{o.name}>')
                else:
                    bits.append(f'<{getattr(c, "__name__", o.name)}>')
    return ' '.join(bits)


