![## Appeal](https://raw.githubusercontent.com/larryhastings/appeal/master/resources/images/appeal.logo.png)

![## Give your program Appeal!](https://raw.githubusercontent.com/larryhastings/appeal/master/resources/images/give.your.program.appeal.png)

##### Copyright 2021-2026 by Larry Hastings

[![# test badge](https://img.shields.io/github/actions/workflow/status/larryhastings/appeal/test.yml?branch=master&label=test)](https://github.com/larryhastings/appeal/actions/workflows/test.yml) [![# python versions badge](https://img.shields.io/pypi/pyversions/appeal.svg?logo=python&logoColor=FBE072)](https://pypi.org/project/appeal/)


## Quickstart

```Python
import appeal

app = appeal.Appeal()

@app.command()
def hello(name):
    print(f"Hello, {name}!")

app.main()
```

Here's a simple ``fgrep`` utility:

```Python
import appeal
import sys

app = appeal.Appeal()

@app.command()
def fgrep(pattern, *files, ignore_case=False):
    if not files:
        files = ['-']
    print_file = len(files) > 1
    if ignore_case:
        pattern = pattern.lower()
    for file in files:
        if file == "-":
            f = sys.stdin
        else:
            f = open(file, "rt")
        for line in f:
            if ignore_case:
                match = pattern in line.lower()
            else:
                match = pattern in line
            if match:
                if print_file:
                    print(file + ": ", end="")
                print(line.rstrip())
        if file != "-":
            f.close()


if __name__ == "__main__":
    app.main()
```


## Overview

Appeal is a command-line argument processing library for
Python, in the same class of solution as *argparse*,
*optparse*, *getopt*, *docopt*, *Typer*, *Google Fire*,
and *click*.  But Appeal takes a refreshing new approach.

Other libraries have complicated, cumbersome interfaces
that force you to repeat yourself over and over.
Appeal leverages Python's rich function call interface,
making it effortless to define your command-line interface.
You write Python, and Appeal translates it into
command-line interfaces with options and arguments.

Appeal provides amazing power and flexibility--but it's
also intuitive, because it mirrors Python itself.
If you can write Python, you're ready to use Appeal!


Appeal's only dependency is
[my **big** library](https://github.com/larryhastings/big)--and
that's only at compile-time.

Appeal supports Python 3.6 and up.  It provides POSIX-style
command line semantics, making it best-in-class for UNIX,
Linux, BSD, and macOS--and, increasingly, Windows.

### A New And Appealing Approach

Appeal isn't like other command-line parsing libraries.
In fact, you really shouldn't think of Appeal as a
"command-line parsing library" per se. And, although you
work with Appeal by passing in functions for Appeal to call,
you shouldn't think of these functions as "callbacks".

Appeal lets you design *APIs* callable from the command-line.
It's just like any other Python library API--except that
the caller calls you from the command-line instead of from
Python.  Appeal is the mechanism converting between these two
domains: it translates your API into command-line semantics,
then translates the user's command-line back into calls to your API.

This raises another good point: the API you build using Appeal
also often makes for a very nice *automation API,* allowing
your program to also be used as a library by other programs
with minimal effort.

### New features in Appeal 1.0

Appeal 1.0 is a major rewrite.  It keeps Appeal 0.6's semantics and
APIs--it runs all the old tests--but adds a bounty of new marquee powers:

* **Standalone scripts.**  Appeal can *compile* your command-line
  parser, producing a *standalone Python script*.  The resulting
  script has no dependencies beyond Python itself--it doesn't even
  need Appeal installed!  The emitted script is also *fast*--a
  standalone Appeal parser takes single-digit *microseconds* to run.
  Not *mille-*, *micro-*.
* **Composable documentation**, rendered through templates
  you can reshape, and a color theme you can restyle.
* **Tab completion** for bash, zsh, and fish, answered by the
  same grammar that parses your command line--and it works in
  the standalone scripts too.
* **A class as your whole program**: `__init__` handles the
  global options, decorated methods are the commands.  The
  old "preparers" are gone, it *just works.*
* **Config layering**: bind a dict of settings to a precommand
  (`@app.precommand(config=...)`), fill it from your config
  file; the command line still wins.
* An interactive **REPL**, turning your command-line interface
  into a mini-shell.
* **MCP support.**  Writing tools for AI robots?  Appeal will
  not only ingest a JSON tool call blob, it'll generate the JSON
  Schema from your program.  And unlike other competing tools,
  Appeal supports not only nesting but full recursion.  You
  write Appeal like normal; the only new API is just "do the MCP
  thing now please".


## Basics

### Taxonomy

Let's start by establishing the terminology we'll use
for command-lines, based on command-line idioms established
by POSIX and by popular programs.  Here's a sample
command-line, illustrating all the various types of things
you might ever see:

    % ./script.py --debug add --flag ro -v -xz myfile.txt
      ^           ^       ^   ^      ^  ^  ^   ^
      |           |       |   |      |  |  |   |
      |           |       |   |      |  |  |   argument
      |           |       |   |      |  |  |
      |           |       |   |      |  |  multiple short options
      |           |       |   |      |  |
      |           |       |   |      |  short option
      |           |       |   |      |
      |           |       |   |      oparg
      |           |       |   |
      |           |       |   long option
      |           |       |
      |           |       command
      |           |
      |           global long option
      |
      program name

Command-lines are a sequence of strings separated by
whitespace.  The meaning of each string can depend
both on the position of the string and the characters
in the string itself.

An *argument* is any whitespace-delimited string on the
command-line that doesn't start with a `-` (minus sign).
Unless it's an *oparg*--which we'll talk about in a minute--the
meaning of an argument is defined by its position.  For example,
if you ran:

    fgrep WM_CREATE window.c

`WM_CREATE` and `window.c`
would be *arguments;* the first argument, `WM_CREATE`,
would be the string you wanted to search for, and `window.c`
would be the name of the file you wanted to search.

A *command* is a special kind of argument some programs
use to specify what function you want the program to perform.
A good example of a program that uses commands is `git`;
when you run `git add` or `git commit`, `add` and `commit`
are both *commands.*  The command is always the first
argument to a program that uses them.

If a string on the command-line starts with a `-` (minus
sign), that's an *option*.  There are two styles of
option: *short options* and *long options.*

*Short options* start with a single dash, `-`.  This is
followed by one or more individual characters, which
are the short option strings.  In the above example,
we specify two sets of short options: the first is `-v`,
the second is `-xz`.  You can combine options together,
and it's the same as specifying them separately.  We
could have said `-vxz`, or `-v -x -z`; these both do
the same thing.  When we talk about short options, we
say the word "dash" followed by the letter.  For example,
`-v` would be pronounced "dash v".

*Long options* start with two dashes, `--`.  Everything
after the two dashes is the name of the option.  In the
above example, we can see one long option, `--flag`.
Again, when we talk about long options, we say the
dashes out loud, followed by the words from the option.
For example, `--flag` would be pronounced "dash dash flag".

Both types of options can optionally take one (or more)
arguments of their own.  An argument to an option is
called an *oparg.*  In the above example, the long option
`--flag` takes the oparg `ro`.

Finally, there are *global options* and *command
options.*  Global options apply to the entire
program, are always available, and are specified
*before* the command.  Command options are
command-specific, and appear *after* the command.
Global options can be long options or short options;
command options can be long options or short options, too.


### Remapping Python To The Command-Line

Now let's consider a Python function call:

```Python
#      positional parameters |  keyword-only parameters
#            |        |      |      |
#            v        v      |      v
def fgrep(pattern, filename, *, ignore_case=False):
    ...
```

We can draw some similarities between Python
function calls and command-lines.

For example, they both support arguments where
position is significant.  A command-line *argument*
is similar to a Python function *positional*
parameter, in that they're both identified by
position.

Python function calls and command-lines also
both support arguments identified by name.
A command-line *option* is similar to a Python
*keyword-only* parameter.

> ***Note:*** In Python, all parameters after `*args` or `*` are
> keyword-only parameters.  You can only pass in arguments
> to those parameters by name.  If you called `fgrep`
> yourself in Python, you could only pass in a value for
> `ignore_case` like this:
>
>     result = fgrep(p, f, ignore_case=True)

This leads us to the fundamental concept behind Appeal.
With Appeal, you write a Python function, and tell
Appeal that it represents a *command.*  Appeal
examines the function, translating its parameters into
command-line features.  Positional parameters become
command-line arguments, and keyword-only parameters
become options.

(Technically, Appeal translates both *positional parameters*
and *positional-or-keyword parameters* into arguments.
For the sake of clarity and conciseness, I'll always
refer to these collectively as *positional parameters.)*


## Hello, World!

Let's see Appeal in action, with our first example.
In all our examples we're going to assume your program
is called `script.py`.  Let's say `script.py` looked like
this:

```Python
import appeal
app = appeal.Appeal()

@app.command()
def hello(name):
    print(f"Hello, {name}!")

app.main()
```

If you now ran `python3 script.py help hello`, you'd
see usage information for your `hello` command.
It'd start like this:

    usage: hello name

Already, a lot has happened!  Let's go over it piece by piece:

* We created an `Appeal` object called `app`.
  This object will handle processing the command-line
  and calling the appropriate command function.
* We decorated the function `hello()` with `@app.command()`,
  a method call on our Appeal object.
  This tells Appeal that `hello()` should be a
  *command*, using the name of the function as the
  command string, and translating the function's
  parameters into the command-line parameters.
  So our command-line command is called `hello`.
  We call a function decorated with `@app.command()`
  a *command function.*
* Our `hello()` command function takes one positional
  parameter, `name`.  Therefore, our `hello` command
  on the command-line takes one positional argument,
  which we identify as `name` in the usage string.
* Appeal also automatically created help for our
  program: a `help` command, plus `-h` and `--help`
  options on every command.  Usage shows you what
  command-line options and arguments the command
  will accept.

So!  If you ran this command at the command-line:

    % python3 script.py hello world

Appeal would call your `hello()` function like this:

```Python
hello('world')
```

and you'd be rewarded with:

    Hello, world!

The return value from your command function sets the exit
status for your program, by one rule: return an int and
that's your exit status, C-style--zero is success, nonzero
is failure.  Any other return value--or none at all--is
success, exit status 0.  A bool is not an int here, even
though Python pretends otherwise: `return True` means "it
worked", and it would be perverse for that to exit 1 just
because `True == 1`.  So `True`, `False`, strings, lists,
your own objects--all success.  To fail with a message,
raise `appeal.CommandError(message, exit_code)` instead.

(The int rule is for `@app.command()` functions, whose job
is doing things, not computing values.  If you're also
publishing your interface as an automation API and some
function's *answer* is an int--a count of rows, say--keep
that function separate and give Appeal a thin wrapper that
prints it: the API returns the value, the command reports
it.)

And if the user gets the command-line wrong--a missing
argument, an unknown option--Appeal prints a polite error
and the usage for the command that failed, and your program
exits with status 2.  Your command function is never called
with malformed input; that's the deal.  (Errors print to
standard error, the POSIX diagnostic convention, so a
pipeline reading your program's output never receives an
error message as data.  Prefer everything on one stream?
`Appeal(errors=sys.stdout)`.)


## Default Values And `*args`

Let's change up our example, and add an optional parameter:

```Python
import appeal
app = appeal.Appeal()

@app.command()
def fgrep(pattern, filename=None):
    print(f"fgrep {pattern} {filename}")

app.main()
```

Now our command is called `fgrep`, and it takes two parameters.
The second one, `filename`, is optional, with a default value of `None`.

You can of course specify both parameters yourself.  Running this:

    % python3 script.py fgrep WM_CREATE window.c

results in Appeal calling your `fgrep()` function like this:

```Python
fgrep('WM_CREATE', 'window.c')
```

But you can also omit the `filename` parameter.
If you run this command at the command-line:

    % python3 script.py fgrep WM_CREATE

Appeal would call `fgrep()` like this:

```Python
fgrep('WM_CREATE')
```

Notice Appeal only passes in the arguments you supplied on the
command-line; it's Python that fills in the default value
of `None` for the `filename` parameter.

What else can Appeal command functions do?  Well, they can
have a `*args` parameter. Naturally, a command function that
takes `*args` (internally called a *var_positional*
parameter) can accept as many positional arguments as the
user wants to supply.  Here's a demonstration:

```Python
import appeal
app = appeal.Appeal()

@app.command()
def fgrep(pattern, *filenames):
    print(f"fgrep {pattern} {filenames}")

app.main()
```

Now the user could pass in no filenames, one filename,
fifty filenames--as many as they want!  They'd all be
collected in a tuple and passed in to `fgrep()` in the
`filenames` parameter.

One more shape worth knowing: a command like `cp`, which takes
one or more sources and then a required destination at the
*end*.  Python won't let you write `def cp(*src, dest)` with
`dest` as a positional, and a keyword-only parameter maps to an
*option*--always optional (we'll get to that)--so that's no
help either.  The Appeal way is a *converter group*: a
converter whose `*args` absorbs the sources, with `dest` an
ordinary positional after it.  Appeal reserves `dest` from the
*end* of the command-line, so the group leaves room for it:

```Python
import appeal
app = appeal.Appeal()

def sources(*src):
    return src

@app.command()
def cp(src: sources, dest):
    print(f"cp {src} {dest}")

app.main()
```

The usage reads:

    cp [<SRC>]... <DEST>

Run `script.py cp a b c`, and Appeal fills `src` with `('a',
'b')` and `dest` with `'c'`--the *last* argument lands in
`dest`, and everything before it is collected by the group.


## Options, Opargs, And Keyword-Only Parameters

Now let's examine what Appeal does with keyword-only
parameters.  Let's add three keyword-only parameters
to our example:

```Python
import appeal
app = appeal.Appeal()

@app.command()
def fgrep(pattern, *filenames, color="", number=0, ignore_case=False):
    print(f"fgrep {pattern} {filenames} {color!r} {number} {ignore_case}")

app.main()
```

Now the `fgrep` command-line usage looks like this:

    fgrep [-c|--color color] [-n|--number number] [-i|--ignore-case] pattern [filenames]...

Again, a lot just happened.

First, I'll remind you, keyword-only parameters
are presented as options on the command-line.
Appeal automatically took each keyword-only parameter,
added `'--'` to the front of the parameter name,
and turned that into an option.  (Also, if the parameter
name has any underscores, Appeal turns those into dashes.)

Second, Appeal also automatically uses the first letter of a
keyword-only parameter as a short option.  So the
`color` keyword-only parameter becomes both the `--color`
*and* `-c` options.  When running your program, the user
can use `-c` or `--color` interchangeably.  The same goes
for `-i` and `--ignore-case`, and for `-n` and `--number`.

(What if you have two keyword-only parameters that start
with the same letter?  The first one gets the short option.
If we added a keyword-only parameter named `credit` to the
end of `fgrep()`'s parameter list, Appeal would map `color`
to `--color` and `-c`, but only map `credit` to `--credit`.
And a parameter whose name is a single character gets *only*
the short option: a parameter named `n` maps to `-n`, never
`--n`--a one-letter long option would just be confusing.)

Third, options are *always optional.*
(As a pedantic wag might put it--"the clue's right there in the name.")
Therefore, in Appeal, keyword-only parameters that map to
options must have a default value.  (A keyword-only parameter
*without* a default becomes a required trailing argument, as
we saw with `cp` above--precisely *because* it can't be an
option.)

Fourth, notice that `--color` takes an argument, or *oparg.*
Appeal noticed that the `color` parameter had a default
value of `""`--its default value is a `str`.
So Appeal infers that you want the user to supply an oparg
to `--color`.  If the user specifies `--color` on the
command-line, it must be followed by an oparg, and Appeal
will take the string off the command-line and pass it
straight into the `color` parameter.

Fifth, `--number` also takes an oparg, but it has a default of `0`.
Appeal infers from that that `--number` should be an `int`.
Appeal automatically converts the string from the command-line
into a Python object for you, using the type of the default value.
(Appeal did that for `--color` too--except `--color` takes a str,
so no conversion is necessary.)  When the user provides `--number`
on the command-line, it must be followed by an oparg; Appeal will
take that oparg, pass it in to `int`, then take the return value
from `int` and pass it in to the `number` parameter.

Finally, `ignore_case` has a default value of `False`.
Boolean values for options are a special case: they don't
take an oparg. All they do is negate the default value.
So if the user specifies `-i` once on the command-line,
Appeal would pass `True` in to the `ignore_case` parameter.

(By the way, a default value of `None` is a second
special case.  If a positional or keyword-only parameter
has a default value of `None`, Appeal behaves as if the
type of the default is `str`.  It consumes an argument
or oparg from the command-line and passes it in unchanged
to that parameter.)

Let's put it all together!  If you ran this command at the command-line:

    % python3 script.py fgrep -i --number 3 --color blue WM_CREATE window.c

Appeal would call `fgrep()` like this:

```Python
fgrep('WM_CREATE', 'window.c', color='blue', number=3, ignore_case=True)
```

And if you ran this command at the command-line:

    % python3 script.py fgrep --color green boogaloo

Appeal would call `fgrep()` like this:

```Python
fgrep('boogaloo', color='green')
```

Some option syntax worth knowing, all demonstrated on `--color`:

* `--color blue` and `--color=blue` both work.  For the short
  spelling it's `-c blue` or `-cblue`--the `=` form is a
  *long*-option convention only, exactly as in getopt.
* A flag takes an explicit boolean with `=`, on the long
  spelling only: `--ignore-case=false` and `--ignore-case=true`
  (exactly those two spellings--this is not the place for
  `yes`, `si`, or `naturally`).  A bare flag still means
  `True`; the explicit form exists so the command line can turn
  *off* what a config file turned on (see
  [Config layering](#config-layering)).
* Short options bundle: `-vxz` means `-v -x -z`.  A short
  option that takes one oparg ends its bundle, and binds
  either the next string (`-ic blue`) or the rest of its own
  token, getopt-style: `-cblue` and `-icblue` both mean `blue`.
  The rest binds *verbatim*--the `=` is not a separator here, so
  `-cblue` is `blue` but `-c=blue` is the literal oparg `=blue`,
  and `-dNAME=1` passes `NAME=1` whole (think `-DNAME=1`).
* Repeating an option is fine, and the last one wins:
  `--color red --color blue` means blue, exactly as in getopt,
  argparse, and click--it's what lets a shell alias bake in a
  default (`alias grep='grep --color=auto'`) and still be
  overridden (`grep --color=never`).  Options that *collect*
  their repetitions are one annotation away--see [Specifying An
  Option More Than Once](#specifying-an-option-more-than-once)
  below.
* `--` (two dashes alone) turns off option recognition for
  the rest of the command-line, so arguments can start with
  dashes: `fgrep -- -weird-pattern`.
* A negative number is an argument, not an option: `add -5 3`
  just works (unless your program actually defines a `-5`
  option, in which case you have only yourself to blame).


## Commands, The Global Command, And Subcommands

Many programs that support "commands" also have
"global options".  Global options are options
specified on the command-line *before* the command.
For example, in the example command-line at the top
of this document, `script.py` takes a `--debug`
option specified before the command--which makes it
a "global option".

Appeal supports global options, too.  It's simple:
write your command function like normal, but
instead of decorating it with `@app.command()`, decorate
it with `@app.global_command()`.  Appeal will process all
those options before the command, and call your global
command function--*before* it calls the command function.

`@app.global_command()` also gets used for programs that
don't use "commands".  Although the "command" command-line
paradigm is popular these days, most programs don't bother
with them.  For example, `ls`, `grep`, and... hey! `python`
itself!  None of these programs support commands, but they
all support command-line arguments and options.

Naturally, Appeal supports this behavior.  Simply decorate
one function with `@app.global_command()` and don't add
any command functions.  Now that function owns the whole
command-line.

On the flip side of this coin, Appeal also supports
*subcommands:* your command can *itself* be followed by
another command, `git remote add`-style.  To add a
subcommand to an existing command, name the parent command
in a call to `app.command()`, and use the object it returns
as your decorator:

```Python
import appeal
app = appeal.Appeal()

@app.command()
def db(label):
    print(f"db {label}")

db_commands = app.command('db')

@db_commands.command()
def deploy(version: int):
    print(f"deploy {version}")

app.main()
```

(On Python 3.9 and newer you can chain the calls directly--
`@app.command('db').command()`--thanks to PEP 614's relaxed
decorator grammar.  The two-step spelling above parses on every
Python Appeal supports.)

So now the whole command-line looks something like this:

    script.py [global options] db <label> deploy <version>

The parent command runs first--it's the "global command" of
its own little command set--then the subcommand.  Running
`script.py db main deploy 9` prints `db main` and then
`deploy 9`.

What should Appeal do if your program takes commands, but the
user doesn't supply one?  That's what the *default command* is
for.  If you don't specify one, Appeal treats an empty command
line as a request for orientation, not a mistake--it prints the
usage line and the list of commands to standard output (no
`error:` anywhere) and exits with status 1, like `git`.  To
specify your own default command, decorate a function with
`@app.default_command()`:

```Python
@app.default_command()
def default():
    return status()
```

Notice that the default command doesn't take any arguments
or options.  It simply can't accept any, by definition.
(If the user specified options without a command, they'd be
"global options", processed by the global command.  And if
the user specified an argument, that would automatically be
the name of the command to run.)

Two more things about command names.

First: the command word is the function's name, *literally*--
Appeal never renames your commands.  (Options get dash-for-
underscore treatment because `--ignore_case` looks silly;
commands don't, because the function name IS the command.)

Second: if you want a command word that isn't a valid Python
identifier--dashes, say--pass `name=` to `app.command()`:

```Python
import appeal
app = appeal.Appeal()

@app.command(name='add-item')
def add_item(x: int):
    print(f"added {x}")

app.main()
```

`name=` is you saying the command word out loud; Appeal uses
it exactly as written.

### Cycling: several commands on one line

Here's an Appeal 1.0 superpower.  Pass `repeat=True` to
`Appeal()`, and once a command's arguments are all satisfied,
the next argument may name *another* command--and the line
starts over.  The commands run left to right:

```Python
import appeal
app = appeal.Appeal(repeat=True)

@app.command()
def add(x: int, y: int):
    print(f"sum {x + y}")

@app.command()
def mul(x: int, y: int):
    print(f"product {x * y}")

app.main()
```

    % python3 script.py add 1 2 mul 3 4
    sum 3
    product 12

The rules are exactly what you'd hope:

* A command's *whole* signature must be satisfied--optional
  arguments included--before the next word can name a command.
  (A command taking `*args` never fills up, so it soaks up the
  rest of the line.)
* Appeal parses the *entire* command-line before running
  anything.  If the line is malformed anywhere, *no* work
  happens--that's an Appeal rule.  (A *conversion* failure--
  `add one 2`--is different: conversions happen as each
  command runs, so like `make`, the commands to the left have
  already run when the failure stops the line.)
* If a command returns a non-zero integer, the line halts
  there, and that's your exit status.
* Subcommand sets can cycle too--pass `repeat=True` to
  `app.command('db', repeat=True)` and the `db` set keeps
  accepting subcommands until a word pops back up to the
  outer set.


## Annotations And Introspection

Python 3 supports annotations for function parameters, meant
to conceptually represent types.  Appeal supports annotations
too; they explicitly tell Appeal what type of object a parameter
requires.  For example:

```Python
import appeal
app = appeal.Appeal()

@app.command()
def fgrep(pattern, *filenames, id: float = None):
    print(f"fgrep {pattern} {filenames} {id}")

app.main()
```

Here `id` has a default value of `None`, but it also has
an explicit annotation of `float`.   If the user uses `--id`
on the command-line, it must be followed by an oparg,
which Appeal will convert to a Python object by calling `float`.
(And, as you can see, the annotation and the type of the default
don't *necessarily* have to agree... although it's usually a
good idea.)

Although annotations are *meant* to represent types, Appeal
actually accepts any callable--it can be a type, or a
user-defined class, or just a regular function.  Appeal
calls these annotations *converters.*

Here's how Appeal decides on the converter for a parameter,
from highest-priority to lowest-priority:

* If the signature for that parameter has an annotation,
  Appeal uses the annotation as the converter.
* If the signature for that parameter *doesn't* have an
  annotation, but *does* have a default value, Appeal
  will use `type(default)` as the converter in most cases.
  The exceptions:
  - If `type(default)` is `NoneType`, Appeal will use `str`
    instead.
  - If `type(default)` is `bool`, and the parameter is a
    keyword-only parameter, the option becomes a flag that
    negates the default, as we saw above.
* If the signature for that parameter lacks both an annotation
  *and* a default value, Appeal uses `str` as the converter.

Converters are surprisingly flexible.
For example, Appeal will introspect the converter for a
keyword-only parameter and map all its positional parameters
into opargs.  That's how Appeal supports options that take
*multiple opargs:* you simply annotate the keyword-only
parameter with a converter that takes *multiple parameters.*
Appeal will also pay attention to the annotations for the
converter's own parameters, and use those to convert the
strings from the command-line into Python objects.

Let's tie it all together with another example:

```Python
import appeal
app = appeal.Appeal()

def int_and_float(integer: int, real: float):
    return [integer*3, real*5]

@app.command()
def fgrep(pattern, *filenames, position: int_and_float = (0, 0.0)):
    print(f"fgrep {pattern} {filenames} {position}")

app.main()
```

Here, Appeal would introspect `fgrep()`, then also
introspect `int_and_float()`.  The resulting usage
string would now look like this:

    fgrep [-p|--position integer real] pattern [filenames]...

`--position` takes *two* opargs.  Appeal would
call `int` on the first one and `float` on the second
one.  It would then call `int_and_float()` with those
values, and the return value of `int_and_float()` would
be passed in to the `position` parameter on `fgrep()`.

So now if you ran:

    % python3 script.py fgrep -p 2 13 funkyfresh

Appeal would call:

```Python
fgrep('funkyfresh', position=[6, 65.0])
```

Finally, let's change the example to demonstrate something
else: although converters can be any callable, user-defined
classes work fine too.  And Appeal can correctly infer the
type based on the default value for any type.  So consider
this example:

```Python
import appeal
app = appeal.Appeal()

class IntAndFloat:
    def __init__(self, integer: int, real: float):
        self.integer = integer * 3
        self.real = real * 5

    def __repr__(self):
        return f"<IntAndFloat {self.integer} {self.real}>"

@app.command()
def fgrep(pattern, *filenames, position=IntAndFloat(0, 0.0)):
    print(f"fgrep {pattern} {filenames} {position}")

app.main()
```

This example behaves essentially the same as the previous example
in this section, except the formatting of `position` is slightly
different.  But the command-line usage is exactly the same!
Appeal inferred the converter for `position` based on the type
of its default value, then introspected that type to determine
how many opargs it should consume from the command-line and how
to convert them.

> **An important note about annotations**
>
> If you use static type analysis in your project,
> your static type analyzer may not enjoy analyzing Python
> code using Appeal.  Static type analyzers are designed
> to understand "type hints", a means of specifying static
> type information introduced in Python 3.5 with the
> `typing` module.  But Appeal doesn't use type hints,
> and there are some ways Appeal uses annotations that
> static type analyzers may not like.
>
> Fortunately, there's a way to get static type analyzers
> to work alongside Appeal: `typing.Annotated`, which lets
> you specify an ordered list of values.  Static type hints
> only ever use the *first* value; Appeal only ever uses the
> *last* value.  So you can have both types of annotations,
> side by side, and both static type checkers and Appeal are
> perfectly happy:
>
> ```Python
> def fgrep(pattern, *filenames,
>           position: Annotated[list, int_and_float] = (0, 0.0)):
> ```
>
> This is the only thing from the `typing` module Appeal
> understands--and Appeal itself never imports `typing`
> unless your annotations make it necessary.
> (`typing.Annotated` arrived in Python 3.9, like the
> `list[T]` spellings; on older Pythons, Appeal simply
> never sees it.)


## Converter Flexibility

You can use almost any function as an annotation...
within reason.  Appeal will introspect your annotation,
determine its input parameters, and call it to convert
the command-line argument into the argument it passes
in to your command function.

For example, what if you wanted an option that accepted
a string which gets broken up based on a delimiter substring?
This is a common idiom for `configure` scripts on UNIX-like
platforms; for example,
[Python's own `configure` script](https://github.com/python/cpython/blob/3.9/configure)
supports this option:

    --with-dbmliborder=db1:db2:...

Happily that's easy to do in Appeal.  Just write a converter
function that accepts a string, breaks it into substrings
however you like, and returns the list.

Although... you don't need to bother!  Appeal also provides
a converter that does it for you, called `appeal.split()`.
You pass in as many delimiter strings as you want, and
`appeal.split()` will split the command-line string across all of
them.  (If you don't specify any delimiters, `appeal.split()`
will split at every whitespace character.)

```Python
import appeal
app = appeal.Appeal()

@app.command()
def build(*, with_dbmliborder: appeal.split(':') = ()):
    print(f"build {with_dbmliborder}")

app.main()
```

    % python3 script.py build --with-dbmliborder=gdbm:ndbm
    build ['gdbm', 'ndbm']

One more converter deserves its own introduction:
`appeal.file()`.  It's `open()` as a converter--pass it `open()`'s
parameters, spelled out (`mode`, `buffering`, `encoding`,
`errors`, `newline`, `opener`)--plus the classic command-line
convention: an argument of `-` means the process-standard
stream, chosen by mode (`'r'` means `sys.stdin`, `'w'` means
`sys.stdout`, binary modes get the `.buffer` layer):

```Python
import appeal
import sys
app = appeal.Appeal()

@app.command()
def upcase(inp: appeal.file() = None, *, out: appeal.file('w') = sys.stdout):
    data = inp.read() if inp else ''
    out.write(data.upper())
    out.close()

app.main()
```

    % echo hello | python3 script.py upcase -
    HELLO

Note that `upcase()` closes `out` unconditionally.  That's the
contract: whatever `file()` hands you, you own--close it, use it
in a `with` block, whatever you like.  When the argument was `-`,
the handle is a safe wrapper whose `close()` *flushes* and then
goes inert, so the process's real stream is never harmed.
(argparse hands you bare `sys.stdout` there, and closing it is a
famous footgun.)  An unopenable path is a polite usage error,
naming the file and the reason.  One caveat, inherent to
opening files during conversion (argparse's `FileType` shares
it): a *structurally* malformed line opens nothing, but if a
`file('w')` argument converts and a *later* argument's
conversion then fails, the output file has already been
opened--and truncated--even though your function never ran.  And there's no convention for
`-` meaning stderr--none ever formed, because on POSIX systems
`/dev/stderr` is a path, and paths already work.


## Specifying An Option More Than Once

One thing you might have noticed by now: the interfaces
you've seen only allow Appeal to handle command-lines
where an option can be specified either zero times or
one time.  What if you want the user to be able to
specify an option three times?  Or ten?

The simplest spelling is new in 1.0: annotate the
keyword-only parameter with `list[T]`, and the option is
repeatable--each occurrence takes one oparg, converted with
`T`, and they're all collected into a list.  Its sibling
`dict[K, V]` gives you a repeatable `KEY=VALUE` option:

```Python
import appeal
app = appeal.Appeal()

@app.command()
def build(*, tag: list[str] = (), define: dict[str, int] = {}):
    print(f"build {tag} {define}")

app.main()
```

    % python3 script.py build -t alpha --tag beta -d X=1 --define Y=2
    build ['alpha', 'beta'] {'X': 1, 'Y': 2}

(These spellings require Python 3.9.  The parameterized
converters below do the same jobs and work on every Python
Appeal supports.)

Appeal also provides three repeatable converters with more
history behind them: `counter`, `accumulator`, and `mapping`.

First, let's look at `counter`.  `counter()`
simply counts the number of times an option is
specified on the command-line.  This is a somewhat
common idiom for "verbose" options; a program
that supports `-v` to mean *verbose* may allow
you to specify `-v` more than once to make
it *more* verbose.  Here's how you'd do that
with Appeal:

```Python
import appeal
app = appeal.Appeal()

@app.command()
def fgrep(*, verbose: appeal.counter() = 0):
    print(f"fgrep verbose={verbose!r}")

app.main()
```

If the user ran

    % python3 script.py fgrep

Appeal would call

```Python
fgrep()
```

allowing Python to pass in the default value of `0` to `verbose`.
And if the user ran

    % python3 script.py fgrep -v --verbose -v

Appeal would call

```Python
fgrep(verbose=3)
```

`counter()` takes two optional parameters,
`counter(delta=1, clamp=None)`.  Each occurrence adds
`delta` to a running value that *starts at the
parameter's own default*--and `delta` needn't be a
number, anything the value supports with `+` works.
`clamp` (default `None`: no clamping) is a barrier the
value stops on, approached from either side:
`counter(1, 2)` climbs 1, 2 and then stays at 2, and
`counter(-1, 0)` counts down and stops dead at 0.

`accumulator` handles options that take a single oparg.
It remembers them all and returns them in a single list--
the same job as `list[str]`.  Using crazy science magic
from the future, `accumulator` is parameterized:
`accumulator[int]` converts every oparg with `int`, and
`accumulator[int, float]` makes the option take *two*
opargs per occurrence, collecting `(int, float)` tuples.

`mapping()` is like `accumulator` except it returns a
`dict`.  An option annotated with `mapping()` consumes *two*
opargs from the command-line; the first one is the key, the
second one is the value.  (You can parameterize `mapping` the
same way, though you can only specify exactly two types.
Note the spelling difference from `dict[K, V]`: `mapping()`
takes two separate opargs, `dict[K, V]` takes one `KEY=VALUE`
oparg.)

Behind all of these is the `Option` class: converters that
collect an option's occurrences however you like.  `Option`
isn't useful by itself; it's an abstract base class.  Subclass
it and override up to three methods:

```
class Option:

    def init(self, default):
        ...

    def option(self, ...):
        ...

    def render(self):
        ...
```

Subclasses are *required* to override
`option()` and `render()`.  But `init()` is optional.
(`MultiOption` is an alias of `Option`, kept from 0.6;
use whichever name you like.)

If you then specify a subclass of `Option` as an
annotation on a keyword-only parameter of an
Appeal command function, several things happen:

* If that option is specified one or more times on
  the command-line, Appeal will instantiate exactly
  one of these objects and call its `init()` method,
  passing in the parameter's default value.
  (If the option is never specified, the class is never
  instantiated--the parameter's default passes through
  untouched.)
* Every time the user specifies that option on
  the command-line, Appeal will call the `option()`
  method on the object.
* After finishing processing the command-line,
  Appeal will call the `render()` method on the
  object, and pass the value it returns as the
  argument to that keyword-only parameter.

The most powerful part of this interface: you can
define `option()` to suit your needs--it supports
the same sort of polymorphism as annotations do.
Appeal will introspect your `option()` method to
determine how many opargs to consume from the
command-line, and how to convert them.

If you want your option to take two opargs,
with one being an `int` and the other being
a `float`, you would define `option()` in your
subclass as:

```Python
class MyOption(appeal.Option):

    def option(self, a: int, b: float):
        ...
```

Every time the user specified your option,
it would take two opargs, and they would be
converted into an `int` and a `float` before
calling your `option()` method.  It's up to
you to decide how to store them, and how to
render them into a single value returned
by your `render()` method.

One sibling: `StrictOption`.  It behaves identically to
`Option`, except the option may be given *at most once*--a
second occurrence is `specified more than once`, loudly.
Everywhere else repetition means last-one-wins; subclassing
`StrictOption` is how an option declares, by name, that
repeating it is a mistake.  (In 0.6 this behavior was
spelled `Option`; strictness is opt-in now, so it gets the
louder name.)


## Data Validation

What if you want to restrict the data the user provides
on the command-line?  That's simple, just use a converter!
Appeal provides a couple sample converters for data validation,
but it's easy to write your own.

The classic example is a parameter where you can only use one
of a list of values.  For that, you can use Appeal's `validate()`
converter.  For example, this command restricts the `direction`
parameter to one of six canonical directions:

```Python
import appeal
app = appeal.Appeal()

@app.command()
def go(direction: appeal.validate('up', 'down', 'left', 'right', 'forward', 'back')):
    print(f"go direction={direction!r}")

app.main()
```

Feed it something else and Appeal answers with a proper
usage error:

    error: invalid value for 'direction': 'sideways' (must be one of 'up', 'down', 'left', 'right', 'forward', 'back')

You can pass in an explicit type using a `type=`
named argument to `validate()`; if you omit it,
it uses the type of the first argument.

Appeal also has a built-in range validator
called `validate_range()`.  It takes `start`
and `stop` arguments the same way Python's
`range()` function does. Note that `validate_range()`
differs from Python's `range()` in one subtle way:
values *equal* to `stop` are allowed.

If you prefer, you can "clamp"
the value the user passed in to the range,
by supplying the argument `clamp=True` to
`validate_range()`. In that case, if the value
the user specifies is outside the range, `validate_range()`
will return the closest value of either `start` or `stop`.

(That's why `validate_range()` allows the
value to be *equal to* `stop`.  `clamp` would
be annoying to use if `stop` itself was an
illegal value--particularly if the types
were floats.)

Appeal validation functions are straightforward to write.
So, if these are insufficient to your needs,
you can easily write your own.  Take a look
at the implementations of `validate()` and
`validate_range()` inside Appeal to see one way to do it!


## Multiple Options For The Same Parameter

Some programs have a set of options that all
answer the same question.  Consider this simple-minded
command-line:

    go [--north|--south|--east|--west]

That is, the user picks a direction--and if they name
several, the last one wins, like every repeated option.
How would you do that in Appeal?

Easy.  You simply define multiple options that
write to the same parameter.  All the behavior
you've seen so far is using the *default* way of
mapping keyword-only parameters to options.  But
actually Appeal allows you to make your own mappings.
You can map a parameter as many ways as you want,
even using different converters!

To manually define your own options, use the `app.option()`
method on your Appeal instance.  It's a decorator you
apply to your command function.  The first argument is
the name of the parameter you want the option to write
to.  After that is one or more option strings you want to
map to this parameter.  `app.option()` also takes
`default` and `annotation` keyword-only parameters,
allowing you to specify respectively the default value or
annotation for this option.

Here's a simple example of how to implement the above `go`
command with Appeal:

```Python
import appeal
app = appeal.Appeal()

@app.command()
@app.option("direction", "--north", annotation=lambda: "north")
@app.option("direction", "--south", annotation=lambda: "south")
@app.option("direction", "--east",  annotation=lambda: "east")
@app.option("direction", "--west",  annotation=lambda: "west")
def go(*, direction='north'):
    print(f"go direction={direction!r}")

app.main()
```

All these annotations return a string.  But actually you can
return any type you want--and you can even map multiple
annotations that return different types to the same parameter.
`go --north --south` means south--whichever string spoke last
on the command line wins, exactly like repeating one option.
(Need strict mutual exclusion?  Check in your function; the
command line is a conversation, and the last word stands.)
You can even annotate with an `Option` class to *collect*
the occurrences instead!

Note that, whenever you use the `option()` decorator
to map your own options onto a parameter, Appeal won't add
its default options for that parameter.  It'll only have
the options you explicitly set.  Which means, for example,
that in the sample code above, there aren't any short options
for the options we created.  `-n` won't work, only `--north`.
It's a *fresh declaration:* the parameter's own annotation and
default don't leak into the option's grammar either--if you
want the option to convert, say so in the `@app.option()`
call.  (This is also how you *suppress* an unwanted automatic
short option: map the long option yourself, and mention no
short one.)

One final thing.  Your command function can accept `**kwargs`
too.  The only things that will go into it are options you
create with `app.option()`, which map to parameters that
don't otherwise exist.  If such an option isn't used on the
command-line, its name simply isn't in `kwargs`--absent means
absent.


## Recursive Converters

You already know that you can pass in a converter that takes
multiple parameters, and Appeal will consume multiple arguments
from the command-line to fill it.  And if the parameters of that
converter have annotations, Appeal will call those functions to
convert the command-line argument into the type your converter
wants.

But what if you did... *this?*

```Python
import appeal
app = appeal.Appeal()

def int_float(i: int, f: float):
    return (i, f)

def my_converter(i_f: int_float, s: str):
    return [i_f, s]

@app.command()
def recurse(a: str, b: my_converter = [(0, 0), '']):
    print(f"recurse a={a!r} b={b!r}")

app.main()
```

The `my_converter()` parameter `i_f` is a positional
parameter with an annotation that, itself,
*takes two positional parameters.*

Would it surprise you to know--yes, it actually works!

Converters have been fully recursive this *whole time.*
Actually this fact has been hiding in plain sight
all along--all the examples using `int_and_float()` are recursive
too, because `int_and_float()` has parameters annotated with
`int` and `float`.  Of course, those functions only take
a single string argument; `my_converter()` takes two
annotated positional parameters.
But the principles remain the same.

Still, this is a more complex situation than we've seen before.
`recurse` takes a positional parameter `b` that has a default
value, but its converter takes multiple positional parameters,
and one of those *also* has a converter that takes multiple
positional parameters.  How does Appeal map this to the
command-line?

Appeal "flattens" the tree of converter functions into a linear
series of arguments and options.  In this case the usage string
would look like this:

    recurse a [i f s]

This tells you the `recurse` command takes either one or four command-line
arguments.  That optional group of three command-line arguments
has a special name in Appeal: it's an "argument group".
Technically, Appeal views this command-line as taking two
"argument groups": the first group is required, and consumes
one command-line argument; the second group is optional, and
consumes three command-line arguments.

Now let's add an option and see what changes:

```Python
import appeal
app = appeal.Appeal()

def int_float(i: int, f: float):
    return (i, f)

def my_converter(i_f: int_float, s: str, *, verbose=False):
    return [i_f, s, verbose]

@app.command()
def recurse2(a: str, b: my_converter = [(0, 0), '', False]):
    print(f"recurse2 a={a!r} b={b!r}")

app.main()
```

Now the usage looks like this:

    recurse2 a [[-v|--verbose] i f s]

The brackets *announce* the group: everything you can type in
that group--its options first, then its arguments--reads left
to right, exactly as you'd type it.

Why is `--verbose` only available inside the group?  From a
high conceptual level, Appeal doesn't call `my_converter()`
at all unless you enter that optional group.  Consider, if
the user runs this command:

    recurse2 xyz

Appeal calls your function like so:

```Python
recurse2('xyz')
```

Since Appeal never called `my_converter()`, there's nothing
for `--verbose` to configure.  It only means something once
you're going to call `my_converter()`--which is to say, once
you supply the group's arguments:

    recurse2 pdq 1 2 xyz -v

Appeal calls `recurse2()` like this:

```
recurse2('pdq', my_converter(int('1'), float('2'), 'xyz', verbose=True))
```

You can put the `-v` anywhere from the start of the group
onward--`recurse2 pdq -v 1 2 xyz` works too.  Announcing the
option first is fine; it tells Appeal you intend to enter the
group.  In fact, if every argument in a group has a default,
*just the option alone* conjures the group into existence,
with all its arguments defaulted.

One more trick that falls out of "position decides".  Two
sibling parameters can use the *same* converter--so the same
option string can appear in two argument groups, and its
position on the command-line decides which group it
configures.  With `my_converter` above:

```Python
import appeal
app = appeal.Appeal()

def int_float(i: int, f: float):
    return (i, f)

def my_converter(i_f: int_float, s: str, *, verbose=False):
    return [i_f, s, verbose]

@app.command()
def twice(a: my_converter = None, b: my_converter = None):
    print(f"twice a={a!r} b={b!r}")

app.main()
```

    % python3 script.py twice -v 1 2 x 3 4 y
    twice a=[(1, 2.0), 'x', True] b=[(3, 4.0), 'y', False]

The first `-v` announces the first group; a `-v` typed among
the second group's arguments would configure the second.
Each group accepts its options at most once, as always.

Take a look back at all the examples in this document, and consider
that anywhere you specify a function or type, you can pass in nearly
any callable you like.

For example, the parameterized version of `mapping` isn't limited just to simple types.
If you used `mapping[str, int_float]` as the annotation
for a keyword-only parameter, that option would consume
three opargs on the command line: a `str`, an `int`, and
a `float`, and the dictionary would map strings to 2-tuples
of ints and floats.

Now you're starting to see how powerful Appeal's converters
really are!


## Now Witness The Power Of This Fully Armed And Operational Battle Station

But recursive converters are just the beginning!

> Buckle your seatbelt, Dorothy--because Kansas is going bye-bye.
>
> --Cypher, "The Matrix" (1999)

### Options that map other options

What if you did... *this?*

```Python
import appeal
app = appeal.Appeal()

def my_converter(a: int, *, verbose=False):
    return [a, verbose]

@app.command()
def inception(*, option: my_converter = [0, False]):
    print(f"inception option={option!r}")

app.main()
```

Woah, that works too!  We've created an option that
*itself* takes an option.  If you run `inception --option 5`,
you can now also specify `-v` or `--verbose`--but only
once you've specified `--option`.

In case you're wondering: `app.option()` must
decorate the function that takes the parameter you're
mapping an option *to.*  So if you want to define
explicit options for the `verbose` parameter to
`my_converter` in the above example, you'd
decorate `my_converter` with `app.option()` calls,
not `inception`.  (This also means, everywhere you
use `my_converter` as a converter, it will behave
the same, including taking the same options.)

### Multiple options that aren't MultiOptions

We're just getting started!  How about this:

```Python
import appeal
app = appeal.Appeal()

def my_converter(a: int, *, verbose=False):
    return [a, verbose]

@app.command()
def repetition(*args: my_converter):
    print(f"repetition args={args!r}")

app.main()
```

That works too, and I bet you're already guessing what it
does.  This version accepts as many `int` arguments
as the user wants to specify on the command-line, and *each one*
can optionally take its own `-v` or `--verbose` flag:

    % python3 script.py repetition -v 1 2 3 -v
    repetition args=([1, True], [2, False], [3, True])

(Announce-first, as always: the leading `-v` announces the
first instance, a `-v` typed between `1` and `2` would
announce the second, and the trailing one belongs to the
last.)

### Positional parameters that only consume options

I'll give you one more crazy example:

```Python
import appeal
app = appeal.Appeal()

class Logging:
    def __init__(self, *, verbose=False, log_level='info'):
        self.verbose = verbose
        self.log_level = log_level

    def __repr__(self):
        return f"<Logging verbose={self.verbose!r} log_level={self.log_level}>"

@app.command()
def mixin(log: Logging):
    print(f"mixin log={log!r}")

app.main()
```

Can you guess what usage for `mixin` looks like?  (Probably!)
It looks like this:

    mixin [-v|--verbose] [-l|--log-level log_level]

Even though `log` is a positional parameter, it doesn't consume
any positional arguments on the command-line.  The `Logging`
converter only adds options!  This is what object-oriented
programmers might call a "mix-in".  With the `Logging` converter,
you can add logging options to every one of your commands, without
having to re-implement it each time.  (Though in most cases it's
probably better to add such options to a global command function.)

Internally this works exactly like you'd expect.  Since the
`log` parameter consumes no command-line arguments, Appeal will
always call its converter.  Specifying any of the options will
set arguments for that call.  And the resulting `Logging` object
will be passed in as the argument to `log`.

What's really going on here is that, from Appeal's perspective,
*there's no difference between a "command function" and a
"converter".*  A command function is just a converter that
happens to be mapped to a command.  So anything you can do
with a command function, you can do with a converter too.
A converter can define options, it can be decorated with
`app.option()` or `app.parameter()`, it can accept any kind
of parameter defined by Python, and any parameter can use
(almost) any converter.  And those converters can recursively
use other converters.

By *now* you can see the expressive power Appeal gives you.
Of course, you'll rarely use more than a fraction of that power.
But it's reassuring to know that, whatever command-line API
metaphor you want to express, it's not just *possible* in
Appeal--it's *easy.*


## The Famous `make -j`

The `--jobs`/`-j` option of unix `make` has an interesting and
subtle behavior.  Run `make` and it runs one job at a time.
Run `make -j 5` and it runs five.  But run `make -j`, with no
number, and it runs *as many jobs as it likes.*  In a way, the
`-j` option has *two default values.*

Can you do this with Appeal?  Naturally!  It's a converter
whose one parameter has a default--two defaults, two homes:

```Python
import appeal
import math

app = appeal.Appeal()

def jobs(jobs: int = math.inf):
    return jobs

@app.command()
def make(*targets, jobs: jobs = 1):
    print(f"building {targets} with {jobs} jobs")

app.main()
```

The parameter's own default (`1`) fills when `-j` never
appears; the converter's default (`math.inf`) fills when `-j`
appears bare.  The usage line even tells the user the oparg is
optional:

    make [-j|--jobs [jobs]] [targets]...

An optional oparg is *greedy*: when `-j` has a next string, it
takes it--whatever it looks like.  So `make -j 5` is five
jobs, and so is `make -j5` (or, on the long spelling,
`make --jobs=5`).  The flip side of
greed is that `make all -j install` hands `install` to `-j`
and fails loudly--`invalid value for 'jobs'`--rather than
quietly guessing you meant it as a target.  If `-j` should not
eat the word after it, give `-j` its value explicitly, put it
last, or say `make all -j -- install`: the `--` terminator
outranks greed.


## A Class As Your Whole Program

Maybe you've noticed--all the examples so far have used
standard Python functions as Appeal commands.  What about
methods?  Can you use those for commands?  In 1.0, the
answer isn't just "yes"--it's the nicest way to structure a
whole program.

Decorate a *class* with `@app.global_command()`, and decorate
its methods with `@app.command()`:

```Python
import appeal
app = appeal.Appeal()

@app.global_command()
class MyApp:
    def __init__(self, *, verbose=False):
        self.verbose = verbose

    @app.command()
    def add(self, a: int, b: int):
        print(f"sum {a + b}{' (verbosely)' if self.verbose else ''}")

    @app.command()
    def status(self):
        print(f"status verbose={self.verbose}")

app.main()
```

Here's what that means:

* The class's `__init__` is the *global command*: its
  parameters are the program's global options and arguments.
  Running `script.py -v add 1 2` constructs `MyApp(verbose=True)`.
* Each decorated method is a command, and when it runs, `self`
  is the instance that was just constructed.  Undecorated
  methods (`helper`, `__repr__`, whatever) are just methods;
  nothing is automatic, you decorate what you want exposed.
* A *nested class* decorated with `@app.command()` is a
  subcommand set: the inner class constructs from the parent
  instance, and *its* decorated methods are its subcommands.

Where do the constructed instances go?  Appeal logs them.
After a run, `app.instances` is a list of `(command, instance)`
tuples, appended mechanically as things are called: the global
class logs `(None, instance)`; an ordinary command function or
method logs `(function, None)`; a constructing command (a class)
logs `(class, instance)`.  So `app.instances[0][1]` is your
application object, if you want it back after `main()` returns.

And because the class-as-app is just commands all the way down,
everything else in this document composes with it: cycling
(`Appeal(repeat=True)` lets one line call several methods on
the *same* instance), config layering (the config dict feeds
`__init__`--see below), standalone emission, the REPL, MCP.

(Appeal 0.6 handled methods with "preparer" objects--
`app.app_class()`, `app.command_method()`, and friends.  The
class-as-app replaces all of that.  If you just want to bind
one existing instance's method as a command, the old direct
spelling still works fine: `app.command()(o.method)` passes
the *bound* method in, and Appeal never sees `self`.)


## Writing Help

Appeal automatically generates *usage* for your command
functions.  But it's up to you to write the documentation
explaining what those commands and arguments and options
actually *do.*  You write it where a Python programmer would
want to write it anyway: in docstrings.

Appeal parses your docstrings; they become input to Appeal's
documentation processor.  Appeal splits up the information,
digests it, reformats it, and presents it to the user in
various output formats.  This means Appeal imposes a small
amount of structure onto your docstrings.  It's gentle, and
highly human-readable--you're gonna like it, honest!

A docstring for an Appeal command function should be in this shape:
```
Summary line.

Full description of the documentation.  Multiple lines
and paragraphs are all fine.

This is a second paragraph.

Arguments:
    parametername1: Description of what this argument does.

    parametername2: Documentation for the second argument.

Options:

    keywordargument_a: Description of the option(s) that feed this
      parameter.

Text at the left column goes back into the "full documentation"
section.
```

First, Appeal outdents the entire docstring by its leftmost
nonwhite column.  I talk about the "left column" below; this
is the relative leftmost column, column 1 after outdenting.

The "summary line" is the first line of the docstring.  It should
be a standalone line (or paragraph) that explains what the
command does.  It ends at the first empty line.  You should
keep these short; it should just be a summary, not full
documentation.

The prose, of course, should explain what the command does.
It should be written as it should be presented to the user;
it should talk about options by name (`--version`), not
about keyword-only parameters (`version`).

`Arguments:` and `Options:` are special markers parsed by
Appeal.  They need to be at the left column, and they need
that exact spelling, with the leading capital letter and the
trailing colon without whitespace.  These start the "arguments"
and "options" sections.

The format of these sections is simple.  Every line must be
indented, and every entry starts with the name of a parameter,
followed by a colon, followed by the documentation for that
parameter.  The documentation can cross multiple lines, but
subsequent lines should be indented further.  A line at
the left column ends the section.

It's worth repeating, and pointing out: you define the
documentation by specifying the *parameter name*, not
the *option* or argument name from the command-line.

Here's an example program with full documentation:

```Python
import appeal

app = appeal.Appeal(name='serve')

@app.command()
def serve(host, port: int = 8080, *, verbose=False):
    """
    Serves the thing.

    Longer prose about serving, wrapped and formatted
    for you.

    Arguments:
      host: The host to serve on.
      port: The port.  Defaults to 8080.

    Options:
      verbose: Print more output.
    """
    print('serving', host, port, verbose)

app.main()
```

If you write this to `serve.py` and ask for help on the
serve command, you are rewarded with:
```
% python3 serve.py help serve
Serves the thing.

usage: serve [-v|--verbose] host [port]

Longer prose about serving, wrapped and formatted for you.

Arguments:
    host  The host to serve on.
    port  The port.  Defaults to 8080.

Options:
    -v|--verbose  Print more output.
```

Already this is pretty good.  You specify the parameter name,
and Appeal rewrites it using the actual options and final argument
names on the command-line.  And Appeal makes the real thing
pretty, with bold and color and such.

But it gets better!  In the same way that Appeal command-lines
are composable using recursion, your *documentation* is *also*
composable in the same way!  When you use a converter, you
automatically pull in the documentation for *its* arguments
and options--and all of its children, too.  You document
the arguments and options once, in the command function or
converter where it's defined, and everyone who uses it gets
the documentation for free.

 document a converter's parameters once, in
the converter's own docstring, and every command that uses the
converter inherits that documentation--with the command able
to override any entry it wants (nearest scope wins).  Your
`Logging` mixin documents itself once, everywhere.

Entries are validated against your program's actual grammar at
build time, loudly: document a parameter that doesn't exist,
or file an option under `Arguments:`, and Appeal names your
mistake instead of quietly shipping wrong help.

You can reshape the page (the templates live in a plain dict,
`app.templates`) and color it (pass a `theme=` to `Appeal()`;
Appeal decides whether color actually appears the same way
CPython does, and layout never moves--a colored page strips
back to the monochrome page byte-for-byte).

The full walkthrough--every rule, every template placeholder,
the theme vocabulary--lives in
[appeal.documentation.md](appeal.documentation.md).
Every example in it is executed by the test suite.

And the same corpus renders one more dialect:
`app.documentation('man')` returns your program as a troff
man(1) page--NAME, SYNOPSIS, OPTIONS, a COMMANDS section with a
subsection per top-level command--assembled from the docstrings
you already wrote.  (Returns the text; where it installs is
your packaging's business.  Nested subcommand sets appear in
their parent's listing but don't get subsections of their own
yet.)


## Tab Completion

Appeal answers shell tab-completion from the same grammar that
parses the command-line: command words, option strings, and
even value candidates (give a converter a `completions`
attribute and Appeal will offer its suggestions).  Completion
understands subcommands, cycling, and option opargs.

Wiring it up costs one line in your shell configuration.  The
zero-effort spelling:

    eval "$(env _APPEAL_COMPLETE=source_bash mytool)"

`app.completion(shell)` returns the same shell-function text
for `'bash'`, `'zsh'`, or `'fish'`, if you'd rather install it
properly.  And standalone scripts (next section) ship with
completion baked in--the emitted script answers its own
completion requests with no appeal installed.

The full walkthrough lives in
[appeal.completion.md](appeal.completion.md).


## The REPL

Any Appeal program is one method call away from being an
interactive one:

    app.repl()

reads lines, splits them like a shell, and feeds them through
the *same parser* as the command-line--same commands, same
options, same conversions, same errors (printed politely, with
usage).  Return values print--a REPL is a conversation, so a
calculator's `3` is an answer, not an exit code.  Tab
completion works at the prompt, driven by the same completion
engine as the shell's.  `quit`, `exit`, or end-of-file
(control-D) leaves.

`app.repl(prompt=..., banner=...)` customizes the greeting.


## Reading Config Files

Appeal allows for friction-free command-line APIs.  You write your
command function, point Appeal at it, and whoosh! now you've got a
command-line interface.  But there are other interfaces users may
want to use to configure your program.  Appeal works with
those too.

Appeal doesn't read config *files*--you already have a library
for that.  Read your TOML/JSON/YAML/[Perky](https://pypi.org/project/perky/)
file into a dict with whatever you like; Appeal's job starts
once you have the dict:

```
result = appeal.read_mapping(callable, mapping)
```

`read_mapping` points the command-line metaphor at a mapping:
it reads the names of the callable's parameters, pulls values
out of the mapping using those names, converts them per the
annotations--converters always apply, already-typed values
included--and calls the callable.  Parameters with defaults
are optional; parameters without are required; a key that
names nothing in the callable's tree raises, by name--pass
`strict=False` to ignore such keys instead (for reading a
slice of somebody else's document).  This works especially well with
classes decorated with `dataclasses.dataclass`: a few lines
define a typed configuration object, and `read_mapping` fills
it straight from your config file.

Everything composes, just like the command-line:

* A parameter whose converter takes several parameters reads a
  *nested* dict under the parameter's name--or, equivalently,
  flat keys at the same level.  (Both spellings always work;
  use the nested one when two branches of your tree each have
  a parameter named `x`.  Appeal 0.6's `@app.unnested()` is
  accepted as a no-op.)
* `list[T]`, `dict[K, V]`, tuples, `*args`--they all read the
  obvious shapes.
* An `Option` class reads naturally: the parameter reads a
  *sequence of occurrences*, calling `option()` per element,
  exactly as if each had appeared on a command-line.  (A
  `StrictOption` reads a single occurrence.)
* Booleans parse strictly: real booleans or
  `true/false/yes/no/on/off/1/0`--never truthiness.  `'false'`
  must not mean `True`.
* Missing required values and bad values raise
  `AppealDataError`, carrying the path to the offender
  (`at s.port`).

Two siblings round out the family.  `read_iterable(callable,
iterable)` is row-oriented: it calls the callable once per row
(a sequence of strings), converting positionally, and returns
the list of results.  `read_csv(callable, reader,
first_row_map=None)` does the same for a `csv.reader`--and if
you pass `first_row_map=True`, the CSV's heading row maps each
row's values to parameters *by name*, read_mapping-style.

### Config layering

New in 1.0, and better than calling `read_mapping`
yourself for the common case: **bind a config dict to a
precommand** and its option values layer from it.  You bind the
dict when you register the precommand and fill it later--Appeal
holds the same object, so an empty dict you `.update()` before
`main()` is seen:

```Python
import appeal
app = appeal.Appeal(name='edit')

config = {}                       # bind it now, fill it before main()

@app.global_command(config=config)
def global_command(*, editor='vi', verbose=False):
    print(f"editor={editor} verbose={verbose}")

@app.command()
def work(file):
    print(f"editing {file}")

config.update({'editor': 'emacs'})   # you read this from your rc file
app.main()
```

The layering rules are fixed and unknobbed:

* Config supplies **that precommand's options only**.  Config
  holds program-wide settings; the command line names the work.
  Bind a different dict to each precommand that wants one--no
  single global slot, no guessing which precommand a mapping is
  for.  If you have several layers (system, user, project),
  merge them into the one dict yourself first.
* Precedence is **defaults < config < args**, atomic per
  option: an option the command-line mentions wins *whole*
  (repeatable options replace, never append--the command line
  can always subtract).  That includes flags: config turned
  `verbose` on?  `--verbose=false` turns it back off.
* Keys are **strict**: every key must name a global-command
  option.  A command name, a positional argument, or an
  unknown key is a loud error saying exactly which it is.
  Either this dict is yours, or it isn't.  (One key can never
  work: a *scoped* option--the same string declared by several
  argument groups--is addressed by position, and a mapping has
  no position.  The refusal says so, and points at the
  workaround.)
* Values convert through the ordinary pipeline, with `config:`
  provenance on failures.  Flags use the strict boolean
  spellings; repeatable options take a sequence.

With a class-as-app, this is the whole argparse-replacement
story in three lines: the config file helps construct your
application object, and args picks the methods.


## The Processor: Many Runs, One App

An `Appeal` object is your program's *description*.  The
runtime state of one command-line run lives in a `Processor`--
so one Appeal object can process many command-lines, even
simultaneously (imagine an app server handling commands for
many users, each with their own config):

```Python
import appeal
app = appeal.Appeal()

@app.command()
def greet(name):
    return f'hi, {name}'

processor = app.process(['greet', 'world'])  # one run, inspected
assert processor.result == 'hi, world'
```

`app.process()` runs one command line--a whole-line structural
pre-scan validates every command's shape first, then the
commands convert and run left to right--and returns the
Processor.  (Simultaneously means simultaneously: even threads
racing the very first parse are safe.  Compilation runs
lock-free--Appeal never holds a lock while calling your
code--and a plain lock guards only the cache installs, so
racing threads each build, one wins, and the rest adopt the
winner.)
`processor.instances` is that run's `(command, instance)` log;
`app.instances` is a convenience alias for the latest run's.
`app.main(args)` is `process()` plus polite error printing
plus the exit-code protocol.  Config isn't passed here--bind it
per precommand via `@app.precommand(config=...)`.


## Standalone Scripts: Removed

Earlier 1.0 development pursued emitting your parser as a
standalone, dependency-free Python script--"the north star."
That feature was removed during development: the engine went
interpreter-only, and the speed the standalone script existed
to buy is now the ordinary path--`import appeal` is a few
milliseconds of stdlib-only core, and nothing heavy loads
until help or another lazy feature is used.

## MCP: Your Commands As AI Tools

MCP (the Model Context Protocol) is the JSON-RPC-over-stdio
protocol AI agents use to call tools.  Any Appeal program can
serve its commands as MCP tools:

    app.mcp()

Each command becomes one tool: its docstring summary is the
tool's description, its signature (rendered as JSON Schema) is
the input schema, and each call arrives as a mapping and runs
through the same read driver as `read_mapping`--conversions
always apply, defaults fill absences, converter errors come
back as polite tool errors instead of crashes.  Schema keys
are your *parameter names*; the agent never sees an option
string.

A few things to know:

* MCP tool names are flat strings, so there are no
  subcommands; if your program has them, flatten the names
  yourself--`@app.command(name='db add')` is fine, command
  names are never validated or mangled.
* A class-as-app constructs its instance **once, at server
  startup**--`app.mcp(config=...)` feeds `__init__` under the
  config-layering rules--and every method tool dispatches
  bound to that one instance, so state persists across calls.
* And of course, there's a standalone version:
  `app.standalone_mcp()` emits the whole MCP server as one
  dependency-free script, per the north star.

The machine-readable twin of `--help` is also available
directly: `app.schema('appeal', '1.0')` describes your whole
program as plain JSON-safe data--usage, arguments, options,
types, docs--and `app.schema('mcp', '2024-11-05')` renders
standard JSON Schema per command, versioned by the MCP
protocol's own revisions.  A schema tells a machine what a command accepts;
`read_mapping` runs the command from the JSON object the
machine sends back.


## Errors

Appeal's exceptions form a tiny, principled hierarchy:

* `AppealDataError` -- the base: *the data was wrong.*  Maybe
  it came from a command-line, maybe from a config file, maybe
  from a JSON blob; Appeal often can't know, and the type name
  doesn't pretend to.
* `AppealUsageError(AppealDataError)` -- the data was wrong
  *and* it came from a command-line.  Carries the usage text
  for the command that failed.
* `AppealConfigurationError` -- *you* (the program author)
  used Appeal incorrectly: an unbuildable signature, colliding
  option strings, an unemittable standalone.  Raised at build
  time wherever possible, so structure bugs fail before any
  user input arrives.

`app.main()` catches the data errors and turns them into the
polite protocol: an `error:` line (plus usage, when there is
usage) printed to **standard error**, and exit status 2.
Unknown commands and unknown long options come with a
suggestion when something in your grammar is close--`unknown
command 'stauts' (did you mean 'status'?)`--the way git does
it.  And control-C exits quietly with status 130 (128+SIGINT,
the POSIX convention)--in `main()` only; `process()` propagates
the raw `KeyboardInterrupt`, because automation gets real
exceptions.  That's the whole of Appeal's signal handling, on
purpose: Appeal is an argument processor, not an environment.
Standard error is the POSIX diagnostic convention: it keeps a
filter's stdout clean, so `mytool --oops | jq .` shows the user
Appeal's message instead of feeding it to `jq`.  If you'd
rather have everything on one stream--or somewhere else
entirely--`errors=` takes any writable file object:
`Appeal(errors=sys.stdout)` is 0.6's behavior, and a log
file works too.  (Requested help is always on stdout,
regardless.)  Configuration errors are bugs, so they raise.
`app.process()` catches nothing--automation and tests get real
exceptions.

`AppealError` is the umbrella--every exception Appeal raises
derives from it, so `except AppealError` means "anything Appeal
raised" (0.6 spelled the umbrella `AppealBaseException`;
that name is kept as an alias).  And it has one job of its own:
raise it *from your command* for a runtime failure that should
end the program politely--`raise AppealError("couldn't reach
the server")` prints `error: couldn't reach the server` and
exits 1, no usage (the command line was fine).  It works from a
standalone script too, even though your module and the script
each hold their own copy of the class.

(The short spellings `UsageError`, `DataError`, and
`ConfigurationError` are importable aliases, and both
spellings are the same classes--catch whichever you like.)


## API Reference

`Appeal(name=None, *, theme=None, version=None, repeat=False, errors=None, script=sys.argv[0], margin=79, indent=4, positional_argument_usage_format='{name}', default_options=default_options, help=True)`

Creates a new Appeal instance.

* `name` is your program's name, as shown in usage.  If you
  don't supply it, Appeal uses `script`'s basename.
* `script` is the program's path, used to derive the displayed
  name when `name` isn't given (its basename, or `'program'`
  when that's empty).  It defaults to `sys.argv[0]`, read once
  when Appeal is imported--the only place Appeal consults
  `sys.argv[0]`, so the program name is a controllable input.
  Tests (and embedders) pass `script=` explicitly.
* `theme` colors Appeal's output: `None` means the stock theme
  (when the environment and terminal permit), `False` means
  never, or pass an `appeal.Theme` of your own.
* `version` is your program's version string.  It wires up
  `--version` (as the first token--prints the bare string,
  exits 0) and, when the program has commands, an automatic
  `version` command (suppressed if you define your own).
  No short option: `-v` stays available for `verbose`.
  Also stamped into MCP servers.
* `repeat=True` enables cycling: one command-line may invoke
  several commands, left to right.
* `errors` is the file object error messages print to,
  default `sys.stderr` (resolved at error time, like
  `print(file=None)`).  `sys.stdout` is 0.6's behavior.
  Standalone scripts can bake `sys.stderr` or `sys.stdout`;
  any other stream refuses at emission, by name.
* `margin` (default 79) caps the help page's wrap width; at
  render time the page uses the terminal's width or this cap,
  whichever is narrower (pipes and redirects get the cap, so
  captured output is stable).  Baked into standalone scripts,
  where the *script's* terminal decides.
* `indent` (default 4) sets the left indent of the help tables.
  It works by re-indenting the section templates, which own
  layout--overwrite `app.templates` to go further.
* `positional_argument_usage_format` (default `'{name}'`) is a
  format string that decorates how operands appear in usage
  lines and help tables--positional arguments and option opargs
  alike.  It interpolates the parameter's `{name}` (and, if you
  like, `{name.upper()}`), and nothing else: `'<{name}>'` wraps
  every operand in angle brackets (`--number <number>`),
  `'{name.upper()}'` shouts them (`--number NUMBER`).  An
  explicit `@app.parameter(usage=...)` rename is literal and
  overrides the format outright.  Baked into standalone scripts.
* `default_options` is the policy that turns an automatically-
  mapped keyword-only parameter into option strings: a callable
  `(name, annotation, default)` returning a list of option
  strings.  The stock `default_options` adds a long and a short
  (Appeal claims the long outright and the short if its letter is
  free); `default_long_option` drops the short (the "no auto
  shorts" policy), `default_short_option` drops the long, or pass
  your own.  It runs at build time; only its output--the
  strings--rides into a standalone script, never the callable
  itself.  All three ship on the `appeal` namespace.
* `help` (default `True`) is Appeal's automatic help.  `True`
  gives every command `-h`/`--help` and, for a program with
  commands, a `help` command.  `help=False` suppresses all of
  it--the program answers `-h`/`--help` only if it declares them
  itself.  (Even with `help=True`, a command that claims its own
  `--help` still wins; `help=False` is the blanket off switch.)

Help is on by default: every command answers `-h`/`--help`, and a
program with commands gets a `help` command, unless you define
your own or pass `help=False`.  Version works the same way, when
you supply one: `Appeal(version='1.2.3')` gives you `--version`
and a `version` command for free, and both are baked into
standalone scripts.

`Appeal.command(name=None, *, repeat=False)`

Used as a decorator; registers the decorated callable as a
command.  The command word is the callable's `__name__`,
verbatim--or `name`, verbatim, if you pass it
(`@app.command('sync-all')`: dashes welcome, the function's own
name is ignored).  Decorating a *class* registers a constructing
command whose decorated methods are its subcommands.
Registering a word twice replaces: the second wins.

`app.command('db')` returns the child **Appeal instance** for
the word `db`, creating it if needed--the command tree is a tree
of Appeal instances, linked by `.parent`.  Everything chains:
`.command()` attaches subcommands, `.default_command()` picks
what runs when the line stops at `db`, `.option()` remaps a
subcommand's options, and so on--the child is an Appeal, not a
wrapper.  `repeat=True` makes that node's subcommand set cycle.
`Appeal(name, parent=app)` hangs a node in the tree directly.

`Appeal.global_command()`

Used as a decorator.  Sets the *global command*: the callable
that owns everything before the first command word (or the
whole line, if the program has no commands).  Decorating a
class makes it your program: `__init__` is the global command
and its decorated methods are the commands.

`Appeal.default_command()`

Used as a decorator.  Sets the command run when the program
has commands but the user names none.  Takes no parameters, by
definition.  On a subcommand node
(`@app.command('db').default_command()`) it sets what runs when
the line stops at the parent.

`Appeal.option(parameter_name, *options, annotation=..., default=...)`

Used as a decorator, on the callable that owns
`parameter_name` (a command function or any converter).  Maps
the given option strings to that parameter--*instead of* the
automatic ones, and as a fresh declaration: the option's
grammar comes from `annotation`/`default` given here, not from
the parameter.  Option strings are validated (`-X`, or
`--long-name` of at least four characters).  May be stacked;
may target `**kwargs`.

`Appeal.parameter(parameter_name, *, usage=None)`

Used as a decorator.  Renames how one positional parameter (or
option metavar) displays in usage: `@app.parameter('path',
usage='FILE')`.

`Appeal.main(args=None)`

Processes a command-line and calls your command functions.
Catches data errors and prints them politely (to stderr,
with usage), then *exits the process* with the exit status--
a script's last line can be a bare `app.main()`.  `args`
defaults to `sys.argv[1:]`.  Want the status returned
instead of exiting?  That's `process()`.

`Appeal.process(args=None)`

Like `main()`, but catches nothing and returns the last
command's return value.  The automation entry point.  `args`
defaults to `sys.argv[1:]`.

`Appeal.parse(args=None, config=None)` / `Appeal.processor()`

Stage 1 only: returns a `Processor` holding the fully-parsed
run.  `processor.execute()` runs it; `processor.instances` is
its `(command, instance)` log.  `app.processor()` returns an
unparsed Processor you can drive yourself.

`Appeal.instances`

The latest run's `(command, instance)` log.

`Appeal.help()` / `Appeal.schema(format, version)` / `Appeal.documentation(format)`

The help page (printed); the JSON-safe program description
(returned); and the docs rendered in a named format--only
`'man'` for now, a troff man page (returned).

`Appeal.complete(words, prefix='')` / `Appeal.completion(shell)`

The completion candidates for a partial command-line, and the
shell wiring text for `'bash'`/`'zsh'`/`'fish'`.

`Appeal.repl(*, prompt=None, banner=None)`

The interactive loop.

`Appeal.mcp(*, config=None, version=None)`

Serve the program's commands as MCP tools over stdio, until
stdin closes.

`appeal.read_mapping(callable, mapping)` / `appeal.read_iterable(callable, iterable)` / `appeal.read_csv(callable, reader, *, first_row_map=None)`

The config-reading family, described above.  Also available as
methods on the app.

The converter vocabulary: `appeal.split(*separators)`,
`appeal.file(mode='r', *, buffering=-1, encoding=None, errors=None, newline=None, opener=None)`,
`appeal.validate(*values, type=None)`,
`appeal.validate_range(start, stop=None, *, type=None, clamp=False)`,
`appeal.counter(delta=1, clamp=None)`, `appeal.accumulator`,
`appeal.mapping`, and the classes `appeal.Option` (repeatable;
`appeal.MultiOption` is its alias) and `appeal.StrictOption`
(at most once).

The exceptions: `AppealDataError`, `AppealUsageError`,
`AppealConfigurationError` (and their short aliases).

The theme: `appeal.Theme`.


## Reference: How Parameters Map

The library inspects the parameters of your function and uses
those for the arguments, options, and opargs of your command:

* Positional-only and positional-or-keyword parameters
  (parameters before `*,` or `*args,`) map to positional
  arguments.  Required if they have no default, optional if
  they do.
* `*args` maps to "as many as you like", each converted by its
  annotation (which may be a whole converter tree).
* Keyword-only parameters *with* defaults map to options.
* Keyword-only parameters *without* defaults map to required
  trailing arguments, filled from the end of the line.
* An annotation is a *converter*: any callable Appeal can
  introspect.  Its positional parameters consume arguments (or
  opargs); its keyword-only parameters become options; it
  recurses.
* No annotation, but a default?  The converter is
  `type(default)`--except `None` means `str`, and a boolean
  default on a keyword-only parameter means a flag that
  negates the default.
* No annotation and no default: `str`.
* `list[T]` on an option: repeatable, collecting a list.
  `dict[K, V]` on an option: repeatable `KEY=VALUE`.
  `tuple[T1, T2, ...]` anywhere: that many arguments, one
  tuple.  (These are option-repetition and shape spellings,
  not general-purpose nesting--`list[dict[...]]` and friends
  are refused by name.)
* Exactly four built-in types are special-cased as
  uninspectable leaves: `str`, `int`, `float`, and `bool`.

Putting it all together: if you wanted to write an `fgrep`
command with a usage string like this:

    fgrep [-v|--verbose] [-l|--level level] pattern [file]...

you'd write it as follows:

```Python
@app.command()
def fgrep(pattern, *file, verbose=False, level=0):
    ...
```


## Appeal And POSIX Utility Semantics

The POSIX standard defines command-line behavior for all POSIX
utility commands, in 1003.1, Chapter 12, currently at revision POSIX.1-2017:

  https://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap12.html

Appeal isn't a perfect match for POSIX semantics; it disallows some
things POSIX allows, and allows some things POSIX disallows.

* As per required POSIX semantics (1003.1-2017, Chapter 12),
  options can never be required.  It therefore follows that
  in Appeal, a keyword-only parameter without a default isn't
  an option at all--it's a required trailing argument.
* The POSIX standard makes no mention of "long options",
  so it's not clear whether or not the standard permits them.
  (Presumably they will be permitted in a future standard.)
* POSIX requires that options that accept multiple opargs
  should accept them as a single string with either spaces
  or commas separating the opargs.  Appeal supports this behavior
  with `appeal.split`.  But it also permits options that consume
  multiple separate opargs from the command-line.
* POSIX requires that all options be specified before any positional
  arguments.  Appeal doesn't enforce this, and will happily consume
  options and positional arguments in any order (subject to the
  argument-group scoping described above).  In fact,
  "subcommands" require permitting options after positional arguments
  for anything beyond the simplest possible subcommand support.
* POSIX requires that, if a short option has a single *optional*
  oparg, the oparg must be concatenated directly onto the option:
  if `-j` takes an optional oparg, `-j5` is the only permissible
  spelling.  Appeal supports that spelling--plus `-j 5`, and
  `--jobs=5` on the long spelling.  Note that the space-separated
  form is *greedy*: `-j`
  followed by another string consumes it as the oparg, which is
  what POSIX definitely does *not* want.  I feel Appeal's
  consistency is more important than supporting this syntactic
  hack--argparse and click are greedy the same way--and if `-j`
  shouldn't eat the next word, say `-j5` or put it last.  One
  token outranks greed: `--`.  `make -j -- clean` is unlimited
  jobs and a `clean` operand, exactly as in argparse and click.
  (A *required* oparg still takes `--` verbatim--the
  `grep -e --` idiom.  And required opargs concatenate too, per
  getopt: `-fguava` is `-f guava`--as it is everywhere else.)


## Additional Subtle Features And Behaviors

* You can specify options and arguments in any order on a
  command-line, Appeal doesn't care.  If you want Appeal to
  stop recognizing strings starting with dashes as options,
  specify `--` (two dashes with nothing else).  All subsequent
  strings on the command-line will be used as arguments, even
  if they start with a `-`.  (The effect is local to the parse
  in progress--in a cycling program, the next command's parse
  starts fresh.)
* A lone `-` is always an operand, never an option--it reaches
  your converter verbatim.  `appeal.file()` gives it the classic
  stdin/stdout meaning; without it, the string `'-'` is yours.
* Long-option abbreviation (GNU getopt_long's unambiguous
  prefixes, argparse's `allow_abbrev`) is deliberately NOT
  supported: `--verb` never means `--verbose`.  Abbreviations
  in scripts break when a program grows a new option; tab
  completion serves the interactive-comfort case instead.
  (click refuses for the same reason.)
* Many built-in types are not introspectable.  If you call
  `inspect.signature(int)` it throws a `ValueError`.  Appeal
  special-cases exactly four built-in types as leaves: `str`,
  `int`, `float`, and `bool`.
* Information about a particular converter is localized to
  a particular `Appeal()` instance.  If you decorate a converter
  with `@app.option()`, every place inside that `Appeal()` instance
  that you use that converter will also pick up the changes you
  made with `@app.option()`.
* Appeal is *lazy* and *late-binding*: decorators only record.
  Plans are built, docstrings parsed, and parsers compiled at
  first use--and per command, so a program with fifty commands
  compiles only the one the user invoked.  (Standalone
  emission is deliberately eager: the whole-program artifact
  must build--and refuse--everything.)
* Almost any callable can be a converter--but not *every*
  callable.  A converter used for a `*args` parameter must,
  somewhere in its annotation tree, require at least one
  positional argument (otherwise "one more instance" would
  consume nothing, forever).


## What Changed From Appeal 0.6

Appeal 1.0 is a ground-up rewrite: the 0.6
bytecode interpreter is gone, replaced by a compiler that
analyzes your functions once and generates a specialized
parser (the same generated code serves in-process and
standalone).  Appeal 0.6's test corpus runs against 1.0
as a permanent regression suite.  The *deliberate* semantic
changes, all of them:

* **Argument distribution got smarter.**  Appeal 0.6 distributed
  operands to argument groups greedily and sometimes painted
  itself into a corner, rejecting command-lines that had a
  valid reading.  Appeal 1.0 accepts every command-line version
  1 accepted (with the same meaning), plus the ones greed
  wrongly rejected: with `def f(a='A', p: pair='P')` and two
  operands, 0.6 errored; 1.0 skips `a` and fills
  `pair`.
* **Required trailing arguments.**  Keyword-only parameters
  without defaults were a 0.6 configuration error; in
  1.0 they're required trailing arguments (`def cp(*src,
  dest)`).
* **Same option string, several groups.**  Appeal 0.6 refused a
  converter reuse that declared the same option string twice;
  1.0 allows it when the grammar matches, and position
  decides which group an occurrence configures.
* **Errors print to standard error** by default--the POSIX
  diagnostic convention (0.6 printed them to stdout;
  `Appeal(errors=sys.stdout)` restores that)--and usage errors
  exit with status 2 (0.6 exited 255).
* **Exception names.**  The `Appeal`-prefixed names
  (`AppealUsageError`, ...) are the real class names again,
  with the short spellings kept as aliases--and the hierarchy
  is new: `AppealDataError` is the base, `AppealUsageError`
  the command-line subclass.  `AppealCommandError` (alias
  `CommandError`), which 0.6 documented but never caught, is
  now actually wired: raise it from your command to fail
  with a message and a chosen exit code.
* **Preparers are gone.**  `app.app_class()`,
  `app.command_method()`, and `CommandMethodPreparer` are
  replaced by the class-as-app (`@app.global_command()` on a
  class).
* **`Appeal()` constructor arguments** changed: `help=` is
  gone (help is always on), `positional_argument_usage_format=`
  and `default_options=` are gone (see `@app.parameter()` and
  `@app.option()` respectively); `theme=` and `repeat=` are
  new.
* **An `Appeal` object is reusable.**  Appeal 0.6's "you can't
  call `main()` twice" restriction is gone; per-run state
  lives in the `Processor`.
* **Repeating an option is last-one-wins**, the getopt/argparse
  behavior (0.6 errored with "specified more than once").
  This includes different option strings sharing a parameter
  (`--north --south` is south).  `Option` classes repeat too--
  `option()` is called per occurrence--and `MultiOption` is now
  an alias of `Option`; declare at-most-once by subclassing
  `StrictOption` (which is what 0.6 called `Option`).
* One-character parameter names get only a short option (0.6
  behavior, uniformly enforced), and 0.6's occasional
  internal-repr error messages are now English.

And the additions, one more time, in list form: tab
completion, composed help with templates and themes, cycling,
class-as-app, config layering, `name=`, `list[T]`/`dict[K, V]`/
`tuple[...]` spellings, the REPL, MCP servers, `app.schema()`'s
two formats--
and speed: cold start (build plus first parse) is roughly 10x
faster than 0.6, warm parses roughly 700x, and a
standalone script pays about 2.6ms of total startup where a
0.6 program paid about 70ms.


## Changelog

**1.0** *2026*

Appeal 1.0: the rewrite.  See
[What Changed From Appeal 0.6](#what-changed-from-appeal-06) just above; the
grammar's specification of record lives in
[appeal.grammar.md](appeal.grammar.md).

**0.6.4** *2026/02/24*

* Bugfix: the release of my *big* library, version 0.13,
  broke a little code.  This is just a bugfix release to
  make Appeal compatible with the new big, specifically
  with the new big Log.

**0.6.3** *2024/09/06*

* Bugfix for usage.  If conversion fails for a command-line
  parameter, Appeal now prints a context-specific error,
  followed by usage for the command that failed.  Fixes #18.
* Bugfix for `read_mapping`.  Previously you couldn't have
  two parameters with the same name anywhere in the annotations
  tree for a mapping function, and now you can.

**0.6.2**  *2023/10/12*

* Presentation change: if you run a program without arguments,
  runs no-argument `help` instead of `usage`.  This prints out
  both usage information and a list of commands, which seems more
  useful.  That's how most modern programs do it (e.g. `git`, `hg`).
* Minor API change: renamed Appeal's custom exceptions, to
  remove the word `Appeal`.  So, for example, `AppealUsageError`
  is now simply `UsageError`.  I added aliases so the old names
  still work.  (History note: 1.0 flipped this back--the
  prefixed names are the real names, the short names are the
  aliases.)
* Fixed usage generation, added tests.
