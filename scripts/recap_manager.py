#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
from pathlib import Path
from typing import List, Dict, Any

DEFAULTS = {
    "obsidian_bin": "/Applications/Obsidian.app/Contents/MacOS/Obsidian",
    "vault": "jhb",
    "daily_dir": "Memory/daily",
    "weekly_dir": "Memory/weekly",
}
START = "<!-- AI_SUMMARY_START -->"
END = "<!-- AI_SUMMARY_END -->"
ALLOWED_EXTS = {"md", "py", "ts", "tsx", "js", "json", "yaml", "yml"}


def load_config(cli_args: argparse.Namespace) -> Dict[str, Any]:
    config = dict(DEFAULTS)
    script_dir = Path(__file__).resolve().parent
    skill_dir = script_dir.parent
    config_path = Path(cli_args.config) if getattr(cli_args, 'config', None) else skill_dir / 'config.json'
    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text())
            if isinstance(file_cfg, dict):
                config.update({k: v for k, v in file_cfg.items() if v not in (None, '')})
        except Exception:
            pass
    for key in ('obsidian_bin', 'vault', 'daily_dir', 'weekly_dir'):
        val = getattr(cli_args, key, None)
        if val:
            config[key] = val
    return config


def run_obsidian(config: Dict[str, Any], args: List[str]) -> str:
    cmd = [config['obsidian_bin'], *args]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip() or f"command failed: {' '.join(cmd)}")
    return res.stdout


def read_note(config: Dict[str, Any], path: str) -> str:
    return run_obsidian(config, [f"vault={config['vault']}", 'read', f'path={path}'])


def create_or_overwrite_note(config: Dict[str, Any], path: str, content: str) -> None:
    run_obsidian(config, [f"vault={config['vault']}", 'create', f'path={path}', f'content={content}', 'overwrite'])


def append_note(config: Dict[str, Any], path: str, content: str) -> None:
    run_obsidian(config, [f"vault={config['vault']}", 'append', f'path={path}', f'content={content}'])


def insert_before_summary_block(original: str, entry_block: str) -> str:
    pattern = re.compile(re.escape(START) + r'.*?' + re.escape(END), re.S)
    if pattern.search(original):
        m = pattern.search(original)
        before = original[:m.start()].rstrip()
        summary = original[m.start():]
        return before + '\n\n' + entry_block.strip() + '\n\n' + summary.lstrip()
    return original.rstrip() + '\n\n' + entry_block.strip() + '\n'


def ensure_note(config: Dict[str, Any], path: str, content: str = '') -> None:
    try:
        read_note(config, path)
    except Exception:
        create_or_overwrite_note(config, path, content)


def strip_frontmatter(text: str) -> str:
    return re.sub(r'^---\n.*?\n---\n', '', text, flags=re.S).strip()


def remove_generated_block(text: str) -> str:
    return re.sub(re.escape(START) + r'.*?' + re.escape(END), '', text, flags=re.S).strip()


def replace_summary_block(original: str, new_block: str) -> str:
    pattern = re.compile(re.escape(START) + r'.*?' + re.escape(END), re.S)
    wrapped = f"{START}\n{new_block.strip()}\n{END}"
    if pattern.search(original):
        return pattern.sub(wrapped, original).rstrip() + "\n"
    sep = "\n\n" if original.rstrip() else ""
    return original.rstrip() + sep + wrapped + "\n"


def parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def sunday_for(date: dt.date) -> dt.date:
    return date + dt.timedelta(days=(6 - date.weekday()))


def monday_for(date: dt.date) -> dt.date:
    return date - dt.timedelta(days=date.weekday())


def daily_path(config: Dict[str, Any], date: dt.date) -> str:
    return f"{config['daily_dir'].rstrip('/')}/{date.isoformat()}.md"


def weekly_path(config: Dict[str, Any], sunday: dt.date) -> str:
    return f"{config['weekly_dir'].rstrip('/')}/{sunday.isoformat()}.md"


def extract_wikilinks(text: str) -> List[str]:
    return sorted(set(re.findall(r'\[\[([^\]]+)\]\]', text)))


def parse_tags_text(text: str) -> List[str]:
    tags = re.findall(r'#([A-Za-z0-9_\-\u4e00-\u9fff]+)', text)
    return uniq_keep_order(tags)


