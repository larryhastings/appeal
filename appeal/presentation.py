#!/usr/bin/env python3
#
# appeal/presentation.py
# Part of Appeal v2.
#
# All human-facing output: markdown scanning/transform of docstrings,
# help-page assembly, usage + error rendering, themes and stylesheets.
# Lazy -- imported only when help, usage, or an error actually renders.

import re

from . import ConfigurationError


SPECIAL_SECTIONS = ('options', 'arguments', 'commands')

_ATX_RE = re.compile(r'^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$')
_SETEXT_RE = re.compile(r'^ {0,3}(=+|-+)\s*$')
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
    ANY heading whose text is Options/Arguments/Commands
    (case-insensitive), of ANY kind and level, opens the special
    section; it runs to the next heading of any kind or EOF.
    `where` names the docstring's owner in error messages.
    """
    prefix = f"{where}: docstring " if where else "docstring "
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
                        f"{prefix}has two {h[0]!r} sections")
                i += h[2]
                content = []
                while i < n and _heading_at(lines, i) is None:
                    content.append(lines[i])
                    i += 1
                sections[name] = _parse_definition_list(
                    content, (f"{where}: {h[0]}" if where
                              else h[0]) + ':')
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


def _StyleSheet(*args, **kwargs):
    return StyleSheet(*args, **kwargs)


def _style_span(*args, **kwargs):
    return style(*args, **kwargs)


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
    'heading_warning':   ('T', '⦃orange⦙T⦄'),
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
    'heading5':   ('T', '⦃italic⦙⦃heading_color⦙T⦄⦄'),
    'heading6':   ('T', '⦃heading_color⦙⦃lower⦙T⦄⦄'),
    # inline structure
    'code':       ('T', 'T'),               # themes color this
    'codeblock':  ('T', '⦃code⦙T⦄'),        # inherits code
    'link':       ('T', '⦃underline⦙T⦄'),
    'marker':     ('T', 'T'),
    'blockquote': ('T', 'T'),
    'term':       ('T', '⦃bold⦙T⦄'),        # deflist terms
    # GitHub alerts: bodies wear their kind's color too
    'note':              ('T', '⦃blue⦙T⦄'),
    'heading_note':      ('T', '⦃bold⦙⦃blue⦙T⦄⦄'),
    'tip':               ('T', '⦃green⦙T⦄'),
    'heading_tip':       ('T', '⦃bold⦙⦃green⦙T⦄⦄'),
    'important':         ('T', '⦃purple⦙T⦄'),
    'heading_important': ('T', '⦃bold⦙⦃purple⦙T⦄⦄'),
    'warning':           ('T', '⦃orange⦙T⦄'),
    'heading_warning':   ('T', '⦃bold⦙⦃orange⦙T⦄⦄'),
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
    marker    = ('T', '⦃yellow⦙T⦄'),
    link      = ('T', '⦃underline⦙⦃blue⦙T⦄⦄'),
    heading_color = ('T', '⦃cyan⦙T⦄'),
)


light_warm_theme = _theme(
    command   = ('T', '⦃bold⦙⦃dark_orange⦙T⦄⦄'),
    option    = ('T', '⦃dark_red⦙T⦄'),
    argument  = ('T', '⦃italic⦙⦃dark_gray⦙T⦄⦄'),
    summary   = ('T', '⦃bold⦙⦃dark_red⦙T⦄⦄'),
    error     = ('T', '⦃bold⦙⦃red⦙T⦄⦄'),
    code      = ('T', '⦃dark_orange⦙T⦄'),
    marker    = ('T', '⦃dark_yellow⦙T⦄'),
    link      = ('T', '⦃underline⦙⦃dark_purple⦙T⦄⦄'),
    heading_color = ('T', '⦃dark_red⦙T⦄'),
)


dark_warm_theme = _theme(
    command   = ('T', '⦃bold⦙⦃light_orange⦙T⦄⦄'),
    option    = ('T', '⦃light_red⦙T⦄'),
    argument  = ('T', '⦃italic⦙⦃light_gray⦙T⦄⦄'),
    summary   = ('T', '⦃bold⦙⦃light_orange⦙T⦄⦄'),
    error     = ('T', '⦃bold⦙⦃light_red⦙T⦄⦄'),
    code      = ('T', '⦃light_orange⦙T⦄'),
    marker    = ('T', '⦃light_yellow⦙T⦄'),
    link      = ('T', '⦃underline⦙⦃light_purple⦙T⦄⦄'),
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
    link      = ('T', '⦃underline⦙⦃dark_purple⦙T⦄⦄'),
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
    link      = ('T', '⦃underline⦙⦃light_purple⦙T⦄⦄'),
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
                | _StyleSheet(appeal_theme))
    return spec


def usage_units(usage):
    """
    Split a usage line into its unbreakable top-level units: the
    program name, bare operands, and complete bracket groups like
    '[-t|--times <int>]'.  Wrapping never splits a unit.
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
        if ch == '[':
            depth += 1
        elif ch == ']':
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
    line_span = _style_span('line')
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
            body = wrap_words(usage_units(usage),
                              margin, raw=measure,
                              indent=(prefix, ' ' * len(prefix)))
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


