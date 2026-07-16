# Shell completion & colorization (API proposal)

*Originally a proposal; every decision point is marked ⚖ and
numbered, and all eight were ruled on 2026/07/07 (rulings inline,
marked ✔).  As of 2026/07/08 it is IMPLEMENTED--completion and
colorization both; bash, zsh, and fish--and Part 3 below is the user
walkthrough.*

Terms used below, defined first: **the north star** is Appeal 1.0's
standing rule that every feature must work in generated standalone
scripts or refuse by name.  **Scissors** snippets are marked,
self-contained source regions one project borrows from another;
appeal's own script-side code isn't scissored--it all lives in
appeal/runtime.py, which is streamed whole into every standalone
script.
**Reentry** means a program being called back by the shell's
completion function to answer "what could come next?" from its own
tables.  **The trio** is big's word-wrap trio (wrap_words /
split_text_with_code / merge_columns), which formats help text at
runtime.  **SGR** strings are raw ANSI escape codes
(`\x1b[1;36m`).


## Part 1: shell completion

### What exists

`app.complete(words, prefix) -> [str]` -- the engine.  Given the
words already typed and the partial word at the cursor, it returns
candidates: option strings on a `-` prefix (minus used singles,
nothing in value positions or after `--`), command words at the
command position, help topics.  Empty list = "no opinion", which
shells treat as "fall back to filenames."  Tested, shipping.

What's missing is everything around it: how the shell asks, how
values complete, and how standalone scripts join in.

### The reentry protocol (how bash asks your program)

The standard trick (click, argparse+shtab): the completion
function the shell sources doesn't know your grammar--it calls
YOUR PROGRAM back in a hidden mode, and the program answers from
its own tables.

Proposed: an environment-variable reentry, invisible to the argv
space (no reserved option strings, no reserved command words):

    _APPEAL_COMPLETE=bash    <- set only by the shell function
    COMP_WORDS / COMP_CWORD  <- the shell's own protocol

`app.main()` checks for it before anything else:

    def main(self, argv=None):
        if '_APPEAL_COMPLETE' in os.environ:
            return self._answer_completion()      # print + exit 0
        ...

⚖ **1: env reentry vs. a hidden option vs. a `completion`
command.**  Env is invisible and collision-proof; a hidden
`--appeal-complete` option pollutes the option space; a
`completion` subcommand pollutes the command space.  I propose
env.  (Cost: someone with `_APPEAL_COMPLETE` exported gets weird
behavior; the shell function sets it per-invocation so this is
theoretical.)

✔ **RULED: environment reentry, gated on empty argv.**  The
protocol only engages when the program is run with NO arguments
(the completion callback and the source one-liner both invoke
bare; the shell's data travels in COMP_WORDS/COMP_CWORD, never
argv).  An accidentally-exported _APPEAL_COMPLETE thus only
affects bare invocations.

### Getting the script into the shell

    app.completion(shell) -> str      # the bash/zsh/fish function text

and the user wires it however they like:

    mytool completion bash > /etc/bash_completion.d/mytool   # if they add a command
    eval "$(env _APPEAL_COMPLETE=source_bash mytool)"        # click-style one-liner

⚖ **2: do we ALSO auto-provide it?**  Options: (a) API only
(`app.completion(shell)`), user decides how to expose it; (b) the
reentry protocol answers a special `source_bash` request, so the
eval one-liner works with zero user code; (c) an automatic
`completion` command next to `help`.  I propose (a)+(b): the API
for deliberate wiring, the eval-trick for zero-effort, no new
auto-command.  (c) is one ruling away if you want it.

✔ **RULED: (a)+(b), no (c).**

### Value completion: converters get a say

