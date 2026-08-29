# Appeal

## A CLI library that reads your mind by reading your function signatures

##### Copyright 2021-2026 by Larry Hastings

> *Write the function. Appeal writes the command line.*

**Appeal** turns an ordinary Python function into a command-line program.
You write a function--positional parameters, keyword-only parameters,
annotations, a docstring--and Appeal reads its signature and builds the
whole command-line interface for you: the argument parsing, the type
conversion, the options, the help, all of it. No parser to construct, no
decorators stacked on every parameter, no help strings duplicated off to
the side. The function *is* the specification.

Appeal is fast, too. It imports in about 1.6 milliseconds and parses a
command line in under one--single-digit milliseconds start to finish,
several times faster than argparse and roughly ten times faster than
Click, Typer, or Fire. (There's a table below; it's not close.)

And it does the modern stuff you'd expect a 1.0 to do: it renders Markdown
in your help, it colors its output, it does shell tab-completion, it reads
config files, and it can turn your commands into an MCP server so an AI can
call them as tools--all from that same one function signature.

Appeal runs on Python 3.6 and up. A few of the shorthand spellings in this
document--`list[str]`, `dict[K, V]`, `tuple[int, int]`, and `X | None`--are
themselves 3.9-or-newer syntax; on older Pythons reach for the equivalent
`appeal.accumulator` / `appeal.mapping` / `typing` forms instead. Everything
else works everywhere.


## Quickstart

This program:

```Python
import appeal
app = appeal.Appeal()

@app.precommand()
def greet(name, count: int = 1, *, loud=False):
    "Greet someone, possibly repeatedly."
    for _ in range(count):
        message = f"HELLO, {name.upper()}!" if loud else f"Hello, {name}."
        print(message)

app.main()
```

gives you this command line:

```
% python3 greet.py Alice
Hello, Alice.

% python3 greet.py Alice 3 --loud
HELLO, ALICE!
HELLO, ALICE!
HELLO, ALICE!

% python3 greet.py
error: missing argument 'name'
usage: greet name [count] [-l|--loud]
```

Look at what you *didn't* write. You didn't say `name` is a required
argument--it has no default, so of course it's required. You didn't say
`count` takes an integer--you annotated it `int`, so of course it does.
You didn't say `--loud` is a flag--it's keyword-only with a boolean
default, so of course it's a flag, and of course it gets a `-l` too. You
didn't write a line of help-formatting code; the docstring became the help.

That's the whole idea. You already tell Python all of this when you write
the function. Appeal just reads it.


## Overview

### The idea

Every command-line library makes you say the same things twice. You write
a function that takes a filename and a count and a verbose flag--and then
you write it *again*, in the library's dialect: `add_argument('filename')`,
`add_argument('--count', type=int, default=1)`,
`add_argument('--verbose', action='store_true')`. Or a stack of
`@click.option(...)` decorators. Or `filename: str = typer.Argument(...)`.
Same information, twice, and now they can drift apart.

Appeal deletes the second copy. Your function signature is already a
precise, machine-readable description of what your command accepts:

* **positional parameters** become command-line **arguments**,
* **keyword-only parameters** become command-line **options**,
* **annotations** become **type converters**,
* **defaults** decide what's **required** and what's **optional**,
* and the **docstring** becomes the **help**.

You write the function once. That's the specification. Appeal reads it.

### Why Appeal

* **Nothing to duplicate.** The signature is the source of truth. There's
  no second description to keep in sync.
* **It's fast.** ~1.6 ms to import, under 1 ms to parse. Your `--help`
  shouldn't cost more than your program.
