# Pre-compiled, not standalone: the Appeal API at bare-Python speed

A prototype (2026-08-16) of a third emission shape.  Where the
*standalone* script embeds all of Appeal+big into one ~200 KB
file, this **compiled** module stays small by importing a minimal
runtime -- it just refuses to drag in help/color/build.  It still
wears the Appeal API, so the program is unchanged:

```python
if 1:
    import compiled as appeal      # pre-compiled: fast
else:
    import appeal                  # real Appeal: builds at runtime
```

Flip the `1` to `0` and `weather.py` still works, byte-for-byte
the same commands.

## The pieces

| file | lines | what |
|---|---|---|
| `appeal_runtime.py` | 92 | the minimal runtime -- stdlib-only (`import sys`): `parse_tokens`, `check_count`, `run_main`, `UsageError` |
| `compiled.py` | ~120 | the pre-compiled parser wearing the Appeal API: baked per-command parsers + an `Appeal` class whose decorators *match* live functions to them (they don't build) |
| `weather.py` | ~45 | the program: decorated commands + the `1`/`0` toggle |

## The numbers (cold start, `weather.py report portland -v`)

    bare python ............................. 10.9 ms
    compiled (wears the Appeal API) ......... 11.0 ms   (+0.1)
    real appeal (same source, if 0) ......... 50.8 ms
    real appeal standalone (~200 KB) ........ 39.4 ms

Python-start to dispatched command at bare-Python speed, with the
Appeal interface intact.  The whole runtime is 3 KB instead of
200 KB.

## Help by fallback -- not baked in

The compiled module carries **no** pre-digested help text: no
docstrings, no layout tuples, no renderer.  That's deliberate --
importing even the digested help would cost time the fast path
shouldn't spend.

So the fast path (parse a valid line, dispatch) touches nothing
heavy and never imports appeal.  The instant the minimal runtime
raises -- a usage error, or a `--help` it doesn't recognize --
`compiled` falls back: it imports the real Appeal, rebuilds the app
from the *same live functions*, and lets it render full color,
word-wrapped help straight from their docstrings.

    weather report portland -v     # fast path: 0 appeal modules loaded
    weather report --help          # fallback: full color help
    weather report                 # fallback: rich usage error

Cold start (`report portland -v` vs `report --help`):

    fast path (dispatch) ..... 11.2 ms   (bare python: 12.2)
    fallback (--help) ........ 44.2 ms

## What's faked (this is a prototype)

* The baked parsers are hand-written; codegen would emit them.
* The decorators match commands by *name*; the real shim
  fingerprints the live functions and reports drift ("regenerate
  the parser").  There's no lazy-recompile step here.

The point it proves: keep the Appeal interface AND full help, drop
the import weight from the hot path, and a pre-compiled parser is
as fast as Python can start.
