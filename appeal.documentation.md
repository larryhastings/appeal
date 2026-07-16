# Documenting an Appeal program (and coloring its output)

*The user guide for Appeal 1.0's help system: how docstrings become
`--help` pages, how documentation composes across converters, how
to reshape the output with templates, and how to color it with a
theme.  The design and its rulings live in the proposal (§8.7,
§8.7.1, §8.8) and in appeal.completion.md; this document is the
walkthrough.  Every example here is executed by the test suite.*


## 1: The docstring is input, never output

Appeal never shows your docstring to anyone.  It *reads* it: named
sections are slurped out as data, all remaining prose coalesces
into one blob, and the help page is generated fresh from that
data--so the page's structure belongs to Appeal's templates, not
to how you happened to arrange your docstring.

A docstring is prose, except where a **section heading** appears:
a line that is exactly `Arguments:`, `Options:`, `Commands:`, or
`Subcommands:`--capitalized, on a line by itself, nothing else on
the line.  A heading opens a section of **entries**:

    import appeal

    app = appeal.Appeal(name='serve')

    @app.global_command()
    def serve(host, port: int = 8080, *, verbose=False):
        """
        Serves the thing.

        Longer prose about serving.  This paragraph, and any other
        prose, coalesces into the page's documentation block.

        Arguments:
          host: The host to serve on.
          port: The port.  Defaults to 8080.

        Options:
          verbose: Print more output.
        """
        print('serving', host, port, verbose)

    if __name__ == '__main__':
        import sys
        sys.exit(app.main())

`serve --help` renders:

    Serves the thing.

    usage: serve [-v|--verbose] host [port]

    Longer prose about serving.  This paragraph, and any other
    prose, coalesces into the page's documentation block.

    Arguments:
        host  The host to serve on.
        port  The port.  Defaults to 8080.

    Options:
        -v|--verbose  Print more output.

The rules, all of them:

* An entry is a `name: text` line indented under the heading.
  Deeper-indented lines continue the entry.  Entry text is kept
  with kid gloves--paragraphs and indented code examples inside an
  entry survive into the rendered table.
* A section runs from its heading to the first blank line.  One
  section per heading kind.  `Commands:` and `Subcommands:` are
  the same section wearing context-appropriate clothes (use
  `Commands:` on a global command, `Subcommands:` on a command
  that has them).  `Sub-commands:` is an error, so you don't have
  to remember which spelling we picked.
* A bare `name: text` line *without* a heading above it is just
  prose.  `Note: remember this` stays a note.
* Entries are validated against your program's grammar, at build
  time, loudly: naming something that isn't a parameter is an
  error; documenting an option under `Arguments:` (or vice versa)
  is an error.  A keyword-only parameter *without* a default is a
  required trailing operand, so it belongs under `Arguments:`.
* The docstring's section order doesn't matter; the page's order
  comes from the templates (arguments before options, by
  default).  Your summary is the first prose paragraph.

## 2: Composable documentation