* **Batteries included, dependencies not.** Markdown help, color, tab
  completion, config files, a REPL, and an MCP server--with no dependency
  on Click or Rich. (Appeal uses [big](https://github.com/larryhastings/big)
  for help rendering, and imports it *only* when you print help.)
* **It scales down and up.** `app.main()` on a one-function script is the
  floor; subcommands, subcommand trees, class-based programs, and
  converter groups are the ceiling. You never pay for what you don't use.
* **Your commands aren't just for humans.** The same signatures generate a
  JSON schema and an MCP tool server, so an LLM can call your program the
  same way a person does.

### New in Appeal 1.0

If you used Appeal 0.6, the headline is a from-scratch engine: a signature
compiles once into a small internal plan, and a streaming scanner runs
that plan. It's dramatically faster and the semantics are finally nailed
down (there's a whole section at the end for the fussy rules). Also new:
Markdown help, the `stylesheet=` coloring model, the MCP server,
`process()` returning an inspectable `Processor`, per-precommand config
layering (`@app.precommand(config=...)`), and eager validation of your
whole command tree at startup. See the *Changelog* at the end for the
full list.

### How fast, exactly

Time to import the library plus parse a trivial command line, in
milliseconds, median of 32 cold runs, excluding Python's own startup
(CPython 3.14, one machine):

| library         | import | parse | total |
| :-------------- | -----: | ----: | ----: |
| **Appeal**      |   1.6  |  0.9  | **2.6** |
| docopt          |   4.4  |  0.4  |   4.8 |
| docopt-ng       |   7.3  |  0.6  |   8.0 |
| Click           |  13.0  |  0.9  |  13.9 |
| argparse (stdlib) | 3.0  | 11.9  |  15.3 |
| Typer           |  32.0  |  0.6  |  32.7 |
| Fire            |  44.3  |  3.4  |  47.7 |

argparse is cheap to import but slow to build its parser; Click, Typer, and
Fire are slow to import (Typer drags in Rich, which alone costs more than
Appeal's entire startup). Appeal is the only one that's fast on *both*
counts.


## The pieces, and what they're called

A quick vocabulary, because the rest of this document leans on it.

Consider this command line:

```
% backup --verbose sync ~/photos /mnt/backup --exclude '*.tmp'
```

* `backup` is the **program**.
* `sync` is a **command**--the thing the program *does*. A program can have
  many commands (`git commit`, `git push`); Appeal calls those
  **subcommands**.
* `~/photos` and `/mnt/backup` are **arguments** (a.k.a. **operands**)--the
  positional values the command operates on.
* `--verbose` and `--exclude` are **options**--named, usually optional
  settings. `-v` would be a **short option**; `--verbose` a **long** one.
* `'*.tmp'` is `--exclude`'s **oparg**: the value that belongs to an
  option. (Options that take a value take opargs; flags don't.)
* A **converter** turns a command-line string into a Python value--`int`,
  `float`, your own function, whatever you annotate with.
* A **group** is a converter built from *several* command-line words that
  also brings its own options--think of it as a little sub-command mixed
  into your parameters. (Groups are the deep end; there's a section for
  them near the bottom.)

And here's the single rule that maps Python onto all of that:

```
              positional parameters  |  keyword-only parameters
                    |         |      |         |
                    v         v      |         v
    def sync(source, dest, *, verbose=False, exclude=None):
                    ^         ^      |         ^
                    |         |      |         |
                 argument  argument |        option
```

**Positional parameters become arguments. Keyword-only parameters become
options.** That's it. Everything else--required vs. optional, what type,
how many--Appeal reads from the details.


## Hello, World!

Let's go slowly through the smallest real program. In all these examples
we'll assume your script is called `script.py`.

```Python
import appeal
app = appeal.Appeal()

@app.command()
def hello(name):
    print(f"Hello, {name}!")

app.main()
```

Run `python3 script.py hello world` and you get `Hello, world!`. A lot
happened for four lines of setup; let's go over it piece by piece.

* We made an `Appeal` object, `app`. It handles the command line and calls
  the right function.
* We decorated `hello()` with `@app.command()`. That registers `hello` as a
  command, using the function's name as the command word and its parameters
  as the command-line parameters.
* `hello()` takes one positional parameter, `name`, with no default--so on
  the command line `hello` takes one required argument.
* `app.main()` reads `sys.argv`, runs the right command, and exits with its
  return code.

Appeal also gave you help for free. Run `python3 script.py help hello` (or
`python3 script.py hello --help`) and you'll see:

```
usage: hello name
```

**The return value is your exit code.** Return `None` or `0` for success;
return a nonzero integer for failure. (A function that just falls off the
end returns `None`, i.e. success.)

**And bad input never reaches your function.** Miss an argument, pass an
unknown option, hand `--count` something that isn't an integer--Appeal
prints a polite error and the command's usage, and exits 2. Your function
is only ever called with input that already type-checked. That's the deal.
(Errors go to stderr, so a program downstream in a pipe never receives an
error message as data. Want everything on one stream? `Appeal(errors=sys.stdout)`.)


# The cookbook

The rest of the middle of this document is organized by what you're trying
to *do*. Find your goal, copy the recipe, adjust to taste.


## Make an argument optional

Give the parameter a default. No default means required; a default means
optional.

```Python
@app.command()
def connect(host, port=8080):
    ...
```

`connect example.com` runs with `port=8080`; `connect example.com 9000`
overrides it. In usage, optional arguments wear brackets: `connect host [port]`.


## Give an argument a type

Annotate it. The annotation is a **converter**--any one-argument callable
that turns a string into the value you want.

```Python
@app.command()
def wait(seconds: float):
    ...
```

Now `wait 2.5` calls `wait(2.5)`, a real float. Annotate with `int`,
`float`, `pathlib.Path`, `complex`--anything callable. If your annotation
raises `ValueError` (as `int('x')` does), Appeal catches it and turns it
into a clean usage error; your function never sees the bad value.

You can annotate with a function of your own, too:

```Python
def celsius(s):
    return (float(s) - 32) * 5 / 9

@app.command()
def forecast(temperature: celsius):
    ...
```

`forecast 72` hands your function `22.2...`. A converter is just a function;
there's nothing special about it.


## Add an option or a flag

Make the parameter keyword-only (put it after a bare `*`). Its default
tells Appeal what kind of option it is.

```Python
@app.command()
def build(target, *, jobs: int = 1, verbose=False):
    ...
```

* `jobs` has an `int` default, so `--jobs` (and `-j`) takes an integer
  oparg: `build all --jobs 4`.
* `verbose` has a boolean default, so `--verbose` (and `-v`) is a **flag**:
  present means `True`, absent means the default.

Appeal derives a long option from the parameter name and a short option
from its first letter, automatically. To pick the strings yourself, see
*Rename an option* below.

### A flag that's on by default

Just default it to `True`. Presence stores the *opposite* of the default,
so a `True` default gives you an off-switch:

```Python
@app.command()
def render(*, color=True):    # --color turns it OFF
    ...
```

That reads oddly until you see it in the wild: it's how you spell
`--no-color` without inventing a second option. (Want the explicit
spelling? `--color=false` sets it literally.)


## Count how many times a flag was given

The classic `-v -v -v` (or `-vvv`). Annotate with `counter()`:

```Python
import appeal

@app.command()
def run(*, verbose: appeal.counter() = 0):
    print(verbose)
```

`run -v` prints `1`, `run -vvv` prints `3`. `counter()` is one of Appeal's
built-in **MultiOptions**--options designed to be given more than once.


## Collect a list

Annotate the option with `list[T]`, or with `appeal.accumulator`. Each
occurrence appends:

```Python
@app.command()
def find(*, exclude: list[str] = []):
    ...
```

`find --exclude '*.tmp' --exclude '*.log'` gives you
`exclude=['*.tmp', '*.log']`, and each element is converted with `T`
(`list[int]` would give you integers).


## Collect a mapping

Annotate with `dict[K, V]` (or `appeal.mapping`). Each occurrence is one
`KEY=VALUE` token:

```Python
@app.command()
def deploy(*, define: dict[str, int] = {}):
    ...
```

`deploy --define workers=4 --define retries=2` gives you
`{'workers': 4, 'retries': 2}`--the keys converted with `K`, the values
with `V`. (Giving the same key twice is an error, by name.)


## Restrict a value to a set of choices

Use `validate(...)`:

```Python
import appeal

@app.command()
def go(direction: appeal.validate('north', 'south', 'east', 'west')):
    ...
```

`go north` is fine; `go sideways` fails with a usage error naming the legal
choices. There's also `validate_range(low, high)` for numbers.


## Read a file (or stdin)

`appeal.file(...)` opens the argument for you and hands your function the
open file, closing it when the command finishes. The conventional `-` means
stdin/stdout:

```Python
@app.command()
def wc(infile: appeal.file('r') = '-'):
    print(len(infile.read().split()))
```


## Accept "one or more" (or "zero or more") arguments

Use `*args`. It greedily consumes the remaining arguments:

```Python
@app.command()
def cat(*files):
    for f in files:
        ...
```

`cat a.txt b.txt c.txt` gives `files=('a.txt', 'b.txt', 'c.txt')`. Annotate
it (`*files: pathlib.Path`) to convert every element. And you can put a
*required trailing* argument after a greedy group--the `cp SRC... DST`
shape--with a converter group: a converter whose `*args` absorbs the
sources, then an ordinary positional after it. (A keyword-only parameter
maps to an option, and options are always optional, so it can't be the
required trailing one.)

```Python
def sources(*src):
    return src

@app.command()
def cp(src: sources, dest):
    ...
```

`cp a b c /backup` fills `src=('a','b','c')` and `dest='/backup'`.
Appeal reserves the last argument for `dest` before handing the rest to
the `sources` group.


## Turn several words into one value

Annotate with `tuple[...]`. Each element is its own converter, and the tuple
consumes exactly that many words:

```Python
@app.command()
def plot(point: tuple[int, int]):
    ...           # plot 3 4  ->  point=(3, 4)
```

Put a `tuple[...]` on `*args` and Appeal chunks the stream by the tuple's
size--`*points: tuple[int, int]` reads `1 2 3 4` as `((1,2),(3,4))`, and a
count that doesn't divide evenly is a clean "left over" error.


## Give one parameter several option names

The `@app.option` decorator maps extra strings (or *replaces* the automatic
ones) for a single keyword-only parameter:

```Python
@app.command()
@app.option('verbose', '-v', '--verbose', '--loud')
def run(*, verbose=False):
    ...
```

Naming the strings yourself suppresses the automatic ones, so
`@app.option('output', '--output')` gives you `--output` with *no* auto
`-o`. Stack several `@app.option` calls to accumulate. Its sibling
`@app.argument(name, usage=...)` renames how a parameter shows up in usage
and help (the operand name, or an option's metavar).


## `make -j`: an option with an *optional* value

You know `make -j`: bare `-j` means "as many jobs as you like," `-j4` means
four. That's an option whose oparg is *optional*. By default an option that
takes a value **requires** it (`-f file`); to mark the value optional, wrap
the converter in `appeal.optional[...]`:

```Python
import appeal

@app.command()
def build(*, jobs: appeal.optional[int] = 1):
    ...
```

Three cases, and this is the whole rule:

* **absent** (`build`) → the parameter default, `1`.
* **bare** (`build --jobs`, or `-j`) → `int()`, i.e. `0`. The oparg's type,
  called with no arguments. (For `optional[str]`, bare gives `''`.)
* **given** (`build --jobs 4`, `-j4`, `--jobs=4`) → `int('4')`, i.e. `4`.

The bare case gives you a distinguishable sentinel--`0` here--so `make`'s
"unlimited" spelling has somewhere to land. If you'd rather bare `-j` mean
something specific (say `math.inf`), write a tiny converter with its own
default and annotate with *that* instead; it works the same way.


## Add subcommands

Give the program more than one command and each becomes a subcommand,
`git`-style:

```Python
@app.command()
def push(remote='origin'):
    ...

@app.command()
def pull(remote='origin'):
    ...
```

Now `script.py push` and `script.py pull` both work, and `script.py` with
no command prints the list of commands. To nest deeper, use the child
returned by `app.command('name')`:

```Python
db = app.command('db')

@db.command()
def migrate(revision):
    ...            # script.py db migrate 3
```

A command that has subcommands can still have its *own* body and options--
it runs when you invoke it, whether or not you name a subcommand.
(Appeal never forces you to pick a subcommand; that's your program's call,
not the library's.) And several commands can run on one line--`db migrate 3
push`--each executing as the scanner reaches it. That's **cycling**; see
the fussy bits for exactly when a set repeats.

### The precommand

Register a function with `@app.precommand()` and it runs *first*, on every
invocation, to handle program-wide options before any command:

```Python
@app.precommand()
def setup(*, verbose=False):
    ...
```

Now `script.py --verbose push` runs `setup(verbose=True)` and then `push`.
(A precommand with a required positional argument, and nothing else, is how
you write a single-function program you invoke with no command word--that's
the Quickstart at the top.)

Precommand is **repeatable**: decorate several and they run front-to-back,
each its own era, before any command. They run in registration order; pass
`index=` to place one explicitly.

A precommand doesn't have to be a function--it can also be a **method**, a
**class**, or a bound inner class. A class precommand constructs an instance
(see *Make a whole program out of a class*), and that class's own methods and
inner classes can themselves be precommands that bind to the instance. When
they are, Appeal runs the class's precommand before any of its members'--it
has to, since they need the instance the class builds.

To feed a config file into a precommand's options, bind a dict to it with
`@app.precommand(config=...)`--see *Reading config files*.


## Make a whole program out of a class

Sometimes the commands share state. Make the class the program: decorate it
with `@app.precommand()`, and its `__init__` becomes the global command.
Appeal constructs one instance per run, and the class's methods--decorated
with `@app.command()` like any other command--bind to that instance:

```Python
app = appeal.Appeal()

@app.precommand()
class Tool:
    def __init__(self, *, verbose=False):
        self.verbose = verbose

    @app.command()
    def status(self):
        ...

    @app.command()
    def deploy(self, target):
        ...
```

`script.py --verbose deploy prod` constructs `Tool(verbose=True)` and then
calls `.deploy('prod')` on it. `self` is the real object, so the commands
share state through it.

`@app.default()` works on a method too--the command that runs when the line
names none binds to the instance just like the others. Nested classes and
bound inner classes become subcommand trees, constructed through the parent
instance. And a method can be a `@app.precommand()` era of its own class,
running (bound) before the commands.

(For code written against Appeal 0.6, `app.app_class()` returns
`(app_class, command_method)` decorators--a thin compatibility shim over
exactly this.)


# Documenting, coloring, completing, and configuring


## Writing help

Appeal builds usage automatically. For prose, it reads your **docstring--as
Markdown**. Headers, bold, italics, lists, all of it, rendered to the
terminal:

```Python
@app.command()
def sync(source, dest):
    """
    Sync **source** to **dest**.

    Copies new and changed files only. Deletes nothing unless you
    ask with `--delete`.
    """
```

The first line becomes the one-line summary in the command list; the rest is
the command's help page. You never write help *strings*--the documentation
you'd write anyway *is* the help.

### Documenting individual arguments and options

To describe individual parameters, add a Markdown **heading** named
`Arguments`, `Options`, or `Commands` (any level, and the case doesn't
matter), and under it a **definition list**: the parameter's name on its own
line, then its description on the next line after a `:`.

```Python
@app.command()
def serve(host, port: int = 8080, *, verbose=False):
    """
    Serves the thing.

    Longer prose about serving, wrapped and formatted for you.

    ## Arguments

    host
    : The host to serve on.

    port
    : The port. Defaults to 8080.

    ## Options

    verbose
    : Print more output.
    """
```

Ask for `serve`'s help and you get:

```
usage: net serve [-v|--verbose] <HOST> [<PORT>]

Serves the thing.

Longer prose about serving, wrapped and formatted for you.

Arguments
---------

<HOST>  The host to serve on.
<PORT>  The port.  Defaults to 8080.

Options
-------

-v|--verbose  Print more output.
```

The crucial detail: you name the **parameter** (`host`, `verbose`), and
Appeal rewrites it to the real command-line spelling for you--`<HOST>` in the
argument list, `-v|--verbose` in the options. You document the parameter;
Appeal knows how it shows up.

And the documentation is **composable**, exactly like the converters
themselves. Write a converter group's parameter docs once, in that
converter's *own* docstring, and every command that uses it inherits
them--with the command free to override any entry (nearest scope wins).
Document a `Logging` group once; every command that mixes it in is documented
for free.

To reshape the page itself--reorder the sections, retitle a heading, add
prose blocks--see `appeal.documentation.md`.


## Color

Appeal colors its help and errors through a **stylesheet**--a plain data
dict mapping semantic names (like "usage" or "heading") to colors, resolved
per output stream at print time. `Appeal(stylesheet=...)` sets it;
`stylesheet=None` picks colors per stream automatically (and never colors a
pipe); `stylesheet=False` never colors at all. It's Appeal's own layer--no
Rich, no plugins.


## Tab completion

Appeal generates a shell completion script for bash, zsh, or fish:

```
% python3 script.py completion zsh > _script
```

Source it (or drop it where your shell looks) and you get command,
option, and--where your converters opt in--value completion. See
`appeal.completion.md` for wiring it into each shell.


## The REPL

`app.repl()` drops the user into an interactive prompt that runs your
commands line by line--the same commands, no `python3 script.py` prefix,
with history. Handy for exploratory tools.


## Reading config files

Bind a configuration dict to a precommand with `@app.precommand(config=...)`,
and its option values layer under the command line: defaults, then config,
then argv--argv always wins, whole values at a time. You bind the dict when
you register the precommand and fill it later (Appeal holds the *same*
object), so an empty dict you `.update()` before `main()` is seen:

```Python
settings = {}                       # bind now, fill before main()

@app.precommand(config=settings)
def main(*, editor='vi', jobs: int = 1):
    ...

settings.update(read_my_rc_file())  # e.g. TOML/JSON/environ--your choice
app.main()
```

Config only ever supplies *option* values; it never changes structure, and a
key that isn't one of that precommand's options is an error, by name. Each
precommand that wants config binds its own dict--there's no single global
slot and nothing to guess about which precommand a mapping is for. (Appeal
reads no file formats itself--you hand it a dict, from wherever you like.)


## Running a command from a dict, a list, or a CSV

The same function, invoked without a command line at all. `load.py`'s
readers run a command from structured data, routed through the *same*
converters:

```Python
app.read_mapping(deploy, {'target': 'prod', 'workers': '4'})
app.read_iterable(deploy, ['prod', '--workers', '4'])
app.read_csv(deploy, csv_reader)
```

Write the parameter logic once; it validates identically whether the input
is a terminal string, a config dict, or a row of a spreadsheet.


## The Processor: one run, inspected

`app.process(argv)` returns a **Processor**--the object representing that
run, in the spirit of `subprocess.Popen`. It's not reusable; it *records*.

```Python
proc = app.process(['deploy', 'prod'])
proc.result      # your command's return value
proc.instances   # the (command, instance) log, in the order they ran
```

`app.main()` is the shortcut for scripts: it runs, handles errors politely,
and exits with the code. `app.process()` is the one you call when you want
the value back instead of an exit.


## MCP: your commands as AI tools

Because your commands are already precisely typed functions, Appeal can hand
them to an AI. `app.schema()` produces a JSON schema of every command and
its parameters; `app.mcp()` runs a Model Context Protocol server exposing
them as callable tools. An LLM sees the same `deploy(target, *, workers:
int)` a human sees on the command line--names, types, help and all--and
calls it the same way. No second definition, no adapter: the signature that
built your CLI builds your tool schema.


## Errors

When the command line is wrong, Appeal prints a diagnostic and the failing
command's usage to stderr and exits 2 (the getopt/argparse convention). Your
command function never runs on bad input. If your command *itself* wants to
fail cleanly--bad data, not bad syntax--raise `appeal.CommandError(message,
exit_code)`; Appeal prints the message (no usage; the line was fine) and
exits with your code.


# Recursive Converters (here be dragons)

You can skip this section and be productive forever. It's the deep end, and
Appeal works beautifully without you ever coming here. But it's also the
thing that makes Appeal *Appeal*, so: here be dragons.

A converter doesn't have to be a one-argument function. It can be any
callable with a signature--and Appeal reads *that* signature the same way
it reads a command's. A converter that takes two parameters consumes two
command-line words. A converter with keyword-only parameters brings its own
*options*. A converter whose parameter is *another* converter nests. This
is a **converter group**: a little command folded into one of your
parameters.

```Python
def point(x: int, y: int):
    return (x, y)

@app.command()
def draw(shape, center: point):
    ...              # draw circle 3 4  ->  draw('circle', (3, 4))
```

`center` is one parameter, but it eats two words and builds a point.
Give the group its own options and they appear on the command line, bound to
that group. Nest groups inside groups and the options nest with them. Reuse
the same group across several parameters and Appeal figures out, by
position, which one each option belongs to.

This is genuinely powerful and genuinely mind-bending, and the exact rules--
how a group's options are recognized, how they bind when groups repeat, when
an option "conjures" a group into existence--live in the fussy bits below,
because that's where rules like this belong.


# For users of other CLI parser libraries

If you're coming from somewhere else, here's the Rosetta Stone.

## For argparse users

The mental shift: **stop building a parser, start declaring a function.**
argparse is imperative--you construct a parser and call `add_argument` over
and over. Appeal is declarative--you write the function and it reads the
signature. There's no parser object in your code at all.

| argparse | Appeal |
| :--- | :--- |
| `add_argument('name')` | a positional parameter `name` |
| `add_argument('name', nargs='?')` | a positional parameter with a default |
| `add_argument('--count', type=int, default=1)` | `*, count: int = 1` |
| `add_argument('--verbose', action='store_true')` | `*, verbose=False` |
| `add_argument('-v', action='count')` | `*, verbose: appeal.counter() = 0` |
| `add_argument('--tag', action='append')` | `*, tag: list[str] = []` |
| `add_argument('files', nargs='*')` | `*files` |
| `add_argument('x', choices=[...])` | `x: appeal.validate(...)` |
| `subparsers` + `add_parser` | `@app.command()` per function |
| the help string in `add_argument(help=...)` | one word in your docstring |

And it imports ~2× faster and parses ~12× faster (see the table).

## For Click users

Click and Appeal look similar--both decorate functions--but Click puts the
specification *in the decorators*: `@click.option('--count', type=int,
default=1)` stacked above the function, with the parameter listed again
below. Appeal puts the specification *in the signature*: `count: int = 1`,
once. Click reads your docstring for the command's help; Appeal reads it too,
and renders it as Markdown, and also reads per-parameter help from it--so
your parameters carry no `help=` kwargs. And Appeal has no dependency on
Click or Rich, so it starts ~5× faster.

| Click | Appeal |
| :--- | :--- |
| `@click.argument('name')` | positional parameter `name` |
| `@click.option('--count', default=1)` | `*, count=1` |
| `@click.option('--verbose', is_flag=True)` | `*, verbose=False` |
| `@click.option('-v', count=True)` | `*, verbose: appeal.counter() = 0` |
| `@click.option('--tag', multiple=True)` | `*, tag: list[str] = []` |
| `click.Choice([...])` | `appeal.validate(...)` |
| `@click.group()` / subcommands | `@app.command()` per function |
| `help="..."` on each option | your docstring |

### For Typer users

Typer is the closest library to Appeal in spirit--it, too, reads type hints
on your parameters--so you'll feel at home immediately. The differences:

* **Speed.** `import typer` pulls in Rich, which alone costs more than
  Appeal's entire startup. Appeal is ~13× faster to start, and has no
  Click/Rich dependency.
* **Help lives in the docstring, not in the defaults.** Typer's per-option
  help goes in `typer.Option(help="...")` wrappers on the parameter
  defaults; Appeal reads it from the docstring, leaving your signature
  clean.
* **Converter groups.** A Typer parameter is a flat value; an Appeal
  parameter can be a whole nested group with its own options (the dragons
  section). Typer has no equivalent.
* **`make -j`-style optional opargs, cycling, and a REPL**, all built in.

## For docopt / docopt-ng users

docopt goes the other way from everyone: you write the help text and it
infers the parser. It's charming, but it hands you back only strings, bools,
and counts--*you* convert every value by hand, and there's no type
information anywhere. Appeal derives the parser from the *function*, and
gives you converted, typed values for free. (docopt-ng, the maintained
fork, does no conversion either--and its added type hints made it slower to
import than the original.)


# The fussy bits

Everything below is a precise rule you can ignore until you hit it. This is
the reference for Appeal's corner cases--the semantics that had to be
*decided*, written down so they're decided the same way every time. If the
cookbook is "how," this is "exactly how."

## How Python maps to the command line

| in the signature | on the command line |
| :--- | :--- |
| positional parameter, no default | required argument |
| positional parameter, with default | optional argument |
| `*args` | zero-or-more repetition |
| keyword-only, no default | **error**: options are always optional, so give it a default (for the `cp SRC... DST` shape, use a converter group) |
| keyword-only, with default | option |
| annotation = `int`/`float`/callable | a converter (one word in, one value out) |
| annotation = `bool` default | a flag (presence stores `not default`) |
| annotation = `counter()`/`list[T]`/`dict[K,V]` | a repeatable option |
| annotation = `tuple[T1, ...Tn]` | one slot consuming n words, building a tuple |
| a multi-parameter callable annotation | a converter *group* |
| positional-only marker `/` | no grammatical meaning |

## Fill left to right, and never skip to skip

Arguments fill positional slots strictly left to right, greedily. Appeal
never skips an optional slot in order to fill a *later* optional one--there's
no "completability search." The one permitted skip is over optionals to
reach a *required trailing* argument (the `cp SRC... DST` case). If the
words don't fit left-to-right, that's an arity error, not a puzzle Appeal
solves for you.

## How a `-`-then-digit token is read

Is `-3` the option `-3`, or the number negative three? Appeal decides with a
three-step rule, applied to any token that's a dash followed by a digit:

1. If it parses completely as short options (each character a registered
   short option, optionally ending in an attached oparg), it's options.
2. Otherwise, if `float()` accepts it, it's an argument (a negative number).
3. Otherwise, raise the error step 1 would have raised (unknown option).

So `-3` is the option only if you actually registered a `-3`; otherwise it's
the number. `-2.5` with no such options is an argument; `-xv` is options; a
mapped `-2 -4 -3` bundle works only if all three are registered. (Appeal
doesn't support `-inf`/`-nan` as arguments--they don't start with a digit.)

## Short-option bundling

Several no-oparg short options combine after one dash: `-xvf` is `-x -v -f`.
An option that takes a value ends the bundle and takes the rest as its
value: `-o file.txt`, or attached, `-ofile.txt`. Digits bundle like letters
(subject to the negative-number rule above). A group option that consumes no
operands is treated as a flag for bundling--so a bundle continues right
through it.

## Eras and regions: when an option gets mapped

The command line runs left to right, and it's divided into spans. Knowing the
spans tells you exactly which function a given `--option` lands on--it's the
one idea behind "options anywhere," several commands on one line, and shared
group options alike.

**Eras** are the top-level spans. Any precommands you registered run first,
in the order you registered them, followed by the global command; these are
the *head eras*, and they always run. Then come the command words, each
command its own span. The scanner crosses these boundaries left to right, and
here's the key rule: **an option is recognized anywhere inside its own era,
and its reach ends at the boundary.** Appeal registers each era's options up
front, so within an era order doesn't matter--`--verbose sync src dst` and
`sync src dst --verbose` are identical--but once the next era begins, the
previous era's options are no longer live.

**Regions** are one level down. A converter *group* owns a sub-span inside
its command, and groups bring their own options--flags, single-value, and
fold options (`counter`/`accumulator`/`mapping`), plus the options of groups
nested inside them. When the *same* option string belongs to several group
instances--a converter reused across sibling parameters, say--occurrences
bind **by position**, along the *interval model*:

* Each instance owns a **region**: from its first word up to the next
  instance's first word.
* The **first** instance's region reaches back to the start of its command's
  span--so an option typed before any instance's words binds to the first
  instance ("announce-first").
* The **last** instance's region runs to the end of that span.

### A first example: how a command starts a new era

The simplest way to see eras is with plain positional arguments and no options
at all. Each era eats exactly the positionals it declares; then the next token
is free to be a command word, which selects the next era.

```Python
app = appeal.Appeal('tool', repeat=True)

@app.precommand()
def setup(user):       # one positional argument
    ...

@app.command()
def build(target):     # one positional argument
    ...

@app.command()
def test(suite):       # one positional argument
    ...
```

Run `tool alice build app test unit`. It carves into three eras (`||` marks
each boundary; `(n)` numbers the spans):

```
(1)alice || (2)build app || (3)test unit
```

1. **era 1 -- `setup`, the precommand.** Precommands always run first, with no
   command word of their own. `setup` wants one argument, takes `alice` as
   `user`, and--now saturated--yields.
2. **era 2 -- `build`.** The precommand is full, so the next token, `build`, is
   read as a *command word*: it **selects** the `build` command and opens a new
   era. `build` takes `app` as `target` and yields.
3. **era 3 -- `test`.** Same move: `test` selects another command and opens
   era 3, taking `unit` as `suite`.

There isn't a single option in that line--the *positionals* alone demarcate
the eras. An era ends the moment its command has all the arguments it needs,
and the next word is then read as a command that selects a fresh era. (Running
`build` and `test` on one line is *cycling*, which is why the app set
`repeat=True`.)

### A worked example: eras, regions, and conjuring at once

Here's a program that exercises the whole model--two precommands, two
commands, and a command that reuses a converter group (regions) and includes
a conjurable one:

```Python
app = appeal.Appeal('tool', repeat=True)

@app.precommand()
def auth(*, token=None):        # --token / -t   (takes a value)
    ...

@app.precommand()
def logging(*, verbose=False):  # --verbose / -v  (a flag)
    ...

@app.command()
def status(*, short=False):     # --short / -s
    ...

# a group that NEEDS a name, so it can't be conjured; reused twice below:
def host(name, *, port=22):     # a required NAME, plus --port
    ...

# a group whose arguments are ALL optional, so it CAN be conjured:
def tls(*, insecure=False):     # --insecure
    ...

@app.command()
def link(src: host, dst: host, sec: tls):
    ...
```

Now this line uses every part of it. `||` marks an **era** boundary; `|`
marks a **region** boundary inside the `link` era; the `(n)` tags number the
spans:

```
(1)--token abc || (2)-v || (3)status || (4)link --port 2200 web1 | (5)web2 | (6)--insecure
```

Walk it left to right:

1. **Enter era 1, `auth`** (the first precommand). Appeal maps auth's options
   (`--token`/`-t`). It reads `--token abc`, reaches `-v`--not auth's--and
   yields.
2. **Cross `||` into era 2, `logging`.** Appeal throws away auth's options and
   maps logging's (`--verbose`/`-v`). Now `-v` binds--logging's verbose. The
   next token, `status`, is a command word, so logging yields.
3. **Cross `||` into era 3, `status`.** The head eras are over; the command
   eras begin. Appeal maps status's options; status takes no arguments and the
   next token, `link`, is another command word, so status runs and yields.
4. **Cross `||` into era 4, `link`, and open the first region, `src`.** `link`
   begins filling its first parameter, `src`, a `host` group--so Appeal maps
   host's options *for src*. `--port 2200` binds to src; `web1` becomes src's
   `name`. src now has its required name, so its region closes.
5. **Cross `|` into the second region, `dst`.** Same group, next instance:
   Appeal **remaps** host's options, so from here `--port` would write to dst.
   `web2` becomes dst's `name`.
6. **Cross `|` into `sec`, a conjurable group.** The last parameter is a `tls`
   group whose arguments are all optional. `--insecure` **conjures** `sec` and
   sets it. (Leave `--insecure` off and `sec` is still built, with its
   defaults--an all-optional group always gets built.)

Two things worth pinning down:

* **Regions exist only because `host` is reused.** `--port` needs the interval
  model to say which `host` it means; `--insecure` doesn't--there's only one
  `tls`, so it's recognized anywhere in the `link` era, before or after the
  hosts. A region is strictly a tie-breaker for a *shared* option string.
* **Conjuring turns on whether the leading arguments are required.** `tls`
  conjures because it needs no operands; `host` cannot--`link --port 2200`
  with no name gives `--port only becomes available if you specify <NAME>`. An
  option can *ask* for a group, but it can't invent a required operand. (More
  in *Conjuring*, next.)

The whole picture, top to bottom: the line is a sequence of eras; a command's
span may contain group regions; an option maps to the innermost span that
owns it, recognized anywhere within that span. That single idea is
"options anywhere," multi-command binding, and the interval model at once.

## Conjuring

Giving one of a group's options *conjures* the group into existence, even if
no operand for it appeared--the option is enough to say "this group is
present." Conjuring only ever builds an all-defaults group (it can't invent
required operands), and it happens at most once per group per line. An
option whose group needs a required operand, given with no operand to
follow, is an error naming the option.

## Unreachable options are a configuration error

If you map an option that a later same-named option immediately overwrites--
two zero-operand groups sharing an option string, so the second *stomps* the
first and the first can never be reached--that's a `ConfigurationError`.
(Windowed groups that consume operands sit at different positions and scope
by the interval model, so they're fine; only same-spot collisions stomp.)

## Eager compilation, and `lazy=`

By default (`Appeal(lazy=False)`) the first `process()`/`main()` compiles
*every* command's plan across the whole tree, so a configuration error
anywhere--a bad annotation, a stomped option--surfaces at startup instead of
lurking until someone runs that one command. It costs about 0.05 ms per
command. `Appeal(lazy=True)` restores build-on-demand, where each command
compiles only when first invoked.

## Streaming dispatch

Commands run **as the scanner reaches them**, left to right--not parsed all
up front and then run. A structural error (bad arity, unknown option) is
caught up front, before anything runs; but a *conversion* error partway down
the line doesn't un-run a command that already ran. A command that returns a
nonzero integer halts the rest of the line (it's an exit code).

## Command-line parsing rules (POSIX-like, with extensions)

Appeal's line syntax is POSIX/GNU-like: if you know how `ls` and `grep` read
their arguments, you already know most of it. It is *not* bug-for-bug
argparse.

**What it honors, from the POSIX Utility Syntax Guidelines and GNU:**

* Short options are a single dash and one letter (`-v`); long options are two
  dashes and a name (`--verbose`).
* Short flags **bundle**: `-abc` is `-a -b -c`. (See *Short-option
  bundling*.)
* An option's value may be **attached** or **separate**: `-j4`, `-j 4`,
  `--jobs=4`, and `--jobs 4` all pass `4` to `--jobs`.
* `--` **ends option processing**: everything after it is a positional
  argument, even if it starts with a dash.

**The extensions and deliberate departures:**

* **Options are recognized anywhere on the line**, freely interleaved with
  positional arguments--not "all options, then all arguments." `sync -v a b`
  and `sync a b -v` and `sync a -v b` are the same. (An option only *maps*
  where its converter is in scope--see *Eras and regions*.)
* **The long `--opt=value` form is only for options that take exactly one
  value.** A multi-operand option (`--where X Y`) refuses the attached form;
  give its values space-separated. `=` with nothing after it (`--name=`) is
  the empty string. `=` is a *long*-option separator only: on the short
  spelling `-o=value` binds the literal oparg `=value` (getopt-style, like
  `-DNAME=1`); use `-ovalue` or `-o value` to attach a bare value.
* **Flags take an explicit boolean with `=`, long spelling only**:
  `--verbose=false` turns off what a config file turned on--exactly `true`
  or `false`, nothing else.
* **A `-`-then-digit token is disambiguated**, so `-5` can be a negative
  number *or* short options depending on what's defined. (See *How a
  `-`-then-digit token is read*.)
* **Converters can consume several operands** (`--where X Y`), and an
  all-optional converter can be *conjured* by one of its own options. (See
  *Conjuring*.)
* **Positionals fill left to right, greedily**, skipping an optional only to
  reach a required *trailing* argument (`cp SRC... DST`). (See *Fill left to
  right, and never skip to skip*.)
* **Dispatch is streaming**: commands run as the scanner reaches them, left
  to right. (See *Streaming dispatch*.)


# Reference

## API reference

A precise listing of the public surface. Everything here lives directly on
`appeal`: `import appeal`, then `appeal.Appeal`, `appeal.counter`, and so on.

### The `Appeal` object

```Python
Appeal(name=None, *, version=None, stylesheet=None, errors=None,
       repeat=False, lazy=False, script='-', margin=79, doc=None,
       default_options=..., default_mappings=...,
       positional_argument_usage_format='<{name.upper()}>')
```

The program. Construct one, decorate your functions onto it, and call
`main()` or `process()`.

* `name` — the program name shown in usage. Defaults to the script's name.
* `version` — the version string reported by `--version` / `print_version()`.
* `stylesheet` — the coloring stylesheet (a data dict). `None` picks colors
  per output stream automatically; `False` never colors. See *Color*.
* `errors` — where diagnostics go. Defaults to `sys.stderr`; a live knob.
* `repeat` — whether the program's set of commands may *cycle* on one line.
* `lazy` — `False` (the default) compiles every command's plan at first
  `process()`/`main()`, surfacing configuration errors at startup. `True`
  restores build-on-demand.
* `script`, `margin`, `doc`, `positional_argument_usage_format` — usage and
  help-layout knobs.
* `default_options`, `default_mappings` — the automatic `-x`/`--long`
  derivation policy; pass `None` to suppress the automatic short/long
  options, or a factory to customize. See `appeal.default_mappings`.

### Registering commands

Each of these is a decorator you apply to a function; each returns the
child `Appeal` (or the function) so you can nest.

* `@app.command(name=None, *, repeat=False, parent=None)` — register a
  command. The command word defaults to the function's name (dash-mangled:
  `sync_all` → `sync-all`). `app.command('db')` with no function returns a
  child `Appeal` you hang subcommands off of.
* `@app.precommand(*, index=-1)` — a command that runs *first*, every
  invocation, before command dispatch: for program-wide options and bare
  options like `--help`/`--version`.
* `@app.default()` — the command run when the line names none.
* `@app.subcommand(parent, name=None, *, repeat=False)` — register under a
  command-word path (`'db'`, `'db migrate'`); `command()` is
  `subcommand(None)`. The returned decorator is a reusable tear-off.
* `app.app_class()` → `(app_class, command_method)` — the class-as-program
  helpers (see *Make a whole program out of a class*). `__init__` becomes
  the global command; decorated methods become commands.

### Adjusting a parameter

* `@app.option(name, *options, annotation=None, default=...)` — give the
  keyword-only parameter `name` the option strings you list, *instead* of
  the automatic ones. Stack several to accumulate; pass `annotation`/
  `default` to override what the signature says.
* `@app.argument(parameter_name, *, usage=...)` (alias `@app.parameter`) —
  rename how a parameter shows up in usage and help.

### Running

* `app.main(args=None)` — for scripts. Reads `sys.argv[1:]` (or `args`),
  runs, prints errors politely, and calls `sys.exit()` with the command's
  return code. Does not return.
* `app.process(args=None)` → `Processor` — runs and returns the `Processor`
  for that run (below) instead of exiting. (Config isn't passed here--bind
  it per precommand with `@app.precommand(config=...)`.)
* `app.repl(*, prompt=None, banner=None)` — an interactive prompt that runs
  commands line by line.

`Processor` — the object `process()` returns; one run, not reusable.

* `.result` — the command's return value.
* `.instances` — the `[(command, instance), ...]` execution log, in order.
* `proc(args)` — a Processor is itself callable; `process()` is the shortcut
  that builds one, calls it, and returns it.

### AI, schema, and completion

* `app.schema()` — a JSON schema describing every command and its
  parameters.
* `app.mcp(*, config=None, version=None)` — run a Model Context Protocol
  server exposing your commands as tools.
* `app.completion(shell)` — emit a completion script for `'bash'`, `'zsh'`,
  or `'fish'`.
* `app.complete(words, prefix='')` — the completions for a partial line
  (what the shell script calls into).
* `app.documentation(format)` — render the program's documentation in the
  named format.

### Running from structured data

The same command, same converters, no command-line string:

* `app.read_mapping(callable, mapping)` — run `callable` from a
  `{parameter: value}` dict.
* `app.read_iterable(callable, iterable)` — run it from an argv-style list.
* `app.read_csv(callable, reader, *, first_row_map=None)` — run it per row
  of a CSV reader.

### The converter vocabulary

Annotate a parameter with one of these to shape its conversion.

* `appeal.optional[T]` — mark an option's oparg optional (the `make -j`
  case): absent → the parameter default, bare → `T()`, given → `T(value)`.
* `appeal.counter(*, max=None, step=1)` — count occurrences (`-vvv` → `3`).
* `appeal.accumulator` — collect each occurrence into a list (like
  `list[T]`).
* `appeal.mapping` — collect `KEY=VALUE` occurrences into a dict (like
  `dict[K, V]`).
* `appeal.validate(*values, type=None)` — restrict to a set of legal values.
* `appeal.validate_range(start, stop=None, *, type=None, clamp=False)` —
  restrict a number to a range (`clamp=True` clamps instead of erroring).
* `appeal.split(*separators, strip=False)` — split one word into several
  values on the given separators.
* `appeal.file(mode='r', *, buffering=-1, encoding=None, errors=None,
  newline=None, opener=None)` — open the argument as a file (`-` is
  stdin/stdout), handed to your function open and closed for you.
* `appeal.convert(converter, text, name, usage=None)` — run a converter
  by hand, the way Appeal does internally.
* `appeal.Option` (alias `appeal.MultiOption`) — the base class for writing
  your own option converters; `is_option()` / `is_multioption()` test an
  annotation.

### Exceptions

All descend from `appeal.AppealError`.

* `appeal.CommandError(message, exit_code=1)` — raise this from *inside* a
  command to fail cleanly: Appeal prints `message` (no usage--the command
  line was fine) and exits with `exit_code`.
* `appeal.UsageError` (a.k.a. `AppealUsageError`) — the command line was
  wrong (bad arity, unknown option, a value that wouldn't convert). Appeal
  raises this for you, prints the diagnostic and usage, and exits 2.
* `appeal.ConfigurationError` (a.k.a. `AppealConfigurationError`) — *your*
  setup is wrong (a stomped option, an un-analyzable signature). Surfaces at
  startup under the default eager compilation.

(The full alias set--`AppealBaseException`, `AppealError`, `DataError`,
`AppealDataError`, `AppealCommandError`--exists for precise `except` clauses;
the four names above are the ones you reach for.)

## Updating from 0.6.4

Most 0.6.4 programs run on 1.0 unchanged--the decorators and signatures mean
the same things. Here's the short list of what you may have to touch, in
rough order of how likely it is to bite.

* **`process()` returns a `Processor` now, not the value.** If you called
  `result = app.process(argv)` for the return value, it's now
  `result = app.process(argv).result`. (`main()` is unaffected--it still
  runs and exits.)
* **`Theme` is gone; use `stylesheet=`.** The old `Theme` class and its SGR
  mini-language are deleted. Coloring is now a plain data dict of semantic
  names to colors, passed as `Appeal(stylesheet=...)`. See *Color*.
* **Custom converters render through `__call__`, not `render()`.** If you
  wrote your own `Option`/`MultiOption` subclass, rename its `render()`
  method to `__call__()`.
* **`dict[K, V]` options take one `KEY=VALUE` token per occurrence.** If you
  relied on the older splitting behavior, check your `dict`-typed options.
* **Configuration errors surface at startup now.** Eager compilation is on
  by default (`lazy=False`), so a bad annotation or a stomped option that
  used to lurk until its command ran now raises at the first
  `process()`/`main()`. If you want the old build-on-demand timing, pass
  `Appeal(lazy=True)`.
* **Command names dash-mangle by default** (`upload_database` →
  `upload-database`). The underscore spelling is still accepted on the
  command line, so this rarely breaks anything--but the *displayed* name
  changed.
* **Two decorator renames** (old names still work as aliases, so this is
  optional): `global_command` → `precommand`, `default_command` → `default`.

Everything else--`@command`, positional-to-argument and keyword-only-to-
option mapping, annotations as converters, the `file`/`validate`/`counter`
vocabulary--is unchanged.

## Changelog

### 1.0

The big one, and the reason for the version bump: a from-scratch engine and
a settled semantics. Signatures now compile once to a small plan that a
streaming scanner runs--several times faster than 0.6, with all the corner
cases finally decided (they're in *The fussy bits*, above). Help is rendered
as Markdown, coloring moved to the `stylesheet=` model, `process()` returns
an inspectable `Processor`, your whole command tree is validated at startup,
and the same signatures now feed an MCP server, a JSON schema, tab
completion, and a REPL. The full itemized list:

**Engine**

* Brand-new interpreter. A signature compiles once into a small internal
  plan; a streaming scanner runs it. Several times faster; the old codegen /
  standalone / two-stage machinery is deleted.
* Dispatch is now **streaming**: commands run as the scanner reaches them,
  left to right, not parsed-all-then-run. A structural error (arity, unknown
  option) is still caught up front, before anything runs.
* Whole-line **structural pre-scan** validates the line's shape before the
  live conversion pass, so bad arity never half-runs a line.
* **Eager compilation by default** (`lazy=False`): the first
  `process()`/`main()` compiles every command's plan across the tree, so a
  `ConfigurationError` anywhere surfaces at startup. `lazy=True` restores
  build-on-demand.

**Semantics, pinned down**

* **Fill left to right, never skip to skip**: optional slots fill greedily;
  the only permitted skip is over optionals to reach a required *trailing*
  argument (`cp SRC... DST`).
* **`-`-then-digit rule**: parse-as-short-options first, else `float()` as a
  negative number, else raise the option error.
* **Options recognized anywhere** on the line, including a group's own
  options and the options of nested groups.
* **Converter groups contribute their own options** — flags, single-value,
  and fold options (`counter`/`accumulator`/`mapping`), including options on
  *nested* positional-slot groups (chain-conjuring).
* **Scoped options bind by the interval model**, with announce-first (a
  leading occurrence binds to the first instance).
* **A stomped (unreachable) option is a `ConfigurationError`.**
* **A command may have a body *and* subcommands**; subcommands are never
  required, and a parent runs its own body when none is named.
* **`*args: tuple[X, Y, Z]`** chunks the operand stream by the tuple's arity.
* **`optional[T]`** marks an option's oparg optional (the `make -j` case).
* Command names **dash-mangle** by default (`sync_all` → `sync-all`); the
  underscore spelling is accepted on lookup too, for commands and for
  `help TOPIC`.

**Presentation**

* Help prose is rendered as **Markdown**.
* Coloring moved to the **`stylesheet=`** model (a data dict resolved per
  stream); the old `Theme` class and its SGR mini-language are gone.
* Error rendering runs through the same stylesheet pipeline.

**New capabilities**

* **MCP server** (`app.mcp()`) and **JSON schema** (`app.schema()`).
* **Tab completion** (`app.completion(shell)` / `app.complete(...)`).
* A **REPL** (`app.repl()`).
* Run a command from structured data: `read_mapping` / `read_iterable` /
  `read_csv`, through the same converters.
* **Config layering binds per precommand**: `@app.precommand(config=<dict>)`
  layers that dict's values under the command line (defaults < config <
  argv). You bind the dict at registration and fill it before `main()`
  (Appeal holds the same object). Each precommand routes to its own dict:
  there's no `process(config=)` and no single "global command" slot, so
  config can split across several precommands with nothing to guess.

**API**

* `process()` returns an inspectable **`Processor`** (`.result`,
  `.instances`), in the spirit of `subprocess.Popen`; it's callable, one run,
  not reusable.
* `Appeal(...)` gained `stylesheet=`, `errors=`, `margin=`, `lazy=`, and
  `positional_argument_usage_format=`.
* `@app.subcommand(parent, ...)`, `@app.argument`/`@app.parameter`, and
  `app.app_class()` added; `@app.option` gained `annotation=`/`default=`.
* Two renames: `global_command` → **`precommand`**, and `default_command` →
  **`default`**. The old names survive as compatibility aliases, but the new
  spellings are what to reach for.
* `dict[K, V]` options take one `KEY=VALUE` token per occurrence.
* `MultiOption`/`Option` converters render through `__call__` (was
  `render()`) — a compatibility break for hand-written converters.
* `StrictOption` removed (no demand, no precedent).
