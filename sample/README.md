# The north star, worked: one program, two spellings

This directory holds the same little program twice, so you can see
exactly what "standalone script emission" means end to end.

    sample/
      in_process/
        weather_commands.py   the program's business logic (shared)
        weather.py            imports appeal, builds the app, main()
      standalone/
        weather_commands.py   the SAME business logic, verbatim copy
        weather               the generated script: the whole parser,
                              no appeal, no big
      regenerate.py           the emission step: rebuilds standalone/weather
                              from the in-process app

## The in-process spelling

`in_process/weather.py` is how you develop: ~10 lines.  Import
appeal, register the two commands from `weather_commands.py`, call
`app.main()`.  Appeal builds and compiles the parser lazily, in
memory, every run.  Requires appeal (and big) installed.

    cd in_process
    python3 weather.py report portland -v
    python3 weather.py --help

## The standalone spelling

`standalone/weather` is what you ship.  It was written by
`regenerate.py`, which imports the in-process app and calls
`app.standalone(argv0='weather')`--that's the entire emission step.
The result is one executable file (~4800 lines, ~190KB) whose only
import from your project is `weather_commands`--the business logic
travels beside it, unchanged.  No appeal.  No big.  Nothing else.

    cd standalone
    ./weather report portland -v
    ./weather --help
    FORCE_COLOR=1 ./weather report --help    # roles paint
    ./weather zzz                            # error + listing, stderr

Every command line produces byte-identical output, stdout and
stderr and exit code, in both spellings (checked mechanically:
--help, per-command help, `help CMD`, dispatch, errors, the bare
line).

## A map of the generated script

Line numbers from the current regeneration; the file is meant to be
READ--generated code that reads like hand-written Python is part of
the promise.

      8  ---- the appeal runtime ----   snippet regions, grabbed LIVE
                                        from installed appeal+big at
                                        emission time (the compile-time
                                        grab): exceptions, parse_tokens,
                                        run_command_set, run_main...
    1540  uncolored_theme               the themes, as plain dict DATA
    1599  appeal_theme                  (all seven ride along)
    1664  resolve_stylesheet            the per-stream color decision
    1843  render_baked_help             finishes baked help at runtime:
                                        wrap at the real margin, paint
                                        for the real stream
    2439  wrap_words                    big's layout engine
    4077  class StyleSheet              big's renderer
    4397  ansi_16_color_palette         palettes, incl. best_palette +
    4501  best_palette                  can_colorize + ansi_color_depth
    4617  ---- your program ----        `from weather_commands import
                                        report, forecast`--the ONLY
                                        project import
    4627  _HELP_report / _forecast /    the BAKED HELP: layout tuples
          _HELP_command_set             wearing role markup, computed on
                                        the author's machine; the script
                                        only wraps and paints them
    4637  ---- generated parser ----    scan_/run_/parse_ per command,
                                        plain ifs, readable
    4813  if __name__ == "__main__":    sys.exit(run_main(...,
                                        stylesheet=None))

## The division of labor that makes help work

Help is split into a bake half and a finish half:

* **Baked on the author's machine** (needs appeal + big): docstrings
  parsed as Markdown, merged across converters, ordered by the
  template, laid out into width-independent tuples, dressed in role
  spans (`⦃command⦙report⦄`, `⦃option⦙-v⦄`, ...).  That's the
  `_HELP_*` constants--data, `repr`ed into the file.
* **Finished on the user's machine** (needs nothing): wrap those
  tuples at the real terminal's width, compose the stylesheet for
  the real stream (color iff the stream wants it--NO_COLOR and
  friends always win), paint, print.

So the script contains no Markdown parser and no docstrings--just
the finished layout, waiting for a width and a palette.

## Regenerating

    python3 regenerate.py

Rewrites `standalone/weather` from the in-process app and re-copies
`weather_commands.py`.  Regenerate rather than edit--the header of
the generated file says the same.
