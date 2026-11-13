# MoodleAI — a local Moodle study assistant

MoodleAI is a Windows desktop-style study assistant that drives a **visible Chrome
window** through your Moodle courses with Playwright, reads assignments and
attachments, explains the work, and generates study files. The language model is
**`qwen3:8b` running locally in Ollama**. There is no cloud AI, no remote server,
and no Moodle password storage.

> Everything binds to `127.0.0.1`. The app never submits assignments, uploads
> files, changes grades/profile/enrolment, or deletes Moodle content. Those
> actions stay with you.

---

## Quick start

### Requirements

- Windows 10/11 (verified on Windows 11 build 26200)
- Python 3.11+ (verified on 3.14.3)
- [Ollama](https://ollama.com/) running locally (verified on 0.40.15)
- Google Chrome installed (verified on 156)
- ~8 GB free RAM for `qwen3:8b` (31.7 GB available on the test machine)

### Install

```powershell
cd C:\Users\DELL\Desktop\Agent\MoodleAI

# 1. Python dependencies
python -m pip install -r requirements.txt

# 2. Local model (one time, ~5.2 GB)
ollama pull qwen3:8b

# 3. Optional configuration
Copy-Item .env.example .env
```

The defaults work without editing `.env`. Keep `APP_HOST=127.0.0.1` and
`BROWSER_HEADLESS=false`.

### Run

```powershell
python main.py
```

This starts the API **and** the dashboard on **http://127.0.0.1:8765**. There is
no second process and no Node build step: the dashboard is three static files
(`app/ui/index.html`, `app/ui/styles.css`, `app/ui/app.js`) served by FastAPI
itself, so `connect-src 'self'` and the CSRF header keep working unchanged.

The dashboard's look (oklch neutrals, `0.65rem` radius, card/button/badge
treatment) follows the shadcn/ui token set, so it reads like the previous
Next.js dashboard while shaving a whole toolchain off the install.

1. In **Moodle connection**, the URL is already filled with
   `https://lms.dsu.edu.pk/` (change it if you use another Moodle).
2. If your campus puts Moodle behind an HTTP Basic auth popup, open
   **Network login (only if Chrome asks)** in that panel and enter the network
   username and password. They go straight to Chrome for that one site, stay in
   memory, and are cleared when you disconnect. Without them, Chrome cannot open
   the page at all and the connect call says so explicitly.
3. Click **Connect**. A visible Chrome window opens with the persistent profile in
   `browser/profile`.
4. Log in manually (including SSO/MFA). MoodleAI never sees, types, or stores your
   Moodle account password.
5. That's it. The dashboard polls Chrome every few seconds (or click **Check
   login**), and the first check that sees you logged in starts a **sync** on its
   own. It fetches everything: every course, each assignment's status and due
   date, each assignment's instructions, and the files the teacher attached. It
   all lands in one folder per course under `workspace/`.
6. Ask in the chat. The suggestion chips under the welcome text are built from
   your synced data (“What should I do first?”, “Explain *your most urgent
   assignment*”, “Plan …”, “Summarise *a course*”), or type your own, for example:
   - “Download the assignment PDF and summarize the requirements”
   - “Create a Word solution, a PDF summary, and a Python file”

The dashboard shows saved courses, pending assignments (submitted and graded work
is folded under a “submitted or no status” row), the agent's activity, your
course folders, and any action that needs your approval. Every card — Moodle
connection, Courses, Pending assignments, Files, Activity — collapses from its
title; the choice is remembered in this browser. Activity starts collapsed, and
the connection card folds itself once you are logged in (until you open or close
it yourself).

### Assignment sync

The sync reads `mod/assign/index.php` for each of your courses through Chrome's
own session (same cookies, same network auth) and stores what Moodle reports:

