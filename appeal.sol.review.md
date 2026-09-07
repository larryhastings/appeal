# Appeal v2.fable5 code review

## Executive assessment

Appeal’s core architecture is coherent, the implementation is extensively tested, and the claimed 100% statement coverage is real. Nevertheless, I would not consider this snapshot release-ready. I found four high-impact correctness or compatibility defects, several medium-impact inconsistencies in auxiliary surfaces, and specific inaccuracies in `README.draft.md`.

This report ignores `README.md` entirely. Codex will not implement these changes; Claude Fable can address them, after which Codex will review the resulting live worktree and rerun the relevant validation.

## Findings

### 1. High — Supported Python 3.6 and 3.7 fail at runtime

The project declares Python 3.6+ in [pyproject.toml](/home/larry/tron/src/appeal/pyproject.toml:11), but the featherweight signature reader unconditionally accesses `code.co_posonlyargcount` in [frontend.py](/home/larry/tron/src/appeal/appeal/frontend.py:170). That attribute was added in Python 3.8.

A minimal registered command fails on both Python 3.6 and 3.7 with:

```text
AttributeError: 'code' object has no attribute 'co_posonlyargcount'
```

There is a second compatibility failure: the chained decorator expression in [test_appeal.py](/home/larry/tron/src/appeal/tests/test_appeal.py:5185) requires PEP 614 and is invalid syntax on Python 3.6–3.8. Consequently the 3.7 and 3.8 jobs declared in [test.yml](/home/larry/tron/src/appeal/.github/workflows/test.yml:79) cannot run the canonical test suite.

Recommendation:

- Either restore 3.6 compatibility using an appropriate fallback such as `getattr(code, "co_posonlyargcount", 0)` and rewrite the chained-decorator test, or formally raise the supported floor.
- Add a real command-construction smoke test—not merely `import appeal`—on every supported Python.
- If the floor remains 3.6, ensure tests themselves use only 3.6-compatible syntax.

### 2. High — MCP errors can terminate the server without a response

[run_mcp()](/home/larry/tron/src/appeal/appeal/mcp.py:288) catches only `AppealDataError` around a tool call at [mcp.py](/home/larry/tron/src/appeal/appeal/mcp.py:325). A command raising the intentionally public `CommandError`, a general `AppealError`, or an unexpected exception terminates the server and sends no JSON-RPC response.

Other confirmed protocol defects include:

- Malformed JSON is silently discarded.
- Valid JSON that is not an object, such as `[]`, crashes at `request.get(...)`.
- An arbitrary requested protocol version such as `"totally-unsupported"` is echoed as though supported at [mcp.py](/home/larry/tron/src/appeal/appeal/mcp.py:298).
- Request and parameter shapes are insufficiently validated, allowing malformed clients to terminate the process.

The MCP specification distinguishes tool-execution failures, which should return a tool result with `isError: true`, from malformed protocol requests, which should return JSON-RPC errors. It also requires the server to answer an unsupported requested version with a version the server actually supports. [MCP tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools), [MCP lifecycle specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle).

Recommendation:

- Convert `CommandError` and other expected business failures into `CallToolResult`-style responses with `isError: true`.
- Convert unexpected server exceptions into an internal JSON-RPC error without ending the request loop.
- Validate decoded JSON, JSON-RPC fields, method parameters, and tool arguments.
- Declare and negotiate an explicit set of supported MCP versions.
- Add a regression proving that a failed call is followed successfully by another request on the same server.

### 3. High — `functools.wraps` silently changes the command grammar

The custom signature implementation checks `__signature__`, but it does not follow `__wrapped__` in [frontend.py](/home/larry/tron/src/appeal/appeal/frontend.py:149).

For a wrapped command whose intended signature is:

```python
def command(count: int, *, loud=False):
    ...
```

Appeal sees the wrapper’s `(*args, **kwargs)` signature. The consequences are silent and significant:

- `count` remains a string instead of being converted to `int`.
- `--loud` is rejected as an unknown option.
- The plan reports unbounded positional arity rather than the public signature.

This contradicts the project audit’s claim that `functools.wraps`-wrapped commands work in [appeal.1.0.break.audit.md](/home/larry/tron/src/appeal/appeal.1.0.break.audit.md:460).

Recommendation:

- Follow `__wrapped__` with cycle protection, or defer to real `inspect.signature()` when it is present.
- Preserve decorations registered against the outer wrapper while taking grammar from the unwrapped callable.
- Test conversions, options, defaults, methods, and multiple wrapper layers—not just the resulting parameter names.

### 4. Medium-high — Postponed and union annotations are unsupported

The signature reader consumes raw `func.__annotations__` in [frontend.py](/home/larry/tron/src/appeal/appeal/frontend.py:179). It does not resolve string annotations or forward references.

A command defined under:

```python
from __future__ import annotations

def command(n: int):
    ...
```

