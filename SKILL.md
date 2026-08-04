---
name: conversation-recap-to-obsidian
description: Use when summarizing chats or existing Obsidian daily/weekly notes into review-ready entries, daily summaries, weekly reports, session recaps, work-item groupings, wikilinks, tags, conclusions, key points, Obsidian Bases indexes, or group-sendable weekly report versions. Also use when the user says “总结会话”, “总结上周周报”, asks to refresh Daily Note/weekly notes, or asks to make the recap/weekly workflow stronger.
---

# Conversation Recap to Obsidian

This skill turns raw conversation or existing Obsidian markdown into **review-ready notes**, not generic summaries. Treat the whole daily/weekly note as input content regardless of who wrote each part.

The default goal is to help the user answer:
- What were the main things done?
- What problems did those things solve?
- What were the key points?
- What conclusions or outputs matter later?
- Which notes/documents should be linked for follow-up?

## Use this skill for

- writing a structured recap of the current conversation into Obsidian
- appending a single session entry into a daily note
- regenerating a daily summary by reading the full daily note first
- creating a weekly report from multiple daily notes
- creating a weekly report in Obsidian and then outputting a concise group-sendable summary
- merging a multi-day thread into one weekly module
- replacing stale generated summary blocks while preserving all non-target content
- producing Obsidian wikilinks for relevant artifacts
- consolidating notes that may contain mixed content from humans, this assistant, and other AI/tools

## Core design

Split the work in three layers:

1. **Entry layer = raw work-item capture**
   - append a single session recap into the daily note
   - preserve concrete issue / solution / conclusion / key point details
   - act as the source material for later daily and weekly synthesis

2. **Daily summary layer = same-day aggregation**
   - read the full daily note
   - merge duplicate work threads
   - compress the day into a small number of reusable conclusions

3. **Weekly layer = cross-day synthesis**
   - merge same-topic work across multiple days
   - rank larger / more complex items first
   - avoid day-by-day流水账

4. **Verification and review layer = durable Obsidian writeback**
   - reread saved notes after writing
   - verify `word_count` against the actual body character count
   - keep generated summary markers balanced
   - optionally create `.base` index files for browsing daily and weekly notes inside Obsidian

Use scripts when they improve reliability. Do not avoid them just to stay “pure prompt only.”

## Obsidian companion skills

When this skill writes Obsidian content, apply these installed skill conventions as needed:

- `obsidian-markdown`: use valid properties/frontmatter, wikilinks, tags, embeds, and callouts. Use wikilinks only for durable vault-local notes or artifacts.
- `obsidian-cli`: prefer the configured vault and exact `path=` when paths are known; after writes, reread the target file instead of trusting command output.
- `obsidian-bases`: use `.base` files for index/review surfaces, not for replacing prose recaps. Keep Bases valid YAML and test with a parser when possible.
- `json-canvas`: use only when the user asks for a visual map, relationship board, or project canvas; do not add canvas files to ordinary recap flows by default.

## Daily note target

Default path:
- `daily/YYYY/MM/YYYY-MM-DD.md` (organized by year and month)
- Users may override the base directory through local config

## Mode 1: Session recap mode

Use this when the user has just finished one conversation or one work block and wants to **record a new entry** instead of refreshing the whole day.

### Session recap principle

Create a new item in the daily note as source material for later summaries.

### Default entry structure

```markdown
#### 事项标题 — HH:mm

- **结果**: ...
- **问题**: ...
- **处理**: ...
- **要点**: ...
- **文档**: [[...]] · [[...]]
- **标签**: #tag-a #tag-b
```

`问题` is optional and only rendered when `--problem` is supplied. Supply it whenever the item fixed something: it is the **only** source for the weekly `核心解决的问题` field, so an entry written without it cannot contribute that field to any later weekly report.

### Session recap guidance

- Prefer one concrete work item per entry.
- If the conversation truly covered multiple unrelated things, either split into 2 entries or name the entry at a higher level.
- Keep each field tight and useful; the raw entry should still be reviewable without becoming a transcript.
- The title should describe the work item, not just say “对话总结”.
- Use tags sparingly; 1-3 strong tags are enough.
- `结果` is the final outcome or durable state.
- `问题` is the user-visible symptom or the gap being closed. Keep it about what was wrong, never about the fix.
- `处理` is the shortest useful description of what changed or how it was handled. Make it a self-contained statement that names the fix, not only the root cause — a reader who sees only this line should still know what was done.
- `要点` is for 1-2 reusable decisions, constraints, or lessons. Do not paste test logs or every implementation detail.
- Keep process evidence such as tests, commits, pushes, and dirty-tree handling out of `要点` unless it is the actual lesson.
- `文档` should include only the strongest durable links, normally 1-3.
- Preserve document hierarchy: session entries belong in the raw entry section of the daily note, and the generated `## 今日总结` block should stay as a higher-level summary section near the end of the note.
- When appending an entry to a daily note that already contains `## 今日总结`, insert the new entry **before** the generated summary block, then refresh the summary if needed.