def rows_markdown(rows, role=None):
    """
    Corpus rows [(display, doc-lines), ...] as one Markdown
    definition list, definition order preserved (ruled
    2026-08-05).  An undocumented row is a term with an empty
    definition (': ' with nothing after it--bare ':' wouldn't
    parse as a definition list).  Entry lines are already
    Markdown; continuation lines re-indent under the ':'.

    A nested option's row (its display carries two leading
    spaces per depth level, from the merge) becomes a NESTED
    definition list inside its parent's details (ruled
    2026-08-06)--the rendered table indents sub-options beneath
    the option that declares them.

    Argument/option displays arrive pre-built as role spans.
    `role` (e.g. 'command') dresses rows whose display is plain
    text instead--the command listing's words wear 'command'.
    """
    if role is not None:
        rows = [(style(role, escape_styles(display)), lines)
                for display, lines in rows]
    # display is a role-tagged span; the markdown SOURCE term is its
    # plain text (strip_styles), and the span itself is collected, in
    # document order, to be injected as a StyledText term post-parse.
    def entry_block(display, lines, children):
        body = [l for l in lines] or ['']
        first = f": {body[0]}" if body[0] else ": "
        rest = [("  " + l) if l.strip() else '' for l in body[1:]]
        block = [strip_styles(display), first] + rest
        terms = [display]
        for child in children:
            block.append('')
            sub_block, sub_terms = entry_block(*child)
            block.extend(("  " + l) if l.strip() else '' for l in sub_block)
            terms.extend(sub_terms)
        return block, terms

    # rebuild the tree the merge flattened: depth = the display's
    # leading two-space pairs (on the plain text)
    roots = []
    stack = []                  # (depth, entry) path to the tip
    for display, lines in rows:
        plain = strip_styles(display)
        stripped_plain = plain.lstrip(' ')
        depth = (len(plain) - len(stripped_plain)) // 2
        entry = (display.lstrip(' '), lines, [])
        while stack and stack[-1][0] >= depth:
            stack.pop()
        (stack[-1][1][2] if stack else roots).append(entry)
        stack.append((depth, entry))
    blocks, terms = [], []
    for e in roots:
        b, t = entry_block(*e)
        blocks.append('\n'.join(b))
        terms.extend(t)
    return '\n\n'.join(blocks), terms


def render_help_page(usage, corpus, templates, margin=79,
                     file=None, stylesheet=None, suppress=()):
    """
    The --help page, the Markdown pivot's engine (ruled
    2026-08-05): the template establishes the page's ORDER and
    dresses its headings (Markdown, '## Options' by default);
    the corpus fills the slots--summary and doc are Markdown
    prose, the three tables become definition lists whose terms
    are the resolved displays, rows synthesized in definition
    order.  The assembled document renders through big's
    pipeline in one pass.  usage is not Markdown: rendered and
    wrapped separately, at whole units, painted when themed.
    Empty sections are suppressed, header and all; suppress
    names slots to omit entirely (help()'s usage=/summary=/doc=
    knobs).

    BAKE + RUN in one call: help_page_pieces does the Markdown
    work (this machine), render_baked_help wraps and paints (any
    machine).
    """
    return render_baked_help(
        help_page_pieces(usage, corpus, templates, suppress),
        margin, file=file, stylesheet=stylesheet)


def role_layout(layout):
    """
    Dress the summary section: every word wears the 'summary' role
    (join_styles fuses them back at render).  Table terms are dressed
    at bake time now (built structurally, injected as StyledText),
    and doc prose flushes section-less, so only the summary reaches
    here.  Purely additive: a plain sheet strips the spans, so
    unthemed output is unchanged.
    """
    return tuple(style('summary', item)
                 if isinstance(item, str) and item.strip() else item
                 for item in layout)