fails during plan construction with:

```text
ConfigurationError: parameter 'n': annotation 'int' isn't callable
```

The callable validation at [frontend.py](/home/larry/tron/src/appeal/appeal/frontend.py:806) also rejects `int | None`. That spelling is currently advertised as supported shorthand in [README.draft.md](/home/larry/tron/src/appeal/README.draft.md:27).

Recommendation:

- Resolve annotations using the appropriate version-compatible type-hint machinery when raw annotations require it.
- Define the intended semantics of `X | None`; implement them if the draft is authoritative, or remove the claim.
- Preserve `Annotated` metadata and Appeal’s custom generic converters during resolution.
- Add coverage for postponed annotations on functions, methods, classes, recursive converters, and decorated callables.

### 5. Medium — `README.draft.md` contains confirmed API inaccuracies

The draft is substantially closer to the live v2 design than the old documentation, but the following statements do not match the implementation:

- The constructor signature at [README.draft.md](/home/larry/tron/src/appeal/README.draft.md:1213) includes nonexistent `positional_argument_usage_format`, says `margin=79` instead of `None`, and presents `script='-'` as a fixed default. The live signature is at [appeal/__init__.py](/home/larry/tron/src/appeal/appeal/__init__.py:823).

- `stylesheet=` is repeatedly described as a plain data dictionary, including [README.draft.md](/home/larry/tron/src/appeal/README.draft.md:697). The implementation accepts `None`, `False`, or a complete composed `big.StyleSheet` at [presentation.py](/home/larry/tron/src/appeal/appeal/presentation.py:538). Passing an ordinary dict fails during help or error rendering.

- The example at [README.draft.md](/home/larry/tron/src/appeal/README.draft.md:751) treats `read_iterable()` as an argv parser. The implementation is row-oriented and rejects callables containing any keyword-only parameters at [load.py](/home/larry/tron/src/appeal/appeal/load.py:350).

- `app.schema()` is called JSON Schema at [README.draft.md](/home/larry/tron/src/appeal/README.draft.md:783). It returns Appeal’s JSON-safe program description at [appeal/__init__.py](/home/larry/tron/src/appeal/appeal/__init__.py:1793); the actual JSON Schema generator is the internal per-command MCP schema function.

- The draft says `appeal.file()` hands a file to the command “open and closed for you” at [README.draft.md](/home/larry/tron/src/appeal/README.draft.md:1324). A normal path-backed file remains open after the command returns; only the command can presently close it.

- The precommand reference omits the important `config=None` parameter at [README.draft.md](/home/larry/tron/src/appeal/README.draft.md:1247).

- As noted above, the introduction advertises `X | None`, which the implementation rejects.

Recommendation:

- Correct those API descriptions after the implementation decisions are settled.
- Change the documentation tests to exercise `README.draft.md`.
- Add direct tests for API signatures and examples that cannot be validated reliably by merely compiling code fences.

### 6. Medium — The REPL prints `Processor` objects rather than command results

[repl()](/home/larry/tron/src/appeal/appeal/__init__.py:2517) does:

```python
result = self.process(words)
...
print(result)
```

But `process()` returns a `Processor`, as defined in [appeal/__init__.py](/home/larry/tron/src/appeal/appeal/__init__.py:659). Therefore:

- A command returning `"VALUE"` prints `<Processor result='VALUE' ...>`.
- A command returning `None` still prints `<Processor result=None ...>`.
- The displayed execution count and repr leak an internal object into the user interface.

The REPL also catches data and configuration errors but not `CommandError` or general `AppealError`, so an intentional command failure terminates the session.

Recommendation:

- Print `processor.result` only when it is not `None`.
- Handle the public command-error taxonomy consistently with `main()`, while continuing the REPL.
- Strengthen tests to compare complete output rather than checking whether expected numbers or strings occur as substrings.

### 7. Medium — MCP schemas misdescribe nullary options

The MCP schema generator special-cases flags as booleans, but lets `kind == "nullary"` fall through to a string schema in [mcp.py](/home/larry/tron/src/appeal/appeal/mcp.py:224).

The structured-data reader does the opposite: [load.py](/home/larry/tron/src/appeal/appeal/load.py:101) reads nullary values as booleans. `True` invokes the zero-argument converter; `False` chooses the default.

Thus the generated schema tells MCP clients to send a string that `read_mapping()` rejects.

Recommendation:

- Emit `{"type": "boolean"}` for nullary options.
- Add a round-trip test that generates the schema and then feeds schema-conforming data through `read_mapping()`.

### 8. Medium — Completion does not share compact-short-option semantics with parsing

[_completion_scan()](/home/larry/tron/src/appeal/appeal/completion.py:200) reduces a short option token to `word[:2]` at line 236. It neither parses attached values nor walks bundled short options.

Confirmed divergences:

