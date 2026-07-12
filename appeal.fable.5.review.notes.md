# Appeal review notes — Claude Fable 5, July 2, 2026

> **Companion document:** the full, Larry-voiced v2 design proposal now lives
> in `appeal.v2.proposal.md` (written July 2026). That's the polished spec;
> this file is the raw working notes / decision log behind it.

Distilled findings, decisions, and proposals from the Fable 5 review session.
Companion to the chat transcript. Prior sessions: appeal.opus.4.6.review.txt,
appeal.opus.4.8.review.txt.

Repo state at review time: 0.6.4 + the Opus 4.8 bugfix patch and coverage
tests applied but **uncommitted** (270 tests, all passing).


## 1. Verified new findings (none flagged by prior reviews; all confirmed live)

1. **`--` poisons the Appeal instance.** `CharmInterpreter.__call__` does
   `force_positional = self.appeal.root.force_positional = True` — run-time
   parser state written onto the shared description object, never reset.
   After processing a command line containing `--`, a subsequent `process()`
   on the same Appeal instance silently treats options as positionals.
   Falsifies the README's "process multiple command-lines with the same
   Appeal object" claim. Fix: move `force_positional` onto the
   Processor/interpreter.

2. **`Appeal.process()` with default args crashes**:
   `TypeError: object of type 'NoneType' has no len()` — `Processor.__call__`
   does `len(sequence)` in the help/version pre-checks before handling the
   `None` default. `main()` survives only because it defaults to
   `sys.argv[1:]` first.

3. **`CharmBaseInterpreter.abort()` raises RuntimeError.** Its first line is
   `raise RuntimeError("ABORT MESSAGE: " + message)`; the real implementation
   below (`self._stop(); self.aborted = True`) is dead code — looks like a
   debugging raise that never got removed. Consequence: every compiled
   `abort` opcode — including user-facing `read_mapping` errors like
   "beta is required but was not set in the mapping" — escapes as
   RuntimeError, uncaught by `main()`'s UsageError handler. Also makes the
   `self.aborted` machinery unreachable.

4. **`SimpleTypeConverter.convert`'s zero-argument path returns the default
   into the void** (`return self.default` — callers ignore the return value;
   `execute()` returns `self.value`, still None). Either dead (the
   discretionary-queue path usually bypasses it) or silently delivers None
   instead of the default. Adjudicate during the coverage push.

5. **`foo -h` doesn't work.** `-h`/`--help` (and `-v`/`--version`) are only
   recognized as the *lone* argument — `Processor.__call__` pre-scans raw
   argv. `script.py cmd -h` → "unknown option -h". Every mainstream tool
   supports trailing -h; biggest everyday UX gap.

6. **Positional bool-default footgun.** The bool special case only applies to
   keyword-only params, so `def foo(flag=False)` (positional) consumes an
   argument and converts with `bool(str)`: `foo false` → `flag=True`.
   Should be a ConfigurationError or a real str→bool parser.

7. Bare `list`/`dict`/`tuple` annotations produce a misleading error
   ("empty <class 'tuple'> used as default"). Once `list[X]` means something
   (see §3), bare `list` should raise "did you mean list[str]?".

8. Error-message audit needed: `"invalid value something something
   converter"` (a literal TODO, reachable, in `Converter.convert`),
   `"why a second time, fool"`, abort messages that leak bytecode vocabulary
   ("lookup_to_o: key ...") to users reading config files.

9. Repo cruft: `1/`, `2/`, `x.py`, `exceptio.py`, `appeal.from.odyssey.zip`,
   stale `.coverage`, `htmlcov/`.


## 2. Architecture review (summary; full argument in transcript)

**The core idea is right and unique**: signature ↔ command-line, with the
recursive step (converter ≡ command function) providing compositional
closure. No other library has it. Preserve at all costs.

**The cost structure is inverted**: the simple 95% path (flat args/options/
subcommands) currently pays for the machinery that only the recursive 5%
needs (Charm VM, assembler, peephole optimizer, discretionary-converter
queueing, remember/forget_converters).

**Charm is a coroutine wearing a VM costume.** The interpreter's two-phase
loop (run opcodes until `next_to_o`, consume an argument, resume) is
suspend/resume — what Python spells natively with generators. Registers →
locals; call stack → Python frames; remember/forget_converters → lexical
scope. Evidence in-repo: (a) the ~2000 lines of commented-out `want_prints`
scaffolding exist because register-machine state is invisible to pdb;
(b) the "go-faster stripe" mini-loops and dispatch.py/experiment.py exist
because interpreter dispatch has overhead; (c) `_charm_usage` and
`compute_usage` are *second and third interpreters* for the same bytecode
with hard-coded branch heuristics. A tree-shaped plan makes all three
consumers plain tree walks. [Full "coroutines" discussion: pending, next
session topic.]

