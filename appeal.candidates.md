# Candidates: where I built around a missing piece instead of building it

*For Larry's review, 2026-09-08.  Each entry is a place where, as far
as I can tell, I solved a problem locally--a workaround, a special
case, a heuristic, a string standing in for structure--rather than
giving the code the capability it needed or bringing the gap to you.
You judge each: keep, fix, or reverse.  The list is incomplete by
construction: it's what I can see from inside the pattern, and the
appendix of dated rulings is where the rest of the iceberg is most
likely to be.*

The Astra review's angle-bracket finding is the model: the usage line
was rendered, then parsed to find metavars, when the metavars were
sitting in the Plan the whole time.  That one is fixed.  These are the
ones that look like it, in one way or another.


## A: Structure re-derived from rendered text or string encodings

**A1. The availability message hard-codes the `<NAME>` decoration.**
`backend._operand_list` renders required operand names as `<A> <B>` to
say "`--flag` only becomes available if you specify <X> <Y>".  That is
the *default* argument decoration; a program that sets its own
(`argument_decoration` in the stylesheet, `host` rather than `<HOST>`)
gets a message that doesn't match its usage line.  The general thing:
the message names operands the same way usage does, through the plan's
decoration, or carries the names as roles and lets the sheet decorate.

**A2. Duplicate option displays are detected by comparing rendered
text.**  `presentation.merge_docs`, the position-qualifier pass: to
decide whether an option row needs "(after <A>, before <B>)", it
strips the style spans off each row's display string and counts equal
strings.  The plan knows which rules share strings (`scoped_keys`);
the text comparison rediscovers it, and would misfire if two different
rules ever rendered alike.

**A3. The man page strips role spans back out of displays.**
`presentation.man_page`'s `opt()` takes a display already rendered as
a styled span, strips the spans, then escapes for troff.  Since R08 the
table rows are structural; the usage line still arrives as a span
string, so the man page un-renders it.  The general thing: a plain
(unstyled) rendering of usage that both troff and tests can use.

**A4. (Reduced, 2026-09-08.)**  `BoundInnerPlan.attribute` is a field
now, and the backend's `__call__` asks the plan.  The field is still
derived from the qualname's last segment at build time, once; whether
big's BoundInnerClass exposes the name any other way is for you to
say.

**A5. The execution log finds a subcommand's callable by name.**
`Processor._command_for`: the log records `(command, instance)` pairs
but a `_Step` doesn't remember which node it came from, so a bare word
is looked up across every parent's subcommand list and answers None
when ambiguous.  A step knows its node at the moment it's made; record
it and the search disappears.

