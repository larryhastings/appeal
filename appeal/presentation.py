#!/usr/bin/env python3
#
# appeal/presentation.py
# Part of Appeal 1.0.
#
# All human-facing output: markdown scanning/transform of docstrings,
# help-page assembly, usage + error rendering, themes and stylesheets.
# Lazy -- imported only when help, usage, or an error actually renders.

from . import ConfigurationError


##
## The docstring dialect (proposal §8.7, as ruled in §8.7.1--the
## 2026-07-07 design review, Larry's).
##
## A docstring is Markdown, parsed ONCE by big (Larry, 2026-09-11:
## one recognizer of Markdown syntax, big's--Appeal's own line
## scanner, definition-list parser, and code-shielding regexes are
## gone).  Appeal owns only its dialect: a heading reading exactly
## Options / Arguments / Commands / Subcommands opens a special
## section, whose content must be exactly one definition list with
## plain-text terms; everything else is the prose.  The pieces are
## carried as big's NODES from here to the page (never written back
## out as text and re-parsed); text comes out only through big's
## writers (gfm, commonmark, troff).
##

SPECIAL_SECTIONS = ('options', 'arguments', 'commands')
_SPECIAL_HEADINGS = {'Options': 'options',
                     'Arguments': 'arguments',
                     'Commands': 'commands',
                     'Subcommands': 'commands'}   # either word, any context
                                                 # (Larry, 2026-09-10)


def _heading_text(heading):
    "A heading's text if it is exactly one plain Text child, else None."
    from big.markdown import Text
    children = heading.children
    if len(children) == 1 and isinstance(children[0], Text):
        return children[0].text.strip()
    return None


def markdown(blocks, format='commonmark'):
    "Blocks written out as Markdown text (big's writer); '' for none."
    from big.markdown import MarkdownDocument, render_document
    if not blocks:
        return ''
    return render_document(MarkdownDocument(list(blocks)), format)


def _refuse(where, heading, what):
    label = f"{where}: {heading}" if where else heading
    raise ConfigurationError(f"docstring section {label}: {what}")


def _definition_entries(blocks, where, heading):
    """
    The special section's content as [(term, definition blocks), ...]:
    exactly one definition list, each term one plain Text (no
    formatting), each entry's definitions' blocks concatenated.
    Empty content is legal: it asks for the section, auto-filled.
    """
    from big.markdown import DefinitionList, Text
    if not blocks:
        return []
    if len(blocks) != 1 or not isinstance(blocks[0], DefinitionList):
        found = ', '.join(type(b).__name__ for b in blocks
                          if not isinstance(b, DefinitionList))
        _refuse(where, heading,
                f"must contain only a definition list (found {found})")
    entries = []
    for entry in blocks[0].entries:
        children = entry.term.children
        if len(children) != 1 or not isinstance(children[0], Text):
            _refuse(where, heading,
                    f"term {markdown([entry.term]).strip()!r} must be "
                    f"unformatted")
        definition = []
        for d in entry.definitions:
            definition.extend(d.blocks)
        entries.append((children[0].text.strip(), definition))
    return entries


def scan_docstring(text, where=None):
    """
    The docstring, parsed by big and split by Appeal's dialect.
    Returns a dict of NODES:
      summary    the first paragraph's blocks ([] or [Paragraph])
      body       every other block EXCEPT the special sections
                 (their headings included), in source order
      options, arguments, commands
                 [(term, entry), ...] or None when the section
                 wasn't written--each entry the SAME dict, split
                 the same way from the definition's blocks (Larry,
                 2026-09-12: a definition is a docstring in
                 miniature, so an option's entry may carry its
                 converter's own sections, nested)
    A heading of any level, ATX or setext, reading exactly 'Options' /
    'Arguments' / 'Commands' / 'Subcommands' (this case) opens the
    special section; it runs to the next heading of any kind or the
    end.  Anything inside a code fence is code, by big's parse.  An
    empty section is legal: it asks for the section, auto-filled.
    `where` names the docstring's owner in error messages.
    """
    from big.markdown import parse
    return scan_blocks(parse(text or '').blocks, where)


def scan_blocks(blocks, where=None, heading=None):
    "scan_docstring on blocks already parsed; heading labels an entry's."
    from big.markdown import Heading, Paragraph
    prefix = f"{where}: docstring " if where else "docstring "
    if heading:
        prefix = f"{prefix}entry {heading}: "
    sections = {name: None for name in SPECIAL_SECTIONS}
    body = []
    i, n = 0, len(blocks)
    while i < n:
        block = blocks[i]
        name = (_SPECIAL_HEADINGS.get(_heading_text(block))
                if isinstance(block, Heading) else None)
        if name is None:
            body.append(block)
            i += 1
            continue
        label = '#' * block.level + ' ' + _heading_text(block)
        if sections[name] is not None:
            raise ConfigurationError(
                f"{prefix}has two {label!r} sections")
        i += 1
        content = []
        while i < n and not isinstance(blocks[i], Heading):
            content.append(blocks[i])
            i += 1
        sections[name] = [
            (term, scan_blocks(definition, where, f"{label}: {term!r}"))
            for term, definition in _definition_entries(content, where,
                                                        label + ':')]
    summary = []
    if body and isinstance(body[0], Paragraph):
        summary = [body.pop(0)]
    result = {'summary': summary, 'body': body}
    result.update(sections)
    return result


def to_github(text):
    """
    A docstring's Markdown re-spelled for GitHub flavor: big's gfm
    writer (definition lists become inline-HTML <dl>; strikethrough
    and alerts GitHub renders natively--Larry's ruling, 2026-08-05).
    """
    from big.markdown import parse, render_document
    return render_document(parse(text), 'gfm')


def to_commonmark(text):
    """
    A docstring's Markdown re-spelled for plain CommonMark: big's
    commonmark writer (definition lists become bold term +
    blockquote, strikethrough is dropped, alerts become bold-labelled
    blockquotes).
    """
    from big.markdown import parse, render_document
    return render_document(parse(text), 'commonmark')


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
    return stylesheet.render(joined)


import shutil

from big.builtin import can_colorize
from big.markdown import (glyphs_from_stylesheet, layout_document,
                          markdown_defaults, parse,
                          split_styles_document, style_document)
from big.stylesheet import (StyleSheet, ansi_16_color_palette,
                            escape_styles, join_styles,
                            strip_styles, style,
                            transforms)
