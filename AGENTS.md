# AGENTS.md — Owlet working agreement

This file is the contract for every change made in this repository, by a human or
an AI agent. Read it at the start of a session. If a request conflicts with a rule
here, say so and ask before proceeding.

**Stack:** Python 3.11+ (verified 3.14.3) · FastAPI · Playwright · local Ollama
(`qwen3:8b`) · SQLite. Dashboard is vanilla HTML/CSS/JS served by FastAPI: no
Node, no build step (§6). **No cloud AI, no API keys, ever.**

---

## 1. Non-negotiable invariants

These define the product. Never weaken them without an explicit, written request
from the owner — and even then, stop and confirm.

| Invariant | Enforced in | Means |
| --- | --- | --- |
| **Loopback only** | `app/config.py` (`load_settings`), `app/api.py` (`local_only`) | `APP_HOST` stays `127.0.0.1`/`localhost`/`::1`. Host and `Origin` headers are checked on every request. |
| **Local model only** | `app/config.py` | `OLLAMA_HOST` must resolve to loopback. No cloud inference, ever. |
| **No password handling** | `browser/session.py`, `app/ui/` | Moodle *account* login is manual in visible Chrome; the app never asks for, reads, types, logs, or stores it. The single exception is the optional **network** (HTTP Basic auth) pair in the collapsed "Network login" panel: it goes straight to Playwright's context at launch, lives in memory only, is cleared on disconnect, and is never logged, stored, or put in the database. |
| **Read-only Moodle** | `app/security.py`, `browser/moodle.py`, `tools/` | No submit, upload, delete, grade, profile, or enrolment actions. Dangerous paths stay blocked even if the model asks. |
| **Path jail** | `app/security.py` (`PathJail`), `tools/` | All writes resolve under `workspace/` or `generated/`. Reject traversal, absolute paths, symlinks. |
| **Approval gate** | `app/state.py`, `tools/base.py` (`ToolContext.require_approval`) | Overwrites and refreshes pause for a click. Timeout = deny. Stop = deny pending. |
| **No shell, no execution** | `tools/` | Generated code is written and never run. No `subprocess`, `os.system`, `eval`, `exec`. |
| **Untrusted content** | `agent/prompts.py` | Page/PDF/file text is labelled data, never instructions. |
| **CSRF header** | `app/api.py` (`local_only`) | Every POST/PUT/PATCH/DELETE needs `X-Owlet: 1`. |
| **No secret leakage** | `app/events.py`, `app/logging_setup.py` | Redact cookies, tokens, session keys; strip URL query strings in UI and logs. |

`BROWSER_HEADLESS` also stays `false` — the owner must be able to watch what runs.

---

## 2. Map of the codebase

```
main.py                 # uvicorn entry point; python main.py → http://127.0.0.1:8765
app/
  api.py                # FastAPI app, ~26 routes, SSE at /api/events, local_only middleware
  state.py              # AppState: run lifecycle, approvals, snapshots, reset
  config.py             # frozen Settings dataclass, validated from env; ensure_directories()
  security.py           # URL policy, PathJail, filename sanitization, truncate_text
  events.py             # in-process pub/sub feeding SSE
  logging_setup.py      # rotating file + console logs, redaction
agent/
  llm.py                # Ollama client: health, streaming text, streaming tool calls
  loop.py               # MoodleAgent: bounded tool-calling loop, Stop handling
  prompts.py            # system prompt, untrusted-content rules
browser/
  session.py            # visible persistent Chrome via Playwright, login detection
  moodle.py             # DOM snapshot + Moodle parsers (courses, assignments)
tools/
  base.py               # ToolSpec / ToolRegistry / ToolContext / ToolError / StrictArgs
  *.py                  # the 14 registered tools + workspace/document helpers
database/db.py          # SQLite repository (courses, assignments, conversations, messages,
                        # activity, approvals)
config/app_state.json   # saved Moodle URL only
app/ui/                 # the dashboard: index.html, styles.css, app.js
workspace/, generated/  # user content (gitignored)
tests/                  # unit, API, browser, end-to-end
```

Data flow: `Chrome ← Playwright ← BrowserSession ← Moodle parsers → ToolRegistry →
MoodleAgent loop → events/approvals → FastAPI + SSE → app/ui dashboard →
SQLite + workspace`.

---

## 3. Commands

```powershell
# run (one process; the dashboard is served by FastAPI)
python main.py                 # app + dashboard, 127.0.0.1:8765

# full fast suite (no browser, no model)
$env:PYTHONIOENCODING="utf-8"
python -m pytest tests -q

# real Chrome tests (opens a browser window)
$env:RUN_BROWSER_TESTS="1"; python -m pytest tests -q

# real Ollama tests (needs qwen3:8b pulled)
$env:RUN_OLLAMA_TESTS="1"; python -m pytest tests/test_ollama.py -q

# single file / single test
python -m pytest tests/test_api.py -q
python -m pytest tests/test_api.py::test_name -q
```