**Appeal's semantics are a grammar** — see §6. This is the recommended
specification and implementation frame for v2.

**Proposed v2 shape** (three layers):
1. *Introspection → plan tree.* One module; walks signatures (callables and
   builtin-generic spellings), produces an immutable tree of plan nodes.
   All grouping/degeneracy/option-scope analysis = tree folds. Only place
   `inspect` is touched. The grammar is written down here.
2. *Drivers.* Three small consumers of the plan tree: argv parser
   (recursive descent, partial/tolerant mode for completion), mapping
   reader, iterable reader. No shared VM — shared tree, three walks.
3. *Presentation.* Usage, help, composed docstrings: pure functions of the
   plan tree, built on big's split_text_with_code / wrap_words /
   merge_columns. (text.py is condemned; docstring template design per the
   Opus 4.8 session: literal-substring `{argument}`/`{documentation}`
   placeholders.)

**Keep the tests, not the code.** The 270-test suite is the v1 behavioral
spec; port it first, diverge only where a finding says v1 was wrong.

**Policy decisions to write down before coding**: the accepted grammar
(incl. option-scoping rules); Appeal-object immutability (all run-state on
Processor); the error-message catalog; minimum Python; bool-positional rule.


## 3. API decisions log (agreed with Larry this session)

- **typing policy**: Larry dislikes the typing module; language-built-in
  spellings only. ONE blessed exception: `typing.Annotated`.
  - Builtin generics (feature-detected via `getattr(types, 'GenericAlias',
    None)`; self-gating, costs nothing on ≤3.8 because users there can't
    write the syntax): `list[X]` kw-only = repeatable option accumulating X
    (alternate spelling of `accumulator[X]` — accepted); `dict[K, V]` =
    mapping option; `tuple[A, B]` = multi-oparg.
  - Static-analysis story: common spellings are mypy-clean *by construction*
    (delivered runtime type == annotation). Function converters' mypy-clean
    spelling is `Annotated[ReturnType, converter]` — the user imports it,
    Appeal never does (duck-types `__metadata__`, already shipped in 0.5.5).
    Ship `appeal/__init__.pyi` + `py.typed` so `@app.command()` doesn't
    erase signatures under mypy strict. Total typing imports in Appeal
    runtime code: zero.
  - No `Literal` (use `validate()`), no Protocol/ParamSpec/etc.

- **Metaphor extensions**:
  - Positional-or-keyword params doubling as options (grep -e style):
    **REJECTED** — don't hang special semantics on the default, unchosen
    parameter kind. (Larry also deliberately skipped positional-only `/`
    support; his name is on PEP 570.)
  - **Trailing required positional — ON THE LIST, Larry warming**:
    keyword-only parameter with *no default* (currently a
    ConfigurationError, i.e. dead syntax space) maps to a required trailing
    positional. `def cp(*sources, dest)` → usage `cp [sources]... dest`.
    Rationale: leading no-default params + trailing no-default params form
    the minimum argument count; optionals fill the middle. Preserves
    "options are always optional" (this isn't an option). Parsing =
    reserve-the-suffix (decidable when trailing group or repetition unit
    has fixed arity; rule for the general case goes in the grammar doc).
    Rejected alternative: `dest=appeal.trailing` sentinel. Current
    workaround: `def cp(file, *files)` + documentation.
  - `list[X]` heals the MultiOption wound (multiplicity is a call-site
    property; argv has no call site; `list[X]` is the signature spelling of
    "many of these", unavailable before PEP 585/2020). MultiOption demotes
    to advanced extension point; `counter()` et al. remain.
  - Error channel: `UsageError` already = "you used it wrong" (message +
    contextual usage; good). Add a sibling for "you used it right, the
    operation failed": message + exit code, NO usage block (the git
    distinction).
  - Class-based subcommand namespaces (`git add` ≡ `git.add()`): promote
    from app_class() sidecar toward first-class. Underdeveloped limb.
  - Accepted limitation: option-scoping rules are imposed by argv's
    linearization, not implied by the metaphor. Write them down and defend.


## 4. TODO feature: shell completion

Mechanism (Click-style, industry standard):
- Install phase: user's rc file has `eval "$(_MYPROG_COMPLETE=bash_source
  myprog)"`. Program checks that env var FIRST in main(); if set, prints a
  shell-specific completion function (bash/zsh/fish templates shipped as
  data) and exits.
