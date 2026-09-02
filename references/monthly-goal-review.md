# Monthly Goal Review

A monthly goal review is not a larger weekly report. Convert one month of evidence into a defensible statement of user value, progress, results, open gaps, next actions, and requested support.

The split of labour is fixed: **the script counts, the reviewer judges.** `inventory-month` proves which Daily Notes exist and flattens every structured entry into a private ledger; the reviewer merges, weighs, labels and writes.

## Contents

- [1. Establish scope and inventory the source](#1-establish-scope-and-inventory-the-source)
- [2. Synthesize by goal-level workstream](#2-synthesize-by-goal-level-workstream)
- [3. Produce the review form first](#3-produce-the-review-form-first)
- [4. Add evidence after the pasteable review](#4-add-evidence-after-the-pasteable-review)
- [5. Write back safely](#5-write-back-safely)
- [6. Refresh an existing review](#6-refresh-an-existing-review)
- [Success bar](#success-bar)

## 1. Establish scope and inventory the source

1. Resolve the reporting month, the project or goal, the form headings, and the destination from the user request and any screenshot. Without a screenshot, use the default headings in section 3.
2. Read the previous month's review if one exists and keep its `下月重点动作` at hand. It feeds the private alignment section (section 3), **not** this month's `本月目标`. Never let a planned item silently disappear between months.
3. Build the ledger with the script, writing it **outside the vault** (the scratchpad or a temp dir):

   ```bash
   python3 scripts/recap_manager.py inventory-month --month 2026-08 \
     --tags kizuna,omnivibe,创角 --work-item-prefixes CC,PLT \
     --keywords 创角,comfyproxy,banzi --project Kizuna \
     --brief --out /path/outside/vault/ledger-brief.md
   python3 scripts/recap_manager.py inventory-month --month 2026-08 ... --out /path/outside/vault/ledger.md
   ```

   - Scope rules are OR-ed: a tag (matched by prefix, so `创角` also catches `创角agent`), a work-item domain prefix, or a title keyword puts an entry in scope. No rule means everything is in scope.
   - The header proves coverage: the count and dates of every Daily Note found, notes with no structured entries, and how many entries were included versus excluded. Copy the count into `source_daily_notes`; do not count by hand.
   - The generated `今日总结` block is dropped automatically. Only what the sessions themselves recorded feeds the ledger.
4. Read the **excluded list first** and fix the scope before reading anything else. Any excluded title that belongs to the goal means a tag, prefix, or keyword is missing; rerun rather than adding it by hand. Whatever stays excluded gets named in the review as deliberately excluded work.
5. Read the `--brief` ledger (title, 结果, 工作项 per entry) once, end to end, to form the cross-day workstreams. Then open the full ledger only for the entries whose delivery or acceptance boundary the brief line cannot settle. Open the raw Daily Note only when the ledger still leaves a real ambiguity. Every note is counted by the script; every in-scope entry is read at least in brief form; nothing is skipped because it looked minor.
6. Follow durable links only when they resolve an ambiguity or prove an important artifact. Prefer live notes, code, logs, deployment state, and acceptance evidence over memory.
7. Keep private working notes with these fields while reading; they never go into the final review:

   ```text
   workstream | dates | user/core problem | result/output | artifact |
   code | automated tests | test deployment | device acceptance |
   release/production | open gap | needed collaborator
   ```

## 2. Synthesize by goal-level workstream

- Merge the same initiative across dates. Do not organize the review as a chronological diary.
- Rank workstreams by user/player value, importance of the solved problem, delivery maturity, duration, and decision weight.
- `本月目标` describes what the month actually pursued. Use the user's stated goal when one was given for the month; otherwise synthesize it from the ledger as `玩家/用户价值 → 核心问题 → 具体目标` and say so. Do not copy last month's plan into it: the plan and the reality are compared separately in the alignment section.
- Choose an honest completion label: `已达成`, `阶段性达成`, `推进中`, or `未启动`. Use a percentage only when a defined denominator or agreed milestone plan makes the number defensible.
- Keep these states separate: code implemented, automated tests passed, test environment deployed, real-device/business acceptance passed, client release merged, production impact measured.
- Never convert a test deployment into player impact. Never convert a target, plan, or pending acceptance into a completed result.
- Preserve quantitative baselines, targets, and measured outcomes. State explicitly when a target still lacks a comparable post-change measurement.
- A measurement belongs to the month it was taken. Results measured after `period_end` may appear only as `月后补充（YYYY-MM-DD）`; the month in which they were measured owns them and reports them as its own progress. This is what keeps two consecutive reviews from claiming the same number.

## 3. Produce the alignment note, then the review form

### 与上月计划的对齐（自留，不进表单）

When a previous month's review exists, write this section **first in the body, above the form**. It is for the user alone and is never pasted anywhere, so it can be blunt. Its job is to compare plan against reality without letting either contaminate the other:

```markdown
## 与上月计划的对齐（自留，不进表单）

| 上月规划的动作 | 本月实际做了什么 | 对齐判断 |
|---|---|---|

**计划外但占了大量精力**：…

**对齐总评**：…
```

- One row per planned action, quoted with its original metrics. The middle column cites dated ledger facts; the last column is one of `达成` / `部分对齐` / `未启动` / `偏离`, followed by one clause on how (means changed, target unjudged, superseded).
- List unplanned work that consumed real effort, and say whether it was a product insert, an incident, or a precondition of a planned item.
- End with an overall verdict: counts per label, the one or two reasons for the deviation, and any item that has slipped two months in a row.

### The form

When the user supplies form headings, reproduce them exactly. Otherwise use this default order:

```markdown
## 本月目标

## 本月目标完成情况

## 解决效果

## 下月重点动作

## 需要协同支持的事项
```

Write the first four sections so the user can paste them directly into a performance-review form.

### 本月目标

- Lead with player/user value.
- State the core problem being solved.
- Name the goals the month actually pursued, synthesized from the ledger, and say that they were derived from the month's work; point to the alignment section for the plan-versus-reality comparison.
- Keep it at the level of outcomes the month was after, not a list of technical tasks; but never restate last month's plan as if it had been this month's goal.

### 本月目标完成情况

- Start with one completion judgment and its boundary.
- Group progress into 3–6 cross-day workstreams.
- For each workstream, name the durable result or output and its strongest delivery evidence.
- Distinguish completed work from testing-only, pending acceptance, and planned follow-up.
- Do not repeat the plan-versus-reality accounting here; it lives in the alignment section.

### 解决效果

- Describe `before → after → why it matters` from the user or business perspective.
- State the evidence scope, such as automated tests, testing, selected device cases, release, or production metrics.
- Do not claim broad business impact without production data.

### 下月重点动作

- Express each item as `价值/体验 → 未解决问题 → 重点动作 → 验证指标`.
- Prioritize closing acceptance and measurement gaps before starting unrelated expansion.
- Carry forward incomplete targets explicitly instead of silently resetting them.

### 需要协同支持的事项

- Name the function or team, the exact decision/resource/access needed, and why it blocks or accelerates the goal.
- Prefer concrete asks such as an acceptance matrix, stable test accounts/devices, provider retry policy, release window, or production metric access.
- Avoid vague requests such as “加强协同” or “希望支持”.

## 4. Add evidence after the pasteable review

Add only the evidence surfaces that help review and correction:

```markdown
## 事实依据与进展归并

| 工作主线 | 本月代表性进展/产出 | 状态判断 |
|---|---|---|

## 月度日报来源
```

- Paste the ledger's `日报来源链接` line so every Daily Note that was counted is linked.
- State the exact source-note count, the scope rules used, and the intentionally excluded workstreams by name.
- Link only proven durable artifacts. Never invent a document, work-item ID, heading, or block anchor.

## 5. Write back safely

1. Use the user-provided destination. Otherwise write to the `monthly_dir` configured in `config.json`; the `suggested_note_path` printed by `inventory-month --out` is that path. Create the directory if it does not exist.
2. Use frontmatter with at least `title`, `date`, `type: monthly-goal-review`, `period_start`, `period_end`, `status`, `source_daily_notes`, `word_count`, and useful tags. Add `project` when the scope is project-specific. `date` is the authoring date and never changes afterwards; refreshes stamp `updated`.
3. Do not add `AI_SUMMARY` markers anywhere in the note. It is a reviewed artifact, not a disposable generated block.
4. Do not modify source Daily Notes while creating or refreshing the monthly review.
5. Run `verify-note --path <note> --fix`. For a `monthly-goal-review` note it reports, beyond `word_count` and markers:
   - `required_headings_ok` / `missing_headings`: the five form headings;
   - `has_alignment_section`: whether `## 与上月计划的对齐` is present (expected whenever a previous month's review exists);
   - `source_daily_notes_ok` / `actual_source_daily_notes`: recounted from the vault, corrected by `--fix`;
   - `no_summary_markers_ok`;
   - `post_period_dates`: every ISO date in the body later than `period_end`. Each one must sit inside a `月后补充（…）` phrase; otherwise move the fact to the month that owns it.
6. Reread the saved note and confirm no sentence is truncated, no target is mislabeled as achieved, and testing, deployment, acceptance, release, and production claims remain distinct.

## 6. Refresh an existing review

A human-reviewed monthly note is authoritative. On refresh, preserve user corrections and revise only the requested sections; never regenerate the whole note from Daily Notes.

Every partial edit ends with the same consistency sweep, because a fact added to one section is usually contradicted by four others:

1. the `总体判断` callout at the top and the alignment section's verdict, if the fact changes either;
2. the matching workstream bullet under `本月目标完成情况`, including its `未完成边界` line;
3. `解决效果` and `下月重点动作`, if the new fact changes what is proven or what is next;
4. the matching row of the `事实依据与进展归并` table;
5. frontmatter `status`, and `updated` (stamped by `verify-note --fix`);
6. any date later than `period_end` carries `月后补充（YYYY-MM-DD）`.

Then run `verify-note --fix` and reread.

## Success bar

A strong monthly review lets a manager understand, without reading the diary:

- the player/user value pursued;
- the most important real progress and durable outputs;
- what changed because of the work;
- what is genuinely complete versus still awaiting acceptance or measurement;
- what next month should close;
- separately, for the user's own eyes, what last month asked for and what actually happened to each item;
- exactly which cross-team support is needed.
