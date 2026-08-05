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

from .plan import Terminal, format_arg, _oparg_names
from .runtime import AppealConfigurationError


##
## The docstring parser (proposal §8.7, as ruled in §8.7.1).
##
## A docstring is input, never output: section headings and their
## entries are slurped out, the remaining prose coalesces into one
## blob, and the templates own the output's structure entirely.
##

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
                       coalesced Markdown, in source order, with
                       the special sections slurped out
        arguments      dict of name -> documentation lines
        options        dict of name -> documentation lines
        commands       dict of name -> documentation lines

    THE DOCSTRING IS MARKDOWN (the pivot, ruled 2026-08-05;
    Markdown ONLY--the 'Arguments:' + 'name: desc' grammar died
    unshipped).  The first paragraph is the summary; ANY heading
    (ATX or setext, any level, case-insensitive) named Options,
    Arguments, or Commands opens a special section, which must
    contain exactly one definition list with unformatted terms;
    everything else is the doc, other headings included.  The
    user's heading decoration is input spelling only--the help
    template dresses the page.

    'where' names the docstring's owner, for error messages.
    Grammar violations raise AppealConfigurationError; validation
    against the plan (unknown names, kinds, visibility) happens
    later, in the merge.
    """
    from .markdown import scan_docstring
    scanned = scan_docstring(doc or '', where)

    def entry_dict(pairs, section):
        entries = {}
        for name, text in (pairs or ()):
            if name in entries:
                raise AppealConfigurationError(
                    f"{where}: in the {section} section: "
                    f"{name!r} is documented twice")
            entries[name] = text.split('\n') if text else []
        return entries

    return {
        'summary': (scanned['summary'].split('\n')
                    if scanned['summary'] else []),
        'documentation': (scanned['body'].split('\n')
                          if scanned['body'] else []),
        'arguments': entry_dict(scanned['arguments'], 'Arguments'),
        'options': entry_dict(scanned['options'], 'Options'),
        'commands': entry_dict(scanned['commands'], 'Commands'),
        # the template establishes the page's order and dresses
        # the headings (ruled 2026-08-05): nothing to present
        'presentation': {'order': (), 'headers': {}, 'indents': {}},
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
    argument_rows = []     # (rowkey, display) in plan order
    option_rows = []       # (rowkey, display, site) in plan order:
                           # site is (flanking-argument anchors,
                           # depth) for qualifiers and indentation
    docs = {}              # rowkey -> lines, post-merge
    command_names = tuple(command_names) if command_names else ()
    fmt = plan.arg_format  # positional_argument_usage_format: how
                           # operand names decorate in the tables
    arg = lambda name: format_arg(fmt, name)

    def arg_name(s):
        # an operand's rendered metavar: an explicit rename
        # (usage_name != name) is literal and wins; otherwise the
        # name flows through the format string
        if s.usage_name != s.name:
            return s.usage_name
        return format_arg(fmt, s.usage_name)

    def flanks(p, index):
        # the nearest argument display before/after slot index, at
        # this level--the anchors a position qualifier names
        before = after = None
        for s in p.slots[:index]:
            before = arg_name(s)
        for s in p.slots[index + 1:]:
            after = arg_name(s)
            break
        return (before, after)

    namespaces = {}    # id(plan) -> its own subtree namespace

    def option_subtree(child, depth):
        # An option's converter subtree: its inner options become
        # rows indented beneath the declaring option's row (full
        # depth), and its operands are named--so the converter may
        # document them--though they show inline in the option
        # display, not as rows of their own.  Builds
        # namespaces[id(child)] so apply() can resolve the
        # converter's own docstring against its own window (a
        # converter documents its own options even when the name
        # is ambiguous a level up), and returns the names to merge
        # into the declaring plan's namespace.
        ns = {}
        for inner in child.options:
            rowkey = id(inner)
            display = _option_display(inner, fmt)
            ns.setdefault(inner.name, ('option', display, rowkey))
            option_rows.append(
                (rowkey, '  ' * depth + display, (None, None)))
            if inner.child is not None:
                for name, value in option_subtree(inner.child,
                                                  depth + 1).items():
                    ns.setdefault(name, value)
        for s in child.slots:
            # an operand of the option: shown inline in the option
            # display, so no row--but named, so the converter may
            # document it (with nowhere to show, the text is
            # dropped) rather than erroring
            ns.setdefault(s.name, ('argument', s.usage_name, id(s)))
            if not isinstance(s.child, Terminal):
                for name, value in option_subtree(s.child, depth).items():
                    ns.setdefault(name, value)
        namespaces.setdefault(id(child), ns)
        return ns

    def walk(p, override=None, anchors=(None, None)):
        # returns the subtree namespace for p: name -> (kind,
        # display, rowkey).  override, when set, is the
        # transparency rule in flight: (rowkey, display) for the
        # subtree's sole terminal--the outer annotated parameter's
        # name flowing through.
        namespace = {}
        for o in p.options:
            if o.name not in namespace:
                rowkey = id(o)
                namespace[o.name] = ('option', _option_display(o, fmt), rowkey)
                option_rows.append((rowkey, _option_display(o, fmt), anchors))
            if o.child is not None:
                for name, value in option_subtree(o.child, 1).items():
                    namespace.setdefault(name, value)
        for index, s in enumerate(p.slots):
            if isinstance(s.child, Terminal):
                if override is not None:
                    # override display is already final (formatted or
                    # literal, decided where it was captured)
                    rowkey, display = override
                else:
                    rowkey, display = s.name, arg_name(s)
                namespace.setdefault(s.name, ('argument', display, rowkey))
                argument_rows.append((rowkey, display))
            else:
                inner = s.child.sole_terminal_slot()
                child_override = None
                if inner is not None and override is None:
                    # the transparency rule: this converter
                    # consumes exactly one operand, so the
                    # annotated parameter's name flows through.
                    # (An explicit rename on the inner parameter
                    # still wins the *display*.)
                    display = (inner.usage_name
                               if inner.usage_name != inner.name
                               else arg_name(s))
                    child_override = (s.name, display)
                child_namespace = walk(s.child, child_override or override,
                                       flanks(p, index))
                for name, value in child_namespace.items():
                    if (name in namespace
                            and namespace[name][0] == 'ambiguous'):
                        continue
                    if (name in namespace
                            and namespace[name][2] != value[2]
                            and namespace[name][0] == value[0]):
                        # the same name from two sibling subtrees:
                        # documenting it HERE can't pick one
                        namespace[name] = ('ambiguous', value[1], None)
                        continue
                    namespace.setdefault(name, value)
                if child_override is not None:
                    namespace.setdefault(
                        s.name, ('argument', child_override[1], s.name))
                else:
                    namespace.setdefault(s.name, ('internal', s.usage_name, s.name))
        namespaces.setdefault(id(p), namespace)
        return namespace

    def apply(p, namespace=None):
        # deepest first: children's entries land, then ours
        # overwrite (nearest enclosing scope wins).  Each scope's
        # entries resolve against ITS OWN subtree namespace--a
        # converter documents its own window even when the name is
        # ambiguous a level up.
        namespace = namespaces[id(p)]
        # children (positional converters AND option converters)
        # land their docstrings first, deepest scope first; this
        # plan's own docstring then overwrites, so the nearest
        # enclosing scope wins on a name clash
        for s in p.slots:
            if not isinstance(s.child, Terminal):
                apply(s.child)
        for o in p.options:
            if o.child is not None:
                apply(o.child)
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
                if found == 'ambiguous':
                    raise AppealConfigurationError(
                        f"{where}: {name!r} is ambiguous in {where!r}--"
                        f"two of its converters' windows have one; "
                        f"document it in the converter's docstring")
                if found == 'internal':
                    raise AppealConfigurationError(
                        f"{where}: {name!r} is not one of the visible "
                        f"command-line arguments of {where!r}")
                if found != kind[:-1]:
                    raise AppealConfigurationError(
                        f"{where}: {name!r} (in the {heading} section) "
                        f"is an {found}, not an {kind[:-1]}")
                docs[entry[2]] = lines
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

    walk(plan)
    parsed = apply(plan)

    # position qualifiers, only where a display is duplicated:
    # the flanking arguments' names say which window each row is
    seen = {}
    for rowkey, display, anchors in option_rows:
        seen[display.strip()] = seen.get(display.strip(), 0) + 1
    rows = []
    for rowkey, display, anchors in option_rows:
        if seen[display.strip()] > 1 and anchors != (None, None):
            before, after = anchors
            if before and after:
                display += f' (after {before}, before {after})'
            elif before:
                display += f' (after {before})'
            elif after:
                display += f' (before {after})'
        rows.append((rowkey, display))

    return {
        'summary': parsed['summary'],
        'documentation': parsed['documentation'],
        'presentation': parsed.get('presentation'),
        'arguments': [(display, docs.get(name, []))
                      for name, display in argument_rows],
        'options': [(display, docs.get(name, []))
                    for name, display in rows],
        'commands': [(word, docs.get(word, []))
                     for word in command_names],
    }


def command_set_corpus(global_plan, entries, auto_help=True,
                       auto_version=False, doc=None, listing=True):
    """
    The corpus for a multi-command program's listing.  entries is
    a sequence of (word, summary) pairs in declaration order.  The
    command rows' documentation comes from the global command's
    Commands: entries, falling back to each command's own summary;
    the auto help and version commands document themselves
    (version before help, v1's listing order).
    """
    words = [word for word, _ in entries]
    if auto_version:
        words.append('version')
    if auto_help:
        words.append('help')
    if doc is not None:
        # the resolved program documentation (the doc= argument
        # or the shared module's docstring, ruled 2026-08-01)
        # supplies the program half: summary, prose, Commands:
        # overrides.  The global command's docstring still
        # documents ITS parameters in its own contexts.
        parsed = parse_docstring(doc, '<program documentation>')
        known = set(words)
        for name in parsed['commands']:
            if name not in known:
                raise AppealConfigurationError(
                    f"program documentation: 'Commands:' entry "
                    f"{name!r} isn't a command word")
        corpus = {'summary': parsed['summary'],
                  'documentation': parsed['documentation'],
                  'presentation': parsed.get('presentation'),
                  'arguments': [], 'options': [],
                  'commands': [(w, parsed['commands'].get(w, []))
                               for w in words]}
    elif global_plan is not None:
        corpus = merge_docs(global_plan, command_names=words)
    else:
        corpus = {'summary': [], 'documentation': [],
                  'arguments': [], 'options': [],
                  'commands': [(word, []) for word in words]}
    if listing:
        # the LISTING never shows the global command's own
        # arguments/options tables (v1's shape; the single
        # template would otherwise render them)--only usage,
        # prose, commands
        corpus['arguments'] = []
        corpus['options'] = []
    fallback = dict(entries)
    if auto_version:
        fallback['version'] = "Print the program's version."
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


def _option_display(o, fmt):
    "The option as shown in help tables: '-t|--times times'."
    bits = ['|'.join(o.strings)]
    if o.kind == 'group':
        bits.append('...')
    elif o.kind not in ('flag', 'nullary'):
        if o.usage_name is not None:
            # @app.parameter renamed the metavar: explicit wins
            bits.append(o.usage_name)
        else:
            for name in _oparg_names(o):
                bits.append(format_arg(fmt, name))
    return ' '.join(bits)




def man_page(prog, corpus, usage, command_pages=None, version=None):
    """
    The help corpus in troff clothing: a man(1) page assembled
    from the same predigested rows --help renders.  command_pages,
    for a multi-command program, is [(word, usage, corpus), ...]--
    each becomes a subsection under COMMANDS.  Returns the troff
    text; installing it somewhere is packaging's business, not
    Appeal's.
    """
    def esc(text):
        # troff: escape backslashes, protect a leading control
        # character; prose hyphens stay plain
        text = text.replace('\\', '\\e')
        if text.startswith(('.', "'")):
            text = '\\&' + text
        return text

    def opt(display):
        # option/argument display columns use troff minus signs
        return esc(display).replace('-', '\\-')

    out = []
    line = out.append

    def paragraphs(lines):
        first = True
        for text in '\n'.join(lines).split('\n\n'):
            if not text.strip():
                continue
            if not first:
                line('.PP')
            first = False
            for row in text.split('\n'):
                line(esc(row))

    def rows(section, pairs):
        if not pairs:
            return
        line(f'.SH {section}')
        for display, lines in pairs:
            line('.TP')
            line(f'.B {opt(display)}')
            if lines:
                paragraphs(lines)

    source = f'{prog} {version}' if version else prog
    line(f'.TH {prog.upper()} 1 "" "{esc(source)}" ""')
    line('.SH NAME')
    summary_line = ' '.join(corpus['summary']).strip()
    line(f'{esc(prog)} \\- {esc(summary_line)}' if summary_line
         else esc(prog))
    line('.SH SYNOPSIS')
    line(f'.B {opt(usage)}')
    for word, sub_usage, _ in (command_pages or ()):
        line('.br')
        line(f'.B {opt(sub_usage)}')
    if corpus['documentation']:
        line('.SH DESCRIPTION')
        paragraphs(corpus['documentation'])
    rows('ARGUMENTS', corpus['arguments'])
    rows('OPTIONS', corpus['options'])
    if command_pages:
        line('.SH COMMANDS')
        for word, lines in corpus['commands']:
            line('.TP')
            line(f'.B {esc(word)}')
            if lines:
                paragraphs(lines)
        for word, sub_usage, sub_corpus in command_pages:
            line(f'.SS "{esc(prog)} {esc(word)}"')
            line(f'.B {opt(sub_usage)}')
            if sub_corpus['documentation']:
                line('.PP')
                paragraphs(sub_corpus['documentation'])
            for label, pairs in (('Arguments:', sub_corpus['arguments']),
                                 ('Options:', sub_corpus['options'])):
                if not pairs:
                    continue
                line('.PP')
                line(f'.B {label}')
                for display, lines in pairs:
                    line('.TP')
                    line(f'.B {opt(display)}')
                    if lines:
                        paragraphs(lines)
    return '\n'.join(out) + '\n'
