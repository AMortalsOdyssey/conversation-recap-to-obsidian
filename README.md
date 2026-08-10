# conversation-recap-to-obsidian

A publishable skill for turning conversations or existing Obsidian notes into high-value daily and weekly review notes.

## What it does

- Turns a single conversation into a structured entry inside today’s daily note
- Serializes concurrent session entries through a local SQLite WAL queue and shared file lock
- Reads all entries from the current day and generates a **daily summary**
- Reads a full week of daily notes and generates a **weekly report** grouped by work item instead of by date
- Carries stable work-item links through the entry, daily-summary, and weekly-report layers
- Verifies saved notes by checking generated-section markers and `word_count`
- Prints a concise group-sendable weekly version from the saved weekly note
- Creates optional Obsidian Bases indexes for daily notes and weekly reports
- Treats the whole note as usable source material, regardless of whether parts were written by a human, Claude Code, OpenClaw, or another AI/tool
- Rewrites only the generated summary block and leaves the rest of the note untouched

## Who this is for

This skill is useful if you:
- work with AI for many rounds every day and want to keep what matters
- want your conversations to become durable Obsidian notes instead of disappearing in chat history
- care more about reviewable work logs than generic chat summaries

## Everyday usage

| You say | Effect |
|---|---|
| `/summary` or `summary` | Summarize the current conversation and append one `####` entry to today’s daily note |
| `/summary daily` or `daily` | Read all entries from today and regenerate the daily summary at the end of the file |
| `/summary weekly` or `weekly` | Read one week of daily notes and generate a standalone weekly report grouped by work item |
| `weekly brief` | Print a short weekly version suitable for sending to a group chat |

## A typical day

1. You finish one development session → say **summary** → one `####` entry is appended  
2. Later you fix a bug → say **summary** again → a second `####` entry is appended  
3. At the end of the day → say **daily** → the skill reads the day’s entries and regenerates the daily summary  
4. On Friday or Monday → say **weekly** or **last week** → the skill reads the week’s daily notes and generates a grouped weekly report

## Key behaviors

- **Daily summaries are regenerable**: if you add new entries after generating a daily summary, run **daily** again and the old summary will be replaced instead of duplicated
- **Handwritten content stays safe**: the skill only replaces the generated summary block and does not overwrite the rest of the note
- **Weekly reports are grouped by topic**: if one task spans three days, it appears as one merged weekly module rather than three separate day-based notes
- **Three-layer structure**: raw entries → daily summary → weekly report
- **Concurrent appends are durable**: `append-entry` commits to SQLite first, then blocks until its entry is flushed to Markdown; an interrupted flush leaves the row pending for the next run
- **No background service**: queue draining happens inside `append-entry`, with `flush-pending` as a manual recovery command
- **Session entries stay compact**: raw entries use `结果` / `处理` / `要点` / `文档` / `标签` instead of verbose problem-solution transcripts, plus an optional `问题` line when the item fixed something
- **Nothing is dropped silently**: a supplied `--problem` is persisted (it is the only source for the weekly `核心解决的问题` field), truncated fields always end with `…`, and every daily item keeps its outcome instead of collapsing into a titles-only list
- **Daily summaries are scannable**: the generated block uses `今日重点` / `关键判断` / `文档与标签` instead of long semicolon-heavy lines
- **Saved notes are verifiable**: the helper can reread a saved note, check `word_count`, and fix it when needed
- **Weekly reports can produce a shareable brief**: the helper reads the saved weekly report and prints a compact version
- **Optional Obsidian Bases indexes**: generate `.base` files for browsing daily and weekly notes inside Obsidian
- **Tags and document links are supported**: useful for later search, filtering, and review
- **Work-item references are safe**: complete `[[document#^block|ID]]` links stay clickable, while bare IDs render as code instead of phantom notes
- **Frontmatter is maintained automatically**: daily/weekly notes keep `date`/`type`/`word_count` and derived `tags`; `word_count` is the body character count after frontmatter

## Advantages

- **Saves time**: no need to manually rewrite chat history into notes
- **Consistent structure**: every summary follows the same shape
- **Actually reviewable**: focused on problem, solution, conclusion, and key takeaways instead of chat noise
- **Built for continuity**: daily and weekly notes become a reusable work record
- **Configurable**: the same shared skill can adapt to different vaults and directory layouts

## File structure

