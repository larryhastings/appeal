#!/usr/bin/env python3

##
## theme_lab.py -- Appeal's theme drafting bench.
##
## Renders one sample help page under every proposed theme, so
## you can look at them in your actual terminal and hack the
## colors.  EVERYTHING IS MEANT TO BE EDITED: the themes are
## plain dict literals right below, the sample page is one
## marked-up string, and rerunning the script shows your edits.
## Glyphs (the bullet, quote bars, heading rules, alert emoji)
## are zero-arg spans--⦃bullet⦄, ⦃note_emoji⦄--resolved by the
## stylesheet, so a theme can re-glyph as well as recolor
## (markdown_defaults supplies Unicode; big also ships
## markdown_ascii_glyphs).
##
##     python3 tools/theme_lab.py            # all themes
##     python3 tools/theme_lab.py dark_cool  # just one
##
## The vocabulary (ruled 2026-08-06):
##   roles     program, command, option, argument, oparg
##             (defaults to argument), summary, error
##   concepts  heading1-6, code, codeblock, link, marker,
##             blockquote, rule, term, note/tip/important/
##             warning/caution (+ heading_<kind>)
##
## Entry syntax is big's StyleSheet: 'name': ('T', replacement),
## where the replacement is what the span's text becomes--'T' is
## the text itself, and ⦃style⦙...⦄ wraps it in another style.
## So ('T', '⦃bold⦙⦃cyan⦙T⦄⦄') means "bold cyan".  Colors are
## palette-independent NAMES (red, light_red, dark_red, orange,
## yellow, green, cyan, blue, purple, gray + light_/dark_ each,
## white, black); the palette maps them to escapes at the end.
##

import os
import sys

# the lab belongs to THIS repo's appeal, not any installed one
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from big.markdown import markdown_defaults
from big.stylesheet import (
    StyleSheet,
    ansi_16_color_palette,
    ansi_truecolor_palette,
    plain_palette,
    uncolored_palette,
)


##
## appeal_markdown_defaults: Appeal's opinions about the Markdown
## concepts and the role defaults--ATTRIBUTES ONLY, no color.
## Every theme starts from a copy of this and adds color.
## (uncolored_theme IS this dict, verbatim.)
##

