#!/usr/bin/env python3
#
# appeal/presentation.py
# Part of Appeal 1.0.
#
# All human-facing output: markdown scanning/transform of docstrings,
# help-page assembly, usage + error rendering, themes and stylesheets.
# Lazy -- imported only when help, usage, or an error actually renders.

import re

from . import ConfigurationError


SPECIAL_SECTIONS = ('options', 'arguments', 'commands')

# the special section openers, LITERALLY (Larry's ruling, 2026-09-07):
# a heading of any level, ATX or setext, whose text is exactly one of
# these (this case), outside any fence (Larry, 2026-09-10: any number
# of octothorpes--a converter's docstring nests under a command's, so
# its sections sit a level down).  Anything else is prose.
_SPECIAL_HEADINGS = {'Options': 'options',
                     'Arguments': 'arguments',
                     'Commands': 'commands',
                     'Subcommands': 'commands'}   # either word, any context
                                                 # (Larry, 2026-09-10)
_ATX_RE = re.compile(r'^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$')
_SETEXT_RE = re.compile(r'^ {0,3}(=+|-+)\s*$')
# a code fence: three or more backticks or tildes, indented at most
# three; closed by the same character, at least as many
_FENCE_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})')
# a definition marker: ':' indented at most three (big's rule), then
# the gap that sets the content column
_DEFMARK_RE = re.compile(r'^( {0,3}):( *)(.*)$')
# characters that read as Markdown formatting in a term.  '_' is
# deliberately absent: parameter names carry underscores, and
# mid-word underscores aren't emphasis anyway.
_TERM_FORMATTING = set('*`~[]<>')


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


def _fence_step(line, fence):
    """
    Track code fences line by line: returns the fence state after
    `line`--(char, length) inside a fence, None outside--given the
    state before it.
    """
    m = _FENCE_RE.match(line)
    if fence is None:
        return (m.group(1)[0], len(m.group(1))) if m else None
    if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
        return None
    return fence


def _indent(line):
    return len(line) - len(line.lstrip(' '))


