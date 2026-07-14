# CLI-library feature survey

A feature-parity survey of five Python command-line argument-parsing
libraries, with Appeal added for comparison.  The scope is strictly
**command-line argument parsing and dispatch**; features unrelated to
that (color, prompts, pagers, signal handling, ...) are catalogued
separately under "Out of scope / don't want".

**Sources.** Every non-Appeal claim below was read from the library's
own source and docs (cloned under `~/tron/src`) or, for argparse, the
stdlib.  Versions read:

| library  | version read | notes |
|----------|--------------|-------|
| Click    | 8.5.0.dev (repo main) | docs are Markdown |
| argparse | stdlib 3.14 | |
| docopt   | 0.6.2 | single-file `docopt.py` |
| Fire     | 0.7.1 | |
| Typer    | 0.26.8 | vendors Click **8.3.1** in `typer/_click/` (not a normal dependency) |

Appeal's column reflects Appeal v2 as built (see the grammar doc and
README).

Columns are ordered Appeal-first, then Click, argparse, docopt, Fire,
Typer.


## Cross-library matrix

### 1. Declaration style

| Appeal | Click | argparse | docopt | Fire | Typer |
|---|---|---|---|---|---|
| function signature + annotations (decorators) | decorators on a function | imperative builder | docstring **is** the spec | introspects any object | function signature + annotations |

### 2. Options & arguments

| Feature | Appeal | Click | argparse | docopt | Fire | Typer |
|---|---|---|---|---|---|---|
| short/long | ✓ (auto-short = 1st letter) | ✓ | ✓ | ✓ | ✓ | ✓ |
| bundling `-xvf` | ✓ (+ `-fjoe`=`-f joe`) | ✓ | ✓ | ✓ | ✗ | ✓ |
| `--opt=val` & `--opt val` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| negatable `--no-foo` | **✗ (uses `--flag=false`)** | ✓ (`/`) | ✓ (3.9+) | ✗ | ✓ `--nofoo` | ✓ (auto) |
| counting `-vvv` | ✓ (`counter`/fold) | ✓ | ✓ | ✓ | ✗ | ✓ |
| multi-value | ✓ (`list[T]`/`tuple[…]`/MultiOption) | ✓ | ✓ | ✓ | ✓ | ✓ |
| defaults | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `--` end-of-opts | ✓ | ✓ | ✓ | ✓ | ✓‡ | ✓ |
| prefix/abbrev | **✗ (refuses)** | ✗ | ✓ (default on) | ✓ (long) | ✗ | ✗ |
| env-var fallback | **✗ (by doctrine)** | ✓ | ✗ | ✗ | ✗ | ✓ |

‡ Fire's `--` separates *Fire's own* meta-flags (`--help`/`-i`/`--trace`/…)
from the target's args, not "end of options" as elsewhere.

### 3. Types & conversion

| Appeal | Click | argparse | docopt | Fire | Typer |
|---|---|---|---|---|---|
| **any callable is a converter** (the core metaphor); leaves str/int/float/bool; `validate()`/`validate_range()`/`split()` for choices/ranges; converter groups + `tuple[…]`; err→`UsageError` exit 2 | rich ParamTypes (INT/FLOAT/BOOL/UUID/Choice/IntRange/FloatRange/Path/File/DateTime/Tuple) + custom `convert()`; err→`BadParameter` exit 2 | `type=` callable, `choices=`, `FileType`; err→exit 2 | **none** — strings/lists/int-counts/bools → flat dict | **none declared** — `ast.literal_eval` inference, bare word→str; no validation/choices | annotation-driven (via Click): int/float/bool/Path/File/datetime/UUID/**Enum**/**Literal**→choices; custom `parser=`/`callback=`; err→`BadParameter` |

### 4. Subcommands & dispatch

