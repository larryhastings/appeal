# Appeal v2: the grammar, the coroutine, and the plan tree

**by Larry Hastings and Claude Fable 5**

*2026/07/02*

> **Historical design document.** This is the original v2 design
> rationale, written before the implementation began.  It predates
> most of the rulings that shaped the shipped library and does not
> track later changes.  The **spec of record is
> [appeal.grammar.md](appeal.grammar.md)**; for how the code is
> actually organized see [appeal.tour.md](appeal.tour.md).  Kept
> for design history.  (Naming: the rewrite shipped as **Appeal
> 1.0**; "v2" was its working nickname, and "v1" here means the
> 0.6 line.)

## 1: Overview

This document proposes a ground-up rewrite of Appeal--"Appeal v2"--and lays
out everything I want to change, why, and in what order.

The headline is a single structural move.  Appeal v1 is built on a little
bytecode virtual machine I call "Charm."  Charm works--I use Appeal happily,
all the time--but it's about five thousand lines of machinery, it's hard to
debug, and every consumer of it (the parser, the usage generator, the docs
generator) has to be its own interpreter.  This proposal replaces Charm with
two things that were hiding inside it the whole time: a **grammar** (a written-
down description of the command-lines Appeal accepts) and a **plan tree** (a
plain, passive data structure the grammar compiles into).  The parser, the
help system, and everything else become simple walks over that tree.

I want to be clear up front: **the core idea of Appeal is not changing at
all.**  The thing I'm proudest of--how Appeal maps a Python function to a
command-line, and the recursive step where converters are themselves just
functions--survives completely intact.  This proposal is about the *engine
room*, not the *deck*.  If anything, v2 makes the good idea shine brighter,
because the metaphor stops being smothered by the machinery implementing it.

You (the reader) are assumed to be comfortable with Appeal v1 at the level of
its README: you know that positional parameters become command-line arguments,
keyword-only parameters become options, annotations become "converters," and
that converters are introspected recursively exactly like command functions.
Everything past that, I'll define as we go.

This document was drafted by Claude (a Fable 5 session) from a long review-and-
design conversation, then is mine to overhaul into my own voice.  Yeah, the
co-authorship line at the top feels a little strange.  Cher, Bono, Charo, and
now apparently me.


## 2: What Appeal is (a refresher, and the one thing that matters)

Let me restate Appeal's central idea, because the whole proposal rests on it.

A Python 3 function signature is *already* a complete description of a command-
line interface.  Look at a function:

```Python
def fgrep(pattern, *files, ignore_case=False):
    ...
```

That signature says: there's a required thing called `pattern`, then any number
of `files`, and an optional switch called `ignore_case`.  That is *also* a
perfectly good description of a command-line.  Appeal's job is to be the
translator between the two worlds--to read that signature and accept
`fgrep -i needle a.txt b.txt` on the command-line, then turn around and call
your function with the right arguments.

Every other command-line library makes you describe your interface *twice*:
once in the function, and again in a pile of decorators or configuration.
Appeal makes you describe it *zero* times.  The signature is the description.

And here's the part that makes Appeal different from everyone else who's had a
similar idea (Typer, Fire, click to a degree): **the translation is recursive.**
An annotation on a parameter is a "converter," and a converter is just another
function, whose signature Appeal reads exactly the same way--all the way down.
That one property is what gives you, for free:

* mix-ins (a class whose `__init__` takes only keyword-only parameters becomes
  a bag of options you can add to any command),
* options that themselves map more options,
* `*args` where each item can carry its own options,
* and multi-argument options like `--position X Y`.

None of those are features anyone sat down and implemented.  They're
*consequences*.  They fall out of "a converter is just a function, read
recursively."  No false modesty: that recursion is the best idea in this
corner of the software world, and nobody else has it.  v2 keeps it, exactly.

There's a name for what that recursion makes Appeal, and I'll use it constantly
below, so let me introduce it now.


## 3: The first reframing--Appeal is a grammar

A **grammar**, in the computer-science sense, is a set of rules describing
which sequences of things are "valid" and what structure they have.  If you've
ever seen a language defined with rules like "a *sentence* is a *subject*
followed by a *verb* followed by an *object*," that's a grammar.  Programming
languages are defined by grammars.  So are file formats.

Here's the claim: **every Appeal signature is a grammar rule, and Appeal has
been a grammar all along--I just never wrote the grammar down.**

Take the README's own showpiece:

```Python
def int_float(i: int, f: float): ...
def my_converter(i_f: int_float, s: str, *, verbose=False): ...

@app.command()
def recurse2(a: str, b: my_converter = [...]): ...
```

Transcribe each signature into a rule mechanically.  A plain positional
parameter is a slot for one command-line argument.  A converter annotation is
a reference to *another rule*.  A default value makes something optional.
`*args` means "repeat."  You get:

```
recurse2      =  ARG(a)  my_converter?
my_converter  =  int_float  ARG(s)             [ options: -v | --verbose ]
int_float     =  ARG(i)  ARG(f)
```

(The trailing `?` is the usual EBNF quantifier: zero or one, i.e. optional,
because `b` has a default.  A rule name on the right-hand side is a
reference to that rule; `ARG(x)` is a terminal--one command-line argument,
consumed by the terminal converter for parameter `x`.)

Now look at what Appeal v1 *already prints* as usage for `recurse2`:

```
recurse2 a [[-v|--verbose] i f s]
```

That's the same object, with every rule inlined: expand `my_converter`,
then expand `int_float` inside it, and the terminals that remain are
exactly `i f s`--the usage line is the grammar flattened to a derivation.
The square brackets are the standard notation for
"optional."  **Appeal's usage strings have been the grammar, pretty-printed,
this entire time.**  The recursive step of the metaphor--the thing I'm proudest
of--is precisely the property that a grammar's rules can refer to other rules.
That's what makes it a grammar and not just a fixed template.  click and
argparse have fixed, non-recursive templates.  Appeal has a rule system.

### 3.1: Why this makes the hard parts easy

There's a second observation that turns the grammar from a nice metaphor into
a practical simplification.

On the command-line, a positional argument is just a string.  Any string.
Appeal can't look at `window.c` and know it's "the file argument" versus "the
pattern argument"--they're both just strings.  It figures out which is which
purely by *counting*: this is the first argument, so it's the pattern; the next
one is the file.  (Conversion failures--like passing `banana` where an `int`
was wanted--happen *later*, and are a different kind of error entirely.  They
aren't part of deciding the shape.)

That means **every decision Appeal's parser makes is a counting decision.**
"Should I enter this optional group?" can't depend on what the next argument
looks like; they all look alike.  It can only depend on *how many* arguments
are left and *how many* the group needs.

Say that sentence while looking at `argument_grouping.py`--the module I always
worried I'd gotten subtly wrong.  Three passes of pure arithmetic over minimum
and maximum argument counts.  Now I know *why* it had to be arithmetic: it's a
counting automaton, and I derived it by hand without knowing that's what I was
building.  The "argument groups" it computes are literally the states of that
automaton.  This is good news twice over: the module is basically right (a
previous review verified the algorithm), and in v2 it stops being a separate,
scary thing and becomes a few lines of a normal tree computation (see §6).

### 3.2: What we get by writing the grammar down

* **A real specification.**  Right now the *only* complete description of the
  command-lines Appeal accepts is the implementation.  Edge cases got
  *discovered* rather than *designed*.  A written grammar (about a page)
  becomes the thing we test against and reason about.
* **Better errors, computed not hand-written.**  Because arity is just counting,
  the set of valid argument counts for a command is computable--and it's always
  a tidy, eventually-repeating set (like "1 or 4," or "2, 4, 6, ..."). So the
  error message can be the whole truth: *"frobnicate takes 1 or 4 arguments;
  you gave 3,"* instead of v1's group-relative mumbling.  (More in §8.3.  This
  also finally closes my long-standing "communicate argument groups better"
  issue--by making the word "group" disappear from errors entirely.)