## Mode 2: Daily summary mode

A daily summary is **regenerable**. It is not an append-only log.

When asked to refresh the daily summary:
1. Read the full daily note.
2. Treat the whole note as usable source material regardless of whether parts were written by a person or another AI/tool.
3. Ignore only the previous generated summary block for this skill to avoid recursive self-copying.
4. Extract the day’s main work items from the note content.
5. Compress them into a concise review section.
6. Replace only the generated summary block.

### Default daily output shape

```markdown
## 今日总结

### 今日重点
1. **事项名**：结果或产出

### 关键判断
- ...

### 文档与标签
- 文档：[[...]] · [[...]]
- 标签：#tag-a #tag-b
```

### Daily writing guidance

- Prefer outcomes over chronology.
- Merge duplicate points.
- If multiple sessions worked on the same thing, describe it once more strongly.
- Keep all major same-day work visible, but compress each item to the smallest useful unit.
- The default item shape is one line: `事项名：结果或产出`.
- Every work item keeps its outcome. Items past the numbered highlight limit drop to a terser bullet, but never to a titles-only list — a bare title tells a reviewer nothing about what the item achieved.
- The generated block is a draft. When the script's ordering buries a genuinely major item, or `关键判断` ends up dominated by one topic, rewrite the block by hand and rerun `verify-note --fix`. The raw entries remain the source of truth, so a later refresh can safely regenerate it.
- Put detailed problem / solution / evidence in the raw session entry, not in the daily summary.
- Use `关键判断` only for reusable decisions, constraints, or lessons; do not copy every key point from every entry, and skip process evidence such as tests, commits, pushes, and dirty-tree handling.
- Keep links limited to durable notes or outputs.
- Treat the entire note as evidence; do not downgrade a section just because it was written by another AI/tool.
- Ignore only the current skill's previous generated summary block when refreshing, so the summary does not recursively paraphrase itself.
- Keep the summary compact and high-density rather than long and chatty.
- Avoid semicolon-heavy mega-lines. Prefer short numbered modules and short bullets.

## Mode 3: Weekly summary mode

Default path:
- `weekly/YYYY/MM/YYYY-MM-DD.md` (organized by year and month)

The date is the **Sunday** of that reporting week.

### Weekly summary principle

A weekly report should be organized by **work item**, not by day.

The correct unit is not “Tuesday” or “Wednesday.”
The correct unit is “the import pipeline fix,” “the skill redesign,” “the database migration,” etc.

If one work item spans 3 days, merge those 3 daily notes into one weekly module.

### Weekly frontmatter and module structure

Weekly notes should include frontmatter like:

```markdown
---
word_count: 1234
type: weekly-summary
week_start: 2026-03-23
week_end: 2026-03-29
---
```

Then the body uses modules like:

```markdown
### 1. 事项名
- 涉及日期：2026-03-17、2026-03-18、2026-03-19
- 核心解决的问题：...
- 关键点：...
- 结论/产出：...
- 相关文档：[[...]] · [[...]]
- 标签：#tag-a #tag-b
```

### Weekly ranking rule

Sort weekly items by importance using these signals:
1. number of involved days
2. amount of structured content / subpoints
3. visible complexity or decision weight

Larger, longer-running, more complex items should appear earlier.

### Weekly writing guidance

- Merge same-topic work across days.
- Avoid day-by-day流水账.
- Avoid vague weekly overviews.
- Name each item in a way the user can recognize later.
- Prefer 2-5 strong modules over 12 weak fragments.
- If a single work item appears in several daily notes, produce one merged module instead of repeating it by date.
- When source material is sparse or uneven, still try to infer the strongest few work modules from headings, bullets, and existing summary sections.
- Keep each module concise; the default should read like a crisp weekly review, not a transcript.

### Weekly command behavior

When the user says “总结上周周报” or equivalent:
1. Generate or refresh the previous week’s Obsidian weekly note first, normally with `scripts/recap_manager.py generate-weekly-auto --mode last-week`.
2. Read back the generated weekly note and verify it was written.
3. If the user says a current-day daily note contains补记上周工作, or the relevant daily note explicitly marks content as belonging to last week, incorporate that material into the previous week’s weekly note before finalizing.
4. Keep the weekly note organized by work item, not by day. If the script produces too many small modules, merge them into 2-6 stronger modules by theme and update `word_count`.
5. Run `scripts/recap_manager.py verify-note --path <weekly-note-path> --fix` after edits, then reread the note if `fixed` is true.
6. Produce a separate group-sendable version with `scripts/recap_manager.py print-weekly-brief --path <weekly-note-path>` and include it in the final response.

