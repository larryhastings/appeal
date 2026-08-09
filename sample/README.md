# The north star, worked: one program, one spelling

`weather.py` is a complete Appeal program written the ONE way
Appeal programs are written--import appeal, make an app, decorate
your commands.  The standalone story (ruled 2026-08-09) is a
compiled MODULE that wears the Appeal API, so this same file runs
both worlds; the whole difference is the import:

    try:
        import standalone as appeal      # shipped: the compiled parser
    except ImportError:
        import appeal                    # dev box: the real thing

## Try it

    cd sample
    rm -f standalone.py
    python3 weather.py                   # compiles standalone.py, exits
    python3 weather.py report portland -v
    python3 weather.py --help
    FORCE_COLOR=1 python3 weather.py report --help
    python3 weather.py zzz               # error + listing, stderr

The first run falls through to real appeal, notices (the
`recompile` flourish) and writes `standalone.py` beside itself.
Every run after that imports the compiled module--no appeal, no
big, anywhere.  The flourish is optional showing-off: a shipped
program only needs the try/except, and regenerating is "delete
standalone.py, run once against installed appeal".

## How the compiled module works

`standalone.py` (~4800 lines, meant to be read) contains:

* the appeal runtime, plucked live from installed appeal+big at
  compile time: parsing, dispatch, the layout engine, StyleSheet,
  the palettes, all seven themes, help finishing;
* the baked help--layout tuples wearing role markup, wrapped and
  painted at the real terminal at print time;
* the generated parser: `scan_`/`run_`/`parse_` per command,
  plain readable ifs;
* `_STANDALONE`, the spec: per-command FINGERPRINTS and converter
  resolution paths;
* an `Appeal` class that doesn't build anything: `@app.command()`
  on `forecast` says "I have the precompiled bits for that over
  here", and binds the live function as the thing `run_forecast`
  calls.  Converters resolve from the live function's annotations
  the same way--the module never imports your code.

Verification is all-or-nothing at `main()`: EVERY registered
function is checked against its fingerprint (signature, defaults,
annotations, docstring, @option/@parameter decorations), not just
the command you ran.  Edit `forecast`'s signature and run
`report`--it yells about forecast, lists every offender, and
names the remedy (regenerate).

Live knobs stay live: `stylesheet=`, `errors=` (any stream) apply
at YOUR program's runtime; the environment (NO_COLOR and friends)
always wins by default.
