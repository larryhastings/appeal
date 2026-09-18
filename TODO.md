# TODO

Agreed work, not yet built.  Larry's rulings; one entry per item.

* **documentation('gfm'): section headings sit a level above their
  command** (found 2026-09-17 during the rulings walk).  The program
  is `# tool`, a command is `## tool build`, and the command's
  `# Arguments` then outranks the command it belongs to.  Sections
  should nest one level below their command's heading.
* **man page OPTIONS loses the placeholder name** (same walk).  The
  usage line says `[-h|--help [<TOPIC>]]`; the OPTIONS entry says
  `\-h|\-\-help [<VALUE>]`.  The rows should carry the same operand
  markup the usage line does.
* **A leftover word popped up from a non-cycling set is diagnosed at
  the set that catches it** (found 2026-09-17, rulings walk).  With
  the root cycling and `db` not, `tool db migrate status` says
  `unknown command 'status'` with the root's suggestions, though the
  word was typed right after `db migrate`.  The suggestion should
  draw on both sets: the one the word was popped from and the one
  that caught it.
* **Drop the injected `line` role once big draws a thematic break to
  the margin** (2026-09-18; the bug report went to the big session).
  `render_markdown_help` and `render_baked_help` inject `line` under
  the sheet and Appeal's `rule` role fills the break glyph to it.
  When wrap_words tiles the break itself, both go, and the theme's
  `rule` entry with them.
* **A subcommand's `plan.usage()` drops its parent words** (found
  2026-09-18, rulings walk): `tool start [<PORT>]` for `tool db
  start`.  Help pages build the line from the node and are right;
  the MCP schema's `usage` field uses `plan.usage()` and is wrong
  for subcommands.
* **The man page's SYNOPSIS stops at top-level commands** (same
  walk): `tool db` appears, `tool db start` never does, and
  subcommand pages aren't in the COMMANDS section either.
* **An optional zero-operand converter prints as empty brackets in
  usage** (found 2026-09-18, rulings walk): `go(a, b='', c: Stamp =
  None, d='')` reads `go <A> [<B>] [] [<D>]`.  A slot with nothing
  to type should print nothing.

