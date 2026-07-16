# A guided tour of the Appeal 1.0 implementation

*For Larry, who looked at it on the plane and asked "where is the
engine?"  Slowly, with a lot of context.  Refreshed 2026-07-09,
after the conventions audit; every count and claim below was
checked against the tree that day.*

> **A note on names.**  This release is **Appeal 1.0**; the previous
> releases were the 0.6 line.  In the engineering docs (this one, the
> tour, the proposal) the rewrite is nicknamed **v2** and shipping
> Appeal 0.6.x is **v1**--the codenames the rewrite was carried out
> under.  Wherever this document says "v1 semantics" or "probed
> against v1," read "the 0.6 line."


## Where the engine went

The reason you can't find the engine is that there isn't one--not
in the v1 sense.  v1's engine was the Charm interpreter: a virtual
machine with opcodes, program counters, processors, and compilers
targeting it.  At runtime, v1 *interpreted* your grammar, every
parse, using ~4,500 lines of machinery that existed whether or not
any particular feature was in play.

v2 is organized around one deliberate inversion: **everything that
can be decided before the parse, is.**  What's left at runtime is
so small it doesn't feel like an engine, because it isn't one.
The architecture is:

    one passive data structure  +  six consumers that walk it

The data structure is the **Plan tree** (plan.py).  The consumers:

    interpreter.py   parses argv by interpreting the tree   (rung 1)
    codegen.py       parses argv by GENERATING a parser     (rung 3)
    help.py          renders --help (and man pages) from the tree
    read.py          reads config mappings/sequences by the tree
    schema.py        describes the tree as JSON
    complete.py      answers "what could come next?" (tables for
                     the completion engine in runtime.py)