def normalize_path_candidate(candidate: str) -> str | None:
    c = candidate.strip().strip('`').strip('"').strip("'")
    c = c.replace('·', '').strip()
    if not c or '://' in c:
        return None
    if c.startswith('~/') or c.startswith('/') or c.startswith('./') or c.startswith('../'):
        return None
    if '<' in c or '>' in c:
        return None
    ext = c.rsplit('.', 1)[-1].lower() if '.' in c else ''
    if ext not in ALLOWED_EXTS:
        return None
    if '/' not in c and not c.endswith('.md'):
        return None
    return c


def extract_inline_paths(text: str) -> List[str]:
    patterns = [
        r'`([^`]+\.(?:md|py|ts|tsx|js|json|yaml|yml))`',
        r'([A-Za-z0-9_][A-Za-z0-9_./\-]*\.(?:md|py|ts|tsx|js|json|yaml|yml))'
    ]
    out = []
    for pat in patterns:
        out.extend(re.findall(pat, text))
    cleaned = []
    for x in out:
        norm = normalize_path_candidate(x)
        if norm:
            cleaned.append(norm)
    return sorted(set(cleaned))


def clean_title(title: str) -> str:
    return re.sub(r'\s+[—-]\s+\d{1,2}:\d{2}$', '', title).strip()


def extract_sections(text: str) -> List[Dict[str, str]]:
    body = remove_generated_block(strip_frontmatter(text))
    lines = body.splitlines()
    sections = []
    current = None
    for line in lines:
        if re.match(r'^####\s+', line.strip()):
            if current:
                sections.append(current)
            current = {'title': clean_title(re.sub(r'^####\s+', '', line).strip()), 'body': ''}
        else:
            if current is None:
                continue
            current['body'] += line + '\n'
    if current:
        sections.append(current)
    return sections


def parse_structured_fields(body: str) -> Dict[str, str]:
    labels = {'问题': 'problem', '方案': 'solution', '结论': 'conclusion', '关键点': 'key_points', '关联': 'links', '标签': 'tags'}
    out = {v: '' for v in labels.values()}
    for line in body.splitlines():
        m = re.match(r'^-\s+\*\*(.+?)\*\*:\s*(.*)$', line.strip())
        if m and m.group(1) in labels:
            out[labels[m.group(1)]] = m.group(2).strip()
    return out


def extract_loose_bullets(text: str) -> List[str]:
    body = remove_generated_block(strip_frontmatter(text))
    bullets = []
    for line in body.splitlines():
        line = line.strip()
        if re.match(r'^-\s+', line) and '**' not in line:
            bullets.append(re.sub(r'^-\s+', '', line))
    return bullets


def render_link_line(links: List[str]) -> str:
    links = sorted(set(links))[:8]
    if not links:
        return '无'
    rendered = []
    for x in links:
        rendered.append(f'[[{x}]]' if not x.startswith('[[') else x)
    return ' · '.join(rendered)


def render_tags_line(tags: List[str]) -> str:
    tags = uniq_keep_order(tags)
    if not tags:
        return '无'
    return ' '.join(f'#{t}' for t in tags[:6])


def derive_tags_from_title(title: str) -> List[str]:
    t = title.lower()
    out = []
    if 'jwt' in t:
        out.append('jwt')
    if 'auth' in t or '验签' in title:
        out.append('auth')
    if 'skill' in t or 'summary' in t:
        out.append('summary-skill')
    if '排障' in title:
        out.append('线上排障')
    if 'obsidian' in t:
        out.append('obsidian')
    return out


