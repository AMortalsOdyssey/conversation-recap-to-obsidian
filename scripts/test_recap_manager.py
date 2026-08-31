import datetime as dt
import contextlib
import io
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recap_manager


def run_append_process(script: str, config_path: str, index: int) -> None:
    subprocess.run(
        [
            sys.executable,
            script,
            'append-entry',
            '--config',
            config_path,
            '--date',
            '2026-08-10',
            '--time',
            f'10:{index:02d}',
            '--title',
            f'Concurrent item {index}',
            '--solution',
            f'Applied change {index}.',
            '--conclusion',
            f'Completed result {index}.',
            '--key-points',
            f'Keep decision {index}.',
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def kill_on_direct_write(config, path, content) -> None:
    os.kill(os.getpid(), signal.SIGKILL)


def crash_during_flush(config) -> None:
    recap_manager.write_note_direct = kill_on_direct_write
    recap_manager.flush_pending(config)


def make_test_config(root: Path):
    vault = root / 'vault'
    vault.mkdir()
    return {
        **recap_manager.DEFAULTS,
        'obsidian_bin': '/missing/obsidian',
        'vault': 'TestVault',
        'vault_path': str(vault),
        'daily_dir': 'daily',
        'weekly_dir': 'weekly',
        'index_dir': 'index',
        'queue_db': str(root / 'state' / 'queue.db'),
    }


class DailySummaryFormatTests(unittest.TestCase):
    def test_entry_block_is_compact(self):
        class Args:
            time = "18:40"
            title = "Skill 摘要格式优化"
            problem = "今日总结和原始条目字段过长，阅读成本高。"
            solution = "把 entry 层压缩为结果、处理、要点、文档、标签。"
            conclusion = "Daily Note 变成更适合快速回看的结构。"
            key_points = "详细证据留在原始上下文；日报只保留可复用结论。"
            links = "project/changelog/features/browser-layout.md"
            tags = "summary-skill,obsidian"

        entry = recap_manager.build_entry_block(Args)

        self.assertIn("- **结果**:", entry)
        self.assertIn("- **处理**:", entry)
        self.assertIn("- **要点**:", entry)
        self.assertIn("- **文档**:", entry)
        self.assertNotIn("- **方案**:", entry)
        # A supplied problem must be persisted: it is the only source for the weekly
        # 核心解决的问题 field, so discarding it made --problem a no-op argument.
        self.assertIn("- **问题**:", entry)
        self.assertIn("阅读成本高", entry)

    def test_entry_block_omits_problem_line_when_not_supplied(self):
        class Args:
            time = "18:40"
            title = "Skill 摘要格式优化"
            problem = ""
            solution = "把 entry 层压缩为结果、处理、要点、文档、标签。"
            conclusion = "Daily Note 变成更适合快速回看的结构。"
            key_points = "详细证据留在原始上下文；日报只保留可复用结论。"
            links = "project/changelog/features/browser-layout.md"
            tags = "summary-skill,obsidian"

        entry = recap_manager.build_entry_block(Args)

        self.assertNotIn("- **问题**:", entry)
        self.assertEqual(entry.count("- **"), 5)

    def test_entry_problem_survives_round_trip_into_weekly_field(self):
        class Args:
            time = "09:00"
            title = "登录 401 排障"
            problem = "登录后立刻 401 被踢回，测试与正式环境都复现。"
            solution = "补 JWKS 公钥验签并修正 issuer。"
            conclusion = "两个环境恢复正常。"
            key_points = "先确认 session claims，再加严格校验。"
            links = ""
            tags = "auth"

        entry = recap_manager.build_entry_block(Args)
        body = entry.split("\n", 2)[2]
        fields = recap_manager.parse_structured_fields(body)

        self.assertIn("401", fields["problem"])

    def test_daily_summary_keeps_outcome_for_overflow_items(self):
        # Overflow items used to collapse into a titles-only "其余事项" blob, which hid
        # what a major item actually achieved on a busy day.
        entries = []
        for i in range(1, recap_manager.DAILY_SUMMARY_HIGHLIGHT_LIMIT + 2):
            entries.append(
                f"#### 事项{i} — 0{i % 10}:00\n\n"
                f"- **结果**: 结果{i}落地并部署\n"
                f"- **处理**: 做法{i}\n"
                f"- **要点**: 要点{i}\n"
                f"- **文档**: 无\n"
                f"- **标签**: #t\n"
            )
        note = "---\ndate: 2026-08-04\ntype: daily\n---\n# 2026-08-04\n\n" + "\n".join(entries)

        summary = recap_manager.build_daily_summary_from_note(note)
        highlights = summary.split("### 关键判断")[0]
        last = recap_manager.DAILY_SUMMARY_HIGHLIGHT_LIMIT + 1

        self.assertNotIn("其余事项", highlights)
        self.assertIn(f"**事项{last}**：结果{last}落地并部署", highlights)

    def test_weekly_module_omits_problem_line_when_never_recorded(self):
        # "核心解决的问题：无" reads as "this solved nothing" rather than "nobody wrote
        # it down"; entries in the compact shape legitimately have no 问题 line.
        without_problem = {
            'compact': {
                'title': '只有紧凑字段的事项',
                'dates': ['2026-08-04'],
                'problems': [],
                'key_points': ['一条可复用结论'],
                'conclusions': ['已部署并验证'],
                'links': [],
                'tags': ['t'],
            }
        }
        body = recap_manager.build_weekly_report(
            dt.date(2026, 8, 3), dt.date(2026, 8, 9), without_problem
        )

        self.assertNotIn('核心解决的问题', body)
        self.assertIn('结论/产出：已部署并验证', body)

        with_problem = dict(without_problem)
        with_problem['compact'] = dict(without_problem['compact'], problems=['登录后 401'])
        body_with = recap_manager.build_weekly_report(
            dt.date(2026, 8, 3), dt.date(2026, 8, 9), with_problem
        )

        self.assertIn('核心解决的问题：登录后 401', body_with)

    def test_shorten_text_always_marks_truncation(self):
        # Cutting at a clause separator used to return the head with no marker, so a
        # line that lost its second half still read as a finished sentence.
        text = "根因是里程碑调度只对 Path B 生效，因此 Path A 全程只剩一个落档点，改 flushReason 一处即可"
        short = recap_manager.shorten_text(text, 40)

        self.assertLessEqual(len(short), 41)
        self.assertTrue(short.endswith("…"), short)
        self.assertEqual(recap_manager.shorten_text("很短的一句话", 40), "很短的一句话")

    def test_daily_summary_uses_scannable_sections(self):
        note = """---
date: 2026-05-18
type: daily
---
# 2026-05-18

#### Project 中等宽度 Thread 可见性修复 — 18:13

- **问题**: Safari 在约 890px 宽度下点击 reply 后 Thread 状态已打开但面板不可见，问题集中在 768px-1099px 平板/窄桌面断点。
- **方案**: 新增 tablet-inspector-main/thread-open 布局类，在该断点把 Thread inspector 提升到主内容列。
- **结论**: Thread 修复提交 5e9fb7f 已推送到 GitLab 与 GitHub，Feature changelog 已同步移动端/窄桌面浏览器体验边界。
- **关键点**: 参考产品在相同尺寸下保留左侧 Chat rail 并把 Thread 放到主区域；本项目用 890px、760px、1200px 三宽度验证。
- **关联**: [[project/changelog/features/browser-layout.md]]
- **标签**: #Project #thread #响应式布局

#### Project Human 与 Server 权限管理补齐 — 18:18

- **问题**: Human 详情页和 Server settings 缺少完整 Owner/Admin 管理入口。
- **方案**: Human 详情页新增 Permissions 角色表单，Server settings 改成 Owners & Admins 面板。
- **结论**: Human 与 Server 权限补齐提交 982977d 已推送到 gitlab/main 与 origin/main。
- **关键点**: Owner 可以管理其他 Owner 但不能移除自己；权限约束由后端 capability 兜底。
- **关联**: [[project/changelog/features/permissions.md]]
- **标签**: #Project #权限管理
"""
        summary = recap_manager.build_daily_summary_from_note(note)

        self.assertIn("### 今日重点", summary)
        self.assertIn("### 关键判断", summary)
        self.assertIn("### 文档与标签", summary)
        self.assertIn("Project 中等宽度 Thread 可见性修复", summary)
        self.assertIn("Project Human 与 Server 权限管理补齐", summary)
        self.assertNotIn("- 今日主要事项：", summary)
        self.assertNotIn("- 核心解决的问题：", summary)

    def test_daily_word_count_is_body_character_count(self):
        normalized = recap_manager.normalize_daily_note("", dt.date(2026, 5, 18))
        meta, body = recap_manager.split_frontmatter(normalized)

        self.assertEqual(meta["word_count"], len(body))

    def test_tags_support_hierarchy_and_hyphen(self):
        tags = recap_manager.parse_tags_text("#summary-skill #project/weekly #线上排障")

        self.assertEqual(tags, ["summary-skill", "project/weekly", "线上排障"])

    def test_weekly_brief_uses_generated_modules(self):
        note = """---
type: weekly-summary
week_start: 2026-06-15
week_end: 2026-06-21
word_count: 1
---
# 周报 - 2026-06-21

<!-- AI_SUMMARY_START -->
## 本周重点事项（按复杂度 / 投入度排序）

### 1. Team Sharing
- 涉及日期：2026-06-16、2026-06-17
- 核心解决的问题：同步上报在网关超时边界下容易误判失败。
- 关键点：receipt-first 先返回收据，再由后台 worker 完成索引。
- 结论/产出：完成异步上报边界梳理，并把验证方式沉淀进 Daily Note。
- 相关文档：[[myproject/magclaw/changelog/features/team-sharing.md]]
- 标签：#team-sharing

### 2. Obsidian 与 Skill 工作流
- 涉及日期：2026-06-18
- 核心解决的问题：周报整理缺少可复用索引。
- 关键点：Base 适合做周报视图。
- 结论/产出：新增周报索引思路。
- 相关文档：无
- 标签：#obsidian #summary-skill

## 本周总体结论
- 本周主要推进了 Team Sharing。
<!-- AI_SUMMARY_END -->
"""

        brief = recap_manager.build_weekly_brief_from_note(note)

        self.assertIn("上周工作：", brief)
        self.assertIn("1. Team Sharing：完成异步上报边界梳理", brief)
        self.assertIn("2. Obsidian 与 Skill 工作流：新增周报索引思路", brief)

    def test_weekly_brief_groups_repeated_project_prefixes(self):
        note = """<!-- AI_SUMMARY_START -->
### 1. Kizuna 创角问题修复
- 结论/产出：修复工具卡消失和确认卡被覆盖。

### 2. Kizuna 性能优化
- 结论/产出：优化远程图片与多图缩略图处理。

### 3. Kizuna 测试验证
- 结论/产出：完成测试环境部署和真机验收。

### 4. MagClaw Team Sharing
- 结论/产出：修复误触发并发布新版本。
<!-- AI_SUMMARY_END -->
"""

        brief = recap_manager.build_weekly_brief_from_note(note)

        self.assertIn("1. Kizuna", brief)
        self.assertIn("   - 创角问题修复：修复工具卡消失和确认卡被覆盖。", brief)
        self.assertIn("   - 性能优化：优化远程图片与多图缩略图处理。", brief)
        self.assertIn("   - 测试验证：完成测试环境部署和真机验收。", brief)
        self.assertIn("2. MagClaw Team Sharing：修复误触发并发布新版本。", brief)
        self.assertNotIn("2. Kizuna", brief)

    def test_weekly_brief_groups_repeated_cjk_project_prefixes(self):
        # Latin-only prefix detection left Chinese project names ungrouped, so a week of
        # 创角* modules still printed several top-level items with the same leading words.
        note = """<!-- AI_SUMMARY_START -->
### 1. 创角问题修复
- 结论/产出：修复工具卡消失。

### 2. 创角性能优化
- 结论/产出：优化多图缩略图。

### 3. 记忆系统迁移
- 结论/产出：完成端口切换。
<!-- AI_SUMMARY_END -->
"""

        brief = recap_manager.build_weekly_brief_from_note(note)

        self.assertIn("1. 创角", brief)
        self.assertIn("   - 问题修复：修复工具卡消失。", brief)
        self.assertIn("   - 性能优化：优化多图缩略图。", brief)
        self.assertIn("2. 记忆系统迁移：完成端口切换。", brief)
        self.assertNotIn("2. 创角", brief)

    def test_weekly_brief_never_drops_work_silently(self):
        mods = "".join(
            f"### {i}. Kizuna 模块{i}\n- 结论/产出：产出{i}。\n\n"
            for i in range(1, recap_manager.MAX_BRIEF_SUBITEMS + 3)
        )

        brief = recap_manager.build_weekly_brief_from_note(
            "<!-- AI_SUMMARY_START -->\n" + mods + "<!-- AI_SUMMARY_END -->\n"
        )

        self.assertIn("另有 2 项同项目工作", brief)

    def test_weekly_brief_prefix_tolerates_empty_title(self):
        # A malformed "### 1. " heading used to raise IndexError and kill the command.
        self.assertEqual(recap_manager.weekly_brief_prefix(""), "")
        self.assertEqual(recap_manager.weekly_brief_prefix("   "), "")

    def test_base_index_content_is_valid_yaml_shape(self):
        for kind in ("daily", "weekly"):
            content = recap_manager.build_base_index_content(kind)
            self.assertIn("filters:", content)
            self.assertIn("views:", content)
            self.assertIn("- type: table", content)

    def test_verify_note_can_fix_word_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            note_path = root / "Memory" / "daily" / "2026" / "06" / "2026-06-24.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text("---\nword_count: 1\n---\n# 2026-06-24\n\n正文\n")

            class Args:
                config = None
                obsidian_bin = "/missing/obsidian"
                vault = "TestVault"
                vault_path = str(root)
                daily_dir = None
                weekly_dir = None
                index_dir = None
                path = "Memory/daily/2026/06/2026-06-24.md"
                fix = True

            with contextlib.redirect_stdout(io.StringIO()):
                recap_manager.cmd_verify_note(Args)
            meta, body = recap_manager.split_frontmatter(note_path.read_text())

            self.assertEqual(meta["word_count"], len(body))

    def test_queue_insert_flush_round_trip_uses_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = make_test_config(root)
            payload = {
                'title': 'Queue round trip',
                'problem': '',
                'solution': 'Drain the SQLite row.',
                'conclusion': 'The entry reached the Daily Note.',
                'key_points': 'Mark done only after the Markdown write.',
                'links': '',
                'tags': 'queue',
                'time': '10:00',
                'work_items': '',
            }

            entry_id = recap_manager.enqueue_entry(config, dt.date(2026, 8, 10), payload)
            result = recap_manager.flush_pending(config)
            status, error = recap_manager.queue_entry_result(config, entry_id)
            note = (root / 'vault' / 'daily' / '2026' / '08' / '2026-08-10.md').read_text()
            with contextlib.closing(recap_manager.queue_connect(config)) as conn:
                journal_mode = conn.execute('PRAGMA journal_mode').fetchone()[0]

            self.assertEqual(result, {'flushed': 1, 'failed': 0, 'remaining': 0})
            self.assertEqual((status, error), ('done', None))
            self.assertEqual(journal_mode.lower(), 'wal')
            self.assertIn('#### Queue round trip — 10:00', note)
            self.assertEqual(note.count(recap_manager.START), 1)
            self.assertEqual(note.count(recap_manager.END), 1)

    def test_six_concurrent_appends_lose_nothing_and_leave_vault_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = make_test_config(root)
            config_path = root / 'config.json'
            config_path.write_text(json.dumps(config))
            script = str(Path(recap_manager.__file__).resolve())
            ctx = multiprocessing.get_context('fork')
            processes = [
                ctx.Process(target=run_append_process, args=(script, str(config_path), i))
                for i in range(6)
            ]

            for process in processes:
                process.start()
            for process in processes:
                process.join(30)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)

            note_path = root / 'vault' / 'daily' / '2026' / '08' / '2026-08-10.md'
            note = note_path.read_text()
            verify = subprocess.run(
                [
                    sys.executable,
                    script,
                    'verify-note',
                    '--config',
                    str(config_path),
                    '--path',
                    'daily/2026/08/2026-08-10.md',
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            verification = json.loads(verify.stdout)
            with contextlib.closing(recap_manager.queue_connect(config)) as conn:
                pending = conn.execute(
                    "SELECT COUNT(*) FROM entry_queue WHERE status = 'pending'"
                ).fetchone()[0]

            self.assertEqual(note.count('#### Concurrent item '), 6)
            self.assertEqual(note.count(recap_manager.START), 1)
            self.assertEqual(note.count(recap_manager.END), 1)
            self.assertTrue(verification['word_count_ok'])
            self.assertTrue(verification['summary_markers_ok'])
            self.assertEqual(pending, 0)
            vault_files = [path.name for path in (root / 'vault').rglob('*') if path.is_file()]
            self.assertFalse(any(name.endswith(('.lock', '.db', '.db-wal', '.db-shm')) for name in vault_files))

    def test_killed_flush_is_recovered_by_the_next_flush(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = make_test_config(root)
            payload = {
                'title': 'Crash recovery',
                'problem': '',
                'solution': 'Replay the pending row.',
                'conclusion': 'The entry is recovered.',
                'key_points': 'Pending is durable.',
                'links': '',
                'tags': '',
                'time': '11:00',
                'work_items': '',
            }
            recap_manager.enqueue_entry(config, dt.date(2026, 8, 10), payload)
            ctx = multiprocessing.get_context('fork')
            process = ctx.Process(target=crash_during_flush, args=(config,))

            process.start()
            process.join(30)
            self.assertEqual(process.exitcode, -signal.SIGKILL)
            with contextlib.closing(recap_manager.queue_connect(config)) as conn:
                pending_before = conn.execute(
                    "SELECT COUNT(*) FROM entry_queue WHERE status = 'pending'"
                ).fetchone()[0]

            result = recap_manager.flush_pending(config)
            note = (root / 'vault' / 'daily' / '2026' / '08' / '2026-08-10.md').read_text()

            self.assertEqual(pending_before, 1)
            self.assertEqual(result['remaining'], 0)
            self.assertIn('#### Crash recovery — 11:00', note)

    def test_work_items_render_across_entry_daily_and_weekly_and_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = make_test_config(root)
            config_path = root / 'config.json'
            config_path.write_text(json.dumps(config))
            source = root / 'vault' / 'docs' / 'real-work-items.md'
            source.parent.mkdir(parents=True)
            source.write_text(
                '### RECAP-CORE-26810-1 — Queue rollout\n\n'
                'The implementation is accepted. ^recap-core-26810-1\n'
            )
            link = '[[docs/real-work-items#^recap-core-26810-1|RECAP-CORE-26810-1]]'
            bare_id = 'RECAP-CORE-26810-2'
            script = str(Path(recap_manager.__file__).resolve())
            base_cmd = [sys.executable, script]

            subprocess.run(
                base_cmd + [
                    'append-entry', '--config', str(config_path), '--date', '2026-08-10',
                    '--time', '12:00', '--title', 'Work item links', '--solution', 'Pass links through.',
                    '--conclusion', 'All three recap layers retain them.', '--key-points', 'Never invent targets.',
                    '--work-items', f'{link},{bare_id}',
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            subprocess.run(
                base_cmd + ['refresh-daily-auto', '--config', str(config_path), '--date', '2026-08-10'],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            subprocess.run(
                base_cmd + [
                    'generate-weekly-auto', '--config', str(config_path),
                    '--mode', 'current', '--date', '2026-08-10',
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )

            daily = (root / 'vault' / 'daily' / '2026' / '08' / '2026-08-10.md').read_text()
            weekly = (root / 'vault' / 'weekly' / '2026' / '08' / '2026-08-16.md').read_text()
            target, anchor = link[2:-2].split('|', 1)[0].split('#^', 1)
            target_path = root / 'vault' / f'{target}.md'

            self.assertGreaterEqual(daily.count(link), 2)
            self.assertIn('- 相关工作项：' + link, weekly)
            self.assertIn(f'`{bare_id}`', daily)
            self.assertIn(f'`{bare_id}`', weekly)
            self.assertTrue(target_path.exists())
            self.assertIn(f'^{anchor}', target_path.read_text())

    def test_empty_work_items_keeps_legacy_bytes(self):
        class Args:
            time = '09:00'
            title = '兼容性检查'
            problem = ''
            solution = '保持原输出。'
            conclusion = '兼容完成。'
            key_points = '不增加空字段。'
            links = ''
            tags = 'compat'
            work_items = ''

        entry = recap_manager.build_entry_block(Args)
        expected_entry = (
            '#### 兼容性检查 — 09:00\n\n'
            '- **结果**: 兼容完成。\n'
            '- **处理**: 保持原输出。\n'
            '- **要点**: 不增加空字段\n'
            '- **文档**: 无\n'
            '- **标签**: #compat\n'
        )
        self.assertEqual(entry, expected_entry)

        daily = recap_manager.build_daily_summary_from_note(entry)
        expected_daily = (
            '## 今日总结\n\n### 今日重点\n'
            '1. **兼容性检查**：兼容完成。\n\n'
            '### 关键判断\n- 不增加空字段\n\n'
            '### 文档与标签\n- 文档：无\n- 标签：#compat'
        )
        self.assertEqual(daily, expected_daily)

        items = {
            'compat': {
                'title': '兼容性检查', 'dates': ['2026-08-10'], 'problems': [],
                'key_points': ['不增加空字段'], 'conclusions': ['兼容完成。'],
                'links': [], 'tags': ['compat'],
            }
        }
        weekly_body = (
            '# 周报 - 2026-08-16\n\n'
            '<!-- AI_SUMMARY_START -->\n'
            '## 本周重点事项（按复杂度 / 投入度排序）\n\n'
            '### 1. 兼容性检查\n'
            '- 涉及日期：2026-08-10\n'
            '- 关键点：不增加空字段\n'
            '- 结论/产出：兼容完成。\n'
            '- 相关文档：无\n'
            '- 标签：#compat\n\n'
            '## 本周总体结论\n'
            '- 本周主要推进了 兼容性检查\n'
            '<!-- AI_SUMMARY_END -->\n'
        )
        expected_weekly = (
            '---\n'
            'type: weekly-summary\n'
            'week_start: 2026-08-10\n'
            'week_end: 2026-08-16\n'
            f'word_count: {len(weekly_body)}\n'
            'tags: [compat]\n'
            '---\n'
            + weekly_body
        )
        weekly = recap_manager.build_weekly_report(
            dt.date(2026, 8, 10), dt.date(2026, 8, 16), items
        )
        self.assertEqual(weekly, expected_weekly)

    def test_replace_summary_preserves_other_content_and_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = make_test_config(root)
            date = dt.date(2026, 8, 10)
            path = recap_manager.daily_path(config, date)
            original = recap_manager.normalize_daily_note(
                '# 2026-08-10\n\n#### Existing entry — 08:00\n\n- **结果**: Keep me.\n',
                date,
            )
            original = recap_manager.replace_summary_block(original, '## 今日总结\n\n旧总结')
            original = recap_manager.normalize_daily_note(original, date)
            recap_manager.write_note_direct(config, path, original)
            replacement = root / 'replacement.md'
            replacement.write_text('## 今日总结\n\n### 今日重点\n- 新总结')

            class ReplaceArgs:
                obsidian_bin = None
                vault = None
                vault_path = config['vault_path']
                daily_dir = config['daily_dir']
                weekly_dir = None
                index_dir = None
                queue_db = config['queue_db']
                date = '2026-08-10'
                file = str(replacement)

            with contextlib.redirect_stdout(io.StringIO()):
                recap_manager.cmd_replace_summary(ReplaceArgs)
            updated = recap_manager.read_note_direct(config, path)
            before_body = recap_manager.split_frontmatter(original)[1]
            after_body = recap_manager.split_frontmatter(updated)[1]

            class VerifyArgs:
                obsidian_bin = '/missing/obsidian'
                vault = 'TestVault'
                vault_path = config['vault_path']
                daily_dir = config['daily_dir']
                weekly_dir = None
                index_dir = None
                queue_db = config['queue_db']
                path = 'daily/2026/08/2026-08-10.md'
                fix = False

            verify_output = io.StringIO()
            with contextlib.redirect_stdout(verify_output):
                recap_manager.cmd_verify_note(VerifyArgs)
            verification = json.loads(verify_output.getvalue())

            self.assertEqual(
                recap_manager.remove_generated_block(before_body),
                recap_manager.remove_generated_block(after_body),
            )
            self.assertIn('### 今日重点\n- 新总结', updated)
            self.assertEqual(updated.count(recap_manager.START), 1)
            self.assertEqual(updated.count(recap_manager.END), 1)
            meta, body = recap_manager.split_frontmatter(updated)
            self.assertEqual(meta['word_count'], len(body))
            self.assertTrue(verification['word_count_ok'])
            self.assertTrue(verification['summary_markers_ok'])

    def test_queue_path_rejects_vault_and_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = make_test_config(root)
            config['queue_db'] = str(root / 'vault' / 'queue.db')
            with self.assertRaises(ValueError):
                recap_manager.queue_db_path(config)

            config['queue_db'] = str(Path(recap_manager.__file__).resolve().parent / 'queue.db')
            with self.assertRaises(ValueError):
                recap_manager.queue_db_path(config)


if __name__ == "__main__":
    unittest.main()


class EntryDateResolutionTests(unittest.TestCase):
    """An entry must be dated by when the work happened, not by when someone
    typed 总结会话. A batch of recaps written days later used to land on the
    summarizing day, which silently corrupts the weekly 涉及日期 field."""

    class Args:
        date = ''
        time = ''
        cli_session = ''

    def _args(self, **overrides):
        args = EntryDateResolutionTests.Args()
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def _claude_transcript(self, root: Path, session_id: str, records) -> None:
        project = root / '.claude' / 'projects' / '-Users-tt-code-demo'
        project.mkdir(parents=True, exist_ok=True)
        with (project / f'{session_id}.jsonl').open('w', encoding='utf-8') as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')

    def _codex_rollout(self, root: Path, session_id: str, records) -> None:
        day = root / '.codex' / 'sessions' / '2026' / '08' / '27'
        day.mkdir(parents=True, exist_ok=True)
        name = f'rollout-2026-08-27T02-56-39-{session_id}.jsonl'
        with (day / name).open('w', encoding='utf-8') as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')

    @contextlib.contextmanager
    def _home(self, root: Path):
        original_claude = recap_manager.CLAUDE_SESSION_ROOT
        original_codex = recap_manager.CODEX_SESSION_ROOT
        recap_manager.CLAUDE_SESSION_ROOT = root / '.claude' / 'projects'
        recap_manager.CODEX_SESSION_ROOT = root / '.codex' / 'sessions'
        try:
            yield
        finally:
            recap_manager.CLAUDE_SESSION_ROOT = original_claude
            recap_manager.CODEX_SESSION_ROOT = original_codex

    def test_claude_session_dates_the_entry_by_first_real_user_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._claude_transcript(root, 'sess-a', [
                # Synthetic turns carry the wrong clock and must be skipped.
                {'type': 'user', 'timestamp': '2026-08-26T18:00:00.000Z',
                 'message': {'content': '<system-reminder>ignore me</system-reminder>'}},
                {'type': 'user', 'timestamp': '2026-08-26T18:56:39.784Z',
                 'message': {'content': '根据下面的方案，执行落地，部署测试环境'}},
                # The summarize request days later must never win.
                {'type': 'user', 'timestamp': '2026-08-31T02:38:37.308Z',
                 'message': {'content': '总结会话'}},
            ])
            with self._home(root):
                date, entry_time, source = recap_manager.resolve_entry_datetime(
                    self._args(cli_session='sess-a'))

        self.assertEqual(source, 'session')
        self.assertEqual(date, dt.date(2026, 8, 27))
        self.assertEqual(entry_time, '02:56')

    def test_codex_rollout_shape_is_understood_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._codex_rollout(root, 'thread-b', [
                {'type': 'session_meta', 'timestamp': '2026-08-27T02:56:00.000Z', 'payload': {}},
                {'type': 'response_item', 'timestamp': '2026-08-26T18:56:39.000Z',
                 'payload': {'type': 'message', 'role': 'user',
                             'content': [{'type': 'input_text', 'text': '把这批改动落地'}]}},
            ])
            with self._home(root):
                date, _, source = recap_manager.resolve_entry_datetime(
                    self._args(cli_session='thread-b'))

        self.assertEqual(source, 'session')
        self.assertEqual(date, dt.date(2026, 8, 27))

    def test_explicit_date_always_beats_the_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._claude_transcript(root, 'sess-c', [
                {'type': 'user', 'timestamp': '2026-08-26T18:56:39.784Z',
                 'message': {'content': '真实用户轮'}},
            ])
            with self._home(root):
                date, entry_time, source = recap_manager.resolve_entry_datetime(
                    self._args(date='2026-08-28', time='17:07', cli_session='sess-c'))

        self.assertEqual(source, 'explicit')
        self.assertEqual(date, dt.date(2026, 8, 28))
        self.assertEqual(entry_time, '17:07')

    def test_missing_date_and_session_warns_loudly_instead_of_defaulting_silently(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._home(Path(tmp)):
                date, _, source = recap_manager.resolve_entry_datetime(self._args())
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    recap_manager.warn_entry_date_source(source, date, '')

        self.assertEqual(source, 'today')
        self.assertEqual(date, dt.date.today())
        message = stderr.getvalue()
        self.assertIn('WARNING', message)
        # The warning has to say the entry cannot be moved afterwards; there is no
        # move/remove command, so a silent default is unrecoverable.
        self.assertIn('no command to move an entry', message)

    def test_unreadable_session_falls_back_to_today_with_the_session_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._home(Path(tmp)):
                date, _, source = recap_manager.resolve_entry_datetime(
                    self._args(cli_session='does-not-exist'))
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    recap_manager.warn_entry_date_source(source, date, 'does-not-exist')

        self.assertEqual(source, 'today')
        self.assertIn('does-not-exist', stderr.getvalue())
