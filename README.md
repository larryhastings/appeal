![## Appeal](https://raw.githubusercontent.com/larryhastings/appeal/master/resources/images/appeal.logo.png)

![## Give your program Appeal!](https://raw.githubusercontent.com/larryhastings/appeal/master/resources/images/give.your.program.appeal.png)

##### Copyright 2021-2023 by Larry Hastings

[![# test badge](https://img.shields.io/github/actions/workflow/status/larryhastings/appeal/test.yml?branch=master&label=test)](https://github.com/larryhastings/appeal/actions/workflows/test.yml) [![# python versions badge](https://img.shields.io/pypi/pyversions/appeal.svg?logo=python&logoColor=FBE072)](https://pypi.org/project/appeal/)


## Quickstart

```Python
import appeal
import sys

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
Python, like `argparse`, `optparse`, `getopt`,
`docopt`, `Typer`, and `click`.  But Appeal takes a
refreshing new approach.

Other libraries have complicated, cumbersome interfaces
that force you to repeat yourself over and over.
Appeal leverages Python's rich function call interface,
making it effortless to define your command-line interface.
You write Python functions, and Appeal translates them into
command-line options and arguments.

Appeal provides amazing power and flexibility--but it's
also intuitive, because it mirrors Python itself.
If you understand how to write Python functions,
you're already halfway to understanding Appeal!

Appeal has only one dependency,
[my **big** library.](https://github.com/larryhastings/big)

Appeal is currently only supported for POSIX platforms
(UNIX, Linux, BSD, OS X, etc).  It might work on Windows
but this has not yet been tested.

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
the second is `-xz`.  You can combine options togther,
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
*keyword-only* argument.

This leads us to the fundamental concept behind Appeal.
With Appeal, you write a Python function, and tell
Appeal that it represents a *command.*  Appeal
examines the function, translating its parameters into
command-line features.  Positional parameters become
command-line arguments, and keyword-only parameters
become options.

(Technically, Appeal translates both *positional parameters*
and *positional-or-keyword parameters* into arguments.
For the sake of clarity and consiseness, I'll always
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

    usage: script.py hello name

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
  parameters, `name`.  Therefore, our `hello` command
  on the command-line takes one positional argument,
  which we identify as `name` in the usage string.
* Appeal also automatically created simple help for our
  program, displaying *usage* information.  Usage shows
  you what command-line options and arguments the command
  will accept.

So!  If you ran this command at the command-line:

    % python3 script.py hello world

Appeal would call your `hello()` function like this:

```Python
hello('world')
```

and you'd be rewarded with:

    Hello, world!

The return value from your command function is the return
code for your program.  If you return `None` or `0`, that's
considered success; returning a non-zero integer indicates
failure.  (And if your function exits without a return
statement, Python behaves as if your function ended with
`return None`.)


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
fgrep('WM_CREATE', None)
```

Actually that's not 100% accurate.  When Appeal
builds the arguments to call your `fgrep()` function,
it only passes in the arguments you passed in on the
command-line.  So actually Appeal calls your `fgrep()`
function like this:

```Python
fgrep('WM_CREATE')
```

And it's Python that sets the `filename` parameter to `None`.

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

    usage: script.py fgrep [-c|--color] [-n|--number int] [-i|--ignore-case] pattern [filenames]...

Again, a lot just happened.

First, I'll remind you, keyword-only parameters
are presented as options on the command-line.
Appeal automatically took each keyword-only parameter,
added `'--'` to the front of the parameter name,
and turned that into an option.  (Also, if the parameter
name has any underscores, Appeal turns those into dashes.)

Second, Appeal also automatically uses the first letter of a
keyword-only argument as a short option.  So the
`color` keyword-only parameter becomes both the `--color`
*and* `-c` options.  When running your program, the user
can use `-c` or `--color` interchangably.  The same goes
for `-i` and `--ignore_case`, and for `-n` and `--number`.

(What if you have two keyword-only parameters that start
with the same letter?  The first one gets the short option.
If we added a keyword-only parameter named `credit` to the
end of `fgrep()`'s parameter list, Appeal would map `color`
to `--color` and `-c`, but only map `credit` to `--credit`.)

Third, options are *always optional.*
(As a pedantic wag might put it--"the clue's right there in the name.")
Therefore, in Appeal, keyword-only
parameters to command functions must *always* have a
default value.  (Python programmers usually have default
values for their keyword-only parameters anyway, so this
requirement isn't a big deal.)

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
so no conversion is necessary.)  When the user provides an oparg
to `--number` on the command-line, it must be followed by an
oparg; Appeal will take that oparg, pass it in to `int`, then take
the return value from `int` and pass it in to the `number` parameter.

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


## The Global Command, Subcommands, And The Default Command

Many programs that support "commands" also have
"global options".  Global options are options
specified on the command-line *before* the command.
For example, in the example command-line at the top
of this document, `script.py` takes a `--debug`
option specified before the command--which makes it
a "global option".

Appeal supports global options, too.  It's simple:
write your command function like normal, but
instead of decorating it with `Appeal.command()`, decorate
it with `Appeal.global_command()`.  Appeal will process all
those options before the command, and call your global
command function.

`Appeal.global_command()` also gets used for programs that
don't use "commands".  Although the "command" command-line
paradigm is popular these days, most programs don't bother
with them.  For example, `ls`, `grep`, and... hey! `python`
itself!  None of these programs support commands, but they
all support command-line arguments and options.

Naturally, Appeal supports this behavior.  Simply decorate
one function with `Appeal.global_command()` and don't add
any command functions.

On the flip side of this coin, Appeal also supports
*subcommands*.  This is a common feature of command-line
parsing libraries, though it's rarely-used in practice.
The idea is, your command can *itself* be followed by
another command.

To add a subcommand to your Appeal instance, just
decorate your command function with two chained
command calls, specifying the name of the existing
command in the first call, like so:

```Python
@app.command()
def db(...):
    ...

@app.command("db").command()
def deploy(...):
    ...
```

This adds a `deploy` subcommand under the `db` command.
So now the whole command-line looks something like this:

    script.py [global arguments and options] db [db arguments and options] deploy [deploy arguments and options]

Finally, what should Appeal do if your program
takes commands, but the user doesn't supply one?
That's what the *default command* is for.  The
default command is a command function Appeal will
run for you if your Appeal instance has commands,
and the user doesn't supply one.  For example,
if `script.py` has ten different commands, but the
user just runs

    script.py

without any arguments, Appeal would run the default
command.

If you don't specify a default command, Appeal has
a built-in default *default command*.  The default *default
command* raises a usage error, which means it prints basic help
information and exits.

To specify your own default command, just decorate a
command function with the `Appeal.default_command()` decorator.
For example, if you wanted your program to run the `status`
command when the user didn't specify a command, you could
do this:

```Python
@app.default_command()
def default():
    return status()
```

Notice that the default command doesn't take any arguments
or options.  It simply can't accept any, by definition.

(If the user specified options
without a command, they'd be considered "global options"
and would be processed by the global command.  And if the
user specified an argument, that would automatically be the
name of the command to run.)

And yes, subcommands can have a default command too:

```Python
@app.command('db').default_command()
def db_default():
    return db_status()
```

## Annotations And Introspection

Python 3 supports annotations for function parameters, meant
to conceptually represent types.  Appeal supports annotations
too; they explicitly tell Appeal what type of object a parameter
requires.  For example:

```Python
import appeal
app = appeal.Appeal()

@app.command()
def fgrep(pattern, *filenames, id:float=None):
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
    keyword-only parameter, Appeal will use a special internal
    class that implements the special-case "negate the default"
    behavior for options with boolean default values.
* If the signature for that parameter lacks both an annotation
  *and* a default value, Appeal uses `str` as the converter.

Converters are surprisingly flexible.
For example, Appeal will introspect the converter for a
keyword-only parameter and map all its positional arguments
into opargs.  That's how Appeal supports options that take
*multiple opargs:* you simply annotate the keyword-only
parameter with a converter that takes *multiple arguments.*
Appeal will also pay attention to the annotations for the
converter's own arguments, and use those to convert the
strings from the command-line into Python objects.

Let's tie it all together with another example:

```Python
import appeal
app = appeal.Appeal()

def int_and_float(integer: int, real: float):
    return [integer*3, real*5]

@app.command()
def fgrep(pattern, *filenames, position:int_and_float=(0, 0.0)):
    print(f"fgrep {pattern} {filenames} {position}")

app.main()
```

Here, Appeal would introspect `fgrep()`, then also
introspect `int_and_float()`.  The resulting usage
string would now look like this:

    usage: script.py fgrep [-p|--position integer real] pattern [filenames]...

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
> Fortunately, there are ways to get static type analyzers
> to work alongside Appeal.
>
> First, you can decorate your Appeal command functions
> and converters with `@typing.no_type_check()`.  This should
> only be necessary if you use functions as annotations;
> if you only ever use types and classes, this shouldn't be
> necessary.
>
> Second, if you're using Python 3.9 or newer, you can use
> `typing.Annotated` with your annotations.  `typing.Annotated`
> allows you to specify an ordered list of values, and static
> type hints only ever use the *first* value.  Appeal also
> handles `typing.Annotated`, but Appeal only ever uses the
> *last* value.  This makes it easy--you can have both types
> of annotations, side by side, and both static type checkers
> and Appeal are perfectly happy.


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
`appeal.split()` will split the command-line across all of
them.  (If you don't specify any delimiters, `appeal.split()`
will split at every whitespace character.)


## Specifying An Option More Than Once

One thing you might have noticed by now: the interfaces
you've seen only allow Appeal to handle command-lines
where an option can be specified either zero times or
one time.  What if you want the user to be able to
specify an option three times?  Or ten?  That's what the
`MultiOption` class is for.  `MultiOption` objects
are converters that allow options to be specified
multiple times.

`MultiOption` isn't useful by itself; it's only an
abstract base class.  To make use of it you'll
need to use a subclass--or create your own.

This time, let's start with some examples.  Appeal
provides three useful subclasses of `MultiOption`:
`counter`, `accumulator`, and `mapping`.

First, let's look at `counter`.  `counter`
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
def fgrep(*, verbose:appeal.counter()=0):
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

`accumulator` handles options that take a single oparg.
It remembers them all and returns them in a single array.
Like so:

```Python
import appeal
app = appeal.Appeal()

@app.command()
def fgrep(*, pattern:appeal.accumulator=[]):
    print(f"fgrep pattern={pattern!r}")

app.main()
```

If the user ran

    % python3 script.py fgrep --pattern three -p four --pattern fiv5

Appeal would call

```Python
fgrep(pattern=['three', 'four', 'fiv5'])
```

What if you don't want strings, but another type?  Using crazy
science magic from the future, `accumulator` is actually
parameterized.  You can say:

```Python
import appeal
app = appeal.Appeal()

@app.command()
def fgrep(*, pattern:appeal.accumulator[int]=[]):
    print(f"fgrep pattern={pattern!r}")

app.main()
```

and now the opargs to `--pattern` will all be converted using int.

You can even specify multiple types as arguments to the
parameterized version of `accumulator`, separated by commas.
The option will then require multiple opargs and convert
them to the types specified.

`mapping` is like `accumulator` except it returns a
`dict` instead of a `list`.  An option annotated with `mapping()`
consumes *two* positional arguments from the command-line;
the first one is the key, the second one is the value.
(You can also parameterize `mapping` the same way you parameterize
`accumulator`, though you can only specify exactly two types.)


Of course, you can also subclass `MultiOption` to make your own
converter classes with custom behavior. `MultiOption` subclasses
can override these three methods:

```Python
class MultiOption:

    def init(self, default):
        ...

    def option(self, ...):
        ...

    def render(self):
        ...
```

Well, actually, subclasses are *required* to override
`option()` and `render()`.  But `init()` is optional.

If you then specify a subclass of `MultiOption` as an
annotation on a keyword-only parameter of an
Appeal command function, several things happen:

* If that option is specified one or more times on
  the command-line, Appeal will instantiate exactly
  one of these objects and call its `init()` method.
* Every time the user specifies that option on
  the command-line, Appeal will call the `option()`
  method on the object.
* After finishing processing the command-line,
  Appeal will call the `render()` method on the
  object, and pass the value it returns as the
  argument to that keyword-only parameter.

The most powerful part of this interface: you can
redefine `option()` to suit your needs--it supports
the same sort of polymorphism as annotations do.
Appeal will introspect your `option()` method to
determine how many opargs to consume from the
command-line, and how to convert them.

Let's demonstrate all this with another example.
If you want your option to take two opargs,
with one being an `int` and the other being
a `float`, you would define `option()` in your
subclass as:

```Python
class MyMultiOption(appeal.MultiOption):

    def option(self, a:int, b:float):
        ....
```

Every time the user specified your option,
it would take two opargs, and they would be
converted into an `int` and a `float` before
calling your `option()` method.  It's up to
you to decide how to store them, and how to
render them into a single value returned
by your `render()` method.

`MultiOption` is a subclass of a general
`Option` class.  `Option` behaves identically
to `MultiOption`, except it only permits
specifying the option once on the command-line,
which means it will only your `option()`
method once.
You usually don't need to bother with making subclasses
of `Option`--it's usually better to just use a class
directly, like our `class IntAndFloat` example.
The only feature you get by subclassing `Option` is,
you get the default value for the parameter passed in
to your constructor.

(The downside of subclassing `Option` and `MultiOption`
is that it makes exporting your Appeal API as an automation
API a little less convenient for the user, because your
users will have to construct these objects and feed
values into them by calling the `option` method.)


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
def go(direction:appeal.validate('up', 'down', 'left', 'right', 'forward', 'back')):
    print(f"go direction={direction!r}")

app.main()
```

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

Some programs have a set of options on their
command-line that are mutually exclusive.  Consider
this simple-minded command-line:

    go [--north|--south|--east|--west]

That is, you want the user to be able to "go" in
one of those four directions, but *only* one.
How would you do that in Appeal?

Easy.  You simply define multiple options that
write to the same parameter.  All the behavior
you've seen so far is using the *default* way of
mapping keyword-only parameters to options.  But
actually Appeal allows you to make your own mappings.
You can map a parameter as many ways as you want,
even using different converters!

To manually define your own options, use the `Appeal.option()`
method on your Appeal instance.  It's a decorator you
apply to your command function.  The first argument is
the name of the parameter you want the option to write
to.  After that is one or more options you want to
map to this parameter.  `Appeal.option()` also takes
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
You can even annotate with a `MultiOption` to allow specifying
that option multiple times!

Note that, whenever you use the `option()` decorator
to map your own options onto a parameter, Appeal won't add
its default options for that parameter.  It'll only have
the options you explicitly set.  Which means, for example,
that in the sample code above, there aren't any short options
for the options we created.  `-n` won't work, only `--north`.

One final thing.  Your command function can accept `**kwargs`
too.  The only things that will go into it are options you
create with `Appeal.option()`, which map to parameters that
don't otherwise exist.


## Recursive Converters

You already know that you can pass in a converter that takes
multiple arguments, and Appeal will consume multiple arguments
from the command-line to fill it.  And if the arguments to that
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
def recurse(a:str, b:my_converter=[(0, 0), '']):
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

(We actually saw our first "argument group" in the
second example in the
**Annotations And Introspection** section above, but
that time the argument group was an oparg.)

Now let's add an option and see what changes:

```Python
import appeal
app = appeal.Appeal()

def int_float(i: int, f: float):
    return (i, f)

def my_converter(i_f: int_float, s: str, *, verbose=False):
    return [i_f, s, verbose]

@app.command()
def recurse2(a:str, b:my_converter=[(0, 0), '', False]):
    print(f"recurse2 a={a!r} b={b!r}")

app.main()
```

Now the usage looks like this:

    recurse2 a [i [-v|--verbose] f s]

Notice the way Appeal renders it in the usage
string--the options aren't created until *after* the first
argument in the optional argument group.  This may seem
strange but that's how it works.  That's how it *has* to work.

Why?  From a high conceptual level, Appeal doesn't know that
you've "entered" the optional argument group until it
sees the user supply the first argument for that group.
So it doesn't create the options defined in that group
until after the first argument.

This high conceptual level corresponds exactly to how Appeal
calls your function.  Consider, if the user runs this command:

    recurse2 xyz

Appeal calls your function like so:

```Python
recurse2('xyz')
```

Since Appeal never called `my_converter()`, it can't
map `--verbose`.  It can only map `--verbose` once it
knows it's going to call `my_converter()`, and that
only becomes true the moment you supply that second
command-line argument.

Once you *do* supply that second command-line argument,
you have to supply two more, for a total of four.

    recurse2 pdq 1 2 xyz

Appeal calls your function like so:

    recurse2('pdq', my_converter(int('1'), float('2'), xyz))

    recurse2 pdq 1 2 xyz

And in this example, you can supply the `-v` or `--verbose` anywhere *after*
the second parameter.  So if your command-line looks like this:

    recurse2 pdq 1 2 xyz -v

Appeal calls `recurse()` like this:

```Python
recurse2('pdq', my_converter(int('1'), float('2'), xyz, verbose=True))
```


Take a look back at all the examples in this document, and consider
that anywhere you specify a function or type, you can pass in nearly
any callable you like.

For example, the parameterized version of `mapping` isn't limited just to simple types.
If you used `mapping[str, int_float]` as the annotation
for a keyword-only parameter, that option would consume
three arguments on the command line: a `str`, an `int`, and
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
def inception(*, option:my_converter=[0, False]):
    print(f"inception option={option!r}")

app.main()
```

Woah, that works too!  We've created an option that
*itself* takes an option.  If you run `fgrep --option`,
you can now also specify `-v` or `--verbose`--but only
*after* you've specified `--option`.

In case you're wondering: `Appeal.option()` must
decorate the function that takes the parameter you're
mapping an option *to.*  So if you want to define
explicit options for the `verbose` parameter to
`my_converter` in the above example, you'd
decorate `my_converter` with `Appeal.option()` calls,
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
def repetition(*args:my_converter):
    print(f"repetition args={args!r}")

app.main()
```

That works too, and I bet you're already guessing what it
does.  This version of `weird` accepts as many `int` arguments
as the user wants to specify on the command-line, and *each one*
can optionally take its own `-v` or `--verbose` flag.

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
def mixin(log:Logging):
    print(f"mixin log={log!r}")

app.main()
```

Can you guess what usage for `mixin` looks like?  (Probably!)
It looks like this:

    mixin [-v|--verbose] [-l|--log-level str]

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
`app.option()` (or `app.parameter()` which we haven't
discussed yet), it can have accept any kind of parameter defined
by Python, and any parameter can use (almost) any converter.
And those converters can recursively use other converters.

Realy, anything can be used with anything:

* Converters for positional parameters
  can take positional parameters, or keyword-only parameters, or `*args`, or `**kwargs`.
* Converters for keyword-only parameters
  can take positional parameters, or keyword-only parameters, or `*args`, or `**kwargs`.
* Converters for `*args`
  can take positional parameters, or keyword-only parameters, or `*args`, or `**kwargs`.
* Command functions can use any converter.
* The global command function can use any converter.

By *now* you can see the expressive power Appeal gives you.
Of course, you'll rarely use only a fraction of that power.
But it's reassuring to know that, whatever command-line API
metaphor you want to express, it's not just *possible* in
Appeal--it's *easy.*


## Classes, Instances, And Preparers

Maybe you've noticed--all the examples so far have used
standard Python functions as Appeal commands.  What about
method calls?  Can you use those for commands?  The answer
is, yes of course!  But it's slightly more complicated.

Appeal's whole purpose in life is to call functions by
pulling data from the command-line.  Whenever it sees a
positional parameter on a function, it thinks "okay, I'm
gonna have to supply an argument to that".  So if you map
an *unbound* method call to a command:

```Python
class MyApp:
    @app.command()
    def sum(self, *operands: int):
        return sum(*operands)
```

Appeal would see the `self` parameter and think "aha! I
need to pass a string in there!"  We need to prevent
Appeal from seeing that parameter in the first place.

There are two major techniques to handle this.  The first
is straightforward, if a bit inflexible: create the instance
of your class first, then call `app.command()()` on the
bound instances.  Like this:

```Python
app = appeal.Appeal()
class MyApp:
    def sum(self, *operands: int):
        return sum(*operands)

o = MyApp()
app.command()(o.sum)
app.main()
```

Since you pass in the already-bound method to Appeal, it doesn't
even *see* the `self` parameter in the signature.  (The signature
of a bound method doesn't include the `self` parameter.)

This works fine... but maybe it looks a little weird.  We're no
longer decorating functions (or methods), instead we're calling
the decorator function directly and passing in the bound method.
It also restricts us to one instance of `MyApp` per Appeal
instance, which might be restrictive.

The other technique uses a little magic to provide a convenient
and familiar-looking interface.  `Appeal.app_class()` gives you
two decorators; you use one to decorate your class, and the
other to decorate methods in the class.  Appeal will instantiate
your class for you, and use your `__init__` method as your app's
"global command" to handle global options!

```Python
import appeal

app = appeal.Appeal()
app_class, command_method = app.app_class()

@app_class()
class MyApp:
    def __init__(self, *, verbose=False):
        print(f"MyApp init verbose={verbose!r}")
        self.verbose = verbose

    def __repr__(self):
        return "<MyApp>"

    @command_method()
    def add(self, a, b, c):
        print(f"MyApp add self={self!r} a={a!r} b={b!r} c={c!r} self.verbose={self.verbose!r}")

app.main()
```

Behind the scenes, this uses a `CommandMethodPreparer` object
to handle late-binding the method to the object.  Since
`Appeal.app_class()` is a little inflexible, you may want
to use these objects directly.  You can create one manually
by calling `Appeal.command_method()`.  Here's an example showing
how to use one:

```Python
import appeal

app = appeal.Appeal()
command_method = app.command_method()

class MyApp:
    def __init__(self, id):
        self.id = id

    def __repr__(self):
        return f"<MyApp id={self.id!r}>"

    @command_method()
    def add(self, a, b, c):
        print(f"MyApp add self={self!r} a={a!r} b={b!r} c={c!r}")

my_app = MyApp("dingus")

p = app.processor()
p.preparer(command_method.bind(my_app))
p.main()
```

This is the first time you're seeing the `Processor`
object.  All the runtime information for processing
a command-line lives in the `Processor` object; in
fact, `Appeal.main` and `Appeal.process` are both
thin wrappers over their equivalent methods on the
`Processor` object.  Moving all the runtime information
into the `Processor` object lets you process multiple
command-lines with the same Appeal object, even
simultaneously!

The `CommandMethodPreparer` object is at the core of how Appeal
handles late-binding of methods to objects.  First,
you decorate the method calls of your class with this object.
You then call the `bind` method on that object to pass in the
instance of that class you want to bind those methods to--though
`app_class()` takes care of that for you.  `bind()` returns a callable
you pass in to `Processor.preparer`, which binds the method to that 
instance before Appeal calls it.

Under the covers, `CommandMethodPreparer` wraps the method
with a `functools.partial` object, passing in a placeholder
object for the `self` parameter.  Then `command_method.bind()`
replaces the placeholder for the real instance.  For maximum
compatibility, it actually uses `getattr()` to bind the
instance to the method.

## Writing Help

Appeal automatically generates *usage* for your command functions.
But it's up to you to write the documentation explaining what those
commands and arguments and options actually *do.*

There's very complete notes on how to write documentation in Appeal,
see `appeal/notes/writing.documentation.txt` in the Appeal source
distribution.  In a nutshell, you write docstring in a particular way,
and Appeal can mechanically parse them and combine them together.
So you document each converter separately, and Appeal smooshes all
these bits of documentation together to produce the help for your
command function.

(One note: the main help for your program should be the docstring
for your Appeal instance's global command.)


## Appeal's latest superpower: reading config files

Appeal allows for friction-free command-line APIs.  You write your
command function, point Appeal at it, and whoosh! now you've got a
command-line interface.  But there are other interfaces users may
want to use to configure your program.  Now Appeal can work with
those too.

For example, your program may read configuration from environment
variables.  Some programs launch an editor; for example `git` will
open an editor when committing a revision.  Traditionally on
UNIX-based platforms this is configurable using two environment
variables, `VISUAL` and `EDITOR` in that order of preference.

Appeal doesn't need to add explicit support for environment variables,
as Python already has an easy-to-use interface.  For example, here's
how to support the environment variables configuring your editor:

However, many programs also support a configuration file, also called
an "rc file"  on UNIX.  By convention settings in such a config file
usually take precedence over environment variables.  For example, you
can configure what editor `git` uses for commits with a value called
`core.editor` stored in a config file.

As of 0.6, Appeal has support for reading data from configuration
files.  Note that Appeal doesn't read the data files itself; you
already have a library for that.  Instead, Appeal has a generic
mechanism for reading data from either an iterable or a mapping--
either a list or a dict.

The first step is to read in the values from the configuration file,
and produce a dict or dict-like object.  You can use any library
you like.  For example, the [`tomli`](https://pypi.org/project/tomli/) library
works well for [TOML files.](https://en.wikipedia.org/wiki/TOML)
JSON and YAML parsers also work nicely.  And this facility works
*especially* well with my [`Perky`](https://pypi.org/project/perky/)
file format.  Though that's just a coincidence, as they were designed
separately, years apart.  Honest!

(You can also use `configparser` to read your INI config file,
but this doesn't mesh well with Appeal's model.  Better support
for reading INI files is a possible future direction for Appeal.)

Once you've got a dictionary containing your configuration information,
you can get Appeal to read from it using a single method call:

```Python
    Appeal.read_mapping(self, callable, mapping)
```

Simply pass in the callable you want called, and the mapping--the dict--you
read from your config file.  Appeal will read the names of the callable's
parameters, pull values out of the mapping using those names, and pass those
values in to a call to the callable.

Of course, any mapping will work.  But this method works particularly
well with classes decorated with `dataclasses.dataclass`.  In just a few
lines, you can define a class to contain your configuration information,
read it out of a file, and populate the class with values of all the correct
types!

In a lot of ways, this works very similarly to Appeal when it's processing
a command-line.  For example:

* Appeal will use the annotations and default values to convert
  the values from the dictionary into the correct types.
* Parameters with default values are optional; parameters without
  default values are required.

But there are differences too:

* You can use positional-only, positional-or-keyword,
  or keyword-only arguments.  However, var-positional
  (`*args`) and var-keyword (`**kwargs`) are unsupported.


Let's bring all of this together with an example.  Let's say we're
writing a hypothetical program that may launch an editor.  Our
sophisticated program has *five* ways to decide what program
to run for the editor.  In decreasing order of importance:

* Command-line options '-e' and '--editor' specify the
  editor to use for this instnace.
* The config file `~/.myprogramrc` is a Perky file, and it
  can contain an `editor` value.
* If the user has set an `VISUAL` environment variable, use that.
* If the user has set an `EDITOR` environment variable, use that.
* The default value is `/usr/bin/vi`.

Here's sample Python code implementing those semantics:

```Python
    default_editor = os.environ.get("VISUAL",
        os.environ.get("EDITOR", "/usr/bin/vi"))
    @dataclasses.dataclass
    class ConfigFile:
        editor:str=default_editor

    d = perky.load(os.path.expanduser("~/.myprogramrc"))
    app = appeal.Appeal()
    config_file = app.read_mapping(ConfigFile, d)

    @app.global_command()
    def global_command(*, editor=config_file.editor):
        print(f"editor = {editor}")

    app.main()
```

Note: using `os.path.expanduser` and a hard-coded filename
like this is no longer considered best practice.  You should use 
[`platformdirs`](https://pypi.org/project/platformdirs/) to
define the paths to your config files.

### Nesting

Appeal's config file reader supports reading values from nested dicts.
This maps directly onto nested function calls in annotations.  If an
annotation takes two or more parameters, the name of the parameter
with that annotation will be used as the name of the nested dict.

Since that probably wasn't clear--sorry!--an example would probably help.
Consider this example dictionary:

```Python
    d = {
        'a': 33,
        'b': {
            'verbose': True,
            'color': 'blue',
        },
    }
```

Here the value of `'b'` is a nested dict.  If we want Appeal to read
a dict with this shape, it will have to descend into that nested dict.
Appeal does that by default when a parameter has an annotation, and
the annotation takes two or more parameters.  Here's sample Python
showing how to read this dict using Appeal:

```Python
    def read_b(verbose=False, color='black'):
        return (verbose, color)

    def config_file(a: int, b: read_b):
        return (a, b)
```

Because `read_b` is an annotation taking multiple parameters,
Appeal will assume the value of `'b'` is a nested dict,
and will get the values of `'verbose'` and `'color'` from
that dict.

If you don't want this behavior, you can disable it by
decorating the annotation function with the `unnested`
method on the Appeal object.  If we change the code to
the following:

```Python
    @app.unnested()
    def read_b(verbose=False, color='black'):
        return (verbose, color)

    def config_file(a: int, b: read_b):
        return (a, b)
```

Appeal *won't* descend into a nested dict named `'b'`.
In this case the dictionary would have to be be completely
flat, like this:

```Python
    d = { 'a': 33, 'verbose': True, 'color': 'blue' }
```

### Iterables

Appeal can also read from iterables inside the dictionary.
The parameter that accepts an iterable should be annotated
with a subclass of `MultiOption`.  Appeal will instantiate
the `MultiOption` and use the MultiOption protocol to
fill the object.

For example, if your config file dict looked like this:

```Python
    d = {
        'color': 'blue',
        'lines': [
            'line 1',
            'here is line 2',
            'and finally, line 3',
            ]
    }
```

Appeal could map it to this callable:

```Python
    @dataclasses.dataclass
    class ConfigFile:
        lines: appeal.accumulator
        color:str = ''
```

If the `MultiOption` option takes multiple parameters,
then the list must contain dictionaries.  For example,
this Python code:

```Python
    class Resolutions(appeal.Multioption):
        def init(self, default=None):
            self.default = default
            self.values = None

        def option(self, width:int, height:int, depth:int):
            if self.values is None:
                self.values = []
            self.values.append((width, height, depth))

        def render(self):
            if self.values is None:
                return self.default
            return self.values

    @dataclasses.dataclass
    class ConfigFile:
        resolutions: Resolutions
        color:str = ''
```

Would be able to read this mapping:

```Python
    d = {
        'color': 'orange',
        'resolutions': [
            {'width': 1280, 'height': 1024, 'depth': 24},
            {'width': 1600, 'height': 1200, 'depth': 16},
        ],
    }
```


### `read_iterable`

In addition to Appeal's `read_mapping` method, Appeal
also supports a `read_iterable` method.  The API is
almost identical:

```Python
    Appeal.read_iterable(callable, iterable)
```

However this function is much simpler.  The iterable
should itself be an iterable of iterables.  Appeal will
call the callable you specify once for every nested
iterable.  The result will be appended to a list, and
`read_iterable` will return that list.

As always, Appeal will handle converting values using
the annotations you specify.  Unlike `read_mapping`,
here you may pass in a function accepting `*args`,
in which case Appeal will process an arbitrary number
of trailing arguments.  Also unlike `read_mapping`,
there's no support for testing--neither nested dictionaties
nor (further) nested lists.  (Like when processing the
command-line, when reading values from the iterable,
nested annotations get flattened.)

### CSV files

Finally, Appeal has special support for reading CSV files.  This may
seem like an odd thing to support--nobody uses CSV files as config
files.  But CSV files were the proof-of-concept for Appeal's config
file support, and it's proved useful in another project, so for
now it's staying in.  There's a special method for reading CSV
files:

```Python
    Appeal.read_csv(self, callable, csv_reader, *, first_row_map=None)
```

You pass in your callable, and a fresh `csv.reader` object.  Appeal
will read the rows out of the `CSV` object, passing in the strings into
the `callable`, and append the result to a list.  The return value is
that list.

If `first_row_map` is false, `read_csv` will ignore the first line of
the CSV file (the "column names" line) and pass in the values from the
CSV file by position.  If `first_row_map` is true, `read_csv` will use
the rows from the first line of the CSV file as keys in a dictionary,
populate the values with each subsequent row, and will pass the
arguments by name.

In other words, if `first_row_map` is false, Appeal calls

```Python
    callable(*row)
```

for every line after the first line in the CSV file.  And if `first_row_map`
is true, Appeal calls

```Python
    d = {key: value for key, value in zip(column_headers, row)}
    callable(**d)
```

for every line after the first line in the CSV file.


## API Reference

`Appeal(help=True, version=None, positional_argument_usage_format="{name}", default_options=default_options)`

Creates a new Appeal instance.

If `help` is true, Appeal automatically adds help support to
your program:

* Adds hard-coded `-h` and `--help` options that print basic help.
* If your Appeal instance has any commands, and you haven't defined
  a `help` command, automatically adds a `help` command.

If `version` is true, it should be a string denoting the version
of your program.  Appeal will automatically add version support
to your program:

* Adds hard-coded `-v` and `--version` options that print
  the version string.
* If your Appeal instance has any commands, and you haven't defined
  a `version` command, automatically adds a `version` command
  which prints the version string.

`positional_argument_usage_format` is the format string used
to format positional arguments for usage.  The only valid
interpolations inside this string are `{name}`, which evaluates
to the name of the parameter, and `{name.upper()}`, which evaluates
to the upper-cased name of the parameter.  So if you want your usage
string to show arguments or opargs as `<name>` or `NAME`, you can
achieve that by setting `positional_argument_usage_format` to
`<{name}>` or `{name.upper()}` respectively.

`default_options` is a callable, called when a keyword-only parameter
for a command function or a converter doesn't have any options
explicitly mapped to it.  The purpose of `default_options` is to
call `Appeal.option()` one or more times to create some default options
for that keyword-only parameter.

The API for a `default_options` callable should be:

    default_options(appeal, callable, parameter_name, annotation, default)

* `appeal` is the Appeal instance.
* `callable` is the command function or converter the parameter is defined on.
* `parameter_name` is the name of the keyword-only parameter that does
   not have any explicitly defined options.
* `annotation` is the annotation for this parameter.  This may
   be explicitly set on the function, or it may be inferred from the
   default parameter.
* `default` is the default value for this parameter.  Since Appeal
   requires that keyword-only parameters must always have default values,
   this may never be `inspect.Parameter.empty`.

The return value of `default_options` is ignored.

The default value of `default_options` is `Appeal.default_options()`,
documented below.


`Appeal.command(name=None)`

Used as a decorator.  Returns a callable that accepts a single
parameter `callable`, which must be a callable.

Adds the callable as a command
for the current Appeal instance.  If `name` is `None`, the name of
the command will be `callable.__name__`.

(Doesn't modify `callable` in any way.)


`Appeal.global_command()`

Used as a decorator.  Returns a callable that accepts a single
parameter `callable`, which must be a callable.

Sets the *global command* for this Appeal object.  This is
the command that processes global options before the first
command function.

Can only be set on the topmost Appeal object.  (You can't
call `app.command('foo').global_command()`.)

(Doesn't modify `callable` in any way.)


`Appeal.default_command()`

Used as a decorator.  Returns a callable that accepts a single
parameter `callable`, which must be a callable.

Sets the *default command* for this Appeal object.  The default
command is run when your Appeal instance has subcommands,
but the user doesn't supply the name of a command on the command-line.

Your default command function must not take any parameters.

(Doesn't modify `callable` in any way.)


`Appeal.option(parameter_name, *options, annotation=empty, default=empty)`

Used as a decorator.  Returns a callable that accepts a single
parameter `callable`, which must be a callable.

Maps an option on the command-line to the parameter `parameter_name`
on the decorated function.  All subsequent positional parameters
are options, like `--verbose` or `-v`.  (Thus, they must be strings,
either exactly two characters long, or four or more characters long.)

`annotation` is the converter that will be used if this
option is invoked.  If no explicit `annotation` is supplied,
`Appeal.option()` will default to `type(default)`.

`default` is the default value for this option.  Since this parameter
only comes into play if the user specifies this option, a `default` value
here is nearly useless.  But it does have two uses:

* If the type of the annotation is a subclass of `Option`, this default
  value will be passed in to `Option.init()`.
* If no `annotation` is specified, the annotation defaults to
  `type(default)`.

It's illegal to call `Appeal.option()` without specifying a value
for either `annotation` or `default`.

Raises `AppealConfigurationError` if any `option` has already been
mapped inside this `Appeal` instance *with a different signature.*

(Doesn't modify `callable` in any way.)


`Appeal.parameter(self, parameter_name, *, usage=None)`

Used as a decorator.  Returns a callable that accepts a single
parameter `callable`, which must be a callable.

Allows for configuration of a positional (or positional-or-keyword)
parameter on a command function or converter.  `parameter_name` is the
name of the parameter; it must be a parameter of the decorated `callable`.

Currently the only supported configuration is `usage`, which specifies
the string that will represent this parameter in usage information.

(Doesn't modify `callable` in any way.)


`Appeal.main(args=None)`

Processes a command-line and calls your command functions.
Stops at the first failure result and passes it in to `sys.exit()`.
Catches usage errors; if it catches one, displays usage information.
The implementation calls `Appeal.process()`.


`Appeal.process(args=None)`

Processes a command-line and calls your command functions.
Stops at the first failure result and returns that result.
Doesn't catch any errors.  Useful mainly for automation,
particularly for testing, and as the main driver underlying
`Appeal.main()`.


`Appeal.default_options()`

`Appeal.default_long_option()`

`Appeal.default_short_option()`

These functions create the default options for a keyword-only
parameter.  They're all valid callbacks for the `default_options`
parameter for the `Appeal()` constructor.  `Appeal.default_options()`
is the default value for that parameter.

`Appeal.default_long_option()` creates the option `--{modified_parameter_name}`
with the default annotation and default value.  `modified_parameter_name` is
`parameter_name.lower().replace('_', '-')`.

`Appeal.default_short_option()` creates the option `-{parameter_name[0]}`
with the default annotation and default value.

`Appeal.default_options()` creates both.

In all three cases, if the function isn't able to map at least one option,
it raises an `AppealConfigurationError`.

Notes on the default option semantics:

* When `Appeal.default_option()` converts a keyword-only parameter
  into a long option and a short option, Appeal copies off the first
  character as the short option, and *then* runs a conversion function
  on the string.  The conversion function lowercases the string and
  converts underscores into dashes.  So for the the keyword-only
  parameter `Define`, `Appeal.default_option()`
  would (attempt to) create the two options `-D` and `--define`.
  For the keyword-only parameter `block_type`, it would attempt to
  create `-b` and `--block-type`.

* What if you have multiple keyword-only parameters that have
  the same first letter?  Only the first mapping succeeds.
  So if you use `def myoptions(*, block_type=None, bad_block=None)`
  as an Appeal command, `-b` will map to `block_type`.  If you
  want it to map to `bad_block`, just swap the two keyword-only
  parameters so `bad_block` is first, or explicitly define your
  options by decorating your function with `Appeal.option()`.
  (As of some recent version, Python guarantees it will maintain
  the order of keyword-only parameters when introspecting a
  function--and it was accidentally true in every version of
  Python before that explicit guarantee anyway.)


`AppealConfigurationError`

An exception.
Raised when the Appeal API is used improperly.

`AppealUsageError`

An exception.
Raised when Appeal processes an invalid command-line.
Caught by `Appeal.main()`, which uses it to print usage
information and return an error.

`AppealCommandError`

An exception.
Raised when an Appeal command function returns a
result indicating an error.  (Equivalent to `SystemExit`.)
Caught by `Appeal.main()`, which uses it to print usage
information and return an error.


## Reference

The library inspects the parameters of your function and uses
those for the arguments, options, and opargs of your subcommand:

* Positional-only and positional-or-keyword parameters
  (parameters before `*,` or `*args,`) map to positional
  arguments.  This:

  ```Python
  @app.command()
  def fgrep(pattern, file, file2=None):
      ...
  ```

  would take two required command-line arguments, "pattern"
  and "file", and an optional third command-line argument "file2".

* Keyword-only parameters map to options.  They must have a default
  value.  The name of the
  parameter is the name of the option, e.g. this subcommand
  accepts a `--verbose` argument:

  ```Python
  @app.command()
  def foo(*, verbose=False):
      ...
  ```

* If an argument to your function has an annotation, that
  value is called to convert the string from the command-line
  before passing in to your function.  e.g.

  ```Python
  @app.command()
  def foo(level:int):
      ...
  ```

  would call `int` on the string from the command-line before
  passing it in to level.

* If a parameter to your function doesn't have an annotation,
  but *does* have a default value, it behaves as if you added
  an annotation of `type(default_value)`.  e.g.

  ```Python
  @app.command()
  def foo(level=0):
      ...
  ```

  would also call `int` on the string from the command-line before
  passing it in to `level`.

  * Keyword-only parameters with a `bool` annotation or a boolean
    default value are special: they don't take an argument.  Instead,
    they toggle the default value.

  * Parameters with a default value of `None` and no annotation
    are also slightly special, in that they take a `str` argument
    (as taking a `NoneType` argument doesn't make sense).

  * Appeal automatically adds single-letter options for keyword-only
    parameters when possible.  Since keyword-only parameters maintain
    their order in Python*++*, Appeal gives the single-letter shortcut to
    the first parameter that starts with that letter.  e.g.

    ```Python
    @app.command()
    def foo(*, verbose=False, varigated=0):
        ...
    ```

    `-v` would map to `--verbose`, not `--varigated`.

Putting it all together: if you wanted to write an "fgrep" subcommand
with a "usage" string like this:

    fgrep [-v|--verbose] [--level <int>] pattern [ file1 [ file2 ... ] ]

you'd write it as follows:

```Python
@app.command()
def fgrep(pattern, *file, verbose=False, level=0):
    ...
```

 *++* This is now guaranteed behavior in current Python, and even
    in the Python 3 series before that, it was always true anyway.


## Appeal And POSIX Utility Semantics

The POSIX standard defines command-line behavior for all POSIX
utility commands, in 1003.1, Chapter 12, currently at revision POSIX.1-2017:

  https://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap12.html

Appeal isn't a perfect match for POSIX semantics; it disallows some
things POSIX allows, and allows some things POSIX disallows.

* As per required POSIX semantics (1003.1-2017, Chapter 12),
  options can never be required.  It therefore follows that
  in Appeal, keyword arguments to command functions must
  always have a default.
* The POSIX standard makes no mention of "long options",
  so it's not clear whether or not the standard permits them.
  (Presumably they will be permitted in a future standard.)
* POSIX requires that options that accept/require multiple opargs
  should accept them as a single string with either spaces
  or commas separating the opargs.  Appeal supports this behavior
  with `appeal.split`.  But it also permits options that consume
  multiple separate opargs from the command-line.
* POSIX requires that all options be specified before any positional
  arguments.  Appeal doesn't enforce this, and will happily consume
  options and positional arguments in any order.  In fact,
  "subcommands" require permitting options after positional arguments
  for anything beyond the simplest possible subcommand support.
* POSIX requires that, if an option (short option) has a single
  *optional* argument (oparg), its argument must be concatenated
  to the option.  For example, if `-f` takes an optional argument,
  and you want to specify the argument `guava`, you *must* write
  this as `-fguava`, no other spelling is permissible.  While Appeal
  supports this spelling, it also supports `-f=guava` and `-f guava`.
  More importantly, if you specify `-f` on your command-line (and
  not `-f=<something>` or `-f<something>`), Appeal *will* consume the
  next argument on the command-line as an oparg, which is what POSIX
  definitely does *not* want.  I feel Appeal's consistency is
  more important than supporting this syntactic hack.  Note that
  the oparg is still optional, so if `-f` is the last thing
  on your command-line, that will achieve this "option with
  default value" behavior.


## Additional Subtle Features And Behaviors

* You can specify options and arguments in any order on a
  command-line, Appeal doesn't care.  If you want Appeal to
  stop recognizing arguments starting with dashes as options,
  specify `--` (two dashes with nothing else).  All subsequent
  strings on the command-line will be used as arguments, even
  if they start with a `-`.
* Many built-in types are not introspectable.  If you call
  `inspect.signature(int)` it throws a `ValueError`.  Appeal
  has special-cased exactly five built-in types: `bool`,
  `int`, `str`, `complex`, and `float`.
* `Accumulator` actually allows parameterizing multiple types,
  separated by commas.  `Accumulator[int, float]` will take
  two opargs each time the option is specified, and the first
  will be an `int` and the second will be a `float`.  The
  list returned will contain tuples of ints and floats.
* You can't call `main()` on an Appeal object more than once.
  The `Appeal()` instance you use has internal state that changes
  when you execute its `main()` method.
* Information about a particular converter is localized to
  a particular `Appeal()` instance.  If you decorate a converter
  with `@app.option()`, every place inside that `Appeal()` instance
  that you use that converter will also pick up the changes you
  made with `@app.option()`.
* You shouldn't call `usage()` until you've added all the
  commands, options, and parameters information into your
  Appeal object.  Why?  Because, for example, `usage()`
  computes the default options for keyword-only parameters
  that haven't gotten any explicitly defined options.
  But if you then define one of those options, Appeal will
  throw an error at you.
* Almost any callable can be a converter--but not *every*
  function.  There are two limitations.  First, as already
  mentioned, in order for a function to be a legal converter,
  every keyword-only parameter must have a default value.
  The second requirement is more specific: in order to use
  a function as a converter for a `*args*` parameter,
  *somewhere* in the annotations tree under that function,
  some function must take a required positional parameter.

Finally, the UNIX `make` command has an interesting
and subtle behavior.  The `--jobs` and `-j` options to `make`
specify how many jobs to run in parallel.  If you run
`make` without any parameters, it runs one job at a time.
If you run `make -j 5`, it runs five jobs at a time.  But!
If you specify `make -j`, where `-j` is the last thing on the
command-line it runs *as many jobs at a time as it wants*.
In a way, the `-j` option has *two default values.*

Can you do this with Appeal?  Naturally!  Simply specify
your keyword-only parameter with both an annotation and
a default value, then design the annotation function
to take one argument that *also* has a default value.
Like so:

```Python
def jobs(jobs:int=math.inf):
    return jobs

@app.command()
def make(*targets, jobs:jobs=1):
    ...
```


Restrictions on Appeal command functions:

* You may not use `inspect.Parameter.empty` as a default value
  for any keyword-only parameter to a converter or command function.
* The converter for a *var_positional* (`*args`) parameter
  *must* require at least one positional argument.


## Changelog

**0.6.2**  *2023/10/12*

* Presentation change: if you run a program without arguments,
  runs no-argument `help` instead of `usage`.  This prints out
  both usage information and a list of commands, which seems more
  useful.  That's how most modern programs do it (e.g. `git`, `hg`).
* Minor API change: renamed Appeal's custom exceptions, to
  remove the word `Appeal`.  So, for example, `AppealUsageError`
  is now simply `UsageError`.  I added aliases so the old names
  still work; I'll remove them eventually, but I promise to
  keep the old names around for at least one year.
* Fixed usage generation, added tests.
* Fixed a bug where using a "simple type" (e.g. bool, float)
  as an annotation for an option would cause Appeal help to
  raise an exception.  Fixes #15.

**0.6.1**  *2023/07/22*

* Fixed 3.6 and 3.7 support--some equals-sign-in-f-strings
  uses crept in.
* Added GitHub Actions integration.  Tests and
  coverage are run in the cloud after every checkin.
  Thanks to [Dan Pope](https://github.com/lordmauve)
  for gently walking me through this!
* Fixed metadata in the `pyproject.toml` file.
* Added badges for testing and supported Python
  versions.  (Didn't add the coverage badge yet...
  it's too embarassing!)


**0.6** *2023/07/20*

A huge upgrade!

* A new feature: Appeal can now read configuration files!
  Check out the new APIs `Appeal.read_mapping`,
  `Appeal.read_iterable`, and even `Appeal.read_csv`.
  This was a massive undertaking and involved a big
  overhaul of the compiler.

* The biggest change to existing behavior: Appeal now
  early-maps options.  (See issue #3.)
  In short: when options are only defined in an optional group,
  they get provisionally mapped (made available) *before* the first
  argument in that group.  Using that option enters the group just
  like specifying the first argument in that group.  You'll see
  the difference in usage; an optional group that mapped an option
  used to look like `[a [-v|--verbose] b c]` but now looks like
  `[[-v|--verbose] a b c]`.

* Appeal now handles multiple short options smashed together
  (e.g. `-ace`) *identically* to them being specified separately
  (e.g. `-a -c -e`).  This caused an observable change in behavior
  regarding when child options get unmapped.

  - Appeal only permits using child options in a limited context:
    it must be after the parent option is executed, it
    must be after the parent option has consumed all its required
    *or optional* opargs, and it must be before any top-level
    positional argument or option mapped before the parent option
    was executed.  But Appeal was lax about enforcing these rules
    when using multiple short options smashed together (e.g. `-ace`);
    it would handle all the options and *then* unmap child options
    as needed.  The good news: Appeal now enforces these rules here
    too.  (The old behavior seems to have been *intentional* on my
    part--what was I *thinking?!)*

* The usage message raised for an unknown option is now much
  better.  If the option is defined anywhere in the program
  being run, it prints a different message telling you it
  can't be used here, but also tells you where it can be used.
  For example, if you use option `-x`, but that's a child
  option mapped by `--parent`, the message would say
  `-x can't be used here, it must be used immediately after --parent`.

* Renamed `Appeal.argument` to `Appeal.parameter`.
  This was one of those "what was I *thinking?"* moments.
  The function affects the parameter, not the argument.
  The old name still works but will be removed before 1.0.

* `short_option_concatenated_oparg` is now more strictly
  enforced: it's only permitted for short options that have
  *exactly one* **optional** oparg, as specified by POSIX.

**0.5.9**

* Improved the error message generated when you have a
  required parameter after a `VAR_POSITIONAL` parameter.
  (This command-line can never succeed, because the
  `VAR_POSITIONAL` consumes all remaining arguments on
  the command-line, which means the subsequent required
  parameter can never be satisfied.)  Fixes #6.
* Changed README to use absolute instead of relative links
  for images, which means images should now render properly
  on the Appeal page at PyPI.  Thanks for the PR, Hugo!
* Switched a bunch of internal classes to use Python "slots".
  Hopefully a tiny memory and speed optimization.
* Remove unused / unneeded stuff:
    * unused `partial_replace*` functions
    * unused Charm bytecode instructions `jump_relative` and `load_o_option`
    * unneeded CharmInterpreter register `option` (only used in one error
      message, obviated by instead using the program name directly)
* Now that Appeal depends on [**big**](https://github.com/larryhastings/big)
  anyway, switch to
  [**big**'s `PushbackIterator`.](https://github.com/larryhastings/big#pushbackiteratoriterablenone).
* Add peephole optimizer step for jump-to-jump optimization.
  Honestly this was probably unnecessary, as I don't think Appeal's
  compiler can even *generate* code with jumps-to-jumps (yet).

**0.5.8**

* Fixed up the "name" of the program for options.  We used to take
  the name of the command, add all the option strings, and join it
  together with commas, as in `'command, -o, --option'`.  Now it
  looks like `'command -o | --option'`.
* Fixed presentation bug: if you didn't have enough positional
  arguments for your command function, but you ever invoked an
  option on the command-line, the usage text would contain the
  name of the last option invoked (aka the last Charm program run).
  Added a regression test for this.
* Cleaned up implementation a little: instead of using mystery
  lists on the `CharmInterpreter` stack and context_stack, I now
  use instances of bespoke `CharmStackEntry` and
  `CharmContextStackEntry` classes.

**0.5.7**

* Rewrite the technology behind `accumulator[...]` and
  `mapping[...]`.  It previously used `exec()`, which was
  limiting; for example, you couldn't use your own types
  or converters.  The new implementation should be much
  more robust; it now manually defines an explicit signature
  for the `option()` method of the subclass it creates.
* This fixed a regression, where you couldn't use a locally-defined
  class (e.g. `IntFloat`) as one of the types in the square
  brackets for `accumulator` and `mapping`.  Added a test
  for this.

**0.5.6**

* Fix formatting for usage when you have a
  global command *and* subcommands.

**0.5.5**

* Add support for `typing.Annotated`, new in Python 3.9.
* Add dependency to
  [my **big** library.](https://github.com/larryhastings/big)
  This gives Appeal a much better implementation of `multisplit`,
  and I plan to switch to the **big** word wrapper and columnizer
  functions, which are a... "big" improvement over what's in
  Appeal right now.
* Rename `SingleOption` to just `Option`.  (The name
  `SingleOption` is now deprecated, but I'll leave it
  as a redundant name for `Option`... for now.)

**0.5.3**

* Fix compatibility back to Python 3.6.

**0.5.2**

* Fix compatibility with Python 3.11.  Python's `inspect.Parameter` object
  no longer allows a `name` that happens to be a keyword, which was a minor
  inconvenience (Appeal used to use `lambda` here sometimes).

**0.5.1**

* Fixed regression, issue #5.  If you didn't supply enough required
  parameters, you'd get a `TypeError` instead of a proper usage error.

**0.5**

* Initial release!