- **Submission status** — `Not submitted`, `Draft`, `Submitted`,
  `Submitted for grading`, `Graded`, `Reopened`, or `No submission required`
  (Moodle's own “No submission” counts as not submitted). A blank cell is
  recorded as *status unknown* rather than guessed as pending; a phrase the app
  does not recognise (another theme or language pack) is shown verbatim.
- **Due date** — the exact instant comes from Moodle's calendar, the same
  deadline list the Timeline block shows (the read-only web service
  `core_calendar_get_action_events_by_timesort`, called from inside the logged-in
  page so the session key never leaves Chrome). A `<time datetime>` or
  `data-timestamp` on the index page is used too. Display text like “Sunday, 15
  November 2026, 11:59 PM” is shown but never parsed, so the badge is never hours
  off. If the calendar service is unavailable, the sync still stores status and
  due text and says “due-date alerts limited”.
- **Alert badge** — computed on the server: red **overdue** once the due instant
  has passed, red **urgent** within 24 h, amber **soon** within 48 h. Roles are
  sorted so overdue and urgent work floats to the top.

- **Course folders and files.** For each course it creates
  `workspace/<course>/`, named exactly like the Courses list. For each assignment
  it reads the assignment page in the background and saves
  `instructions/assignment.md`, `instructions/metadata.json` and
  `extracted/page.txt`. It then downloads the teacher's attachments from the
  assignment description into `attachments/`. Your own submitted files (the
  submission table) are never fetched. The download size limit
  (`MAX_DOWNLOAD_MB`) applies.
- **Attachment text.** Each teacher file (`.pptx`, `.docx`, `.pdf`, or a text/code
  file) is read into `extracted/<filename>.txt`, and the same text is added to
  `instructions/assignment.md` under *Attachment content*, above the raw page
  text. That way the instructions show what the slides or brief actually ask, not
  just the file name. PowerPoint is read straight from the `.pptx` zip with the
  standard library (slides in order, plus speaker notes). Each file is added once,
  marked so it is never duplicated, and folders saved by an older version get
  their attachment text on the next sync. Legacy `.ppt`/`.doc` and scanned PDFs
  with no text layer are listed in the sync notes instead.
  `extracted/page.txt` stays the Moodle page text only.

Pages are read with in-page `fetch` through Chrome's own session, so the visible
tab is never navigated. Files are only ever **created**: an existing file is never
rewritten, so a repeat sync downloads nothing it already has. The one exception is
`assignment.md`, which gets an attachment's text added once, in its marked section. Assignments that are
still pending are re-checked on each sync for files a teacher added later.

Press **Sync** in the Moodle connection panel to re-read everything at any time;
the hint under the buttons shows progress (the panel header says “syncing…”
while it runs). Courses that could not be read, and files that were skipped, are
listed in the sync summary. While a sync runs, chat messages and `/reset` wait
for it to finish, because both would disturb the same Chrome tab. Each sync
overwrites the stored status, so a resubmitted assignment stops looking pending.
There is no background timer: sync runs on login and when you ask.

### Assignment drafts

Every assignment row has a **Draft** button. It opens a dialog with the draft's
files and their review state:

1. **Draft with AI** starts an ordinary agent run in *draft mode*, shown in the
   chat as `Draft: <assignment>`. The assignment is pre-opened, so the model reads
   the saved `instructions/` and `attachments/` (or opens the Moodle page if
   nothing was synced) and writes each deliverable into that assignment's folder:
   - written work → `documents/*.docx`, or `pdfs/*.pdf` if the brief asks for PDF
   - programming work → `code/*` (never run)
   - online-text assignments → `code/online_text.md`, the exact text to paste
   - quizzes, forums, or anything that isn't a file → `code/notes.md` saying what to do
2. When the run ends, the model's final checklist (deliverables, each requirement
   mapped to a file or MISSING, assumptions) is saved as `explanations/REVIEW.md`.
3. **Mark reviewed** records the SHA-256 of every draft file in
   `<assignment>/review.json`. Editing, adding, or deleting a draft file afterwards
   shows the draft as **Changed since review** until you review it again.

Draft files are everything under `code/`, `documents/`, `pdfs/` and
`submission/`; teacher files and the app's notes are never counted. Overwrites
still go through the approval dialog. **Nothing is submitted:** upload the
reviewed files to Moodle yourself. The drafts come from a local 8B model, so treat
them as a starting point, check your course's policy on AI-assisted work, and
expect to fix things.

Routes: `GET /api/assignments/{id}/draft` (files + status),
`POST /api/assignments/{id}/draft` (start), `POST /api/assignments/{id}/review`
(record hashes). The two POSTs need `X-MoodleAI: 1` like every mutation.

---

## What it can do

