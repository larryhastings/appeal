#!/usr/bin/env python3

##
## theme_lab.py -- Appeal's theme drafting bench.
##
## Renders one sample help page under every proposed theme, so
## you can look at them in your actual terminal and hack the
## colors.  EVERYTHING IS MEANT TO BE EDITED: the themes are
## plain dict literals right below, the sample page is one
## marked-up string, and rerunning the script shows your edits.
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

import sys

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
    'heading1':   ('T', '⦃bold⦙⦃heading_color⦙⦃upper⦙T⦄⦄⦄'),
    'heading2':   ('T', '⦃bold⦙⦃heading_color⦙T⦄⦄'),
    'heading3':   ('T', '⦃bold⦙⦃heading_color⦙T⦄⦄'),
    'heading4':   ('T', '⦃italic⦙⦃heading_color⦙T⦄⦄'),
    'heading5':   ('T', '⦃italic⦙⦃heading_color⦙T⦄⦄'),
    'heading6':   ('T', '⦃heading_color⦙T⦄'),
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
    'heading_note':      ('T', '⦃heading2⦙⦃blue⦙T⦄⦄'),
    'tip':               ('T', '⦃green⦙T⦄'),
    'heading_tip':       ('T', '⦃heading2⦙⦃green⦙T⦄⦄'),
    'important':         ('T', '⦃purple⦙T⦄'),
    'heading_important': ('T', '⦃heading2⦙⦃purple⦙T⦄⦄'),
    'warning':           ('T', '⦃orange⦙T⦄'),
    'heading_warning':   ('T', '⦃heading2⦙⦃orange⦙T⦄⦄'),
    'caution':           ('T', '⦃red⦙T⦄'),
    'heading_caution':   ('T', '⦃heading2⦙⦃red⦙T⦄⦄'),
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
## the sample page: one string wearing every role and concept.
## (Hand-marked--the real flense will emit these spans itself.)
##

SAMPLE = """\
usage: ⦃program⦙serve⦄ [⦃option⦙-v⦄|⦃option⦙--verbose⦄] [⦃option⦙-p⦄|⦃option⦙--port⦄ ⦃oparg⦙<PORT>⦄] ⦃argument⦙<HOST>⦄

⦃summary⦙Start the server, and serve until interrupted.⦄

Longer prose with ⦃bold⦙bold⦄, ⦃italic⦙italic⦄, and ⦃code⦙inline_code()⦄,
plus a ⦃link⦙hyperlink⦄ and ⦃strikethrough⦙the old way⦄.

⦃heading1⦙===========⦄
⦃heading1⦙Heading One⦄
⦃heading1⦙===========⦄

⦃heading2⦙Heading Two⦄
⦃heading2⦙-----------⦄

⦃heading3⦙Heading Three⦄

⦃heading4⦙Heading Four⦄

⦃heading5⦙Heading Five⦄

⦃heading6⦙Heading Six⦄

  ⦃marker⦙•⦄ the first bullet
  ⦃marker⦙•⦄ the second bullet

  ⦃codeblock⦙$ serve --port 8080 example.com
serving example.com:8080⦄

⦃heading_note⦙▐ Note⦄
⦃note⦙▐⦄ Notes are blue.

⦃heading_tip⦙▐ Tip⦄
⦃tip⦙▐⦄ Tips are green.

⦃heading_important⦙▐ Important⦄
⦃important⦙▐⦄ Important is purple.

⦃heading_warning⦙▐ Warning⦄
⦃warning⦙▐⦄ Warnings are orange.

⦃heading_caution⦙▐ Caution⦄
⦃caution⦙▐⦄ Caution is red.

⦃heading2⦙Arguments⦄
⦃heading2⦙---------⦄

⦃argument⦙<HOST>⦄  The interface to bind.

⦃heading2⦙Options⦄
⦃heading2⦙-------⦄

⦃option⦙-v⦄|⦃option⦙--verbose⦄        Narrate the process.
⦃option⦙-p⦄|⦃option⦙--port⦄ ⦃oparg⦙<PORT>⦄  The TCP port.

⦃heading2⦙Commands⦄
⦃heading2⦙--------⦄

⦃command⦙serve⦄  Start the server.
⦃command⦙stop⦄   Stop the server.

⦃error⦙error:⦄ unknown command 'zerve'
"""


def main(argv):
    picks = argv or list(PAIRINGS)
    for name in picks:
        if name not in PAIRINGS:
            sys.exit(f"unknown theme {name!r}; "
                     f"the menu is {', '.join(PAIRINGS)}")
        theme_dict, palette = PAIRINGS[name]
        # the composition, per the 2026-08-06 rulings:
        # markdown_defaults beneath (safety net), palette, then
        # the theme outermost
        from big.stylesheet import transforms
        sheet = (markdown_defaults | transforms | palette
                 | StyleSheet(theme_dict))
        bar = '=' * 62
        print(bar)
        print(f'==  {name}_theme  (over {_palette_name(palette)})')
        print(bar)
        print()
        print(sheet.render(SAMPLE))
        print()


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