| Feature | Appeal | Click | argparse | docopt | Fire | Typer |
|---|---|---|---|---|---|---|
| nested subcommands | ✓ (nested sets) | ✓ | ✓ | words only (no dispatch) | ✓ (object tree) | ✓ `add_typer` |
| chaining | ✓ (cycling/`repeat`) | ✓ `chain=True` | ✗ | ✗ | ✓ (trailing calls) | ✓ (via Click) |
| global/per-cmd opts | ✓ (global + scoped) | ✓ Context/callback | ✓ parents | manual | n/a | ✓ callback |
| default command | ✓ | ✓ `invoke_without_command` | ✗ | manual | the object | limited |

### 5. Help & usage

| Appeal | Click | argparse | docopt | Fire | Typer |
|---|---|---|---|---|---|
| `-h/--help`; docstring-sourced; **`margin`/`indent`/template** knobs; `version=` → `--version` + `version` cmd (no `-v`) | `--help` **only** (not `-h`); docstring/`help=`; `\b` no-rewrap; `max_content_width`; `@version_option` | `-h/--help`; `help=`/`description=`; `formatter_class`, `max_help_position`; `action='version'` | the text you wrote; auto `-h/--help/--version` | docstrings+signature; `-- --help` (+ `--help` shortcut); **pages via `$PAGER`** | `--help`; docstring; **Rich** markup + `rich_help_panel` |

### 6. Shell completion

| Appeal | Click | argparse | docopt | Fire | Typer |
|---|---|---|---|---|---|
| bash/zsh/fish, dynamic (per-converter `.completions`), `_APPEAL_COMPLETE`; **refuses powershell** | bash/zsh/fish (8.0+), dynamic, `_PROG_COMPLETE` | **✗** (needs argcomplete) | ✗ | bash/fish (`-- --completion`) | bash/zsh/fish/**powershell**, dynamic + help-tuples, `--install/--show-completion` |

### 7. Value sources / config

| Appeal | Click | argparse | docopt | Fire | Typer |
|---|---|---|---|---|---|
| **config layering** (`defaults < config < args`, atomic per option) via a passed mapping; no env-var; no built-in file format | `envvar`, `auto_envvar_prefix`, `default_map` (you load the file — no format built in) | `fromfile_prefix_chars` (`@args.txt`); env manual | none (`[default:]` in doc) | none | `envvar`, `default_map` (via Click) |

### 8. Code generation / standalone

| Appeal | Click | argparse | docopt | Fire | Typer |
|---|---|---|---|---|---|
| **✓ emits a standalone, dependency-free script** (the north star) | ✗ | ✗ | ✗ | ✗ | ✗ |

None of the five competitors emits a standalone script.  Click's
"standalone-apps" doc is about PyInstaller/shiv bundling, not codegen.

### 9. Out of scope / "don't want"

| Appeal | Click | argparse | docopt | Fire | Typer |
|---|---|---|---|---|---|
| **none bundled** — only `run_main` touches the world (exit codes, ^C→130, error stream), and only in `main()` | color/prompt/progress/pager/edit/launch/file-mgmt/`standalone_mode` signal handling/CliRunner | `FileType` opens files; `error()`→exit 2 | **none** (text→dict) | REPL/`--interactive`, `--trace`, pager | all of Click + Rich + `--install-completion` writes shell rc + CliRunner |


## Per-library notes (source-grounded)

### Click 8.5.0.dev
- **Declaration:** decorators (`decorators.py`): `@command`/`@group`/`@option`/`@argument`; params populate the function's kwargs.  `@group` is `@command` with `cls=Group`.
- **Parsing** (`parser.py`): bundling via `_match_short_opt`; `--opt=value` split on `=`; `--` stops option parsing (`parser.py:333`).  **No abbreviation** — `_match_long_opt` does an exact dict lookup (`parser.py:363-367`); "Did you mean" is error-message-only via `difflib` (`exceptions.py:257`).  `-h` is **not** default; only `--help` (`core.py:456`).
- **Types** (`types.py`): STRING/INT/FLOAT/BOOL/UUID + `Choice` (enum-aware, `case_sensitive=`), `IntRange`/`FloatRange` (`clamp=`), `DateTime`, `File` (`atomic=`), `Path`, `Tuple`; custom via `ParamType.convert()`.  `fail()`→`BadParameter` (a `UsageError`, exit code 2).
- **Dispatch:** `Group` nesting; `chain=True`; `Context`/`pass_context`/`pass_obj`; `invoke_without_command`; `result_callback`.
- **Config:** `ParameterSource` = COMMANDLINE/ENVIRONMENT/DEFAULT/DEFAULT_MAP/PROMPT; `envvar`, `auto_envvar_prefix`, `default_map` (you populate it — Click reads no INI/TOML/YAML itself).
- **Completion** (`shell_completion.py`): bash/zsh/fish, landed in Click 8.0, `_<PROG>_COMPLETE=<shell>_source`; dynamic via `shell_complete=`.
- **Out of scope** (`termui.py`/`utils.py`): `echo`/`secho`/`style`, `prompt`/`confirm`/`getchar`/`pause`, `progressbar`, `echo_via_pager`, `edit`, `launch`, `clear`/`get_terminal_size`, `LazyFile`/`open_file(atomic=)`/`get_app_dir`; **`standalone_mode`** in `Command.main` catches exceptions and converts KeyboardInterrupt→Abort→`sys.exit(1)`; `CliRunner` testing.

### argparse (stdlib 3.14)
- **Declaration:** imperative — `ArgumentParser()` then `add_argument()` one at a time.
- **Options:** actions `store_true/false`, `store_const`, `append`, `count` (`-vvv`), `extend` (3.8+), `help`, `version`; `BooleanOptionalAction` auto `--foo/--no-foo` (3.9+, `argparse.py:926`); short bundling; `--opt=value`/`-xX`.  **Unambiguous long-option prefix abbreviation is ON by default** (`allow_abbrev=False` to disable).
- **Types:** `type=` any callable; `choices=` (error lists them); `FileType`.  No ranges/enums built in.
- **Dispatch:** `add_subparsers()`/`add_parser()` with per-subcommand args, `aliases=`, and `deprecated=` (3.13+); `parents=` for shared options.
- **Help:** auto `-h/--help` (`add_help=True`); `action='version'`; `metavar=`; `formatter_class` (Raw*/ArgumentDefaults), `max_help_position`.
- **Config:** `fromfile_prefix_chars='@'` reads args from a file; no env fallback; no completion (needs third-party `argcomplete`).
- **Out of scope:** minimal — `FileType` opens files; `error()`→stderr + `sys.exit(2)`.  No colors/prompts/pagers/signals.

