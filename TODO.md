# TODO

Agreed work, not yet built.  Larry's rulings; one entry per item.

## Required options (Larry, 2026-09-09; parity review item 4)

A keyword-only parameter with no default is a REQUIRED option, not a
refusal--reversing v1's "options are always optional".  The docs
guide quietly: options should usually be optional, a value the user
must give is usually an argument, but a required option is allowed.

* Front end: the option rule is marked required, every variety, nested
  converters included (`def pair(a, *, unit)` requires --unit whenever
  pair is entered).
* The check is structural, pass 1: at an era's end the engine asks
  each converter it entered whether every required option landed;
  missing -> `error: missing option '--token'` wearing that command's
  usage, like a missing argument.  -h still wins.
* A precommand's config may supply it (config keys are vetted in the
  scan; the check consults them).
* Usage renders it unbracketed: `deploy --region <REGION> [--dry-run]
  <TARGET>`.  The help table shows nothing extra.
* Schema/MCP: joins the `required` list.  Completion: nothing.
* Update: the corpus test expecting the refusal; the README's three
  "options are always optional" statements.

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
  ints only (the leaves that round-trip).  Recognized by identity on
  the object the user already imported (the pathlib trick: never
  import `typing` for it).  Today it fails at parse time with "Cannot
  instantiate typing.Literal".
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
