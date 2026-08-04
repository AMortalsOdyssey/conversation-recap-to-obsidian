import datetime as dt
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recap_manager


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

        self.assertIn("上周", brief)
        self.assertIn("1. Team Sharing：完成异步上报边界梳理", brief)
        self.assertIn("2. Obsidian 与 Skill 工作流：新增周报索引思路", brief)

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


if __name__ == "__main__":
    unittest.main()