def _parse_definition_list(lines, where):
    """
    Parse section content that must be exactly one definition
    list: term lines at the margin, `: ` definitions beneath,
    continuations indented to the definition's content column,
    blank-line paragraph breaks inside a definition.  The content
    column is big's (the parser that renders the details): the
    ':' plus its gap sets it, and every line indented at least
    that far belongs to the definition--including a NESTED
    'term / : definition' pair, colon and all.  Returns
    [(term, definition_markdown), ...]; anything that isn't a
    definition list refuses by name.
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
        if _DEFMARK_RE.match(line):
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
        if j >= n or not _DEFMARK_RE.match(lines[j]):
            refuse(f"term {term!r} has no ': definition'")
        i = j
        parts = []
        while i < n and _DEFMARK_RE.match(lines[i]):
            m = _DEFMARK_RE.match(lines[i])
            margin, gap, first = m.groups()
            if len(gap) > 4:                # big: a wide gap is one
                first = gap[1:] + first     # space plus indented text
                gap = ' '
            column = len(margin) + 1 + len(gap)
            block = [first]
            i += 1
            cont = []
            while i < n:
                s = lines[i]
                if not s.strip():
                    j = i
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and _indent(lines[j]) >= column:
                        cont.append('')
                        i = j
                        continue
                    break
                if _indent(s) >= column:
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
    # an empty section is legal (Larry, 2026-09-10): it ASKS for the
    # section, filled in from the signature
    return entries


def scan_docstring(text, where=None):
    """
    Appeal's textual docstring scanner (no Markdown parser
    involved).  Returns a dict:
      summary    the first paragraph, newlines intact
      body       everything else EXCEPT the special sections
                 (their headings included), stripped
      options, arguments, commands
                 [(term, definition_markdown), ...] or None when
                 the section wasn't written
    A heading of any level, ATX or setext, reading exactly
    'Options' / 'Arguments' / 'Commands' (this case, outside any
    code fence) opens the special section; it runs to the next
    heading of any kind (outside a fence) or EOF.  Any other
    spelling is prose: ignored, kept in the body.  An empty
    section is legal: it asks for the section, auto-filled.
    `where` names the docstring's owner in error messages.
    """
    prefix = f"{where}: docstring " if where else "docstring "
    lines = (text or '').split('\n')
    sections = {name: None for name in SPECIAL_SECTIONS}
    body_lines = []
    fence = None
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        heading = _heading_at(lines, i) if fence is None else None
        name = _SPECIAL_HEADINGS.get(heading[0]) if heading else None
        if name is not None:
            if sections[name] is not None:
                raise ConfigurationError(
                    f"{prefix}has two {line.rstrip()!r} sections")
            i += heading[2]
            content = []
            while i < n and (fence is not None
                             or _heading_at(lines, i) is None):
                fence = _fence_step(lines[i], fence)
                content.append(lines[i])
                i += 1
            sections[name] = _parse_definition_list(
                content, (f"{where}: {line.rstrip()}" if where
                          else line.rstrip()) + ':')
            continue
        fence = _fence_step(line, fence)
        body_lines.append(line)
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
# code, which the textual transforms must leave alone: a fenced
# block (opened and closed by the same fence), or a code span (a
# backtick run closed by a run of the same length)
_CODE_RE = re.compile(
    r'(?:^ {0,3}(`{3,}|~{3,}).*?^ {0,3}\1[ \t]*$)'
    r'|(?:(`+)(?!`).+?(?<!`)\2(?!`))',
    re.MULTILINE | re.DOTALL)


def _outside_code(text, transform):
    "transform(text) applied to the stretches that aren't code."
    out = []
    pos = 0
    for m in _CODE_RE.finditer(text):
        out.append(transform(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(transform(text[pos:]))
    return ''.join(out)


def _strip_strikethrough(text):
    return _outside_code(text, lambda t: _STRIKETHROUGH_RE.sub(r'\1', t))


def _alerts_to_commonmark(text):
    def label(m):
        return f"{m.group(1)}**{m.group(2).title()}:**"
    return _outside_code(text, lambda t: _ALERT_RE.sub(label, t))


def _transform_definition_lists(text, render_block):
    """
    Find every definition list in `text` textually (a margin term
    line whose next non-blank line starts with ':') and replace
    it with render_block(entries).  Lenient: anything that isn't
    a definition list passes through untouched.
    """
    lines = text.split('\n')
    out = []
    fence = None
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        fence = _fence_step(line, fence)
        starts = (fence is None and line.strip() and not line[:1].isspace()
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
    from big.markdown import (layout_document, markdown_defaults,
                              parse, split_styles_document,
                              style_document)
    from big.stylesheet import (join_styles, strip_styles,
                                uncolored_palette)
    from big.text import wrap_words
    document = split_styles_document(style_document(parse(text)))
    layout = layout_document(document)
    if width is None:
        import shutil
        width = shutil.get_terminal_size((79, 24)).columns
    rendered = wrap_words(layout, margin=width, raw=strip_styles)
    joined = join_styles(rendered)
    if stylesheet is None:
        stylesheet = uncolored_palette | markdown_defaults
    # the renderer injects `line` (ruled 2026-08-08): the margin
    # as a drawable string, underneath so the sheet's own wins
    from big.stylesheet import StyleSheet
    stylesheet = StyleSheet({'line': ('-' * width,)}) | stylesheet
    return stylesheet.render(joined)


import re
import shutil

from big.builtin import can_colorize
from big.markdown import (glyphs_from_stylesheet, layout_document,
                          markdown_defaults, parse,
                          split_styles_document, style_document)
from big.stylesheet import (StyleSheet, ansi_16_color_palette,
                            escape_styles, join_styles,
                            plain_stylesheet, strip_styles, style,
                            transforms)
from big.text import (OverflowStrategy, _iterate_over_bytes,
                      expand_tabs, format_definition_list,
                      merge_columns, split_text_with_code,
                      wrap_words)

from . import AppealConfigurationError

_re = re

style_delimiters = '⦃⦙⦄'


_decoration_sheets = {}

def decorate_argument(name, entry=None):
    # render a RAW operand name through the argument_decoration
    # transform (host -> <HOST>), the single definition of operand
    # placeholder decoration.  Applied EARLY--baked into the text--so
    # big's layout sizes def-list columns at the decorated width and
    # the usage wrap sees real widths, both colored and plain (ruled
    # 2026-08-24).  Color stays a late, per-theme concern (the
    # 'argument'/'oparg' roles); only the decoration bakes in here.
    #
    # The SHAPE is the caller's stylesheet entry (ruled 2026-09-03:
    # "it's in the stylesheet precisely so users can tweak it")--
    # entry is the app sheet's 'argument_decoration' definition,
    # stamped onto its plans at build; None means the stock shape.
    # Bake-time, deliberately: the per-stream automatic default
    # (stylesheet=None) keeps the stock shape, because layout must
    # not vary per stream.
    if entry is None:
        pair = uncolored_theme['argument_decoration']
    elif hasattr(entry, 'replacement'):     # a StyleSheet definition
        pair = tuple(entry.args) + (entry.replacement,)
    else:
        pair = tuple(entry)
    sheet = _decoration_sheets.get(pair)
    if sheet is None:
        sheet = transforms | StyleSheet({'argument_decoration': pair})
        _decoration_sheets[pair] = sheet
    return sheet.render(
        style('argument_decoration', escape_styles(name)))


appeal_markdown_defaults = {
    'heading1': ('T',
        '⦃clip⦙⦃line⦄⦙⦃fill⦙⦃heading1_rule⦄⦙⦃strip⦙T⦄⦄⦄\n'
        '⦃strip⦙T⦄\n'
        '⦃clip⦙⦃line⦄⦙⦃fill⦙⦃heading1_rule⦄⦙⦃strip⦙T⦄⦄⦄'),
    'heading2': ('T',
        '⦃strip⦙T⦄\n'
        '⦃clip⦙⦃line⦄⦙⦃fill⦙⦃heading2_rule⦄⦙⦃strip⦙T⦄⦄⦄'),
    'rule': ('T', '⦃fill⦙T⦙⦃line⦄⦄'),
    'heading_note':      ('T', '⦃blue⦙T⦄'),
    'heading_tip':       ('T', '⦃green⦙T⦄'),
    'heading_important': ('T', '⦃purple⦙T⦄'),
    'heading_warning':   ('T', '⦃dark_yellow⦙T⦄'),
    'heading_caution':   ('T', '⦃red⦙T⦄'),
}


uncolored_theme = {
    **appeal_markdown_defaults,
    'heading_color': ('T', 'T'),
    'heading1':   ('T',
        '⦃heading_color⦙⦃clip⦙⦃line⦄⦙⦃fill⦙⦃heading1_rule⦄⦙⦃strip⦙T⦄⦄⦄⦄\n'
        '⦃bold⦙⦃heading_color⦙⦃strip⦙T⦄⦄⦄\n'
        '⦃heading_color⦙⦃clip⦙⦃line⦄⦙⦃fill⦙⦃heading1_rule⦄⦙⦃strip⦙T⦄⦄⦄⦄'),
    'heading2':   ('T',
        '⦃bold⦙⦃heading_color⦙⦃strip⦙T⦄⦄⦄\n'
        '⦃heading_color⦙⦃clip⦙⦃line⦄⦙⦃fill⦙⦃heading2_rule⦄⦙⦃strip⦙T⦄⦄⦄⦄'),
    'heading3':   ('T', '⦃bold⦙⦃heading_color⦙⦃strip⦙T⦄⦄⦄\n'
                       '⦃heading_color⦙⦃fill⦙⦃heading2_rule⦄⦙⦃strip⦙T⦄⦄⦄'),
    'heading4':   ('T', '⦃italic⦙⦃heading_color⦙T⦄⦄'),
    'heading5':   ('T', '⦃heading_color⦙T⦄'),
    'heading6':   ('T', '⦃heading_color⦙⦃lower⦙T⦄⦄'),
    # inline structure
    'code':       ('T', 'T'),               # themes color this
    'codeblock':  ('T', '⦃code⦙T⦄'),        # inherits code
    'link':       ('URL', 'T', '⦃underline⦙T⦄'),   # URL is luggage (big >= 0.15)
    'marker':     ('T', 'T'),
    'blockquote': ('T', 'T'),
    'term':       ('T', '⦃bold⦙T⦄'),        # deflist terms
    'unit':       ('T', 'T'),               # a usage unit that never wraps
                                            # apart (a required option and
                                            # its operands)
    # GitHub alerts: bodies wear their kind's color too
    'note':              ('T', '⦃blue⦙T⦄'),
    'heading_note':      ('T', '⦃bold⦙⦃blue⦙T⦄⦄'),
    'tip':               ('T', '⦃green⦙T⦄'),
    'heading_tip':       ('T', '⦃bold⦙⦃green⦙T⦄⦄'),
    'important':         ('T', '⦃purple⦙T⦄'),
    'heading_important': ('T', '⦃bold⦙⦃purple⦙T⦄⦄'),
    'warning':           ('T', '⦃dark_yellow⦙T⦄'),
    'heading_warning':   ('T', '⦃bold⦙⦃dark_yellow⦙T⦄⦄'),
    'caution':           ('T', '⦃red⦙T⦄'),
    'heading_caution':   ('T', '⦃bold⦙⦃red⦙T⦄⦄'),
    # the roles, attribute-only defaults
    'program':    ('T', '⦃bold⦙T⦄'),
    'command':    ('T', '⦃bold⦙T⦄'),
    'option':     ('T', '⦃bold⦙T⦄'),
    'argument':   ('T', 'T'),
    # the distinct decoration transform: a raw operand name -> its
    # placeholder (host -> <HOST>), formerly the app's
    # positional_argument_usage_format.  Kept out of the 'argument'
    # color role so themes stay color-only.
    'argument_decoration': ('T', '<⦃upper⦙T⦄>'),
    'oparg':      ('T', '⦃argument⦙T⦄'),    # ruled: defaults to argument
    'summary':    ('T', '⦃bold⦙T⦄'),
    'error':      ('T', '⦃bold⦙T⦄'),
}


# plain and uncolored are ONE structure over two palettes: the theme
# emits the document (heading rules are TEXT), and the palette decides
# what renders--uncolored_palette expresses bold/italic/underline but
# no colors, plain_palette expresses nothing.  A copy, so a red pen
# on one can't silently edit the other.
plain_theme = dict(uncolored_theme)


def _theme(**overrides):
    "A theme: the uncolored base plus your colors."
    t = dict(uncolored_theme)
    t.update(overrides)
    return t


appeal_theme = _theme(
    command   = ('T', '⦃bold⦙⦃cyan⦙T⦄⦄'),
    option    = ('T', '⦃cyan⦙T⦄'),
    argument  = ('T', '⦃italic⦙T⦄'),
    summary   = ('T', '⦃bold⦙T⦄'),
    error     = ('T', '⦃bold⦙⦃red⦙T⦄⦄'),
    code      = ('T', '⦃green⦙T⦄'),          # ruled: code is green
    marker    = ('T', '⦃dark_purple⦙T⦄'),
    link      = ('URL', 'T', '⦃underline⦙⦃blue⦙T⦄⦄'),      # URL: luggage
    heading_color = ('T', '⦃cyan⦙T⦄'),
)


light_warm_theme = _theme(
    command   = ('T', '⦃bold⦙⦃dark_orange⦙T⦄⦄'),
    option    = ('T', '⦃dark_red⦙T⦄'),
    argument  = ('T', '⦃italic⦙⦃dark_gray⦙T⦄⦄'),
    summary   = ('T', '⦃bold⦙⦃dark_red⦙T⦄⦄'),
    error     = ('T', '⦃bold⦙⦃red⦙T⦄⦄'),
    code      = ('T', '⦃dark_amber⦙T⦄'),
    marker    = ('T', '⦃dark_yellow⦙T⦄'),
    link      = ('URL', 'T', '⦃underline⦙⦃dark_purple⦙T⦄⦄'),      # URL: luggage
    heading_color = ('T', '⦃dark_red⦙T⦄'),
)


dark_warm_theme = _theme(
    command   = ('T', '⦃bold⦙⦃light_orange⦙T⦄⦄'),
    option    = ('T', '⦃light_red⦙T⦄'),
    argument  = ('T', '⦃italic⦙⦃light_gray⦙T⦄⦄'),
    summary   = ('T', '⦃bold⦙⦃light_orange⦙T⦄⦄'),
    error     = ('T', '⦃bold⦙⦃light_red⦙T⦄⦄'),
    code      = ('T', '⦃light_amber⦙T⦄'),
    marker    = ('T', '⦃light_yellow⦙T⦄'),
    link      = ('URL', 'T', '⦃underline⦙⦃light_purple⦙T⦄⦄'),      # URL: luggage
    heading_color = ('T', '⦃light_red⦙T⦄'),
)


light_cool_theme = _theme(
    command   = ('T', '⦃bold⦙⦃dark_cyan⦙T⦄⦄'),
    option    = ('T', '⦃dark_blue⦙T⦄'),
    argument  = ('T', '⦃italic⦙⦃dark_gray⦙T⦄⦄'),
    summary   = ('T', '⦃bold⦙⦃dark_blue⦙T⦄⦄'),
    error     = ('T', '⦃bold⦙⦃red⦙T⦄⦄'),     # errors stay red, even here
    code      = ('T', '⦃dark_green⦙T⦄'),
    marker    = ('T', '⦃dark_cyan⦙T⦄'),
    link      = ('URL', 'T', '⦃underline⦙⦃dark_purple⦙T⦄⦄'),      # URL: luggage
    heading_color = ('T', '⦃dark_blue⦙T⦄'),
)


dark_cool_theme = _theme(
    command   = ('T', '⦃bold⦙⦃light_cyan⦙T⦄⦄'),
    option    = ('T', '⦃light_blue⦙T⦄'),
    argument  = ('T', '⦃italic⦙⦃light_gray⦙T⦄⦄'),
    summary   = ('T', '⦃bold⦙⦃light_cyan⦙T⦄⦄'),
    error     = ('T', '⦃bold⦙⦃light_red⦙T⦄⦄'),
    code      = ('T', '⦃light_green⦙T⦄'),
    marker    = ('T', '⦃light_cyan⦙T⦄'),
    link      = ('URL', 'T', '⦃underline⦙⦃light_purple⦙T⦄⦄'),      # URL: luggage
    heading_color = ('T', '⦃light_blue⦙T⦄'),
)


def resolve_stylesheet(spec, file=None):
    """
    The runtime half of the stylesheet decision.  spec is what
    the program was configured with: None (auto), False (never
    any color), or a complete composed StyleSheet, used VERBATIM
    (ruled 2026-08-06).  Auto composes appeal_theme over the
    ANSI 16 when this stream wants color at this moment
    (can_colorize: NO_COLOR and friends always win)--the 16
    because the terminal remaps them to its own scheme, so the
    theme stays legible on light and dark alike (ruled
    2026-08-06); False (and colorless auto) gets the plain
    palette: no escapes of any kind.
    """
    if spec is None or spec is False:
        palette = (ansi_16_color_palette
                   if (spec is None) and can_colorize(file=file)
                   else plain_stylesheet)
        return (markdown_defaults | transforms | palette
                | StyleSheet(appeal_theme))
    return spec


def usage_units(usage):
    """
    Split a usage line into its unbreakable top-level units: the
    program span (`tool db stop`, spaces and all), bare operands, and
    complete bracket groups like '[-t|--times <int>]'.  Wrapping
    never splits a unit.
    """
    units = []
    unit = []
    depth = 0
    for ch in usage:
        if ch == ' ' and not depth:
            if unit:
                units.append(''.join(unit))
                unit = []
            continue
        if ch in '[⦃':               # a bracket group, or a style span
            depth += 1
        elif ch in ']⦄':
            depth -= 1
        unit.append(ch)
    if unit:
        units.append(''.join(unit))
    return units


default_template = (
    'usage: {usage}\n'
    '\n'
    '{summary}\n'
    '\n'
    '{doc}\n'
    '\n'
    '## Arguments\n'
    '{arguments}\n'
    '\n'
    '## Options\n'
    '{options}\n'
    '\n'
    '## Commands\n'
    '{commands}\n'
)


_TEMPLATE_SECTIONS = ('usage', 'summary', 'doc', 'options',
                      'arguments', 'commands')


def help_margin(margin=None, file=None):
    """
    The wrap width for a rendered help page.  An explicit int wraps
    at exactly that width (tty or not).  None (the default) measures:
    if `file` is a terminal, its real width; otherwise 79, so
    redirected/captured output is stable.
    """
    if margin is not None:
        return margin
    import sys
    stream = file if file is not None else sys.stdout
    try:
        if stream.isatty():
            import shutil
            return shutil.get_terminal_size((79, 24)).columns
    except (AttributeError, ValueError, OSError):
        pass
    return 79


def help_stylesheet(file=None):
    """
    The StyleSheet a help page paints with by default, for this
    stream at this moment: appeal_theme over the ANSI 16 when
    the stream wants color, the plain palette (no escapes of any
    kind) when it doesn't--pipes and captures stay clean.
    Exactly resolve_stylesheet(None, file).
    """
    return resolve_stylesheet(None, file)


def render_baked_help(pieces, margin=79, file=None,
                      stylesheet=None):
    """
    The runtime half of a help page.  pieces is the baked,
    template-ordered tuple from help_page_pieces: ('usage',
    prefix, usage-string) entries already carry their role spans
    (built by Plan.usage) and are wrapped at whole units;
    ('markdown', layout) entries carry big's width-independent
    layout tuples--wrap_words lays them out at the real margin
    (the sheet-rendered width measuring the words), join_styles
    fuses adjacent spans.  Either way the stylesheet paints last:
    stylesheet is a spec (None auto per stream, False never,
    or a composed StyleSheet used verbatim).
    """
    sheet = resolve_stylesheet(stylesheet, file)
    # the renderer injects `line`--'-' repeated to the margin, a
    # full-width rule bare and a margin-wide model inside
    # clip/fill (ruled 2026-08-08).  Only the renderer knows the
    # margin; injected UNDERNEATH, so a sheet that defines its
    # own `line` wins (the stylesheet= verbatim rule).
    sheet = StyleSheet({'line': ('-' * margin,)}) | sheet
    glyphs = glyphs_from_stylesheet(sheet)
    # measure a unit's visible width: glyphs_from_stylesheet knows
    # big's markdown glyph roles; operand placeholders are already
    # DECORATED into literal text (host -> <HOST>) by the time they
    # get here, so their width measures correctly too.  The bare
    # ⦃line⦄ word is the one role only the renderer sizes.
    line_span = style('line')
    line_glyph = sheet.render(line_span)
    measure = lambda w: strip_styles(
        glyphs(w).replace(line_span, line_glyph))
    out = []
    for piece in pieces:
        if piece[0] == 'usage':
            prefix, usage = piece[1], piece[2]
            # the usage line already carries its role spans (built by
            # Plan.usage) with operands decorated inline; split into
            # units and wrap
            units = usage_units(usage)
            # a continuation line hangs under the first thing after the
            # program span--unless that span is long, when it hangs at 8
            # (Larry, 2026-09-10)
            span = len(measure(units[0])) if units else 0
            hang = span + 1 if span < 16 else 8
            body = wrap_words(units, margin, raw=measure,
                              indent=(prefix, ' ' * (len(prefix) + hang)))
            out.append(sheet.render(body))
        else:
            layout = piece[1]
            text = wrap_words(layout, margin=margin, raw=measure)
            # span_linebreaks: a WRAPPED heading is one heading--
            # its structural entry fires once, around the block
            text = join_styles(text, span_linebreaks=True)
            out.append(sheet.render(text).rstrip('\n'))
    text = '\n\n'.join(out)
    while '\n\n\n' in text:
        text = text.replace('\n\n\n', '\n\n')
    return text.lstrip('\n').rstrip() + '\n'


def parse_help_template(template):
    """
    Partition a help template at its six {section} placeholders.
    Returns [(name, header, indent), ...] in template order:
    header is the text since the previous placeholder (leading
    newlines included); indent is the header text after its last
    newline--the body's per-line indent.  All six sections must
    appear exactly once; anything else in braces refuses.
    """
    import re as _re
    template = '\n'.join(line.rstrip()
                         for line in template.split('\n'))
    sections = []
    seen = set()
    pos = 0
    for m in _re.finditer(r'\{([A-Za-z_]+)\}', template):
        name = m.group(1)
        if name not in _TEMPLATE_SECTIONS:
            raise AppealConfigurationError(
                f"help template: unknown section {{{name}}}")
        if name in seen:
            raise AppealConfigurationError(
                f"help template: {{{name}}} appears twice")
        seen.add(name)
        header = template[pos:m.start()]
        nl = header.rfind('\n')
        indent = header[nl + 1:] if nl >= 0 else ''
        if not indent.strip() == '':
            indent = ''
        sections.append((name, header, indent))
        pos = m.end()
    missing = [s for s in _TEMPLATE_SECTIONS if s not in seen]
    if missing:
        raise AppealConfigurationError(
            f"help template: missing section(s): "
            f"{', '.join('{' + s + '}' for s in missing)}")
    return sections


def _reword_heading(header, old, new):
    "The template header with a heading reading exactly `old` reworded."
    lines = header.split('\n')
    i = 0
    while i < len(lines):
        heading = _heading_at(lines, i)
        if heading is not None and heading[0] == old:
            if heading[2] == 1:                 # ATX: the text after the #s
                lines[i] = lines[i].replace(old, new, 1)
            else:                               # setext: text, then its rule
                lines[i] = new
                lines[i + 1] = lines[i + 1][0] * len(new)
            i += heading[2]
            continue
        i += 1
    return '\n'.join(lines)


def rows_document(rows, header, role=None, titles=None, level=2):
    """
    A table section as a big document: the template's header
    (Markdown) followed by the corpus rows as a DefinitionList,
    built as NODES (Astra R08: no Markdown round trip, no
    re-deriving the hierarchy from indentation).  Each row's
    display--a role-tagged span--is the entry's term, carried as
    StyledText so big styles it verbatim; its doc lines are
    Markdown, parsed into the definition's blocks.  A row's nested
    sections (Larry, 2026-09-10: a converter's own Arguments and
    Options, under the option that declares it) follow its prose
    inside the definition, each a heading one level deeper than
    this section's and a definition list of its own.  `titles`
    dresses those headings (the template's words); `role` (e.g.
    'command') dresses rows whose display is plain text--the
    command listing's words wear 'command'.
    """
    from big.markdown import (parse, DefinitionList, DefinitionEntry,
                              Term, Definition, StyledText)
    titles = titles or {}

    def entries_for(rows, level):
        entries = []
        for display, lines, nested in rows:
            if role is not None:
                display = style(role, escape_styles(display))
            text = '\n'.join(lines)
            blocks = parse(text).blocks if text.strip() else []
            for kind, subrows in nested:
                title = titles.get(kind, kind.capitalize())
                blocks.extend(parse('#' * (level + 1) + ' ' + title).blocks)
                blocks.append(DefinitionList(entries_for(subrows, level + 1)))
            entries.append(DefinitionEntry(Term([StyledText(display)]),
                                           [Definition(blocks)]))
        return entries

    document = parse(header)
    document.blocks.append(DefinitionList(entries_for(rows, level)))
    return document


def render_help_page(usage, corpus, templates, margin=79,
                     file=None, stylesheet=None, suppress=(),
                     subcommands=False):
    """
    The --help page, the Markdown pivot's engine (ruled
    2026-08-05): the template establishes the page's ORDER and
    dresses its headings (Markdown, '## Options' by default);
    the corpus fills the slots--summary and doc are Markdown
    prose, the three tables become definition lists whose terms
    are the resolved displays, built as nodes in definition
    order.  Everything renders through big's pipeline.  usage is
    not Markdown: rendered and wrapped separately, at whole
    units, painted when themed.
    Empty sections are suppressed, header and all; suppress
    names slots to omit entirely (help()'s usage=/summary=/doc=
    knobs).

    BAKE + RUN in one call: help_page_pieces does the Markdown
    work (this machine), render_baked_help wraps and paints (any
    machine).
    """
    return render_baked_help(
        help_page_pieces(usage, corpus, templates, suppress, subcommands),
        margin, file=file, stylesheet=stylesheet)


def role_layout(layout):
    """
    Dress the summary section: every word wears the 'summary' role
    (join_styles fuses them back at render).  Table terms are dressed
    at bake time (built structurally, as StyledText terms), and doc
    prose flushes section-less, so only the summary reaches here.  Purely additive: a plain sheet strips the spans, so
    unthemed output is unchanged.
    """
    return tuple(style('summary', item)
                 if isinstance(item, str) and item.strip() else item
                 for item in layout)


def help_page_pieces(usage, corpus, templates, suppress=(),
                     subcommands=False):
    """
    The bake half of a help page: assemble the template-ordered
    Markdown, parse/style/lay it out via big, dress the flense's
    sections in role spans (role_layout), and return the
    template-ordered piece tuple render_baked_help consumes at
    runtime--('usage', prefix, usage-string) for the usage line,
    ('markdown', layout) for everything else, where layout is
    big's width-independent flat tuple.  Every value reprs into
    valid Python source.
    """
    from big.markdown import (layout_document, parse,
                              split_styles_document, style_document)
    pieces = []
    md = []            # pending markdown, flushed per role change

    def bake(document, section=None):
        layout = layout_document(
            split_styles_document(style_document(document)))
        if section == 'summary':
            layout = role_layout(layout)
        pieces.append(('markdown', layout))

    def flush(section=None):
        text = ''.join(md)
        md.clear()
        if text.strip():
            bake(parse(text), section)

    sections = parse_help_template(templates)
    if subcommands:
        # a command's own page lists its SUBcommands (Larry, 2026-09-10):
        # the template's 'Commands' heading reads 'Subcommands' there, a
        # heading worded otherwise is left alone
        sections = [(name, _reword_heading(header, 'Commands', 'Subcommands')
                     if name == 'commands' else header, indent)
                    for name, header, indent in sections]
    titles, levels = {}, {}
    for name, header, indent in sections:
        if name in ('arguments', 'options', 'commands'):
            # the template's own heading dresses the nested ones too
            lines = header.split('\n')
            for i in range(len(lines)):
                heading = _heading_at(lines, i)
                if heading is not None:
                    titles[name], levels[name] = heading[0], heading[1]
    for name, header, indent in sections:
        if name in suppress:
            continue
        if name == 'usage':
            flush()
            nl = header.rfind('\n')
            prefix = header[nl + 1:] if nl >= 0 else header
            pieces.append(('usage', prefix, usage))
            continue
        if name in ('summary', 'doc'):
            content = '\n'.join(corpus[name if name == 'summary'
                                        else 'documentation'])
            if not content.strip():
                continue
            if name == 'doc':
                md.append(header + content)
                continue
            flush()
            md.append(header + content)
            flush(name)
            continue
        if not corpus[name]:
            continue
        # a table section is built as nodes, on its own
        flush()
        bake(rows_document(corpus[name], header,
                           'command' if name == 'commands' else None,
                           titles, levels.get(name, 2)))
    flush()
    return tuple(pieces)



import inspect as _inspect
import re as _re

from .frontend import Terminal
from . import AppealConfigurationError


##
## The docstring parser (proposal §8.7, as ruled in §8.7.1).
##
## A docstring is input, never output: section headings and their
## entries are slurped out, the remaining prose coalesces into one
## blob, and the templates own the output's structure entirely.
##

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
        # the sections the docstring wrote (empty or not): a converter
        # nests under an option only where its docstring asked
        'requested': frozenset(k for k in SPECIAL_SECTIONS
                               if scanned[k] is not None),
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

    The edge decides (Larry, 2026-09-10).  An ARGUMENT edge merges:
    a positional converter's arguments and options become rows of
    the parent's own tables.  An OPTION edge nests: the option's row
    is the converter's whole docstring--summary and prose, then an
    Arguments and an Options block of its own, auto-filled from its
    signature, entries overriding--but only the blocks the
    converter's docstring asked for, by writing the section (empty
    or not).  A converter that asked for nothing clips its subtree:
    nothing of it reaches the parent's tables; its author documented
    it some other way.  The gate is checked only where an option's
    row is assembled--an argument edge always merges, so entries
    climb through undocumented positional converters.  (The parent
    may still document a nested name: nearest scope wins on the
    text.)

    Each docstring's entries are validated against the subtree of
    the callable that wrote them: an entry must name a visible
    argument, an option, or (for 'Commands:' entries, only when
    command_names is given) a command word.  Violations raise
    AppealConfigurationError.

    Returns the corpus, predigested and ready to format:

        summary        the command's own summary, as lines
        documentation  the command's own prose blob, as lines
        arguments      [(display, lines, nested), ...] in plan order
        options        [(display, lines, nested), ...] in plan order
        commands       [(word, lines, ()), ...] if command_names,
                       else []

    A row's display is a role-tagged span; its lines are Markdown;
    nested is a tuple of ('arguments'|'options', rows) blocks the
    row carries, in that order, each rows a list of the same shape.

    Every visible surface gets a row; undocumented rows carry
    empty lines.  command_names, if given, is an iterable of the
    command words this plan can dispatch to.
    """
    docs = {}              # rowkey -> lines, post-merge
    deprecated = set()     # rowkeys of deprecated options: noted in the row
    requested = {}         # path -> the sections that docstring asked for
    prose = {}             # path -> (summary lines, documentation lines)
    command_names = tuple(command_names) if command_names else ()

    class Level:
        # one table pair: a command's, or a nested converter's
        __slots__ = ('arguments', 'options')
        def __init__(self):
            self.arguments = []     # (rowkey, display) in plan order
            self.options = []       # (rowkey, display, anchors, sub) in
                                    # plan order: the flanking-argument
                                    # anchors qualify a duplicated
                                    # display; sub is a group option's
                                    # own Level

    def arg_name(s):
        # an operand's display: the name (or @app.parameter rename)
        # decorated into its placeholder (host -> <HOST>) and tagged
        # with the 'argument' role.  Built structurally here; injected
        # as a StyledText term (no post-layout reparse).
        return style('argument',
                     decorate_argument(s.usage_name, plan.decoration))

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

    # Row identity is the OCCURRENCE (Astra R07): the path of slot/option
    # ids from the root down.  A plan is shared by every use of its
    # callable (frontend memoizes), so a node id alone can't tell two
    # visible uses of the same converter apart; a path can.
    namespaces = {}    # path -> that occurrence's subtree namespace

    def walk(p, level, override=None, anchors=(None, None), path=()):
        # returns the subtree namespace for p: name -> (kind,
        # display, rowkey, owner) where owner is the declaring
        # plan's name (an 'ambiguous' entry carries the owners'
        # names instead, for the refusal).  override, when set,
        # is the transparency rule in flight: (rowkey, display) for
        # the subtree's sole terminal--the outer annotated
        # parameter's name flowing through.
        namespace = {}
        for o in p.options:
            # several rules may share one NAME (@app.option's each-call-
            # is-its-own-rule: go2's --north/--south both map direction);
            # the namespace entry is first-wins, but every rule gets its
            # own listing row--usage advertises them all, so must Options
            rowkey = path + (id(o),)
            display = _option_display(o, plan.decoration)
            if o.name not in namespace:
                namespace[o.name] = ('option', display, rowkey, p.name)
            sub = None
            if o.child is not None:
                # an option edge: the converter's own tables, nested
                sub = Level()
                for name, value in walk(o.child, sub, None, (None, None),
                                        rowkey).items():
                    namespace.setdefault(name, value)
            if o.restriction == 'hidden':
                continue                # documentable, never shown
            if o.restriction == 'deprecated':
                deprecated.add(rowkey)
            level.options.append((rowkey, display, anchors, sub))
        for index, s in enumerate(p.slots):
            here = path + (id(s),)
            if isinstance(s.child, Terminal):
                if override is not None:
                    # override display is already final (formatted or
                    # literal, decided where it was captured)
                    rowkey, display = override
                else:
                    rowkey, display = here, arg_name(s)
                namespace.setdefault(s.name, ('argument', display, rowkey,
                                              p.name))
                level.arguments.append((rowkey, display))
            else:
                inner = s.child.sole_terminal_slot()
                child_override = None
                if inner is not None and override is None:
                    # the transparency rule: this converter
                    # consumes exactly one operand, so the
                    # annotated parameter's name flows through.
                    # (An explicit rename on the inner parameter
                    # still wins the *display*.)
                    display = (style('argument',
                                     decorate_argument(inner.usage_name,
                                                       plan.decoration))
                               if inner.usage_name != inner.name
                               else arg_name(s))
                    child_override = (here, display)
                # an argument edge: the converter's rows merge into
                # this level
                child_namespace = walk(s.child, level,
                                       child_override or override,
                                       flanks(p, index), here)
                for name, value in child_namespace.items():
                    if (name in namespace
                            and namespace[name][0] == 'ambiguous'):
                        namespace[name][3].append(value[3])
                        continue
                    if (name in namespace
                            and namespace[name][2] != value[2]
                            and namespace[name][0] == value[0]):
                        # the same name from two sibling subtrees:
                        # documenting it HERE can't pick one
                        namespace[name] = ('ambiguous', value[1], None,
                                           [namespace[name][3], value[3]])
                        continue
                    namespace.setdefault(name, value)
                if child_override is not None:
                    namespace.setdefault(
                        s.name, ('argument', child_override[1], here, p.name))
                else:
                    namespace.setdefault(
                        s.name, ('internal', s.usage_name, here, p.name))
        assert path not in namespaces
        namespaces[path] = namespace
        return namespace

    def apply(p, path=()):
        # deepest first: children's entries land, then ours
        # overwrite (nearest enclosing scope wins).  Each scope's
        # entries resolve against ITS OWN subtree namespace--a
        # converter documents its own window even when the name is
        # ambiguous a level up.
        namespace = namespaces[path]
        # children (positional converters AND option converters)
        # land their docstrings first, deepest scope first; this
        # plan's own docstring then overwrites, so the nearest
        # enclosing scope wins on a name clash
        for s in p.slots:
            if not isinstance(s.child, Terminal):
                apply(s.child, path + (id(s),))
        for o in p.options:
            if o.child is not None:
                apply(o.child, path + (id(o),))
        where = getattr(p.callable, '__name__', repr(p.callable))
        parsed = parse_docstring(_inspect.getdoc(p.callable), where)
        requested[path] = parsed['requested']
        prose[path] = (parsed['summary'], parsed['documentation'])
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
                    owners = [repr(owner) for owner in entry[3]]
                    candidates = ', '.join(owners[:-1]) + ' and ' + owners[-1]
                    raise AppealConfigurationError(
                        f"{where}: {name!r} is ambiguous in {where!r}--"
                        f"{candidates} each have one; document it in "
                        f"the converter's docstring")
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

    def assemble(level):
        # the output rows of one Level: position qualifiers where a
        # display is duplicated (the flanking arguments' names say
        # which window each row is), the deprecation note, and--for a
        # group option--the converter's prose and the blocks it asked for
        arguments = [(display, docs.get(rowkey, []), ())
                     for rowkey, display in level.arguments]
        seen = {}
        for rowkey, display, anchors, sub in level.options:
            key = strip_styles(display).strip()
            seen[key] = seen.get(key, 0) + 1
        options = []
        for rowkey, display, anchors, sub in level.options:
            if seen[strip_styles(display).strip()] > 1 and anchors != (None, None):
                before, after = anchors
                if before and after:
                    display += f' (after {before}, before {after})'
                elif before:
                    display += f' (after {before})'
                else:               # the != (None, None) guard: one anchor
                    display += f' (before {after})'     # exists, and it's after
            if rowkey in deprecated:
                display += ' (deprecated)'
            lines = docs.get(rowkey)
            nested = ()
            if sub is not None:
                summary, documentation = prose[rowkey]
                if lines is None:
                    # the converter's whole docstring is the row's text
                    lines = summary + ([''] + documentation
                                       if documentation else [])
                sub_arguments, sub_options = assemble(sub)
                wants = requested[rowkey]
                nested = tuple(
                    (kind, rows) for kind, rows in (('arguments', sub_arguments),
                                                    ('options', sub_options))
                    if kind in wants and rows)
            options.append((display, lines or [], nested))
        return arguments, options

    top = Level()
    walk(plan, top)
    parsed = apply(plan)
    arguments, options = assemble(top)

    return {
        'summary': parsed['summary'],
        'documentation': parsed['documentation'],
        'presentation': parsed.get('presentation'),
        'arguments': arguments,
        'options': options,
        'commands': [(word, docs.get(word, []), ())
                     for word in command_names],
    }