* **Completeness checks.**  "Required argument after `*args`" (which can never
  be satisfied) becomes a grammar well-formedness error, in the same family as
  every other "this rule can't work" check, all found in one pass.
* **A fuzzer for free.**  A grammar is *generative*: you can run it backwards to
  produce random *valid* command-lines.  That's a migration safety net--see
  §9.


## 4: The second reframing--Charm is a coroutine, misspelled

Now the engine room.

Charm is Appeal v1's bytecode virtual machine.  When Appeal processes a command,
it "compiles" the signature tree into a little program of ~40 bytecode
instructions, then an interpreter executes them against the command-line.

Here's what that interpreter actually does, stripped to its essence: it runs
instructions until it needs a command-line argument, **pauses**, goes and deals
with the command-line (which might mean running a *different* program, for an
option), then **resumes** where it left off.  Run, pause, resume.

That is a **coroutine**.  A coroutine is just a computation that can suspend
itself partway through and be resumed later, right where it stopped.  Python
has had first-class syntax for this since 2001: a generator function, using
`yield`.  When a generator hits `yield`, it freezes--all its local variables,
its exact position in the code--and hands control back to the caller.  Later
you resume it and it picks up as if nothing happened.

Charm is a from-scratch, hand-built implementation of suspend-and-resume,
written in Python-the-application.  And the punchline is that Python-the-
language *already ships* an industrial-strength version of exactly that.  A
generator's frozen state--its instruction pointer, its local variables, its
call stack--is maintained in C by people who've spent thirty years debugging
it.  Charm rebuilds all of that by hand, in the application, because it didn't
reach for the version already in the interpreter.

The correspondence is line-by-line:

| Charm (v1) | The native Python spelling (v2) |
|---|---|
| the bytecode program + instruction pointer | a generator function + its frozen frame |
| the call stack of saved register-sets | `yield from` (Python's own frame stack) |
| the `o`, `flag`, `converter` registers | ordinary local variables |
| `push`/`pop` of registers | expression nesting (locals just have scope) |
| `label` / `jump` / `branch` | `if` and `while` |
| "consume an argument" (`next_to_o`) | `token = yield NeedArgument(...)` |
| `remember_converters`/`forget_converters` | object lifetime = a loop iteration's scope |
| the discretionary-converter queue (§4.1) | *deleted*--the problem stops existing |

The evidence that this is the right move is already sitting in the v1 codebase,
in three places:

1. **The commented-out debug scaffolding.**  There are roughly two thousand
   lines of `# if want_prints:` blocks in the interpreter.  They exist because
   when your state lives in registers and five parallel stacks, no debugger can
   show it to you, so I had to build a bespoke print-everything system.
   Generators get `pdb`, real stack traces with real function names, and
   `locals()` for free.  The scaffolding isn't something to tidy up--it's
   something the new architecture makes *unnecessary*.

2. **The "go-faster stripe" mini-loops.**  In a couple of spots the interpreter
   special-cases clusters of instructions to avoid per-instruction overhead.
   That overhead only exists *because there's an interpreter*.  Native Python
   code has no dispatch loop to be slow.

3. **The second and third interpreters.**  Usage generation and docs generation
   in v1 are *additional* interpreters that fake-execute the same bytecode with
   hard-coded "pretend this branch goes this way" hacks.  That's the tax of
   compiling to an opaque form: every consumer must interpret it.  When the
   compiled form is a *tree* instead (§5), every consumer is just a tree walk.

### 4.1: The mechanism that gets deleted

I want to call out one specific win, because it retires a piece of v1 I was
weirdly proud of.

v1 has a "discretionary converter" system: queueing, unqueueing, flushing, ~150
lines, and one of the subtlest invariants in the whole codebase.  It exists
because straight-line bytecode must *create* a converter object before it knows
whether it'll actually be used (the argument that would trigger it is optional
and might not arrive), so it needs a way to create-then-maybe-revoke.

In the coroutine design, you simply *don't enter* an optional part of the parse
until its first argument actually shows up.  A frozen generator frame holds
exactly the state you've committed to and nothing speculative.  So there's
nothing to revoke, and the entire queue/unqueue/flush mechanism doesn't get
ported--the problem it solved no longer exists.  That's the tell that Charm was
solving problems it created.

None of this means Charm was a mistake.  I built it exploring what Appeal even
*means*, and a bytecode you can disassemble is a legitimate way to explore.  It
got me to working, correct semantics.  But the rewrite starts *from* those known
semantics, and for known semantics, "a generator that walks a tree" is strictly
less machinery than "a VM that runs bytecode."


## 5: The plan tree--what the compiler produces

So if the compiler no longer emits bytecode, what does it emit?  A **plan tree**:
a plain, passive data structure describing the command.  Think of it as an
`inspect.Signature` that has *finished thinking*--it knows not just what you
wrote, but what it means (which annotations are sub-rules, which optionals are
secretly required, how many arguments fit, which options live in which scope).

It's a handful of dumb classes with no behavior of their own:

* **`Plan`** -- one grammar rule, i.e. one callable.  Holds: the callable, its
  name and docs, its *slots* (the positional parameters, in order), its
  *options*, its computed arity `(min, max)`, and the set of valid argument
  counts.
* **`Slot`** -- one positional parameter; an *edge* to a child.  Holds: the
  parameter name, the name to show in usage, its child (either another `Plan`,
  or a marker meaning "this is just a string"), whether it's required (after
  the analysis in §6), which group it's in, and whether it repeats (`*args`).
* **`Group`** -- one counting decision.  Holds: optional-or-not, min, max, and
  which slots belong to it.
* **`OptionRule`** -- one option.  Holds: its strings (`-v`, `--verbose`), the
  parameter it fills, its own child `Plan` for any arguments the option takes,
  and whether it repeats.

Every consumer reads straight off this:

* **usage** is a walk that prints brackets at optional groups;
* the **exact-arity error** is just the valid-count set, phrased as English;
* the **JSON schema** (§8.1--the big one) is a walk that emits nested objects;
* the **parser** reads `group.min`/`group.max` to make its counting decisions;
* **code generation** (§7) emits one function per `Plan`.

Three properties matter:

1. **It's passive and immutable.**  All behavior lives in the consumers; nothing
   at parse time ever writes to the tree.  This is the fix for a real v1 bug--
   in v1, encountering `--` on the command-line writes a flag *onto the shared
   Appeal object* and never clears it, so a second parse on the same object
   misbehaves.  In v2 the description literally has nowhere to store run-state,
   so that class of bug can't exist.
