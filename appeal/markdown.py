##
## appeal/markdown.py
##
## The Markdown pivot's Appeal half (Larry's spec, 2026-08-05;
## appeal.markdown.md is the plan of record).  Appeal docstrings
## are Markdown--big.markdown's subset when Appeal renders them,
## any Markdown at all when they only pass through textually.
##
## Three kinds of machinery live here:
##
## * scan_docstring: Appeal's own hand-written TEXTUAL scanner.
##   Finds the summary paragraph, the body, and the special
##   Options / Arguments / Commands sections--any heading kind
##   (ATX `# Options` or setext `Options\n===`), any level.
##   Each special section must contain exactly one definition
##   list; the terms must be unformatted.
##
## * to_github / to_commonmark: textual transformations of a
##   whole docstring for README-grade output.  Textual ON
##   PURPOSE: they never parse the Markdown, so a user writing
##   in some other library's dialect isn't limited to big's
##   subset (Larry's ruling: the full parser is isolated to
##   help rendering).
##
## * render_markdown_help: the full big pipeline for terminal
##   help--parse -> style_document -> split_styles_document ->
##   render_terminal -> join_styles -> StyleSheet.render.  The
##   ONE place the real parser runs, and the plugin seam.
##
## big imports live inside the functions that need them:
## importing appeal never drags big in.
##

import re

from .runtime import ConfigurationError


SPECIAL_SECTIONS = ('options', 'arguments', 'commands')

_ATX_RE = re.compile(r'^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$')
_SETEXT_RE = re.compile(r'^ {0,3}(=+|-+)\s*$')
_TERM_FORMATTING = set('*_`~[]<>')


def _heading_at(lines, i):
    """
    If lines[i] starts a heading, return (text, level, consumed);
    else None.  Both spellings (Larry: the hand-written scanner
    must support both): ATX (`## Text`, optional trailing #s) and
    setext (`Text` underlined with = for level 1 or - for 2).
    """
    line = lines[i]
    m = _ATX_RE.match(line)
    if m:
        return (m.group(2).strip(), len(m.group(1)), 1)
    if line.strip() and not line[:1].isspace() and i + 1 < len(lines):
        u = _SETEXT_RE.match(lines[i + 1])
        if u:
            return (line.strip(), 1 if u.group(1)[0] == '=' else 2, 2)
    return None


def _continues(line):
    "A definition continuation line: indented, not a new ':'."
    return line[:1].isspace() and not line.lstrip().startswith(':')


def _parse_definition_list(lines, where):
    """
    Parse section content that must be exactly one definition
    list: term lines at the margin, `: ` definitions beneath,
    indented continuations, blank-line paragraph breaks inside a
    definition.  Returns [(term, definition_markdown), ...];
    anything that isn't a definition list refuses by name.
    """
    def refuse(what):
        raise ConfigurationError(f"docstring section {where}: {what}")

    entries = []
    i, n = 0, len(lines)
    while i < n:
        if not lines[i].strip():
            i += 1
            continue
        line = lines[i]
        if line.lstrip().startswith(':'):
            refuse(f"definition with no term: {line.strip()!r}")
        if line[:1].isspace():
            refuse(f"stray indented text {line.strip()!r} (the section "
                   f"must contain only a definition list)")
        term = line.strip()
        bad = _TERM_FORMATTING & set(term)
        if bad:
            refuse(f"term {term!r} must be unformatted "
                   f"(contains {''.join(sorted(bad))!r})")
        i += 1
        j = i
        while j < n and not lines[j].strip():
            j += 1
        if j >= n or not lines[j].lstrip().startswith(':'):
            refuse(f"term {term!r} has no ': definition'")
        i = j
        parts = []
        while i < n and lines[i].lstrip().startswith(':'):
            first = lines[i].lstrip()[1:]
            if first[:1] == ' ':
                first = first[1:]
            block = [first]
            i += 1
            cont = []
            while i < n:
                s = lines[i]
                if not s.strip():
                    j = i
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and _continues(lines[j]):
                        cont.append('')
                        i = j
                        continue
                    break
                if _continues(s):
                    cont.append(s)
                    i += 1
                    continue
                break
            if cont:
                pad = min(len(c) - len(c.lstrip())
                          for c in cont if c.strip())
                block.extend(c[pad:] if c.strip() else '' for c in cont)
            parts.append('\n'.join(block).rstrip())
        entries.append((term, '\n\n'.join(parts)))
    if not entries:
        refuse("empty (a special section must contain a "
               "definition list)")
    return entries


def scan_docstring(text):
    """
    Appeal's textual docstring scanner (no Markdown parser
    involved).  Returns a dict:
      summary    the first paragraph, newlines intact
      body       everything else EXCEPT the special sections
                 (their headings included), stripped
      options, arguments, commands
                 [(term, definition_markdown), ...] or None when
                 the section wasn't written
    ANY heading whose text is Options/Arguments/Commands
    (case-insensitive), of ANY kind and level, opens the special
    section; it runs to the next heading of any kind or EOF.
    """
    lines = (text or '').split('\n')
    sections = {name: None for name in SPECIAL_SECTIONS}
    body_lines = []
    i, n = 0, len(lines)
    while i < n:
        h = _heading_at(lines, i)
        if h is not None:
            name = h[0].lower()
            if name in SPECIAL_SECTIONS:
                if sections[name] is not None:
                    raise ConfigurationError(
                        f"docstring has two {h[0]!r} sections")
                i += h[2]
                content = []
                while i < n and _heading_at(lines, i) is None:
                    content.append(lines[i])
                    i += 1
                sections[name] = _parse_definition_list(
                    content, h[0] + ':')
                continue
        body_lines.append(lines[i])
        i += 1

    body = '\n'.join(body_lines).strip('\n')
    summary = []
    rest = ''
    if body:
        chopped = body.split('\n')
        for k, line in enumerate(chopped):
            if not line.strip():
                rest = '\n'.join(chopped[k:]).strip('\n')
                break
            summary.append(line)
        else:
            rest = ''
    result = {'summary': '\n'.join(summary), 'body': rest}
    result.update(sections)
    return result


