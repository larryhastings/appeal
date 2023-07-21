#!/usr/bin/env python3

import big.all as big
import pathlib
import sys


argv0 = pathlib.Path(sys.argv[0])
appeal_dir = argv0.parent

appeal_script = appeal_dir / "__init__.py"
# appeal_script = appeal_dir / "test.py"

if len(sys.argv) == 1:
    script = appeal_script
else:
    if sys.argv[1] in ("-h", "--help", "help"):
        print("usage: want_prints.py <filename>")
        print()
        print("toggles 'if want_prints:' blocks on and off.")
        print("if you don't specify a filename, it toggles them")
        print("in __init__.py in the same directory.")
        sys.exit(0)

    script = pathlib.Path(sys.argv[1])


with script.open("rt") as f:
    lines = big.PushbackIterator(big.lines_rstrip(big.lines(f.read())))

output = []
append = output.append

uncommented_line = "want_prints = 1"
commented_line = "want_prints = 0"


# stage 1: find want_prints line
for info, line in lines:
    if line == uncommented_line:
        comment = True
        append(commented_line)
        break
    if line == commented_line:
        comment = False
        append(uncommented_line)
        break
    append(line)
else:
    sys.exit("error: couldn't find 'want_prints = 1' line in '{str(appeal_script)}'")



# stage 2: either comment out or uncomment out 'if want_prints:' blocks

block_indent = None

empty_lines = []
append_empty_line = empty_lines.append

def flush_empty_lines(comment=False):
    if not comment:
        output.extend(empty_lines)
    else:
        empty_comment = block_indent + '#'
        for _ in empty_lines:
            append(empty_comment)
    empty_lines.clear()



if_want_prints = 'if want_prints:'
comment_string = '# '

for info, line in lines:
    if block_indent is not None:
        if not line:
            append_empty_line(line)
            continue

        # if line doesn't start with block_indent, we've definitely outdented.
        if line.startswith(block_indent):

            # okay, it still starts with block_indent.
            # we might still have outdented exactly to block_indent.

            sensor = line[len(block_indent)]
            # append(f"## LINE {info.line_number} LINE '{line}' SENSOR '{sensor}'")

            if comment:
                # we're commenting.
                # stay in block if sensor is whitespace.
                if sensor.isspace():
                    flush_empty_lines(comment=True)
                    line = block_indent + comment_string + line[len(block_indent):]
                    append(line)
                    continue
            else:
                # we're uncommenting.
                # if we hit any empty lines, we're out of the block.
                stay_in_block = not bool(empty_lines)

                if stay_in_block and (sensor == '#'):
                    if line.strip() == '#':
                        # just an empty line comment.
                        # it won't have comment_string, which is '# '.
                        # just emit an empty line and stay in block.
                        append('')
                        continue

                    _, octothorpe, line = line.partition(comment_string)
                    assert _.startswith(block_indent)
                    assert (not _) or _.isspace(), f"line {info.line_number}: expected either no indent or all spaces but line starts with '{_}'"
                    assert octothorpe
                    append(block_indent + line)
                    continue

        # append("## END BLOCK")
        if comment and line.startswith(block_indent + '#') and not empty_lines:
            # insert an empty line between the if want_prints block
            # and the comment line that follows
            append_empty_line('')

        block_indent = None
        flush_empty_lines()
        # don't just append, it might be another 'if want_prints:' line
        lines.push((info, line))
        continue


    if not line.endswith(if_want_prints):
        append(line)
        continue

    stripped = line.strip()
    block_indent = line[:-len(stripped)]

    # start block
    if comment:
        assert stripped == if_want_prints, f"failed on line {info.line_number}: expected '{if_want_prints}' but got '{stripped}'"
        append(block_indent + comment_string + if_want_prints)
        continue

    # uncomment
    assert comment_string in line, f"failed on line {info.line_number}: expected comment string {comment_string!r} in line {line} but none was found"
    block_indent, octothorpe, line = line.partition(comment_string)
    assert octothorpe
    # append("## START BLOCK")
    append(block_indent + if_want_prints)
    continue

flush_empty_lines()

lines = "\n".join(output)

with script.open("wt") as f:
    f.write(lines)