# big's strip-everything palette; the name is a parameter here
from big.stylesheet import plain_stylesheet as plain_stylesheet_palette
from big.text import (OverflowStrategy, _iterate_over_bytes,
                      expand_tabs, format_definition_list,
                      merge_columns, split_text_with_code,
                      wrap_words)

from . import AppealConfigurationError

import re as _re

style_delimiters = '⦃⦙⦄'


_decoration_sheets = {}

def decorate_argument(name, entry=None):
    # render a RAW operand name through the argument_decoration
    # transform (host -> <HOST>), the single definition of operand
    # placeholder decoration.  Applied EARLY--baked into the text--so
    # big's layout sizes def-list columns at the decorated width and
    # the usage wrap sees real widths, both colored and plain (ruled
    # 2026-08-24, Larry confirmed 2026-09-18 with ruling 13).  Color stays a late, per-theme concern (the
    # 'argument'/'oparg' roles); only the decoration bakes in here.
    #
    # The SHAPE is the caller's stylesheet entry (ruled 2026-09-03,
    # Larry confirmed 2026-09-18: "it's in the stylesheet precisely
    # so users can tweak it")--
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


def ruled_headings(theme):
    "A copy of `theme` whose headings are drawn with rules, not underlined."
    return {**theme,
            'heading_underline': ('T', 'T'),
            'heading1_rule':      ('=',),
            'heading1_rule_next': ('=',),
            'heading2_rule':      ('-',),
            'heading3_rule':      ('-',)}



# The themes, in layers (Larry, 2026-09-12).  A theme is a dict of
# StyleSheet entries; the palette beneath it decides what an
# attribute or a color renders to.
#
#   plain_theme      the root: every role a no-op, no attribute, no
#                    color.  Headings are drawn with rules ('=' over
#                    and under H1, '-' under H2 and H3), the one
#                    structure that survives a palette expressing
#                    nothing.
#   uncolored_theme  a copy of plain plus the attributes--bold,
#                    italic, underline live HERE and nowhere else.
#                    Headings are underlined, so the rules go.
#   _theme(...)      a colored theme: the uncolored base with each
#                    named role WRAPPED in its color, attributes
#                    inherited.  The alert colors are shared.
#
# ruled_headings(theme) puts the drawn rules back on any theme.

_base_theme = {
    'rule': ('T', 'T'),        # paint role over the thematic break; big sizes it to the line (>= 0.15)

    # headings: the structural entry, bare, over the color hook
    'heading_color':     ('T', 'T'),
    'heading_underline': ('T', 'T'),

    'heading1': ('T', '⦃heading_underline⦙⦃heading_color⦙⦃strip⦙T⦄⦄⦄'),
    'heading1_rule_ink': ('T', '⦃heading_color⦙T⦄'),

    'heading2': ('T', '⦃heading_underline⦙⦃heading_color⦙⦃strip⦙T⦄⦄⦄'),
    'heading2_rule_ink': ('T', '⦃heading_color⦙T⦄'),

    'heading3': ('T', '⦃heading_underline⦙⦃heading_color⦙⦃strip⦙T⦄⦄⦄'),
    'heading3_rule_ink': ('T', '⦃heading_color⦙T⦄'),

    'heading4': ('T', '⦃heading_color⦙T⦄'),

    'heading5': ('T', '⦃heading_color⦙T⦄'),

    'heading6': ('T', '⦃heading_color⦙⦃lower⦙T⦄⦄'),

    # inline structure
    'code':       ('T', 'T'),
    'codeblock':  ('T', '⦃code⦙T⦄'),        # inherits code
    'link':       ('URL', 'T', 'T'),        # URL is luggage (big >= 0.15)
    'marker':     ('T', 'T'),
    'blockquote': ('T', 'T'),
    'term':       ('T', 'T'),               # deflist terms

    # GitHub alerts
    'note':              ('T', 'T'),
    'heading_note':      ('T', 'T'),
    'tip':               ('T', 'T'),
    'heading_tip':       ('T', 'T'),
    'important':         ('T', 'T'),
    'heading_important': ('T', 'T'),
    'warning':           ('T', 'T'),
    'heading_warning':   ('T', 'T'),
    'caution':           ('T', 'T'),
    'heading_caution':   ('T', 'T'),

    # the roles
    'program':    ('T', 'T'),
    'command':    ('T', 'T'),
    'option':     ('T', 'T'),
    'argument':   ('T', 'T'),

    # the distinct decoration transform: a raw operand name -> its
    # placeholder (host -> <HOST>), formerly the app's
    # positional_argument_usage_format.  Kept out of the 'argument'
    # color role so themes stay color-only.
    'argument_decoration': ('T', '<⦃upper⦙T⦄>'),
    'oparg':      ('T', '⦃argument⦙T⦄'),    # ruled: defaults to argument
                                            # (its own role; Larry confirmed 2026-09-18)
    'summary':    ('T', 'T'),
    'error':      ('T', 'T'),
}

plain_theme = ruled_headings(_base_theme)

uncolored_theme = {
    **_base_theme,
    # headings underline (Larry, 2026-09-12: a heading is short, and
    # a long one that wraps underlines across the wrap) and the rules
    # go; bold on top
    'heading_underline': ('T', '⦃underline⦙T⦄'),

    'heading1': ('T', '⦃bold⦙⦃heading_underline⦙⦃heading_color⦙⦃strip⦙T⦄⦄⦄⦄'),
    'heading1_rule':      ('',),
    'heading1_rule_next': ('',),

    'heading2': ('T', '⦃bold⦙⦃heading_underline⦙⦃heading_color⦙⦃strip⦙T⦄⦄⦄⦄'),
    'heading2_rule':      ('',),
    'heading3_rule':      ('',),

    'heading3': ('T', '⦃bold⦙⦃heading_underline⦙⦃heading_color⦙⦃strip⦙T⦄⦄⦄⦄'),

    'heading4': ('T', '⦃italic⦙⦃heading_color⦙T⦄⦄'),

    'link':       ('URL', 'T', '⦃underline⦙T⦄'),
    'term':       ('T', '⦃bold⦙T⦄'),

    'heading_note':      ('T', '⦃bold⦙T⦄'),
    'heading_tip':       ('T', '⦃bold⦙T⦄'),
    'heading_important': ('T', '⦃bold⦙T⦄'),
    'heading_warning':   ('T', '⦃bold⦙T⦄'),
    'heading_caution':   ('T', '⦃bold⦙T⦄'),

    'program':    ('T', '⦃bold⦙T⦄'),
    'command':    ('T', '⦃bold⦙T⦄'),
    'option':     ('T', '⦃bold⦙T⦄'),
    'argument':   ('T', '⦃bold⦙T⦄'),       # every placeholder alike
    'summary':    ('T', '⦃bold⦙T⦄'),
    'error':      ('T', '⦃bold⦙T⦄'),
}


