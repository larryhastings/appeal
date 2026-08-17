# Appeal sample: `weather`

One program, two engines.

- **`weather.py`** — the program: `import appeal`, make an app, decorate
  three commands (`report`, `forecast`, `sync`). `sync` exercises the
  special converters (`split`, `counter`, `accumulator`, `validate`).
- **`compiled.py`** — the precompiled parser, generated from those
  signatures. It imports only `appeal.runtime` (the stdlib-only core),
  bakes **only** parse + dispatch, and wears the Appeal API so the
  program imports it unchanged.
- **`regenerate.py`** — rebuilds `compiled.py` from `weather.py`.

## Run it

```
python weather.py report Paris --units F --verbose
python weather.py forecast Reykjavik 5
python weather.py sync a:b:c -vv --tag alpha --mode fast
python weather.py --help
python weather.py sync a:b --mode nope      # a usage error
```

The top of `weather.py` selects the engine:

```python
if 1:
    import compiled as appeal   # the fast, precompiled parser
else:
    import appeal               # the real Appeal, builds at runtime
```

Flip the `1` to a `0` and every command produces **byte-for-byte
identical** output — that's the contract.

## What the compiled module does (and doesn't)

- **Does**: parse and dispatch, at the speed of hand-written `if`s.
  It imports `appeal.runtime` and nothing heavy — no `inspect`, no
  `build`, no rendering machinery on the success path.
- **Doesn't**: bake any help or usage text. The moment the line asks
  for help (`--help`, `help CMD`) or is wrong (a bad value, an unknown
  command), the parser hands off to full Appeal, which renders the
  help or the colorized error **live** — never re-running your command
  line, so a command that already ran can't fire twice.
- **Polices itself**: each command carries a *fingerprint* of its
  signature (and, recursively, its converters'). Edit a signature or a
  converter and forget to regenerate, and `main()` refuses with a loud
  "stale — regenerate" error instead of parsing wrong.

## Regenerate after editing a command

```
python regenerate.py
```