appeal_markdown_defaults = {
    # headings (Larry's design, 2026-08-06): every level wears
    # heading_color--ONE slot, so a theme recolors all six with
    # one entry.  h1 is bold ALL-CAPS between lines of =, h2
    # bold over a line of - (the =/- rows come from big's
    # layout; the style paints them), h3 bold, h4-5 italic, h6
    # color only.
    'heading_color': ('T', 'T'),
    # STRUCTURE IN THE SHEET, live (ruled 2026-08-08; big's
    # layout hard-codes nothing now).  h1 sits between full
    # rules, h2 over one--⦃fill⦙pattern⦙model⦄ sizes the rule to
    # the stripped heading, ⦃clip⦙⦃line⦄⦙...⦄ bounds it at the
    # margin (`line` is renderer-injected: '-' to the margin).
    # Delete a rule line, lose the rule; give heading4 one, it
    # rules.  The transforms on call: fill clip center strip
    # lstrip rstrip upper lower title.
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
    'rule':       ('T', 'T'),
    'term':       ('T', '⦃bold⦙T⦄'),        # user deflists in prose
    # GitHub alerts: big's colors, kept
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


def theme(**overrides):
    "A theme: appeal_markdown_defaults plus your colors."
    t = dict(appeal_markdown_defaults)
    t.update(overrides)
    return t


##
## the seven themes.  Edit freely--this is the whole point.
##

# plain: every span strips.  (Built mechanically: same keys, all
# do-nothing.)
plain_theme = {name: ('T', 'T') for name in appeal_markdown_defaults}
plain_theme['codeblock'] = ('T', '⦃code⦙T⦄')
plain_theme['oparg'] = ('T', '⦃argument⦙T⦄')

# uncolored: attributes, no color--the defaults, verbatim.
uncolored_theme = dict(appeal_markdown_defaults)

# appeal_theme: Larry's, designed against the ANSI 16 (terminal
# light/dark modes remap those for legibility, so this looks
# right on both).  THIS IS A STRAWMAN--hack away.
appeal_theme = theme(
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

# the four corners: warm = red/orange/yellow, cool =
# blue/green/cyan, purple in both.  light_* themes use dark_
# colors (dark ink on a light page); dark_* themes use light_.
# NOTE one deliberate deviation: code is ORANGE in the warm
# corners (green read as a wrong note there)--Larry to confirm.

light_warm_theme = theme(
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

dark_warm_theme = theme(
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

light_cool_theme = theme(
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

dark_cool_theme = theme(
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


##
## which palette each theme pairs with, for the demo.  (In real
## Appeal you compose these yourself into a stylesheet.)
##

PAIRINGS = {
    'plain':      (plain_theme,      plain_palette),
    'uncolored':  (uncolored_theme,  uncolored_palette),
    'appeal':     (appeal_theme,     ansi_16_color_palette),
    'light_warm': (light_warm_theme, ansi_truecolor_palette),
    'dark_warm':  (dark_warm_theme,  ansi_truecolor_palette),
    'light_cool': (light_cool_theme, ansi_truecolor_palette),
    'dark_cool':  (dark_cool_theme,  ansi_truecolor_palette),
}


##
## the sample page, two honest halves--ONE technology.  Both
## halves are "baked pieces", the same things Appeal bakes into
## a compiled script, and both finish through Appeal's REAL
## finisher, render_baked_help (wrap at the margin, fuse spans,
## paint with the sheet).  They differ only in provenance:
##
## SAMPLE_MARKDOWN is baked by big's real ingestion (parse ->
## style -> layout), like a docstring--edit the Markdown freely.
##
## ROLE_PIECES simulates the OUTPUT of Appeal's flense (the
## usage lines, the Arguments/Options/Commands tables, the error
## line).  The real pipeline emits these role spans now; the lab
## keeps a hand-baked copy so one page showcases EVERY role and
## stays freely editable.  (Role markup can never ride a
## docstring anyway: the pipeline ESCAPES delimiters found in
## source text, correctly.)
## The vocabulary is the layout grammar the flense will speak:
## role-styled words, ('indent', first, rest), and the
## definition-list markers--so the tables go through the real
## compact layout, not hand-spaced columns.
##

SAMPLE_MARKDOWN = """\
Start the server, and serve until interrupted.

Longer prose with **bold**, *italic*, and `inline_code()`,
plus a [hyperlink](https://example.com) and ~~the old way~~.

# Heading One which by the way is super duper long almost excessively so and it's kind of pointless like this

## Heading Two

### Heading Three

#### Heading Four

##### Heading Five

###### Heading Six

- the first bullet
- the second bullet

A code block, indented four:

    $ serve --port 8080 example.com
    serving example.com:8080

> [!NOTE]
> Notes are blue.

> [!TIP]
> Tips are green.

> [!IMPORTANT]
> Important is purple.

> [!WARNING]
> Warnings are orange.

> [!CAUTION]
> Caution is red.

A definition list, through the real compact layout--the
column sits at the 80th-percentile term, so the short terms
share it and the monster falls back man-style:

`-q`|`--quiet`
: Hush.

`-v`|`--verbose`
: Narrate the process.

`--config` *FILE*
: Read configuration from *FILE*.

`-p`|`--port` *PORT*
: The TCP port to bind.  Defaults to 8080, or the value of
  the `SERVE_PORT` environment variable if set.

`--enable-experimental-quantum-transport`
: You have been warned.
"""

def _mark(role, text):
    "Per-word role spans; join_styles refuses them back together."
    return tuple(f'⦃{role}⦙{word}⦄' for word in text.split())


ROLE_PIECES = (
    # the flense's line between the doc and the machine-made
    # sections--also proves the renderer's `line` injection.
    ('markdown', ('⦃heading_color⦙⦃line⦄⦄',)),
    # the usage line: each bracket group is ONE word, so wrapping
    # never splits a unit (usage_units' rule).
    ('markdown', (
        ('indent', 'usage: ', '       '),
        '⦃program⦙serve⦄',
        '[⦃option⦙-v⦄|⦃option⦙--verbose⦄]',
        '[⦃option⦙-p⦄|⦃option⦙--port⦄ ⦃oparg⦙<PORT>⦄]',
        '⦃argument⦙<HOST>⦄',
    )),
    # the SET flavor's usage line: the <COMMAND> placeholder keeps
    # the argument DECORATION (a hole to fill) but wears the command
    # ROLE, cross-referencing the listing's words below (ruled
    # 2026-09-07)
    ('markdown', (
        ('indent', 'usage: ', '       '),
        '⦃program⦙serve⦄',
        '⦃command⦙<COMMAND>⦄',
    )),
    ('markdown', _mark('summary',
        'Start the server, and serve until interrupted.')),
    ('markdown', (
        '⦃heading2⦙Arguments⦄', '\n\n',
        ('def start',),
        ('term', '⦃argument⦙<HOST>⦄'),
        'The', 'interface', 'to', 'bind.',
        ('def end',),
    )),
    ('markdown', (
        '⦃heading2⦙Options⦄', '\n\n',
        ('def start',),
        ('term', '⦃option⦙-v⦄|⦃option⦙--verbose⦄'),
        'Narrate', 'the', 'process.',
        ('term', '⦃option⦙-p⦄|⦃option⦙--port⦄', '⦃oparg⦙<PORT>⦄'),
        'The', 'TCP', 'port.',
        ('def end',),
    )),
    ('markdown', (
        '⦃heading2⦙Commands⦄', '\n\n',
        ('def start',),
        ('term', '⦃command⦙serve⦄'),
        'Start', 'the', 'server.',
        ('term', '⦃command⦙stop⦄'),
        'Stop', 'the', 'server.',
        ('def end',),
    )),
    ('markdown', ('⦃error⦙error:⦄', 'unknown', 'command', "'zerve'")),
)

WIDTH = 72


def main(argv):
    from big.markdown import (layout_document, parse,
                              split_styles_document, style_document)
    from big.stylesheet import transforms
    from appeal.presentation import render_baked_help

    document = split_styles_document(style_document(
        parse(SAMPLE_MARKDOWN)))
    pieces = (('markdown', layout_document(document)),) + ROLE_PIECES

    picks = argv or list(PAIRINGS)
    for name in picks:
        if name not in PAIRINGS:
            sys.exit(f"unknown theme {name!r}; "
                     f"the menu is {', '.join(PAIRINGS)}")
        theme_dict, palette = PAIRINGS[name]
        # the composition, per the 2026-08-06 rulings:
        # markdown_defaults beneath (safety net), the transforms
        # (upper etc), the palette, then the theme outermost.
        # `line` is the renderer's business: render_baked_help
        # injects it at the real margin (ruled 2026-08-08).
        sheet = (markdown_defaults | transforms
                 | palette | StyleSheet(theme_dict))
        bar = '=' * 62
        print(bar)
        print(f'==  {name}_theme  (over {_palette_name(palette)})')
        print(bar)
        print()
        print(render_baked_help(pieces, margin=WIDTH,
                                stylesheet=sheet))


def _palette_name(palette):
    import big.stylesheet as s
    for name in ('plain_palette', 'uncolored_palette',
                 'ansi_16_color_palette', 'ansi_256_color_palette',
                 'ansi_truecolor_palette'):
        if getattr(s, name) is palette:
            return name
    return 'custom palette'


if __name__ == '__main__':
    main(sys.argv[1:])