| Area | Capability |
| --- | --- |
| Moodle reading | Courses, course pages, assignments, due text, submission status, visible page text and links |
| Automatic sync | On login (and on demand) reads every course and assignment, stores submission status, due date, and a due-soon/overdue badge, and saves instructions plus teacher attachments into one folder per course — no model involved |
| Attachments | Download files linked on assignment pages into the assignment workspace |
| Documents | Read PDFs (PyMuPDF), DOCX, PPTX (slides + notes), TXT/MD/CSV/JSON/YAML/code |
| Study help | Explain assignments, break requirements into tasks, work through problems step by step |
| File generation | Word `.docx`, PDF (Chrome print engine), and safe code/text files |
| Assignment drafts | One click drafts every deliverable (Word/PDF/code/online text) into the assignment folder, writes a requirement checklist, and tracks your review with file hashes. You submit yourself |
| Workspace | One folder per synced course, one per assignment, with `instructions/`, `attachments/`, `extracted/`, `explanations/`, `code/`, `documents/`, `pdfs/`, `submission/`. Course names given by the model or a breadcrumb (“database systems”, “CS301”) resolve to the synced course, so a course never gets a second folder |
| UI | Single-page dashboard served by FastAPI: chat with streaming, tool traces, and suggestions built from your data; collapsible cards; browser status; approvals; course-folder tree with downloads; activity log; light/dark theme |

### What it deliberately cannot do

- Submit an assignment or upload a file to Moodle
- Change grades, profile, password, enrolment, or course settings
- Delete or edit Moodle content
- Bypass login, SSO, MFA, CAPTCHA, or access controls
- Execute generated code
- Run shell commands
- Talk to any AI service other than local Ollama
- Accept connections from outside `127.0.0.1`

---

## Architecture

```
Visible Chrome  <--Playwright-->  BrowserSession  -->  Moodle parsers
      |                                                    |
      | manual login                                       v
      +--------------------> AppState sync  --------->  Tool registry
                                   |                       |
   FastAPI + SSE  <---- events/approvals ----  Agent loop (validated tool calls)
        |                                                  |
        v                                                  v
   Dashboard (single page, 127.0.0.1:8765)       Ollama qwen3:8b (localhost)
        |
        +--> SQLite (courses, assignments, conversations, activity, approvals)
        +--> workspace/ (course material, explanations, generated files)
```

The dashboard's assignment list is filled by `app/sync.py`, which `AppState`
runs directly (after login, or from the **Sync** button). It is not a model tool,
so the tool set below is unchanged and the model cannot start a scrape.

A chat turn is a bounded tool-calling loop:

1. The system prompt and the last messages go to `qwen3:8b`.
2. Tool calls are validated with Pydantic and classified as read-only or not.
3. Tools run against the visible browser or the local workspace.
4. Tool errors are returned to the model as data, so it can recover or explain.
5. Consequential overwrites pause for an approval in the dashboard.
6. The answer streams back over Server-Sent Events and is saved in SQLite.

The loop stops after `MAX_AGENT_ITERATIONS` rounds (12 by default) and supports
**Stop** between every step, with a forced cancel after 10 seconds.

---

## Tool set

Only these 14 implemented tools are exposed to the model:

| Tool | Purpose | Moodle writes? |
| --- | --- | --- |
| `get_current_page` | Current URL, title, login/connection state | No |
| `get_page_content` | Visible text, headings, links, activities | No |
| `navigate_to_url` | Open an allowed read-only Moodle page | No |
| `list_courses` | Course overview from the logged-in session | No |
| `open_course` | Open a course and read its activity list | No |
| `list_assignments` | Pending assignments with raw due/status text | No |
| `open_assignment` | Read details and save the local workspace | No |
| `download_attachment` | Fetch a file the user can already see and save its text in `extracted/` | No |
| `read_pdf` | Extract PDF text with page limits | No |
| `read_document` | Read DOCX/PPTX/text/code from the workspace | No |
| `create_word_document` | Create `.docx` in `documents/` | No |
| `create_pdf` | Render a PDF with headless Chrome | No |
| `create_code_file` | Write an allowed code/text file; never runs it | No |
| `save_assignment_workspace` | Save/refresh instructions, metadata, page text | No |

Navigation is limited to the Moodle host and blocks paths such as logout, admin,
enrolment, grade editing, profile editing, and assignment upload/submission.
File writes are jailed to `workspace/` and `generated/`.