Above them sits **__init__.py**: the `Appeal` facade (decorators
in, laziness everywhere) and the `Processor` (one command-line
run's state).  Below them sits **runtime.py**: everything a
parser needs at run time.  Both get their own sections below;
the facade in particular grew real machinery this cycle.

Four terms of art appear throughout this document and the code;
here they are, before anything uses them:

* **Rungs** are implementation strategies for running the plan
  tree, ordered like a ladder by how much work happens before the
  parse (proposal §7.1).  **Rung 1**: interpret the tree directly
  --simple, slow, debuggable.  **Rung 2**: compose closures--
  skipped on purpose (neither simple nor fast).  **Rung 3**: emit
  Python source and exec it--the production parser.  Appeal builds
  rungs 1 and 3 from the same tree and tests them against each
  other forever; when you see "both rungs," that's what it means.
* **The north star** is the design rule this whole rewrite serves:
  every command must be emittable as a *standalone* Python script
  --stdlib plus your own module, no appeal installed.  Its teeth:
  no feature may work in-process but silently fail to emit; it
  must either work standalone or refuse by name.
* **Scissors** is the marked-region idiom for *borrowing*:
  `# --8<-- start NAME --8<--` ... `# --8<-- end NAME --8<--`
  brackets a self-contained run of lines (a "snippet") that
  another project copies verbatim and re-syncs.  Snippets are
  named for what they provide, may declare `requires` on other
  snippets (extraction is transitive), and MUST NOT NEST (a
  lesson this tree has now learned three separate times).  The
  machinery lives in big: `extract_snippets` and friends, plus
  `python -m big.snip`; tools/sync_snippets.py re-syncs Appeal's
  borrowed copies.  Appeal borrows five snippets from
  big/text.py: the word-wrap trio, its two helpers (`big
  linebreaks`, `big _iterate_over_bytes`), the toy multisplit
  (big 0.14's keep=True pairs form), and
  `format_definition_list` (which lays out every help table).
* **The trio** is big's word-wrap trio--`wrap_words`,
  `split_text_with_code`, `merge_columns`--carried (with its
  sibling snippets) inside runtime.py itself, so it travels
  wherever the runtime does.

And one recurring *problem* worth naming up front, because three
different mechanisms in the tree exist to solve it:

* **The two-copies problem.**  A standalone script embeds
  Appeal's code--but the *user's module*, which the script
  imports, may itself `import appeal` (it must, to subclass
  `Option` or raise `AppealError`).  Now the process holds TWO
  copies of Appeal's classes, and `isinstance`/`except` checks
  against the script's copy can't see the installed copy's
  instances.  The cure, each time: recognize by *name and home*
  (a class named X whose `__module__` ends with
  `appeal.runtime`).  You'll meet it as `is_option` /
  `is_strict_option` (build and read recognizing option
  classes), and `foreign_appeal_error` (run_main recognizing
  raised exceptions).

If you want to point at The Engine, point at two places:

1. `build._analyze` -- ~50 lines that compute the counting
   automaton's tables, and
2. `interpreter._fill` -- ~80 lines that execute them.

Everything else is bookkeeping.  That's the whole secret.


## The one mental key: the counting automaton

All the cleverness in Appeal's grammar--optional groups, nested
converters, "which operand goes where"--reduces to one question:
**given n operands, how do the slots split them?**

v1 answered it greedily at parse time and sometimes painted itself
into a corner.  v2 answers it *at build time*, by computing two
tables for every slot:

* `count_options` -- the operand counts this slot can consume,
  sorted descending.  A required terminal: `(1,)`.  An optional terminal:
  `(1, 0)`.  An optional group whose converter takes two required
  operands: `(2, 0)`.
* `suffix_after` -- the set of totals the slots *after* this one
  can still consume, computed right to left.

Then the parse rule is one sentence: **walk the slots left to
right; at each slot, take the largest count in `count_options`
such that what remains is in `suffix_after`.**  No backtracking,
no ambiguity, no lookahead beyond a set-membership test.

Worked example, the canonical one:

    def pair(x, y): ...
    def f(a='A', p: pair='P'): ...

Analysis, right to left:
    p:  count_options = (2, 0)      suffix_after = {0}
    a:  count_options = (1, 0)      suffix_after = {0, 2}
    whole plan: valid counts = {0, 1, 2, 3}

Parse `f x y` (n=2):
    slot a: try 1 -> remaining 1, is 1 in {0,2}?  NO.
            try 0 -> remaining 2, is 2 in {0,2}?  YES.  a skipped.
    slot p: try 2 -> remaining 0 in {0}?  YES.  p = pair('x','y').

That's the engine, in its entirety, doing the thing v1's greedy
distributor got wrong.  Everything else we've ever debated--
gates, windows, force-entry, announce-first--is a small rider on
this loop.


## File by file

Read them in this order; each builds on the last.

### plan.py (~350 lines) -- the noun

Four record classes, deliberately passive:

* `Terminal(converter)` -- a slot that eats one string.
* `Slot` -- one positional parameter: name, child (Terminal or Plan),
  required/repeat/trailing flags, default, the two automaton
  tables above, and `barrier` (the gate rule).
* `OptionRule` -- one option: strings, kind, converters, default.
  The kinds: `flag`, `value`, `nullary` (a zero-arg converter:
  presence IS the value, so it refuses `=`--its own table kind
  since the `=true`/`=false` work), `accumulate`/`mapping`
  (list[T]/dict[K,V]), `fold`/`fold1` (Option/StrictOption
  classes), `group` (a converter with its own grammar).
  `table_entry()` says how parse_tokens should consume it.
* `Plan` -- one callable: slots, options, arity--plus the keys
  the class-as-app machinery reads (`constructs`: this command
  builds an instance, stash it under this key; `binds`: this
  command reads the instance under this key--a method's self),
  `tree_trailing` (trailing operands anywhere in the subtree
  reserve from the END of the stream), and `scoped_keys` (option
  strings declared by several windows--position decides).
  `usage()` reads the tree back out as the usage line,
  announce-first: inside every bracket, options render before
  operands, because that's the order you may type them.

Nothing in this file ever runs during a parse.  Nothing in this
file ever mutates after build.  If you're wondering "what IS a
command, to Appeal?"--it's an instance of Plan, and you can just
print one.

### build.py (~1,200 lines) -- signature in, Plan out

The compiler front-end.  `build(callable)` does:

1. `inspect.signature`, walk the parameters.
2. Positional params -> Slots.  `_child_for` decides Terminal vs
   recursion: a callable annotation that isn't a blessed terminal
   (str/int/float/bool) becomes a child Plan--the heart of the
   metaphor.  tuple[...] annotations, Option classes, vocabulary
   products--each has a short branch here.
3. Keyword-only params with defaults -> OptionRules, via
   `_build_option_rule`, the one function holding ALL option-kind
   inference (it also serves @app.option declarations and
   **kwargs options).  Keyword-only params *without* defaults ->
   required trailing operands.
4. `_analyze` -- the automaton tables (see above).
5. Top-level only: `_finalize_options` (collision check, auto
   short options, and the scoped-keys discovery--the same string
   declared by several windows is legal when the grammar
   matches) and `_mark_barriers` (the gate rule).

Classes build too: a class's plan is its `__init__`'s grammar
plus `constructs`; a method's plan skips self and carries
`binds`.  Option-class detection goes through `is_option` /
`is_strict_option` (two-copies aware), and `is_strict_option`
decides fold (repeatable) vs fold1 (at most once).

If a grammar question starts "when I write THIS signature, what
does Appeal think it means?"--the answer is in build.py, and
usually in `_child_for` or `_build_option_rule`.

### runtime.py (~3,700 lines, the snippet warehouse) -- the plumbing

The only appeal code that exists at parse time in a standalone
script--the emitter plucks the snippets the parser needs and
nothing else (23 appeal snippets plus big's five, at this
writing).  Grammar-free by design: it never knows what your
command looks like; the generated parser hands it tables.  An
inventory, in snippet order:

* **exceptions**: the hierarchy--`AppealError` is the umbrella
  (v1's AppealBaseException role; alias kept), with
  `AppealConfigurationError` (author bugs; main() lets them
  raise) and `AppealDataError` (bad data, any provenance;
  carries usage) under it, and `AppealUsageError(AppealDataError)`
  for the command line specifically.  AppealError's own job:
  raise it from a command for a polite message + exit 1.  Also
  here: `did_you_mean` (difflib suggestions for unknown
  commands/long options) and `foreign_appeal_error` (the
  two-copies problem, exceptions edition).
* `parse_tokens` -- THE token splitter.  One pass over argv
  producing `(operands, given)`.  All token-level syntax lives
  here and only here; the current rules are worth restating
  because several changed in the conventions audit:
  - `--foo bar`, `--foo=bar`, `-f bar`, `-f=bar`; bundling
    `-vxz`; and getopt's attachment rule: an option taking
    exactly one value binds the REST of its token verbatim
    (`-fjoe` is `-f joe`, `-DX=1` is `-D 'X=1'`).
  - flags record BOOLEANS: bare presence records True, and the
    explicit spellings `--flag=true`/`--flag=false` (exactly
    those two) record what they say--which is how the command
    line overrules a config layer.
  - repetition is last-one-wins for flag/nullary/value/group
    kinds; `record()` REINSERTS the key so `given` stays in
    last-occurrence order (cross-spelling shared parameters
    resolve by that order).  fold1 (StrictOption) is the one
    kind that still errors "specified more than once".
  - greed: an optional oparg takes the next token whatever it
    looks like--EXCEPT `--`, which outranks greed once the
    minimum is met (a required oparg still takes `--` verbatim,
    the `grep -e --` idiom).  `--` otherwise ends option
    recognition for the rest of the parse; a lone `-` is always
    an operand; negative numbers are operands unless that exact
    short option exists.
  - the command-word split: in command mode, scanning stops at
    the first operand naming a command (or at the maximum), and
    the rest of argv returns unparsed.
* the collectors: `convert` (one terminal; ValueError/TypeError
  -> polite UsageError), `call_converter`, `fold` (the Option
  protocol driver), `accumulate`, `collect_mapping`.
* the **dispatcher**: `scan_command_set` / `run_command_set`--
  the two-stage heart, own section below.
* `window_options` -- bind a *args group's option occurrences to
  instances by operand position (announce-first; last-wins per
  instance).
* `ScopedQueue` and the `scoped_*` helpers -- the interval model
  for scoped options: windows open/close during the structural
  walk, occurrences resolve to windows offline (announce-first;
  boundary ties go FORWARD), values replay during the live walk.
  Three modes per key: 'multi' (collect), 'last' (overwrite),
  'strict' (fold1's error).
* `check_count` -- arity errors as English.
* `run_main` -- the whole-program driver, and the only place
  with opinions about the process: prints data errors (+usage)
  to the `errors` stream (default sys.stderr, any file object)
  and returns 2; lets configuration errors raise (bugs stay
  tracebacks); prints a user-raised AppealError and returns 1
  (foreign copies recognized); catches KeyboardInterrupt ->
  130, quietly, HERE AND NOWHERE ELSE; answers `--version` as
  the first token; answers completion reentries.  Appeal is an
  argument processor, not an environment--this function is the
  full extent of its worldly involvement.
* `run_mcp` -- newline-delimited JSON-RPC over stdio: the MCP
  server loop (initialize / tools list+call; data errors come
  back as isError results).
* the **completion engine**: `complete_command` /
  `complete_command_set` answer from tables (complete.py builds
  them, emission bakes them; nested set entries recurse to any
  depth), `completion_reentry` speaks the _APPEAL_COMPLETE
  protocol, `completion_script` prints the bash/zsh/fish
  couriers.
* the **theme**: `Theme` (seven slots, symbolic strings),
  `can_colorize` (CPython's env precedence, verbatim),
  `resolve_theme`, and the painters--layout first, paint second,
  so a colored page strips back to monochrome byte-for-byte.
* **help rendering**: `render_help_page` assembles a --help page
  from a predigested corpus through the templates (a plain
  dict; the app's `indent` knob works by re-indenting the
  section templates at construction).
  `help_margin(cap)` resolves the wrap margin at render time:
  min(terminal width, cap)--pipes get the cap, so captured
  output is stable.  `render_usage`/`usage_units` wrap the
  usage line at whole units.
* the **converter vocabulary**: `split`, `validate`,
  `validate_range`, `counter`, `accumulator`, `mapping`, and
  `file` (open() as a converter: `-` means stdin/stdout by
  mode, wrapped in `_ProcessStream`, whose close() flushes and
  goes inert--the process's streams outlive one command line).
  Factory products carry a recipe string (`__appeal_recipe__`);
  a standalone script re-runs the factory, so closures survive
  emission.
* the **Option family**: `Option` (repeatable: option() per
  occurrence--v1's MultiOption semantics; `MultiOption` is now
  an alias) and `StrictOption(Option)` (at most once, loudly--
  what v1 called Option; strictness is opt-in, by name).

### interpreter.py (~580 lines) -- rung 1, the reference

`parse(plan, argv)`:

1. Build the option table from the tree; call parse_tokens.
2. `check_count` against the plan's valid counts.
3. Peel trailing operands off the end (tree-wide: `tree_trailing`).
4. `_fill` -- the automaton loop from the mental-key section,
   recursing into child plans, with three riders: the *gate*
   check (options given too early), *force-entry* (an option of
   a skipped group conjures it, once per key per line), and
   *windows* (per-instance binding for *args groups).
5. `_option_kwargs` -- resolve options into kwargs
   (`_shared_winner` picks the spelling that spoke last).
6. Call your function.

`dispatch` mirrors the command-set machinery.  This is the
implementation to read and the one to pdb.  It is definitionally
correct: rung 3 is fuzzed against it forever.

### codegen.py (~1,600 lines) -- rung 3, the generator

The same logic as the interpreter, but instead of *doing* it, it
*writes it down* as Python source specialized to one plan.  The
automaton's decisions become `if remaining - 2 in (0, 2):` chains
over constant sets; each child plan becomes a named fill
function; every plan gets scan_X (stage 1, structural, no user
code) and run_X (stage 2, conversions and the call) plus fused
parse_X glue.  The best way to understand it is not to read the
emitter--read its OUTPUT:

    from appeal import build, compile_plan
    print(compile_plan(build(your_function)).source)

Two delivery modes from the same source:

* `compile_plan` / `compile_command_set` -- exec() it with the
  refs bound by reference (converters, defaults), register with
  linecache so tracebacks show generated lines.  This is what
  app.process() runs.
* `emit_standalone` / `emit_standalone_command_set` -- render
  every ref as text: imports for your callables, repr-literals
  for defaults, the three process streams by identity
  (`_out_default = sys.stdout`--re-evaluated at the SCRIPT's
  runtime), and *recipes* for vocabulary products.  Then pluck
  the needed snippets out of appeal/runtime.py (requires
  directives make it transitive).  The plucking is big.snip,
  imported at emission time--Appeal's one soft dependency:
  parsing in-process needs no big at all; the emitted script
  needs neither big nor appeal.  Anything unrenderable refuses
  BY NAME; that refusal is the north star's enforcement.

For a command set, the emitted dispatcher includes: a `_SET_x`
dict per parent (nested to any depth--children emit first, so a
parent's literal can reference its child sets), the baked usage
listings, `_print_listing` (the themed listing; also serves the
bare-invocation exit-1 path), the auto help and version
commands, the baked completion tables (recursive, same shape as
in-process), and a `run_main(...)` call carrying the theme spec,
the completion table, the `errors` stream (identity-rendered),
and the version string.

`emit_standalone_mcp` is a different beast entirely--see the MCP
section below.

`Refs` is the little registry of names the generated code expects;
`render_ref` is the standalone honesty check.  `_RESERVED` /
`_local` mangle user parameter names away from the emitter's own
locals (ask the corpus about the converter parameter named `i`).

### __init__.py (~1,300 lines) -- the facade and the Processor

The `Appeal` class records decorations and builds NOTHING until
first use (laziness is per command: a program with fifty
commands compiles the one the user invoked).  Worth knowing:

* `@app.command()` on a *class* triggers `_adopt_class` (§8.6):
  the methods its body decorated get reclaimed by identity; the
  class becomes the parent of its own little set (or, via
  `@app.global_command()`, the whole program).  `_method_owner`
  and `_class_parents` carry the bookkeeping; execution stashes
  instances in a per-run `env` dict keyed by qualname.
* `self._subs` is FLAT: every parent maps its own children, at
  any depth; `plan_for` finds a nested parent's plan by scanning
  it.  `_set_entry_for` builds the recursive set dicts the
  dispatcher's resolution stack walks.
* `_CompileOnDispatch` is the lazy command table handed to the
  dispatcher: `.get(word)` compiles that one command--and
  supplies the automatic `help` and `version` commands.
* Laziness is thread-safe the house way (ruled 2026-07-11):
  every cache site builds with NO lock held (compilation
  inspects user code, and foreign code never runs under a
  lock), then takes `self._lock`--a plain Lock--only to
  test-and-set the slot.  Racing first parses each compile;
  one wins; the rest adopt the winner and discard their work.
* **config layering** lives here: `_config_vet` (stage 1,
  strict keys: every key must name a global-command option;
  scoped options refused by design--position is their essence,
  and a mapping has none) and `_config_inject` (stage 2:
  synthesizes `given` entries so conversion rides the ordinary
  pipeline; argv wins whole, per option).
* the **Processor**: one run's state.  `parse(argv, config=)`
  is stage 1--scan the WHOLE line, nothing executes; a
  malformed line dies here (the Appeal rule: no work).
  `execute()` is stage 2--conversions and calls, left to
  right, a nonzero int halts.  `.instances` is the mechanical
  (command, instance) log.  app.parse/process/main are thin
  wrappers; main() adds run_main's politeness.
* the rest of the public surface: help(), schema(),
  documentation('man'), completion(shell)/complete(),
  repl(), mcp(config=)/standalone_mcp(), standalone()/
  write_standalone(), read_mapping/read_iterable/read_csv.

### The other consumers

* **help.py** -- the build-time half of help: `parse_docstring`
  (the `Arguments:`/`Options:`/`Commands:` grammar--the
  docstring is input, never output), `merge_docs` (entries
  layer across the converter tree, nearest scope wins),
  `command_set_corpus` (the dispatcher listing, auto help and
  version rows included), and `man_page` (the same corpus in
  troff clothing, for app.documentation('man')).  The output is
  a *corpus*: predigested rows, ready to bake into standalone
  scripts.  The run-time half--render_help_page and friends--
  lives in runtime.py.
* **read.py** -- read_mapping walks tree x dict (by name, groups
  recurse, nested and flat spellings); read_iterable walks tree
  x rows; Option classes read sequences of occurrences
  (StrictOption: one).  Interpreted only, no codegen--but since
  the MCP snippet route, read.py is also a STREAMING SOURCE:
  the whole module (markers after its import block) travels
  into standalone MCP servers.  Its imports are top-level and
  absolute for exactly that reason.
* **schema.py** -- tree -> JSON-safe dicts; `mcp_input_schema`
  turns a plan into a JSON Schema whose keys are PARAMETER
  NAMES (never option strings).
* **complete.py** -- builds the completion tables (plain data
  plus converter references) the runtime engine answers from;
  recursive for nested sets; `completions` attributes on
  converters supply value candidates.

(There used to be more files here--vocabulary.py, text.py.  Their
contents live in runtime.py now: everything a running parser needs
is one file, and that file is the standalone payload.)


## The dispatcher and the Processor: two stages, and cycling

This is the largest piece of architecture the original tour
predates, so slowly:

**Stage 1 (scan): the whole line parses before anything runs.**
`scan_command_set` walks argv: the global command's portion
first (its scan stops at the first operand naming a command,
once its minimum is met), then command after command.  Each
command contributes an *invocation*--(word, run, operands,
given, positions)--to a list.  Structural problems (unknown
command, wrong counts, gate violations, window binding) raise
HERE, before any user code runs: a malformed line does no work.

**Cycling** is the loop: with `repeat=True`, once a command's
arguments are satisfied--ALL of them, optional included
(saturation)--the next token may name another command.  The
**resolution stack** decides what a word may name: consulting a
set for its FIRST command is free (that's descent); re-entering
a set for a second command requires that set's own `repeat`; a
non-repeat set doesn't block its ancestors.  First match wins,
and the stack re-bases at the resolved set.  Nested sets (any
depth) are dicts pushed onto this stack.

**Stage 2 (execute): left to right, conversions included.**
`run_command_set` (or `Processor.execute`) runs each
invocation's `run` in order.  Conversions happen here--so a
CONVERSION failure mid-line is make-like: the commands to its
left already ran.  A nonzero int return halts the line and is
the exit status.  Class commands stash their instances in the
run's `env`; methods find self there.

**The tails**: an empty line yields `('bare',)` (the listing to
stdout, exit 1--orientation, not a diagnostic; a user
default_command takes precedence), `('default',)` runs it,
`('fused', ...)` covers trailing oddities like the help command.


## The MCP snippet route

`app.mcp()` serves the program's commands as MCP tools
in-process: each tools/call is `read_mapping(plan, arguments)`.
A class-based program constructs its instance ONCE at server
startup (config= feeds __init__ under the layering rules) and
method tools dispatch bound.

`app.standalone_mcp()` is the interesting one: rather than
re-implement the read driver (we tried; the parallel walker
drifted immediately and died), the emitter STREAMS THE REAL
MODULES into the script--the `appeal plan classes` region of
plan.py, all of build.py, all of read.py--plus the runtime's
exceptions/option-protocol/mcp snippets.  The script calls
`build(command)` at its own startup and `read_mapping` per
call.  One copy of the rules in the world; the price is a
bigger script (roughly 2,000 lines), which was ruled a good
problem to have.


## Three deep dives

### Deep dive: build.py, one signature start to finish

    def pair(x, y): return (x, y)
    def f(a='A', p: pair='P'): return (a, p)

`build(f)`:

1. `inspect.signature(f)`; walk the parameters in order.
2. `a`: positional, no annotation, default `'A'`.  `_child_for`
   sees no annotation and blesses `str`: `Slot('a',
   child=Terminal(str), required=False, default='A')`.
3. `p`: annotated with `pair`, a callable that isn't a blessed
   terminal--so recurse: `build` runs on `pair` (memoized: a
   converter used twelve times builds once), producing a child
   `Plan` with two required str slots.  `p` becomes a Slot whose
   child is that Plan--a nonterminal.  (Had `pair` consumed
   exactly one operand, the transparency rule would make `p`'s
   name flow through to its terminal, in usage and docs.)
4. No keyword-only parameters, so no `_build_option_rule` calls
   here; when there are, that one function infers the kind (bool
   default -> flag, list[T] -> accumulate, an Option subclass ->
   fold--or fold1 if strict--a converter -> group...) for
   signature options, @app.option declarations, and **kwargs
   alike.
5. `_analyze` computes the counting-automaton tables.  For f:

       a  count_options: (1, 0)  suffix_after: ({0, 2}, 0)
       p  count_options: (2, 0)  suffix_after: ({0}, 0)
       valid_counts: {0, 1, 2, 3}

   Read `a`'s row as: "slot a can consume 1 or 0 operands
   (prefer 1); whatever comes after me can consume 0 or exactly
   2."  That's everything the parser will ever need to route
   operands--decided here, once, before any argv exists.
6. Top level only: `_finalize_options` (collision check; auto
   short options, first declared wins; scoped-keys discovery)
   and `_mark_barriers` (the gate rule).
7. `_validate_completions`: any converter carrying a
   `completions` attribute gets its signature checked now--
   build time is when structure bugs fail.

The plan is passive from here on.  Print it; nothing hides.

### Deep dive: parse_tokens, one argv start to finish

parse_tokens is grammar-blind: it takes argv plus a table mapping
each option string to (key, kind, [counts...]), and splits tokens
from payloads.  Given flags -v/--verbose, and value options
-t/--times and --color:

    parse_tokens(['-v', '-t', '3', '--color=red', '--', '-literal'],
                 options)
    -> (['-literal'], {'--verbose': True, '--times': '3',
                       '--color': 'red'})

Walking it: `-v` is a flag (records the boolean True; `-v=false`
would record False--exactly those two spellings); `-t` consumes
the next token; `--color=red` carries its value inline; `--`
turns option recognition off for the REST OF THIS PARSE (local
state, never program state), so `-literal` lands in operands.
Not shown but living in the same loop: bundling (`-vt3` = `-v -t
3`); getopt attachment (`-tred` = `-t red`, the rest of the
token verbatim); last-wins repetition with the reinsertion trick
(a re-recorded key moves to the END of `given`, which is how
`--north --south --north` resolves to north); fold1's
"specified more than once"; the `--`-outranks-greed carve-out
for optional opargs; negative numbers as operands; windowed
collection for *args groups (payloads recorded with operand
positions, bound to instances later); unknown options getting
did_you_mean suggestions; and the command-word split.

Everything token-level is in this one function; nothing
downstream ever looks at a raw argv again.

### Deep dive: what's in a standalone script

`emit_standalone(plan)` produces one file with six layers, top to
bottom (tests write them to tmpdirs constantly, or
`print(app.standalone())`):

1. A header comment naming the program and its provenance.
2. `import enum`, `import operator`, `import sys`--the whole
   import footprint.
3. The runtime, plucked: the base snippets every parser needs
   (exceptions, parse tokens, check count, run main, help--and so
   the theme, the completion engine, and big's five borrowed
   snippets, via requires directives), plus whatever else the
   generated source calls, plus whatever the refs declare
   carries them (__appeal_snippet__--how `file()`'s wrapper
   class travels).
4. Your program: `from yourmodule import yourfunc, converter...`
   --user callables are imported, never serialized.  This is
   where the north star bites: a callable defined in __main__, a
   bare lambda, a class made in a REPL--none are importable, so
   emission refuses, naming the offender.  Vocabulary products
   survive as *recipes*; process-stream defaults render by
   identity (`_out_default = sys.stdout`).
5. The refs and constants: defaults as repr-literals, the baked
   usage strings and help corpus, the templates dict, the theme
   spec, the completion tables, `_SET_` dicts for nested sets
   (children first), `_VERSION` when set.
6. The generated parser, then the main block:

       if __name__ == "__main__":
           sys.exit(run_main(parse_f, theme=..., completion=(...),
                             errors=..., version=...))

   run_main answers completion reentries and --version, prints
   errors per the stream ruling, returns exit codes politely
   (2 usage, 1 AppealError, 130 ^C), and hands bare invocations
   the listing.

The invariant that makes it all testable: the same generated
source runs in-process (exec'd with refs bound live) and
standalone (refs rendered as text)--so the differential fuzzer's
verdict on rung 3 covers both delivery modes.


## The life of a parse (in-process, command set)

    app.process(['-v', 'add', '1', '2'])
      -> Appeal._compile()                 [first call only]
           -> global plan compiled with the command-word split
           -> commands wrapped in _CompileOnDispatch (lazy)
      -> Processor.parse(argv)             STAGE 1: scan only
           -> scan_command_set(...)        global portion, then
                                           word -> scan_add -> ...
                                           (resolution stack; a
                                           malformed line raises
                                           HERE, nothing has run)
      -> Processor.execute()               STAGE 2: left to right
           -> run_global(...)              config injects here
           -> run_add(...)                 conversions, the call
           -> instances log appends

Second call: everything under _compile is cached.  Warm parses
are microseconds; that's why.


## The life of a standalone script

    app.write_standalone('mytool.py')
      -> build every plan (eager on purpose: the whole-program
         artifact must build and refuse everything)
      -> emit (same source text as in-process)
      -> render refs:   from yourmodule import serve
                        _port_default = 8080
                        _path_converter = split(':')     <- recipe
      -> pluck needed snippets out of appeal/runtime.py
      -> append: if __name__ == '__main__': sys.exit(run_main(...))

The script imports nothing but the stdlib and your module.  The
same generated parser text runs in both modes--that's the north
star's enforcement mechanism: one source, two bindings.


## Where to look when...

    "why did this signature build that grammar?"   build._child_for / _build_option_rule
    "why did operand X go to slot Y?"              interpreter._fill + the two tables (print the plan!)
    "why did this token parse as an option?"       runtime.parse_tokens
    "why did the LAST occurrence win?"             parse_tokens.record (the reinsertion) / interpreter._shared_winner
    "why did -- stop my greedy option?"            parse_tokens' greedy loops (the minimum-met carve-out)
    "which window claimed this occurrence?"        runtime.ScopedQueue.resolve (announce-first) / window_options
    "what will the generated code do?"             print(compile_plan(plan).source)
    "why did standalone emission refuse?"          codegen.render_ref
    "where does --help text come from?"            help.merge_docs -> runtime.render_help_page
    "why did the page wrap THERE?"                 runtime.help_margin (min(terminal, margin))
    "where do 'did you mean' suggestions come from?"  runtime.did_you_mean (exceptions snippet)
    "who decided the exit code / error stream?"    runtime.run_main (and ONLY run_main)
    "where do --version / the version command live?"  run_main (the option) / _CompileOnDispatch + emit_command_set (the command)
    "why does my class method see self?"           the env dict: Processor.execute / run_command_set + plan.binds
    "why no color?"                                runtime.can_colorize (PYTHON_COLORS > NO_COLOR > FORCE_COLOR > TERM > isatty)
    "why does TAB offer nothing?"                  runtime.complete_command (and: does the converter carry completions?)
    "how does '-' become stdin?"                   runtime.file / _ProcessStream (the 'appeal file' snippet)
    "how does a man page happen?"                  help.man_page <- app.documentation('man')
    "why did except AppealError not catch it?"     it did--or see foreign_appeal_error (two-copies)
    "what are the exact grammar rules?"            appeal.grammar.md  (the spec of record)


## Things to type at it

    python3 -c "
    import sys; sys.path.insert(0, '.')
    from appeal import build, compile_plan

    def pair(x, y): return (x, y)
    def f(a='A', p: pair='P'): return (a, p)

    plan = build(f)
    for s in plan.slots:
        print(s.name, s.count_options, s.suffix_after)
    print(plan.valid_counts)
    print()
    print(compile_plan(plan).source)
    "

Ten minutes of that--change the signature, watch the tables and
the generated source move--teaches the engine better than any
document, including this one.  When you graduate, print a whole
program: `print(app.standalone())` is the entire architecture in
one file, in order, with your name on the imports.