### docopt 0.6.2
- **Declaration:** the docstring **is** the spec.  `docopt(doc, argv=None, help=True, version=None, options_first=False)` parses the `Usage:` and `Options:` sections of your help text (`docopt.py:490`, `parse_section` `:464`).
- **Notation-classified elements** (`parse_atom` `:402`): `<arg>`/`ARG` = positional; plain word = command; `-o`/`--opt` = option.  Supports `[options]` shortcut, `[--]` separator, `[-]` stdin convention, mutual exclusion `a|b`, repetition `...`, optional `[ ]`, required `( )`, bundling `-oiv`, `--opt=value`/`--opt value`, `[default: value]` markers, **unambiguous long-option prefix matching** (`:307-311`).
- **Types:** **none** — values are string / bool / int-count / list; returns a flat dict.
- **Dispatch:** none built in — commands are just booleans in the dict; you branch yourself.  `options_first=True` helps git-style layouts.
- **Help:** auto `-h/--help` (prints the doc) and `--version` (`extras` `:476`) when present in your patterns.
- **Out of scope:** **none.**  Imports only `sys`+`re`; text in → dict out, or `SystemExit` with usage.  The cleanest scope — but also does the least (no conversion, no dispatch, no generated help).

### Fire 0.7.1
- **Declaration:** none — `fire.Fire(component)` introspects any function/class/dict/module/object (`core.py`).  The run loop consumes args in order: attribute access, call, or instantiate, each result becoming the next component (`core.py:430-566`).
- **Options:** `--name value` / `--name=value`; boolean `--name` / `--noname`; single-letter `-x` = unique-prefix match; positionals from the signature; required-ness from the signature (no default = required).
- **`--` separator:** `SeparateFlagArgs` (`parser.py:39`) splits on the last isolated `--`; everything after it is Fire's *own* flags.  A second, different separator (`--separator`, default `-`) delimits chained calls.
- **Types:** `DefaultParseValue` (`parser.py:59`) = `ast.literal_eval`-style; numbers/bools/None/lists/dicts/tuples parse as literals, bare words become strings, `BinOp` (`1+2`) rejected → string.  No validation, no choices.
- **Dispatch:** chaining by trailing calls on returned objects; members of a class/dict/module are subcommands.
- **Help:** from docstrings + signatures (`helptext.py`); `command -- --help` (plus a bare `--help` shortcut); **help/trace are paged through `$PAGER`/`less`** (`console/console_io.py:68`).
- **Completion:** bash + fish via `-- --completion` (`completion.py`).
- **Out of scope:** `-i`/`--interactive` REPL (IPython if installed, else stdlib `code`), `--trace`, `--verbose`, the pager, `--separator`.

