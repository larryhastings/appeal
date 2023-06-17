Test files for `appeal/want_prints.py`
--------------------------------------


If you want to run the `appeal/want_prints.py` "test suite",
run it with a filename argument.  You should be able to turn
`uncommented.py` into `commented.py` and vice-versa.

There's one extra change `want_prints` will make that deliberately
isn't in the originals though.  When commenting out `want_prints`,
and you have a comment that starts *immediately* after a
`if want_prints:` block:

```Python
    def foo():
        if want_prints:
            print("xyz")
        # comment starts immediately after the block
        # without an intervening blank line
```

`want_prints.py` will insert an empty line between the commented-out
`if` block and a subsequent comment line if there's no empty line
there yet:

```Python
    def foo():
        # if want_prints:
        #     print("xyz")

        # comment starts immediately after the block
        # without an intervening blank line
```

Otherwise, when you run `want_prints` again, to remove comments,
`want_prints` would *uncomment* those comment lines.  And who wants that!
Nobody, that's who.
