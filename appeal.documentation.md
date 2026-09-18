# Documenting an Appeal program (and coloring its output)

*The user guide for Appeal 1.0's help system: how docstrings become
`--help` pages, how documentation composes across converters, how
to reshape the page with the template, and how to color it with a
stylesheet.  The docstring format is Markdown (the pivot, ruled
2026-08-05); big's Markdown parser renders it.  Every example here
is executed by the test suite.*


## 1: The docstring is input, never output

Appeal never shows your docstring to anyone.  It *reads* it: the
special sections are slurped out as data, the rest is your
program's prose, and the help page is generated fresh--so the
page's structure belongs to Appeal's template, not to how you
happened to arrange your docstring.

**Your docstring is Markdown.**  The first paragraph is the
summary.  Everything after it is the documentation.  And a line
that is exactly `# Arguments`, `# Options`, or `# Commands`--one
octothorpe, one space, that capitalization, at the left margin,
not inside a code fence--opens a special section, which must
contain exactly one **definition list**: the term is a parameter
name (or command word), bare and unformatted; the details after
the `:` are Markdown.

    import appeal

    app = appeal.Appeal(name='serve')

    @app.precommand()
    def serve(host, port: int = 8080, *, verbose=False):
        """
        Serves the thing.

        Longer prose about serving.  This paragraph, and any
        other prose, is the page's documentation block.

        # Arguments
        host
        : The host to serve on.

        port
        : The port.  Defaults to 8080.

        # Options
        verbose
        : Print more output.
        """
        print('serving', host, port, verbose)

    if __name__ == '__main__':
        import sys
        sys.exit(app.main())

`serve --help` renders:

    usage: serve [-v|--verbose] <HOST> [<PORT>]

    Serves the thing.

    Longer prose about serving.  This paragraph, and any other
    prose, is the page's documentation block.

    Arguments
    ---------

    <HOST>  The host to serve on.
    <PORT>  The port.  Defaults to 8080.

    Options
    -------

    -v|--verbose  Print more output.

The rules, all of them:

* The **term must be unformatted**--it's a name, not prose.  The
  details take whatever Markdown you like: emphasis, code spans,
  nested lists, multiple paragraphs (indent the continuations).
* A special section runs from its heading to the **next heading
  of any kind** (or the end), and must contain ONLY its
  definition list--trailing prose there is an error; put it
  before the section, or under its own heading.
* Only that exact spelling opens a section.  `## Options`,
  `# options`, `OPTIONS` underlined with dashes, an indented
  `# Options`, or a `# Options` inside a code fence is ordinary
  prose: Appeal leaves it in the documentation and says nothing.
* Your OTHER headings are yours: anything not named
  Options/Arguments/Commands stays in the documentation and
  renders as part of it.
* The details may nest a definition list of their own: indent it
  to the details' column (the column after `: `), colon and all.
* Entries are validated against your program's grammar, at build
  time, loudly: naming something that isn't a parameter is an
  error; documenting an option under `Arguments` (or vice versa)
  is an error.
* Terms render as their command-line **displays**: `host` becomes
  `<HOST>`, `verbose` becomes `-v|--verbose`.
* The docstring's section order doesn't matter; the page's order
  comes from the template (arguments before options, by
  default), and the template dresses the headings--your
  decoration is just how you spelled the input.  Your summary is
  the first paragraph.

## 2: Composable documentation

Converters are functions, and functions have docstrings--so
document a converter's parameters *once*, in the converter, and
every command using it inherits that documentation:

    import appeal

    app = appeal.Appeal(name='plot')

    def point(x: float, y: float):
        """
        A 2D point.

        # Arguments
        x
        : The horizontal coordinate.

        y
        : The vertical coordinate.
        """
        return (x, y)

    @app.precommand()
    def plot(label, p: point = (0.0, 0.0), *, verbose=False):
        """
        Plots a labeled point.

        # Arguments
        label
        : What to call it.

        p.y
        : The vertical coordinate.  (Overrides point's own
          documentation--the nearest enclosing scope wins.  A
          converter's parameter is reached by its path.)

        # Options
        verbose
        : Narrate the plotting.
        """
        print(label, p, verbose)

    if __name__ == '__main__':
        import sys
        sys.exit(app.main())