---

## Configuration

Copy `.env.example` to `.env`. Useful values:

| Variable | Default | Notes |
| --- | --- | --- |
| `APP_HOST` | `127.0.0.1` | Validated: loopback only |
| `APP_PORT` | `8765` | API and dashboard port — one process serves both |
| `OLLAMA_HOST` | `http://localhost:11434` | Must be local |
| `OLLAMA_MODEL` | `qwen3:8b` | Any installed local model works, but the app is tuned for qwen3 |
| `OLLAMA_NUM_CTX` | `16384` | Context window |
| `OLLAMA_TEMPERATURE` | `0.2` | Lower is more predictable |
| `OLLAMA_KEEP_ALIVE` | `30m` | Keeps the model warm between turns |
| `OLLAMA_THINK` | `false` | Thinking mode roughly doubles CPU latency |
| `BROWSER_CHANNEL` | `chrome` | `chrome`, `msedge`, or `chromium` |
| `BROWSER_HEADLESS` | `false` | Must stay false for manual login/oversight |
| `BROWSER_TIMEOUT_MS` | `30000` | Page navigation timeout |
| `MOODLE_URL` | `https://lms.dsu.edu.pk/` | Default LMS; change it here or in the dashboard |
| `MAX_DOWNLOAD_MB` | `50` | Attachment size cap |
| `MAX_PAGE_CHARS` | `14000` | Page text sent to the model |
| `MAX_TOOL_RESULT_CHARS` | `8000` | Tool result cap |
| `APPROVAL_TIMEOUT_S` | `300` | A timeout denies the action |
| `MAX_AGENT_ITERATIONS` | `12` | Tool-round safety bound |
| `SYNC_ON_CONNECT` | `true` | Read courses/status/due dates automatically once login is detected |
| `DUE_SOON_HOURS` | `48` | Amber badge threshold |
| `DUE_URGENT_HOURS` | `24` | Red badge threshold; must not exceed `DUE_SOON_HOURS` |

`python-docx` files, PDFs, and browser output stay on this machine. No API keys
are required anywhere.

---

## Workspace layout

```
MoodleAI/
├── main.py                 # uvicorn entry point
├── app/
│   ├── api.py              # FastAPI routes, SSE, CSRF/host guards
│   ├── state.py            # app state, approvals, run lifecycle
│   ├── drafts.py           # assignment draft files, review hashes, REVIEW.md
│   ├── config.py           # validated settings
│   ├── security.py         # URL policy, path jail, sanitization
│   ├── events.py           # in-process pub/sub
│   └── ui/                 # dashboard: index.html, styles.css, app.js
├── agent/
│   ├── llm.py              # local Ollama client
│   ├── loop.py             # bounded tool-calling loop
│   └── prompts.py          # untrusted-content rules
├── browser/
│   ├── session.py          # visible persistent Chrome
│   └── moodle.py           # DOM snapshot + parsers
├── tools/                  # the 14 tools and workspace helpers
├── database/               # SQLite repository
├── workspace/              # your course material and study files
├── generated/              # files created with no course or assignment open
├── logs/                   # rotating local logs
├── config/                 # saved Moodle URL (not a password)
├── browser/profile/        # persistent Chrome profile (gitignored)
└── tests/                  # unit, API, browser, and end-to-end tests
```

An assignment workspace looks like:

```
workspace/Database Systems/Normalization Exercise/
├── instructions/assignment.md
├── instructions/metadata.json
├── attachments/instructions.pdf
├── extracted/page.txt
├── extracted/instructions.pdf.txt # attachment text (also in assignment.md)
├── explanations/REVIEW.md      # draft checklist (after "Draft with AI")
├── code/normalize.py
├── documents/solution.docx
├── pdfs/summary.pdf
├── submission/
└── review.json                 # hashes recorded by "Mark reviewed"
```

A file created while a course is open but no assignment is goes to
`workspace/<course>/<code|documents|pdfs>/`; only with nothing open does it land
in `generated/`. In the dashboard's Files card these are the **Courses** and
**Other** tabs. Course folders always show; empty scaffolding folders inside an
assignment stay hidden until something is saved in them.

Nothing silently overwrites an existing file. An overwrite opens the approval
dialog; denying it leaves the original file untouched.