def command_set_corpus(global_plan, entries, doc=None, listing=True,
                       tables_wanted=True):
    """
    The corpus for a multi-command program's listing.  entries is
    a sequence of (word, summary) pairs in declaration order.  The
    command rows' documentation comes from the global command's
    Commands: entries, falling back to each command's own summary.
    The listing shows the head's own Arguments and Options tables
    too (Larry, 2026-09-10: every page shows its tables), unless
    tables_wanted says the head has none of its own.
    """
    words = [word for word, _ in entries]
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
        tables = (merge_docs(global_plan) if global_plan is not None
                  else {'arguments': [], 'options': []})
        corpus = {'summary': parsed['summary'],
                  'documentation': parsed['documentation'],
                  'presentation': parsed.get('presentation'),
                  'arguments': tables['arguments'],
                  'options': tables['options'],
                  'commands': [(w, parsed['commands'].get(w, []), 0)
                               for w in words]}
    elif global_plan is not None:
        corpus = merge_docs(global_plan, command_names=words)
    else:
        corpus = {'summary': [], 'documentation': [],
                  'arguments': [], 'options': [],
                  'commands': [(word, [], 0) for word in words]}
    if not tables_wanted:
        # a program whose head is only Appeal's own -h/--version
        # precommand has no tables of its own to show
        corpus['arguments'] = []
        corpus['options'] = []
    fallback = dict(entries)
    corpus['commands'] = [
        (word, lines or ([fallback[word]] if fallback.get(word) else []), ())
        for word, lines, nested in corpus['commands']]
    return corpus