There is no Python linter, formatter, or type-checker configured. **Do not invent
one or add one without asking.** Match the existing style instead (§5). The
dashboard is plain CSS/JS with no type-checking or build step.

---

## 4. Change protocol

**Before editing**

1. Read the file(s) you are about to change. Never edit from memory.
2. Check `git status` — treat any unexpected modification as the owner's work.
   Investigate before overwriting or deleting.
3. Restate the plan in one or two lines if the change touches more than one module.

**While editing**

- Smallest diff that solves the problem. No drive-by refactors, renames, or
  reformatting of untouched code.
- Preserve existing behaviour unless the request is to change it.
- Follow surrounding patterns over personal preference.

**After editing**

- Run `python -m pytest tests -q`. The suite must stay green; a change that
  breaks a test is not done. Do not delete or `skip` a test to make it pass —
  either fix the code or explain to the owner why the test's expectation changed.
- Update `README.md` when user-visible behaviour, config, tool set, routes, or
  test counts change. README is the product doc.
- Report what changed, what you verified, and what you did not verify.

**Ask before doing any of these**

- Adding a dependency (Python or Node) or changing `requirements.txt` versions.
  The dashboard has no Node dependencies any more (§6), so every new package
  needs a yes first.
- Changing `app/api.py` security middleware, `app/config.py` validation, or
  anything in `app/security.py`.
- Adding, removing, or re-flagging a tool in the registry (changes model behaviour).
- Changing the SQLite schema, or writing a migration.
- Deleting files or directories, or running anything destructive.
- Touching `workspace/`, `generated/`, `browser/profile/`, `database/*.db*`,
  `config/app_state.json`, `.env`, or `logs/` — these are user data.
- Any network call beyond the local Ollama and the user's Moodle host.

**Never do**

- Commit, amend, rebase, push, or force-push unless asked. `main` is the branch.
- Add secrets, tokens, or credentials to any file or log.
- Add `subprocess`, `os.system`, `eval`, `exec`, or dynamic imports of user input.
- Widen the path jail, the URL allowlist, or the host check "temporarily".
- Add a cloud LLM fallback, telemetry, or analytics.
- Present unverified work as verified.

**Commit style** (only when asked): `feat:`, `fix:`, `docs:`, `test:`, `refactor:`
lowercase, imperative, no trailing period — see `git log`.

---

## 5. Code conventions

**Python**

- `from __future__ import annotations` at the top of every module.
- Module docstring, then stdlib / third-party / local imports (blank-line separated).
- Full type hints; `str | None` style (PEP 604), not `Optional`.
- `@dataclass` for data; `@dataclass(frozen=True)` for settings and specs.
- `pathlib.Path` for all filesystem work. No string concatenation of paths.
- `logging.getLogger(__name__)`; no `print()` in application code.
- Async for anything touching the browser, model, or API.
- Raise the existing typed errors (`ToolError`, `BrowserError`, `SecurityError`,
  `LLMError`); don't invent parallel ones.
- Exception messages are user-facing text that may reach the model — make them
  actionable and free of secrets.

**Adding a tool** (the 14 are fixed until the owner asks otherwise)

1. Subclass `StrictArgs` for arguments; validate with Pydantic.
2. Handler signature `async def handler(ctx: ToolContext, args: X) -> dict[str, Any]`
   returning JSON-serializable data.
3. `ctx.check_cancel()` inside long loops.
4. `await ctx.require_approval(...)` for anything that overwrites or writes.
5. Register a `ToolSpec` with `read_only` / `requires_approval` set honestly.
6. Add a test; update the README tool table.

**Adding an API route**

1. Read routes from `AppState`; no business logic in `api.py`.
2. State-changing routes are POST/PUT/PATCH/DELETE and therefore require the
   `X-Owlet: 1` header — the middleware already enforces it, so the UI must send it.
3. Return JSON-serializable dicts; errors via `HTTPException`.
4. Keep `connect-src 'self'` in mind: the browser only talks to the same origin.
5. Add an API test; update the README if it is user-facing.

**UI (vanilla HTML/CSS/JS, in `app/ui/`)**

- Three files only: `index.html`, `styles.css`, `app.js`. No Node, no bundler, no
  framework. FastAPI serves them from `/`, `/styles.css`, `/app.js`.
- The visual language is the shadcn/ui token set (§6): oklch neutrals, a
  `0.65rem` radius, hairline borders, flat cards, small controls, light/dark theme
  driven by `data-theme` on `<html>`. Stay inside those tokens; no new colour
  literals and no external fonts or CDNs (`default-src 'self'`).