**A6. Config vetting rebuilds every command to say where a key lives.**
`_config_vet`, the "say where the key actually lives" loop: for an
unknown key, it builds every command's plan inside `try/except
Exception` to find a positional or option of that name.  The same
question is now answered for options by `_option_owners` (built for
the misplaced-option error); the two should be one table, and the
except should go.

**A7. Windowed and scoped options are encoded as string prefixes in the
completion table.**  `OptionRule.table_entry` prefixes the kind with
`'w:'` for a windowed option; `completion.py` decodes with
`kind[:2] in ('w:', 's:')`.  A structural fact (this option belongs to
a `*args` window) is written into a string and parsed back out.  The
general thing: a field.


## B: Heuristics standing in for a fact the plan could carry

**B1. `_help_enabled` is an admitted approximation.**  `Appeal.__init__`:
"the legacy bare-app help machinery still keys off this flag;
approximate it until the era unification lands".  The era unification
landed.  The flag is derived from `default_mappings is not None` and
still gates help behavior in places the metadata precommand should own.

**B2. Hand-coded near-miss suggestions for `default_mappings`.**
`default_global_mappings()` carries a literal dict, `{'-v': '-V', '--h':
'--help', ...}`, of misspellings it anticipates.  Everywhere else
suggestions come from `did_you_mean`.  Either use it here too or drop
the hint.

**B3. BoundInnerClass support is a probe and a re-fetch.**
`build_plan` binds the wrapped class to a throwaway `_Probe` instance
to read its grammar, under `except Exception: grammar = callable`; at
run time `Converter.__call__` re-fetches the descriptor off the real
parent instance (A4), and a comment in the method-command branch calls
the BIC case a "known-unported edge".  It works, and tests cover it,
but it's three special cases holding hands around a wrapper Appeal
"doesn't know".  Worth deciding whether Appeal should know it (import
big's BoundInnerClass by name and handle it directly) or not support
it.

**B4. The availability message reconstructs which option was typed
from the kwargs dict's first key.**  `_availability_message`: "when
kwargs is non-empty, its first key is the option the user actually
typed".  That is true today because of insertion order and because a
conjure writes its kwarg on the spot.  It is an inference about an
event from the state it left behind.  The general thing: the binding
records the spelling it was invoked by (it already receives it as
`spelling`) on the summoned instance.

**B5. (Not a candidate, noted for completeness.)**  The dash-digit
rule in `is_option_token` (bundle if it carves, else operand if it
floats, else option) is your heuristic from 2026-08-24, kept as ruled.


## C: Ad-hoc state flags on Converter instances instead of a model

**C1. The `make -j` oparg-group behavior lives in six instance flags.**
`_optarg`, `_attached`, `_opt_display`, `_optarg_root` (19 uses in
`backend.py`) plus `_summoned` and `_window` (8 uses) are set on
Converter instances from outside, by whichever code path built them,
and consulted deep inside `_fill_argument`.  Each flag is a mode: "this
group is an option's operand, grab unconditionally", "this instance
was conjured", "this instance is a `*args` element".  The modes are
real, but they're expressed as attributes poked onto objects after
construction, and the fill routine branches on all of them.  The
general thing is a decision on where a mode belongs: on the
instruction that created the instance (so the fill reads its
instruction), or in a small class per mode.  This is the largest
single item on the list and the one I'd most want you to read
`_fill_argument` with in mind.


## D: Defensive lookups on our own objects

*(2026-09-08: the plan-side ones went with the plan varieties--
`windowed`, `explicit`, `repeat`, `maximum`, `options` are read
directly now, and Terminal is never duck-typed as a Plan.  The
completion and `__init__` entries below are also gone.  Left: the
`__origin__`/`__args__`/`completions`/`factory` probes on user
annotations, which are genuinely optional attributes.)*

The no-defensive-programming rule is yours, and these violate it in a
way that also hides a design smell: `Terminal` and `Plan` are both
used as a slot's child, and code that doesn't want to branch on which
asks with `getattr(child, 'maximum', None)`, `getattr(child, 'options',
())`, `getattr(s, 'repeat', False)`, `getattr(owner, 'windowed',
False)`, `getattr(option, 'explicit', False)`.

* `frontend.py:1827` (`repeat` on a child's slots)
* `frontend.py:2011`, `2013` (`windowed` on an owner)
* `frontend.py:2037`, `__init__.py:509-510` (`explicit`, `auto_shorts` on
  an option rule: both are always set in `OptionRule.__init__`)
* `frontend.py:2201`, `2203` (`maximum`, `options` on a child that might
  be a Terminal)
* `completion.py:87` (`windowed` on an owner)

Each is one of: an attribute that is always present (so the default is
dead), or a Terminal being treated as a Plan with no options and no
maximum (so the two types want a shared interface, or one isinstance
branch).


## E: Broad excepts

Two are deliberate and I'd keep them: the REPL's completer swallows
everything (a completion bug must not kill the session) and the REPL's
command runner prints a traceback like Python's REPL; MCP's tool call
turns any exception into a protocol error and keeps serving.  Two are
candidates:

* `frontend.build_plan`, the `_Probe` binding (B3): `except Exception:
  grammar = callable` hides whatever the descriptor raised.
* `_config_vet` (A6): `except Exception: continue` hides a
  configuration error in an unrelated command.


## F: Markdown handled twice

**F1. The README-grade transforms are a second Markdown
implementation.**  `to_github` and `to_commonmark` are textual: they
find definition lists by line shape, track fences, and rewrite with
regexes (`_transform_definition_lists`, `_strip_strikethrough`,
`_alerts_to_commonmark`).  This was kept textual by ruling, but big
now has `render_document` with GFM, CommonMark, and troff back ends
over its parsed document.  The Appeal transforms could be deleted in
favor of parse-then-render through big, which would also retire the
fence tracking I added for R08.

**F2. The docstring scanner re-implements big's definition-list
rules.**  `_parse_definition_list` mirrors big's content-column rule so
nested definitions survive (R08).  The strict opener rule is yours and
is cheap to check on lines; the definition-list body, though, is
parsed here and then parsed again by big when the help page renders.
Astra's suggestion, extracting the special sections from big's parsed
document instead of from lines, would leave one parser.  I didn't do
it because the ruling asked for the strict line rule, which is
compatible with either.


## G: String keys that carry structure

`plan.constructs` and `plan.binds` are qualname strings used as keys
into the per-run `env` dict (class-as-app: a constructing step stashes
its instance under the string; a method step reads it back).  It
works, and it's how the tour describes it.  It also means A4.  Noting
it so you can decide whether the env should be keyed by plan identity.


## H: Things I'd want you to read with fresh eyes, no specific defect

* `_run_node` in `__init__.py`: the dispatcher grew by accretion
  (eras, bleed relay, `--` state, the `tried` pool, default and
  listing).  It's correct under the suite but it's the densest
  function in the tree.
* `_build_class.register` in `backend.py`: the instruction layout,
  including the `boundary` insertion index that lets a conjurable
  slot's PreOption "leap over optional leaves".  The comment explains
  it; whether the *rule* is what you want is a question for you.
* The "ruled" comments in the appendix.  Where a comment says "ruled
  <date>" without "Larry", it is most likely a decision I made in a
  session and wrote down as settled.  Some of those you have since
  confirmed; the ones you don't recognize are yours to reopen.


## I: Back-pocket designs (agreed feasible, deliberately not built)

* **The emergency brake: two-stage parsing** (Larry, 2026-09-09,
  from the feature-parity review's item 2).  An immediate precommand
  such as `--plugin X` raises a brake exception; Appeal stops, and
  returns the tokens no executed step consumed, in original order, as
  a list of strings.  The program maps more (the plugin registers
  commands and options on the same Appeal), then calls
  `app.main(remaining)`.  What it needs, none of it in the way of
  today's code: tag each token with its original position when the
  line comes in (the `--x=y` split, the bundle carve and the
  trailing-operand lift all carry the token object, so the tag rides
  along); a per-converter ledger of token ids taken (a step's engine
  can take a token for a neighbouring era's converter, so the ledger
  is per converter, not per engine); the exception caught in the
  execute loop.  Two rules that fall out: the braking precommand must
  be immediate (structural problems are raised after immediate eras
  run, so a pass-3 brake would lose to an unknown option later on the
  line), and a bundle is all or nothing.  What must stay true for
  this to remain cheap: one engine per era with explicit consumption
  (no whole-line re-tokenizing), and structural problems deferred
  until after immediate eras.  Not important enough to build now.

* **Help groups for options** (Larry, 2026-09-09, parity review item
  20; not sure, not for 1.0).  A nicety: `app.option(..., group=
  'Output')` tags an option, and the help's `# Options` definition
  list renders under group headings, argparse's argument groups /
  typer's help panels.  Where it lives when built: the decorations
  registry already carries per-parameter presentation metadata (the
  metavar rename), so a group tag is one more decoration; the
  renderer sorts the definition list by it.  Nothing assumes options
  are one flat list, so nothing to keep in mind beyond not adding
  such an assumption.