- TAB phase: the shell function re-invokes the program with
  `_MYPROG_COMPLETE=bash_complete` + words-so-far + cursor index (env
  vars). Program parses the *partial* command line, prints typed candidates
  one per line (`plain` value; `file`/`dir` = delegate to the shell's own
  filename completion; zsh/fish accept a help string), exits.
- Candidates = FIRST set of the current parse state: (a) option strings in
  scope — Appeal's scoped child options complete contextually, which no
  other library can express; (b) subcommand names; (c) per-parameter
  completers. `validate()` completes for free (knows its values).
  `appeal.Path()`/`File()` emit type `file`. General protocol: duck-typed
  `__appeal_complete__(prefix)` on any converter.
- Requirements: v2 parser must support tolerant/partial parsing from day
  one (v1's interpreter pausing at next_to_o is accidentally halfway
  there). Completion mode must never write errors to stdout. Startup cost
  per TAB ≈ Python startup; acceptable.
- At implementation time: crib the battle-tested shell script templates
  from Click's source (quoting edge cases live there).

## 5. TODO feature: colorization

- Precedent: argparse grew stdlib colorization (3.14+, extended since) using
  raw ANSI SGR — validates zero-dependency approach. Appeal is POSIX-only;
  colorama is unnecessary.
- Design: has-a. `Appeal(theme=...)`. Theme styles SEMANTIC ROLES, not
  colors: theme.option(), theme.metavar(), theme.heading(), theme.error(),
  theme.command(), theme.summary(). A theme is any object with those
  methods; default = identity/plain; ship exactly one ANSI theme (~30
  lines). rich/colorama users can adapt in three lines; Appeal imports
  nothing.
- On/off policy (copy stdlib conventions exactly): color iff isatty and
  TERM != "dumb" and NO_COLOR unset; FORCE_COLOR forces on; consider
  PYTHON_COLORS. One function, one place.
- The real engineering: ANSI escapes have len()>0 but display width 0 —
  interacts with wrap_words/merge_columns. Either style AFTER layout
  (color only tokens that survive wrapping intact: headings, left column,
  option strings — probably sufficient) or give the text pipeline an
  escape-aware width hook (possibly a small feature request for big).
  Decide jointly with the docstring-rendering redesign.
- Scope: Appeal colorizes its own output only (usage/help/errors).


## 6. "Appeal is a grammar" (specification frame for v2)

The full essay is in the session transcript; the load-bearing points:

- Each signature is a production rule. Positional params = sequence;
  str-leaves = terminals; converter annotations = nonterminal references;
  defaults = optional suffixes (?); *args = repetition (*); return values
  flowing up = synthesized attributes (converters are the semantic
  actions). Options = a second token channel with lexically-scoped rule
  environments (entering a production pushes its option rules; the 0.6
  "early-mapped options" change = "a production's options become visible
  when the production becomes enterable; using one commits entry").
  `--` and oparg consumption = lexer mode switches. Subcommands = the one
  place a terminal matches a specific literal.
- KEY SIMPLIFICATION: positional terminals are indistinguishable — any
  string matches any terminal (conversion failure is a *semantic* error,
  not a parse error). A grammar over an effectively unary alphabet means
  all parse decisions are COUNT decisions. This is exactly why
  argument_grouping.py is pure arithmetic (min/max/optionality), and why
  greedy entry ("another token exists → enter the group") is the parse
  strategy. Argument groups ARE the states of the count-based decision
  automaton; in v2, grouping and parsing become the same code instead of
  parallel implementations that must agree.
- Payoffs: (1) the language finally has a written spec — usage strings are
  literally the grammar pretty-printed (`recurse2 a [[-v|--verbose] i f s]`
  is EBNF with square brackets); (2) compile-time errors become grammar
  well-formedness checks (unsatisfiable production, conflicting option
  rules in one scope); (3) completion = FIRST sets; (4) the grammar is
  GENERATIVE: derive random valid command lines from random signatures →
  fuzz v1 vs v2 for parity during the rewrite — a migration safety net no
  hand-written test suite matches.


## 6.5. "Charm is coroutines" and the implementation ladder (settled with Larry)

The CharmInterpreter's two-phase loop (run opcodes until `next_to_o`,
consume an argument, resume) is suspend/resume — a coroutine hand-built as
a bytecode VM. Correspondence: program+ip ↔ generator frame; call_stack ↔
`yield from`; registers ↔ locals; push/pop ops ↔ lexical scope;
remember/forget_converters ↔ object lifetime; labels/jumps ↔ if/while;
discretionary-converter queueing ↔ dissolved (don't create a converter
until the count decision commits entry). Loop two (option detection,
split_value, bundling, `--`, the options scope stack) survives nearly
verbatim as "the driver". Control flow is NEVER emitted as data — it
lives once, in source, as literal if/while.

