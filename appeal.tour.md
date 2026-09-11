# A guided tour of the Appeal 1.0 implementation

*For Larry, who wants to read the code and needs the map first.
Rewritten 2026-09-08 against the tree as it stands that day; every
name, count, and example below was checked against it.  Where the old
tour (2026-08-25) described a two-pass dry/live engine and Conjure*
bindings, that engine no longer exists; this document replaces it.*

This is a reader's document, not a reference.  It defines each term
before it uses it, and ties each mechanism to the command line it
exists for.  Sections build on each other; read them in order the
first time.


## 0: The shape of the thing

There is one path from a command line to your function, and it has
three stages:

1. **Front end** (`frontend.py`): read a callable's signature, produce a
   **Plan**.  A Plan is plain data describing the grammar: which
   positional operands the callable takes, which options, in what shape.
   No argv is involved.  This runs once per callable per process.

2. **Back end** (`backend.py`): turn a Plan into a **Converter class**,
   then run an **Engine** that walks argv against it.  This is the
   parser.  The Engine does not call your function while it parses; it
   records what it found, and calls your function afterward.

3. **Dispatcher** (`__init__.py`): the `Appeal` object is a tree of
   command words; `Processor` runs one command line by walking that tree,
   asking the back end to parse each piece, then executing the pieces in
   order.

Everything else is a consumer of the Plan: `presentation.py` renders
help, usage, and errors; `completion.py` answers shell tab completion;
`load.py` runs a callable from a dict or a CSV row instead of argv;
`schema.py` describes the Plan as JSON-safe data; `mcp.py` serves
commands as MCP tools.  None of those are imported until something asks
for them.  `import appeal` loads `__init__`, `frontend`, `backend`, and
`converters` and nothing else, not even from the standard library.

A word about the word **converter**, which this codebase uses in two
senses and you will have to hold both:

* A *converter* in the user's sense is any callable that turns
  command-line strings into a value: `int`, or your `def point(x: int,
  y: int)`.  Annotating a parameter with one says "this parameter is
  filled by running that callable on the next operand(s)".
* A *Converter* (capital C, `backend.Converter`) is a generated class,
  one per Plan, that the Engine parses *into*.  An instance of it holds
  the operands and options found so far for one use of one callable.
  When the parse is done, calling the instance calls your callable.

When the text says "the converter's Plan" it means the Plan built from
the user's callable; "a Converter instance" always means the generated
object.


## 1: The front end: a signature becomes a Plan

`build_plan(callable)` is the entry point.  For

```python
def serve(host, port: int = 8080, *, verbose=False, retries: int = 1):
    ...
```

it produces a `Plan` with two **slots** and two **option rules**.

* A **Slot** is one positional parameter: its name, whether it's
  required, its default, and its **child**.  The child is either a
  **Terminal**, wrapping a one-string-in callable (`str` for `host`,
  `int` for `port`), or another Plan, when the parameter's annotation
  is itself a callable that takes operands (a **group**; more below).
  `repeat=True` marks a `*args` slot.

* An **OptionRule** is one keyword-only parameter: its option strings
  (`('-v', '--verbose')`), the parameter name it fills, and its
  **kind**: `flag` (presence stores a value, consumes nothing), `value`
  (consumes one operand and converts it), `fold` (repeatable: a
  counter, an accumulator, a mapping), `nullary` (a zero-argument
  callable that presence *calls*), or `group` (an option whose
  annotation is a callable taking operands of its own).

The interesting case is a **group**.  Given

```python
def point(x: int, y: int): return (x, y)
def draw(shape, spot: point = None, *, bold=False): ...
```

the slot `spot` has a child Plan built from `point`, with its own two
slots.  Plans nest to any depth, and a Plan is built once per callable
per process and shared: `def line(a: point, b: point)` has two slots
pointing at the *same* `point` Plan.  (That sharing is why the
documentation code identifies rows by their path from the root rather
than by the Plan object; section 8.)

**The varieties.**  `Plan` is a small hierarchy (your design,
2026-09-08), each variety differing in how it's built and in one
method, `__call__`, its calling convention:

* `SignaturePlan`: read from a callable's signature; the ordinary case.
* `ClassPlan`: a class as a command; calling it constructs the
  instance, stashed under `constructs` for its methods to bind to.
* `MethodPlan`: a method as a command; the call supplies the instance
  stashed under `binds` as `self`.
* `BoundInnerPlan`: big's BoundInnerClass; constructs through the
  parent instance's attribute.
* `WindowPlan`: a `*args` converter group; `windowed`, options bind by
  window.
* `IterablePlan` (`TuplePlan`, `ElementsPlan`): the operands become a
  tuple or list; nothing is called.  Its `key` carries its element
  shape, since every `tuple[...]` shares the callable `tuple`.

`OptionRule` is one too: `Flag`, `Value`, `Fold` (and `FoldOnce`),
`Nullary`, `GroupOption`, each answering what it consumes and what the
backend binds its strings to.  Consumers ask the rule instead of
branching on a kind string.

**What the front end decides**, all of it in `frontend.py`:

* `signature()` reads parameters off `__code__` and `__annotations__`
  directly, so the fast path never imports `inspect` (7 ms).
* `Plan.child_for` classifies each parameter: terminal or group,
  `*args`, option kind.  Builtin generics are handled by feature
  detection (`list[int]`, `X | None`); the `typing` module is not
  consulted.  `OptionRule.build` does the same for options.  A `Build`
  object carries the context (the memo of plans built, the cycle
  stack, the decorations) that every constructor needs.
* `Plan.finalize_options` assigns option strings: a parameter's long
  option is its name with underscores turned to dashes, and a short
  option is proposed from the first letter and claimed if free.  Within
  one command every string has exactly one owner.  A string declared by
  two *sibling* groups (`a: point, b: point` both have `--flag`) is
  **scoped**: it's legal, and which window it binds to is decided by
  position on the line.
* `Plan.analyze` computes the operand-count footprint: `minimum`,
  `maximum`, and `valid_counts`, the exact set of operand counts the
  fill rule accepts.  It gets that set by *simulating* the Engine's
  fill rule on counts (`greedy_body`), so the number in an error
  message cannot disagree with what the parser does.
* `Plan.promote_optionality`: an optional operand followed by a
  required one can never actually be skipped, so it becomes required.
  This is the rule you named **fill left to right**: a slot fills
  whenever an operand remains; an optional is skipped only to leave
  room for a required operand after it, never to reach a *later*
  optional.

A Plan also carries the era flags (`share`, `immediate`; section 5)
and, once the back end has built it, `compiled`: the
Converter class.  The back end reads *only* the Plan.  It never looks
at annotations, signatures, or attributes of the callable.  If it needs
a fact, the fact is put on the Plan.


## 2: The back end, part one: a Plan becomes a Converter class

`converter_for(plan)` returns the Plan's Converter class, building it on
first use and caching it on `plan.compiled`.  The class is small.  It
has:

* `converter`: the user's callable, wired on as a class attribute.
* `register(self, engine)`: the method that lays down the Plan's
  **instructions** into an Engine (next section).
* `args` and `kwargs` on each instance: where parsed operands and
  options land.
* `__call__`: finish every value in `args` and `kwargs`, then hand them
  to the plan, whose `__call__` is the variety's calling convention.
  This is the moment your function runs.

`_build_class` writes `register` from the Plan, in memory, once.  There
is no generated source anymore; the class is built by ordinary Python
closures.


## 3: The back end, part two: the Engine

The Engine is the parser.  Hold this picture:

* An Engine owns a slice of argv, a **stack** of instructions, and a
  **handler table** mapping option strings to **bindings**.
