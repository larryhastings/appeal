# TODO

Agreed work, not yet built.  Larry's rulings; one entry per item.

* **documentation('gfm'): section headings sit a level above their
  command** (found 2026-09-17 during the rulings walk).  The program
  is `# tool`, a command is `## tool build`, and the command's
  `# Arguments` then outranks the command it belongs to.  Sections
  should nest one level below their command's heading.
* **The -h row's documentation is rarely seen** (found 2026-09-19,
  building topics).  Its text lives on the -h operand's converter,
  but a program with its own precommand merges only THAT plan's
  options, so the row is absent from its overview and its man page;
  a program without one hides Appeal's head from the overview
  (tables_wanted).  Only the precommand-less man page shows it.
* **A leftover word popped up from a non-cycling set is diagnosed at
  the set that catches it** (found 2026-09-17, rulings walk).  With
  the root cycling and `db` not, `tool db migrate status` says
  `unknown command 'status'` with the root's suggestions, though the
  word was typed right after `db migrate`.  The suggestion should
  draw on both sets: the one the word was popped from and the one
  that caught it.
* **A subcommand's `plan.usage()` drops its parent words** (found
  2026-09-18, rulings walk): `tool start [<PORT>]` for `tool db
  start`.  Help pages build the line from the node and are right;
  the MCP schema's `usage` field uses `plan.usage()` and is wrong
  for subcommands.
* **An optional zero-operand converter prints as empty brackets in
  usage** (found 2026-09-18, rulings walk): `go(a, b='', c: Stamp =
  None, d='')` reads `go <A> [<B>] [] [<D>]`.  A slot with nothing
  to type should print nothing.