---

## Security model

- **Loopback only.** `APP_HOST` is validated at startup. Host and Origin headers
  are checked on every request.
- **CSRF guard.** Every state-changing request needs `X-MoodleAI: 1`, which a
  cross-site form cannot set.
- **No Moodle account password handling.** Login is manual in Chrome. The app
  never asks for, reads, types, logs, or stores your Moodle credentials.
- **Network credentials are transient.** The one credential field is the optional
  hidden "Network login" pair for campus HTTP Basic auth. It is passed to Chrome
  (Playwright `http_credentials`) for that site only, held in memory, cleared on
  disconnect, and never written to logs, disk, or SQLite.
- **Read-only Moodle.** No tool can submit, upload, delete, grade, or change
  account settings. Dangerous paths are blocked even if the model asks.
- **Untrusted course content.** Page/PDF/document text is explicitly labelled as
  data, not instructions. Embedded “ignore your rules” text does not override the
  system prompt.
- **Approval gate.** Overwrites and workspace refreshes pause until you click
  Approve. Timeouts deny. Stopping the agent denies everything pending.
- **Path jail.** All writes are resolved against `workspace/`/`generated/`;
  traversal, absolute paths, and symlinks are rejected.
- **No shell, no execution.** Generated code is written to disk and never run.
- **Local CSP.** `script-src 'self'`, no framing, no MIME sniffing, no caching.
- **Redaction.** Cookies, tokens, session keys, and secret-looking fields are
  removed from events and logs; URLs are shown without query strings in the UI.

The dashboard is a local control surface, not a hardened multi-user web app.
Do not expose port 8765 through a tunnel, reverse proxy, or firewall rule.

---

## Tests

```powershell
$env:PYTHONIOENCODING="utf-8"

# Fast suite: config, security, database, parsers, loop, tools, API
python -m pytest tests -q

# Optional: real visible Chrome tests (opens and closes a browser window)
$env:RUN_BROWSER_TESTS="1"
python -m pytest tests -q

# Optional: real Ollama tests (requires qwen3:8b)
$env:RUN_OLLAMA_TESTS="1"
python -m pytest tests/test_ollama.py -q
```

The fast suite covers 124 tests plus 10 skipped tests that need a real browser or
the local model. `tests/test_drafts.py` covers draft review hashing and a scripted
draft run end to end; `tests/test_extract.py` covers PPTX/DOCX/PDF text extraction
and the attachment section in `assignment.md`. With `RUN_BROWSER_TESTS=1` the
browser tests also run, leaving only the Ollama tests skipped. The optional browser
suite includes:

- visible Chrome launch, login detection, and host-policy enforcement
- live Moodle DOM parsing for courses/assignments/assignment details
- a complete **mock Moodle → PDF download → PDF read → Word/PDF/code files →
  workspace persistence → overwrite denial** workflow
- the automatic sync against a mock Moodle: statuses, calendar due dates, course
  folders, saved instructions, the teacher's attachment downloaded and the
  student's own submission file skipped, and a repeat sync that rewrites and
  re-downloads nothing

The real-model path was also exercised manually through the dashboard: a plain
answer, a tool-call round with a recoverable browser error, and an approval
dialog for an overwrite. Those live checks are intentionally not part of the
default suite because they depend on model speed and local Chrome.

### Verified status

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Environment audit (Windows, Python, Ollama, qwen3, Chrome) | Complete |
| 2 | Project skeleton, config, logging, security helpers, SQLite | Complete |
| 3 | Ollama client: health, streaming text, streaming tool calls | Complete |
| 4 | Visible persistent browser, manual login detection, URL policy | Complete |
| 5 | Moodle course/assignment parsing from live DOM | Complete |
| 6 | Tool registry, Pydantic validation, bounded agent loop, approvals | Complete |
| 7 | Attachment download, resource resolution, PDF extraction | Complete |
| 8 | Word/PDF/code generation, document reading, overwrite approval | Complete |
| 9 | FastAPI app, SSE dashboard, file tree, approval UI, API tests | Complete |
| 10 | End-to-end mock Moodle study workflow | Complete |

Not verified against your real Moodle instance because that requires your manual
login and your institution's page layout. Private Moodle themes are handled with
heuristics; if a page is unusual, the agent can still return the visible page
text via `get_page_content`.