* An **instruction** says what the parser expects next.  There are
  four: `ArgumentInstruction` (a positional operand goes here),
  `OptionInstruction` (install this option's binding), `PreOptionInstruction`
  (install a binding for an option that belongs to a group that hasn't
  been entered yet), and `RepeatInstruction` (a `*args` template: re-lay
  these instructions for each element).
* A **binding** is the object that fires when an option token is seen.
  There are four kinds, matching the option kinds: `LiveBinding` (flag),
  `ValueBinding` (one operand, or a nullary), `MultiBinding` (a fold),
  `GroupBinding` (an option whose value is a group).  A binding knows
  its **target**, the Converter instance whose `kwargs` it writes to.

`engine.enter(converter)` calls the converter's `register`, which pushes
its instructions onto the stack in order.  Then `engine.parse()` runs
the loop:

1. Pop and register every option instruction at the top of the stack.
   Options register *eagerly*, before the operands they sit beside, so
   an option is recognized anywhere in its era, not only after its
   group has been reached.  This is **flat recognition**.
2. Look at the next token.  If it's an option (section 4 says what that
   means), look it up in the handler table and **invoke** its binding,
   which consumes any opargs it takes.
3. Otherwise, if the top of the stack is an `ArgumentInstruction`, fill
   it with the token: leaf operands are recorded (not converted yet;
   section 6); a group operand constructs the child Converter and
   enters it, which pushes *its* instructions on top.
4. When the stack is empty, the converter has **saturated**: it has
   taken every operand it can.  The Engine yields.  Whatever token is
   next belongs to someone else: a command word, or a later era.
5. If the stack is empty and the next token is an option the table
   doesn't know, that also ends the era.  If the stack is *not* empty
   and an unknown option arrives, that's an error.

The stack is a plain list with the top at the end.  Instructions are
only ever pushed at the top and popped from the top, and the code looks
at most one item below the top (to see whether a `RepeatInstruction`
follows).

**Conjuring.**  With `draw(shape, spot: point = None)` where `point`
has `--flag`, the line `draw circle --flag 3 4` names `--flag` before
any operand has started the `point` group.  `PreOptionInstruction` makes
that work: `draw`'s `register` installs a binding for `--flag` whose
target is not an existing instance but a `_Conjure`: "the Converter for
slot `spot`, built on first use".  Naming `--flag` builds the `point`
instance early, marks it `_summoned`, and stashes it by slot; when the
fill reaches the `spot` slot it picks up the stashed instance instead
of making a fresh one.  If the summoned instance then never gets its
required operands, the error says the option "only becomes available
if you specify <X> <Y>", which is truer than "unknown option".  A
`_Chain` target does the same for an option buried two groups down.
Once the owner is resolved, a conjured option is the *ordinary* binding
of its kind; there is no separate conjure implementation.

**Shadowing.**  If the command itself declares `--flag`, a group's
`--flag` is not advertised at the command level: the command's own
option wins, and the group only gets its `--flag` after an operand has
entered it.

**Windows.**  A `*args` group (`def cmd(*points: point)`) is laid down
by a `RepeatInstruction`: before each element the template's
instructions are re-pushed, so each element gets a fresh Converter and
its options re-register.  Each element is a **window**; an option
between elements binds to the window being built, and an option past
the last operand binds to the nearest built instance rather than
starting an empty one.

**Trailing operands.**  For `def cp(*src, dst)`, `dst` is a required
operand *after* an absorbing `*args`.  `enter()` reserves the last N
operands from the end of the era's tokens before any filling starts,
skipping over options and their opargs so `cp a b dst --verbose`
reserves `dst` and not `--verbose`.  Reserved tokens are lifted out of
the stream and delivered by keyword.  Working out how many tokens an
option occupies, for that skip, is the one place the Engine reads a
token without consuming it (`_option_span`), and it uses the same
carving function the real parse uses.


## 4: What is an option token?  The scanner's rules

One function answers this for the engine and for completion, so they
can't disagree: `is_option_token(tok, classifiers)`.

* `-` and `--` are never options.
* Anything else starting with a dash is an option, with one exception:
  a dash followed by a digit.  `-243` might be the bundle `-2 -4 -3` or
  might be a negative number.  Rule: if it carves completely as a
  short-option bundle against the options currently registered, it's
  options; else if it parses as a float, it's an operand; else it's an
  option, so the parse raises the honest "unknown option".