- The parser accepts `-n5`, but completion believes `-n` is still waiting for its value and suppresses positional candidates.
- The parser accepts `-ab` as two flags, but completion marks only `-a` as used and offers `-b` again.
- The separated spellings `-n 5` and `-a -b` behave correctly.

Recommendation:

- Share the compact-option tokenization logic with the real scanner, or extract a common non-throwing lexical helper.
- Test attached opargs, flag bundles, mixed bundles, unknown bundle members, and cursor positions inside partially typed compact options.

### 9. Medium — A boolean command result becomes a process exit status

After executing a command, [run_main()](/home/larry/tron/src/appeal/appeal/__init__.py:250) returns any `int`. Because `bool` is a subclass of `int`, `True` is returned as an exit status and becomes process status 1 under `sys.exit()`.

This conflicts with the backend’s explicitly documented rule in [backend.py](/home/larry/tron/src/appeal/appeal/backend.py:1138): only nonzero, non-boolean integers halt dispatch.

Recommendation:

- Apply the same non-boolean integer rule in `run_main()`.
- Pin the intended results for `None`, `False`, `True`, zero, nonzero integers, and arbitrary objects.

### 10. Medium — Coverage is high, but the CI guarantee and assertions are weaker than claimed

The local branch-aware run produced:

```text
4729 statements, 0 missed
2182 branches, 28 partial
99% combined branch coverage
```

Thus statement coverage is genuinely 100%, but branch coverage is not.

CI currently:

- Runs coverage without `--branch` at [coverage.yml](/home/larry/tron/src/appeal/.github/workflows/coverage.yml:22).
- Fails only below 68% at [coverage.yml](/home/larry/tron/src/appeal/.github/workflows/coverage.yml:43).
- Does not exercise the draft README.
- Uses substring assertions in places where exact behavior matters, which allowed the REPL processor-repr defect to pass.

Recommendation:

- Enable branch coverage in CI.
- Set an intentional threshold near the actual expected level.
- Test semantic outcomes and complete output, not merely executed lines.
- Make the draft documentation part of the normal suite.

### 11. Low — The source distribution contains repository debris

`flit build` succeeded, but the generated source archive included:

- The 277 KB [.bak_ta](/home/larry/tron/src/appeal/.bak_ta) backup.
- The executable debugging script [boom.py](/home/larry/tron/src/appeal/boom.py:1).
- The obsolete `x.py` prototype.
- Several historical review transcripts.

The resulting source archive was approximately 636 KB, compared with a clean wheel of approximately 148 KB.

Recommendation:

- Remove accidental tracked artifacts or exclude them from the sdist.
- Add a packaging test that inspects the wheel and sdist member lists.

## Conditional observation: sample directory

I am not counting this as a release blocker without knowing whether `sample/` is intended to represent v2.

If it is intended as current documentation, it is broken:

- [sample/regenerate.py](/home/larry/tron/src/appeal/sample/regenerate.py:28) calls nonexistent `Appeal.precompile()`.
- [sample/weather.py](/home/larry/tron/src/appeal/sample/weather.py:23) imports a generated module requiring nonexistent `appeal.runtime`.
- `sample/regenerate3.py` imports other deleted modules from the earlier compiled-parser architecture.

If the directory is intentionally historical, it should be labelled or relocated accordingly.

## Validation performed

- Reviewed the live Appeal `v2.fable5` worktree, not merely its commit history.
- Reviewed the live sibling Big `oh_fifteen` worktree, including modified and untracked files.
- Appeal’s canonical suite passed on Python 3.9–3.14.
- Python 3.6–3.8 could not compile the canonical suite because of the chained decorator.
- Python 3.6 and 3.7 additionally failed a real command-construction smoke test.
- Python 3.8 passed the command-construction smoke test.
- Python 3.14 `compileall` succeeded.
- Statement coverage reached 100%; branch coverage reached 99%.
- `flit build` succeeded.
- Big’s Markdown suite passed all 88 tests.
- Big’s full live suite had four failures, all in the untracked `big.cmdline` work. They appear unrelated to Appeal’s Markdown and stylesheet dependency.
- Appeal now correctly declares `big >= 0.15`; that previously known metadata issue is resolved.
- Appeal’s tracked worktree was clean after the review. No implementation changes were made.

## Recommended order for Claude Fable

1. Resolve the supported-Python decision and make the implementation and test suite agree with it.
2. Repair MCP error isolation, request validation, and version negotiation.
3. Restore wrapped-signature behavior.
4. Resolve postponed annotations and `X | None`.
5. Fix the REPL, nullary MCP schema, compact-option completion, and boolean exit behavior.
6. Correct and test `README.draft.md`.
7. Tighten coverage enforcement and clean the source distribution.
8. Decide whether `sample/` is current, historical, or removable.

Once Fable’s changes are present in the live directories, Codex will review those changes rather than implement them.
