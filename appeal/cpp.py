#!/usr/bin/env python3

"""
cpp

A rudimentary Python preprocessor--a dumb rip-off of Ned Batchelder's "cog".


Usage:
    python3 cpp.py [filename [filename2 ...]]

Scans through the text of each file looking for triple-quoted strings
whose first line is the literal string

    # cpp

It digests the contents of that triple-quoted string, executes it,
and prints out the result after the triple-quoted string.  The code
is executed with an replacement for print() that captures the output.
The captured output is injected into the original file after the
triple-quoted string.


Note: the output is followed by the hard-coded line '# cpp'.  And
when you add a new blob of preprocessor code, you MUST add this trailing
'# cpp' line yourself.  (cp.py has no way of knowing that this is a
newly-created preprocessor block, and will read in the rest of the
file looking for this trailing marker.)


Note: the scanner doesn't actually understand how to match pairs of
triple-quoted strings.  It would have to be very sophisticated to
understand, e.g.

    my_string = ''' a b c
    '''

Instead it's deliberately super-dumb.  The scanner scans for two
lines in a row that, stripped, contain a triple-quoted string,
then the hard-coded string '# cpp'.  So, if you want to, you could
easily fool it, like so:

    hot_mess = '''
    '''
    # cpp

Congratulations, cpp.py will start digesting the code on the line
after the '# cpp' line above, EVEN THOUGH it's not in a hard-coded
string!  Wheeeee.
"""

import os
import sys


def process(filename):
    result = []

    def my_print(*a):
        s = " ".join(str(x) for x in a)
        result.append(s)

    my_globals = {"print": my_print}

    with open(filename, "rt") as f:
        text = f.read()
    lines = enumerate(text.split("\n"))
    processing = False
    quoted_filename = filename if len(filename.split()) == 1 else repr(filename)
    while lines:
        # state 1: scan for triple-quotes
        if not processing:
            for line_number, line in lines:
                result.append(line)
                stripped = line.strip()
                if stripped not in ('"""', "'''"):
                    continue
                prefix = line.partition(stripped)[0]
                marker = stripped
                line_number, line = next(lines)
                result.append(line)
                stripped = line.strip()
                if stripped == "# cpp":
                    processing = True
                    break
            else:
                break
        if processing:
            program = []
            skip = len(prefix)
            for line_number, line in lines:
                result.append(line)
                stripped = line.strip()
                if stripped == marker:
                    # go time!
                    for line_number, line in lines:
                        stripped = line.strip()
                        if stripped == "# cpp":
                            program = "\n".join(program)
                            result.extend((
                                '',
                                '# Don\'t modify this stuff directly!',
                                '# Everything from here to the',
                                '#         # cpp',
                                '# line below is generated.',
                                '#',
                                '# Modify the code in the quotes above and run',
                                f'#         % python3 cpp.py {quoted_filename}',
                                '# to regenerate.',
                                '',
                                ))
                            exec(program, my_globals)
                            result.append(line)
                            processing = False
                            break
                    if not processing:
                        break
                    sys.exit("no '# cpp' marker found for a block. failed.")

                assert line.startswith(prefix)
                line = line[skip:]
                program.append(line)
            else:
                sys.exit("no '# cpp' marker found for a block. failed!")
    result = "\n".join(result)
    if not result.endswith('\n'):
        result += '\n'
    tmpfile = filename + ".txt"
    with open(tmpfile, "wt") as f:
        f.write(result)
    os.unlink(filename)
    os.rename(tmpfile, filename)


"""
# cpp

for i in range(5):
    print(f"# a{i} = {i}")
"""
# a0 = 0
# a1 = 1
# a2 = 2
# a3 = 3
# a4 = 4
# cpp

if __name__ == "__main__":
    for filename in sys.argv[1:]:
        process(filename)