- `app.js` is the only place `fetch` and `EventSource` are constructed. Its `api()`
  helper adds `X-Owlet: 1` to every POST/PUT/PATCH/DELETE.
- Live updates use `EventSource("/api/events")`; a reconnect re-reads `/api/state`.
- Model output is untrusted: Markdown is escaped before the allow-listed formatter
  runs, and links are limited to `http(s)`.
- The dashboard asks for **no credentials** — no password field anywhere. Login
  happens in visible Chrome (§1).
- `app/ui/` is copied into `tmp_path` by `tests/test_api.py`, which asserts the
  three assets, their security headers, and the absence of credential inputs.

---

## 6. Decided: dashboard stays vanilla, styled like the shadcn app

**Owner decision (2026-11-12, same day): reverse the Next.js migration.** Option A
(Next.js + shadcn/ui + Tailwind in `web/`) was built, then rejected: the value was
in the *look*, not the toolchain, and a second runtime plus a build step was not
worth it for a single-page dashboard. `web/` was deleted; `app/ui/` is the UI, and
`python main.py` serves app and dashboard on `127.0.0.1:8765` in one process.

**What "like the Next app" means here:** the design tokens and component
treatment, not the framework. Concretely:

- Tokens copied from the former `web/app/globals.css`: `--background`,
  `--foreground`, `--card`, `--primary`, `--muted`, `--muted-foreground`,
  `--accent`, `--destructive`, `--border`, `--input`, `--ring`, `--sidebar*`, in
  oklch, with light, `prefers-color-scheme: dark`, and `[data-theme="dark"]` sets.
- Component feel: shadcn card (flat, hairline border, `--radius`), button variants
  (`btn-primary` = near-black `--primary`, `btn-ghost`, `btn-danger` =
  `--destructive`, `btn-icon`), badge-shaped pills, segmented tabs, muted table-ish
  list rows, dialog-shaped approval modal, sonner-style bottom-right toast.
- No framework, no Tailwind, no JSX, no `web/`, no `package.json`. The class names
  are the contract between `app.js` and `styles.css`.

**Non-negotiables**

1. **One process, same origin.** FastAPI serves `/`, `/styles.css`, `/app.js`.
   No dev server, no proxy, no CORS, no `DASHBOARD_URL`.
2. **`X-Owlet: 1` on every mutation**, set in one place in `app.js`.
3. **SSE streams directly** from `/api/events`; reconnect re-reads `/api/state`.
4. **No secrets in the client.** No tokens, no Moodle URLs with query strings.
5. **Approvals keep their semantics.** Timeout denies, Stop denies pending, the
   dialog is blocking. The UI never auto-approves.
6. **`python main.py` lands on the real dashboard**, not a placeholder page.
7. **`tests/test_api.py` asserts the three assets** and their security headers,
   and that the only password field is the transient network-login one.
8. **README updated in the same change** (run instructions, UI row, tree, counts).

**Not approved:** reintroducing Node/Next/Tailwind, SSR, a second API layer,
moving logic into the client, or anything in §1.

**Status (2026-11-12): reverted and restyled; `web/` deleted.** `app/ui/` is
restored from the pre-migration commit. `styles.css` was rewritten on the shadcn
token set; `app.js` keeps its structure. `app/api.py` serves the three assets
again, `app/config.py` has `ui_dir` back, and `tests/test_api.py` asserts them
(81 passed, 9 skipped). Tool-call rows were reworked into a two-column grid
(state icon, then tool name) aligned to the assistant bubble edge, with the
result text on a second line in the same column.

The collapsed **Network login** panel is part of this UI on purpose:
`browser/session.py` raises an explicit error telling the user to open it when a
campus fronts Moodle with HTTP Basic auth, so the panel cannot be removed without
breaking that recovery path. It is the only credential field in the app; see the
§1 row for the guarantees.

Verified in a browser at 127.0.0.1:8765: page loads with no console errors, every
CSS rule parsed, both themes resolve, sidebar/card geometry correct, tool rows
flush with the assistant bubble edge (both `x=367`), 6px gaps between consecutive
rows, and the network-login panel closed → hidden, open → 34px fields in two equal
columns. Not verified: a live model run producing real tool rows, and a real HTTP
Basic auth connect (that opens Chrome against Moodle).

Run it with:

```powershell
python main.py          # app + dashboard, 127.0.0.1:8765
```

---

## 7. Definition of done

A change is done when:

- [ ] It does what was asked, and nothing else changed.
- [ ] `python -m pytest tests -q` passes.
- [ ] No invariant from §1 was weakened.
- [ ] No user data or secret was touched.
- [ ] `README.md` reflects any user-visible change.
- [ ] The owner has been told exactly what was verified and what was not.
