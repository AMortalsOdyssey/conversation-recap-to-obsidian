# Monthly Goal Review

A monthly goal review is not a larger weekly report. Convert one month of evidence into a defensible statement of user value, progress, results, open gaps, next actions, and requested support.

## Contents

- [1. Establish scope and inventory the source](#1-establish-scope-and-inventory-the-source)
- [2. Synthesize by goal-level workstream](#2-synthesize-by-goal-level-workstream)
- [3. Produce the review form first](#3-produce-the-review-form-first)
- [4. Add evidence after the pasteable review](#4-add-evidence-after-the-pasteable-review)
- [5. Write back safely](#5-write-back-safely)
- [Success bar](#success-bar)

## 1. Establish scope and inventory the source

1. Resolve the reporting month, requested project or goal, form headings, and destination from the user request and any screenshot.
2. List every existing Daily Note in the target month and record the exact count before summarizing.
3. Read every listed Daily Note in full. Treat the whole note as evidence, but use raw entries to recover exact delivery and acceptance boundaries when a generated daily summary is compressed or truncated.
4. Include only work relevant to the requested goal. Name unrelated projects or workstreams that were deliberately excluded when the month contains mixed work.
5. Follow durable links only when they resolve an ambiguity or prove an important artifact. Prefer live notes, code, logs, deployment state, and acceptance evidence over memory.
6. Build a private evidence ledger with these fields:

```text
date | workstream | user/core problem | result/output | artifact |
code | automated tests | test deployment | device acceptance |
release/production | open gap | needed collaborator
```

Do not paste this ledger verbatim into the final review. Use it to prevent evidence loss and false completion claims.

## 2. Synthesize by goal-level workstream

- Merge the same initiative across dates. Do not organize the review as a chronological diary.
- Rank workstreams by user/player value, importance of the solved problem, delivery maturity, duration, and decision weight.
- Use the user's stated goal when available. If the goal must be inferred, express it as `玩家/用户价值 → 核心问题 → 具体目标` and mark it as an evidence-based synthesis rather than a historical quote.
- Choose an honest completion label: `已达成`, `阶段性达成`, `推进中`, or `未启动`. Use a percentage only when a defined denominator or agreed milestone plan makes the number defensible.
- Keep these states separate: code implemented, automated tests passed, test environment deployed, real-device/business acceptance passed, client release merged, production impact measured.
- Never convert a test deployment into player impact. Never convert a target, plan, or pending acceptance into a completed result.
- Preserve quantitative baselines, targets, and measured outcomes. State explicitly when a target still lacks a comparable post-change measurement.

## 3. Produce the review form first

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
- End with concrete, verifiable goals.
- Avoid turning the goal into a list of technical tasks discovered after the fact.

### 本月目标完成情况

- Start with one completion judgment and its boundary.
- Group progress into 3–6 cross-day workstreams.
- For each workstream, name the durable result or output and its strongest delivery evidence.
- Distinguish completed work from testing-only, pending acceptance, and planned follow-up.

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

- Link every Daily Note that was actually read.
- State the exact source-note count and any intentionally excluded workstreams.
- Link only proven durable artifacts. Never invent a document, work-item ID, heading, or block anchor.

## 5. Write back safely

1. Use the user-provided destination. Otherwise reuse the vault's existing monthly-review directory; if none exists, create `monthly-reviews/YYYY/MM/`.
2. Use frontmatter with at least `title`, `date`, `type: monthly-goal-review`, `period_start`, `period_end`, `status`, `source_daily_notes`, `word_count`, and useful tags. Add `project` when the scope is project-specific.
3. Treat a human-reviewed monthly note as authoritative. On refresh, preserve user corrections and revise only the requested sections; do not blindly regenerate the whole note from Daily Notes.
4. Do not add `AI_SUMMARY` markers around the whole monthly review. It is a reviewed artifact, not a disposable generated block.
5. Do not modify source Daily Notes while creating or refreshing the monthly review.
6. Run `verify-note --fix`, reread the saved note, and confirm:
   - `word_count` equals the body character count;
   - every required form heading exists;
   - the source-note count matches the inventory;
   - testing, deployment, acceptance, release, and production claims remain distinct;
   - no sentence is truncated and no target is mislabeled as achieved.

## Success bar

A strong monthly review lets a manager understand, without reading the diary:

- the player/user value pursued;
- the most important real progress and durable outputs;
- what changed because of the work;
- what is genuinely complete versus still awaiting acceptance or measurement;
- what next month should close;
- exactly which cross-team support is needed.
