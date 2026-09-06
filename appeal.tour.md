# A guided tour of the Appeal 1.0 implementation

*For Larry, who asked "where is the engine?" — the interpreter-only
answer.  Rewritten 2026-08-25, after the codegen/standalone machinery was
removed and the tree settled into eight modules; every count and claim
below was checked against the tree that day.*

> **A note on names.**  This release is **Appeal 1.0**; the previous
> releases were the 0.6 line.  In the engineering docs the rewrite is
> nicknamed **v2** and shipping Appeal 0.6.x is **v1** — the codenames the
> rewrite was carried out under.  "v1 semantics" means "what 0.6 did, kept
> on purpose."

> **What changed since the last tour.**  Appeal used to be a *compiler*: it
> had two parser "rungs" — an interpreter (`interpreter.py`, rung 1) and a
> code generator (`codegen.py`, rung 3) that emitted standalone scripts —
> plus `plan.py`, `build.py`, and a `runtime.py` snippet warehouse, and every
> case ran through both rungs for parity.  All of that is gone.  Appeal is
> now a single interpreter.  The eight files below replace those six; the
> library is smaller and there is exactly one code path from a command line
> to your function.

## Where the engine went

There are three moving parts, and they run in this order:

1. **`frontend.py` — signature → `Plan`.**  It reads a callable's parameters
   (without `inspect`, to stay fast) and builds a `Plan`: a small tree of
   slots, option rules, and terminals describing *what the grammar is*.  A
   Plan is inert data — no argv, no side effects.

2. **`backend.py` — `Plan` → `Converter` classes → run.**  It turns each Plan
   into a `Converter` subclass whose `register()` method emits a handful of
   **instructions** (Argument, Option, PreOption, Repeat).  An `Engine` then
   walks the actual `argv` against those instructions and calls your
   function.  This is the interpreter — the thing the old tour went looking
   for and couldn't find in one place.

3. **`__init__.py` — the `Appeal` object and the `Processor`.**  `Appeal` is
   your program's description (the command tree).  `Processor` is one run: it
   drives the backend over one command line, first in a dry structural
   pre-scan, then live.

