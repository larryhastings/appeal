# TODO

Agreed work, not yet built.  Larry's rulings; one entry per item.

* **documentation('gfm'): section headings sit a level above their
  command** (found 2026-09-17 during the rulings walk).  The program
  is `# tool`, a command is `## tool build`, and the command's
  `# Arguments` then outranks the command it belongs to.  Sections
  should nest one level below their command's heading.
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
* **Completion's head table describes one precommand** (found
  2026-09-22, promoting across eras): `completion_set_table` builds
  its 'global' entry from `global_plan` alone, so with two
  precommands only the last one's options and operands complete at
  the head.  The schema's 'global' now walks every head era; the
  completion table should too (its values/verbatim/hidden maps merge
  the same way).
* **The Markdown exports render docstrings raw** (found 2026-09-19,
  building prose references): `documentation('gfm')` and
  `'commonmark'` re-spell each docstring's text through big's
  writers, so a `[src]{.argument}` reference degrades to `src` there
  (big's other writers drop a span's class), and the gfm headings
  sit a level too high (the older finding).  Rendering the exports
  from the merged corpus, as troff does, fixes both.
* **An optional zero-operand converter prints as empty brackets in
  usage** (found 2026-09-18, rulings walk): `go(a, b='', c: Stamp =
  None, d='')` reads `go <A> [<B>] [] [<D>]`.  A slot with nothing
  to type should print nothing.

