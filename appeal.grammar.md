# The Appeal grammar

*Appeal 1.0's spec-of-record for how Python signatures read as command-line
grammars.  Everything else--the plan builder, the interpreter, usage
generation, the JSON schema--is tested against this document.
(Proposal §10 step 2.)*

> **A note on names.**  This release is **Appeal 1.0**; the previous
> releases were the 0.6 line.  In the engineering docs (this one, the
> tour, the proposal) the rewrite is nicknamed **v2** and shipping
> Appeal 0.6.x is **v1**--the codenames the rewrite was carried out
> under.  Wherever this document says "v1 semantics" or "probed
> against v1," read "the 0.6 line."

## The one rule

A callable is a production rule.  Its positional parameters are the
right-hand side, in order.  A parameter annotated with another callable
is a nonterminal--recurse.  Everything else is a terminal that consumes
one command-line string.

    command     ::=  option* operand*        (options interleave freely)
    rule(f)     ::=  slot(p1) slot(p2) ... slot(pn)
    slot(p)     ::=  STRING                  if converter(p) is a terminal
                 |   rule(converter(p))      otherwise

## The mapping table

| You write | Appeal reads |
|---|---|
| positional param, no default | required operand |
| positional param, with default | optional operand (may promote--see grouping) |
| `*args` | zero-or-more repetition of `slot(args)`.  A converter with several parameters and/or its own options makes each instance a **group**: a fixed-size chunk of operands, with the group's options bound to instances by **window** (see Options).  Staged: group parameters must be required terminals (fixed instance size). |
| keyword-only, with default | an **option** (`--name`), not an operand |
| keyword-only, **no** default | **rejected** (`ConfigurationError`): keyword-only params map to options, and options are always optional, so they must have a default (the 0.6.4 rule).  For the `cp SRC... DST` shape--a required operand after an absorbing group--use a converter group: the group's `*args` absorbs, a plain positional after it is reserved from the end |
| `**kwargs` | legal: it receives `@app.option` declarations for parameters not in the signature.  Absent ones simply aren't passed (v1, probed: `F a {}`)--a deliberate asymmetry with real parameters, whose defaults DO fill: `'verbose' in kwargs` is the only place "given at all?" is observable.  Repeatable kinds (`list[T]`, `dict[K, V]`, `MultiOption`) route too.  NOT an arbitrary-option sink: undeclared options stay unknown.  Bare `**kwargs` gets nothing. |
| positional-only marker `/` | no grammatical meaning (deliberately, ask the author of PEP 570) |
| no annotation | `str`--the identity terminal |
| annotation = callable | that callable's rule (recursion!) |
| annotation = `bool` on an option, default `False` | flag: presence means `True`, takes no operand |
| `Annotated[T, converter]` | the documented v1 contract, kept: "Appeal only ever uses the *last* value"--and it holds at **every** annotation site (positional, option, `@app.option`'s `annotation=`, `*args`, trailing operand, tuple element, `list[T]`/`dict[K, V]` elements).  Provisional import: on Python < 3.9 Appeal limps along without it, v1-style. |
| annotation = `list[T]` on an option | accumulator: option may repeat, collects `T` values |
| annotation = `dict[K, V]` on an option | mapping: option may repeat, collects `K=V` pairs |
| `list`/`dict` anywhere else (operands, tuple elements, nested inside each other) | refused by name: they are *option-repetition* spellings (a fold over occurrences), not operand shapes.  A list-shaped value from one operand is a converter's job (the `appeal.split` idiom). |
| annotation = `tuple[T1, T2, ...Tn]` on a positional param | one slot consuming n operands, building a tuple; elements recurse (converters, nested tuples).  `tuple[T, ...]` (variable-length) refused by name--use `*args`. |
| annotation = `tuple[T1, ...Tn]` on an option | a multi-operand value option building a tuple (`--span 3 4`); elements must be terminals; `tuple[T, ...]` refused by name--use `list[T]` |
| converter subclassing `Option` | repeatable option with a custom fold (v1's protocol; v1 spelled this `MultiOption`, now an alias): `init(default)` once, `option(...)` per occurrence--its signature defines the per-occurrence operands, each a required terminal, converted; zero and multi-operand occurrences both work (`--pt 3 4`)--and `render()` produces the value.  **If the option is never given, the class is never instantiated**: the parameter's default passes through untouched.  Only meaningful on options; refused by name elsewhere. |
| annotation = `X | None` (builtin union, 3.10+) | the converter is X--the None arm is the type checker's (it's the default's type).  Only the BUILTIN spelling per the house typing stance; `typing.Optional`/`typing.Union` stay unsupported, and a union of two real types (`int | str`) is refused by name (fixed 2026-09-03, Sol review #4) |
| default value, no annotation | converter is `type(default)` if in `{str, int, float}`, else `str` |
| default value is a `list`, no annotation | a group inferred from the element types: `b=[0, 0.0]` takes an int and a float and produces a list (v1's corpus) |

**Stringized annotations are refused** (ruled 2026-09-03): a string
where an object belongs--whether from `from __future__ import
annotations` (PEP 563) or hand-stringizing--raises
`NotImplementedError("Appeal doesn't support stringized
annotations")` at build.  Appeal reads annotation OBJECTS; stringized
annotations are going away (PEP 649), and Appeal won't grow support
for them.

## Options

* Long name: parameter name, underscores become dashes (`dry_run` →
  `--dry-run`).  A **one-character name gets no long option** (v1,
  probed: parameter `n` has only `-n`; `--n` doesn't exist).  An
  option that ends up with no strings at all (one-char name, letter
  taken) is a named config error.
* **Repetition is last-one-wins** (ruled 2026-07-09): a flag or a
  single-value option given twice keeps the *last* occurrence--the
  command line can always override or subtract, and a repeated flag is
  idempotent.  Only the repeatable kinds *collect* occurrences
  (`list[T]`, `dict[K, V]`, and `Option` subclasses).
* Short name: the parameter's first letter (`-v`), assigned first-
  declared-first-served (walk order: a rule's own options, then its
  children's, depth-first); an option whose letter is taken gets no
  short.  (Matches v1's observed behavior.)
* This long-and-short default is the constructor knob
  `default_options` (v1's, restored): a policy `(name, annotation,
  default) -> list[str]` run at build time on every
  automatically-mapped keyword-only parameter.  The stock
  `default_options` yields the long plus the short; `default_long_option`
  drops the short (the "no auto shorts" policy), `default_short_option`
  drops the long, or supply your own.  It runs at build time; only the
  option strings it produces reach the parser.
* **`@app.option(parameter_name, *strings, annotation=…, default=…)`
  blows away ALL default mappings** for one keyword-only parameter
  and maps *only* the strings you specify--no auto long, no auto
  short.  (That's how you suppress an unwanted short: name only the
  long.)  It is a **fresh declaration**, v1 semantics by ruling: the
  parameter's own annotation and default do *not* leak into the
  option's grammar--`annotation`/`default` here declare the option's
  converter and flag-ness exactly as they would on a parameter, and
  a bare `@app.option` is therefore a plain one-value str option,
  even on a bool parameter.  The value when the option is absent is
  always the *parameter's* own default (v1 never passed ungiven
  kwargs).  Stacked decorators accumulate strings.  Option strings
  are validated v1-style: `-X` (one alphanumeric) or `--xxx`
  (two-plus characters).  Naming a parameter that isn't
  keyword-only-with-default is a configuration error.  **Each
  @app.option call is its own declaration** (v1: `--north` and
  `--south` may map one parameter through different converters);
  re-applying an identical declaration is a no-op (overrides live
  on the function; test harnesses re-decorate freely).  A
  **zero-argument converter** as an option's annotation is a flag
  that produces a value by calling it.
* **`@app.parameter(name, *, usage=...)`** renames one parameter
  wherever it shows: an operand's name in usage lines and help
  tables, or an option's metavar (`[-t|--times COUNT]`--the rename
  is literal, replacing the default `<NAME>` decoration).
  v1's API, extended--v1's `@app.parameter` only reached operands; the
  option metavar was unrenamable.  Decorate a converter directly
  to rename its parameters.  `argument` is v1's deprecated alias,
  kept.  Naming a parameter the function doesn't have is a config
  error.
* An option's own parameters are its operands: `def serve(*, port: int)`
  gives `--port <PORT>` (the operand shows the parameter name,
  decorated by default).  An option converter with
  several parameters consumes several operands per use, each shown
  by its own parameter name (`--where x y`; v1, probed).
  Parameters with defaults make those operands *optional*--the
  `make -j` shape--and consumption is **unconditionally greedy** to
  the converter's maximum: a pending operand takes the next token
  whatever it looks like (v1, probed: `-j 5` is jobs=5, `-j -5`
  works for free, `-j -v` is a loud conversion error--never
  reinterpreted as the flag--and a pending operand even eats `--`).
  "Optional" only means running out of line is legal.  The count
  grabbed must still be one the converter accepts; otherwise the
  valid-counts English, naming the option (v1: "grabs whenever one
  exists, then may fail").  The same rule covers `Option`/
  `MultiOption` subclasses whose `option()` has defaulted
  parameters (v1, probed).  Attachment: the long `--name=value` form
  works whenever the option takes exactly one operand.  For short
  options `=` is *not* a separator (getopt-pure, ruled 2026-08-27):
  the concatenated form `-jvalue` binds the rest of the token as the
  oparg--required or optional, unlike v1--and `-j=value` binds the
  literal oparg `=value` (think `-DNAME=1`).  An option that can take
  several operands stands last in its bundle, unattached.
* `--name=value` and `--name value` are equivalent for single-operand
  options.
* Single-dash bundling: `-abc` means `-a -b -c` when all are flags.
* `--` ends option recognition **for the rest of this parse only**--it
  is parser state, never program state (v1 leaked it; the plan tree has
  nowhere to put it).
* **Windows** (repeated groups only): when a converter group rides
  on `*args`, its option strings recur across instances, so
  position selects the instance: the one being built or *about to
  be built* (announce-first; v1, probed), and past the ends of the
  line, the nearest one.  With sizes A B C, the four positions
  `1 A 2 B 3 C 4` map to A, B, C, C (Larry's rule; v1 errors on
  position 4--a deliberate v2 improvement, still a strict
  superset).  Position never rejects; the only error is an option
  with no instances at all.  "Specified more than once" is per
  instance.
* **The gate rule** (Larry's ruling, July 2026): a **required**
  converter group is a wall--options of *skippable* groups behind
  it may not appear until the wall has been fed.  With
  `draw(shape, s: size, c: color=None)` and size required,
  `draw dot --bright 2.5` errors ("too early"); `draw dot 2.5
  --bright` works.  Skippable groups never gate anything (they
  might never appear), plain terminal operands never gate
  (`draw --bright dot` was ruled legal), and a certain group's
  OWN options float free--they announce something that's coming no
  matter what (v1, probed: the differential fuzz caught the first
  draft gating those too, which shipping v1 permits).
* **Scoped options** (July 2026 rulings; supersedes the staged
  scope semantics).  An option string declared by several
  *windows*--a converter reused across sibling slots, or two
  converters agreeing on the grammar--is legal, and occurrences
  bind by position, per **the interval model** (decoded from v1's
  pinned behavior): a window's interval runs from its first token
  to the next window's first token; the first window's interval
  reaches back to the start of the line (announce-first); the
  last window opened extends to the end ("the stuff in the final
  group sticks around forever"); a window nested inside an open
  window of the same key closes strictly--the enclosing window
  absorbs the rest.  Compatibility is exactly the circularity
  line: every declaration must agree on the option's *grammar*
  (kind and oparg counts--consumption must be binding-independent);
  defaults, converters, and docstrings may differ per window.
  Differing grammars refuse by name, as does the same string
  declared twice in ONE window (no position can tell those
  apart).  Giving one of a group's options still **forces the
  group** (v1)--a scoped occurrence forces only when no window
  could claim it, the forced window claims its forcer
  immediately, and forcing happens **at most once per key per
  line** (ruled: chain-conjuring only ever made all-defaults
  husks).  An occurrence with nowhere to go--no window can claim it--errors,
  naming the option (the "only becomes available if you specify ..."
  availability message).  The gate rule exempts scoped keys
  (their placement is the interval model's business).  Unique
  option strings keep flat recognition unchanged: one declaring
  window is every position's nearest.  In help, a duplicated
  display gets a position qualifier naming its flanking
  arguments--`-f|--flag (after a, before c)`--and an option
  whose converter declares options shows them indented beneath
  its row; a command-level docstring entry matching two sibling
  windows refuses ("document it in the converter's docstring").

Help, usage, and error output render through big's markdown +
stylesheet pipeline (`big >= 0.15`), imported **lazily**--only when
something actually renders.  So *parsing* has no runtime dependency on
big: a program that only ever succeeds never imports it, and pays none
of big's startup.  There is one copy of the wrapping/rendering code in
the world, big's; appeal calls it, it doesn't embed it.

## Multiple commands and the global command

All v1 semantics, empirically probed and kept:

* Each `@app.command()` function is a **subcommand**: the first
  operand on the line names one--the **literal** function name
  (`add_item`, not `add-item`; command names don't dash-mangle,
  only option strings do).  The command word is required even when
  only one command is registered.
* `@app.global_command()` is the command with no name.  Its options
  and operands come **before** the command word (its operands fill
  first; the next operand is the command).  It is called before the
  command runs, even when nothing of its was given.  A **nonzero
  int return halts dispatch** and becomes the program's result
  (v1's early-exit-code contract, uniform across every command on
  the line since cycling).
* A global command **with no subcommands owns the whole line**--
  that's how you spell a program without subcommands.  Full
  grammar, options anywhere.
* Global operands are the full grammar--optional operands,
  `*args`, converter groups.  **The command word is the first
  operand that names a command, once the global's minimum operand
  count is satisfied**; a global at its maximum forces the next
  operand to be the word regardless of name.  (v1's greedy ate the
  word--`run x` became `project='run'`, then "unknown command x";
  a footgun, deliberately fixed.  The price: an *optional* global
  operand can't positionally hold a value that names a command;
  make it required, or pass it through an option.)
* The global command and each command are **separate option
  scopes**: a global option after the command word is unknown
  there (matches v1), and same-named options in different commands
  never collide.
* An empty command line with no default command is orientation,
  not a diagnostic (ruled 2026-07-09, git-style): the command
  listing prints to stdout, exit 1, nothing runs.  A *dangling
  nested parent* (`prog db` with no subcommand after it) is a
  half-typed line and stays a loud `no command specified.` error.
  `unknown command 'x'` errors carry a usage listing the commands.
* Standalone: one script embeds every command's parser, the global
  command's, and the dispatcher; converters shared between commands
  are rendered once.

## Arity and grouping (the counting automaton)

Every rule has `(min, max)` arity, folded bottom-up:

* terminal slot: `(1, 1)`
* rule slot: sum of its slots' arities
* optional slot: contributes `0` to min
* repeated slot (`*args`): max becomes infinite

From the linear order of terminal slots, group boundaries are computed:

* An optional that sits **before** a required sibling *in the same
  linear run* is promoted to required (you can't skip the middle of
  a call).  Note this can only arise through converter recursion--
  Python syntax already forbids a defaulted positional before a
  required one in a single signature.
* A required leaf operand that **follows an absorbing converter**
  (one with its own `*args`) is a **trailing operand**: it **reserves**
  its count from the end.  In `cp(src, dst)` with `src` an absorbing
  group (`def src(*words)`), `dst` always gets the last operand and
  `src` gets the rest.  Reservation is not promotion: the absorbing
  group can be empty, so two operands mean `src=()` and `dst`, filled
  from the end.  (The keyword-only-no-default spelling for this is
  gone; use a converter group.)
* The set of **valid operand counts** falls out of the fold, and the
  wrong-count error message is that set phrased as English.
* **Distribution rule (fill left to right):** slots fill strictly
  left to right, each taking what it needs while operands remain.  A
  slot is never skipped to reach a later one: in `f(a='A', p:
  pair='P')`, one operand fills `a`; two operands fill `a` and then
  starve `pair`, which is a usage error (fix the command line, not
  the rule); three fill both.  There is no lookahead and no
  completability search--adding an operand never *un-fills* an
  earlier parameter, so a user can predict every fill by reading
  the signature.  The one non-greedy move is the trailing
  reservation above: required operands to the right of an optional
  are pocketed from the end first, and the front fills greedily
  from what remains.  (Ruled 2026-08-20, confirmed 2026-09-07;
  the earlier "leftmost-maximal-completable" design is retired.)

## Laziness and late binding

Decoration records; use executes.  `@app.command()` and
`@app.option(...)` do nothing but take notes; the plan is built,
analyzed, and compiled at first *use* (`process`, `main`, or
reading `app.plan`).  Consequences, all deliberate
and all tested:

* Configuration errors surface at first use, never at decoration
  time (importing a module full of commands can't blow up).
* Decorator order doesn't matter: `@app.option` above or below
  `@app.command()`, either works, because nothing is inspected
  until both have run.
* Adding an `@app.option` (or any future decorator) invalidates the
  cached plan and compiled parser; the next use rebuilds.
* Laziness is **per command**: dispatching (or examining) one
  command never builds the others, and each compiles once, at its
  first dispatch.  A config error in command B surfaces when B is
  first used.  The one deliberate, eager exception is `.plans`,
  which asks for everything.

The build must preserve this: no stage may require work at
decoration or import time.  Signatures are inspected when the plan
is built, and the backend binds converter *objects* when it compiles
the plan into converter classes--both as late as a use-driven design
allows.

## Errors

`AppealError` is the umbrella (v1's `AppealBaseException`, kept
as an alias): `AppealConfigurationError` and `AppealDataError`
(and so `AppealUsageError`) derive from it.  Its own job: raised
from a command, `main()` prints the message and exits 1
(configuration errors re-raise--bugs stay tracebacks).

Errors print to **standard error** by default (the POSIX
diagnostic convention: a filter's stdout stays clean, so a
pipeline never receives an error message as data).  `errors=`
takes any writable file object--`Appeal(errors=sys.stdout)` is
v1's behavior--resolved at error time like `print(file=None)`.
Requested help prints to stdout regardless.  A usage error exits with status 2, any
other Appeal error with 1 (v1 exited 255).

Three sentences (hierarchy ruled 2026-07-09).  Everything wrong
with the *data handed to the program*--a config mapping, a mapping
or CSV row being read, the command line--is an `AppealDataError`.
The command line specifically raises its subclass `UsageError`
(v1's name, kept): message plus contextual usage.  Everything
wrong with the *program* (a bad signature, an unrenderable
converter) is an `AppealConfigurationError`,
raised at build time, naming the offender.  A converter raising
`ValueError`/`TypeError` on a user's operand becomes a
`UsageError` naming the parameter and the offending text; the
polite `main()` handling catches the whole data family.

## Help

Usage lines render an operand as its **parameter name, bracketed and
upper-cased**: `[-t|--times <TIMES>]`, `<WIDTH>`.  That's the default
`argument_decoration` presentation transform (`<{name.upper()}>`);
an explicit `@app.parameter(usage=...)` rename replaces it, literally.
It decorates positional operands and option operands (opargs) alike.
Usage lines wrap at whole units--a bracket group never splits across
lines--with continuations aligned under the first.

`-h` and `--help` exist automatically (v1, probed): they print the
usage line, then the command's docstring rendered to the margin at
*run time* through big's markdown + stylesheet pipeline (imported
lazily, so the success path never pays for it; indented paragraphs
pass through intact).  Help wins
even when required operands are missing, exits successfully, and
never appears in the usage line.  The user's options always win
the strings: claim `-h` and help keeps only `--help`; claim
`--help` and there is no automatic help at all.  The constructor's
`help=False` is the blanket off switch: no automatic `-h`/`--help`
and (for a program with commands) no automatic `help` command--the
program answers only what it declares itself.

**Parameter documentation**: a top-level docstring line shaped
`name: description` (indented lines continue it) documents the
parameter of that name--anywhere in the command's tree, converter
parameters included.  Entries render as `arguments:` and
`options:` tables (labels from the plan: `-t|--times times`,
usage names for operands), descriptions wrapped into the column
by the trio.  A name matching no parameter stays prose
(`Note: remember this` is safe).  The structure is parsed at
build time; the formatting happens at run time.

**Command sets**: the command listing shows each command with the
first line of its docstring, plus the automatic `help` command
(v1's shape): `help` prints the listing, `help CMD` is exactly
`CMD --help`, and `tool --help`/`-h` as the first word prints the
listing too.  A user-defined `help` command wins; the automatic
one vanishes entirely.

**Completion**: `app.complete(words, prefix)` (and the
`complete`/`complete_set` functions) answer "what could legally
come next?"--option strings when the partial word starts with
`-` (minus used single-occurrence options; nothing in a value
position or after `--`), command words at the command position
(including cycling boundaries), help topics after `help`.  An
empty list means "no opinion" (the shell's business); a converter
that carries a `.completions` supplies candidates for its own
operand.  Shell integration scripts (`app.completion(shell)`, for
bash/zsh/fish) build on this.

## The converter vocabulary

v1's library converters, all semantics probed against 0.6.4:
`split(*separators, strip=False)` (a string into a list),
`validate(*values, type=None)` (an allowlist; rejects with "must
be one of ..."), `validate_range(start, stop=None, *, type=None,
clamp=False)` (one argument means 0..start; clamp pins instead of
erroring), `counter(delta=1, clamp=None)` (a repeatable flag
that starts at the parameter's default and adds `delta` per
occurrence--`delta` needn't be numeric; `clamp` is a barrier the
value stops on, approached from either side), and the annotatable classes
`accumulator` / `mapping` (subscriptable: `accumulator[int, str]`
collects tuples, `mapping[int, str, float]` maps keys to tuples;
they're just `MultiOption` subclasses, so the fold machinery does
all the work).  A bare factory as an annotation
is a config error ("call it first, e.g. split(':')").  Converter
`ValueError` text now flows into usage errors ("invalid value for
'direction': 'up' (must be one of 'north', 'south')").

## Nested subcommands and the default command

`app.command('db')` returns the child Appeal instance for `db`
(the command tree is a tree of Appeal instances--v1's model,
restored 2026-07-18); `.command()` on it attaches subcommands:
the parent runs first, its options come before the subcommand
word, and a parent invoked alone is a usage error--unless the
node has a `.default_command()`, which then runs--implemented as
a nested command set with the parent as its global command, so
it's the same machinery one level down.  `@app.command('x')`
also RENAMES: the word is `'x'`, the decorated function's name
is ignored.

## Class-based commands (§8.6, July 2026 rulings)

A class can be the app: `@app.global_command()` on the class makes
its `__init__` the global command (execution calls **the class**,
so `__new__` works the ordinary Python way), and the
`@app.command()`-decorated functions in its body are the program's
commands--**nothing is automatic**, decorate a method to expose
it.  At execution the global command constructs the instance into
the run's environment; method commands read their `self` from it.
`@app.command()` on a *nested* class makes a subcommand tree: the
nested class constructs via attribute access on the parent
instance--which is why a wrapped nested class (big's
`BoundInnerClass`) composes without Appeal knowing it exists; its
grammar is whatever the parent's attribute accepts (the descriptor
protocol answers at build time).  A methodless class command is a
leaf that constructs (the class-as-namespace pattern).  The
constructed instance is diverted into the environment, never
mistaken for an exit status (the early-exit contract only reads
ints), and every instance lands in the run's `instances` log:
`(None, instance)` for the global class, `(the class, instance)`
for class commands.  A registered command whose first parameter is
`self` with no claiming class refuses by name ("did you forget to
decorate the class?").  v1's `app.app_class()` two-registrar API
retires; its "preparer" API retires with it (the environment does
that job internally; a pre-made instance is spelled with ordinary
bound methods).  Standalone: the class imports, the script reaches
methods by attribute (`MyApp.fgrep`) and constructs at dispatch.

## Two stages, and cycling (July 2026 rulings)

**Three passes** (Larry's rule, 2026-09-07).  *Parcel:* the whole
command line is matched up first--each token to the option, oparg,
argument or command word it is--with **zero user code**: leaves are
recorded as text with their converters, folds as occurrences,
zero-argument converters as deferred calls.  A dead end (a word where
a command should be but isn't one, an option nobody owns) ends the
parcel: it's noted, not raised yet.  *Scan:* the eras marked
`immediate` (Appeal's own `-h`/`--help`/`--version` precommand) that
parceled cleanly execute now--they outrank whatever is wrong later on
the line--then the noted problem is raised.  *Execute:* commands run
left to right, each converting its records in token order and then
calling its callable (user code runs at the command's own position,
so a later command's converter may depend on an earlier command's
effects).  A *conversion* failure is therefore pass 3: commands to its
left have already run, like `make` stopping mid-build.  A nonzero int
return halts dispatch and becomes the result (v1's early-exit
contract, uniform across every command on the line).
`app.process(args)` parses and runs, returning the `Processor` for
that run (there is no separate public parse-only call).  The Processor's `instances` list
is the execution log, one `(command, instance)` pair per command
run, in order (the global command logs `(None, ...)`; instances
arrive with class-based commands).

**Cycling** (`repeat`): when a command's arguments are satisfied--
all of them, optional included, greed being greedy--the next
non-option token is another command word, and the line starts
over.  The boundary is deterministic: before saturation every
token is an argument; at saturation the next non-option token is
a word *whatever it looks like*; a command with unbounded
arguments (`*args`) never saturates and so ends the cycle.
Post-saturation dash-tokens still bind to the finished command
(its option window stays open until the next command word).
`repeat` is **per parent**: `Appeal(repeat=True)` for the root
set, `app.command('db', repeat=True)` for a parent's set.
Resolution of the word after `a b c` (each nested under the
last): c's own subcommands first--consulting a set for its FIRST
command is free, that's descent--then each ancestor's set gated
on that ancestor's own `repeat`, root last; a set without repeat
doesn't block its ancestors.  First match wins, and the chain
re-bases at the resolved command.  (v1 had a `repeat=` flag; its
loop was broken in 0.6.4--child sets ran a leftover check before
returning to the loop--undocumented and untested.  This is the
rebuilt version, from Larry's spec.)
`@app.default_command()` handles an empty command line; without
one, the listing prints to stdout and the exit status is 1.
`app.processor()` is a v1 compat shim.
v1's exception names (`AppealUsageError`, `ConfigurationError`)
are accepted, and `Appeal(version=..., margin=..., indent=...)`
wires the help formatter: version drives `--version` and the
`version` command; `margin` caps the help width (min'd with the
terminal width at render time, v1's rule; default 79, v1's was
80); `indent` re-indents the section templates (default 4,
matching the templates themselves).  The program name comes
from `Appeal(name=...)`, else the basename of `script=`
(default `sys.argv[0]`, captured once at import--the sole place
Appeal reads `sys.argv[0]`).

## The v1 corpus ratchet

`tests/test_corpus.py` runs the whole v1 corpus (tests/test_v1.py,
270 tests) against v2 with an explicit expected-failure list
(currently 100): a passing test regressing fails the runner, and
an expected failure starting to pass also fails it--progress must
be recorded by deleting the name.  Keep the tests, not the code.

## The JSON schema

`schema(callable)` (and `app.schema()`) describe a command as
plain JSON-safe data: usage line, operands (groups recursing),
options with their strings/kinds/operand types, arities, and the
`name: description` docstring entries--the machine-readable twin
of `--help`, for MCP tool definitions and similar consumers.  The
round trip: `schema()` tells a machine what a command accepts;
`read_mapping(callable, json.loads(blob))` runs the command from
the JSON object the machine sends back.  In-process API.

## Reading mappings and iterables

`read_mapping(callable, mapping)` and `read_iterable(callable,
iterable)` point the same metaphor at config files: parameters pull
values by name from a mapping (or by position from a sequence),
converters always apply (already-typed values included, matching
v1), groups recurse--a group reads a sub-mapping under its
parameter's name, or (v1's other spelling) flat keys at the same
level; a sequence-shaped group reads a nested sequence.  Defaults
fill absent keys (v1 0.6.4 demanded every key: a bug, fixed);
unrecognized keys raise by name, judged against the whole tree
(strict=True, ruled 2026-08-29--a deliberate v1 divergence: v1
ignored extras, which survives as strict=False); missing required
keys and bad values are `AppealDataError`s carrying the path
(`at s.port`).  Booleans
parse strictly: real bools, `true/false/yes/no/on/off/1/0`--never
truthiness (`'false'` must not mean True; v1 crashed on flags
outright).  `*args` reads a sequence (v1 refused); `list[T]`,
`dict[K, V]`, tuple slots, and multi-operand converters read the
obvious shapes.  `Option`/`MultiOption` classes read naturally: arity-1
occurrences are scalars, arity-k are k-sequences, arity-0 reads a
bool (`Option`) or a count (`MultiOption`); `MultiOption` values
are sequences of occurrences.  Options keep their defaults under `read_iterable`--a
sequence has no names.  `read_iterable` is **row-oriented** (v1's
corpus): one call per row, results listed, empty rows skipped;
keyword-only parameters and `**kwargs` can't be position-fed and
are configuration errors.  `read_csv(fn, reader, first_row_map=
None)` consumes the heading row: without the map, rows feed
positionally; with it, headings map to parameter names and rows
feed read_mapping-style.  `app.unnested()` is a v1 compat no-op
(v2 reads both the nested and flat spellings).  In-process API only: not part of the command-line grammar.

Because a mapping is a tree, sibling branches may each have a
parameter named `x`--address them with the nested spelling.  The
flat spelling reads the current level and is for shallow configs.

## Precommands

`@app.precommand()` is repeatable: each registration is another
precommand, invoked front-to-back (registration order; `index=`
places one explicitly) before any command.  By default they all parse
as ONE era at the head of the line: every precommand's options are
recognized together, so `foo -q --version` works no matter which
precommand maps which string, in either spelling order.  One era
means one owner per string: two precommands that WROTE the same
option name (a long from a parameter, or an explicit @app.option
claim) are a build error naming both; an auto-proposed short letter
simply yields to the first claimant--the same first-declared-wins
rule the letters follow inside one plan.  Each precommand receives
the values the shared parse bound to it; operands fill in
registration order.

**The era flags** (Larry's design, 2026-09-07), keywords of
`@app.precommand()`:

* `boundary=True`: this precommand **ends its era**; the next
  precommand starts a new one.  A boundary begins a new era even
  when no later precommand registers in it--that empty era is where
  a bleed lands and stops.
* `bleed=True`: the era's options **stay mapped into the next era**,
  still bound to their own precommand.  They're thrown away at the
  end of THAT era unless it bleeds too--one hop per flag.  A later
  era's own option wins a collision with a bled one.
* `immediate=True`: the era **executes during the scan**, as soon as
  it parcels clean--before anything later on the line is judged.
  Only the leading eras may be immediate (an immediate era after a
  waiting one is a build error), and every precommand sharing an era
  must agree.  A nonzero int from an immediate era halts the line.

Appeal's own metadata precommand (`-h`/`--help`/`--version`) declares
all three: it is the first era alone, its options reach the user
precommand era (`foo -q --version`) and no further, and it runs
first--so `foo -h` prints help even when the program's required
operands are missing, and `foo --version garbage` prints the
version.  Nothing is special-cased: those are the flags, and a user
precommand may claim them (`--dump-config`, say).

**Command eras.**  Each command word is an era of its own (one
required argument, the word; no options), followed by the command's
options-arguments-opargs era when it takes anything.  A command era
relays bled options into its own o-a-o era; a command that takes
nothing stops the bleed.  `@app.command(bleed=True)` (and
`subcommand(..., bleed=True)`) keeps the command's options mapped
one command further: on the o-a-o era when there is one, else on the
command era itself, so the relay continues through it.

**Scanning an era**, token by token (the rule, in Larry's words): if
an oparg is owed--optional opargs included--the token is it,
unconditionally (`foo -h --version` asks for help on `--version`).
`--` is remembered and discarded, and it is **line-wide**: no later
token on the line is an option, in this era or any other, though
command words still dispatch (docopt's rule; argparse and click scope
it per level, git and clap kill later commands too--ruled
2026-09-07).  Otherwise a dash token is an option: unknown, with
nothing owed, it **ends the era** and is retried in the next; unknown
with an argument still owed, it's an error; a short cluster mixing
known and unknown letters is an error; known, it's consumed.  Any
other token is an argument: it fills the next slot--optional slots
are owed too, so an era ends only at saturation--or, saturated, ends
the era and is retried.

## Config layering

A dict **bound to a precommand** with `@app.precommand(config=...)`
supplies that precommand's options: config holds program-wide
settings; the command line names the work.  You bind the dict at
registration and fill it before `main()` (Appeal holds the same
object), and each precommand that wants config binds its own--there is
no single global slot, and no `config=` on `process`/`main`.  Merge
your layers into the one dict yourself.  Precedence is fixed and unknobbed:
defaults < config < args, **atomic per option** (an option the
args mention wins whole; repeatable kinds REPLACE, never append--the
command line can always subtract).  Keys are **full strict**
("either this is ours, or it isn't"): every key must name one of
that precommand's options; a command name, a positional argument
(anyone's), or an unknown key is a loud `AppealDataError` saying
which it is (a config file is data of unknown provenance--not the
command line, so not "usage").  Values convert in **stage 2 through the ordinary argv
pipeline** with `config:` provenance on failures; flags use the
strict boolean spellings (never truthiness), and a false flag
means *absent*--the default fills, and a `**kwargs` option stays
an absent key.  Repeatables take a sequence (one entry per
occurrence), mappings a mapping, group options a sequence of
their arguments or a by-name sub-mapping.  The mapping is read
live (mutating it mid-run is the caller's problem, per-run per
Processor--the app-server case).  Config for *scoped* options
is refused **by design** (ruled 2026-07-09): position is the
essence of a scoped option, and a mapping has no position--the
two transports don't compose.  The refusal names the workaround
(set it on the command line, or give the uses distinct parameter
names via `@app.option`).

**Deferred feature -- scoped-option config addressing (post-1.0).**
If you need to set a *scoped* option (one string declared by
several windows--e.g. a converter reused across sibling slots) from
config, 1.0 cannot do it; use the workaround above.  The designed
(ruled) shape for when we build it is nested addressing through the
window's parameter name: for `mg(a, b: child, c: child)` with
`child(p, *, flavor='')`, `{'b': {'flavor': ...}}` targets flavor
in b's window.  This is *not* the same, cheap change as a group
*option*'s by-name sub-mapping (which Med 5 already delivers,
because a group option is one rule): a scoped option is one rule
bound to a window **by position at scan time**, and config injects
flat, by key, at **execute** time--so delivery must reach into the
scoped-binding machinery to place the value in the named window's
own given (or synthesize a positioned occurrence pre-binding).  Two
semantics settled in advance: (1) the named window must actually be
*entered* by argv first--config does no structural rescue, same as
`--deep` without `--where`; (2) inside `{'b': {...}}` only b's
window's OPTIONS are addressable, never its operands (config
supplies options, never positionals).  Forward-compatible: 1.0
keeps `{positional-group-slot: {...}}` refused (rejected as a
positional), so the shape stays reserved; adding support later only
turns that error into acceptance--monotonic, breaking no existing
program and changing nothing for anyone who doesn't write the new
shape.  (Entry point for a future fix: `_config_vet` /
`_config_apply` in `appeal/__init__.py`, `plan.scoped_keys`, and the
backend's scoped-window binding.)

With class-as-app this is the
argparse-replacement story: the config file helps construct your
application object, args picks the methods.

## MCP servers (July 2026 rulings)

MCP (the Model Context Protocol) is the JSON-RPC-over-stdio
protocol AI agents use to call tools.  `app.mcp()` serves this
program's commands as MCP tools.  Each command is one
tool: the docstring summary is its description, `schema()`
translated to JSON Schema is its input schema (keys are
**parameter names**, never option strings), and a `tools/call`
runs `read_mapping(command, arguments)`--the mapping metaphor
pointed at a wire.  Conversions always apply; converter and
usage failures come back as polite `isError` results, not
crashes.

There are no subcommands in MCP: a tool name is one flat string.
Command names are content-agnostic (`name='db add'`--spaces and
slashes are fine, names are never validated or mangled), so a
program with nested subcommands flattens them explicitly;
`mcp()` with nested subcommands registered refuses and says so.

A class-based program (§8.6) constructs its instance **once, at
server startup**--not per call: `mcp(config=...)` feeds
`__init__` under the config-layering rules (strict keys,
global-command options only), and every method tool dispatches
bound to that one instance, so state persists across calls.  A
required `__init__` positional has no coverage (config supplies
only options) and refuses at startup, naming the parameter.

`mcp()` runs the REAL machinery in process--the plans, the builder,
and the read driver--so there is one copy of the rules and nothing to
drift.  `Option` subclasses are recognized structurally
(`is_option`/`is_multioption`), not by identity, so a converter still
reads correctly even if the user's module and appeal disagree about
class identity.

## Implementation status

Appeal is a single interpreter now--no code generator, no emitted standalone
scripts.  A signature compiles once to a `Plan`, and the backend walks `argv`
against it.  Every construct in this document is implemented and tested; a row
is never called done without tests, and `test_corpus` ratchets the whole
matrix.  The grammar is validated at build time (unreachable options, the
same-world rule, arity), so an ill-formed grammar is a `ConfigurationError` at
first use rather than a surprise at parse time.

One standing **refusal by design**: long-option *abbreviation* (`--verb` for
`--verbose`).  Ruled out 2026-07-09 as script-fragile; completion covers the
comfort it would have bought.

The compatibility contract with the 0.6 line is **executed, not asserted**:
the differential-fuzz tests run random shared-grammar programs and command
lines through the shipping 0.6 (extracted from git, in a subprocess) and
through 1.0, and require identical calls wherever 0.6 succeeds.  The 0.6
errors that 1.0 accepts are the two documented supersets below.

## v1 → v2 semantic changes (deliberate)

* **Distribution**: v1 is greedy (an optional group grabs the next
  operand whenever one exists, then may fail); v2 is
  leftmost-maximal-*completable*.  Strict superset: every command
  line v1 parses successfully, v2 parses identically; some v1
  errors (`f(a='A', p: pair='P')` with two operands) become the
  only-possible reading.
* **Option placement**: v1 recognizes a converter's options only
  after the parse reaches the converter's position; v2 (until the
  streaming driver) recognizes them anywhere on the line.  Again a
  strict superset.  Option-forces-group-entry is v1 semantics,
  kept--v2's first cut wrongly errored there, and the differential
  fuzz against installed v1 caught it.
* **Trailing operands come from converter groups**: a required leaf
  after an absorbing group reserves from the end.  (The
  keyword-only-no-default spelling once meant this too, but that
  left the grammar 2026-08-29--back to the 0.6.4 rule that
  keyword-only params are options and must have a default.)
* **Converter-depth grammar** (July 2026, task #10): a `*args`
  converter is an *absorbing* nonterminal--it takes the most the
  slots after it can spare (v1, probed: `pair(a, *rest)` fed the
  whole line; two earlier v2 comments claiming v1 read these as
  one-operand terminals were mis-pins).  v1's "can never be
  satisfied" shape--a required argument after the absorber--is
  satisfiable under completable distribution: the automaton
  reserves suffix minimums (the superset pattern again).
  A trailing operand (a required leaf after an absorbing group)
  follows the **uniform end-reservation rule**: it reserves from the
  end of the command's stream wherever it sits in the tree; counts
  sum tree-wide, tokens fill in traversal order (= stream order: an
  inner converter's trailing consumes before its outer's).  Still
  refused by name: windowed groups on `*args` inside a converter.
* **Option operands went v1-greedy** (July 2026, un-deferred from
  the streaming-driver exile): optional opargs consume
  unconditionally to the converter's maximum.  Before this fix v2
  silently never consumed them--`make -j 5` parsed differently
  than v1, violating this section's superset claim--and v2's short
  forms carried a mis-pin: the first cut read `-me` as the bundle
  m-then-e, citing v1; probing showed v1 reads `m('e')` (concat
  wins over any bundle reading).  Both corrected; the superset
  claim holds again.
* **The compatibility contract is executed, not asserted**:
  `test_differential_fuzz_against_installed_v1` runs random
  shared-grammar programs and command lines through the shipping
  v1 in site-packages and through v2, requiring identical calls
  wherever v1 succeeds.  (v1 errors that v2 parses are the two
  documented supersets at work.)