Short options are carved by `parse_short_options(tok, classifiers)`,
where `classifiers` is three sets of letters: the options taking no
oparg (these bundle: `-vd`), exactly one (`-uFILE`, or `-u FILE`), and
two or more (must be last, values as separate words).  The function is
a generator and re-reads the classifiers before each letter, because
invoking one letter can register the next (`-me`: `-m` enters a group
that declares `-e`).

Four rules govern opargs and era boundaries; they're the ones you set
on 2026-09-07:

1. An option's oparg grabs the next token unconditionally, even one
   that looks like an option (`make -j -5` style), and even when the
   oparg is optional.  Only end of line or `--` declines it.
2. "Need" includes optional slots: while any slot is still open, the
   era is still consuming.
3. An unknown long option with nothing owed ends the era (the token
   belongs to whoever is next).  An unknown option while something is
   still owed is an error.
4. A short bundle mixing owned and unowned letters is an error, not a
   boundary.

`--` is **era-scoped**: once seen, no later token in that era is an
option.  The next era starts fresh (click's behavior), unless the era
shares forwards, in which case the `--` state relays along with the
era's options.  Where a command word goes, `--` is consumed and the next
token is the word.

A **verbatim** slot (`*args: appeal.verbatim`, or a single parameter)
sidesteps the rule: the Engine fills it with the next token before
asking whether it's an option.  Once a verbatim `*args` has taken its
first token the Engine sets `verbatim_run`, and from there `--` is a
token like any other.  `Plan.verbatim_sites()` reports the operand
positions where this happens, so the trailing-operand reserve scan in
`Engine.enter` and completion's `_completion_scan` classify those
tokens the same way.


## 5: The dispatcher: eras, command words, and the tree

An `Appeal` object is a **node** in a tree.  Each node has a **table**
of command words to child nodes, an optional **global command** (its
own body), an ordered list of **precommands**, and possibly a
**default** command for a bare line.  `@app.command()` registers a
word; `app.command('parent')` is the node for that word, and its own
`.command()` registers a word one level down;
`@app.precommand()` adds a precommand; a class registered as the global
command is a class-as-app whose methods are the commands.

A command line is read left to right as a sequence of **eras**.  An era
is a stretch of the line parsed by one Engine against one set of
converters.  There are two sorts:

* **Head eras** come before any command word.  Appeal's own metadata
  precommand (`-h/--help`, `--version`) is the first; then the user's
  precommands, in registration order, with a class moved ahead of its
  own member precommands.  Each precommand is an era of its own, and
  the precommand eras share their options with each other (share,
  below), so `prog -q --version` works no matter which precommand owns
  which string.  One string may have only one owner across the head.
* **Command eras**: each command word opens up to three eras: the word
  itself, the command's **help era** (`-h`/`--help`, immediate, sharing
  forwards into the next era so `build lib -h` works), and, when the command takes
  anything, its arguments-options-opargs era: the tokens after the word
  up to saturation.

Two flags on `@app.precommand()` shape the head:

* `share` (default False): which neighboring eras recognize this era's
  options, still bound to this era's converter.  Internally an era's
  share is FORWARDS (the next era), BACKWARDS (the previous), True
  (both), False (neither), or PRECOMMAND (both, but only among
  precommand eras); `precommand(share=False)` is PRECOMMAND and
  `share=True` is True, so a precommand's options always reach the
  other precommands, and reach the first command only when asked.
  FORWARDS carries the `--` state along.  BACKWARDS is done by
  registering the next era's handlers into this era before it parses,
  so `prog --beta aval` works when `aval` belongs to the earlier era.
  (`@app.command(share=True)` is FORWARDS: a command's options relay
  one command further.)
* `immediate=True`: the era executes first, once the whole line has
  parceled, before the line is judged and before any waiting era runs,
  wherever it sits.  That is how `-h` beats a malformed line, at the
  head and after a command word.

The metadata precommand is `immediate=True` and shares like any other
precommand.