def build_daily_summary_from_note(note_text: str) -> str:
    sections = extract_sections(note_text)
    titles, problems, key_points, conclusions, links, tags = [], [], [], [], [], []
    for s in sections:
        f = parse_structured_fields(s['body'])
        titles.append(s['title'])
        if f['problem']:
            problems.append(f['problem'])
        if f['key_points']:
            key_points.append(f['key_points'])
        if f['conclusion']:
            conclusions.append(f['conclusion'])
        links.extend(extract_wikilinks(f.get('links', '')))
        links.extend(extract_inline_paths(f.get('links', '')))
        tags.extend(parse_tags_text(f.get('tags', '')))
        tags.extend(parse_tags_text(s['body']))

    if not titles:
        loose = extract_loose_bullets(note_text)
        major = '；'.join(loose[:3]) if loose else '当天暂无可提炼的结构化工作记录。'
        return (
            '## 今日总结\n\n'
            f'- 今日主要事项：{major}\n'
            f'- 核心解决的问题：无\n'
            f'- 关键点：无\n'
            f'- 结论/产出：无\n'
            f'- 相关文档：无\n'
            f'- 标签：无'
        )

    tags.extend(derive_tags_from_title(' '.join(titles)))
    return (
        '## 今日总结\n\n'
        f"- 今日主要事项：{'；'.join(titles[:3])}\n"
        f"- 核心解决的问题：{'；'.join(problems[:3]) if problems else '无'}\n"
        f"- 关键点：{'；'.join(key_points[:3]) if key_points else '无'}\n"
        f"- 结论/产出：{'；'.join(conclusions[:3]) if conclusions else '无'}\n"
        f"- 相关文档：{render_link_line(links)}\n"
        f"- 标签：{render_tags_line(tags)}"
    )


def normalize_bucket(title: str) -> str:
    t = title.lower()
    if 'summary' in t or '日报' in t or '周报' in t or '复盘' in t:
        return '总结与复盘能力'
    if '导入' in title or '迁移' in title or '数据库' in title:
        return '导入链路与数据库修复'
    if 'obsidian' in t or 'skill' in t:
        return 'Obsidian 与 Skill 工作流'
    return title


def uniq_keep_order(seq: List[str]) -> List[str]:
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def gather_week_items(config: Dict[str, Any], week_start: dt.date, week_end: dt.date) -> Dict[str, Dict]:
    buckets: Dict[str, Dict] = {}
    for i in range(7):
        d = week_start + dt.timedelta(days=i)
        path = daily_path(config, d)
        try:
            text = read_note(config, path)
        except Exception:
            continue

        sections = extract_sections(text)
        if sections:
            for sec in sections:
                fields = parse_structured_fields(sec['body'])
                bucket = normalize_bucket(sec['title'])
                item = buckets.setdefault(bucket, {'title': bucket, 'dates': [], 'problems': [], 'key_points': [], 'conclusions': [], 'links': [], 'tags': []})
                item['dates'].append(d.isoformat())
                if fields['problem']:
                    item['problems'].append(fields['problem'])
                if fields['key_points']:
                    item['key_points'].append(fields['key_points'])
                if fields['conclusion']:
                    item['conclusions'].append(fields['conclusion'])
                item['links'].extend(extract_wikilinks(fields.get('links', '')))
                item['links'].extend(extract_inline_paths(fields.get('links', '')))
                item['tags'].extend(parse_tags_text(fields.get('tags', '')))
                item['tags'].extend(parse_tags_text(sec['body']))
                item['tags'].extend(derive_tags_from_title(sec['title']))
        else:
            loose = extract_loose_bullets(text)
            if loose:
                bucket = '零散工作记录'
                item = buckets.setdefault(bucket, {'title': bucket, 'dates': [], 'problems': [], 'key_points': [], 'conclusions': [], 'links': [], 'tags': []})
                item['dates'].append(d.isoformat())
                item['key_points'].extend(loose[:5])

    buckets.pop('总结提炼', None)
    return buckets


def build_weekly_report(week_start: dt.date, week_end: dt.date, items: Dict[str, Dict]) -> str:
    ranked = sorted(items.values(), key=lambda x: (len(set(x['dates'])), len(x['problems']) + len(x['key_points']) + len(x['conclusions'])), reverse=True)
    body_lines = [f'# 周报 - {week_end.isoformat()}', '', START, '## 本周重点事项（按复杂度 / 投入度排序）', '']
    if not ranked:
        body_lines += ['- 本周暂无可提炼的结构化日报内容。', END, '']
    else:
        for idx, item in enumerate(ranked, 1):
            dates = '、'.join(sorted(set(item['dates'])))
            problems = '；'.join(uniq_keep_order(item['problems'])[:3]) or '无'
            key_points = '；'.join(uniq_keep_order(item['key_points'])[:4]) or '无'
            conclusions = '；'.join(uniq_keep_order(item['conclusions'])[:3]) or '无'
            body_lines += [
                f'### {idx}. {item["title"]}',
                f'- 涉及日期：{dates}',
                f'- 核心解决的问题：{problems}',
                f'- 关键点：{key_points}',
                f'- 结论/产出：{conclusions}',
                f'- 相关文档：{render_link_line(uniq_keep_order(item["links"]))}',
                f'- 标签：{render_tags_line(item["tags"])}',
                ''
            ]
        overall = '；'.join([f'本周主要推进了 {x["title"]}' for x in ranked[:3]])
        body_lines += ['## 本周总体结论', f'- {overall}', END, '']
    body = '\n'.join(body_lines)
    word_count = len(re.findall(r'\S+', body))
    frontmatter = '\n'.join([
        '---',
        f'word_count: {word_count}',
        'type: weekly-summary',
        f'week_start: {week_start.isoformat()}',
        f'week_end: {week_end.isoformat()}',
        '---',
        ''
    ])
    return frontmatter + body + ('\n' if not body.endswith('\n') else '')