def summary(callable):
    """
    The callable's docstring summary--its whole first paragraph,
    joined--for command listings, which wrap it in their right column
    (Larry, 2026-09-09: a paragraph the author wrapped in the source
    used to be cut off at its first line).
    """
    doc = _inspect.getdoc(callable)
    if not doc:
        return ''
    lines = []
    for line in doc.splitlines():
        if not line.strip():
            break
        lines.append(line.strip())
    return ' '.join(lines)


def _operand_markup(plan, decoration=None):
    """
    A plan's operands as a usage fragment, role-tagged: `<HUE>`,
    `[<LEVEL>]`, `[<FILE>]...`; a nested converter's operands are
    spelled out (or its outer name, when it takes exactly one).
    """
    bits = []
    for s in plan.slots:
        if isinstance(s.child, Terminal) or s.child.sole_terminal_slot():
            text = style('oparg', decorate_argument(s.usage_name, decoration))
        else:
            text = _operand_markup(s.child, decoration)
        if s.repeat:
            text = f'[{text}]...'
        elif not s.required:
            text = f'[{text}]'
        bits.append(text)
    return ' '.join(bits)


def _option_display(o, decoration=None):
    """
    The option as shown in help tables, role-tagged:
    '⦃option⦙-t⦄|⦃option⦙--times⦄ ⦃oparg⦙<TIMES>⦄'.  Built
    structurally (option strings vs opargs), so nothing downstream
    has to re-derive it.
    """
    bits = ['|'.join(style('option', escape_styles(s)) for s in o.strings)]
    if o.child is not None:
        # a mini-usage of the group's operands (Larry, 2026-09-10)--never
        # its nested options: those are the row's own Options block
        text = _operand_markup(o.child, decoration)
        if text:
            bits.append(text)
    elif o.consumes_operands:
        names = ([o.usage_name] if o.usage_name is not None
                 else o.oparg_names())
        for name in names:
            bits.append(style('oparg', decorate_argument(name, decoration)))
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
        # option/argument display columns use troff minus signs; the
        # usage line carries appeal role spans (troff bolds via .B,
        # not our spans), so strip them to visible text first
        return esc(strip_styles(display)).replace('-', '\\-')

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

    def entries(pairs):
        for display, lines, nested in pairs:
            line('.TP')
            line(f'.B {opt(display)}')
            if lines:
                paragraphs(lines)
            for kind, subrows in nested:
                # a converter's own block, indented under its row
                line('.RS')
                line(f'.B {kind.capitalize()}:')
                entries(subrows)
                line('.RE')

    def rows(section, pairs):
        if not pairs:
            return
        line(f'.SH {section}')
        entries(pairs)

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
        for word, lines, nested in corpus['commands']:
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
                entries(pairs)
    return '\n'.join(out) + '\n'