Three implementation rungs, all consuming the same plan tree:
1. Hand-written generic tree-walker generator (~few hundred lines).
2. Compile-time closure composition (skip — neither simplest nor fastest).
3. Emit Python source per command, `exec` it (Jinja/Tidy-style; register
   in linecache for real tracebacks; the generated source IS the
   disassembly — charm_print's successor is `print(command.source)`).

DECISION (Larry, who wrote Argument Clinic and Tidy and needs no
convincing about codegen): **rung 3 is the production design; rung 1 is
kept as the executable grammar spec and parity-fuzz oracle.** All rungs
beat Charm on speed; rung 3 wins outright. Bonus: disk-cache the
generated parser (Tidy-style module) — per-invocation cost matters for
shell completion, which re-invokes the program on every TAB.


## 6.6. Three further reframings (v2 opportunities, not v1 critiques)

**A. Argv is a serialization format; Appeal is a codec.** A command line is
a serialized Python call; Appeal is its deserializer. v2's parse/execute
split makes the intermediate — the fully-bound invocation tree — a
first-class value: dry-run (--dry-run for free), audit logging, record/
replay, test assertions without side effects. And every deserializer
implies a serializer: given (callable, args, kwargs), walk the plan tree
BACKWARDS and emit the argv that parses to it. Type-safe subprocess
invocation building — `appeal.render_argv(fgrep, pat, files,
ignore_case=True)` → guaranteed-roundtrip argv. Nobody has this. Related
inverse: usage strings are the grammar pretty-printed, so parse a usage
string INTO a signature scaffold (docopt is Appeal read backwards —
migration tool / party trick). Config layering falls out too: argv,
mapping, env are three serializations of the same call; parse each to a
partial binding, merge by precedence, execute once (replaces the
default-value plumbing idiom).

**B. The plan tree is an IDL; argv is just the first backend.** The same
tree that renders to a command-line renders to: JSON Schema — i.e., AI
tool-calling / MCP. `app.mcp()`: every Appeal command becomes an MCP tool,
schema from the plan tree, dispatch through the EXISTING mapping driver
(a JSON request body is a mapping; read_mapping already is the decoder).
Your CLI is automatically an agent-callable API. Likewise nearly free:
an interactive REPL (`app.repl()`: readline + shlex.split + same parser +
same completion engine), and in principle web forms/TUI. The 2026 Jones
to keep up with, and it costs almost nothing given v2's architecture.

**C. Arity is semilinear → exact arity errors.** Because parse decisions
are count decisions (§6), the set of valid argument counts for any
command is computable from the plan-tree fold — an eventually-periodic
set of integers (e.g. {1, 4} or {2, 5, 8, 11, ...}). So error messages
can be exact: "frobnicate takes 1 or 4 arguments; you gave 3" instead of
generic group-based messages. Also: detect dead groups at compile time
(optional group whose entry can never be distinguished), and print the
arity set in verbose help.


## 6.6.1. Appeal vs MCP JSON Schema: a proper intersection, not a superset

Neither contains the other. Three zones:
- Appeal-exclusive: executable semantics (converters RUN — conversion,
  validation, side effects); `complex` (no JSON type); and the linear/
  positional concerns (order, argument grouping, option scoping) — which
  mostly EVAPORATE rather than fail to translate: they're artifacts of
  serializing a call onto a line, and JSON is already a tree. (Option
  scoping, Appeal's hardest subsystem, simply doesn't exist in the JSON
  rendering — child options are just properties of the nested object.)
- JSON-Schema-exclusive: declarative constraints (pattern, format,
  min/max, etc. — recoverable if the menagerie exposes metadata, see
  below); unions (anyOf); explicit null; open objects
  (additionalProperties — recoverable via **kwargs if the v2 mapping
  driver supports it, v1 rejects it); and UNBOUNDED RECURSION ($ref) —
  which Appeal must reject on principle: JSON has brackets, argv has no
  delimiters, so Appeal's grammar must be finite-depth (build()'s cycle
  check is the enforcement).
- Intersection: finite trees of named, typed, optionally-repeated,
  required-or-optional, documented parameters — i.e. everything a sane
  MCP tool needs.