`plot --help`'s arguments table documents `label` (from plot),
`x` (inherited from point), and `y` (plot's override winning over
point's)--each documented where it made sense to write it.

The fine print:

* **Only entries travel.**  A converter's prose and summary stay
  home; they describe the converter, not your command.
* **Nearest wins, silently.**  Overriding what you inherit is the
  feature, not a conflict.
* **Only visible things have rows.**  The flat command line shows
  operands and options; an intermediate converter like `p` above
  is grammar structure, not a visible argument, so documenting
  `p` is an error that tells you to document its operands
  instead.
* Undocumented parameters still get their row, with an empty
  description.  Documentation is encouraged, never required.
* An option's converter can declare options of its own; their
  rows nest beneath the declaring option's row in the table, as
  a nested definition list.

## 3: Reshaping the page: the template

The page's structure is ONE template--a plain string on your
Appeal instance--naming six sections: `{usage}`, `{summary}`,
`{doc}`, `{options}`, `{arguments}`, `{commands}`.  All six must
appear.  The template establishes the page's ORDER, and its text
between placeholders is **Markdown**--the section headings, in
particular, are the template's to dress (`## Options` by
default).  Replace it to taste:

    import appeal

    app = appeal.Appeal(name='terse')
    app.templates = (
        'usage: {usage}\n'
        '\n'
        '{summary}\n'
        '\n'
        '{doc}\n'
        '\n'
        '## Options\n'
        '{options}\n'
        '\n'
        '## Arguments\n'
        '{arguments}\n'
        '\n'
        '## Commands\n'
        '{commands}\n'
    )

    @app.precommand()
    def terse(thing, *, loud=False):
        """
        Does the thing, tersely.

        # Arguments
        thing
        : The thing.
        """
        print(thing, loud)

    if __name__ == '__main__':
        import sys
        sys.exit(app.main())

That page leads with usage, then the prose, then options before
arguments.  A section with no content vanishes, header and all;
`usage` is not Markdown--it renders and wraps separately, at
whole units, and must sit on a single template line.  One
template serves both a command's help page and a dispatcher's
listing--the unused sections simply vanish.  Layout inside the
sections (the definition lists, wrapping, indentation) belongs
to big's renderer.

## 4: Color

Coloring is a **stylesheet**--big's `StyleSheet`, composed from
four layers: big's neutral Markdown defaults, the transforms,
a *palette*, and a *theme*.  A theme is plain data: a dict
mapping the Markdown concepts (`heading1`..`heading6`, `code`,
`link`, alerts, ...) and Appeal's role vocabulary (`program`,
`command`, `option`, `argument`, `oparg`, `summary`, `error`)
to style entries.  Colors in a theme are palette-independent
*names* (`red`, `dark_red`, `orange`, ...); the palette maps
them to escape sequences.  Appeal ships seven themes, in
layers: `plain_theme` is the root, every role a no-op, its
headings drawn with rules; `uncolored_theme` copies it and adds
the attributes (bold, italic, underline live there and nowhere
else), and underlines headings instead of ruling them; and a
colored theme wraps each role of the uncolored base in a color,
attributes inherited--`appeal_theme` (the default, designed
against the ANSI 16, so your terminal's own light/dark scheme
keeps it legible) and the four corners `light_warm_theme` /
`dark_warm_theme` / `light_cool_theme` / `dark_cool_theme`:

    import appeal

    from big.markdown import markdown_defaults
    from big.stylesheet import (StyleSheet, ansi_truecolor_palette,
                                transforms)

    sheet = (markdown_defaults | transforms | ansi_truecolor_palette
             | StyleSheet(appeal.dark_cool_theme))
    app = appeal.Appeal(name='vivid', stylesheet=sheet)

    @app.precommand()
    def vivid(image, *, contrast: float = 1.0):
        """
        Renders vividly.

        # Arguments
        image
        : The image file.
        """
        print(image, contrast)

    if __name__ == '__main__':
        import sys
        sys.exit(app.main())

`stylesheet=` takes the *complete* composition and uses it
verbatim, for every stream that wants color--Appeal adds nothing
and second-guesses nothing.  A stream that doesn't want color (a
pipe, or an environment that says no) gets `plain_stylesheet=`
instead, the same way; pass the same sheet to both to color a
pipe too (your sheet, your rules).  To recolor one thing, extend
a shipped theme:
`dict(appeal.appeal_theme, error=('T', '⦃bold⦙⦃orange⦙T⦄⦄'))`.
To draw rules under headings instead of underlining them,
`appeal.ruled_headings(appeal.appeal_theme)`.
`tools/theme_lab.py` renders a sample page under every theme,
built to be hacked on.

