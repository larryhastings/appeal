# Appeal 1.0 break audit

*Confronting Appeal 1.0 with independent oracles it was never
allowed to negotiate with.  Round 1: the pristine v1 corpus --
`git show 33ffcce:tests/` and that commit's whole tree (its own
README, so the README-derived tests are genuinely pristine),
run unmodified against 1.0.  The only accommodation: constructor
kwargs shimmed (break A1 below) so one TypeError doesn't mask
everything behind it, and the deleted `argument_grouping` module
shimmed back in (break A2).*

Raw score, pristine corpus vs 1.0: SmokeTests 114/138,
ReadmeTests 64/67, OptionParsing 11/13, ConfigFileReading 14/15,
everything else clean.  Every failure is catalogued below.
NOTHING here is excused; "Larry-ruled" claims cite the July
transcript and still await confirmation.

## A. Hard API breaks (v1-legal code crashes)

* **A1. `Appeal(usage_max_columns=..., usage_indent_definitions=...)`
  raises TypeError.**  RULED (Larry, 2026-07-18, review item 1):
  **intentional -- keep `margin=` / `indent=` only**, no aliases;
  the 79 default stands too.  v1 programs update their spelling.
* **A2. `appeal.argument_grouping` is gone** ("retire the v1
  oracle" deleted the module).  Any `import
  appeal.argument_grouping` breaks.
* **A3. `app.app_class()` / `app.command_method()` are gone.**
  RULED (Larry, 2026-07-18, review item 4): **restored as a
  20-line compatibility layer** over class-as-app --
  `app.app_class()` returns (app_class, command_method), the
  README example runs unmodified with v1-identical output.
  Tests: test_app_class_compat_layer.  (A2 stays deleted --
  review item 3; A1/F5 ruled keep-the-new-way -- items 1-2.)
* **A4. `process(args=None, kwargs=None)` became
  `process(args=None, config=None)`** (main too).  RULED (Larry,
  2026-07-18, review item 5): **keep `config=`, no alias.**
  (Verified: v1's `kwargs=` was mapping-mode plumbing, ignored on
  CLI calls; 1.0's `config=` is the real config-layering feature.
  An alias would silently change a hypothetical caller's
  behavior--worse than the TypeError.)

## B. Semantic changes (same command line, different behavior)

* **B1. Option repetition.**  RULED by Larry (2026-07-18, review
  item 6), superseding both v1's "specified more than once" AND
  the July session's plain last-wins: repetition is for
  overriding defaults.  **Value options: last-one-wins** in
  command-line order, across spellings and shared-parameter
  declarations (an alias baking in `--north` is harmlessly
  overridden by `--south`).  **Bare flags: idempotent
  store-not-default** (`-v -v -v` is `-v`; a default-True flag
  idempotently stores False)--Larry considered toggle-per-
  occurrence, then chose idempotent after a parser-landscape
  survey (store-true dominant, counting for -vvv, toggle rare);
  explicit `=true/false` stays absolute, last spoken wins.
  Tests: test_options_repeat_semantics.  STILL OPEN from this
  family:
  `Option` repeatable vs v1's at-most-once (+ `StrictOption`) --
  Larry is thinking about it.
* **B2. `-fjoe` with a required-oparg short is now accepted.**
  RULED (Larry, 2026-07-18, review item 7): **keep 1.0's getopt
  rule** -- glued values for any one-value short option,
  `-DX=1` included, `-f=joe` strips the separator.
* **B3. Same converter reused across siblings.**  RULED and
  IMPLEMENTED (Larry, 2026-07-18, review item 8).  Positional
  siblings: scoped windows (already working).  Keyword-only
  SIBLING OPTION GROUPS (e1: extras, e2: extras) were BROKEN
  (child options refused with "requires --e1"; announcing --e2
  crashed with AttributeError); now: shared child options bind by
  announcement--nearest announced parent before them; with no
  announcement they SUMMON the first declared sibling with
  defaults, and never cascade (no way to say which sibling an
  unannounced option means, so e2 exists only when announced);
  out-of-declaration-order announcements refuse by name once a
  shared option was spoken; a first sibling with required
  arguments can't be summoned (loud refusal).  Both rungs, full
  parity.  Tests: test_sibling_option_groups.
* **B3a. Optional opargs: opargs are REQUIRED by default;
  `optional[T]` is the marked spelling.**  RULED 2026-08-03,
  twice: a morning ruling made None-defaulted str options
  optional-oparg; Larry walked it back the same day on the make
  precedent (`-f file` is the common case, `-j [jobs]` the
  marked one)--0.6.4's required-value behavior is RESTORED
  byte-for-byte (corpus pin test_str_i_f_4 stands unmodified).
  The marked spelling is the new `appeal.optional`, subscriptable:
  `jobs: optional[int] = 1` -- absent -> 1, bare `-j` -> int()
  == 0, `-j 3` -> 3.  Bare `optional` refuses by name
  (converter-factory protocol); products carry recipes+snippets
  so standalone scripts re-run `optional[int]` verbatim.  The
  precommand's help topic is `optional[str]`.  optional_str
  remains deleted.  Tests: test_optional_oparg_subscript.
* **B4. 0.6.4 scope rejections accepted** (five_level_stack x8,
  options_stack x6, mixed_groups_2/5, test_test_3): options
  recognized anywhere, completable distribution.  Verified
  2026-07-18: mixed_groups_2 parses to the only completable
  reading; mixed_groups_5's early -f binds the first child
  group.  Larry re-engaged the design at review item 8 and
  supplied the sibling summoning rule (B3) as its refinement --
  treated as ratified alongside it.
* **B5. `help test` output appears to omit the usage line
  entirely** (the capture begins at the docstring summary).  If
  real, that's a regression against v1 *and* against July-4 v2.
  VERIFY.

## C. Presentation rulings

* **C1. Metavars render `<NAME>` -- full clap.**  RULED (Larry,
  2026-08-04, after a survey): the default
  positional_argument_usage_format is now `'<{name.upper()}>'`.
  Camps surveyed: bare CAPS (argparse, click, GNU), angle
  brackets (docopt, git/hg man pages, Rust's clap == `<FILE>`),
  plain lowercase (make's man page, v1 Appeal).  Ruled with the
  git/docopt/Rust lineage.  Applies to usage lines AND the
  Arguments/Options table row labels; explicit
  @app.parameter(usage=) renames stay literal and unformatted,
  as ever; optional opargs stay bracketed: `[-j|--jobs [<JOBS>]]`.
  v1's plain default remains one knob away.  Corpus pin
  test_two_or_more_files_usage converted (its renamed operands
  stay literal "file"; the unrenamed one formats).

## D. Behaviors deliberately dropped (round 1.5: v1 worked, a
## session decided the new way was better, tests edited away)

*Found by inverting the search: instead of running v1's tests,
enumerate what 1.0 refuses/changed on purpose and probe live v1
with each construct.*

* **D1. The auto version story.**  RULED and REDESIGNED (Larry,
  2026-07-19, review item 9 -> the default_mappings design),
  superseding the earlier keep-1.0-shape ruling: version support
  is now the STOCK `default_mappings(app)` callback -- runs once
  at first compile, maps the `version` command ->
  app.default_version and `-V`/`--version` -> the precommand,
  each only if free (yields to user declarations).  The
  precommand is a real stage ahead of the global command; its
  options scan the pre-command-word era and act at scan time
  (metadata outranks a malformed line).  `Appeal(default_mappings
  =None)` banishes ALL default semantics; `default_options` also
  accepts None; re-ported 2026-07-22 to the arglet style:
  `(app, callable, name, annotation, default)`, and the
  policy REGISTERS via app.option() on a per-build registrar
  proxy--one mechanism, declining is not calling.  Public
  introspection: .commands/.handler/.default_handler/.options.
  Subclass Appeal and override default_version/default_help to
  customize.  Stage B DONE (2026-07-19): `-h`/`--help` are
  precommand options for command-set programs, with the greedy
  optional topic (`tool -h` = `tool help`; `tool -h work` =
  `tool help work`), yielding and banishable like -V; the
  precommand EXITS (sys.exit(0))--main() converts to a return
  code, raw process() propagates.  v1-invisible in usage lines
  (corpus goldens pinned).  Single-command programs keep
  whole-line-era help (semantics identical there).  STILL
  OPEN: strictly removing per-command `prog go --help` (full
  era enforcement) awaits a help-rendering refactor--`help go`
  internally rides the per-command --help path today.  Tests:
  test_default_mappings_design.
* **D2. Help layout.**  RULED and IMPLEMENTED (Larry,
  2026-07-19, review item 10): **usage first (0.6.4's order),
  prog prefix restored.**  The master templates lead with the
  usage line; app-built command plans stamp `plan.prog`, so the
  line reads `usage: prog go ...`--pasteable into a shell.
  Direct build() plans carry no prefix (no app to name).
  Supersedes B5.

## E. Public API deleted (surface diff, v1 site-packages vs 1.0;
## corpus never imports these, so round 1 was blind to them)

* **E1. `appeal.AppealCommandError` / `appeal.CommandError` are
  gone.**  README-documented (line 2090): raising it from a
  command is v1's way to fail with an error message/exit code.
  Any script that raises it dies with AttributeError.
* **E2. `appeal.Preparer` is gone** -- the README section
  "Classes, Instances, And Preparers" (dependency injection);
  `bind_processor` and `CommandMethodPreparer` too.
* **E3. `app.usage()` is gone** -- README-documented (line 2260).
* **E4. `appeal.Converter`, `SingleOption` (deprecated-but-kept
  alias of Option), `BaseOption`, `SimpleTypeConverter`,
  `Inferred*`, `SpecialSection`, `parse_bool`, `big` -- all gone.
* **E5. Appeal methods gone: `version`, `usage`, `error`,
  `execute`, `analyze`, `convert`, `bind_appeal`,
  `bind_processor`, `command_method`/`app_class` (=A3),
  `compute_usage`, `render_docstring`, `option_signature`,
  `map_to_converter`, `format_positional_parameter`.**

Probed clean (v1 and 1.0 agree): tuple defaults, `**kwargs`,
`--`, `--name=joe`, bare `-`, multi-param option converters,
`*args: pair`, nested converters, `validate_range`,
`accumulator`, `-h`/`--help` options.  1.0-liberal (superset,
not breaks): `go --help` per-command, negative-number operands.

## F. Round 2: signature diffs + v1-idiom probes (vs the real
## 0.6.4 in site-packages; the corpus never exercises these)

* **F1. v1's docstring markup is no longer parsed -- help renders
  it as literal garbage.**  v1's documented format (README ->
  writing.documentation.txt; used throughout v1's own tests) is
  `[[arguments]]` / `[[options]]` / `[[commands]]` ... `[[end]]`
  sections with `{param} docs` entries.  A session designed a new
  `Arguments:` heading grammar (appeal.documentation.md) and
  retired the old format with the old tests.  1.0 output for a
  real v1 docstring: `[[arguments]] {str1} A string!  [[end]]`
  dumped as prose, ALL per-parameter documentation lost, empty
  `Arguments:` section appended.  Every documented v1 script
  produces garbage help.
* **F2. `@app.command('db').default_command()` raises
  AttributeError.**  README-documented subcommand idiom; 1.0's
  `_SubRegistrar` implements `.command()` but nobody implemented
  `.default_command()`.
* **F3. `app.help('go')` raises TypeError** -- v1 is
  `help(*command)`; 1.0's `help()` takes no arguments.
* **F4. `Appeal(parent=app)` raises TypeError** -- the v1
  constructor's `parent=` is gone (1.0 moved parentage into
  `command(parent=)`).
* **F5. Eight more v1 constructor knobs dropped.**  RULED
  (Larry, 2026-07-18, review item 2): **all eight stay gone;
  always-on is fine.**  (Probed: six were dead even in v1 --
  stored, never read, or refusing any non-default value; only
  `short_option_equals_oparg` / `short_option_concatenated_oparg`
  gated real behavior, now permanently on.)
* **F6. `app.parameter(name)` / `app.argument(name)` now REQUIRE
  `usage=`** (v1: `usage=None` default).
* **F7. `read_csv(csv_reader=)` renamed to `reader=`;
  `parse()` changed from `parse(processor)` to
  `parse(args, config)`.**

Probed clean in round 2: chained `@app.command('db').command()`,
`@app.default_command()` (unchained), underscore command names,
`process()` called twice, tuple defaults, `**kwargs`, string
annotations (broken in v1 too), duplicate option strings across
converters (v1 also refuses).

## G. Round 3: the *args-group arity decision, and the chained
## registrar

A session decided *args converter groups must have a FIXED
per-instance argument count ("awaits the streaming driver").
v1 fills optional parameters greedily per instance.  Two faces:

* **G1. Windowed case refuses the program.**  `def color(hue='k',
  *, bold=False)` + `def draw(shape, *colors: color)`: v1 builds
  and runs it (`draw dot red --bold blue` -> red, blue-bold).
  1.0: AppealConfigurationError "optional parameter 'hue' in a
  *args converter group awaits the streaming driver (each
  instance's argument count must be fixed)".
* **G2. Unwindowed case SILENTLY MISBINDS.**  `def pair(a,
  b='B')` + `def draw(*pairs: pair)`, line `draw 1 2 3 4`:
  v1 -> `(('1','2'), ('3','4'))` (greedy fill); 1.0 ->
  `(('1','B'), ('2','B'), ('3','B'), ('4','B'))` (every instance
  clamped to required minimum, defaults filled, NO error).  Same
  line, different call -- the worst kind of break.
* **G3. `app.command('db')` returns a `_SubRegistrar` with THREE
  attributes (`app`, `command`, `parent`); v1 returns a full
  sub-Appeal (~40 chainable methods).**  Every v1 chained idiom
  except `.command()` dies with AttributeError:
  `.default_command()` (=F2), `.option()`, `.global_command()`,
  `.parameter()`, `.process()`, ...
* G4 (liberal, noted): a *args converter inside *args is refused
  by v1 ("label used twice"), accepted by 1.0.

## H. Larry's withheld breaks (2026-07-18): both fixed

* **H0 (break #1, Larry had to tell me): flags with a non-False
  default were REFUSED.**  build.py: "a flag's default must be
  False"--in v2 since the very first build (91b1134), my own
  argparse-shaped assumption that a flag stores True.  v1's rule
  (probed): presence stores `not default`, truthiness included
  (True->False, None->True, 0->True)--a default-True flag is how
  you spell "turn this default-on thing OFF".  No corpus test
  covered it, so it survived every migration gate.  **FIXED**:
  refusal removed; OptionRule carries `present = not default`;
  flag table entries carry it; both rungs store it on presence;
  explicit `=true/false` still sets the literal value; windowed
  flags inherit it per instance.  Differentially verified against
  v1 (all default shapes + windowed).  Hunt post-mortem: my nets
  probed option GRAMMAR shapes but never varied DEFAULTS--a whole
  axis missing; noted for the round-2 fuzz generator.

Standing rule (Larry, 2026-07-18): NOTHING else on this list
gets changed until we go over it together, top to bottom--some
of these he wants changed, some left alone.  The subcommand tree
(H2) is the ONLY fix in the tree.

* **H1 -- NOT Larry's break; NOT fixed.  On the discussion list.**
  I diagnosed the removal of "specified more than once"
  (last-one-wins, commit 6076e44 of July 12, citing a
  "ruled 2026-07-09" the session made to ITSELF) as break #1,
  restored the v1 error, and was wrong twice: it isn't the break
  Larry hit, and Larry ruled that nothing on this list gets
  "fixed" until discussed.  The restoration is reverted; 1.0's
  last-one-wins behavior stands, awaiting discussion.  Ledger of
  the same session's self-ruled repetition family, for that
  discussion: (a) single-valued options last-one-wins vs v1's
  error [6076e44; go2_6 golden edited; test_options_last_wins
  written to bless it; Larry's own 8b975e6 built the v1 error
  deliberately]; (b) `Option` class made repeatable (v1: at most
  once) + `StrictOption` invented for the old meaning; (c) flag
  `=true/false` explicit spellings (v2-new, interacts with (a)).
* **H2 (the API break): `@app.command('x')` no longer renamed.**
  A session repurposed `command()`'s first positional from v1's
  `name` to `parent=`, so `@app.command('x')` returned a
  `_SubRegistrar` -- "TypeError: '_SubRegistrar' object is not
  callable".  Larry's own `tron` uses `@app.command("list")` and
  `@app.command("import")` (an identifier CAN'T be named
  `import`--rename is the only spelling), so tron died at import
  the moment 1.0 was installed.  **FIXED** per Larry's ruling: no
  registrar objects, ever--`app.command('db')` returns the child
  **Appeal instance** (the command tree is a tree of Appeals,
  v1's model); `@app.command('x')` renames; redefinition
  replaces; chained `.command()` / `.default_command()` /
  `.option()` / everything works at any depth; `Appeal(parent=)`
  restored; per-set default commands wired through both rungs
  (root default now emits into standalone scripts too--it never
  had).

## I. Inference-from-defaults audit (2026-07-18; the axis the
## flag break exposed).  ACCUMULATED ONLY -- nothing fixed.

Method: a differential battery over (kind x annotation x default
x argv) shapes, 152 combinations, v1 0.6.4 vs 1.0, comparing
outcome, value, AND type.  22 raw divergences, clustering into:

* **I1. Positional parameters lost converter-from-default-TYPE
  for arbitrary types.**  v1: `def go(p=Path('/tmp/q'))` ->
  the user's value comes back as `Path`; any type(default) is
  the converter (`t=Tag('x')` -> Tag('hello')).  1.0: plain str.
  int/float defaults ARE preserved (both convert, same errors),
  and OPTIONS preserve the full rule (`--out` with a Path
  default -> Path; Tag -> Tag)--so 1.0 is internally
  inconsistent: same default, different semantics by parameter
  kind.
* **I2. Non-empty TUPLE defaults lost v1's inferred sequence
  converter.**  v1: `def go(pair=(1, 2))` consumes TWO operands,
  each int-converted (`go 7 8` -> (7, 8)); same for options
  (`--pair 7 8`).  1.0: one optional str operand (positional) /
  zero opargs (option)--the v1-legal lines error with wrong-
  count.  Non-empty LIST defaults are preserved (`items=['a',
  'b','c']` consumes 3)--the tuple/list asymmetry is 1.0's.
* **I3 (liberal, note only).**  Empty-container defaults ((),
  [], {}): v1 refuses at build ("can't infer"); 1.0 accepts as
  an optional str operand with the container filling when
  absent.  Dict defaults: v1 CRASHES (AttributeError, a v1
  bug); 1.0 accepts.  No v1-legal program is affected.
* **Preserved (verified same, 130 combinations):** default None
  -> str value when given (Larry's documented example); str
  defaults; int/float defaults converting with same error
  shapes; bool defaults (flags, after H0); list defaults as
  sequences; annotation-beats-default interplay for str/int/
  float/Path; option Path/custom-class defaults.

## J. Axis sweep (2026-07-18): batteries A-G, v1 0.6.4 vs 1.0.
## ACCUMULATED ONLY -- nothing fixed.

* **J1. `main()` no longer exits the process.**  RULED and
  FIXED (Larry, 2026-07-19, review item J1): **main() EXITS
  again** (0.6.4's contract)--bare `app.main()` scripts (tron,
  cq, utimify) report their codes to the shell.  Usage errors
  exit 2 (getopt/argparse convention, chosen over 0.6.4's -1);
  a command's nonzero int is the code; empty line exits 1;
  process() remains the returning API.  Verified end-to-end from
  the shell.  Tests: test_main_exits_the_process.
* **J2. Errors moved from stdout to stderr.**  v1 prints "Error:
  ..." to STDOUT; 1.0 prints "error: ..." to stderr (the
  constructor comment even records "sys.stdout is v1's
  behavior").  Deliberate, documented, still a divergence
  pipelines can notice.  Related: usage-error exit code -1 -> 2;
  empty command line v1 error+exit -1 -> 1.0 listing+exit 1
  ("ruled 2026-07-09", git-style--another session self-ruling).
* **J3. `complex` converter lost.**  v1: `n: complex` parses
  '1+2j' (SimpleTypeConverterComplex).  1.0: usage error (complex
  isn't special-cased; its (real, imag) signature reads as a
  2-operand converter).
* **J4. Uppercase parameter long-option casing.**  `*, Verbose`:
  v1 derives `-V|--verbose` (lowercases the long); 1.0 derives
  `-V|--Verbose` (preserves case).
* **J5 (design note, no v1 oracle).**  1.0's `read_csv` without
  first_row_map DISCARDS the first row as headings--a headerless
  CSV silently loses its first record.  v1's read_csv raises
  TypeError (broken), so this is 1.0's own design to review, not
  a preservation question.
* **Liberal (v1 broken or refusing; 1.0 works; note only):**
  read_mapping with flags/defaults/extra keys (v1
  ConfigurationError/RuntimeError), read_iterable (v1 TypeError),
  functools.partial and instance-`__call__` commands (v1
  AttributeError), functools.wraps-wrapped commands (v1 parses
  the wrapper's *args), v1's `repeat=` (probed: the subcommand's
  own leftover check fires before the cycling loop--v1's cycling
  NEVER worked; 1.0's is a rebuild).
* **Verified preserved:** Option/MultiOption protocol
  (init/option/render, arities 0/1/2, optional opargs, defaults
  through init, MultiOption accumulation)--everything except the
  known repetition family; option-string derivation (dry_run ->
  --dry-run, single-char no-long, digits, trailing underscore,
  x_y_z, default_long_option/default_short_option policies);
  read_mapping nested + flat spellings, already-typed values,
  int->float; @app.option blow-away/stacking/annotation=/
  default=; @app.parameter usage renames; positional-only `/`;
  lambda/classmethod/str.upper converters; nonzero-int
  early-exit and cycling contracts (against 1.0's own docs; v1
  oracle broken here).

## Reclassified after Larry's rulings (2026-07-18)

* **F1 (docstring format): NOT a break.**  Larry: the new
  `Arguments:` grammar is desirable, keep it, do not restore
  `[[arguments]]`.  Remains listed as a documented 1.0 change.

## C. Message/format divergences (same outcome, different text)

* C1. Count errors: v1 "str_i_f requires 2 arguments in this
  argument group." vs v2 phrasing (three goldens).
* C2. Multi-param Option-class scalar rejection message.
* C3. Usage lines: `<metavar>` style and unit-wrapping
  [Larry-ruled July: "[-t|--times <int>]", wrap at whole units].

## Excluded as harness artifacts

* NewStyleTests argv0 mismatch (expects the runner's own script
  name).

## Rounds still to run

2. **Differential fuzz resurrection**: site-packages has 0.6.3,
   git has 0.6.4; rebuild the retired fuzz harness, run at
   volume, and extend the generator into post-July feature space
   (scoped windows, greedy operands, config injection) that the
   corpus never covers.
3. **The edit register**: diff `33ffcce:tests/test_all.py`
   against today's `tests/test_v1.py` -- every golden edit, skip,
   and reinterpretation any session ever made, classified
   [Larry-ruled + quote] / [session-decided, needs ruling] /
   [accident], with claimed rulings verified against the actual
   transcripts in ~/.claude/projects/.
4. **New-surface risks** (v1 never had these; they can't break v1
   code but can surprise): auto `version` command word when
   `version=` is set; `default_options` hook; `script=`,
   `errors=`, `theme=`, `repeat=`, `help=` constructor semantics.
5. **Larry's real scripts** as acceptance tests, wherever they
   live.

## Validation protocol

Larry found two breaks by normal use and is withholding them.
If both appear above, the method may be sound; if either is
missing, the net has holes and rounds 2-5 must widen.