2. **It kills the `fake_parameter` hack.**  v1's unit of compilation is an
   `inspect.Parameter`, so to compile a bare command *function* it has to
   manufacture a counterfeit Parameter (with a fake name, and a special case
   for Python 3.11's lambda-name rule).  v2's unit is the callable (a `Plan`);
   per-use facts live on the `Slot` edge.  No counterfeits.
3. **vs. `inspect.Signature`:** a Signature is *one* rule, unanalyzed--its
   annotations are still raw callables.  The plan is the *whole grammar with the
   analysis attached*.  Signature is the parse tree of your `def`; the plan is
   the analyzed, ready-to-run intermediate form.


## 6: The compiler--what we actually write

Here's the part that surprised me most, and it's good news.

**Appeal v2 has no parsing technology in its compiler at all.**  A tool like
yacc exists to read a grammar written as *text* and turn it into tables.  But
Appeal's grammar is written as Python signatures--and CPython *already parsed
those* when it compiled your `def` statement.  `inspect.signature` hands the
grammar to us as structured data.  So the compiler skips the entire front half
of a compilers textbook (no lexer, no parser, no tables) and starts at semantic
analysis: walk a tree, compute some facts, check for nonsense.  CPython is our
yacc, and has been since 2012.

The compiler is one builder plus four small passes, all plain recursive
functions:

* **`build(callable) -> Plan`** -- recursive descent over the signature tree.
  This is where the v1 "converter factory" chain survives nearly intact (it's
  the genuinely good part of the v1 compiler): given a parameter, decide its
  converter.  New in v2: it dereferences `Annotated`, understands builtin
  generics (§8.4), and--something v1 never had--detects cycles so a self-
  referential converter can't recurse forever.
* **`compute_arity(plan)`** -- the bottom-up `(min, max)` fold.  A terminal is
  `(1,1)`; an optional child contributes `0` to the minimum; `*args` makes the
  maximum infinite.
* **`compute_groups(plan)`** -- the replacement for `argument_grouping.py`.
  Flatten the terminals into their linear order, each carrying how deeply-optional
  it is; one backward scan promotes "trailing optionals that sit before a
  required sibling" to required (that's the clever bit of the old second pass,
  now about four lines because the flattening already did the structural work);
  a forward walk places the group boundaries.
* **`collect_options(plan)`** and **`validate(plan)`** -- keyword-only params
  become option rules; every configuration error is gathered in one pass.

And `argument_grouping.py` / `pgi` (the v1 "parameter grouper iterator")?  It's
not thrown away--it's **retired as the interpreter.**  During the transition we run
*both* the old grouper and the new `compute_groups` over thousands of random
signatures and diff them (§9).  My most-worried-about module ends its life as
the instrument that certifies its own replacement.  I finally find out
empirically whether it was ever right.


## 7: How the parser runs--and the "compile to Python" option

The parser is the coroutine from §4: a generic tree-walking generator plus a
driver.  I'll show the shape, then the one real decision.

The walker, roughly (this is illustrative; the real thing is ~100 lines
handling every node kind):

```Python
def parse_node(node, stream):
    with stream.options_scope(node.options):      # push this rule's options
        args = []
        for slot in node.slots:
            if slot.starts_optional_group and not stream.enter_group(node, slot):
                break                              # the counting decision (§3.1)
            if slot.is_leaf:
                args.append((yield NeedArgument(slot)))
            else:
                args.append((yield from parse_node(slot.child, stream)))  # recurse
        return build_invocation(node, args, stream.collected_kwargs(node))
```

The **driver** holds the real argv and answers `NeedArgument` requests.  For
each token: is it option-shaped (starts with `-`)?  If so, look it up in the
current options scope, run *its* rule as another generator to completion, stash
the result, and go find the next token for the still-suspended request.  If it's
a plain argument, hand it back.  This driver is, almost verbatim, v1's "loop
two"--the part of Charm that was never the problem.  Loop *one*--the forty
opcodes, the assembler, the peephole optimizer--is what dissolves into
`yield from`.

Notice there is no `IfElse` object or `While` object.  Control flow is not data.
It lives *once*, in the walker's source, as the literal keywords `if` and
`while`, and that single hand-written walker serves every signature ever
written.  The per-signature artifact (the plan tree) is inert data; the logic
that interprets it is fixed code.

### 7.1: Rung 1 vs. rung 3

There are two honest ways to run the plan tree, and I want both, in sequence:

* **Rung 1: walk the tree.**  The generator above, interpreting the plan
  directly.  A few hundred lines.  Simple, obviously-correct, `pdb`-friendly.
* **Rung 3: emit Python source and `exec` it.**  The compiler *writes* a
  Python function per rule--`def parse_frobnicate(stream): a = yield from
  stream.terminal(SLOT_a); b = yield from parse_color(stream); ...`--and `exec`s it
  into a real function object.  This is exactly the Jinja2 / Tidy move: compile
  the description into standalone Python.