One entry is shape, not color: **`argument_decoration`** is how an
operand's name becomes its placeholder (`host` -> `<HOST>`).  Put
your own entry in the sheet you pass and usage lines, help tables,
and the command placeholder all follow it--
`dict(appeal.uncolored_theme, argument_decoration=('T', 'T'))`
renders operands bare.  Shape is decided at build (it's baked into
layout), so only an explicitly-given sheet changes it, and when
both sheets are given they must agree on it; the automatic
default below keeps `<HOST>`.

Which slot paints is decided per stream, at print time, the
same way CPython itself decides (`PYTHON_COLORS` beats
`NO_COLOR` beats `FORCE_COLOR`, then `TERM=dumb`, then whether
the stream is a terminal).  `stylesheet=None` (the default) is
`appeal_theme` over the ANSI 16; `plain_stylesheet=None` (the
default) is `plain_theme` over the plain palette (no escapes of
any kind, headings ruled).  `stylesheet=False` means never any
color: the plain slot paints every stream.

Two guarantees worth knowing:

* **Color never moves text.**  Layout is computed on
  styles-stripped text and painted afterward, so a colored help
  page strips back to its theme's monochrome page byte-for-byte.
  (The theme decides structure: a colored theme underlines its
  headings, where `plain_theme` draws rules--so a colorless
  stream's page differs there, and only there.)
  Piping `--help` through `sed -e 's/\x1b\[[0-9;]*m//g'`
  proves it, if you're the proving kind.
* **Roles, not escapes.**  The page is built as structural role
  markup ("this span is an option"), and escape codes enter only
  at print time, per the stylesheet decision above--so the same
  page colors on a terminal and stays clean into a pipe, decided
  per stream, every time.

## 5: Your docs, elsewhere

The same documentation renders in other dialects, by API
(deliberately no command-line switch--wire one up if you want
it):

* `app.documentation('gfm')`--GitHub-flavored Markdown for a
  README: definition lists become inline-HTML `<dl>` (with the
  blank lines that make GitHub render the Markdown inside);
  everything else GitHub renders natively.
* `app.documentation('commonmark')`--pure CommonMark: definition
  lists become a bold term over a blockquote, alerts become
  bold-labelled blockquotes, strikethrough is stripped.
* `app.documentation('troff')`--a man(1) page.

Tab completion is another face of the same design--the grammar
answering questions about itself; its walkthrough lives in
appeal.completion.md, Part 3.

## 6: The famous `make -j`

The `--jobs`/`-j` option of unix `make` has two defaults: run
`make` and it uses one job; `make -j 5` uses five; a bare
`make -j` uses as many as it likes.  In Appeal an option's value
is required unless you say otherwise (ruled 2026-08-03, the make
precedent: `-f file` is the common case), and the way to say
otherwise is a converter that takes one operand with a default
(Larry, 2026-09-18: the one spelling):

    import appeal

    app = appeal.Appeal(name='make')

    def jobs(jobs: int = 0):
        "How many jobs to run in parallel; zero means as many as it likes."
        return jobs

    @app.precommand()
    def make(*targets, jobs: jobs = 1):
        """
        Builds the targets.

        # Arguments
        targets
        : What to build.
        """
        count = 'unlimited' if jobs == 0 else jobs
        print(f"building {targets} with {count} jobs")

    app.main()

Three cases.  Absent, the parameter's own default fills (`1`).
Bare `-j` calls the converter with nothing, so its own default
answers--zero here, your sentinel for "no limit"; you choose it,
not the type.  With a value, `make -j 5` is five jobs, and so are
`make -j5` and `make --jobs=5`.  (Not `-j=5`: `=` is a long-option
separator only--on a short option the rest of the token binds
verbatim, so `-j=5` hands the int converter `=5`.)  The usage line
reads `[-j|--jobs [<JOBS>]]`: the converter's own parameter name is
plumbing, and the option's names the placeholder, as it does for a
positional argument.  An option whose parameter is a plain type
requires its value, full stop--exactly like make's man page marks
`-j [jobs]` and not `-f [file]`.

An optional operand is *greedy*: when `-j` has a next token, it
takes it--whatever it looks like.  So `make all -j install`
hands `install` to `-j` and fails loudly--`invalid value
'install' (not a valid int)`--rather than quietly guessing you
meant it as a target.  If `-j` should not eat the word after
it, give `-j` its value explicitly or put it last.