### Typer 0.26.8 (vendors Click 8.3.1)
- **Declaration:** function params + type annotations; `typer.Argument()`/`typer.Option()`, in either `Annotated[...]` or default-value style.  Annotation→Click-type dispatch in `main.py:1520-1618`.
- **Options:** `*param_decls` for short/long; **auto `--name/--no-name` for bools** (`main.py:1697`); `count=True`; `List[...]`→`multiple`; required from `...`/no-default; `envvar=`.  Bundling etc. inherited from vendored Click's parser.
- **Types:** int/float (+ `IntRange`/`FloatRange`), bool, UUID, datetime, `Path` (`exists=`/…), File variants, **Enum→choices** (`case_sensitive`), `Literal`→choices; custom via `parser=`/`callback=`.  Errors → vendored Click `BadParameter`.
- **Dispatch:** `add_typer()` nesting (no depth limit); `@app.callback()` group options; `chain` supported (via Click).
- **Completion:** bash/zsh/fish/powershell; `--install-completion`/`--show-completion`; dynamic `autocompletion=`; completion items carry help as `(value, help)` tuples.
- **Out of scope:** all of vendored Click, plus Rich pretty-tracebacks + rich-markup help/panels; `typer.echo/style/prompt/confirm/progressbar/launch`; **`--install-completion` writes to the user's `~/.bashrc`/`~/.zshrc`/fish/PowerShell profile** (`_completion_shared.py:101-199`); `testing.CliRunner`.


## Where Appeal stands (parity read)

**Core parity: yes.**  Every parsing feature that argparse/Click/Typer
share — short/long, bundling, `=`/space, counting, negatable flags,
multi-value, defaults, choices/enums, custom converters, nested
subcommands, `--`, env-var fallback — Appeal covers, save the two
deliberate divergences below.

**Two deliberate divergences (both defensible):**
- **Negatable flags.**  Appeal uses `--flag=false`, not `--no-flag`
  (ruled: no alternate spellings).  Click/argparse/Typer/Fire all offer
  `--no-`/`--nofoo`.  Worth a doc note since users may expect it.
- **Prefix/abbreviation matching.**  Appeal refuses it — aligned with
  Click, Fire, and Typer; argparse (default-on) and docopt accept it.
  Worth a doc note that it's a choice, for argparse migrants.

**Where Appeal is unique or in the minimal camp:**
- **Standalone emission** — no competitor emits a dependency-free
  script.  Unique.
- **Config layering** — `defaults < config < args`, atomic per option,
  as a first-class passed mapping.  Click makes you populate
  `default_map` by hand; nobody else has it.
- **Help-formatting knobs** — `margin`/`indent`/templates, more
  first-class than Click's `max_content_width`/`\b`.
- **Empty "don't want" column** — Appeal bundles none of the
  color/prompt/pager/REPL/signal machinery; it keeps company with
  argparse and docopt, against Click/Typer/Fire.

**Possible gaps to weigh (not necessarily to close):**
- Command **chaining** semantics: confirm Appeal's cycling/`repeat`
  covers the "several commands in one line" use Click's `chain=True`
  targets.
- **PowerShell** completion: Appeal refuses it; Typer supports it.  A
  yes/no call, not a bug.