An era is an object, `Appeal.Era` (private): its kind (`head`,
`command`, `help`, `aoo`), its plan, `share` (resolved against its
neighbors into `forwards` and `backwards`), `immediate`, and the
command word it belongs to.  A node produces them: `_head_eras()` once
per line, `_command_eras(word)` as each word dispatches.  One method,
`_parcel_era`, parcels any era: builds its Converter instances, makes an
Engine over `argv[pos:]`, enters the converters (last-registered first,
so the first-registered precommand's operands fill first), seeds any
shared handlers, parses, lists the step, relays what it shares, and advances `pos`
by what the era consumed.

`_run_node(line, pos, top)` is the dispatcher for one node.  It:

1. Parcels each head era.
2. Loops over command words: looks the word up in the table, parcels
   the eras the word opens, then recurses into the child node if it has
   subcommands or a default.
3. If no command word of this node was named: runs the node's default,
   or for a top-level set with no default, lists the commands and
   returns 1.

Every era and command it parses becomes a **`_Step`** appended to the
**`_Line`**.  A `_Line` is one command line's parse in progress: the
argv, the steps so far, whether `--` has been seen, and the handlers
bled into the next era.  A `_Step` is one unit to execute later: its
kind (`era`, `command`, `default`, `listing`), its Converter instance,
its Engine, and its config binding if any.


## 6: The three passes

This is the part that changed most since the last tour, and it's the
rule you set: **parcel, scan, execute**.

`Processor(app)(argv)` does:

1. **Parcel and scan.**  `_run_node` walks the whole line, era by era,
   command by command, as section 5 describes.  Every Engine's
   `parse()` matches tokens to instructions and checks structure: the
   counts, unknown options, a starved oparg.  It runs *no user code*.
   A leaf operand becomes a `_Raw` record (the text and its converter),
   a fold occurrence becomes a `_FoldOccurrence`, a zero-argument
   converter becomes a `_Nullary`.  These sit in the Converter's
   `args`/`kwargs` and in the Engine's `pending` list, in token order.
   If something is structurally wrong, the walk stops, and the error
   is *noted*, not raised.
2. **Immediate eras run.**  The steps listed so far that came from
   `immediate=True` eras execute, in line order, wherever they sit
   (the head's metadata era, a command's help era).  If one halts
   (returns a nonzero non-bool int, or exits, which is what `--help`
   does after printing), the run is over.
3. **The problem, if any, is raised.**  So `tool --help build badcmd`
   prints `build`'s help (the scan noted `badcmd` as a problem, but the
   immediate era outranks it), while `tool badcmd --help` reports the
   bad command: `--help` never got an era of its own to scan clean in.
   (`tool --help badcmd` is a third thing: `--help` takes an optional
   topic, so `badcmd` is the topic, and help says it isn't a command.)
4. **Execute.**  The remaining steps run left to right.  For each,
   `Engine.execute()` resolves the pending records in token order (this
   is where `int('x')` fails and becomes a polite usage error) and then
   calls the Converter instance, which calls your function.  A
   conversion error in the third command does not un-run the first two.
   That's **streaming dispatch**.

Why records rather than converting during the scan: a user's converter
is user code, and it must not run before its whole line has been
judged.  `finish()` is the one function that turns a record, a fold, or
a child Converter into its final value; `Converter.__call__` applies it
to everything it holds.

Class-as-app is threaded through the same loop with an `env` dict: a
step whose Converter `constructs` an instance stashes it under the
class's name; a method command's step reads it back as `self`.


## 7: Config layering

`Appeal(config=some_dict)` binds one mapping to the program.  The
dispatcher splits it as it goes: at the root, `_split_config` peels
off the keys that are command words (their sections) and
`_head_config` deals the rest to the head era that owns each key; at
every command word, the child node splits its section the same way,
and the command's arguments-options-opargs era gets what's left.  At
execute time, before an era's or a command's Converter is called,
`_config_apply` layers its mapping in: **defaults < config < argv**,
per option.  Each key is vetted against that plan's options, by
`config_key` (strictly, unless `Appeal(strict=False)` says to take what
matches and ignore the rest)--structurally, in pass 1, so a bad key
fails before anything runs.  Each vetted value is
read with the same by-name readers `load.py` uses for `read_mapping`,
then **assigned to its owner**: the era's Converter for its own
options; the argv-built nested instance when a nested converter's
option is named and argv already built that converter; otherwise the
nested converter is built from the mapping with defaults for the rest.
Nothing is turned back into command-line text.  An owner behind a
`*args` window can't be addressed by a mapping and says so.


## 8: Presentation

`presentation.py` is imported only when help, usage, or an error
renders.  It has three parts.

**The docstring dialect.**  `scan_docstring` parses the docstring once
with big and walks the tree (Larry, 2026-09-11: one recognizer of
Markdown, big's--the hand-written line scanner, definition-list parser
and code-shielding regexes are gone).  The first `Paragraph` is the
summary.  A `Heading` whose text is exactly one plain word from the
menu (`Options`, `Arguments`, `Commands`, `Subcommands`) opens a
special section, running to the next heading; its content must be
exactly one `DefinitionList` node with plain-text terms.  A `# Options`
inside a fence is a `CodeBlock`'s text, by big's parse.  Everything
travels on as big's nodes: rows carry a definition's blocks, the page
builder appends prose blocks to the template's parsed header, the man
page writes blocks through big's troff writer, and the exports are
big's gfm and commonmark writers.

**The merge.**  `merge_docs(plan)` walks the Plan tree and layers every
callable's entries: a converter documents its own parameters once, every
command using it inherits that, and the nearest enclosing scope wins on
a clash.  The edge decides (Larry, 2026-09-10): an argument edge
merges the converter's rows into the parent's `Level` (its table
pair); an option edge opens a `Level` of the converter's own, whose
rows become the option row's nested `('arguments' | 'options', rows)`
blocks--only those the converter's docstring asked for (its
`requested` set), and a converter that asked for nothing clips its
subtree.  Rows are identified by their **occurrence path**, the chain
of slot and option ids from the root, not by parameter name and not by
Plan object, because the same Plan appears at two paths when a
converter is used twice.  A bare name that two sibling groups both
declare is refused at the command with the candidates named.

