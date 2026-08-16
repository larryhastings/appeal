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

## Special converters -- split, counter, accumulator, validate, ...

The `sync` command exercises them.  Every one splits into
*recognition* (arity/flag-ness, always baked into `scan_*`) and
*conversion* (string -> value), and conversion lands in one of
three buckets -- none of which imports appeal:

1. **Baked inline** -- codegen knows what they do, so it emits plain
   Python.  `counter(step=2)` becomes `2 * count`; `accumulator[str]`
   becomes a list comprehension; `validate('fast','safe')` becomes a
   membership check; `int`/`float` become `int(x)`.  The MultiOption
   *classes* never ship -- they're a build-time abstraction; the
   runtime just does the fold.
2. **A tiny runtime helper** -- `split` uses `multisplit()` (a
   ~10-line stand-in for big's `toy_multisplit`), on an operand
   (`source`) or an option value (`only`); `file` would use
   `open()`.  Small logic, no big/appeal.  `mapping[str,int]`
   (`--define KEY VALUE`, two tokens per occurrence) folds inline
   to a dict.
3. **Imported from your module** -- a custom converter function or
   `MultiOption` subclass is *your* code; the parser imports it just
   like it imports the command functions.

The vocabulary NAMES (`appeal.split`, `appeal.counter`, ...) exist
here as inert **stand-ins** so a signature like
`def sync(source: appeal.split(':'))` evaluates at import without
pulling real appeal.  They're never called on the fast path (the
conversion is baked); they carry the recipe so the fallback can
rehydrate the real converter for help.  Measured: `sync` with all
four special converters parses at bare-Python speed, zero appeal
modules loaded.

## What's faked (this is a prototype)

* The baked parsers are hand-written; codegen would emit them.
* The decorators match commands by *name*; the real shim
  fingerprints the live functions and reports drift ("regenerate
  the parser").  There's no lazy-recompile step here.

The point it proves: keep the Appeal interface AND full help, drop
the import weight from the hot path, and a pre-compiled parser is
as fast as Python can start.
