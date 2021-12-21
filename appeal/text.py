import itertools
from itertools import zip_longest
import operator

# please leave this copyright notice in binary distributions.
license = """
appeal/text.py
part of the Appeal software package
Copyright 2021 by Larry Hastings
All rights reserved.

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""


def presplit_textwrap(words, margin=79, *, two_spaces=True):
    """
    Combines "words" into lines and returns the result as a string.

    "words" should be an iterator containing pre-split text.

    "margin" specifies the maximum length of each line.

    If "two_spaces" is true, words that end in sentence-ending
    punctuation ('.', '?', and '!') will be followed by two spaces,
    not one.

    Elements in "words" are not modified; any leading or trailing
    whitespace will be preserved.  This is used for the "don't reformat
    indented 'code'" feature: "code" lines start with '\n', and the last
    one ends with '\n' too.
    """

    words = iter(words)
    col = 0
    lastword = ''
    text = []

    for word in words:
        l = len(word)

        if not l:
            lastword = word
            col = 0
            text.append('\n')
            continue

        if two_spaces and lastword.endswith(('.', '?', '!')):
            space = "  "
            len_space = 2
        else:
            space = " "
            len_space = 1

        if (l + len_space + col) > margin:
            if col:
                text.append('\n')
                col = 0
        elif col:
            text.append(space)
            col += len_space

        text.append(word)
        col += len(word)
        lastword = word

    return "".join(text)


test_number = 0
verbose = False

def _test_presplit_textwrap(input, expected, margin=79):
    global test_number
    test_number += 1
    got = presplit_textwrap(input, margin)
    if verbose:
        print()
        print(f"test_presplit_textwrap test #{test_number}:")
        print("[input]")
        print(input)
        print("[expected]")
        print(expected)
        print("[got]")
        print(got)
        print("[repr(got)]")
        print(repr(got))
        print()
    assert got == expected, f"presplit_textwrap test #{test_number} failed!\n{   input=}\n{expected=}\n{     got=}"

def test_presplit_textwrap():
    global test_number
    test_number = 0
    _test_presplit_textwrap(
        "hello there. how are you? i am fine! so there's that.".split(),
        "hello there.  how are you?  i am fine!  so there's that.")
    _test_presplit_textwrap(
        "hello there. how are you? i am fine! so there's that.".split(),
        "hello there.  how\nare you?  i am fine!\nso there's that.",
        20)
    _test_presplit_textwrap(
        ["these are all long lines that must be by themselves.",
        "   more stuff goes here and stuff.",
        " know what i'm talkin' about?  yeah, that's what i'm talking about."],
        "these are all long lines that must be by themselves.\n   more stuff goes here and stuff.\n know what i'm talkin' about?  yeah, that's what i'm talking about.",
        20)



class _column_wrapper_splitter:

    def __init__(self, tab_width, allow_code):
        self.tab_width = tab_width
        self.allow_code = allow_code
        self.words = []
        self.hopper = []
        self.emit = self.hopper.append
        self.col = self.next_col = 0
        self.line = self.next_line = 0
        self.next(self.state_initial)

    def newline(self):
        assert not self.hopper, "Emitting newline while hopper is not empty!"
        self.words.append('')

    def empty_hopper(self):
        if self.hopper:
            self.words.append(''.join(self.hopper))
            self.hopper.clear()

    def next(self, state, c=None):
        self.state = state
        if c is not None:
            self.state(c)

    def write(self, c):
        if c in '\t\n':
            if c == '\t':
                self.next_col = col + self.tab_width - (col % self.tab_width)
            else:
                self.next_col = 0
                self.next_line = self.line + 1
        else:
            self.next_col = self.col + 1

        self.state(c)

        self.col = self.next_col
        self.line = self.next_line

    def close(self):
        self.empty_hopper()

    def state_paragraph_start(self, c):
        if c.isspace():
            if c == '\n':
                self.newline()
            return
        if (self.col >= 4) and self.allow_code:
            next = self.state_code_line_start
        else:
            next = self.state_line_start
        self.next(next, c)

    state_initial = state_paragraph_start

    def state_code_line_start(self, c):
        self.emit(' ' * self.col)
        self.next(self.state_in_code_line, c)

    def state_in_code_line(self, c):
        if c == '\n':
            self.empty_hopper()
            self.next(self.state_paragraph_start, c)
            return
        self.emit(c)

    def state_line_start(self, c):
        if c.isspace():
            if c == '\n':
                self.newline()
                self.next(self.state_paragraph_start, c)
            return
        if self.col >= 4:
            self.newline()
            next = self.state_code_line_start
        else:
            next = self.state_in_text_line
        self.next(next, c)

    def state_in_text_line(self, c):
        if not c.isspace():
            self.emit(c)
            return

        self.empty_hopper()
        if c == '\n':
            self.next(self.state_line_start)


    # def state_paragraph_start(self, c):
    #     if c.isspace():
    #         return
    #     if self.col >= 4:
    #         next = self.state_code_line_start
    #     else:
    #         next = self.state_in_paragraph
    #     self.next(next, c)

    # state_initial = state_paragraph_start

    # def state_code_line_start(self, c):
    #     if c.isspace():
    #         if c == '\n':
    #             self.newline()
    #             self.next(self.state_paragraph_start)
    #         return
    #     if self.col < 4:
    #         raise ValueError("Can't outdent past 4 in a code paragraph! (line " + str(self.line) + " col " + str(self.col) + ")")
    #     self.emit(' ' * self.col)
    #     self.next(self.state_in_code, c)

    # def state_in_code(self, c):
    #     if c.isspace():
    #         if c == '\n':
    #             self.empty_hopper()
    #             self.newline()
    #             self.next(self.state_code_line_start)
    #         else:
    #             self.emit(' ' * (self.next_col - self.col))
    #     else:
    #         self.emit(c)

    # def state_paragraph_line_start(self, c):
    #     if not c.isspace():
    #         return self.next(self.state_in_paragraph, c)
    #     if c == '\n':
    #         self.newline()
    #         self.newline()
    #         self.next(self.state_paragraph_start)

    # def state_in_paragraph(self, c):
    #     if not c.isspace():
    #         self.emit(c)
    #         return

    #     self.empty_hopper()
    #     if c == '\n':
    #         self.next(self.state_paragraph_line_start)


def fancy_text_split(s, *, tab_width=8, allow_code=True):
    """
    Splits up a string into individual words, suitable
    for feeding into presplit_textwrap().

    Paragraphs indented by four spaces or more preserve
    whitespace; internal whitespace is preserved, and the
    newline is preserved.  (This is for code examples.)

    Paragraphs indented by less than four spaces will be
    broken up into individual words.
    """
    cws = _column_wrapper_splitter(tab_width, allow_code)
    for c in s:
        cws.write(c)
    cws.close()
    return cws.words


def _test_fancy_text_split(input, expected):
    global test_number
    test_number += 1
    got = fancy_text_split(input)
    if verbose:
        print()
        print("_" * 79)
        print(f"test_fancy_text_split test #{test_number}:")
        print("[input]")
        print(input)
        print("[expected]")
        print(expected)
        print("[got]")
        print(got)
        print("[repr(got)]")
        print(repr(got))
        print()
    assert got == expected, f"fancy_text_split test #{test_number} failed!\n{   input=}\n{expected=}\n{     got=}"

def test_fancy_text_split():
    global test_number
    test_number = 0
    _test_fancy_text_split(
        "hey there party people",
        ['hey', 'there', 'party', 'people'],
        )
    _test_fancy_text_split(
        "hey there party people\n\nhere, we have a second paragraph.\nwith an internal newline.\n\n    for i in code:\n        print(i)\n\nmore text here? sure seems like it.",
        ['hey', 'there', 'party', 'people', '', '', 'here,', 'we', 'have', 'a', 'second', 'paragraph.', 'with', 'an', 'internal', 'newline.', '', '', '    for i in code:', '', '        print(i)', '', '', 'more', 'text', 'here?', 'sure', 'seems', 'like', 'it.']
        )
    _test_fancy_text_split(
        "hey there party people.\nhere, we have a second paragraph.\nwith an internal newline.\n    for i in code:\n        print(i)\nmore text here? sure seems like it.",
        ['hey', 'there', 'party', 'people.', 'here,', 'we', 'have', 'a', 'second', 'paragraph.', 'with', 'an', 'internal', 'newline.', '', '    for i in code:', '', '        print(i)', '', 'more', 'text', 'here?', 'sure', 'seems', 'like', 'it.']
        )



def _max_line_length(lines):
    return max([len(line) for line in lines])


##
## TODO
## this should take min & max column size
## if a column exceeds its max size,
##   it eats the entire remainder of the line,
##   and subsequent columns are merely indented by min
## if there were columns before it, they continue unabated.
##
##  example: merge_columns(
##     "i am a single line that is way too long for everybody to even permit", 20, 40,
##     "this is the second\ncolumn of text.")
##
##  outputs
##     +--note: this is column 0
##     v
##     i am a single line that is way too long for everybody to even permit
##                         this is the second
##                         column of text.
def merge_columns(*blobs, column_spacing=1, extra_lines_after_too_long=0):
    """
    Merge n blobs containing text together, each blob getting
    its own column.

    Each "blob" is a tuple of three items:
        (text, min_width, max_width)
    Text should be a single text string, with newline
    characters separating lines.

    The width of each column starts by calculating the width of the
    longest line and adding 1 (so there is a space between columns).
    If this is less than min_width, it's brought up to min_width.
    Each line of text is padded with spaces to bring it up to this
    desired width.

    If this calcualted width is greater than max_width, then some
    special formatting takes over.  Let's call the column that
    exceeds its max width Cn, for n'th Column.  Columns C0 through
    Cn will print normally, but columns Cn+1 and up will be paused
    until the _last_ line of Cn that exceeds max_width.

    As an example, calling merge_columns() as follows:
        merge_columns(
            (
                ("1a 1b 1c 1d 1e".split(), 2, 2),
                ("2a 2b 2cd 2efgh 2ijkl 2m 2n".split(), 3, 4),
                ("3abcd 3efgh".split(), 5, 5),
                ("4stuv 4wxyz".split(), 5, 5),
            ), longest=True)
    would return this result:
        1a 2a
        1a 2b
        1a 2cd
        1b 2efgh
        1c 2ijkl
        1d 2m 3abcd 4stuv
        1e 2n 3efgh 4wxyz

    The output will continue until all lines from all blobs are exhausted.
    Any blobs that run short will be padded with spaces, although lines
    will be .rstrip()ped--the last character on each line of the output
    (either before the '\n' or the last line of the string) will return
    False for .isspace().

    This function does not text-wrap the lines.
    """
    columns = []
    widths = []
    last_too_wide_lines = []
    max_lines = -1

    for blob in blobs:
        s, min_width, max_width = blob

        # check types, let them raise exceptions as needed
        assert isinstance(s, str)
        operator.index(min_width)
        operator.index(max_width)

        lines = s.rstrip().split('\n')
        max_lines = max(max_lines, len(lines))
        columns.append(lines)

        measured_width = _max_line_length(lines) + column_spacing
        width = min(max_width, max(min_width, measured_width))
        widths.append(width)

        last_too_wide_line = -1
        if measured_width > max_width:
            for i, line in enumerate(lines):
                if len(line) > max_width:
                    last_too_wide_line = i
        last_too_wide_lines.append(last_too_wide_line + extra_lines_after_too_long)

    column_iterators = [enumerate(iter(c)) for c in columns]
    lines = []

    while True:
        line = []
        all_iterators_are_exhausted = True
        for column_iterator, width, last_too_wide_line in zip_longest(column_iterators, widths, last_too_wide_lines):
            try:
                i, column = next(column_iterator)
                all_iterators_are_exhausted = False
                if i <= last_too_wide_line:
                    line.append(column)
                    break
                column = column.ljust(width)
            except StopIteration:
                column = " " * width
            line.append(column)
        if all_iterators_are_exhausted:
            break
        line = "".join(line).rstrip()
        lines.append(line)

    text = "\n".join(lines)
    return text.rstrip()


def _test_merge_columns(input, expected, **kwargs):
    global test_number
    test_number += 1
    got = merge_columns(*input, **kwargs)
    if verbose:
        print()
        print("_" * 79)
        print(f"test_merge_columns test #{test_number}:")
        print("[input]")
        print(input)
        print("[expected]")
        print(expected)
        print("[got]")
        print(got)
        print("[repr(got)]")
        print(repr(got))
        print()
    assert got == expected, f"merge_columns test #{test_number} failed!\n{     input=}\n{expected=}\n{     got=}"

def test_merge_columns():
    global test_number
    test_number = 0
    _test_merge_columns([("1\n2\n3", 5, 5), ("howdy\nhello\nhi, how are you?\ni'm fine.", 5, 40), ("ending\ntext!", 80, 80)],
        "1    howdy            ending\n2    hello            text!\n3    hi, how are you?\n     i'm fine.")
    _test_merge_columns([("super long lines here\nI mean, they just go on and on.\n(text)\nshort now\nhowever.\nthank\nthe maker!", 5, 15), ("this is the second column.\ndoes it have to wait?  it should.", 20, 60)],
        'super long lines here\nI mean, they just go on and on.\n(text)\nshort now\nhowever.       this is the second column.\nthank          does it have to wait?  it should.\nthe maker!', extra_lines_after_too_long=2)


def _test_pipeline(columns, expected):
    global test_number
    test_number += 1
    splits = [(fancy_text_split(column), min, max) for column, min, max in columns]
    wrapped = [(presplit_textwrap(split, margin=max), min, max) for split, min, max in splits]
    got = merge_columns(*wrapped)
    if verbose:
        print()
        print("_" * 79)
        print(f"test_pipeline test #{test_number}:")
        print()
        print("[columns]")
        print(columns)
        print("[splits]")
        print(splits)
        print("[wrapped]")
        print(wrapped)
        print("[expected]")
        print(expected)
        print("[got]")
        print(got)
        print("[repr(got)]")
        print(repr(got))
        print()
    assert got == expected, f"pipeline test #{test_number} failed!\n{     input=}\n{expected=}\n{     got=}"

def test_pipeline():
    global test_number
    test_number = 0
    _test_pipeline(
        (
            (
            "-v|--verbose",
            20,
            20,
            ),
            (
            "Causes the program to produce more output.  Specifying it multiple times raises the volume of output.",
            0,
            60,
            ),
        ),
        '-v|--verbose        Causes the program to produce more output.  Specifying it\n                    multiple times raises the volume of output.'
    )

    _test_pipeline(
        (
            (
            "-v|--verbose",
            10,
            10,
            ),
            (
            "Causes the program to produce more output.  Specifying it multiple times raises the volume of output.",
            0,
            60,
            ),
        ),
        '-v|--verbose\n          Causes the program to produce more output.  Specifying it\n          multiple times raises the volume of output.'
    )

    # an empty column just adds space.  so, to indent everything, add an empty initial column.
    _test_pipeline(
        (
            (
            "",
            4,
            4,
            ),
            (
            "-v|--verbose",
            20,
            20,
            ),
            (
            "Causes the program to produce more output.  Specifying it multiple times raises the volume of output.",
            0,
            60,
            ),
        ),
        '    -v|--verbose        Causes the program to produce more output.  Specifying it\n                        multiple times raises the volume of output.'
    )


if __name__ == "__main__":
    import sys
    verbose = ("-v" in sys.argv) or ("--verbose" in sys.argv) # ironic, no?
    test_presplit_textwrap()
    test_fancy_text_split()
    test_merge_columns()
    test_pipeline()
