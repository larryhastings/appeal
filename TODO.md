# TODO

Agreed work, not yet built.  Larry's rulings; one entry per item.

## Hidden and deprecated options and commands (Larry, 2026-09-10; parity review item 19)

One knob, `restriction=None | 'hidden' | 'deprecated'`, on
`app.option()` and `app.command()`.  None (the default) is generally
available.

* `'hidden'`: still recognized, absent from usage, the help tables,
  the command listing, the schema, and completion (hidden means
  hidden).  For developer switches and old spellings kept for
  compatibility.
* `'deprecated'`: shown in help with a note, and using it prints
  `warning: '--old' is deprecated` to stderr.  No replacement-name
  parameter: the body prints the pointer if it cares.  No
  hidden-and-deprecated combination.