def _inject_styled_terms(document, terms):
    """
    Replace each definition-list term in `document` (in document
    order) with its pre-built role span, delivered as a big
    StyledText node so style_document carries it verbatim (no
    escaping, no re-derivation).  `terms` is the ordered list
    rows_markdown collected.
    """
    from big.markdown import StyledText, DefinitionList
    it = iter(terms)

    def walk(blocks):
        for b in blocks:
            if isinstance(b, DefinitionList):
                for entry in b.entries:
                    entry.term.children = [StyledText(next(it))]
                    for d in entry.definitions:
                        walk(d.blocks)

    walk(document.blocks)


def help_page_pieces(usage, corpus, templates, suppress=()):
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

    def flush(section=None, terms=None):
        text = ''.join(md)
        md.clear()
        if text.strip():
            document = parse(text)
            if terms:
                # a table section: its def-list terms are pre-built
                # role spans, injected as StyledText so big's layout
                # carries them verbatim (no post-layout reparse)
                _inject_styled_terms(document, terms)
            layout = layout_document(
                split_styles_document(style_document(document)))
            if section == 'summary':
                layout = role_layout(layout)
            pieces.append(('markdown', layout))

    for name, header, indent in parse_help_template(templates):
        if name in suppress:
            continue
        if name == 'usage':
            flush()
            nl = header.rfind('\n')
            prefix = header[nl + 1:] if nl >= 0 else header
            pieces.append(('usage', prefix, usage))
            continue
        terms = None
        if name == 'summary':
            content = '\n'.join(corpus['summary'])
        elif name == 'doc':
            content = '\n'.join(corpus['documentation'])
        else:
            content, terms = rows_markdown(
                corpus[name], 'command' if name == 'commands' else None)
        if not content.strip():
            continue
        if name == 'doc':
            md.append(header + content)
            continue
        # a roled section bakes alone, so its terms line up with its
        # own def-list
        flush()
        md.append(header + content)
        flush(name, terms)
    flush()
    return tuple(pieces)



import inspect as _inspect
import re as _re

from .frontend import Terminal, _oparg_names
from . import AppealConfigurationError


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
            display = _option_display(inner, plan.decoration)
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
            # several rules may share one NAME (@app.option's each-call-
            # is-its-own-rule: go2's --north/--south both map direction);
            # the namespace entry is first-wins, but every rule gets its
            # own listing row--usage advertises them all, so must Options
            rowkey = id(o)
            if o.name not in namespace:
                namespace[o.name] = ('option',
                                     _option_display(o, plan.decoration),
                                     rowkey)
            option_rows.append((rowkey,
                                _option_display(o, plan.decoration),
                                anchors))
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
                    display = (style('argument',
                                     decorate_argument(inner.usage_name,
                                                       plan.decoration))
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
        key = strip_styles(display).strip()
        seen[key] = seen.get(key, 0) + 1
    rows = []
    for rowkey, display, anchors in option_rows:
        if seen[strip_styles(display).strip()] > 1 and anchors != (None, None):
            before, after = anchors
            if before and after:
                display += f' (after {before}, before {after})'
            elif before:
                display += f' (after {before})'
            else:               # the != (None, None) guard: one anchor
                display += f' (before {after})'     # exists, and it's after
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


def command_set_corpus(global_plan, entries, doc=None, listing=True):
    """
    The corpus for a multi-command program's listing.  entries is
    a sequence of (word, summary) pairs in declaration order.  The
    command rows' documentation comes from the global command's
    Commands: entries, falling back to each command's own summary.
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
    corpus['commands'] = [
        (word, lines or ([fallback[word]] if fallback.get(word) else []))
        for word, lines in corpus['commands']]
    return corpus


def summary(callable):
    "The first line of the callable's docstring, for command listings."
    doc = _inspect.getdoc(callable)
    return doc.splitlines()[0] if doc else ''


def _option_display(o, decoration=None):
    """
    The option as shown in help tables, role-tagged:
    '⦃option⦙-t⦄|⦃option⦙--times⦄ ⦃oparg⦙<TIMES>⦄'.  Built
    structurally (option strings vs opargs), so nothing downstream
    has to re-derive it.
    """
    bits = ['|'.join(style('option', escape_styles(s)) for s in o.strings)]
    if o.kind == 'group':
        bits.append('...')                       # structural, stays bare
    elif o.kind not in ('flag', 'nullary'):
        names = ([o.usage_name] if o.usage_name is not None
                 else _oparg_names(o))
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