Today the engine has no opinion about VALUES (`--color <TAB>`
falls back to filenames).  But `validate('red', 'green')` KNOWS
its values.  The protocol, in your the-object-is-the-API
style, as ruled (TOOWTDI--one form only): **a converter may
carry a `completions` attribute, always a callable**, signature
`(prefix: str) -> tuple of str`.  The engine always calls it,
always passing the prefix (the partial value text under the
cursor; empty string when nothing's typed yet--the common case).
The prefix is a hint (the engine prefix-filters the result
regardless); the return type is exactly a tuple of str.
Signature checked at build time, return type at query time,
violations refused by name:

    def color(name):
        ...
    color.completions = lambda prefix='': ('red', 'green', 'blue')

    def branch(name):
        ...
    branch.completions = lambda prefix: git_branches(prefix)

Emission is free: converters reach standalone scripts by being
imported, and the attribute rides along--lambdas included.

* `validate(...)` products get it automatically (their values).
* `appeal.validate_range(1, 5)` with int type could offer '1'..'5'
  when small; probably too cute--proposed: no.
* The completion engine, when the cursor sits in a value position
  (today's "return []"), consults the expecting converter's
  `completions` and filters by prefix.

⚖ **3: the attribute name.**  `completions` (plain, friendly,
tiny collision risk with user attributes) vs `__appeal_completions__`
(ugly, collision-proof) vs a registration API
(`app.completions(converter, ...)`--indirection, meh).  I propose
plain `completions`, matching how `counter`/`accumulator` are just
plain names.

✔ **RULED: `completions`, with the always-callable contract
above.**

⚖ **4: operand completion.**  Same protocol: a positional
parameter's converter carries `completions`.  A parameter without
one stays "no opinion" -> filenames.  Also: should the automatic
`help` topic completion offer command names?  (Already does.)

✔ **RULED: yes to both.**

### Standalone scripts (the north star)

The emitted script must complete too.  The engine's `_scan` +
candidate logic is small and table-driven; proposal: it moves into
runtime.py (the file streamed whole into every script), standalone
scripts get the same env-reentry in their `run_main`, and
`completions` attributes render like everything else (tuples as
literals; callables must be importable or recipes--the usual
refusal rules).

⚖ **5: is standalone completion v1.0 scope, or a follow-up?**
It's maybe a day of work.  I propose: same release, because the
north star's whole point is that features don't get to skip it.

✔ **RULED: same release.**


## Part 2: colorization

### What gets colored

The two humans-look-at-it surfaces: `--help` output and error
messages.  Concretely, these spans:

    usage: mytool [-v|--verbose] [-t|--times times] shape [width]
    ^prefix ^prog  ^option        ^option  ^metavar  ^operand

    arguments:            <- heading
      shape   the shape   <- label column / description
    options:              <- heading
      -v|--verbose  ...   <- label column

    error: wrong number of arguments...   <- 'error:' prefix

### The Theme object

Your rule: the object you pass IS the API.

    theme = appeal.Theme(
        program='bold',
        option='cyan',
        metavar='dim',
        operand='',
        heading='bold',
        error='bold red',
        summary='',
        )
    app = appeal.Appeal(theme=theme)

* Values are a tiny symbolic language: space-separated words from
  {bold, dim, italic, underline, red, green, yellow, blue,
  magenta, cyan, white, bright-*}.  Compiled once to SGR strings.
  Empty string = leave alone.
* `appeal.Theme()` (no args) = the stock theme.  `theme=None` on
  Appeal = **auto**: stock theme when stdout isatty, monochrome
  otherwise.  `theme=False` = never.  Auto mode adopts CPython's
  own `_colorize` precedence wholesale (an appeal program and a
  stock argparse program respond identically to the same shell):
  PYTHON_COLORS beats NO_COLOR beats FORCE_COLOR, then TERM=dumb,
  then isatty.
* **Slot references**: slot names join the symbolic vocabulary.
  A slot's value may include other slot names, which expand to
  that slot's resolved words (`metavar='option dim'`).  Resolved
  once at Theme construction, in dependency order; reference
  cycles and unknown words are refused by name.  This is also
  the additive-growth mechanism: a future new slot ships with a
  default that *references* the old slot it specializes, so
  older themes style it sensibly automatically.

⚖ **6: symbolic strings vs raw SGR vs style objects.**  Symbolic
strings are readable and theme files (perky!) could hold them.
Raw escapes as an escape hatch: a value starting with `\x1b`
passes through.  I propose symbolic + passthrough.

✔ **RULED: symbolic strings** (with the passthrough--which also
lets someone paste values straight out of a CPython _colorize
theme).  For the record: 3.14/3.15 argparse theming was examined
and is *not* the model--private/global/raw-SGR--but its env
semantics (above) and its 13-slot taxonomy (a menu for future
slots) were adopted as cheat material.

Also to consider during the build (ruling deferred to the
composable-docs work): **big.template** for applying colors--
recursive help templates whose interpolations dynamically look
up colorization (or not) from the theme; big.template already
has the two candidate engines (rich eval-based
parse/eval_template_string, and the cheap Formatter).  The two
forces to resolve there: §8.7's brace-collision argument favors
partition templates, and the north star favors whatever engine
fits in the warehouse.

⚖ **7: granularity.**  The seven-ish slots above, or fewer
(text/emphasis/error), or more (per-section)?  I propose the
seven; fewer loses the option/metavar distinction that makes
usage lines readable.

✔ **RULED: the seven, plus slot references (above).**  Growth is
additive-only; argparse's thirteen slots are the pre-priced menu
if demand appears.

### The implementation subtlety worth deciding up front

ANSI codes have zero display width, but `len()` counts them--so
coloring text BEFORE wrapping breaks the trio's arithmetic (lines
overflow, columns misalign).  Proposal: **layout first, paint
second.**  The renderers compute the uncolored layout exactly as
today, then apply styles to whole known spans (a usage unit, a
table label, the 'error:' prefix)--never to text that has already
been wrapped mid-span.  This costs a little expressiveness (no
coloring arbitrary words inside wrapped prose) and buys total
immunity from width bugs.  The wrap-then-paint helpers live in
runtime.py so standalone scripts get identical output.

⚖ **8: does the theme travel into standalone scripts?**  The
theme is data (symbolic strings) -> renders as a literal, so yes,
trivially--the script bakes the theme it was emitted with, still
honoring isatty/NO_COLOR at runtime.  Propose: yes.

✔ **RULED: yes.**  What travels is the theme and the auto
*behavior*, never the emission-time decision: the script
re-evaluates isatty and the env vars at its own runtime, so a
script emitted on a tty still renders monochrome into a pipe.

### Not proposed (deliberately)

* Coloring inside docstring prose (width bugs, and it's the
  user's text, not ours).
* Markup in docstrings (a whole language; separate discussion if
  ever).
* Colored output from the user's own command (their business).


## Part 3: Using it--tab completion with zsh, start to finish

This walks you from an Appeal program to working tab completion
in zsh.  (bash users: same steps, spell every `zsh` below `bash`;
the fallback and sourcing details differ only inside the emitted
shell function.)

### 0: The program

Any Appeal program works.  This one has a converter with
opinions about its values:

    #!/usr/bin/env python3
    import appeal

    app = appeal.Appeal(name='scoop')

    def flavor(name):
        return name
    flavor.completions = lambda prefix='': (
        'vanilla', 'chocolate', 'pistachio')

    @app.global_command()
    def scoop(cone, taste: flavor = 'vanilla', *, sprinkles=False):
        """
        Serves a scoop.

        Arguments:
          cone: which cone to fill.
          taste: which flavor to serve.
        """
        print('scoop', cone, taste, sprinkles)

    if __name__ == '__main__':
        import sys
        sys.exit(app.main())

Install it on your PATH as `scoop` (or use the standalone script
`app.standalone()` emits--completion works identically there; the
engine and the reentry protocol travel inside the script).

The `completions` attribute is the whole value-completion
protocol: always a callable, taking the partial word typed so far
(empty string when there's nothing yet), returning a tuple of
strings.  Appeal filters by the prefix again itself, so the
argument is an optimization hint, not an obligation.  A converter
without the attribute means "no opinion", and no opinion falls
back to zsh's filename completion.  Options complete themselves
(`scoop --s<TAB>` -> `--sprinkles`) with no code at all--the
grammar already knows.

### 1: Wire it into zsh

One line in your `~/.zshrc`, after your `compinit` call:

    eval "$(env _APPEAL_COMPLETE=source_zsh scoop)"

That's the zero-effort spelling: run bare with that variable set,
the program prints its own completion function and the `compdef`
that registers it; `eval` installs both.  If you'd rather keep a
file (and skip running the program at shell startup), save it
once and source that instead:

    env _APPEAL_COMPLETE=source_zsh scoop > ~/.zsh/scoop-completion.zsh
    # then in ~/.zshrc, after compinit:
    source ~/.zsh/scoop-completion.zsh

(Programmatically, the same text is `app.completion('zsh')`.
Either way the shell function must land *after* `compinit`,
because it uses `compdef`.)

### 2: Try it

Open a fresh shell (or `source ~/.zshrc`) and type:

    $ scoop <TAB>            # filenames: `cone` has no opinion
    $ scoop x <TAB>          # vanilla  chocolate  pistachio
    $ scoop x p<TAB>         # pistachio
    $ scoop x pistachio --s<TAB>   # --sprinkles

### 3: How it works (so the failure modes make sense)

Every TAB re-runs your program, invisibly: the emitted shell
function copies zsh's completion state (`words`, `CURRENT`) into
environment variables (`COMP_WORDS`, `COMP_CWORD`, converting
zsh's 1-based index to the protocol's 0-based one), sets
`_APPEAL_COMPLETE=zsh`, and runs `scoop` with NO arguments.
Appeal notices the variable--only ever on an empty command
line--answers the question from its own grammar tables instead of
parsing, prints one candidate per line, and exits.  The function
feeds those to `compadd`; no candidates means `_files` (filename
completion).

Consequences worth knowing:

* **Startup time is completion latency.**  Every TAB pays one
  interpreter start plus your program's imports.  Standalone
  scripts are brisk (they import almost nothing); an in-process
  program that imports heavy things at module level will feel it
  at the TAB key.
* **The reentry only fires on a bare command line.**  An exported
  `_APPEAL_COMPLETE` in your environment can only perturb
  argument-less runs; anything with arguments parses normally.
* **A completion callable that returns the wrong type raises,
  loudly, into your TAB.**  That garbled line is deliberate--a
  silent empty list would hide the bug forever.  The signature
  (callable, one positional argument) is checked much earlier, at
  build time.
* **compdef unknown?**  Your `compinit` hasn't run yet--move the
  eval/source below it.

### 4: fish

Same story, `fish` for `zsh`:

    env _APPEAL_COMPLETE=source_fish scoop > ~/.config/fish/completions/scoop.fish

fish's courier sends the current token's *text* rather than an
index (that's what fish can cheaply provide); the reentry handles
both dialects.  Filename fallback comes from
`__fish_complete_path`.  (Caveat, honestly: bash and zsh couriers
are exercised against the real shells in the test suite; fish
isn't installed on the development machine, so its courier is
protocol-tested but not shell-verified.  First fish user gets to
confirm it--or file the bug.)


## Build order (as ruled)

0. **Composable documentation first** (proposal §8.7)--it hasn't
   shipped, the current help renderer is a stopgap, and
   colorization's paint-after-layout should integrate with the
   real template renderer once, not twice.  (big.template
   consideration lives there.)
1. Theme + paint-after-layout in help/usage/errors (small, self
   contained, immediately visible).
2. `completions` protocol + value completion in the engine.
3. Env reentry + `app.completion(shell)` for bash; zsh/fish after.
4. Completion into the scissors + standalone reentry.
5. Tests at each step; the corpus ratchet guards the rest.