Everything else is a consumer of those three: **`presentation.py`** renders
help/usage/errors, **`converters.py`** is the type vocabulary,
**`load.py`** runs a command from structured data instead of a string, and
**`schema.py`** describes the plan tree as plain data (the `app.schema(format)`
formats); **`mcp.py`** / **`completion.py`** serve MCP tools and shell
completion (the MCP server consumes schema.py's JSON-Schema projection).
None of them are on the fast path of a successful parse.

## The one mental key: instructions, a queue, and a handler table

If you hold one picture in your head, hold this one.

A `Converter`, when the Engine **enters** it, calls its `register()`, which
drops **instructions** into the running Engine:

* an **`ArgumentInstruction`** (a positional operand) is pushed onto
  `engine.queue` — the list of operands still waiting to be filled;
* an **`OptionInstruction`** or **`PreOptionInstruction`** installs a
  **handler** (a *binding*) into `engine.handlers`, keyed by the option
  string (`--verbose`, `-v`).

Then the Engine walks `argv` left to right:

* a **non-option** token fills the next `ArgumentInstruction` off the queue;
* an **option** token looks its string up in `engine.handlers` and **fires
  the binding**, which sets a value, folds an occurrence, or *conjures* a
  converter that isn't there yet.

Three rulings fall out of this shape, and they're the whole personality of
the parser:

* **Flat recognition.**  Options are registered *eagerly* — the moment a
  converter's instructions are laid down, before its operands arrive (a
  `*args` window's options are registered up front too).  So an option is
  recognized *anywhere* on the line, not only after you've reached its
  group.  That's why `draw a --bold` says "`--bold` only becomes available
  if you specify `<WIDTH>`" instead of "unknown option": `--bold` is known;
  it just has no `size` to attach to yet.

* **Eras and regions.**  Entering a converter runs *its* `register()`, so its
  options enter the tables exactly when it's in scope and leave when it's
  not.  Shared option strings bind *announce-first* (the leading occurrence
  goes to the first window; later ones track the current region).

* **Conjuring.**  A `PreOptionInstruction` installs a `Conjure*` binding:
  firing the option *summons* the converter instance before its operands
  exist (the binding sets `instance._summoned = True`).  An all-optional
  converter is fully conjured this way; a converter with a required operand
  is summoned but then *starves* if the operand never arrives — that
  starvation is the availability error above.

## File by file

Eight modules, ~9,500 lines.

### `frontend.py` (~2,300 lines) — signature in, Plan out

Reads callables and builds Plans.

* **`signature()` / `Signature` / `Parameter`** — Appeal's own parameter
  reader.  It pulls names, kinds, defaults, and annotations off `__code__`
  and `__annotations__` directly; `inspect` is only imported as a last
  resort (Argument-Clinic'd builtins).  This is the ~7ms-per-import that the
  fast path avoids.
* **`Plan`, `Slot`, `OptionRule`, `Terminal`** — the noun.  A `Plan` has
  ordered `slots` (positional operands, each a `Terminal` leaf or a nested
  child `Plan`) and `options` (`OptionRule`s).  `usage()` renders the plan's
  usage line, with role spans for coloring.
* **`build_plan(callable)`** — the entry point.  Walks the signature, maps
  parameters to slots and options (leaf converter, converter group, `*args`
  repeat, fold, flag, …), runs the analyses (option reachability, the
  same-world rule), and returns the top `Plan`.
* **`_is_option_group` / `_is_multiparam_converter` / `dereference_annotated`
  / `_oparg_names`** — the classification helpers that decide what a
  parameter *is*.

### `backend.py` (~1,500 lines) — the interpreter

The Plan consumer.

* **The instructions** — `ArgumentInstruction`, `OptionInstruction`,
  `PreOptionInstruction`, `RepeatInstruction`.  Each has a `register()` that
  acts on the Engine (queue or handler table).  These are the "opcodes,"
  except there's no program counter — they're declarative registrations that
  a reactive loop consumes.
* **The bindings** (option handlers) — `LiveBinding` (a flag),
  `ValueBinding` (one oparg), `MultiBinding` (a fold: counter/accumulator/
  mapping), `GroupBinding` (a converter-group option), and the four
  **`Conjure*` bindings** (`ConjureBinding`, `ConjureValueBinding`,
  `ConjureFoldBinding`, `ConjureChainBinding`) that a `PreOption` installs to
  summon a not-yet-built converter — including down a nested chain.
* **`build_converters([plan, ...])`** — compiles Plans into `Converter`
  subclasses (`_build_class` per plan; group refs and fixups resolve through
  the class table).  A `Converter` holds two phases: parse (fill `args`/
  `kwargs`) and render (`__call__`, which finalizes deferred values and calls
  your function).
* **`Engine`** — one converter, one token stream.  `enter()` registers a
  converter's instructions and reserves trailing operands; the run loop fills
  the queue and fires handlers; `run()` does it, `consumed` reports how far
  it got.  The `dry` flag is the structural pre-scan: it validates shape
  (arity, unknown options) without converting or calling anything.
* **`execute(commands, argv, ...)`** — the low-level "run this table of
  converter classes against this argv" entry the facade calls.
* **The dry-run helpers** — `_own_shape`, `_group_capacity`, `_valid_counts`,
  `_availability_message`, `_takes_many` — read a converter's shape (by
  dry-running its `register`) to size options and write good errors.

### `converters.py` (~500 lines) — the vocabulary

The built-in converter factories and the `MultiOption` protocol: `split`,
`validate`, `validate_range`, `counter`, `accumulator`, `mapping`, `file`,
`optional`, plus `convert()` (the leaf conversion that turns
`ValueError`/`TypeError` into a polite `UsageError`).  Low-level: depends
only on the exceptions.

### `__init__.py` (~2,550 lines) — the facade, the tree, the Processor

The public surface and the dispatcher.

* **`Appeal`** — your program.  It's a *tree* of `Appeal` nodes (one per
  command word); `@app.command()` / `@app.precommand()` / `@app.default()`
  register callables, `@app.subcommand(path)` mounts deeper.  Registration
  is lazy; plans build on first use (or eagerly, at first `process()`, unless
  `lazy=True`).
* **`Processor`** — one run.  `Processor(app)(argv)` runs the whole line
  twice: a **dry** `_run_node` (structural pre-scan across the entire command
  set — every command's arity and options, no bodies) then a **live**
  `_run_node` that pops the converters the dry pass built and actually
  converts and calls.  A structural error anywhere aborts before anything
  runs; a *conversion* error partway down doesn't un-run an earlier command
  (streaming dispatch).
* **`_run_node`** — dispatch for one set node: run its **eras** (the
  precommands, hoisted so a class runs before its own members), then its
  **command words**, recursing for subcommands.  A class-as-app constructs
  its instance once and stashes it in `env`; method commands bind to it from
  there.
* **`run_main`** — `main()`'s driver: catch `AppealError`, print it politely
  (through the stylesheet), map to an exit code.
* **config layering** — `_config_vet` / `_config_apply`: a dict bound to a
  precommand (`@app.precommand(config=...)`) layers onto that precommand's
  options at dispatch (defaults < config < argv).
* **the exceptions** — `AppealError` and its children (`ConfigurationError` =
  your bug; `DataError`/`UsageError` = the user's input; `CommandError` = a
  command failing on purpose).

### `presentation.py` (~1,400 lines) — all human-facing output

Lazy: imported only when help, usage, or an error actually renders, so the
success path never pays big's startup.  It scans docstrings (a hand-written
scanner, no Markdown parser), transforms README-grade Markdown, assembles the
help page as a template-ordered set of pieces, and renders them through big's
markdown + stylesheet pipeline — colored on a tty, stripped otherwise.  Help
tables carry their role coloring structurally, as big `StyledText` nodes.

