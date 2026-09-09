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