# the colors every colored theme shares: the GitHub alert kinds
_alert_colors = {
    'note': 'blue',
    'heading_note': 'blue',

    'tip': 'green',
    'heading_tip': 'green',

    'important': 'purple',
    'heading_important': 'purple',

    'warning': 'dark_yellow',
    'heading_warning': 'dark_yellow',

    'caution': 'red',
    'heading_caution': 'red',
}


def _theme(**colors):
    """
    A colored theme: the uncolored base, each named role wrapped in
    its color (a color NAME--the attributes come from the base).  A
    tuple instead of a name is a whole entry, used as given.
    """
    t = dict(uncolored_theme)
    for role, color in {**_alert_colors, **colors}.items():
        if isinstance(color, tuple):
            t[role] = color
            continue
        entry = uncolored_theme[role]
        t[role] = entry[:-1] + (f'⦃{color}⦙{entry[-1]}⦄',)
    return t


appeal_theme = _theme(
    command   = 'blue',
    option    = 'cyan',
    argument  = 'dark_purple',
    error     = 'red',
    code      = 'green',                    # Larry, 2026-09-18: code is green in
                                            # the Appeal and cool themes, amber
                                            # in the warm ones
    marker    = 'dark_purple',
    link      = 'blue',
    heading_color = 'blue',
)


light_warm_theme = _theme(
    command   = 'dark_orange',
    option    = 'dark_red',
    argument  = 'dark_gray',
    summary   = 'dark_red',
    error     = 'red',
    code      = 'dark_amber',
    marker    = 'dark_yellow',
    link      = 'dark_purple',
    heading_color = 'dark_red',
)


dark_warm_theme = _theme(
    command   = 'light_orange',
    option    = 'light_red',
    argument  = 'light_gray',
    summary   = 'light_orange',
    error     = 'light_red',
    code      = 'light_amber',
    marker    = 'light_yellow',
    link      = 'light_purple',
    heading_color = 'light_red',
)


light_cool_theme = _theme(
    command   = 'dark_cyan',
    option    = 'dark_blue',
    argument  = 'dark_gray',
    summary   = 'dark_blue',
    error     = 'red',                      # errors stay red, even here
    code      = 'dark_green',
    marker    = 'dark_cyan',
    link      = 'dark_purple',
    heading_color = 'dark_blue',
)


dark_cool_theme = _theme(
    command   = 'light_cyan',
    option    = 'light_blue',
    argument  = 'light_gray',
    summary   = 'light_cyan',
    error     = 'light_red',
    code      = 'light_green',
    marker    = 'light_cyan',
    link      = 'light_purple',
    heading_color = 'light_blue',
)


def resolve_stylesheet(stylesheet, file=None, plain_stylesheet=None):
    """
    The runtime half of the stylesheet decision: which of the
    program's two sheets paints THIS stream at THIS moment (Larry,
    2026-09-12).  `stylesheet` is for a stream that wants color,
    `plain_stylesheet` for one that doesn't--can_colorize decides
    (NO_COLOR and friends always win, then whether the stream is a
    tty).  Either may be None for the stock composition: appeal_theme
    over the ANSI 16 (the 16 because the terminal remaps them to its
    own scheme, so the theme stays legible on light and dark alike,
    ruled 2026-08-06, Larry confirmed 2026-09-18) or plain_theme over the plain palette (no
    escapes of any kind, headings ruled).  stylesheet=False means
    never any color: the plain sheet for every stream.  A sheet given
    for a slot is used VERBATIM in that slot (the same sheet in both
    colors a pipe too).
    """
    if (stylesheet is not False) and can_colorize(file=file):
        if stylesheet is not None:
            return stylesheet
        palette, theme = ansi_16_color_palette, appeal_theme
    else:
        if plain_stylesheet is not None:
            return plain_stylesheet
        palette, theme = plain_stylesheet_palette, plain_theme
    return markdown_defaults | transforms | palette | StyleSheet(theme)


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
    '\n'
    '## Topics\n'
    '{topics}\n'
)


_TEMPLATE_SECTIONS = ('usage', 'summary', 'doc', 'options',
                      'arguments', 'commands', 'topics')


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
    the stream wants color, plain_theme over the plain palette
    (no escapes of any kind) when it doesn't--pipes and captures
    stay clean.  Exactly resolve_stylesheet(None, file).
    """
    return resolve_stylesheet(None, file)


def render_baked_help(pieces, margin=79, file=None,
                      stylesheet=None, plain_stylesheet=None):
    """
    The runtime half of a help page.  pieces is the baked,
    template-ordered tuple from help_page_pieces: ('usage',
    prefix, usage-string) entries already carry their role spans
    (built by Plan.usage) and are wrapped at whole units;
    ('markdown', layout) entries carry big's width-independent
    layout tuples--wrap_words lays them out at the real margin
    (the sheet-rendered width measuring the words), join_styles
    fuses adjacent spans.  Either way the stylesheet paints last:
    stylesheet and plain_stylesheet are the pair resolve_stylesheet
    picks from, by whether `file` wants color.
    """
    sheet = resolve_stylesheet(stylesheet, file, plain_stylesheet)
    # a horizontal rule spans the margin (ruled 2026-08-08; Larry
    # confirmed 2026-09-18)--drawn by big's wrap_words since big
    # 4e40e20 (2026-09-18), which sizes a rule to its context; the
    # margin-wide `line` role Appeal used to inject is gone
    glyphs = glyphs_from_stylesheet(sheet)
    # measure a unit's visible width: glyphs_from_stylesheet knows
    # big's markdown glyph roles; operand placeholders are already
    # DECORATED into literal text (host -> <HOST>) by the time they
    # get here, so their width measures correctly too
    measure = lambda w: strip_styles(glyphs(w))
    out = []
    for piece in pieces:
        if piece[0] == 'usage':
            prefix, units = piece[1], list(piece[2])
            # the usage line's UNITS carry their role spans (built by
            # Plan.usage_units) with operands decorated inline; wrap at
            # whole units, never inside one
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
    Partition a help template at its seven {section} placeholders.
    Returns [(name, header, indent), ...] in template order:
    header is the text since the previous placeholder (leading
    newlines included); indent is the header text after its last
    newline--the body's per-line indent.  All seven sections must
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


def _template_headings(header):
    "The (text, level) of every heading in a template section header."
    from big.markdown import parse, Heading
    return [(_heading_text(b), b.level) for b in parse(header).blocks
            if isinstance(b, Heading) and _heading_text(b) is not None]


def _reword_heading(header, old, new):
    """
    The template header with a heading reading exactly `old` reworded:
    parsed by big, the heading's text replaced, written back as gfm
    (whitespace normalized; the page renders it through big anyway).
    """
    from big.markdown import parse, render_document, Heading, Text
    document = parse(header)
    hit = False
    for block in document.blocks:
        if isinstance(block, Heading) and _heading_text(block) == old:
            block.children[:] = [Text(new)]
            hit = True
    if not hit:
        return header
    return '\n' + render_document(document, 'gfm') + '\n'


def rows_document(rows, header, role=None, titles=None, level=2):
    """
    A table section as a big document: the template's header
    (Markdown) followed by the corpus rows as a DefinitionList,
    built as NODES (Astra R08: no Markdown round trip, no
    re-deriving the hierarchy from indentation).  Each row's
    display--a role-tagged span--is the entry's term, carried as
    StyledText so big styles it verbatim; its doc lines are
    NODES already (the merge carries big's blocks).  A row's nested
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
            blocks = list(lines)                # big's nodes, as merged
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


def render_help_page(usage_units, corpus, templates, margin=79,
                     file=None, stylesheet=None, suppress=(),
                     subcommands=False, plain_stylesheet=None):
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
        help_page_pieces(usage_units, corpus, templates, suppress, subcommands),
        margin, file=file, stylesheet=stylesheet, plain_stylesheet=plain_stylesheet)


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