### The other consumers

* **`load.py`** (~400 lines) — `read_mapping` / `read_iterable` / `read_csv`:
  run a command from a dict, a list, or a CSV row, through the *same*
  converters, no command line involved.
* **`schema.py`** (~260 lines) — the plan tree as plain data: the full
  description (`app.schema('appeal')`) and the JSON-Schema projection
  (`app.schema('mcp')`) both live here; generic machinery, MCP is one
  consumer.
* **`mcp.py`** (~150 lines) — serve the commands as MCP tools (stdio, stdlib
  only), schemas from schema.py; the class-as-app constructs once at
  server startup.
* **`completion.py`** (~560 lines) — shell tab completion off the same plans.

## The dispatcher and the Processor: two passes

`Processor(app)(argv)` is the whole run, and it makes two passes over the
line:

```
built = []
app._run_node(argv, 0, self, top=True, dry=True,  built=built)   # pre-scan
built.reverse()
app._run_node(argv, 0, self, top=True,           built=built)    # live
```

The **dry pass** validates the entire command set structurally and records
each `Converter` class it builds into `built` (in traversal order).  The
**live pass** pops those classes back off — nothing is built or planned
twice — and this time converts operands and calls your functions.  Because
the two passes visit the same converters in the same order, the pre-scan can
reject a malformed line (bad arity three commands deep) before the *first*
command runs, while a genuine conversion failure mid-line still leaves the
commands that already ran, run.

`_run_node` handles one set node.  For that node it:

1. runs the **head eras** — the help/version precommand, then each
   registered precommand, in registration order but with a class hoisted
   ahead of its own member precommands (it builds the instance they need);
2. dispatches **command words** left to right, entering subcommand sets
   recursively;
3. if nothing was named, runs the node's **default**, or prints the listing.

A class era constructs its instance and stashes it (`env[cls.constructs] =
instance`); a method/BIC era or command binds its `self` from `env` (`conv.
bound = env.get(cls.binds)`).  That `env` dict — created fresh per run — is
the whole of the class-as-app machinery.

## Deep dive: one signature, start to finish

`build_plan(cmd)` for `def cmd(src, dst='.', *, verbose=False)`:

1. `signature(cmd)` reads three parameters off `__code__`: `src`
   (positional, required), `dst` (positional, default `'.'`), `verbose`
   (keyword-only, default `False`).
2. Each positional becomes a `Slot` with a `Terminal` child (leaf converter
   `str`); `verbose`, keyword-only with a bool default, becomes an
   `OptionRule` of kind `flag`.
3. The analyses run: option reachability (no option is stomped by a greedy
   earlier one), the same-world rule (if `cmd` were a method, its class must
   own it).
4. Out comes a `Plan` with two slots and one option — inert, ready to be
   compiled or rendered.

## Deep dive: one argv, start to finish

`app.process(['src', '--verbose'])` for that command:

1. `Processor(app)(['src','--verbose'])`.  Eager mode compiles every command
   in the tree first (so a config error anywhere surfaces now).
2. **Dry pass.**  `_run_node` builds `cmd`'s `Converter` class, makes an
   `Engine`, enters the converter (registers one Argument for `src`, one for
   `dst`, a `LiveBinding` handler for `--verbose`/`-v`), and walks the tokens:
   `src` fills the `src` Argument; `--verbose` fires its handler; `dst` is
   optional and absent.  No conversion, no call.  The built class is recorded.
3. **Live pass.**  Same walk, but now `src` is converted (`str('src')`),
   `--verbose` stores `not False`, `dst` takes its default, and the
   `Converter` renders — calling `cmd(src='src', dst='.', verbose=True)`.
4. `.result` is the return value; `.instances` logs `(command, None)`.

## Where to look when…

* **a signature reads wrong** → `frontend.build_plan` and its classifiers.
* **a command line parses wrong** → `backend.Engine` (the run loop) and the
  binding that owns the option.
* **an option is "unavailable" / conjuring misbehaves** → the `Conjure*`
  bindings and `_availability_message`.
* **dispatch / eras / class-as-app** → `__init__._run_node`.
* **help/usage/errors look wrong** → `presentation.py`.
* **config layering** → `__init__._config_apply` / `_config_vet`.

## Things to type at it

```python
import appeal
from appeal.frontend import build_plan
from appeal.backend import build_converters, _converter_key

p = build_plan(lambda src, dst='.', *, verbose=False: None)
p.slots            # the positional operands
p.options          # the OptionRules
p.usage()          # the usage line (with role spans)

cls = build_converters([p])[_converter_key(p)]
cls().register     # what the Engine calls to lay down instructions
```