AMENDMENT (Larry's catch): the no-recursion theorem is argv's, not
Appeal's. Cycle handling is PER-BACKEND: build() still *detects* cycles
(it must, to terminate — the plan becomes a finite cyclic graph, not a
tree), but the argv driver rejects cyclic plans (no delimiters) while
the mapping/MCP drivers accept them — parse recursion is driven by data
depth, which is finite. The arity fold becomes a least-fixpoint
computation, which automatically rejects unsatisfiable cycles (a cycle
with no optional/absent-able edge has min_args = ∞ → ConfigurationError,
same family as required-after-*args). Schema emission renders cycles as
$defs/$ref; usage/help renders them as named references. Spelling note:
self-referential annotations need string annotations pre-3.14; under
PEP 649 (3.14+) `children: list[tree_node] = ()` inside tree_node's own
def just works (lazy evaluation). Payoff: Appeal MCP tools can declare
genuinely recursive inputs (expression trees, nested filters) — flat
CLI libraries can't even represent the concept.

Design consequences: (1) give the vocabulary a schema-metadata protocol
(validate() → enum, validate_range() → minimum/maximum, split() → hints)
so declarative constraints survive the crossing; (2) the emitted schema
is a SOUND APPROXIMATION — anything it admits that converters reject
fails at execution as a UsageError returned as an MCP tool error, which
is correct protocol behavior (agents retry on tool errors); (3) optional
argument groups render as either a nested optional object (preferred) or
dependentRequired; (4) v1 parity gaps to fix in the v2 mapping driver:
*args → array, **kwargs → open object.

## 6.7. Larry's verdicts on the three tricks + competitive analysis

- Larry's own metaphor, for the record: Appeal "builds an execution of
  your function" — it descends the annotations tree looking for leaves
  (each leaf = "pop the next string off argv, insert here"), builds the
  lists it will splat as *args and dicts it will splat as **kwargs;
  leaves are called first, the command function (root) is called last.
  (= bottom-up evaluation of synthesized attributes, in grammar terms.)
- Trick C (exact arity errors): Larry has a LONGSTANDING open issue —
  "figure out a better way to communicate argument groups to users in
  error messages." The arity fold dissolves it: error messages never
  mention groups at all; they state the command's valid argument counts
  ("takes 1 or 4 arguments; you gave 3") and, mid-parse, the named
  parameters expected next. High priority for v2.
- Trick A (argv synthesis): Larry unconvinced as a user feature
  ("huh, neat, but I'll never use it") — DOWNGRADED to internal role:
  the sentence generator is needed anyway for the parity fuzzer, and
  bidirectionality is the design certificate, not a headline. The real
  underused strength it gestures at: Appeal interfaces are also Python
  automation interfaces.
- Trick B (plan tree as IDL → MCP): the headline. Competitive honesty:
  the FLAT version is open to everyone — argparse's _actions is walkable
  (Gooey built a GUI generator on it), Click 8 ships to_info_dict() for
  exactly this, Typer has real type hints. What only Appeal has:
  (1) a RECURSIVE interface description — JSON Schema is a recursive
  format; Click/argparse IRs bottom out at depth 1 with opaque leaf
  types (type=callable / custom ParamType = black box), while Appeal
  recurses through converter signatures down to simple types, so its
  schemas have real nested structure; (2) a native non-argv entrance —
  MCP tool calls carry structured JSON values (numbers, arrays, nested
  objects); argv-native libraries must stringify/flatten everything back
  into fake argv, while Appeal's mapping driver (read_mapping, shipped
  2023) consumes them natively, non-string values included; (3) composed
  per-parameter docs at every depth for schema descriptions.


## 6.8. Class-instance commands in v2 (replaces app_class/Rebinder machinery)

