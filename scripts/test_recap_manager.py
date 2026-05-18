import datetime as dt
import sys
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
        self.assertNotIn("- **问题**:", entry)
        self.assertNotIn("- **方案**:", entry)

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


if __name__ == "__main__":
    unittest.main()
