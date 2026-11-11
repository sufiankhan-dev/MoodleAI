# AGENTS.md — MoodleAI working agreement

This file is the contract for every change made in this repository, by a human or
an AI agent. Read it at the start of a session. If a request conflicts with a rule
here, say so and ask before proceeding.

**Stack:** Python 3.11+ (verified 3.14.3) · FastAPI · Playwright · local Ollama
(`qwen3:8b`) · SQLite. Dashboard today is vanilla HTML/CSS/JS with no Node and no
build step; it is being migrated to **Next.js + shadcn/ui** (§6). **No cloud AI,
no API keys, ever.**

---

## 1. Non-negotiable invariants

These define the product. Never weaken them without an explicit, written request
from the owner — and even then, stop and confirm.

| Invariant | Enforced in | Means |
| --- | --- | --- |
| **Loopback only** | `app/config.py` (`load_settings`), `app/api.py` (`local_only`) | `APP_HOST` stays `127.0.0.1`/`localhost`/`::1`. Host and `Origin` headers are checked on every request. |
| **Local model only** | `app/config.py` | `OLLAMA_HOST` must resolve to loopback. No cloud inference, ever. |
| **No password handling** | `browser/session.py`, UI | Login is manual in visible Chrome. Never ask for, read, type, log, or store Moodle credentials. |
| **Read-only Moodle** | `app/security.py`, `browser/moodle.py`, `tools/` | No submit, upload, delete, grade, profile, or enrolment actions. Dangerous paths stay blocked even if the model asks. |
| **Path jail** | `app/security.py` (`PathJail`), `tools/` | All writes resolve under `workspace/` or `generated/`. Reject traversal, absolute paths, symlinks. |
| **Approval gate** | `app/state.py`, `tools/base.py` (`ToolContext.require_approval`) | Overwrites and refreshes pause for a click. Timeout = deny. Stop = deny pending. |
| **No shell, no execution** | `tools/` | Generated code is written and never run. No `subprocess`, `os.system`, `eval`, `exec`. |
| **Untrusted content** | `agent/prompts.py` | Page/PDF/file text is labelled data, never instructions. |
| **CSRF header** | `app/api.py` (`local_only`) | Every POST/PUT/PATCH/DELETE needs `X-MoodleAI: 1`. |
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
app/ui/{index.html,styles.css,app.js}   # the entire dashboard today
workspace/, generated/  # user content (gitignored)
tests/                  # unit, API, browser, end-to-end
```

Data flow: `Chrome ← Playwright ← BrowserSession ← Moodle parsers → ToolRegistry →
MoodleAgent loop → events/approvals → FastAPI + SSE → dashboard → SQLite + workspace`.

---

## 3. Commands

```powershell
# run
python main.py

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

There is no linter, formatter, or type-checker configured. **Do not invent one or
add one without asking.** Match the existing style instead (§5).

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
  The only standing exception is the Node packages approved for the migration
  in §6; everything else needs a yes first.
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
   `X-MoodleAI: 1` header — the middleware already enforces it, so the UI must send it.
3. Return JSON-serializable dicts; errors via `HTTPException`.
4. Keep `connect-src 'self'` in mind: the browser only talks to the same origin.
5. Add an API test; update the README if it is user-facing.

**UI (current, until the §6 migration lands)**

- `app/ui/index.html` + `styles.css` + `app.js`, loaded as static files by
  `app/api.py`. No framework, no bundler.
- All state-changing fetches include `X-MoodleAI: 1`; live updates use
  `EventSource("/api/events")`.
- Respect the CSP header set in `app/api.py`: no inline `<script>`, no CDN links.
- `tests/test_api.py` asserts the three UI files are served (`UI_NAMES`) — keep
  them present and updated when the dashboard changes.

---

## 6. Decided: UI redesign with Next.js + shadcn/ui

**Owner decision (2026-11-12): Option A — Next.js (App Router) + shadcn/ui +
Tailwind CSS.** The current `app/ui/` dashboard is compact and will be replaced by
a real **dashboard layout**: sidebar + top bar + padded content grid, cards,
data tables, sheets/dialogs for approvals, toasts for agent events, responsive
breakpoints.

Scope of the migration: **the front end only.** `main.py`, `app/api.py`,
`app/state.py`, `app/config.py`, `app/security.py`, `agent/`, `browser/`,
`tools/`, and `database/` are not rewritten. FastAPI stays the single backend.

**Target shape**

```
web/                        # Next.js app (repo root, sibling of app/)
  app/                      # App Router routes: (dashboard)/…
  components/ui/            # shadcn components (generated, committed)
  lib/api.ts                # the only place fetch/EventSource are constructed
  next.config.ts            # rewrites: /api/* and /api/events → http://127.0.0.1:8765
  README note               # how to run both processes
```

**Non-negotiables for the migration**

1. **Same-origin proxy.** The browser talks only to the Next dev/prod server
   (`127.0.0.1:3000`); `next.config` rewrites `/api/:path*` to
   `http://127.0.0.1:8765/api/:path*`. Do not add permissive CORS and do not
   widen FastAPI's `connect-src 'self'` — the proxy is what keeps §1 intact.
2. **Loopback bind.** Run with `next dev -H 127.0.0.1` (and the same for
   `next start`). Never `0.0.0.0`. The README promise still holds.
3. **`X-MoodleAI: 1` on every mutation.** One `apiFetch()` in `lib/api.ts` sets
   it for POST/PUT/PATCH/DELETE. No component calls `fetch` directly.
4. **SSE must stream.** `/api/events` goes through the proxy with buffering and
   compression disabled on that route; the client reconnects with backoff and
   re-reads `/api/state` after a reconnect.
5. **No secrets in the client.** No tokens, no Moodle URLs with query strings.
6. **Approvals keep their semantics.** Timeout denies, Stop denies pending, the
   dialog is blocking. The UI never auto-approves.
7. **Keep the dashboard useful without it.** FastAPI keeps serving `/` until the
   Next dashboard reaches parity, so `python main.py` never lands on a dead page.
8. **UI-asset tests move in the same change.** `tests/test_api.py` (`UI_NAMES`,
   `/styles.css`, `/app.js`) is updated when `app/ui/` is removed — no test is
   deleted before its replacement exists.
9. **README updates in the same change.** Architecture diagram, the UI row, the
   run instructions (now two processes), and the test counts.

**Dependencies:** adding Node packages is expected for this work and is approved
*for this migration only*. Pin versions, commit `package-lock.json`, and say what
was installed. Python dependencies stay untouched.

**Not approved by this decision:** SSR/data-fetching from FastAPI, auth,
telemetry, a second API layer, moving business logic into Next route handlers, or
anything in §1.

---

## 7. Definition of done

A change is done when:

- [ ] It does what was asked, and nothing else changed.
- [ ] `python -m pytest tests -q` passes.
- [ ] No invariant from §1 was weakened.
- [ ] No user data or secret was touched.
- [ ] `README.md` reflects any user-visible change.
- [ ] The owner has been told exactly what was verified and what was not.
