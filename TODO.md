# TODO

Agreed work, not yet built.  Larry's rulings; one entry per item.

## Integration tests in the suite (Larry, 2026-09-10)

Real programs, run end to end through `app.main`/`app.process` and
`help`, with their rendered pages and outputs pinned--the way the
rgb/color test pins two whole pages.  Today's suite verifies mostly
from the inside (corpus shapes, engine states); the bugs found on
2026-09-10 (nesting never rendered, group options undecorated,
complex off the leaf list, Literal, async) were all invisible to it.
Candidates: a tron-shaped program (commands, subcommands, config,
verbatim passthrough), a video-downloader-shaped one, and a class-as-
app; each exercising help pages, errors with trailers, completion
tables, and the schema.  Smoke-testing tron by hand is anecdotal;
this goes in `tests/`.