v1's problem: methods need `self`, and the instance may not exist until
mid-parse (app_class: global command constructs it). v1 fakes a complete
callable via functools.partial with a placeholder, then execution-time
"preparers" rebind the placeholder to the instance (partial_rebind_method,
CommandMethodPreparer, Rebinder) — plus the update_wrapper/__wrapped__
deletion hack (Larry's bpo-46761). All symptoms of the architecture
lacking an execution phase to defer binding into.

v2 design: the plan tree records the FACT ("callable = MyApp.add, unbound;
self comes from the MyApp instance"); the signature analyzer skips the
self parameter of a method-of-a-command-class; execution runs in an
ENVIRONMENT — the global command's invocation constructs the instance and
deposits it keyed by class; the executor resolves self via
getattr(instance, name). No partials, no placeholders, no rebinding.

Surface: one decorator on the class; __init__ = global command (its
kw-only params = global options); public methods (or a plain
`@appeal.command` marker — needs no app reference, killing the v1
two-decorator chicken-and-egg) = commands; nested classes = subcommand
trees (open decision: does a child __init__ receive the parent instance?).

Generalization: `self` is just the first environment-supplied parameter.
bind_processor / bind_appeal become entries in the same environment —
three ad-hoc mechanisms collapse into one lookup. (Whether to let users
inject their own resources: taste decision, defer.)


## 6.9. The v2 compiler, concretely

Key realization: Appeal v2 has NO parsing technology in the compiler at
all. yacc consumes a grammar written as text; Appeal's grammar is written
as Python signatures, and CPython already parsed those when it compiled
the user's `def`. inspect.signature hands us the grammar as structured
data. The "compiler" is therefore pure semantic analysis: a builder plus
small tree passes. No lexer (the shell pre-tokenized argv; "is it
option-shaped" is ~20 lines in the driver), no parse tables (decisions
are count comparisons read off node attributes).

### The plan tree itself (the compiler's output format)

Plain passive classes, __slots__, no behavior (~120 lines). The design
rhyme is deliberate: Signature : Parameter :: Plan : Slot.

- Plan — one production rule, one per callable USE (expanded per use,
  not interned; the tree is small): callable, name, doc, slots (tuple of
  Slot), options (tuple of OptionRule), arity (min, max), arities (the
  semilinear count set for error messages), environment kind (needs
  self?), id (for $ref emission).
- Slot — one positional parameter = one EDGE: name, usage_name, child
  (a Plan, or STR/simple-type leaf sentinel), required (post-promotion),
  group, repeat kind, default, display flag (unit-chain collapse).
- Group — one count decision: optional, min, max, member slots.
- OptionRule — strings, target parameter, its own Plan for opargs,
  repeatable flag, doc.
- Ref(plan_id) — recursion knot; legal only on paths consumed by
  backends whose wire format has delimiters (mapping/MCP), rejected by
  the argv backend.

Key points:
1. Node/edge split kills fake_parameter: v1's unit of compilation was a
   Parameter, so compiling a bare callable required faking one. v2's
   unit is the callable (Plan); per-use facts (name, default, required,
   usage override) live on the Slot edge. build(callable) takes the
   command function directly.
2. Grouping is an ARGV-BACKEND concern only, computed over the expanded
   (acyclic) leaf sequence and baked onto Slots/Groups; the mapping
   backend ignores groups entirely (named access has no entry-commitment
   problem). This is also why per-use expansion beats interning: the
   same converter can be promoted-required in one command and optional
   in another.
3. The plan is passive and immutable after compilation — all behavior
   lives in the backends; no parse-time state ever touches it (the
   force_positional lesson, structurally enforced).
4. vs inspect.Signature: a Signature is ONE production, unanalyzed —
   annotations still raw callables. The plan is the whole grammar with
   semantic analysis attached: recursion resolved into child Plans,
   promotion already applied, arity/groups/options computed, options
   lifted out of parameters into scope rules. Signature is the AST;
   the plan is the analyzed IR.
5. No plan serialization needed for the disk cache: rebuild-by-
   introspection is cheap; what's cached is rung-3 generated source.

File shape (approx):
- plan.py — node classes + build() + analysis passes (~450 lines):
    build(callable) -> ConverterPlan: recursive descent via
      inspect.signature + the v1 converter-factory chain (map_to_converter
      / get_signature protocol SURVIVES — it's good plugin design), with
      Annotated dereference, builtin generics, simple-type leaves, and a
      cycle check (memo/stack) v1 never had.
    compute_arity(plan): bottom-up (min,max) fold; *args → max=inf;
      trailing-positional feature = suffix reservation here.
    compute_groups(plan): pgi's REPLACEMENT — flatten leaves depth-first
      (each with optionality depth + required flag), one backward scan
      carrying a required-floor to do the promotion (pg's second_pass
      insight), then a forward walk to place group boundaries. ~40 lines.
    collect_options(plan): kw-only → OptionRules, short/long derivation,
      explicit mappings, per-scope collision checks.
    validate(plan): ALL ConfigurationErrors gathered in one pass.
- parse.py — rung-1 walker + driver (v1 loop-two heir).
- emit.py — rung-3 Python-source generator.
  Emission volume: linear in the grammar — ~3-5 lines per parameter in
  the signature tree; code carries CONTROL FLOW ONLY, data (converters,
  defaults, Slot/OptionRule objects, docs) passes by reference through
  the exec namespace. hello(name) ≈ 10 lines; a real command with
  options ≈ 30-60 lines; per-command lazy compilation (v1 already lazy).
  compile()+exec of that is ~0.1-1ms — cheaper than the
  inspect.signature calls that precede it. CORRECTION to earlier
  disk-cache idea: caching generated source saves ~1ms out of ~50-100ms
  interpreter+import startup — not worth it for completion latency;
  measure before building. (The Tidy-style "compile to standalone
  module" idea survives as a possible separate feature — zero-dependency
  deployment — not as a cache.)
- render.py etc. — presentation consumers of the tree.

pgi's fate: not thrown away — RETIRED WITH HONORS AS THE ORACLE.
argument_grouping's algorithm knowledge (the promotion rule) is
reimplemented as the backward scan; its test corpus + differential
testing (run pg and compute_groups on generated random signatures, diff)
is what *proves* the new grouping equivalent before pg is deleted.
Larry's longstanding "not sure pgi is correct" worry resolves empirically
during the transition.

### Do now (independent of v2)
1. **Commit the Opus 4.8 patch** — ~25 bugfixes + the coverage tests are
   still sitting uncommitted in the working tree.
2. Repo hygiene: delete/archive `1/`, `2/`, `x.py`, `exceptio.py`,
   `appeal.from.odyssey.zip`, stale `.coverage`, `htmlcov/`; note
   `appeal/dispatch.py` and `appeal/experiment.py` are scratch files
   sitting INSIDE the package directory (they'd ship in a wheel) — move
   or remove; update .gitignore.
3. Fix the three verified crashes/leaks (small, testable on v1):
   force_positional leak → Processor; `process(None)` TypeError;
   `abort()` debug-raise → real UsageError path (and delete the dead
   `self.aborted` machinery or make it live).
4. README repairs: the environment-variables section has a dangling
   sentence ("here's how to support the environment variables..." —
   followed by nothing); the "can't call main() twice" vs "process
   multiple command-lines simultaneously" contradiction; stale copyright
   years (2021-2023 vs 0.6.4 dated 2026); verify the referenced
   `appeal/notes/*` files actually exist in the sdist.
5. Deprecation harvest: the renamed exceptions (`AppealUsageError` etc.)
   were promised "at least one year" in Oct 2023 — removable;
   `Appeal.argument` → `parameter` alias; `SingleOption` alias.
6. Metadata sync: pyproject still claims 3.6+ while CI already dropped
   3.6 — decide the actual floor and make pyproject/classifiers/README
   agree; unused-imports sweep (`base64`, `time`, `pprint`, `string`...);
   duplicate `from abc import ABCMeta, abstractmethod` (imported twice);
   apparently-unused `_check_methods`.
7. Tests: guard the bottom-of-file `unittest.main()` driver quirk noted
   by Opus 4.8; continue the core coverage push (74% → up) on code v2
   keeps (argument_grouping semantics, option parsing behavior, readers)
   — these tests port to v2 as the behavioral spec.

### Do on v1 only if v2 slips (otherwise subsumed)
8. `foo -h` / `-v` after a command (in v2 this is parse-level, free;
   in v1 it's surgery on the pre-scan).
9. Positional-bool footgun, bare `list`/`dict` annotation errors,
   SimpleTypeConverter zero-arg default-into-void — all
   inference-layer; v2 rewrites that layer.
10. Error-message audit ("something something", "fool", lookup_to_o
    leakage) — v2 gets an error catalog; fixing v1 strings is churn.

### Don't touch (condemned with their subsystems)
11. text.py, the want_prints scaffolding, DictGetattrProxy /
    SpecialSection / compute_usage internals, Rebinder/
    CommandMethodPreparer polish, any Charm refactoring (dispatch
    tables, instruction-class generation — the 4.6 suggestions).
12. Windows: leave the "untested" disclaimer for v1; v2 stance —
    pure-Python parser should just work, colorization is isatty+VT,
    completion ships POSIX shells first (PowerShell later, maybe).


## 8. big dependency review (July 2026 — v2 groundwork, bugs VERIFIED live)

Pieces v2 needs: PushbackIterator; multisplit (+multistrip) for
appeal.split(); wrap_words + split_text_with_code + merge_columns
(presentation); Log (dev tracing only, review deferred).
DROPPED: BoundInnerClass (both v1 uses are Charm-internal, deleted in v2).

Bugs found (all confirmed by execution except doc items):
- B1 PushbackIterator.__next__ has a BARE `except:` — swallows ANY
  exception from the wrapped iterator (ValueError, KeyboardInterrupt...)
  and converts it to silent exhaustion. `for x in p` just ends early,
  error vanishes. The sibling next() method correctly catches only
  StopIteration. HIGH for Appeal: read_iterable wraps user iterables.
- B2 PushbackIterator.__init__: `iterable != None` — invokes user
  __eq__; should be `is not None` (the ==None class again).
- D1/D2 PushbackIterator docstring: "first-in-first-out order, like a
  stack" (FIFO is a queue; behavior is LIFO, as its own example shows);
  "__bool__ returning true if the iterator is exhausted" (inverted).
- B3 wrap_words: two_spaces=False is IGNORED — the parameter is
  clobbered by the first-word setup (`two_spaces = '  '` rebinds it
  unconditionally). Fix: rename the local.
- B4 split_text_with_code: line-LEADING exotic whitespace (NBSP, \r,
  \x0b, \f) with allow_code=True (default) → RuntimeError "unhandled
  whitespace character". NBSP happens in copy-pasted docstrings → help
  generation would crash. ("a\r\nb" mid-line survives; leading crashes.)
- B5 merge_columns: `rstripped_lines` is WRITE-ONLY — loop 1 builds it
  (per-line rstrip + overflow_after padding), loop 2 iterates raw
  `lines`. Two confirmed symptoms: (a) trailing whitespace on a line
  (raw len > max_width, stripped fits) → no overflow detected, ljust
  no-op → subsequent columns MISALIGN; (b) overflow_after padding for a
  column-final overflow vanishes → overflow_after silently no-op.
  Fix: `lines = rstripped_lines` after loop 1 (+ recompute max_lines).
- Minor: merge_columns validates args with `assert` (stripped under
  -O); is_bytes sniffed from columns[0][0] though s may be a pre-split
  list.

- B6 (BONUS, pre-existing, found while fixing B4): split_text_with_code
  loses the FINAL line of a code paragraph when the input has no
  trailing linebreak — close() flushed the pending word into the code
  buffer but never emitted the buffer. Fixed in close().

STATUS: all of B1-B6 + D1/D2 FIXED in the ../big working tree
(uncommitted, July 2, 2026), with regression tests added in big's house
style. big's full suite green (incl. the 15,879-permutation multisplit
tester); Appeal's 270 tests green against patched big. Two existing big
tests updated: the merge_columns overflow_after=3 expectation encoded
bug B5b (its expected output violated its own overflow_after), and two
assertRaises(RuntimeError) for \v whitespace encoded the B4 behavior as
DELIBERATE — flag: Larry may veto the tolerance change and restore the
raise; if so, also revert those two test edits and keep B6's fix.

multisplit: Appeal's usage slice (forward, keep=False, no maxsplit,
strip bool) rides the simple path; no bugs found there; exotic corners
(reverse, PROGRESSIVE) out of scope — and the test suite (seven suites,
105k calls, exhaustive permutation oracle) makes a mini-multisplit
reimplementation indefensible.

Import-cost verdict (measured): `import big.all` ≈ 53ms; bare
`import big` ≈ 0.2ms; big.itertools ≈ 11ms; big.text ≈ 28ms; big.log
≈ 40ms (pulls inspect). v2 plan: NO mini-multisplit. Stop importing
big.all; module-level `from big.itertools import PushbackIterator`
(~11ms, the only startup-path need); lazy-import big.text inside
appeal.split() and the help/presentation path (nobody times --help);
drop or lazy big.Log. Hot path (parse+execute, the MCP tool-call path)
pays ~11ms instead of ~53ms, with zero duplicated code.

Cross-cutting, confirmed at source: all three text functions measure
with len()/ljust — ANSI-escape-unaware, as predicted in the
colorization design; decide style-after-layout vs a width hook in big.
wrap_words raises ValueError on an empty words iterator
(split_text_with_code returns [''] so the pipeline is safe; direct
callers must guard).


## 9. Rewrite sequencing (from the Opus 4.8 session, still endorsed)

1. Commit the pending bugfix/coverage patch. Add fixes for §1 items 1–3
   (small, testable).
2. Core coverage push on stable code (entangled with bug-finding).
3. Grammar write-up = first rewrite artifact.
4. v2 layers 1–2 (plan tree + drivers) against the ported test suite +
   grammar fuzzer.
5. Presentation layer: docstring templates, colorization, completion.