(There's a "rung 2," composing closures, that has neither rung 1's simplicity
nor rung 3's speed.  Skip it.)

Rung 3 is faster (each `yield from` is one C-level resume, replacing several
Python-level opcode dispatches, and CPython's own optimizer gets to work on the
generated body as a unit), and it has a lovely property: **the generated source
is the disassembly.**  `charm_print`'s successor is `print(command.source)`,
except instead of forty opcodes with registers it's fifteen lines of ordinary
Python that any reader already understands.  Register the generated source in
`linecache` (another Jinja trick) and tracebacks and `pdb` step through the
generated lines.

The plan: **build rung 1 first, as the reference implementation and the interpreter;
then emit rung 3 from the same plan tree, and fuzz rung 3 against rung 1
forever.**  Jinja did essentially this--its compiler was built against semantics
already pinned down by an earlier interpreting version.

How much code do we generate?  Linear in the signature--about 3-5 lines per
parameter, control-flow only (converters, defaults, docs all pass by reference
through the `exec` namespace, never rendered into the source).  A `hello(name)`
is ~10 lines; a real command with options is 30-60; compilation stays lazy
(one command compiled per invocation, as v1 already does).  The `compile`+`exec`
of that costs well under a millisecond--cheaper than the `inspect.signature`
calls that precede it.  Which means: **codegen will never be Appeal's slow part;
introspection will.**  A "precompiled" mode (à la Tidy: emit a standalone,
dependency-free module) is worth having for cold-start-sensitive deployments,
but as a feature, not a cache.


## 8: What v2 lets us do that v1 can't

The reframings aren't just tidiness.  They unlock things.

### 8.1: The plan tree is an interface description--so Appeal speaks MCP

This is the one I'd lead with if I were pitching Appeal to a room in 2026.

Everything in the plan tree--names, types, converters, docs, defaults,
required-ness--is an *interface description*.  A command-line is *one rendering*
of it.  It is not the only one.  And there's a hungry new consumer of machine-
readable interface descriptions: AI tool-calling.  An "MCP tool" (the protocol
LLM agents use to call your code) is exactly a name, a JSON Schema of parameters,
some docs, and a dispatch target.  Look at the plan tree.  That's the whole
list.

And the decoder for an incoming tool call--a mapping of parameter names to
values, possibly nested--*is `read_mapping`*, which Appeal has shipped since
2023.  The wire format the entire agent ecosystem standardized on is the format
Appeal's config-file reader already speaks.

So `app.mcp()`--every Appeal command becomes an agent-callable tool, schema
generated from the plan tree, docs from the composed docstrings, dispatch
through the existing mapping driver--is nearly free in the v2 architecture.
What makes this *only* Appeal, and not something click could bolt on:

* **The interface description is recursive**, and JSON Schema is a recursive
  format.  When a flat library hits a custom converter, its schema bottoms out
  at "string" and the agent gets to guess.  Appeal recurses through the
  converter's signature and emits real nested structure.
* **There's a native non-argv door.**  MCP calls carry structured values
  (numbers, arrays, nested objects).  An argv-native library has to flatten
  everything back into fake command-line strings; Appeal's mapping driver eats
  the structured values directly.
* **And recursion is allowed here.**  Argv can't express a self-referential
  grammar (there are no brackets in a flat token stream to mark the nesting).
  But a JSON tool call *is* a tree, so a recursive converter (an expression
  tree, a nested filter) is perfectly parseable--the recursion bottoms out
  where the data does.  So the plan tree's cycle handling is per-backend: the
  argv parser rejects cyclic plans, the mapping/MCP path accepts them and
  renders them as JSON Schema `$ref`.

Implementation notes an implementer needs:

* **The vocabulary grows a schema-metadata protocol.**  A converter may
  expose its declarative constraints (duck-typed attribute or method, no
  base class): `validate('up', 'down')` emits `enum: ["up", "down"]`,
  `validate_range(0, 100)` emits `minimum`/`maximum`, and user converters
  can opt in the same way.  The vocabulary already *knows* these facts; this
  just lets it say them out loud in the schema.
* **The emitted schema is a *sound approximation*, backstopped at runtime.**
  Anything the schema admits that the converters reject--a string that
  fails `int()`, a value outside a range the metadata didn't capture--fails
  at execution as a usage error, which `app.mcp()` returns as an MCP *tool
  error*.  That's correct protocol behavior: agents read tool errors and
  retry with fixed arguments.  So schema fidelity is a quality knob, never
  a correctness requirement.
* **Two v1 gaps the mapping driver must close for parity:** `*args` maps to
  a JSON array, and `**kwargs` maps to an open object
  (`additionalProperties`).  v1's mapping compiler rejects both.
* **Deliberately absent:** `X | None` / union spellings.  We weighed union
  support and skipped it--`default=None` already covers the common case,
  and unions have no natural signature spelling worth the machinery.  A
  dumber implementer should *not* helpfully add it.

The same tree also gives you a REPL (`app.repl()`: readline + `shlex.split` +
the same parser + the same completion engine--an interactive mode for
every Appeal program, maybe fifty lines; see §8.9) almost for free.  One set
of Python functions becomes a CLI, an agent toolset, a config reader, and an
importable library, with no repetition.  Nobody else can offer that, because
nobody else's interface description is a walkable value.

### 8.2: Shell completion

Shell completion works by the shell running your program twice: once at install
to emit a shell function, then again on every TAB, re-invoking your program with
the partial command-line so it can print candidates.  "Given a partial command-
line, what could come next?" is a question a grammar answers natively--it's the
set of things that can legally appear at the current parse position.  Options in
the current scope, subcommand names, and per-converter candidates (a `validate`
converter completes its own legal values for free; a `Path` converter tells the
shell "complete a filename").  The requirement it puts on v2: the parser must
support *partial/tolerant* parsing--stop mid-parse and report what it expected.
Design that in from day one; v1's interpreter pausing at `next_to_o` was
accidentally halfway there.

The mechanics, for whoever implements it (this is the Click-established
protocol, which bash, zsh, and fish tooling all expect):

* **Install phase.**  The user puts one line in their shell rc file:
  `eval "$(_MYPROG_COMPLETE=bash_source myprog)"` (zsh/fish variants
  likewise).  Appeal checks for that environment variable *first thing* in
  `main()`, before any other work; if set to `<shell>_source`, it prints a
  ~40-line completion function for that shell (shipped as three per-shell
  templates--data, not logic) and exits.
* **TAB phase.**  The generated shell function re-invokes the program with
  the variable set to `<shell>_complete`, passing the words-so-far and the
  cursor index via environment variables.  Appeal runs the partial parse,
  prints one candidate per line, and exits.  Each candidate is typed:
  `plain` (a literal string), or `file`/`dir` (which tells the shell-side
  function to fall back to the shell's *own* filename completion--Appeal
  never enumerates the filesystem itself).  zsh and fish also accept a help
  string per candidate; emit the parameter's summary there.
* **Candidate sources**, in order: option strings in the current scope
  (note that Appeal's scoped child options therefore complete *in
  context*--after `--color`, TAB offers `--brightness`; no other library
  can express that), subcommand names if we're at a command position, and
  per-converter candidates via a duck-typed hook (a converter may expose
  `__appeal_complete__(prefix)`; `validate()` implements it automatically
  from its own legal values; an `appeal.Path()` converter returns the
  `file` type).
* **Rules of the road:** completion mode must never print errors to stdout
  (it corrupts the shell's read--stderr or silence); per-TAB latency is
  bounded below by interpreter startup, so keep the pre-parse import
  footprint small (see the dependency plan in §10.1); crib the
  battle-tested shell templates from Click's source at implementation
  time--the quoting edge cases are where the bodies are buried.

Status: **in scope, post-rewrite.**  A nice-to-have that lands after the
core is green, not a launch blocker--but the *partial-parse capability* it
needs is a day-one requirement on the parser, which is why it's specified
here.

### 8.3: Errors that are computed, not hand-written

Because arity is counting (§3.1), the parser can say *"takes 1 or 4 arguments;
you gave 3"* and, mid-parse, *"expected: i f s (or nothing)"*--the actual
parameter names, because parse state is a position in a tree of real
parameters, not an opaque register.  And there are two error *channels*, not
one: `UsageError` ("you typed it wrong"--show usage) stays, and a new sibling
for "you typed it right but the operation failed" (file not found, connection
refused) prints its message and exit code *without* the usage block.  That's the
git distinction: `git: 'foo' is not a git command` shows help; `fatal: not a
git repository` doesn't.

### 8.4: Type hints, on my terms

I don't like the `typing` module and I'm not going to start using it.  But the
*language-builtin* type spellings are fine, and they map beautifully onto things
Appeal already does:

* `pattern: list[int]` (keyword-only) = a repeatable option collecting ints--
  a new, *additional* spelling for what `accumulator[int]` already means.
  **This is the elegant repeatable-option spelling I wanted for a decade.**
  The reason it took this long: "this option, many times" is a *call-site*
  property, and argv has no call site--so multiplicity had to move into the
  signature, and pre-2020 Python had no signature spelling for it.
  `list[int]` (PEP 585, 2020) *is* that spelling, and mypy already
  understands it.

  To be explicit about what this does *not* mean: `accumulator[int]` remains
  supported, and **`MultiOption` remains a supported public API.**  `list[X]`
  covers "collect the values into a list"; `MultiOption` is a *protocol*
  (init / option / render)--it's how you express stateful folds like
  `counter()`, custom aggregation, validation across occurrences.  A spelling
  can't replace a protocol.  Nothing here is deprecated; the common case just
  gets a nicer costume.

  **The `MultiOption` wart, sharpened (TENTATIVE--not signed off).**  It's
  worth being honest about *why* this one feature refuses to fall out of the
  algebra, because it tells us how small we can file the wart.  Strip any
  repeatable option to its essence and it's a **fold**: a seed, a combine
  step per occurrence, a final value.  `accumulator` = `(seed=[],
  combine=append)`; `mapping` = `(seed={}, combine=insert)`; `counter` =
  `(seed=0, combine=+1)`.  `list[X]` and `dict[K,V]` can spell the first two
  *only* because Python happens to have container types whose seed *is* a
  type you can write in an annotation--a lucky coincidence, not a general
  mechanism.  The general operation (a fold over occurrences) has no
  signature shadow at all: Python forbids a repeated keyword at the call
  site (`f(v=1, v=2)` is a `SyntaxError`), so there is nothing for the
  algebra to map *from*.  `counter`'s seed `0` has no free type to ride in
  on (`int` already means "takes an int oparg"), and its `+1` combine
  matches no container's "merge into me."  So `counter` genuinely cannot
  fall out.  That's a gap in the source language, not a failure of
  cleverness.

  What v2 *can* do is shrink the blast radius--and, to be clear about what it
  can't: two named heterogeneous facts ("repeats?" and "how do occurrences
  combine?") is a bundle, and the Pythonic home for a bundle is a class.  So
  **`MultiOption` (or a class exactly like it) is irreducible; we do not
  throw it away.**  What changes:

  1. It stops being *the mechanism* and becomes *the custom-fold class*.
     `accumulator` and `mapping` route through the `list[X]`/`dict[K,V]`
     spellings and never construct or subclass it; its reach shrinks to
     `counter` and genuinely custom folds.
  2. Its recognition leaves the hot path.  `build()` does the
     `issubclass(annotation, MultiOption)` check *once, at compile time*,
     and stamps a `repeat` flag plus a `fold` onto the option's plan node;
     the runtime parser only reads that node field.  No `isinstance` per
     parse.
  3. The protocol *might* thin from three methods to one--`init`/`option`/
     `render` is exactly `fold(occurrences) -> value`, and finite CLI input
     doesn't need the streaming three separate methods buy you.  Offered,
     not mandated; the three-method form can stay as the stateful shim.

  Net: a refined, more razor-sharp `MultiOption`--kept, demoted from
  load-bearing mechanism to one authoring path, isinstance moved off the hot
  path, two common cases spelled around it.  The irreducible floor is a
  `repeat` bit and a `fold`, carried by a class, recognized at compile time.
* `dict[str, int]` = a repeatable key/value option (today's `mapping`),
  likewise an additional spelling, likewise deprecating nothing.
* `tuple[int, float]` = an option taking two arguments of those types.

All of that is builtin syntax with *zero* `typing` imports, and it's self-gating
across Python versions: a user on 3.6 physically can't write `list[int]` in an
annotation, so there's no version check anywhere in Appeal--just one new entry
in the converter-factory chain, guarded by `getattr(types, 'GenericAlias',
None)`.

The one `typing` construct I'll bless is `Annotated`, because Appeal has
*already* supported it since 0.5.5, and it's the clean way to make a function-
converter mypy-happy: `Annotated[ReturnType, my_converter]`.  The user imports
`Annotated`; Appeal never does (it just duck-types `__metadata__`).  Add a
`.pyi` stub so `@app.command()` doesn't erase your function's signature under
mypy-strict, and the whole static-analysis story is: common spellings clean by
construction, `Annotated` for the exotic ones, zero `typing` imports in Appeal's
own runtime.

(For the record: `Annotated` exists at runtime, and Appeal can read it, because
of PEP 649--deferred annotation evaluation.  I wrote PEP 649.  I wrote it partly
*because* PEP 563 was about to break runtime annotation use, which Appeal
depends on.  The language and the library have been circling each other for
years.)

### 8.5: The `cp SRC... DST` shape

There's a famous command-line Appeal v1 can't spell: `cp SRC... DST`--repetition
followed by a required trailing thing.  Python forbids a positional parameter
after `*args`, so the shape falls outside the metaphor.

But Python *does* allow a keyword-only parameter after `*args`, and allows it
with *no default*.  `def cp(*sources, dest)` is legal Python meaning "`dest` is
required, supplied separately from the varargs."  v1 makes required-keyword-only
a configuration error (correctly, since options must be optional--and this isn't
an option).  So that syntax slot is currently *dead space*.

The rule, and this is now **decided**: **a keyword-only parameter with no
default maps to a required trailing positional argument.**
`def cp(*sources, dest)` gives usage `cp [sources]... dest`.  It keeps every
existing rule intact: options stay optional (this isn't an option),
required-ness still means no-default, and the parameter's position (after
`*args`) mirrors the command-line (after the repetition).  Leading no-default
params plus trailing no-default params form the minimum argument count;
optionals fill the middle.

Two further rules:

* **You may have more than one.**  `def mv(*sources, dest_dir, mode)` means
  usage `mv [sources]... dest_dir mode`, with the trailing arguments filled
  in signature order.
* **They must come first among the keyword-only parameters.**  The moment a
  keyword-only parameter has a default value (i.e. is an option), every
  subsequent keyword-only parameter must have one too.  No interleaving
  trailing-positionals and options--`def f(*a, x, y=1, z)` is a
  `ConfigurationError`.  This keeps the signature readable as three clean
  blocks: positionals, then trailing positionals, then options.

This relies on the order of keyword-only parameters being preserved in the
signature.  Python guarantees that as of 3.7--a guarantee I personally asked
for, *because I wanted it for Appeal* (see how long I've been working on
this?).  3.6 delivers the same order in practice; it just wasn't promised by
the language yet.  Safe to rely on across every version Appeal supports.

Parsing: the arity fold computes the trailing block's minimum (the sum of
each trailing parameter's minimum, converters included--a trailing parameter
can have a converter like anything else), and the repetition stops consuming
when only enough arguments remain to satisfy that reservation.  The precise
rule for interleaving with options belongs in the grammar document; the
degenerate case (trailing positionals with no `*args` in sight) is simply
equivalent to ordinary required positionals at the end, and is permitted.

I explicitly *rejected* two alternative spellings: overloading the ordinary
positional-or-keyword parameter (too magic--hangs meaning on the default,
unchosen parameter kind), and a `dest=appeal.trailing` sentinel (labels the
asymmetry but reads like a magic value).  The pragmatic v1 workaround--
`def cp(file, *files)` plus documentation--retires with honor.

### 8.6: Class-based commands, honestly

v1 can map methods of a class to commands, but the machinery is rough: it wraps
the method in a `functools.partial` with a placeholder for `self`, then does
placeholder-rebinding gymnastics at call time (and needs an `update_wrapper`
hack I filed a CPython bug about).  All of that exists because v1 has no
execution phase to defer the binding into.

v2 has one.  The plan tree records the *fact* ("this callable is `MyApp.add`,
unbound; its `self` comes from the `MyApp` instance"); the signature analyzer
skips the `self` slot; and at execution time the global command constructs the
instance and drops it in an *environment* the executor reads from.  No partials,
no placeholders.  Surface: one decorator on the class; `__init__` is the global
command (its keyword-only params are the global options); public methods are
commands; nested classes are subcommand trees.  And `self` turns out to be just
the first example of "a parameter supplied by the environment rather than the
command-line"--the same mechanism absorbs the two other ad-hoc injection
systems v1 has.


### 8.7: The help system, rewritten (composable documentation)

This replaces the largest single chunk of v1 outside Charm: the docstring
parser and renderer (`compute_usage`, the hand-rolled state machine, the
proxy-dict topic resolution, and all of `text.py`, which is hereby
condemned).  The design was worked out in an earlier session and is settled;
here it is in implementable form.

The model is two phases, cleanly separated:

1. **Parse docstrings into a corpus.**  Each callable's docstring is parsed
   once into structured pieces: a summary (the first paragraph), body text,
   and per-parameter documentation.  Composability means the corpus for a
   command *merges up* the pieces from its converter tree--you document each
   converter once, and every command using it inherits that documentation.
2. **Render the corpus through templates.**  What the help output looks
   like is defined by template strings, and rendering is a pure function of
   (corpus, templates).

Template mechanics--deliberately primitive, that's the feature:

* Placeholders are **literal substrings**, found with `str.partition`, not
  format-strings: `{argument}` and `{documentation}`.  No escaping rules,
  no brace-doubling, no conflict with braces in the user's prose--a user
  writing `{verbose}` or a JSON example in their docstring is just writing
  text, because placeholder recognition happens only when parsing the
  *template*, never the documentation body.
* A section template contains **exactly two pairs** of placeholders,
  alternating, `{argument}` first, each on a line by itself.  Two pairs is
  the structurally minimal form that shows the *separator between items* in
  the template itself.  The parser is ~forty lines of partition-and-check,
  and every violation has an obvious error message naming the line.
* **The processor is pluggable.**  The default processor implements the
  full composable behavior.  A user can supply their own--including the
  documented "do-almost-nothing" processor, which clips the summary off the
  top and emits the rest verbatim, with *no* special-section parsing (so
  "Options:" in a docstring is just a heading, not syntax).  If a project
  hates the auto-magic, they switch processors instead of fighting the
  library.

Rendering rules that must be nailed down in code, not discovered:

* **Word wrap and columns come from big**: `split_text_with_code` (which
  preserves indented code blocks), `wrap_words`, and `merge_columns`.
  Nothing hand-rolled.
* **Depth limits.**  Usage lines render nested optional groups inline at
  depth 0-1; deeper than that, collapse to `[--option [options]]` and let
  the options section carry the structure.  Same instinct in the options
  section: past depth 2, stop indenting further and render the deepest
  definitions flat under qualified names--past that depth the nesting stops
  conveying structure the reader can hold in their head.  Both thresholds are
  processor attributes, so a user who wants full nesting can crank them up.
* **Empty-substitution rule.**  If a `{name}` substitution evaluates to
  empty, the *entire line* containing it is dropped before rendering, and
  runs of three-or-more consecutive newlines collapse to two.  Fully
  specified, predictable--no "suppressed somehow" hand-waving (that
  "somehow" is exactly the class of vagueness that bit v1).
* **Name-collision policy.**  Positionals collide differently than options: a
  child converter's positional becomes the parent's positional on the flat
  command line, and v1's dotted-full-name namespace (which we've removed) was
  what disambiguated them.  In v2, a name collision across the converter tree
  is a **`ConfigurationError` at compile time**, naming both sites and
  suggesting a rename--not silent mangling.  Preserves the "use Python's
  mechanisms" principle: Python raises on this class of problem, it doesn't
  auto-mangle.
* **ANSI width, for when colorization (§8.8) is in play.**  Escape sequences
  have `len() > 0` but display width 0, so feeding pre-styled text into
  `wrap_words`/`merge_columns` drifts the columns.  Resolution: style *after*
  layout, so only tokens that survive wrapping intact get colored.  The
  rendering pipeline must not receive pre-styled text.


#### 8.7.1: Rulings (2026/07/07, design review)

The design above was reviewed end to end and the undercooked areas
ruled.  Where the rulings amend the text above, the rulings win.

**The docstring is input, never output.**  It is *harvested*: section
headings and their entries are slurped out, and all remaining prose
coalesces into one blob, in source order.  The output's structure
belongs entirely to the templates; input shape and output shape often
rhyme, but that's coincidence of defaults, not plumbing.

**The docstring grammar.**  A section heading is a line that is
exactly `Arguments:`, `Options:`, `Commands:`, or `Subcommands:`
(the last two are machine-identical; `Commands:` reads right on a
global command, `Subcommands:` on a command with subcommands).
`Sub-commands:` is detected and raised at the user.  A section runs
from its heading to the first blank line; entries are `name: text`
lines indented under the heading, deeper-indented lines continue an
entry.  Entry text is kept as dedented *lines*, kid gloves, never
flattened--code lines and paragraphs in a parameter's documentation
survive (the text trio was written years ago in anticipation of this
moment).  One section per heading kind per docstring; a duplicate is
an error.  Everything else is prose.  Bare top-level `name: text`
lines are NOT entries--without a heading they're just prose, which
retires the plan-resolution heuristic entirely.  Restrictive now,
relaxable later: the other direction is way harder.

**Strictness.**  An entry naming nothing in the plan is a
`ConfigurationError` naming the entry and the callable.  The heading
must match the parameter's kind: `Arguments:` covers operands
*including* keyword-only-no-default parameters (they're trailing
operands); `Options:` means keyword-only with a default; a mismatch
is a `ConfigurationError`.  An entry naming an *invisible* parameter
--an internal node of the grammar, like `b: my_converter`--is a
`ConfigurationError` shaped like "'b' is not one of the visible
command-line arguments of 'recurse2'": only terminals, options, and
command words have rows.

**Merge-up: only entries merge.**  A converter's prose stays home;
its summary stays home too (no summary-fallback: an undocumented
parameter renders with an empty description--and the fallback's
best case died with the observation that internal nodes have no
rows, while its option case would inherit `int.__doc__`).  Nearest
enclosing scope wins, silently: overriding docs inherited from your
kids is the feature.  Options parallel arguments--same tech,
different data.

**Templates.**  One *master template* declares the page and the
order:

    {summary}

    {usage}

    {documentation}

    {arguments}

    {options}

`{documentation}` is the coalesced prose blob.  Arguments before
options in the shipped default.  Absent sections substitute as empty
strings; a template line containing at least one placeholder, ALL of
whose placeholders rendered empty, is dropped; runs of three-plus
newlines collapse to two; the final render is `.rstrip() + '\n'`.
(A dispatcher's command-list page is a second master template with
`{commands}`.)  Section templates keep the two-pair form, amended:
a pair may share a line (the definition-list form--the literal text
between the placeholders is the spacer, the text before `{argument}`
is the indent, and layout is delegated to
`big.format_definition_list`) or sit on adjacent lines (the hanging
form).  Literal lines before the first pair are the section's
heading, so an empty section takes its heading with it by
construction.  The parser stays partition-and-check at both levels;
big.template stays out of this round (the section template is a
structural parse, not a substitution--Formatter is the wrong tool,
and TOOWTDI keeps the page template on the same parser).

**No pluggable processors this release.**  The harvest/merge/render
pipeline is internal machinery; templates are the entire
customization surface.  The do-almost-nothing behavior is emergent:
a heading-free docstring harvests no entries and renders as prose.

**Emission.**  The compiled standalone script is *compiled*, and
that means both the annotation tree and the documentation: harvest
and merge run at build time, and the script ships the predigested
corpus and the templates as literals, ready to format at runtime
(colorization repaints at the script's own runtime, per the
completion/colorization rulings).  The renderer rides the warehouse;
the harvester and merger never leave home.


### 8.8: Colorization

Table stakes since argparse grew it in the 3.14 stdlib.  The design:

* **A `theme` object passed to the `Appeal` constructor, styling semantic
  roles, not colors.**  The renderer never says "make this cyan"; it says
  `theme.option(...)`, `theme.metavar(...)`, `theme.heading(...)`,
  `theme.error(...)`, `theme.command(...)`, `theme.summary(...)`.  A theme is
  any object with those methods returning strings.  Default theme = identity
  (plain text).  Appeal ships exactly one ANSI theme (~30 lines).  A `rich`
  devotee writes a three-line adapter; **Appeal itself imports no color
  library**--raw ANSI SGR codes, exactly as stdlib argparse does (Appeal is
  POSIX-only, so no colorama-for-Windows-consoles concern).
* **On/off policy, copied verbatim from the established convention** (do not
  reinvent): color iff the stream `isatty()` and `TERM != "dumb"` and
  `NO_COLOR` is unset; `FORCE_COLOR` forces it on; honor `PYTHON_COLORS`.
  One function, one place.
* **Scope:** Appeal colorizes only its *own* output--usage, help, error
  messages.  It is not a terminal-styling library for command bodies.
* The width-vs-wrapping interaction is handled by "style after layout"
  (§8.7).  If big's text functions ever grow an escape-aware width hook,
  that becomes the cleaner path; until then, style-after-layout is the rule.

Status: **in scope, post-rewrite nice-to-have.**  Lands after the core is
green; not a launch blocker.


### 8.9: A REPL

`app.repl()`: `readline` + `shlex.split` + the same parser + the same
completion engine (§8.2).  An interactive mode for any Appeal program, on
the order of fifty lines, because every piece it needs already exists.  Read
a line, split it, feed it through the parser exactly as a command-line,
execute, loop.  Completion inside the REPL is the same partial-parse
machinery completion uses.  Post-rewrite nice-to-have; listed so it isn't
forgotten, and because it's another free consumer of the plan tree.


### 8.10: Byproducts and deferred ideas

Kept here so a completeness-minded implementer neither drops them nor
"helpfully" over-builds them:

* **The invocation tree is a value** (§5's parse/execute split makes the
  fully-bound "call this callable with these args" an object you can hold
  before executing).  That hands you, nearly free: `--dry-run` (print the
  invocation instead of running it), audit logging, and record/replay.
  Build the split; expose these as thin conveniences later.
* **argv synthesis** (walk the plan tree backwards: given a callable and
  arguments, emit the argv that parses to it).  Downgraded from a headline
  feature to *internal machinery*--the sentence generator is needed anyway
  for the parity fuzzer (§9), and bidirectionality is the design's
  certificate, not a user-facing selling point.  Do not build a public
  `render_argv` API in the first pass; do build the generator the fuzzer
  needs.
* **Config layering** (argv, a config mapping, and environment variables are
  three serializations of *the same call*; parse each to a partial binding,
  merge by precedence, execute once).  This is the principled replacement for
  v1's default-value plumbing idiom.  **Deferred, listed, not scheduled**--
  design it only when the three drivers are all solid.
* **Environment variables need no feature.**  Defaults are ordinary Python
  expressions, so `editor=os.environ.get("VISUAL", ...)` already composes.
  Say so, so nobody adds a redundant mechanism.


## 9: Migration--keep the tests, not the code

The v1 test suite (270-ish tests, including the README examples) is the true
behavioral specification of Appeal, and it's mostly implementation-agnostic.

The suite runs under **atest**, a tiny stdlib-only harness built during this
work (it lives in `tests/atest.py`): bare `assert a == b` with
unittest-quality, type-aware diff introspection on failure (it borrows
`assertEqual`'s own difflib output--including for `is None` and nested
list/dict/str diffs), plain `def test_*()` functions with no mandatory base
class, and a local-`setup()` idiom (an explicit fixture function right above
its tests, so hoisting one test into a scratch file to debug it means "scroll
up, copy `setup()` too"--no hunting for a hidden `setUp`).  Porting the tests
includes realigning them into that idiom.  atest is the acceptance gate's
machinery, so it's named here.

The plan:

1. **Port the tests first.**  They become v2's acceptance gate.  Diverge from
   them only where this document says v1 was wrong (`foo -h` should work; a
   positional `bool` default shouldn't silently coerce; `abort()` should raise
   a real usage error, not a debugging `RuntimeError`).
2. **Parity-fuzz.**  Because the grammar is generative (§3.2), generate
   thousands of random signature trees, derive random valid command-lines from
   each, and run them through v1 and v2, diffing the resulting calls.  This
   tests the obscure interleavings (optional groups inside `*args` inside
   options) that no hand-written suite covers.  It's the safety net for getting
   from v1 to v2 without silent drift.
3. **Use pgi as an interpreter** during the grouping rewrite (§6), then delete
   `argument_grouping.py` once the diff has been empty longer than my patience.


## 10: Sequencing

1. **Land the pending v1 cleanup.**  There's an uncommitted bugfix-and-coverage
   patch; commit it, plus fixes for the three verified crashes/leaks (the `--`
   state leak, `process()` with no args, and `abort()` raising `RuntimeError`).
   Delete the repo cruft.
2. **Write the grammar.**  About a page of rules plus the signature-to-rule
   mapping table.  This is the first real v2 artifact and the spec everything
   else is tested against.
3. **Build layer 1 (plan tree + analysis passes) and layer 2 (rung-1 parser +
   the three drivers: argv, mapping, iterable)**, against the ported test suite
   and the parity fuzzer.
4. **Emit rung 3** (Python codegen) from the same plan tree; fuzz it against
   rung 1.
5. **Presentation layer:** the composable-docstring redesign (template-based,
   built on `big`'s text functions), colorization (a `theme` object styling
   semantic roles, no external dependency), and completion.
6. **The killer app:** `app.mcp()` and friends, once the plan tree and mapping
   driver are solid.

Note that steps 3-6 each stand alone and stay in the loop with me; this isn't a
single heroic rewrite, it's a sequence of well-scoped phases with a green test
suite at every step.

### 10.1: Dependency and import-cost budget

Appeal depends on `big`.  Import cost matters--for a CLI it's felt on every
invocation, and for the MCP/completion paths per tool-call and per TAB.
Measured: `import big.all` is ~53ms; the pieces are far cheaper imported
individually (`big.itertools` ~11ms, `big.text` ~28ms, `big.log` ~40ms,
because it pulls `inspect`).  The rule for v2:

* **Stop importing `big.all`** (v1's habit).
* **The only module-level big import is `PushbackIterator`**
  (`from big.itertools import PushbackIterator`, ~11ms)--the one thing the
  hot parse path needs.
* **Lazy-import `big.text`** inside `appeal.split()` and inside the
  help/presentation path.  Nobody times `--help`; the parse-and-execute hot
  path (including the MCP tool-call path) never touches it.
* **`Log` dropped or lazy** (dev-tracing only).
* **`BoundInnerClass` leaves the dependency graph entirely**--its only v1
  users are Charm-internal classes that v2 deletes.

Net: the hot path pays ~11ms, not ~53ms, with zero duplicated code.  This is
a requirement to preserve, not an optimization to reach for later.

### 10.2: The README is a deliverable

Adoption depends on it, so it's scoped work, not an afterthought:

* **A copy-paste-runnable Quick Start at the very top** (the Armin-school
  trick, correctly applied): the first thing a reader sees is a complete,
  working ten-line program.
* **A "Using Appeal for MCP tooling" section** (§8.1)--this is the shareable
  anchor link for the 2026 audience, and the single highest-leverage thing
  for Appeal finding the users it deserves.  Fifteen lines above the fold:
  `@app.command()`, then `app.mcp()`, then a screenshot of an agent calling
  it.
* The exhaustive current README becomes the reference manual *below* the
  pamphlet, not the first thing anyone hits.


## 11: Decisions made, and decisions still open

**Made:**

* Replace Charm with plan tree + generator-based recursive-descent parser.
* Rung 3 (emit-and-`exec` Python) is the production parser; rung 1 is the
  reference interpreter.
* Support builtin generics (`list[int]`, `dict`, `tuple`) as *additional*
  annotation spellings; the only blessed `typing` import is `Annotated`; ship
  a `.pyi` stub so `@app.command()` doesn't erase signatures under mypy strict.
* `list[X]` is a new spelling for `accumulator[X]`; `dict[K,V]` for `mapping`.
  **Nothing is deprecated:** `accumulator`/`mapping` stay, and `MultiOption`
  stays a supported public protocol (§8.4)--a spelling can't replace a
  stateful fold like `counter()`.
* **Trailing required positionals** (§8.5): a keyword-only parameter with no
  default is a required trailing positional; you may have several; they must
  precede any defaulted keyword-only parameter (mixing is a
  `ConfigurationError`).
* Two error channels (usage error vs. runtime failure); computed exact-arity
  messages; the word "group" never appears in an error.  An **error-message
  catalog**: every user-facing `UsageError` string collected in one reviewable
  place, not scattered through the parser.
* The plan tree is passive and immutable; all run-state lives in the per-run
  object (kills the `--` leak by construction).
* Class-based commands via an execution-time environment, not placeholder
  rebinding.
* **Help and version are grammar-level, not a raw-argv pre-scan.**  v1's
  pre-scan in `Processor.__call__` is *why* `foo -h` fails; v2 routes `-h`/
  `--help`/`--version` through the parser so they work after a command and
  after subcommands.  `version` claims only `--version` (freeing `-v` for the
  user's `verbose`).
* **API removals/retirements:** `Option`-as-subclass demoted (plain classes
  already work as converters); `read_csv` retired (compose `read_iterable` +
  `csv.reader`); the deprecation harvest lands (old exception aliases,
  `Appeal.argument`, `SingleOption`); bare `list`/`dict`/`tuple` annotations
  raise a helpful error ("did you mean `list[str]`?").
* **Precompiled standalone module** (`appeal compile yourapp` emits a
  dependency-free Python module, Tidy-style): in scope, justified by
  fleet-scale MCP deployments where shaving startup off every tool call
  matters--not a cache, a deployment artifact.
* `app.mcp()`, `app.repl()`, shell completion, and colorization are all in
  scope as post-rewrite consumers of the plan tree.
* Keep supporting Python 3.6 (I develop on newer, but ship compatible).
* **Windows:** the pure-Python parser should just work; colorization is
  `isatty` + VT (no colorama); completion ships POSIX shells first,
  PowerShell later if ever.

**Explicitly NOT doing** (so a literal-minded implementer doesn't add them):

* `X | None` / union annotation spellings--`default=None` covers the common
  case; unions aren't worth the machinery.
* No `typing`-module constructs beyond `Annotated` (no `Literal`--use
  `validate()`; no `Protocol`/`ParamSpec`/etc.).
* No dedicated environment-variable feature--Python defaults + `os.environ`
  already compose.
* No public `render_argv` in the first pass (the argv generator exists only
  for the parity fuzzer).

**Open:**

* Whether nested-class subcommands' child `__init__` receives the parent
  instance.
* Exactly where to draw the "core" vs. "presentation" line inside the codebase
  so the docstring redesign doesn't force rework.
* Whether to expose the environment-injection mechanism (§8.6) to users for
  their own resources, or keep it internal.
* Config layering (§8.10)--listed and deferred, not scheduled.
* The `MultiOption` internal refinement (§8.4)--represent repeatability as a
  `repeat`+`fold` plan-node property recognized at compile time, demoting
  `MultiOption` to the custom-fold authoring path (kept, not removed);
  possibly thin its three-method protocol to a single `fold` function.
  Sounds right, not signed off.


## 12: History, and a thank-you to the machines

Appeal started in my head around 2012.  I named it in 2014.  I tried and failed
to write it several times, and only recently got a version I was happy with.
The core idea--signature as interface, recursion all the way down--never
changed; what took years was the engine.  Charm was the engine that finally
worked, and this proposal retires it with full honors: it discovered everything
it was built to discover.

The reviews that led here were done by a little lineage of Claude models--Opus
4.6 found and fixed a real bug in the argument grouper and did the first full
code review; Opus 4.8 swept up a pile more and started the coverage push; and a
Fable 5 session worked out the two reframings above and drafted this document.
Same family, successive drafts--which, I'm told, is more or less what
"predecessor" means.

The through-line of my career keeps turning out to be "find the place where the
interface is already fully described, and build the compiler that reads it."
Argument Clinic did it for C.  Appeal does it for the command-line.  Tidy does
it for text templates.  Appeal v2 is that instinct applied to Appeal itself:
stop *interpreting* the description, and *compile* it--because Python already
handed us the description, parsed, for free.


---


## Addendum (2026-07-05): what the implementation actually did

*Written after the fact, because the code quietly declined part of
this proposal and nobody wrote that down.  Larry read the proposal
on a plane, then the implementation, and reasonably couldn't
reconcile them.  This section is the missing bridge.*

### The coroutine (§4, §7) was not built

The proposal's rung 1 was a suspending tree-walker: a generator
that pauses with `yield NeedArgument(slot)`, a driver that owns
argv and feeds tokens back one at a time, and option scopes pushed
and popped as the walk enters and leaves rules.

None of that exists in the implementation.  There is no
`NeedArgument`, no driver, no generator anywhere in the parse
path.  It wasn't renamed; it dissolved--and the dissolving had a
specific cause:

**The distribution rule that won needs the total operand count
before the first decision.**  §3.1's counting decision matured,
during implementation, into leftmost-maximal-*completable*: each
slot takes the most operands it can, subject to "what remains must
be a total the later slots can still consume."  Answering that
requires knowing `n`--which means tokenizing ALL of argv before
filling any slot.  And once a single up-front pass
(`parse_tokens`) has split argv into operands and options, the
walker has nothing to suspend for.  `yield NeedArgument(slot)`
became `operands[i]`.  The driver became `parse_tokens`.  The
suspension machinery, having no remaining job, evaporated.

So the shipped shape is **two passes over flat data**, not one
pass over a stream:

    parse_tokens:  argv -> (operands, given)     token syntax only
    fill:          operands x plan tables -> your function's args

Rung 1 (the tree-walking reference implementation) walks and
indexes; rung 3 emits straight-line Python (`if remaining - 2 in
(0, 2):`), not generators.  The "generated source is the
disassembly" promise survived intact--arguably improved, since
plain code beats generator code for readability--and the fuzz-
rung-3-against-rung-1-forever plan survived verbatim.

### The fossils this left, and the resurrection plan

The departure wasn't free; its consequences shaped the semantics,
and then Larry ruled on them:

* The driver's *option scopes* (push on enter, pop on leave)
  became flat recognition--every option in the tree recognized
  anywhere on the line.  When this surfaced as a semantic
  question, Larry ruled it the DESIRED behavior ("easy breezy";
  see appeal.grammar.md, Options), with two riders: the gate
  rule (a required group walls off later options until fed) and
  windows (position binds occurrences to instances under *args
  repetition).
* A family of features was deferred under the label "the
  streaming driver": sibling-converter windows, greedy
  option-group operands, fancier Option.option() signatures.
  That label is no accident--**"the streaming driver" IS this
  proposal's §7 coroutine**, waiting in exile.  Each deferred
  feature is one that genuinely wants token-at-a-time consumption
  with live scopes.  If those features ever matter enough, the
  coroutine comes back as a THIRD consumer of the same plan tree,
  alongside the two rungs--not as a rewrite of them.

### Naming drift (under review)

* "The interpreter" (rung 1's module) took its name from this
  proposal's phrase "the reference implementation and the
  interpreter."  Larry's critique, which stands: that names the
  module's role in a testing activity, not its nature.  Its
  nature is: it interprets the plan tree.  Rename pending.
* `NeedArgument` never existed in code.  The nearest object in
  the implementation is `Terminal`--the passive tree marker meaning
  "this slot consumes one string"--which is a noun because the
  tree never acts; the consumers do.  Rename (possibly to
  `Terminal`, the grammar-theory word the spec already uses)
  also pending.

### What did survive, scorecard

    plan tree as inert data          built as proposed
    control flow is not data         built as proposed
    rung 1 / rung 3, fuzzed forever  built as proposed
    generated source = disassembly   built as proposed (plain code)
    linecache registration           built as proposed
    the coroutine walker             NOT built; see above
    token-at-a-time driver           NOT built; became parse_tokens
    option scopes push/pop           NOT built; ruled away (gates+windows instead)
    rung 2 (closures)                skipped, as proposed