```text
conversation-recap-to-obsidian/
├── SKILL.md
├── README.md
├── config.example.json
├── .gitignore
└── scripts/
    ├── recap_manager.py
    └── test_recap_manager.py
```

## Configuration

Copy `config.example.json` to `config.json` and edit it for your own environment:

```json
{
  "vault": "YOUR_VAULT_NAME",
  "vault_path": "/absolute/path/to/your/vault",
  "daily_dir": "daily",
  "weekly_dir": "weekly",
  "index_dir": "index",
  "queue_db": "",
  "obsidian_bin": "obsidian"
}
```

Configuration precedence:
1. CLI arguments
2. `config.json`
3. built-in defaults

`queue_db` defaults to `~/.local/state/conversation-recap/queue.db`. Leave the example value empty to use that default. The queue database, its WAL/SHM files, and note locks must stay outside both the Obsidian vault and this repository; the script rejects unsafe overrides.

Daily Note writes use the resolved filesystem path so completion is synchronous and protected by the shared lock. Configure `vault_path` when the vault cannot be resolved from the standard platform locations. `obsidian_bin` remains available for reads and non-Daily-note workflows.

## CLI usage

### 1) Append one session entry

```bash
python scripts/recap_manager.py append-entry \
  --title "JWT verification fix and production debugging" \
  --problem "Users were redirected after login because the session validation path was wrong" \
  --solution "Add JWKS-based verification and fix the issuer configuration" \
  --conclusion "Both staging and production are working again" \
  --key-points "Confirm session claims before adding strict validation" \
  --work-items "[[docs/20260325_1_JWT_fix#^auth-jwt-26325-1|AUTH-JWT-26325-1]]" \
  --links "app/core/auth/jwt_auth.py,deploy/config.k8s.yaml" \
  --tags "jwt,auth,production-debugging"
```

`--work-items` accepts a comma-separated mixture of complete Obsidian links and bare IDs. Complete links are preserved; bare IDs are rendered as inline code.

### 2) Flush interrupted pending entries

```bash
python scripts/recap_manager.py flush-pending
```

The command prints JSON counts for `flushed`, `failed`, and `remaining`. Failed rows are retained with their error for manual inspection and are not retried automatically.

### 3) Refresh the daily summary

```bash
python scripts/recap_manager.py refresh-daily-auto --date 2026-03-25
```

### 4) Replace a polished daily summary

Save the new `## 今日总结` block without `AI_SUMMARY` markers, then apply it through the shared lock:

```bash
python scripts/recap_manager.py replace-summary \
  --date 2026-03-25 \
  --file /tmp/recap-summary-block.md
```

The command updates `word_count` and preserves content outside the generated block.

### 5) Generate the current week report

```bash
python scripts/recap_manager.py generate-weekly-auto --mode current
```

### 6) Generate the previous week report

```bash
python scripts/recap_manager.py generate-weekly-auto --mode last-week
```

### 7) Verify a saved note

```bash
python scripts/recap_manager.py verify-note \
  --path "weekly/2026/06/2026-06-21.md" \
  --fix
```

### 8) Print a group-sendable weekly brief

The brief groups repeated modules under the same project prefix so one project does not occupy several top-level numbered items. Treat the command output as a draft and merge its subitems into short categories such as fixes, optimizations, verification, or releases when needed.

```bash
python scripts/recap_manager.py print-weekly-brief \
  --path "weekly/2026/06/2026-06-21.md"
```

### 9) Create Obsidian Bases indexes

```bash
python scripts/recap_manager.py ensure-index-base --kind all
```

This writes `Daily Notes.base` and `Weekly Reports.base` under `index_dir`.

## Publishing guidance

If you want to publish this skill to GitHub or ClawHub, commit:
- `SKILL.md`
- `README.md`
- `config.example.json`
- `.gitignore`
- `scripts/recap_manager.py`
- `scripts/test_recap_manager.py`

Do **not** commit:
- `config.json`
- SQLite databases or `-wal` / `-shm` files
- note lock files
- `__pycache__/`
- `.DS_Store`

## Environment notes

- The queue uses only Python’s standard-library `sqlite3` module in WAL mode; no Redis, daemon, cron dependency, or third-party package is required
- Daily Note writes require a resolvable vault filesystem path and do not depend on the Obsidian App’s asynchronous create/overwrite behavior
- The same skill logic can be reused in Claude Code and OpenClaw as long as environment-specific paths stay in local `config.json`, not in the shared skill logic