### Group-sendable weekly version

Produce this version after weekly note verification when requested directly, or when the user says “总结上周周报”.

Default shape:

```text
上周
1. 关键词：一句话总结。
2. 关键词：一句话总结。
```

Writing guidance:
- Use 4-7 numbered items unless the weekly note is clearly smaller or larger.
- Each item must be one sentence.
- Put a descriptive keyword before `：`; prefer “领域 + 目标” or “能力 + 结果”, such as `Team Sharing 控制面`、`分享权限治理`、`Knowledge Space 共识库`, not overly short labels like `安装` or `治理`.
- Make the keyword specific enough that a group reader can identify the work area without reading the sentence.
- Emphasize outcomes and product capability changes, not commands, tests, commits, or implementation logs.
- Preserve the weekly note’s ranking and merge small adjacent items where the group version would otherwise feel repetitive.
- In the final reply, include the group-sendable block and briefly mention the Obsidian weekly note path.

## Tagging guidance

Tags are optional but useful.

### Principles

- Prefer existing tags already present in the note.
- Add tags only when they improve retrieval or weekly grouping.
- Keep tags lightweight.
- Good tag sources:
  - project or product name
  - work type (`#summary-skill`, `#线上排障`)
  - technical topic (`#jwt`, `#auth`, `#obsidian`)

### Avoid

- over-tagging
- generic tags with no retrieval value
- tags that merely restate the obvious

## Safe rewrite rule

Generated sections should be wrapped with markers so they can be replaced safely:

```markdown
<!-- AI_SUMMARY_START -->
...
<!-- AI_SUMMARY_END -->
```

Preserve all non-target content outside the markers.

## Bundled script

Use the bundled script for stable maintenance tasks:
- `scripts/recap_manager.py`

The script is publishable because it supports shared defaults plus local overrides.
Resolve configuration in this order:
1. CLI arguments
2. `config.json` next to the skill
3. built-in defaults

### Commands

Append a session entry:

```bash
python scripts/recap_manager.py append-entry \
  --title "JWT验签修复与线上排障" \
  --problem "登录后 401，被踢回" \
  --solution "补 JWKS 公钥验签并修正 issuer" \
  --conclusion "测试和正式环境恢复正常" \
  --key-points "先确认 session claims，再加严格校验" \
  --links "app/core/auth/jwt_auth.py,deploy/config.k8s.yaml" \
  --tags "jwt,auth,线上排障"
```

Refresh a daily summary:

```bash
python scripts/recap_manager.py refresh-daily-auto --date 2026-03-25
```

Generate a weekly report:

```bash
python scripts/recap_manager.py generate-weekly-auto --mode last-week
```

Verify a saved note and fix `word_count` if needed:

```bash
python scripts/recap_manager.py verify-note \
  --path "Memory/weekly/2026/06/2026-06-21.md" \
  --fix
```

Print a concise group-sendable weekly version from the saved weekly note:

```bash
python scripts/recap_manager.py print-weekly-brief \
  --path "Memory/weekly/2026/06/2026-06-21.md"
```

Create or refresh Obsidian Bases indexes for review:

```bash
python scripts/recap_manager.py ensure-index-base --kind all
```

This creates `.base` files under `Memory/index/` by default:
- `Daily Notes.base`
- `Weekly Reports.base`

Use these indexes as browsing/review surfaces inside Obsidian. Do not treat them as the source of truth; Daily Notes and weekly notes remain the source material.

## Important constraints

- Do not invent documents or wikilinks.
- Prefer documents explicitly connected to the current work item; do not sweep unrelated paths from the whole note into “相关文档”.
- Do not blindly append a second stale summary if the user asked for refresh/regeneration.
- Do not reduce weekly review into a chronological diary.
- If source notes are weak, still try to infer stable work modules from headings and structured bullets.
- Read the target note before summarizing. Other agents and sessions append to the same daily note, so it may already contain entries for today — check for an existing entry covering the same work instead of adding a near-duplicate, and give the entry a title that distinguishes it from similarly-named work already there.
- When a session spans past midnight, file each work item under the date it actually happened rather than forcing everything into today's note. Creating the earlier date's note is correct and keeps weekly synthesis accurate.
- After every write, reread the saved file and confirm the fields are complete sentences. Field limits truncate, and a truncated line is only marked with a trailing `…`; if a line lost its point, rewrite it shorter rather than leaving the fragment.

## Success bar

A good result lets the user quickly review:
- what the major work items were
- what each item actually solved
- why it mattered
- what durable outputs or notes exist
- which tags or themes recur across the day or week