def help_page_pieces(usage_units, corpus, templates, suppress=(),
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

    def bake(document, section=None):
        layout = layout_document(
            split_styles_document(style_document(document)))
        if section == 'summary':
            layout = role_layout(layout)
        pieces.append(('markdown', layout))

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
        if name in ('arguments', 'options', 'commands', 'topics'):
            # the template's own heading dresses the nested ones too
            for text, level in _template_headings(header):
                titles[name], levels[name] = text, level
    for name, header, indent in sections:
        if name in suppress:
            continue
        if name == 'usage':
            nl = header.rfind('\n')
            prefix = header[nl + 1:] if nl >= 0 else header
            pieces.append(('usage', prefix, tuple(usage_units)))
            continue
        if name in ('summary', 'doc'):
            blocks = corpus[name if name == 'summary' else 'documentation']
            if not blocks:
                continue
            # the template's header is Markdown text; the prose is big's
            # nodes--append them to the parsed header, never re-parse
            document = parse(header)
            document.blocks.extend(blocks)
            bake(document, name)
            continue
        if not corpus[name]:
            continue
        # a table section is built as nodes, on its own
        # a listing's words--commands and help topics alike--wear
        # the 'command' role: both are words you type after `help`
        bake(rows_document(corpus[name], header,
                           'command' if name in ('commands', 'topics')
                           else None,
                           titles, levels.get(name, 2)))
    return tuple(pieces)



import inspect as _inspect
import re as _re

from .frontend import Terminal, _is_leaf
from . import AppealConfigurationError


##
## The docstring parser (proposal §8.7, as ruled in §8.7.1--the
## 2026-07-07 design review, Larry's).
##
## A docstring is input, never output: section headings and their
## entries are slurped out, the remaining prose coalesces into one
## blob, and the templates own the output's structure entirely.
##

def parse_docstring(doc, where):
    """
    Parses one docstring into corpus pieces, as big's NODES:

        summary        the first paragraph of the prose: [] or [Paragraph]
        documentation  the rest of the prose, in source order, with
                       the special sections slurped out
        arguments      dict of name -> the entry, parsed the same way
        options        dict of name -> the entry, parsed the same way
        commands       dict of name -> the entry, parsed the same way
        requested      the sections the docstring wrote (empty or not)

    THE DOCSTRING IS MARKDOWN (the pivot, ruled 2026-08-05; Markdown
    ONLY--the 'Arguments:' + 'name: desc' grammar died unshipped, and
    a docstring written that way renders as prose, no warning; Larry
    confirmed 2026-09-17), parsed by big.  The first paragraph is the summary; a
    heading of any level reading exactly Options, Arguments, Commands
    or Subcommands opens a special section, which must contain exactly
    one definition list with plain-text terms; everything else is the
    doc, other headings included.  An entry's definition is a docstring
    in miniature (Larry, 2026-09-12): its first paragraph and prose are
    the row's text, and it may carry the same special sections, which
    document the converter behind that option.  The user's heading
    decoration is input spelling only--the help template dresses the
    page.

    'where' names the docstring's owner, for error messages.
    Grammar violations raise AppealConfigurationError; validation
    against the plan (unknown names, kinds, visibility) happens
    later, in the merge.
    """
    return _doc_from_scan(scan_docstring(doc or '', where), where)


def _doc_from_scan(scanned, where):
    def entry_dict(pairs, section):
        entries = {}
        for name, entry in (pairs or ()):
            if name in entries:
                raise AppealConfigurationError(
                    f"{where}: in the {section} section: "
                    f"{name!r} is documented twice")
            entries[name] = _doc_from_scan(entry, where)
        return entries

    return {
        'summary': scanned['summary'],
        'documentation': scanned['body'],
        'arguments': entry_dict(scanned['arguments'], 'Arguments'),
        'options': entry_dict(scanned['options'], 'Options'),
        'commands': entry_dict(scanned['commands'], 'Commands'),
        # the sections the docstring wrote (empty or not): a converter
        # nests under an option only where its docstring asked
        'requested': frozenset(k for k in SPECIAL_SECTIONS
                               if scanned[k] is not None),
        # the template establishes the page's order and dresses
        # the headings, program-wide (ruled 2026-08-05; Larry
        # confirmed 2026-09-17): nothing to present
        'presentation': {'order': (), 'headers': {}, 'indents': {}},
    }


def _prose(doc):
    "A doc's text: its summary and the rest of its prose, as blocks."
    return list(doc['summary']) + list(doc['documentation'])


##
## References in prose (Larry, 2026-09-19, big's bracketed spans):
## `[c]{.argument}` in a docstring names the operand `c` of the
## docstring's owner and renders as usage spells it, decoration and
## all; `[region]{.option}` names the option and renders its strings,
## `-r|--region`, without oparg or nested options.  A span with any
## other class is plain tagging--big paints its text in that role
## unchanged.  A reference wears exactly one class and plain text.
##

REFERENCE_CLASSES = ('argument', 'option')


def resolve_references(doc, lookup, where):
    """
    Rewrite the reference spans of a parsed docstring in place--its
    summary, its prose, and every section entry, recursively.
    `lookup(kind, name)` returns the reference's markup, or None when
    the owner has no such parameter; `where` names the docstring.
    """
    from big.markdown import Node, Span, StyledText, Text

    def replace(span):
        (kind,) = span.classes
        if len(span.children) != 1 or not isinstance(span.children[0], Text):
            raise AppealConfigurationError(
                f"{where}: a {kind} reference names a parameter in "
                f"plain text: [name]{{.{kind}}}")
        name = span.children[0].text
        markup = lookup(kind, name)
        if markup is None:
            raise AppealConfigurationError(
                f"{where}: [{name}]{{.{kind}}} names no {kind} of its own")
        return StyledText(markup)

    def walk(node):
        for field in node._fields:
            value = getattr(node, field)
            if isinstance(value, list):
                for i, item in enumerate(value):
                    # a list field holds nodes, never anything else
                    if (isinstance(item, Span) and len(item.classes) == 1
                            and item.classes[0] in REFERENCE_CLASSES):
                        value[i] = replace(item)
                    else:
                        walk(item)
            elif isinstance(value, Node):
                walk(value)

    for block in doc['summary'] + doc['documentation']:
        walk(block)
    for kind in SPECIAL_SECTIONS:
        for entry in doc[kind].values():
            resolve_references(entry, lookup, where)
    return doc


def has_references(summary):
    """
    Does a summary--[] or [Paragraph], inline nodes whose containers
    all hold a `children` list--reference a parameter?
    """
    from big.markdown import Span
    def walk(node):
        if isinstance(node, Span):
            return len(node.classes) == 1 and node.classes[0] in REFERENCE_CLASSES
        return any(walk(child) for child in getattr(node, 'children', ()))
    return any(walk(block) for block in summary)


def plan_references(plan):
    """
    The lookup for a plan's docstring: its own parameters.  An
    argument renders as the usage line's placeholder (its usage
    name, decorated); an option as its strings alone.
    """
    def lookup(kind, name):
        if kind == 'argument':
            for s in plan.slots:
                if s.name == name:
                    return style('argument', decorate_argument(s.usage_name, plan.decoration))
            return None
        for o in plan.options:
            if o.name == name:
                return '|'.join(style('option', escape_styles(s)) for s in o.strings)
        return None
    return lookup


def plans_references(plans):
    "The lookup over several plans--the head's, for the program's documentation."
    lookups = [plan_references(p) for p in plans]
    def lookup(kind, name):
        for one in lookups:
            markup = one(kind, name)
            if markup is not None:
                return markup
        return None
    return lookup


def no_references(kind, name):
    "The lookup where nothing can be referenced: a topic's text."
    return None


def merge_docs(plan, command_names=None):
    """
    The merge: walks the plan tree and lays every occurrence's
    documentation into the rows of the command's tables.

    Each occurrence of a converter has ONE docstring, the one CHOSEN
    for it (Larry, 2026-09-12, the textual model: a parent documents
    a child as if the child's docstring were inserted as the entry's
    definition, so an entry replaces that docstring whole):

      * a converter behind an OPTION: the parent's Options entry for
        that option if the parent wrote one, else the parent's
        @app.option(doc=) for it, else the converter's own docstring
        (writing both is a ConfigurationError--two definitions);
      * a converter behind a positional ARGUMENT: the parent's
        @app.argument(doc=) for it, else its own docstring;
      * the command itself: its own docstring.

    The edge decides how the chosen doc lands (Larry, 2026-09-10).
    An ARGUMENT edge merges: the converter's arguments and options
    become rows of the parent's own tables, and the parent's
    entries may document them by name--a name of the parent's own
    parameter shadows a merged one (nearest wins); the same name
    from two merged siblings is ambiguous at the parent and is
    documented from inside.  An OPTION edge nests: the option's row
    is the chosen doc's prose, and beneath it an Arguments and an
    Options block of the converter's own, auto-filled from its
    signature, for exactly the sections the chosen doc wrote.

    Two more rules for arguments.  A converter reached through an
    argument that consumes exactly ONE operand is transparent: the
    operand wears the annotated parameter's name (outermost wins
    through a chain of such converters), and its documentation is
    the nearest that speaks--the parent's entry, then each
    converter's entry for its one parameter, or, when a converter
    wrote no Arguments section at all, its prose (Larry,
    2026-09-12).  That prose rule is for arguments: an option's row
    keeps the converter's prose for itself.

    Each doc's entries are validated against the occurrence it
    documents: an entry must name a visible argument, an option,
    or (for 'Commands:' entries, only when command_names is given)
    a command word; an Arguments entry carries no sections of its
    own (it documents an operand--@app.argument(doc=) replaces the
    converter's docstring); an Options entry carries sections only
    for an option with a converter.  Violations raise
    AppealConfigurationError.

    Returns the corpus, predigested and ready to format:

        summary        the command's own summary, as blocks
        documentation  the command's own prose, as blocks
        arguments      [(display, blocks, nested), ...] in plan order
        options        [(display, blocks, nested), ...] in plan order
        commands       [(word, blocks, ()), ...] if command_names,
                       else []

    A row's display is a role-tagged span; nested is a tuple of
    ('arguments'|'options', rows) blocks the row carries, in that
    order, each rows a list of the same shape.  Every visible
    surface gets a row; undocumented rows carry empty blocks.
    """
    docs = {}              # rowkey -> blocks, nearest scope first
    deprecated = set()     # rowkeys of deprecated options: noted in the row
    requested = {}         # option rowkey -> the sections its chosen doc wrote
    aimed_docs = {}        # option rowkey -> (doc, where): a dotted entry
                           # from above carrying sections
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
    namespaces = {}    # path -> that occurrence's OWN names
    sole = {}          # path -> the rowkey of its one operand, if transparent

    def walk(p, level, override=None, anchors=(None, None), path=()):
        # fills the Levels (rows in plan order) and records, per
        # occurrence, its OWN parameter names: name -> (kind, rowkey,
        # naming) where kind is 'argument' / 'option' / 'internal' (a
        # converter standing for several words: no row of its own), and
        # naming says whether this parameter is the one that names its
        # word on the page (Larry, 2026-09-16: the parameter nearest the
        # command that stands for that one word).  A docstring's bare
        # names are its own parameters only; deeper ones are reached
        # by dotted path (never flat--"color is not an option of cmd").
        # override, when set, is the transparency rule in flight: the
        # (rowkey, display) of the one word this whole subtree stands
        # for, named by an outer parameter.
        namespace = {}
        for o in p.options:
            # several rules may share one NAME (@app.option's each-call-
            # is-its-own-rule: go2's --north/--south both map direction);
            # the namespace entry is first-wins, but every rule gets its
            # own listing row--usage advertises them all, so must Options
            rowkey = path + (id(o),)
            display = _option_display(o, plan.decoration)
            namespace.setdefault(o.name, ('option', rowkey, True))
            sub = None
            if o.child is not None:
                # an option edge: the converter's own tables nest under
                # the row.  A converter taking exactly one operand: the
                # option's parameter names that operand's row (the naming
                # rule, for options too--Larry, 2026-09-18)
                sub = Level()
                sub_override = None
                if o.child.sole_terminal_slot() is not None:
                    top = next(s for s in o.child.slots
                               if isinstance(s.child, Terminal)
                               or s.child.count_terminals())
                    name = o.usage_name if o.usage_name is not None else o.name
                    sub_override = (rowkey + (id(top),),
                                    style('argument',
                                          decorate_argument(name, plan.decoration)))
                walk(o.child, sub, sub_override, (None, None), rowkey)
            if o.restriction == 'hidden':
                continue                # documentable, never shown
            if o.restriction == 'deprecated':
                deprecated.add(rowkey)
            level.options.append((rowkey, display, anchors, sub))
        for index, s in enumerate(p.slots):
            here = path + (id(s),)
            if isinstance(s.child, Terminal):
                if override is not None:
                    rowkey, display = override
                    namespace.setdefault(s.name, ('argument', rowkey, False))
                else:
                    rowkey, display = here, arg_name(s)
                    namespace.setdefault(s.name, ('argument', rowkey, True))
                level.arguments.append((rowkey, display))
                if p.sole_terminal_slot() is s:
                    sole[path] = rowkey
                continue
            inner = s.child.sole_terminal_slot()
            child_override = None
            if inner is not None and override is None:
                # the transparency rule: this converter consumes exactly
                # one operand, so this parameter names the word
                child_override = (here, arg_name(s))
            # an argument edge: the converter's rows merge into this
            # level, in place
            walk(s.child, level, child_override or override,
                 flanks(p, index), here)
            if child_override is not None:
                namespace.setdefault(s.name, ('argument', here, True))
            elif inner is not None:
                # inside a transparent chain: an outer parameter names
                # the word; this one may still document it (nearest
                # that speaks) but can't be aimed at from above
                namespace.setdefault(s.name, ('argument', override[0], False))
            else:
                namespace.setdefault(s.name, ('internal', here, False))
            if here in sole and p.sole_terminal_slot() is not None:
                # p consumes exactly one operand, and it's under s
                sole[path] = sole[here]
        assert path not in namespaces
        namespaces[path] = namespace
        return namespace

    def step(p, path, segment):
        # one step of a dotted path: the plan and occurrence path of
        # the converter behind p's parameter `segment`, or None
        for s in p.slots:
            if s.name == segment:
                if isinstance(s.child, Terminal):
                    return None
                return s.child, path + (id(s),)
        for o in p.options:
            if o.name == segment:
                if o.child is None:
                    return None
                return o.child, path + (id(o),)
        assert False, f"{segment!r} is in the namespace but not the plan"  # pragma: no cover

    def resolve(p, path, dotted, where, heading):
        # a section entry's name: bare (this docstring's own parameter)
        # or dotted (a walk down the converters).  Returns (found,
        # owner plan, owner path, final name)
        *steps, last = dotted.split('.')
        q, qpath = p, path
        for segment in steps:
            if segment not in namespaces[qpath]:
                raise AppealConfigurationError(
                    f"{where}: {dotted!r} (in the {heading} section): "
                    f"{segment!r} is not a parameter of {label(q)!r}")
            hop = step(q, qpath, segment)
            if hop is None:
                raise AppealConfigurationError(
                    f"{where}: {dotted!r} (in the {heading} section): "
                    f"{segment!r} takes no converter with parameters")
            q, qpath = hop
        found = namespaces[qpath].get(last)
        if found is None:
            hint = _suggest_entry_paths(p, path, last) if not steps else ''
            raise AppealConfigurationError(
                f"{where}: {dotted!r} (in the {heading} section) "
                f"is not a parameter of {label(q)!r}{hint}")
        return found, q, qpath, last

    def _suggest_entry_paths(p, path, name):
        found = []
        def search(q, qpath, prefix):
            for s in q.slots:
                if s.name == name:
                    found.append(prefix + s.name)
                if not isinstance(s.child, Terminal):
                    search(s.child, qpath + (id(s),), prefix + s.name + '.')
            for o in q.options:
                if o.name == name:
                    found.append(prefix + o.name)
                if o.child is not None:
                    search(o.child, qpath + (id(o),), prefix + o.name + '.')
        for s in p.slots:
            if not isinstance(s.child, Terminal):
                search(s.child, path + (id(s),), s.name + '.')
        for o in p.options:
            if o.child is not None:
                search(o.child, path + (id(o),), o.name + '.')
        if not found:
            return ''
        if len(found) == 1:
            return f" (did you mean {found[0]!r}?)"
        return f" (did you mean one of {', '.join(map(repr, found))}?)"

    def label(p):
        return getattr(p.callable, '__name__', repr(p.callable))

    def own_doc(p, where):
        return resolve_references(
            parse_docstring(_inspect.getdoc(p.callable), where),
            plan_references(p), where)

    def own_doc_of(converter):
        # a converter collapsed to a value option: no plan of its own
        # for a reference to name
        where = getattr(converter, '__name__', repr(converter))
        return resolve_references(
            parse_docstring(_inspect.getdoc(converter), where),
            no_references, where)

    def replacement(p, name, where):
        # @app.option(doc=)/@app.argument(doc=) on p's callable: the
        # docstring the parent supplies for the child behind `name`;
        # its references are p's, whose text it is
        text = p.doc_overrides.get(name)
        if text is None:
            return None
        # written like a docstring, cleaned like one (indentation)
        where = f"{where}: doc= for {name!r}"
        return resolve_references(
            parse_docstring(_inspect.cleandoc(text), where),
            plan_references(p), where)

    def apply(p, path, doc, where, argument_edge):
        # top-down: this occurrence's chosen doc lands its entries
        # (nearest scope wins: a row already documented from above
        # keeps that), then chooses each child's doc and recurses
        for kind, heading in (('arguments', 'Arguments:'),
                              ('options', 'Options:')):
            for name, entry in doc[kind].items():
                found, owner, owner_path, last = resolve(p, path, name,
                                                         where, heading)
                aimed = '.' in name
                if found[0] == 'internal':
                    raise AppealConfigurationError(
                        f"{where}: {name!r} is not one of the visible "
                        f"command-line arguments of {label(owner)!r}")
                if found[0] != kind[:-1]:
                    raise AppealConfigurationError(
                        f"{where}: {name!r} (in the {heading} section) "
                        f"is an {found[0]}, not an {kind[:-1]}")
                if aimed and not found[2]:
                    raise AppealConfigurationError(
                        f"{where}: {name!r} (in the {heading} section) "
                        f"documents nothing on the page: an outer "
                        f"parameter stands for that one word, and names it")
                if kind == 'arguments' and entry['requested']:
                    raise AppealConfigurationError(
                        f"{where}: {name!r} (in the Arguments: section) "
                        f"has sections of its own; an argument's entry "
                        f"documents the operand--to replace its "
                        f"converter's docstring, use "
                        f"@app.argument({name!r}, doc=...)")
                if aimed and last in owner.doc_overlaid:
                    raise AppealConfigurationError(
                        f"{where}: {name!r} is documented twice--in the "
                        f"{heading} section and by doc=")
                if kind == 'options' and entry['requested']:
                    target = next(o for o in owner.options if o.name == last)
                    if target.child is None:
                        raise AppealConfigurationError(
                            f"{where}: {name!r} (in the Options: section) "
                            f"has sections of its own, but {last!r} takes "
                            f"no converter to document")
                    if aimed:
                        # a dotted entry with sections is the whole
                        # docstring for that option's converter
                        aimed_docs[owner_path + (id(target),)] = (
                            entry, f"{where}: entry {name!r}")
                docs.setdefault(found[1], _prose(entry))
        if doc['commands']:
            if p is not plan or not command_names:
                raise AppealConfigurationError(
                    f"{where}: a Commands: section, but {label(p)!r} "
                    f"doesn't dispatch to commands")
            for word, entry in doc['commands'].items():
                if word not in command_names:
                    raise AppealConfigurationError(
                        f"{where}: {word!r} (in the Commands: section) "
                        f"is not one of the command words")
                docs[word] = _prose(entry)
        # a converter reached through an argument, consuming exactly
        # one operand, with no Arguments section: its prose documents
        # that operand (Larry, 2026-09-12)
        if (argument_edge and 'arguments' not in doc['requested']
                and path in sole):
            docs.setdefault(sole[path], _prose(doc))
        for o in p.options:
            if o.child is None:
                # a value option through ONE converter of the user's (a
                # one-positional function collapses to a value option):
                # the converter's prose documents the row, the same
                # single-positional rule an argument gets
                if (len(o.converters) == 1 and not _is_leaf(o.converters[0])
                        and _inspect.isfunction(o.converters[0])):
                    given = own_doc_of(o.converters[0])
                    if 'arguments' not in given['requested']:
                        docs.setdefault(path + (id(o),), _prose(given))
                continue
            rowkey = path + (id(o),)
            entry = doc['options'].get(o.name)
            given = replacement(p, o.name, where)
            if entry is not None and given is not None:
                raise AppealConfigurationError(
                    f"{where}: {o.name!r} is documented twice--in the "
                    f"Options: section and by @app.option(doc=)")
            if rowkey in aimed_docs:
                # a dotted entry from above, with sections: the whole
                # docstring for this converter (nearer wins)
                chosen, child_where = aimed_docs.pop(rowkey)
            elif entry is not None:
                chosen, child_where = entry, f"{where}: entry {o.name!r}"
            elif given is not None:
                chosen, child_where = given, f"{where}: doc= for {o.name!r}"
            else:
                chosen, child_where = own_doc(o.child, label(o.child)), label(o.child)
            requested[rowkey] = chosen['requested']
            docs.setdefault(rowkey, _prose(chosen))
            apply(o.child, rowkey, chosen, child_where, False)
        for s in p.slots:
            if isinstance(s.child, Terminal):
                given = replacement(p, s.name, where)
                if given is not None:
                    if given['requested']:
                        raise AppealConfigurationError(
                            f"{where}: doc= for {s.name!r} has sections, "
                            f"but {s.name!r} takes no converter to document")
                    found = namespaces[path][s.name]
                    docs.setdefault(found[1], _prose(given))
                continue
            here = path + (id(s),)
            given = replacement(p, s.name, where)
            if given is not None:
                chosen, child_where = given, f"{where}: doc= for {s.name!r}"
            else:
                chosen, child_where = own_doc(s.child, label(s.child)), label(s.child)
            apply(s.child, here, chosen, child_where, True)
        return doc

    def assemble(level):
        # the output rows of one Level: position qualifiers where a
        # display is duplicated (the flanking arguments' names say
        # which window each row is), the deprecation note, and--for a
        # group option--the blocks its chosen doc asked for
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
            nested = ()
            if sub is not None:
                sub_arguments, sub_options = assemble(sub)
                wants = requested[rowkey]
                nested = tuple(
                    (kind, rows) for kind, rows in (('arguments', sub_arguments),
                                                    ('options', sub_options))
                    if kind in wants and rows)
            options.append((display, docs.get(rowkey, []), nested))
        return arguments, options

    top = Level()
    walk(plan, top)
    parsed = apply(plan, (), own_doc(plan, label(plan)), label(plan), False)
    arguments, options = assemble(top)

    return {
        'summary': parsed['summary'],
        'documentation': parsed['documentation'],
        'presentation': parsed.get('presentation'),
        'arguments': arguments,
        'options': options,
        'commands': [(word, docs.get(word, []), ())
                     for word in command_names],
        'topics': [],
    }


def topic_corpus(name, doc):
    """
    A help topic's corpus (Larry, 2026-09-19): app.topic(name, doc)'s
    text is plain Markdown, dedented like a docstring; its first
    paragraph is the summary (the listing row), the rest is the page.
    No special sections: a topic has no arguments, options, or
    commands, so a heading reading 'Options' is just a heading.
    """
    from big.markdown import parse, Paragraph
    blocks = list(parse(_inspect.cleandoc(doc)).blocks)
    # a topic has no parameters: an argument or option reference in
    # its text is refused, any other span is tagging
    resolve_references({'summary': [], 'documentation': blocks,
                        'arguments': {}, 'options': {}, 'commands': {}},
                       no_references, f'help topic {name!r}')
    summary = []
    if blocks and isinstance(blocks[0], Paragraph):
        summary = [blocks.pop(0)]
    return {'summary': summary, 'documentation': blocks,
            'presentation': None, 'arguments': [], 'options': [],
            'commands': [], 'topics': []}


def page_corpus(prose_plan, head_plans, command_names=None):
    """
    A page's corpus: the prose--summary, documentation, Commands:
    entries--from prose_plan, the command's own, and the Arguments
    and Options tables from EVERY plan in head_plans, in order: the
    list the usage line walks, so a row exists for every option the
    line shows (Larry, 2026-09-19; before, one plan's tables stood
    for the whole head, and a second precommand's options--and
    Appeal's own -h and --version--went undocumented).
    """
    corpus = merge_docs(prose_plan, command_names=command_names)
    corpus['arguments'], corpus['options'] = head_tables(head_plans)
    return corpus


def head_tables(plans):
    "The (arguments, options) rows of every plan in plans, in order."
    arguments, options = [], []
    for plan in plans:
        docs = merge_docs(plan)
        arguments.extend(docs['arguments'])
        options.extend(docs['options'])
    return arguments, options


def command_set_corpus(global_plan, head_plans, entries, doc=None,
                       listing=True, topics=None):
    """
    The corpus for a multi-command program's listing.  entries is
    a sequence of (word, summary blocks) pairs in declaration order;
    topics, the program's help topics ({name: doc}, the root's
    overview only), become the Topics: rows, each its summary.  The
    command rows' documentation comes from the global command's
    Commands: entries, falling back to each command's own summary.
    The listing shows the head's Arguments and Options tables too
    (Larry, 2026-09-10: every page shows its tables), walking every
    head plan like the usage line does (Larry, 2026-09-19).
    """
    words = [word for word, _ in entries]
    if doc is not None:
        # the resolved program documentation (doc=, the class's or the
        # constructing module's docstring--Larry, 2026-09-18) supplies
        # the program half: summary, prose, Commands: overrides.  A
        # precommand's docstring documents ITS parameters only.
        parsed = resolve_references(
            parse_docstring(doc, '<program documentation>'),
            plans_references(head_plans), '<program documentation>')
        known = set(words)
        for name in parsed['commands']:
            if name not in known:
                raise AppealConfigurationError(
                    f"program documentation: 'Commands:' entry "
                    f"{name!r} isn't a command word")
        arguments, options = head_tables(head_plans)
        corpus = {'summary': parsed['summary'],
                  'documentation': parsed['documentation'],
                  'presentation': parsed.get('presentation'),
                  'arguments': arguments,
                  'options': options,
                  'commands': [(w, _prose(parsed['commands'][w])
                                   if w in parsed['commands'] else [], 0)
                               for w in words]}
    else:
        # a subcommand set: the parent command's own docstring is the
        # page's prose (the root always passes a doc, '' when the
        # program has none--a precommand's docstring never stands in)
        corpus = page_corpus(global_plan, head_plans, command_names=words)
    fallback = dict(entries)
    corpus['commands'] = [(word, lines or fallback[word], ())
                          for word, lines, nested in corpus['commands']]
    corpus['topics'] = [(name, topic_corpus(name, doc)['summary'], ())
                        for name, doc in (topics or {}).items()]
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
    return ' '.join(markdown(scan_docstring(doc)['summary']).split())


def _operand_markup(plan, decoration=None, rename=None):
    """
    A plan's operands as a usage fragment, role-tagged: `<HUE>`,
    `[<LEVEL>]`, `[<FILE>]...`; a nested converter's operands are
    spelled out (or its outer name, when it takes exactly one).
    `rename`, when given, is the display for the plan's one operand--
    the option's own name, when a group option's converter takes
    exactly one (Larry, 2026-09-18).
    """
    bits = []
    for s in plan.slots:
        if isinstance(s.child, Terminal) or s.child.sole_terminal_slot():
            text = (rename if rename is not None
                    else style('oparg', decorate_argument(s.usage_name, decoration)))
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
        # its nested options: those are the row's own Options block.  A
        # converter taking exactly one operand: the option names it
        rename = None
        if o.child.sole_terminal_slot() is not None:
            name = o.usage_name if o.usage_name is not None else o.name
            rename = style('oparg', decorate_argument(name, decoration))
        text = _operand_markup(o.child, decoration, rename)
        if text:
            bits.append(text)
    elif o.consumes_operands:
        names = ([o.usage_name] if o.usage_name is not None
                 else o.oparg_names())
        for name in names:
            bits.append(style('oparg', decorate_argument(name, decoration)))
    return ' '.join(bits)




def man_page(prog, corpus, usage, command_pages=None, version=None,
             topics=None):
    """
    The help corpus in troff clothing: a man(1) page assembled
    from the same predigested rows --help renders.  command_pages,
    for a multi-command program, is [(words, usage, corpus), ...],
    every command at every depth in tree order, `words` its word path
    ('db start')--each becomes a subsection under COMMANDS, and each
    usage a SYNOPSIS line.  topics is [(name, corpus), ...], the
    program's help topics: a TOPICS section listing them, then a
    subsection per topic, `prog help name`.  Returns the troff
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

    def paragraphs(blocks):
        # the row's blocks through big's troff writer (a leading .PP
        # would double .TP's own paragraph: drop it)
        text = markdown(blocks, 'troff')
        if text.startswith('.PP\n'):
            text = text[4:]
        line(text)

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
    summary_line = ' '.join(markdown(corpus['summary']).split())
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
                                 ('Options:', sub_corpus['options']),
                                 ('Commands:', sub_corpus['commands'])):
                if not pairs:
                    continue
                line('.PP')
                line(f'.B {label}')
                entries(pairs)
    if topics:
        line('.SH TOPICS')
        for name, page in topics:
            line('.TP')
            line(f'.B {esc(name)}')
            if page['summary']:
                paragraphs(page['summary'])
        for name, page in topics:
            line(f'.SS "{esc(prog)} help {esc(name)}"')
            if page['documentation']:
                paragraphs(page['documentation'])
    return '\n'.join(out) + '\n'