##
## the README-grade textual transformations
##

_STRIKETHROUGH_RE = re.compile(r'~~(?=\S)(.+?)(?<=\S)~~', re.DOTALL)
_ALERT_RE = re.compile(
    r'^(\s*> )\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$',
    re.MULTILINE)


def _strip_strikethrough(text):
    return _STRIKETHROUGH_RE.sub(r'\1', text)


def _alerts_to_commonmark(text):
    def label(m):
        return f"{m.group(1)}**{m.group(2).title()}:**"
    return _ALERT_RE.sub(label, text)


def _transform_definition_lists(text, render_block):
    """
    Find every definition list in `text` textually (a margin term
    line whose next non-blank line starts with ':') and replace
    it with render_block(entries).  Lenient: anything that isn't
    a definition list passes through untouched.
    """
    lines = text.split('\n')
    out = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        starts = (line.strip() and not line[:1].isspace()
                  and not line.lstrip().startswith(':'))
        if starts:
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n and lines[j].lstrip().startswith(':'):
                # a definition-list run: consume greedily
                block = []
                k = i
                while k < n:
                    if not lines[k].strip():
                        j2 = k
                        while j2 < n and not lines[j2].strip():
                            j2 += 1
                        if j2 < n and (lines[j2][:1].isspace()
                                       or lines[j2].lstrip()
                                          .startswith(':')
                                       or _run_has_term(lines, j2, n)):
                            block.extend(lines[k:j2])
                            k = j2
                            continue
                        break
                    block.append(lines[k])
                    k += 1
                try:
                    entries = _parse_definition_list(
                        block, '<definition list>')
                except ConfigurationError:
                    out.append(line)
                    i += 1
                    continue
                out.append(render_block(entries))
                i = k
                continue
        out.append(line)
        i += 1
    return '\n'.join(out)


def _run_has_term(lines, i, n):
    "After a blank gap: does another term/':' pair start here?"
    line = lines[i]
    if not line.strip() or line[:1].isspace() \
            or line.lstrip().startswith(':'):
        return False
    j = i + 1
    while j < n and not lines[j].strip():
        j += 1
    return j < n and lines[j].lstrip().startswith(':')


def _dl_html(entries):
    """
    A definition list as inline HTML <dl> (GitHub / PyPI).  The
    blank lines around each term and definition matter: they end
    the HTML block, so GitHub renders the Markdown INSIDE the
    <dt>/<dd> (both may carry formatting, per Larry).
    """
    parts = ['<dl>']
    for term, definition in entries:
        parts.append(f'<dt>\n\n{term}\n\n</dt>')
        parts.append(f'<dd>\n\n{definition}\n\n</dd>')
    parts.append('</dl>')
    return '\n'.join(parts)


def _dl_blockquote(entries):
    """
    A definition list as bold term + blockquoted definition
    (pure CommonMark).
    """
    parts = []
    for term, definition in entries:
        quoted = '\n'.join(('> ' + line).rstrip()
                           for line in definition.split('\n'))
        parts.append(f'**{term}**\n\n{quoted}')
    return '\n\n'.join(parts)


def to_github(text):
    """
    A docstring's Markdown re-spelled for GitHub flavor:
    definition lists become inline-HTML <dl>; everything else
    GitHub renders natively, so strikethrough and alerts pass
    through untouched (Larry's ruling, 2026-08-05: target
    GitHub flavor, let PyPI catch up in its own time).
    """
    return _transform_definition_lists(text, _dl_html)


def to_commonmark(text):
    """
    A docstring's Markdown re-spelled for plain CommonMark:
    definition lists become bold term + blockquote, strikethrough
    is stripped, alerts become bold-labelled blockquotes.
    """
    text = _transform_definition_lists(text, _dl_blockquote)
    text = _strip_strikethrough(text)
    return _alerts_to_commonmark(text)


##
## the terminal renderer: the one place the real parser runs
##

def render_markdown_help(text, width=None, stylesheet=None):
    """
    Render Markdown for a terminal via big's whole pipeline:
    parse -> style_document -> split_styles_document ->
    render_terminal -> join_styles -> StyleSheet.render.

    stylesheet: a big StyleSheet mapping style names to output;
    None means big's uncolored ANSI (bold/italic, no color)
    composed under markdown_defaults.  This function is the
    pluggable seam: everything else in this module is textual,
    so replacing this renderer swaps the whole Markdown dialect.
    """
    from big.markdown import (markdown_defaults, parse,
                              render_terminal, split_styles_document,
                              style_document)
    from big.template import StyleSheet, ansi_uncolored, join_styles
    document = split_styles_document(style_document(parse(text)))
    rendered = render_terminal(document, width=width)
    joined = join_styles(rendered)
    if stylesheet is None:
        stylesheet = ansi_uncolored | markdown_defaults
    return stylesheet.render(joined)
