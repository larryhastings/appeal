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
##     python3 tools/theme_lab.py            # palettes, then all themes
##     python3 tools/theme_lab.py dark_cool  # just one theme
##     python3 tools/theme_lab.py 16 256     # your terminal's raw
##                                           # ANSI 16 / 256 colors
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
    # layout; the style paints them), h3 bold, h4 italic, h5
    # color only, h6 color lowercased (ruled 2026-09-07: h5
    # dropped italic--it rendered identically to h4).
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
    'heading5':   ('T', '⦃heading_color⦙T⦄'),
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
    'heading_warning':   ('T', '⦃bold⦙⦃dark_yellow⦙T⦄⦄'),
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

# plain and uncolored are ONE structure over two palettes: the theme
# emits the document (heading rules are TEXT), and the palette decides
# what renders--uncolored_palette expresses bold/italic/underline but
# no colors, plain_palette expresses nothing.
plain_theme = dict(appeal_markdown_defaults)

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
    marker    = ('T', '⦃dark_purple⦙T⦄'),
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
## the sample: a REAL Appeal program.  The swatches below are the
## genuine article--the demo app's actual help pages and an actual
## error, rendered by Appeal's live pipeline--so any change to
## Appeal's rendering shows up here the moment it lands, and the
## lab can never drift.  The docstrings are the showcase: edit
## them freely to exercise more Markdown.
##

DEMO_DOC = """\
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

`--config` *<FILE>*
: Read configuration from *<FILE>*.

`-p`|`--port` *<PORT>*
: The TCP port to bind.  Defaults to 8080, or the value of
  the `SERVE_PORT` environment variable if set.

`--enable-experimental-quantum-transport`
: You have been warned.
"""


def build_demo(stylesheet, errors, margin):
    """
    The demo program.  Its overview page carries DEMO_DOC (the
    Markdown zoo) plus the set usage line and the Commands table;
    `help serve` shows a command page (usage with options and
    operands, the Arguments/Options tables); a bad option shows
    the error line and its usage trailer.
    """
    import appeal
    app = appeal.Appeal(name='serve', version='1.0', margin=margin,
                        stylesheet=stylesheet, errors=errors)

    def top(*, verbose=False):
        pass
    top.__doc__ = DEMO_DOC
    app.global_command()(top)

    @app.command()
    def serve(host='0.0.0.0', *, port: int = 8080, quiet=False):
        """
        Start the server.

        ## Arguments

        host
        : The interface to bind.

        ## Options

        port
        : The TCP port to bind.  Defaults to 8080, or the value
          of the `SERVE_PORT` environment variable if set.

        quiet
        : Hush.
        """

    @app.command()
    def stop():
        "Stop the server."

    return app


def render_sample(sheet, margin):
    "The demo app's real output, styled by `sheet`, as one string."
    import contextlib
    import io
    buf = io.StringIO()
    app = build_demo(sheet, buf, margin)
    with contextlib.redirect_stdout(buf):
        app.process(['help'])
        print()
        app.process(['help', 'serve'])
        print()
        try:
            app.main(['--zerve'])
        except SystemExit:
            pass
    return buf.getvalue()


def print_ansi_16():
    "The ANSI 16, straight SGR--what YOUR terminal makes of them."
    print('The ANSI 16.  Terminals remap these to their own scheme;')
    print('foreground code, bright foreground, then the backgrounds.')
    print()
    names = ('black', 'red', 'green', 'yellow',
             'blue', 'magenta', 'cyan', 'white')
    for i, name in enumerate(names):
        normal, bright = 30 + i, 90 + i
        print(f'  \x1b[{normal}m{normal}  {name:<8}sample\x1b[39m'
              f'   \x1b[{bright}m{bright}  bright_{name:<8}sample\x1b[39m'
              f'   \x1b[4{i}m  {40 + i}  \x1b[49m'
              f' \x1b[10{i}m  {100 + i}  \x1b[49m')
    print()


def _cell_ink(n):
    "black or white ink, whichever survives on 256-color cell n."
    if n < 16:
        luma = 1 if n in (7, 10, 11, 14, 15) else 0
    elif n < 232:                       # the 6x6x6 cube
        cube = n - 16
        r, g, b = cube // 36, (cube // 6) % 6, cube % 6
        luma = 1 if (3 * r + 6 * g + b) >= 24 else 0
    else:                               # the grayscale ramp
        luma = 1 if n >= 244 else 0
    return 30 if luma else 97           # black ink on light, white on dark


def print_ansi_256():
    "The ANSI 256, as painted background cells."
    print('The ANSI 256: 0-15 the sixteen, 16-231 the 6x6x6 color')
    print('cube, 232-255 the grayscale ramp.')
    print()
    for start in range(0, 256, 16):
        row = []
        for n in range(start, start + 16):
            row.append(f'\x1b[{_cell_ink(n)}m\x1b[48;5;{n}m {n:3} '
                       f'\x1b[49m\x1b[39m')
        print(''.join(row))
    print()


# the non-theme commands: raw palette swatches
SWATCHES = {
    '16': print_ansi_16,
    '256': print_ansi_256,
}


def main(argv):
    from big.markdown import markdown_defaults
    from big.stylesheet import transforms

    # the demo's output is captured into a buffer before printing,
    # and a buffer isn't a tty--so Appeal's own width detection can't
    # see the terminal.  Measure it HERE, in the process that has it,
    # and hand the width to the app.
    import shutil
    margin = shutil.get_terminal_size((79, 24)).columns

    picks = argv or list(SWATCHES) + list(PAIRINGS)
    for name in picks:
        if name in SWATCHES:
            SWATCHES[name]()
            continue
        if name not in PAIRINGS:
            menu = ', '.join(list(SWATCHES) + list(PAIRINGS))
            sys.exit(f"unknown theme {name!r}; the menu is {menu}")
        theme_dict, palette = PAIRINGS[name]
        # the composition, per the 2026-08-06 rulings:
        # markdown_defaults beneath (safety net), the transforms
        # (upper etc), the palette, then the theme outermost.
        sheet = (markdown_defaults | transforms
                 | palette | StyleSheet(theme_dict))
        bar = '=' * 62
        print(bar)
        print(f'==  {name}_theme  (over {_palette_name(palette)})')
        print(bar)
        print()
        print(render_sample(sheet, margin))


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
