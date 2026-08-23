# appeal/render.py
# Part of Appeal 1.0.
#
# Help, usage, color, and markdown rendering--carved out of
# the core so `import appeal` (the parse/convert/dispatch
# core a precompiled parser imports) pulls in NOTHING from big.
# This module DOES import big (markdown/stylesheet/text); it's
# imported LAZILY--only when help or an error actually renders.

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
    'oparg':      ('T', '⦃argument⦙T⦄'),    # ruled: defaults to argument
    'summary':    ('T', '⦃bold⦙T⦄'),
    'error':      ('T', '⦃bold⦙T⦄'),
}


plain_theme = {name: ('T', 'T') for name in uncolored_theme}


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


def usage_markup(usage):
    """
    Dress a usage line in role spans: the first bare word is the
    program, '-'-led words are options, <words> are arguments at
    the top level and opargs inside brackets, other bare words
    at the top level are arguments.  Structural characters
    (brackets, pipes, ellipses) stay bare.  Purely lexical and
    purely additive--the visible text is unchanged, so
    usage_units and the wrap see the same units, and a plain
    sheet strips the spans back to the input.
    """
    out = []
    append = out.append
    depth = 0
    saw_program = False
    i = 0
    n = len(usage)
    while i < n:
        c = usage[i]
        if c == '<':
            j = usage.find('>', i)
            if j == -1:
                append(escape_styles(usage[i:]))
                break
            role = 'oparg' if depth else 'argument'
            append(style(role, escape_styles(usage[i:j + 1])))
            i = j + 1
            continue
        if c == '-' and ((i == 0) or (usage[i - 1] in '[|= ')):
            j = i
            while j < n and (usage[j].isalnum() or usage[j] in '-_'):
                j += 1
            append(style('option', escape_styles(usage[i:j])))
            i = j
            continue
        if c.isalnum() or c in '_.':
            j = i
            while j < n and (usage[j].isalnum() or usage[j] in '_.'):
                j += 1
            word = usage[i:j]
            if not any(ch.isalnum() for ch in word):
                append(escape_styles(word))      # '...' and friends
            elif not saw_program:
                append(style('program', escape_styles(word)))
                saw_program = True
            elif not depth:
                append(style('argument', escape_styles(word)))
            else:
                append(escape_styles(word))
            i = j
            continue
        if c == '[':
            depth += 1
        elif c == ']':
            depth = max(0, depth - 1)
        append(escape_styles(c))
        i += 1
    return ''.join(out)


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


