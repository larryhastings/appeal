# TODO

Agreed work, not yet built.  Larry's rulings; one entry per item.

## Refuse async commands (Larry, 2026-09-10; parity review item 26)

An `async def` command or converter is refused when its plan builds:
"'fetch' is a coroutine function; wrap it: def fetch(...): return
asyncio.run(...)".  Never run it for them (choosing an event loop is
magic, and breaks inside a running loop).  Today it returns an
unawaited coroutine and the command silently does nothing.

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