Converters are functions, and functions have docstrings--so
document a converter's parameters *once*, in the converter, and
every command using it inherits that documentation:

    import appeal

    app = appeal.Appeal(name='plot')

    def point(x: float, y: float):
        """
        A 2D point.

        Arguments:
          x: The horizontal coordinate.
          y: The vertical coordinate.
        """
        return (x, y)

    @app.global_command()
    def plot(label, p: point = (0.0, 0.0), *, verbose=False):
        """
        Plots a labeled point.

        Arguments:
          label: What to call it.
          y: The vertical coordinate.  (Overrides point's own
              documentation--the nearest enclosing scope wins.)

        Options:
          verbose: Narrate the plotting.
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
  is grammar structure, not a visible argument, so `p: ...` in a
  docstring is an error that tells you to document its operands
  instead.
* Undocumented parameters still get their row, with an empty
  description.  Documentation is encouraged, never required.

## 3: Reshaping the page: templates

The page's structure is a handful of named templates--plain
strings--living in a dict on your Appeal instance.  Overwrite
entries to taste:

    import appeal

    app = appeal.Appeal(name='terse')
    app.templates['help'] = (
        '{usage}\n'
        '\n'
        '{summary}\n'
        '\n'
        '{options}\n'
        '\n'
        '{arguments}\n'
    )

    @app.global_command()
    def terse(thing, *, loud=False):
        """
        Does the thing, tersely.

        Arguments:
          thing: The thing.
        """
        print(thing, loud)

    if __name__ == '__main__':
        import sys
        sys.exit(app.main())

That page leads with usage, then the summary, then options before
arguments.  The master templates are `help` (a command's page)
and `help commands` (a dispatcher's command listing); their
placeholders are `{summary}`, `{usage}`, `{documentation}` (the
prose blob), `{arguments}`, `{options}`, and--for the listing--
`{commands}`.  An absent section vanishes, its heading with it,
and a template line whose placeholders all rendered empty is
dropped.

The three *section* templates (`arguments`, `options`,
`commands`) control the tables themselves.  A section template is
its heading, then exactly two `{argument}`/`{documentation}`
pairs showing the shape of a row and the separation between rows:

    Options:
        {argument}  {documentation}
        {argument}  {documentation}

The literal text before `{argument}` is the table's indent; the
text between the placeholders is the spacer (yes, the definition
lists are laid out by big's `format_definition_list`--spacers,
hang rule, and all).

## 4: Color

Coloring is a `Theme`: named slots for the *roles* in Appeal's
output, each slot a little symbolic string:

    import appeal

    theme = appeal.Theme(
        program='bold',
        option='bright-cyan',
        metavar='dim',
        heading='bold underline',
        error='bold red',
    )
    app = appeal.Appeal(name='vivid', theme=theme)

    @app.global_command()
    def vivid(image, *, contrast: float = 1.0):
        """
        Renders vividly.

        Arguments:
          image: The image file.
        """
        print(image, contrast)

    if __name__ == '__main__':
        import sys
        sys.exit(app.main())

The vocabulary: `bold`, `dim`, `italic`, `underline`; the colors
`black red green yellow blue magenta cyan white` and their
`bright-*` variants; **other slot names**, which expand to that
slot's style (`metavar='option dim'` = whatever options look
like, dimmed); and, as an escape hatch, a raw ANSI sequence (a
value starting with the escape character passes through
verbatim).  Unknown words, and reference cycles, are errors at
construction.  The seven slots: `program`, `option`, `metavar`,
`operand`, `heading`, `error`, `summary`.

When color actually appears is a *runtime* decision, made the
same way CPython itself makes it: `PYTHON_COLORS` beats
`NO_COLOR` beats `FORCE_COLOR`, then `TERM=dumb`, then whether
the stream is a terminal.  Your theme describes what color looks
like; the user's environment decides whether it happens.
`theme=None` (the default) means the stock theme under those same
rules; `theme=False` means never.

Two guarantees worth knowing:

* **Color never moves text.**  Layout is computed uncolored and
  painted afterward, so a colored help page strips back to the
  monochrome page byte-for-byte.  Piping `--help` through `sed
  -e 's/\x1b\[[0-9;]*m//g'` proves it, if you're the proving
  kind.
* **The theme travels.**  A standalone script bakes the theme it
  was emitted with and re-decides at *its* runtime--emitting on a
  terminal doesn't color output that lands in a pipe.

## 5: Where completion fits

Tab completion is the third face of the same design--the grammar
answering questions about itself.  Its walkthrough (zsh and bash)
lives in appeal.completion.md, Part 3.

## 6: The famous `make -j`

The `--jobs`/`-j` option of unix `make` has two defaults: run
`make` and it uses one job; `make -j 5` uses five; a bare
`make -j` uses as many as it likes.  In Appeal that's a converter
whose one parameter has a default--two defaults, two homes:

    import appeal
    import math

    app = appeal.Appeal(name='make')

    def jobs(jobs: int = math.inf):
        return jobs

    @app.global_command()
    def make(*targets, jobs: jobs = 1):
        """
        Builds the targets.

        Arguments:
          targets: What to build.

        Options:
          jobs: How many jobs to run in parallel.
        """
        print(f"building {targets} with {jobs} jobs")

    app.main()

The parameter's own default (`1`) fills when `-j` never appears;
the converter's default (`math.inf`) fills when `-j` appears bare.

An optional operand is *greedy*: when `-j` has a next token, it
takes it--whatever it looks like.  So `make -j 5` is five jobs,
`make -j5` and `make -j=5` too, and `-j -5` would be negative five
(useless to make, but negative numbers cost nothing).  The flip
side of greed is that `make all -j install` hands `install` to
`-j` and fails loudly--`invalid value for 'jobs'`--rather than
quietly guessing you meant it as a target.  If `-j` should not
eat the word after it, give `-j` its value explicitly or put it
last.
