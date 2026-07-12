# Positional option scope (proposal, rev 2)

*Rev 1 designed around a counting/scoping circularity and a
shadowing feature.  The probes killed both: this revision is
smaller, and ends in a decision that's Larry's to make.*

## What the probes established (shipping v1 0.6.4)

1. **Scopes open progressively and never close.**  A group's
   options become recognizable when the parse reaches the group's
   position, and stay recognizable to end of line:
   `f(a: sub1, b: sub2)` accepts `1.5 2.5 --s1` (sub1's flag long
   after sub1 finished) and `1.5 --s2 2.5` (sub2's flag the moment
   sub1 is done), but rejects `--s2 1.5 2.5` (sub2 not yet
   current).  Recognition is *monotone*--no stack needed, just an
   opening position per group.
2. **Shadowing does not exist in v1.**  The same option string in
   two scopes--same arity or different--is a `ConfigurationError`.
   v2's current global-uniqueness rule IS v1's rule; rev 1's
   "shadowed strings must agree in arity" machinery is designing
   for a feature v1 never had.
3. Consequently **the circularity evaporates**: with one meaning
   per option string, flat tokenization is exact.  Counting and
   meaning never disagree.

## The remaining delta, and it's small

v2 today recognizes every option anywhere on the line (flat).
v1 additionally *rejects an option that appears before its group's
opening position*.  That check is the entire feature:

* `parse_tokens` records, per occurrence, how many operands had
  been consumed when the option appeared.
* Each fill function already knows the operand index at which its
  group started.  The check--"every one of my options was given at
  or after my start index"--compiles to one comparison per option,
  in both rungs.  Force-entry composes: a forced group checks its
  options against the position it would have opened.

No driver rewrite.  No scope stack.  The counting automaton, the
tables, and the generated code shape all survive.

## Ruled by Larry (July 3): history and the *args windows

The monotone behavior the probes found was v1's *second* design.
The first was push/pop scope--`--bold` legal only while "inside"
size, taken away on leaving--which worked but was complicated and
unexciting; it was deliberately abandoned for "easy breezy: once
you've entered the group, its options are legal forever after."
This is new ground; there are no outside rules to obey.

**The window rule survives in one place: repeated groups.**  With
`def draw(shape, *sizes: size)`, each size *instance* gets a
window: it opens after the instance's first operand and closes
when the *next* instance begins; the last instance's window runs
to end of line.  `--bold` binds to the instance whose window
contains it.  (If size took width/height/depth, `--bold` may sit
between any of them.)  This is required machinery for converter
groups on `*args`--currently refused by name in v2--because there
the SAME option string legitimately recurs, and position is the
only thing that can disambiguate instances.

Also confirmed: one option string, one meaning, per command line
stays (both v1 and v2 refuse duplicates at build time).  This is a
*command-line flatness* constraint, not a metaphor constraint--see
read_mapping below.

## read_mapping and duplicate names in the schema

No spit-up.  The option-string uniqueness rule exists because a
command line is one flat token stream--`--x` must mean one thing.
A mapping is a *tree*: each converter reads its own nesting level,
so values are addressed by path, not by a global name.  Two
branches of the schema may each have a parameter named `x`;
Python already guarantees uniqueness where it matters (one level =
one signature).  Implementation consequence, recorded for when
read_mapping lands in v2: the collision check lives in the
*command-line* compilation path (`_finalize_options`), never in
`build()` itself, so mapping/iterable consumers of the same Plan
tree don't inherit a constraint that only flat token streams need.

## Ruled (July 3, later): liberal recognition + gates + windows

Larry ruled: `draw dot --bright` and `draw --bright dot` are legal
(liberal recognition for skippable territory), with two conditions.

1. **Windows on *args are real semantics and must work** -- DONE,
   probed and shipped.  Discovery: shipping v1 binds *forward*
   (`a 1 --bold 2` bolds #2, the instance about to be built;
   a trailing `--bold` forces a new instance and errors, never
   default-fills), which contradicts Larry's recollection
   (window opens after the instance's first operand; trailing
   binds to the last instance).  v2 implements SHIPPING v1
   behavior, validated by differential fuzz; flip to the
   remembered postfix rule is one index computation away if
   Larry re-rules.  Also chosen: an occurrence before the
   group's first operand clamps to instance #1 (liberal-
   consistent; v1 unprobed there).
2. **Gates -- DONE (confirmed by Larry: "walls are required
   parameters"), with one refinement the differential fuzz
   forced.**  A group certain to consume operands (required all
   the way up, minimum > 0) is a wall; options of *skippable*
   groups behind it wait until it is fed.  The refinement: the
   first draft gated ALL options behind a wall, and the fuzz
   found shipping v1 accepting an early option of a required
   nested group--v1's real model is that certain groups' options
   float free (they announce something guaranteed to exist),
   while skippable groups' options open positionally.  So the
   gate applies only to skippable-group options, which is
   exactly Larry's --bright example and keeps every v1-working
   line working (validated: six seeds, 4,050 comparable parses,
   zero incompatibilities).  Implementation: parse_tokens
   records first-appearance positions; fills thread a gate index
   forward past barrier slots; skippable groups' fills check
   their options against it; windowed occurrences check inside
   window_options.  Barrier-free commands emit exactly the same
   code as before--the machinery costs nothing unless a wall
   exists.

## The decision (Larry's)

With shadowing banned, an early-placed option is never ambiguous--
`--dashed dot 2.5` has exactly one possible meaning.  So position
enforcement buys v1 error-parity, not correctness.  And completion
does not need it either way: "what's legal at this cursor?" is
answered by the opening positions, which we can compute whether or
not the *parser* enforces them.

* **Option A -- keep flat recognition** (current v2): every line v1
  accepts parses identically; v2 additionally accepts early
  placements.  The documented strict-superset story stays; the
  grammar doc's "destination: positional scope" line is retired as
  a non-goal.
* **Option B -- enforce v1 positions**: exact v1 parity, including
  its rejections.  The differential fuzz's "v1 error, v2 ok"
  bucket shrinks toward zero (those gains were mostly early
  placements).  v2 gets *stricter* than it is today.

Either way, **completion groundwork** (opening positions exposed on
the plan) and **flexible global operands** (the command-word split
as an ordinary group boundary) proceed identically.

## Staging (after the ruling)

1. Expose per-group opening positions on the Plan (build-time).
2. Option B only: record positions in `parse_tokens`, emit the
   comparisons in interpreter + codegen, update the two liberalization
   tests, re-run the differential fuzz (expect gains to shrink).
3. Flexible global operands: replace `validate_global_plan`'s
   fixed-count rule with the automaton over the global plan, using
   the command word as a barrier.  (Independent of A/B.)
4. Completion: a `complete(plan, tokens, cursor)` entry point over
   the same tables--options open at cursor, expected operand type.