**The page.**  `help_page_pieces` assembles the page from the template
(`app.templates`, which sets the order and dresses the headings) and
the corpus.  The Arguments/Options/Commands tables are built directly
as big `DefinitionList` nodes: each row's display is a role-tagged
`StyledText` term, its description parsed into the definition's blocks,
a row's nested blocks as a heading one level deeper plus a nested list.
Nothing is written back out as
Markdown and re-parsed.  `render_baked_help` wraps the pieces at the
real margin and paints them through the stylesheet: a theme is a dict
of role to style, plain and uncolored are one structure over two
palettes, and color appears only when the stream wants it.

Errors ride the same pipeline: `run_main` catches `AppealError`, prints
`error: ...` to stderr with the usage line attached (a command's error
wears that command's usage; an era's error wears the program's), and
returns 2.  Unknown commands and unknown long options suggest a near
match with `difflib.get_close_matches`, from the option strings in scope
where the token appeared; an exact option string that lives elsewhere
in the program gets "can't be used here; it goes after 'build'"
instead.  Never both.


## 9: One command line, start to finish

```python
app = appeal.Appeal(name='tool', version='1.0')

@app.precommand()
def logging(*, quiet=False): ...

@app.command()
def build(target, *, jobs: int = 1): ...
```

and the line `tool -q build lib --jobs 4`:

1. `Processor(app)(argv)` makes a `_Line`.  `_run_node(line, 0, top=True)`.
2. Head era one, the metadata precommand: its Engine sees `-q`, which it
   doesn't own; the stack is empty (this era takes no operands), so the
   era ends having consumed nothing.  Its `--help`/`--version` handlers
   are shared into the next era.  It's `immediate`, so its step is first in
   the list.
3. Head era two, `logging`: Engine over `['-q', 'build', 'lib', '--jobs', '4']`,
   seeded with the bled handlers.  `-q` carves as the flag `q`; its
   `LiveBinding` writes `quiet=True` into logging's Converter.  `build`
   isn't an option and the stack is empty: saturated, yield.  Consumed 1.
4. Command word `build`: a Converter, an Engine over `['lib', '--jobs', '4']`.
   `register` pushes an `OptionInstruction` for `--jobs`/`-j` and an
   `ArgumentInstruction` for `target`.  The option registers.  `lib`
   fills `target` as a `_Raw(str, 'lib')`.  `--jobs` invokes its
   `ValueBinding`, which grabs `4` as `_Raw(int, '4')` into `kwargs`.
   Stack empty, end of line: saturated.
5. `build`'s child node has no subcommands; the line is consumed.  Steps:
   `[era(metadata), era(logging), command(build)]`.
6. The immediate era runs: nothing to do, returns None.  No problem was
   noted.  The `logging` step resolves nothing (a flag has no record)
   and calls `logging(quiet=True)`.  The `build` step resolves `lib`
   and `int('4')`, then calls `build('lib', jobs=4)`.
7. `processor.result` is `build`'s return value.

Now `tool build lib --jobs x`: step 4 records `_Raw(int, 'x')` without
complaint; the structure is fine.  Step 6 fails resolving it:
`error: invalid value for 'jobs': 'x' (invalid literal for int() with
base 10: 'x')` with `build`'s usage line attached.  `logging` already
ran.

And `tool build --jobz 4`: in step 4 `--jobz` is unknown while `target`
is still owed: a structural error, noted.  No immediate era halts, so it
is raised: `unknown option '--jobz' (did you mean '--jobs'?)`.  Nothing
ran.


## 10: Where to look when…

* **a signature reads wrong** (wrong kind, wrong strings, wrong arity):
  `Plan.child_for`, `OptionRule.build`, `Plan.finalize_options`,
  `Plan.analyze`; the plan varieties and `build_plan` in `frontend.py`.
* **a line parses wrong**: `backend.Engine._loop`, `_fill_argument`,
  `_invoke_option`; the token rules in `is_option_token` and
  `parse_short_options`.
* **an option is "not available yet", or conjuring misbehaves**:
  `PreOptionInstruction.register`, `_Conjure`, `_Chain`,
  `_availability_message`.
* **eras, share, `--help` precedence, class-as-app**: `__init__._run_node`,
  `Processor.__call__`, `_group_eras`, `_merge_era_options`.
* **a value converts at the wrong time, or an error un-runs something**:
  the records in `backend.py` (`_Raw`, `_Fold`, `finish`) and
  `_Step.execute`.
* **config**: `_config_vet`, `_config_apply`, `_config_assign`.
* **help text, docstrings, colors**: `presentation.py`; `scan_docstring`
  for the dialect, `merge_docs` for inheritance, `rows_document` for the
  tables, the theme dicts for color.
* **error wording**: `_unexpected` and `did_you_mean` (unknown things),
  `_availability_message`, `_count_list` (counts).


## 11: Things to type at it

Run these from the checkout, so `import appeal` finds the tree.

```python
import appeal
from appeal.frontend import build_plan
from appeal.backend import converter_for, Engine

def point(x: int, y: int): return (x, y)
def draw(shape, spot: point = None, *, bold=False): return (shape, spot, bold)

p = build_plan(draw)
p.slots                 # [<Slot shape <Terminal str> required>, <Slot spot <Plan point ...>>]
p.options               # [<OptionRule -b/--bold (flag) -> bold>]
p.valid_counts          # {1, 3}: shape alone, or shape plus a full point
p.slots[1].child        # the point Plan, shared by every use of point

cls = converter_for(p)  # the generated Converter class, cached on p.compiled
conv = cls()
e = Engine(['circle', '--flag', '3', '4'], conv)   # --flag isn't declared: watch it fail
```

```python
e = Engine(['circle', '3', '4', '--bold'], cls())
e.parse()               # structure only: no int() has run yet
e.root.args             # ['circle' record, <point Converter instance>]
e.root.args[1].args     # [_Raw(int,'3'), _Raw(int,'4')]
e.execute()             # resolves the records in token order, then calls draw
```

```python
app = appeal.Appeal(name='t')
app.command()(draw)
app.process(['draw', 'circle', '3', '4', '--bold']).result
app.process(['draw', 'circle', '--bold', '3', 'x'])   # a conversion error, after the scan passed
```