* **Localization of parser messages** (Larry, 2026-09-09, parity
  review item 21; deferred, wanted if Appeal gets popular).  gettext
  over the messages the *user* sees: 31 UsageError sites, each an
  f-string today, each becoming a looked-up template with named
  holes.  The help page's fixed headings already come from the app's
  templates; `usage:`/`error:` are one string each.  Configuration
  errors are for the author and stay English (argparse's do too).
  What to keep true meanwhile: messages are whole sentences with
  named holes, never concatenated fragments, since translations
  reorder words.

* **Lazy commands** (Larry, 2026-09-09, parity review item 22; wanted
  as a first-class feature, punted for now).  Larry's spelling:

      @app.lazy()
      def foo():
          import massive_module
          return massive_module.foo

  The word comes from the thunk's name as usual; every other piece
  of metadata--the signature, the docstring, the options--comes from
  the real function (Larry: one source of truth, the thunk carries
  nothing but the name).  So invoking another command, and
  completing command names, never resolve the thunk; the bare page's
  listing does, since a summary is the docstring's first paragraph.
  A thunk may return a class, whose subcommand tree is then built
  lazily with it.  Nothing today introspects a node's body before
  its plan builds, so nothing to keep in mind beyond that.


## Appendix: every dated "ruled" comment in the code

45 dated 'ruled' comments, oldest first.  'Larry' in the text means the comment credits you; the rest are mine unless you recognize them.

- `appeal/__init__.py:605` (2026-07-09): refused BY DESIGN (ruled 2026-07-09): position is
- `appeal/__init__.py:2764` (2026-07-09): ruled 2026-07-09).  A global command runs as a head era regardless
- `appeal/converters.py:49` (2026-07-09): An Option may be given any number of times (ruled 2026-07-09:
- `appeal/frontend.py:533` (2026-07-19): `usage: prog go ...` (0.6.4's shape, ruled 2026-07-19)
- `appeal/__init__.py:123` (2026-07-25): names"--and kept them as aliases; ruled again 2026-07-25:
- `appeal/__init__.py:1130` (2026-07-25): the v1 help= knob is dead (ruled 2026-07-25):
- `appeal/__init__.py:1329` (2026-07-25): one layer down; ruled 2026-07-25).  On the root, the
- `appeal/__init__.py:2020` (2026-07-25): (ruled 2026-07-25), and a command's return value is its
- `appeal/frontend.py:1039` (2026-07-25): same meaning as everywhere (ruled 2026-07-25):
- `appeal/frontend.py:1588` (2026-07-25): zero strings is legal (ruled 2026-07-25): "I'm speaking
- `appeal/frontend.py:1749` (2026-07-25): @app.option maps STRINGS (ruled 2026-07-25, the
- `appeal/__init__.py:299` (2026-08-01): Templates (Larry's single-template model, ruled 2026-08-01).
- `appeal/__init__.py:1216` (2026-08-01): (ruled 2026-08-01): doc= beats the global command's
- `appeal/presentation.py:1297` (2026-08-01): or the shared module's docstring, ruled 2026-08-01)
- `appeal/converters.py:387` (2026-08-03): (ruled 2026-08-03, the make precedent: `-f file` is the
- `appeal/__init__.py:1337` (2026-08-04): "The default command's function (None if unset; ruled 2026-08-04)."
- `appeal/__init__.py:1366` (2026-08-04): "This node's own Plan (the root: the global plan; ruled private 2026-08-04)."
- `appeal/__init__.py:115` (2026-08-05): wired, ruled 2026-08-05.)
- `appeal/__init__.py:303` (2026-08-05): (the Markdown pivot, ruled 2026-08-05); the text between
- `appeal/__init__.py:1885` (2026-08-05): (help's knobs, ruled 2026-08-05): bound methods
- `appeal/__init__.py:2028` (2026-08-05): completion(shell).  Formats (ruled 2026-08-05): 'gfm'
- `appeal/presentation.py:971` (2026-08-05): THE DOCSTRING IS MARKDOWN (the pivot, ruled 2026-08-05;
- `appeal/presentation.py:1007` (2026-08-05): the headings (ruled 2026-08-05): nothing to present
- `appeal/__init__.py:34` (2026-08-06): Appeal REQUIRES big (ruled 2026-08-06) for its help/usage rendering.
- `appeal/__init__.py:1211` (2026-08-06): StyleSheet, used VERBATIM (ruled 2026-08-06); the
- `appeal/presentation.py:615` (2026-08-06): (ruled 2026-08-06).  Auto composes appeal_theme over the
- `appeal/presentation.py:815` (2026-08-06): declares them (ruled 2026-08-06).  `role` (e.g. 'command')
- `appeal/presentation.py:402` (2026-08-08): the renderer injects `line` (ruled 2026-08-08): the margin
- `appeal/presentation.py:727` (2026-08-08): clip/fill (ruled 2026-08-08).  Only the renderer knows the
- `appeal/__init__.py:1173` (2026-08-09): by the decorated callable (ruled 2026-08-09: Appeal
- `appeal/__init__.py:1178` (2026-08-10): decoration and resolved lazily (ruled 2026-08-10:
- `appeal/__init__.py:1679` (2026-08-10): rule, ruled 2026-08-10: commands are sentences about the
- `appeal/__init__.py:1770` (2026-08-10): Membership derivation (ruled 2026-08-10): a registered
- `appeal/backend.py:496` (2026-08-16): validates EVERY value (ruled 2026-08-16, "not called validate for
- `appeal/__init__.py:1202` (2026-08-22): cycling is PER NODE (ruled 2026-08-22): `repeat` on a node means its
- `appeal/__init__.py:1235` (2026-08-22): NO lock (ruled 2026-08-22): builds are idempotent and cache installs
- `appeal/__init__.py:1254` (2026-08-22): lock-free lazy caches (ruled 2026-08-22: no Lock).  Builds are
- `appeal/frontend.py:577` (2026-08-24): rides the same decoration (ruled 2026-08-24: one uniform
- `appeal/__init__.py:566` (2026-08-29): strict=False (ruled 2026-08-29, the rc-file-adaptation case)
- `appeal/load.py:358` (2026-08-29): tree claims raises (ruled 2026-08-29: fail loud by default);
- `appeal/__init__.py:2945` (2026-09-03): (ruled 2026-09-03, the Sol review): a command's failure,
- `appeal/frontend.py:579` (2026-09-03): ruled 2026-09-03).
- `appeal/presentation.py:443` (2026-09-03): The SHAPE is the caller's stylesheet entry (ruled 2026-09-03:
- `appeal/__init__.py:552` (2026-09-06): unknown command earns (ruled 2026-09-06).
- `appeal/frontend.py:2156` (2026-09-07): starve pair; ruled exact 2026-09-07).  Trailing operands are