def build_entry_block(args) -> str:
    now = args.time or dt.datetime.now().strftime('%H:%M')
    title = clean_title(args.title)
    tags = [x.strip().lstrip('#') for x in (args.tags or '').split(',') if x.strip()]
    tags.extend(derive_tags_from_title(title))
    links = [x.strip() for x in (args.links or '').split(',') if x.strip()]
    return (
        f'#### {title} — {now}\n\n'
        f'- **问题**: {args.problem}\n'
        f'- **方案**: {args.solution}\n'
        f'- **结论**: {args.conclusion}\n'
        f'- **关键点**: {args.key_points}\n'
        f'- **关联**: {render_link_line(links)}\n'
        f'- **标签**: {render_tags_line(tags)}\n'
    )


def cmd_append_entry(args):
    config = load_config(args)
    date = parse_date(args.date) if args.date else dt.date.today()
    path = daily_path(config, date)
    ensure_note(config, path, f'# {date.isoformat()}\n')
    block = build_entry_block(args)
    existing = read_note(config, path)
    updated = insert_before_summary_block(existing, block)
    create_or_overwrite_note(config, path, updated)
    print(path)


def cmd_refresh_daily_auto(args):
    config = load_config(args)
    date = parse_date(args.date) if args.date else dt.date.today()
    path = daily_path(config, date)
    ensure_note(config, path, f'# {date.isoformat()}\n')
    existing = read_note(config, path)
    summary = build_daily_summary_from_note(existing)
    updated = replace_summary_block(existing, summary)
    create_or_overwrite_note(config, path, updated)
    print(path)


def cmd_generate_weekly_auto(args):
    config = load_config(args)
    anchor = dt.date.today() - dt.timedelta(days=7) if args.mode == 'last-week' else (parse_date(args.date) if args.date else dt.date.today())
    week_start, week_end = monday_for(anchor), sunday_for(anchor)
    items = gather_week_items(config, week_start, week_end)
    content = build_weekly_report(week_start, week_end, items)
    create_or_overwrite_note(config, weekly_path(config, week_end), content)
    print(weekly_path(config, week_end))


def add_common_args(p):
    p.add_argument('--config')
    p.add_argument('--obsidian-bin')
    p.add_argument('--vault')
    p.add_argument('--daily-dir')
    p.add_argument('--weekly-dir')


def main():
    ap = argparse.ArgumentParser(description='Generate Obsidian daily and weekly review notes from existing markdown content.')
    sub = ap.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('append-entry', help='Append a structured session entry into a daily note.')
    add_common_args(a)
    a.add_argument('--date')
    a.add_argument('--time')
    a.add_argument('--title', required=True)
    a.add_argument('--problem', required=True)
    a.add_argument('--solution', required=True)
    a.add_argument('--conclusion', required=True)
    a.add_argument('--key-points', required=True)
    a.add_argument('--links', default='')
    a.add_argument('--tags', default='')
    a.set_defaults(func=cmd_append_entry)

    d = sub.add_parser('refresh-daily-auto', help='Read a daily note and regenerate its summary block.')
    add_common_args(d)
    d.add_argument('--date')
    d.set_defaults(func=cmd_refresh_daily_auto)

    w = sub.add_parser('generate-weekly-auto', help='Read daily notes for a week and generate a weekly report grouped by work item.')
    add_common_args(w)
    w.add_argument('--mode', choices=['current', 'last-week'], default='current')
    w.add_argument('--date')
    w.set_defaults(func=cmd_generate_weekly_auto)

    args = ap.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
