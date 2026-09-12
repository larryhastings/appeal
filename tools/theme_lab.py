#!/usr/bin/env python3

##
## theme_lab.py -- Appeal's theme drafting bench.
##
## Renders one sample help page under every proposed theme, so
## you can look at them in your actual terminal.  The themes are
## THE REAL ONES, imported from appeal/presentation.py--edit them
## there and rerun; the demo program's docstrings (below) are the
## showcase, also freely editable.
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
## The themes are layered (2026-09-12): plain_theme is the root
## (no-ops; headings ruled), uncolored_theme adds the attributes
## (bold/italic/underline live there only; headings underlined),
## and a colored theme is _theme(role='color', ...)--the uncolored
## entry wrapped in that color.  So to make placeholders italic
## everywhere, edit 'argument' in uncolored_theme; to make them
## purple in one theme, edit that theme's argument='...'.  A theme
## may also give a whole tuple for a role, used as given.
## ruled_headings(theme) puts the drawn rules back on any theme.
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
## the themes are APPEAL'S--the signed-off dicts shipping in
## appeal/presentation.py (ruled 2026-09-07: no lab copies, no
## drift).  To tweak a theme, edit it THERE and rerun the lab.
##

from appeal.presentation import (
    appeal_theme,
    dark_cool_theme,
    dark_warm_theme,
    light_cool_theme,
    light_warm_theme,
    plain_theme,
    uncolored_theme,
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
> Warnings are dark yellow.

> [!CAUTION]
> Caution is red.

A definition list, through the real compact layout--the
column sits at the 80th-percentile term, so the short terms
share it:

creamy
: Smooth and rich, like a well-made custard.

crunchy
: Crisp to the bite, with an audible snap.

soft
: Yielding under gentle pressure; think fresh bread.
"""


def build_demo(stylesheet, errors, margin):
    """
    The demo program.  A bad option shows the error line and its
    usage trailer--the overview page, carrying DEMO_DOC (the
    Markdown zoo), the set usage line and the Commands table;
    `help serve` shows a command page (usage with options and
    operands, the Arguments/Options tables).
    """
    import appeal
    import pathlib
    app = appeal.Appeal(name='serve', version='1.0', margin=margin,
                        stylesheet=stylesheet, errors=errors)

    def top():
        pass
    top.__doc__ = DEMO_DOC
    app.global_command()(top)

    @app.command()
    @app.option('config', '--config')            # long only, as before
    @app.argument('config', usage='file')
    @app.option('enable_experimental_quantum_transport',
                '--enable-experimental-quantum-transport')
    def serve(host='0.0.0.0', *, quiet=False, verbose=False,
              config: pathlib.Path = None, port: int = 8080,
              enable_experimental_quantum_transport=False):
        """
        Start the server.

        # Arguments

        host
        : The interface to bind.

        # Options

        quiet
        : Hush.

        verbose
        : Narrate the process.

        config
        : Read configuration from the file.

        port
        : The TCP port to bind.  Defaults to 8080, or the value
          of the `SERVE_PORT` environment variable if set.

        enable_experimental_quantum_transport
        : You have been warned.  (The monster term falls back
          man-style, under the column the short terms share.)
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
        # the error line, with the overview page as its usage
        # trailer; then a command page.  A seam between the two.
        try:
            app.main(['--zerve'])
        except SystemExit:
            pass
        print()
        print('_' * 70)
        print()
        app.process(['help', 'serve'])
    return buf.getvalue()


def print_ansi_16():
    "The ANSI 16, straight SGR--what YOUR terminal makes of them."
    print('The ANSI 16.  Terminals remap these to their own scheme;')
    print('foreground code, bright foreground, then the backgrounds.')
    print('Using big.stylesheet color names.')
    print()
    normal = ('black', 'red', 'green', 'yellow',
              'blue', 'purple', 'cyan', 'light_gray')
    bright = ('gray', 'light_red', 'light_green', 'light_yellow',
              'light_blue', 'light_purple', 'light_cyan', 'white')
    for i, (lo, hi) in enumerate(zip(normal, bright)):
        n, b = 30 + i, 90 + i
        print(f'  \x1b[{n}m{n}  {lo:<13}sample\x1b[39m'
              f'   \x1b[{b}m{b}  {hi:<13}sample\x1b[39m'
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