def help_margin(max_columns=79):
    """
    The wrap margin for a rendered help page: the terminal's
    width, capped at max_columns (v1's rule--a narrow terminal
    re-wraps, a wide one doesn't stretch lines past the cap).
    Pipes and other non-terminals get the cap itself, so captured
    output is stable.
    """
    import shutil
    return min(shutil.get_terminal_size((max_columns, 24)).columns,
               max_columns)


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
    prefix, usage-string) entries are dressed in role spans
    (usage_markup) and wrapped at whole units; ('markdown',
    layout) entries carry big's width-independent layout
    tuples--wrap_words lays them out at the real margin
    (strip_styles measuring the words), join_styles fuses
    adjacent spans.  Either way the stylesheet paints last:
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
    # ...and measures it: glyphs_from_stylesheet only knows big's
    # markdown glyph roles, so a bare ⦃line⦄ word in a layout
    # would measure zero-wide and wrap_words would drop it.  Same
    # symmetry as the injection--only the renderer knows how wide
    # a line is.
    line_span = _style_span('line')
    line_glyph = sheet.render(line_span)
    measure = lambda w: strip_styles(
        glyphs(w).replace(line_span, line_glyph))
    out = []
    for piece in pieces:
        if piece[0] == 'usage':
            prefix, usage = piece[1], piece[2]
            # roles are lexical and additive, so the units and
            # their widths are those of the bare usage line
            body = wrap_words(usage_units(usage_markup(usage)),
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


def rows_markdown(rows):
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
    """
    def entry_block(display, lines, children):
        body = [l for l in lines] or ['']
        first = f": {body[0]}" if body[0] else ": "
        rest = [("  " + l) if l.strip() else '' for l in body[1:]]
        block = [display, first] + rest
        for child in children:
            block.append('')
            block.extend(("  " + l) if l.strip() else ''
                         for l in entry_block(*child))
        return block

    # rebuild the tree the merge flattened: depth = the display's
    # leading two-space pairs
    roots = []
    stack = []                  # (depth, entry) path to the tip
    for display, lines in rows:
        stripped = display.lstrip(' ')
        depth = (len(display) - len(stripped)) // 2
        entry = (stripped, lines, [])
        while stack and stack[-1][0] >= depth:
            stack.pop()
        (stack[-1][1][2] if stack else roots).append(entry)
        stack.append((depth, entry))
    return '\n\n'.join('\n'.join(entry_block(*e)) for e in roots)


def listing_pieces(usage, corpus, templates):
    """
    The terse command listing as BAKED PIECES: the usage line and
    the Commands table, no prose.  Attached to dispatch-level
    UsageErrors and printed for a bare command line;
    render_baked_help finishes it at print time--wrapped at the
    real margin, styled for the real stream (the 2026-08-06
    ruling: errors render through the pipeline too).
    """
    return help_page_pieces(usage, corpus, templates,
                            suppress=('summary', 'doc',
                                      'arguments', 'options'))


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
    machine)--the same two halves a generated script uses, so
    in-process help and standalone help cannot drift.
    """
    return render_baked_help(
        help_page_pieces(usage, corpus, templates, suppress),
        margin, file=file, stylesheet=stylesheet)


def term_markup(word, role):
    """
    Dress one laid-out table-term word in its role span: 'option'
    terms lexically (option strings and <oparg>s), 'argument' and
    'command' terms whole.  The word usually wears the Markdown
    'term' wrapper; the role span nests INSIDE it, so a themed
    table term is bold AND role-colored.  The word came out of
    the styled pipeline, so its text is already escaped.
    """
    prefix = suffix = ''
    inner = word
    open_ = style_delimiters[0] + 'term' + style_delimiters[1]
    close = style_delimiters[2]
    if word.startswith(open_) and word.endswith(close):
        inner = word[len(open_):-len(close)]
        prefix, suffix = open_, close
    if not inner:
        return word
    if role != 'option':
        return prefix + style(role, inner) + suffix
    out = []
    append = out.append
    i = 0
    n = len(inner)
    while i < n:
        c = inner[i]
        if c == '<':
            j = inner.find('>', i)
            if j == -1:
                append(inner[i:])
                break
            append(style('oparg', inner[i:j + 1]))
            i = j + 1
            continue
        if c == '-' and ((i == 0) or (inner[i - 1] in '[|= ')):
            j = i
            while j < n and (inner[j].isalnum() or inner[j] in '-_'):
                j += 1
            append(style('option', inner[i:j]))
            i = j
            continue
        append(c)
        i += 1
    return prefix + ''.join(out) + suffix


_SECTION_TERM_ROLES = {'options': 'option', 'arguments': 'argument',
                       'commands': 'command'}


def role_layout(layout, section):
    """
    Dress one laid-out help section in appeal's role spans--the
    bake half of themed help.  Table sections mark their
    definition-list terms (term_markup); the summary marks every
    word (join_styles fuses them back into one span at render).
    Purely additive: a plain sheet strips the spans, so unthemed
    output is unchanged.
    """
    role = _SECTION_TERM_ROLES.get(section)
    out = []
    for item in layout:
        if role and (type(item) is tuple) and item and (item[0] == 'term'):
            out.append(('term',) + tuple(term_markup(w, role)
                                         for w in item[1:]))
        elif ((section == 'summary') and isinstance(item, str)
              and item.strip()):
            out.append(style('summary', item))
        else:
            out.append(item)
    return tuple(out)


def help_page_pieces(usage, corpus, templates, suppress=()):
    """
    The bake half of a help page: assemble the template-ordered
    Markdown, parse/style/lay it out via big, dress the flense's
    sections in role spans (role_layout), and return the
    template-ordered piece tuple render_baked_help consumes at
    runtime--('usage', prefix, usage-string) for the usage line,
    ('markdown', layout) for everything else, where layout is
    big's width-independent flat tuple.  Every value reprs into
    valid Python source: a generated script embeds the pieces as
    a literal.
    """
    from big.markdown import (layout_document, parse,
                              split_styles_document, style_document)
    pieces = []
    md = []            # pending markdown, flushed per role change

    def flush(section=None):
        text = ''.join(md)
        md.clear()
        if text.strip():
            document = split_styles_document(
                style_document(parse(text)))
            layout = layout_document(document)
            if section:
                layout = role_layout(layout, section)
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
        if name == 'summary':
            content = '\n'.join(corpus['summary'])
        elif name == 'doc':
            content = '\n'.join(corpus['documentation'])
        else:
            content = rows_markdown(corpus[name])
        if not content.strip():
            continue
        if name == 'doc':
            md.append(header + content)
            continue
        # a roled section bakes alone, so role_layout knows whose
        # terms (or words) it is dressing
        flush()
        md.append(header + content)
        flush(name)
    flush()
    return tuple(pieces)


# plain: every span strips to its text (these two spans carry a
# structural role even uncolored)
plain_theme['codeblock'] = ('T', '⦃code⦙T⦄')
plain_theme['oparg'] = ('T', '⦃argument⦙T⦄')