---

## Performance on CPU

`qwen3:8b` is a large model for CPU-only inference. On the test machine it ran at
roughly 5 tok/s with a warm model. The first turn after a restart can take
1–2 minutes because the model is loaded into RAM and the prompt is processed;
later turns are typically faster (tens of seconds, depending on answer length).

Tips:

- Keep `OLLAMA_KEEP_ALIVE=30m` so the model stays warm.
- Ask for one step at a time (“list the assignments”, then “open the first one”).
- Keep `OLLAMA_THINK=false`; thinking mode is much slower on CPU.
- The UI streams tokens and supports **Stop** without freezing.
- A GPU-enabled Ollama install will be dramatically faster with the same app.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Address already in use` | Change `APP_PORT` in `.env`, or stop the other process on 8765 |
| Chrome profile “already in use” | Close the other Chrome window using `browser/profile`, or stop the other MoodleAI instance |
| Chrome not found | Install Chrome, or set `BROWSER_CHANNEL=msedge`. For bundled Chromium: `python -m playwright install chromium` |
| Ollama pill says “offline” | Start the Ollama app / `ollama serve`, then check `ollama list` |
| qwen3:8b missing | `ollama pull qwen3:8b` |
| Login not detected | Finish login in the visible Chrome window, open the Moodle dashboard, then click **Check login** |
| Page parsing looks wrong | Your theme may use unusual markup; ask the agent to read the visible page text, or open the page manually in Chrome |
| PDF generation fails | Chrome must be available to Playwright (`BROWSER_CHANNEL=chrome`); PDFs render without JavaScript |
| `403 Missing local request header` | You are calling the API manually; add `-H "X-MoodleAI: 1"` for POST/PUT/PATCH/DELETE |
| Browser tests are skipped | Set `$env:RUN_BROWSER_TESTS="1"` (they open a real Chrome window) |
| Ollama tests are skipped | Set `$env:RUN_OLLAMA_TESTS="1"` after pulling the model |
| First answer is very slow | Expected on CPU while the model loads; later turns reuse it via `OLLAMA_KEEP_ALIVE` |

---

## Privacy and data reset

Local data lives in:

- `database/moodleai.db` — courses, assignments, conversations, activity, approvals
- `config/app_state.json` — the Moodle URL only (never a password)
- `browser/profile/` — Chrome session for your Moodle login
- `workspace/` — course material and generated study files
- `generated/` — files created with no course or assignment open
- `logs/` — local logs

### Reset from the chat

Type `/reset` in the chat box and confirm. This clears courses, assignments,
conversations, messages, activity, approvals and everything under `workspace/`
and `generated/` (only the top-level `.gitkeep` of each stays). Your saved Moodle URL and the
Chrome login profile are kept, so you do not have to log in again. The command
is not advertised in the composer any more, but it works the same. A reset is
refused while a sync is running. After a reset, press **Sync** to fetch
everything again.

It is also available as `POST /api/reset` with the usual `X-MoodleAI: 1` header,
and every open dashboard tab refreshes itself when a reset happens.

To start completely fresh, stop the app first, then remove any of those paths.
For example:

```powershell
Remove-Item database\moodleai.db -Force
Remove-Item config\app_state.json -Force
Remove-Item browser\profile -Recurse -Force
```

Deleting `workspace/` removes your saved course material and generated files;
back it up first if you want to keep your work.

---

## Known limitations

- Moodle themes and activity plugins differ. Parsers cover common Moodle 4.x
  markup; unusual pages fall back to visible text rather than guessing.
- Attachments behind one-time links, external tools, or non-file resources may
  need to be opened manually.
- Scanned PDFs without a text layer return little or no text; OCR is not included.
- The model can make mistakes. Always review explanations and generated files
  against the actual assignment, and submit manually.
- CPU inference is slow; this is a local-model tradeoff, not a bug.
- Only one agent turn runs at a time. The Stop button is cooperative and forces
  cancellation after 10 seconds.
- The dashboard is designed for a single local user, not multi-user hosting.

---

## License / use

Built as a local study assistant. You are responsible for following your
institution's academic-integrity rules and Moodle terms of use. MoodleAI is a
reading and drafting aid; it does not submit work on your behalf.
