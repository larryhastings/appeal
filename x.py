
def cvt(a:int, b:float, c=None, *, color=None):
    return (a, b, c, color)


@app.command()
def cmd(w:float, x:int, y:cvt=None, z:cvt=None, *, verbose=False):
    return (w, x, y, z, verbose)


######################################################################


class DefaultValue:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class Option_color(Option):
    def __init__(self):
        # self.oparg consumes eagerly
        self.value = self.oparg('color', str)

    def __call__(self):
        return self.value


class Converter_cvt(Converter):
    def __init__(self):
        # self.argument(name, converter, required)
        # consumes normally, still obeys - to mean option
        self.argument('a', int, True)
        self.argument('b', float, True)
        self.argument('c', str, False)

        self.color = DefaultValue(None)

        # self.option registers a handler
        # if the back-end encounters that option,
        # it calls the handler
        self.option("-c", Option_color)
        self.option("--color", Option_color)

    def __call__(self):
        # late-binding construction
        return cvt(*self.args, color=self.color())


class Command_cmd(Command):
    def __init__(self):
        # default values
        self.w = self.x = self.y = self.z = None

        self.argument('w', float, True)
        self.argument('x', int, True)
        self.argument('y', Converter_cvt, False)
        self.argument('z', Converter_cvt, False)

        self.verbose = DefaultValue(False)
        self.option("-v", Option_color)

    def __call__(self):
        return cmd(*args, verbose=self.verbose())

# Note to self (and Claude):
#
# this approach means if you have an option that calls a function/constructor,
# and it's not a MultiOption, and the user specifies it twice on the command-line,
# we'll collect all the inputs to it (opargs, sub-options), but we won't ever
# call __call__ to construct it.
#
# Example: imagine instead of Converter_cvt we had Option_cvt.  It's bound to --cvt
# for our command cmd.  The user runs
#            foo.py cmd --cvt 1 2 3 -c Red -cvt 4 5 6 -c Blue
# We create two Converter_cvt objects.  The first one gets 1 2 3 and color Red.
# The second one gets 4 5 6 and color Blue.  But the second one replaces the first
# one.  When cmd.__call__ is called, 
#
# I think it's okay, but it's something to keep in mind.