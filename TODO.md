# TODO

Agreed work, not yet built.  Larry's rulings; one entry per item.

## Config for the whole tree (Larry, 2026-09-10; parity review item 12)

`Appeal(config=mapping, strict=...)` replaces `precommand(config=)`,
which goes away.  One mapping whose shape mirrors the command tree:

* At each level, a scalar feeds an OPTION by parameter name (at the
  root: whichever head era owns that name); a dict keyed by a command
  word is that command's section, nesting all the way down.
* Options only, never positional parameters.
* A key naming both a command word and an option at the same level:
  the command wins, always (not by the value's type--yucky).
* `app.option(..., config=<key>)` overrides the name a parameter is
  looked up under, so a colliding option can still be configured.  No
  symmetric override for command words.
* Keys are parameter names, not option strings (everyone else does it
  this way; it reads better).
* Precedence unchanged: defaults < config < line, atomic per option.
  The same-object rule holds: bind at construction, fill before main().
* `strict` is one knob for the tree (the rc-file case: a file may hold
  keys for a newer version).
* Update: the config-layering docs and tests; drop precommand(config=,
  strict=).

## Literal and choice completion (Larry, 2026-09-10; parity review item 16)

* `Literal['red', 'blue']` means `validate('red', 'blue')`: strings and
  ints only (the leaves that round-trip).  The pathlib trick: peek
  at sys.modules for `typing` (3.5ms to import, more than Appeal;
  never imported for this), and if present test the annotation's
  `__origin__` against `typing.Literal` by identity; the values are
  `__args__`.  One more case beside the list[T]/dict[K, V] origin
  reads.  Today it fails at parse time with "Cannot instantiate
  typing.Literal".  (Literal is 3.8+; older Pythons can't spell it.)
* `validate(...)` offers its values to completion: it knows the closed
  set, it just never told the protocol.  One `completions` attribute.

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
