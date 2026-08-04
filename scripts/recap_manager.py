#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import List, Dict, Any

DEFAULTS = {
    "obsidian_bin": "obsidian",
    "vault": "default",
    "vault_path": "",
    "daily_dir": "Memory/daily",
    "weekly_dir": "Memory/weekly",
    "index_dir": "Memory/index",
}
START = "<!-- AI_SUMMARY_START -->"
END = "<!-- AI_SUMMARY_END -->"
# How many 今日重点 items get the numbered, longer-outcome treatment. Items beyond
# this still appear with their own outcome, just more tersely.
DAILY_SUMMARY_HIGHLIGHT_LIMIT = 8
ALLOWED_EXTS = {"md", "py", "ts", "tsx", "js", "json", "yaml", "yml"}
ERROR_PREFIX_RE = re.compile(r'^\s*Error:\s+', re.M)
FILE_NOT_FOUND_RE = re.compile(r'^\s*Error:\s+File\s+".*?"\s+not found\.\s*$', re.M)


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
    for key in ('obsidian_bin', 'vault', 'vault_path', 'daily_dir', 'weekly_dir', 'index_dir'):
        val = getattr(cli_args, key, None)
        if val:
            config[key] = val
    return config


def resolve_vault_root(config: Dict[str, Any]) -> Path | None:
    configured = str(config.get('vault_path') or '').strip()
    if configured:
        return Path(configured).expanduser()

    vault = config['vault']
    home = Path.home()
    candidates = [
        home / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents" / vault,
        home / "Documents" / vault,
        home / "Obsidian" / vault,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def note_fs_path(config: Dict[str, Any], path: str) -> Path | None:
    root = resolve_vault_root(config)
    if root is None:
        return None
    return root / Path(path)


@contextmanager
def note_lock(config: Dict[str, Any], path: str):
    fs_path = note_fs_path(config, path)
    if fs_path is None:
        yield
        return
    fs_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = fs_path.with_suffix(fs_path.suffix + ".lock")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def is_error_output(text: str) -> bool:
    stripped = text.strip()
    return bool(ERROR_PREFIX_RE.match(stripped)) or stripped.startswith('Vault not found.')


def strip_known_cli_noise(text: str) -> str:
    cleaned = FILE_NOT_FOUND_RE.sub('', text)
    return cleaned.lstrip('\n')


def run_obsidian(config: Dict[str, Any], args: List[str]) -> str:
    cmd = [config['obsidian_bin'], *args]
    res = subprocess.run(cmd, capture_output=True, text=True)
    output = res.stdout or ''
    if res.returncode != 0 or is_error_output(output):
        raise RuntimeError(res.stderr.strip() or res.stdout.strip() or f"command failed: {' '.join(cmd)}")
    return output


def has_obsidian_cli(config: Dict[str, Any]) -> bool:
    cmd = str(config.get('obsidian_bin') or 'obsidian').strip() or 'obsidian'
    if '/' in cmd:
        return Path(cmd).exists()
    return shutil.which(cmd) is not None


def is_missing_note_error(error: Exception) -> bool:
    text = str(error)
    return 'not found' in text.lower()


def read_note(config: Dict[str, Any], path: str) -> str:
    fs_path = note_fs_path(config, path)
    if has_obsidian_cli(config):
        try:
            return strip_known_cli_noise(run_obsidian(config, [f"vault={config['vault']}", 'read', f'path={path}']))
        except Exception as exc:
            if fs_path is not None and fs_path.exists():
                return strip_known_cli_noise(fs_path.read_text())
            if is_missing_note_error(exc):
                raise FileNotFoundError(path)
            if fs_path is not None:
                raise RuntimeError(f"obsidian read failed for {path}: {exc}") from exc
            raise
    if fs_path is not None:
        if fs_path.exists():
            return strip_known_cli_noise(fs_path.read_text())
        raise FileNotFoundError(path)
    raise RuntimeError(f"obsidian CLI unavailable and vault path is unresolved for {path}")


def create_or_overwrite_note(config: Dict[str, Any], path: str, content: str) -> None:
    fs_path = note_fs_path(config, path)
    if has_obsidian_cli(config):
        try:
            run_obsidian(config, [f"vault={config['vault']}", 'create', f'path={path}', f'content={content}', 'overwrite'])
            return
        except Exception:
            if fs_path is None:
                raise
    if fs_path is not None:
        fs_path.parent.mkdir(parents=True, exist_ok=True)
        fs_path.write_text(content)
        return
    raise RuntimeError(f"obsidian CLI unavailable and vault path is unresolved for {path}")


def append_note(config: Dict[str, Any], path: str, content: str) -> None:
    fs_path = note_fs_path(config, path)
    if has_obsidian_cli(config):
        try:
            run_obsidian(config, [f"vault={config['vault']}", 'append', f'path={path}', f'content={content}'])
            return
        except Exception:
            if fs_path is None:
                raise
    if fs_path is not None:
        fs_path.parent.mkdir(parents=True, exist_ok=True)
        existing = fs_path.read_text() if fs_path.exists() else ''
        fs_path.write_text(existing + content)
        return
    raise RuntimeError(f"obsidian CLI unavailable and vault path is unresolved for {path}")


def insert_before_summary_block(original: str, entry_block: str) -> str:
    region = find_generated_region(original)
    if region:
        start_idx, end_idx = region
        before = original[:start_idx].rstrip()
        summary_region = original[start_idx:end_idx]
        m = re.search(re.escape(START) + r'.*?' + re.escape(END), summary_region, flags=re.S)
        summary = (m.group(0) if m else '').lstrip()
        if not summary:
            return before + '\n\n' + entry_block.strip() + '\n'
        return before + '\n\n' + entry_block.strip() + '\n\n' + summary
    return original.rstrip() + '\n\n' + entry_block.strip() + '\n'


def ensure_note(config: Dict[str, Any], path: str, content: str = '') -> None:
    try:
        read_note(config, path)
    except Exception:
        create_or_overwrite_note(config, path, content)


def split_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
    text = strip_known_cli_noise(text)
    m = re.match(r'^---\n(.*?)\n---\n?', text, flags=re.S)
    if not m:
        return {}, text

    meta: Dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ':' not in line:
            continue
        key, raw = line.split(':', 1)
        key = key.strip()
        value = raw.strip()
        if value.startswith('[') and value.endswith(']'):
            inner = value[1:-1].strip()
            meta[key] = [x.strip().strip('"').strip("'") for x in inner.split(',') if x.strip()]
        elif re.fullmatch(r'"[^"]*"', value):
            meta[key] = value[1:-1]
        elif re.fullmatch(r'\d+', value):
            meta[key] = int(value)
        else:
            meta[key] = value
    return meta, text[m.end():]


def format_frontmatter_value(value: Any) -> str:
    if isinstance(value, list):
        if not value:
            return '[]'
        return '[' + ', '.join(str(x) for x in value) + ']'
    return str(value)


def render_note(meta: Dict[str, Any], body: str) -> str:
    ordered_keys = ['title', 'date', 'created', 'type', 'week_start', 'week_end', 'author', 'word_count', 'tags']
    lines = ['---']
    emitted = set()
    for key in ordered_keys:
        if key in meta and meta[key] not in (None, '', []):
            lines.append(f'{key}: {format_frontmatter_value(meta[key])}')
            emitted.add(key)
    for key in sorted(meta.keys()):
        if key in emitted or meta[key] in (None, '', []):
            continue
        lines.append(f'{key}: {format_frontmatter_value(meta[key])}')
    lines.append('---')
    lines.append('')
    return '\n'.join(lines) + body.rstrip() + '\n'


def count_body_chars(text: str) -> int:
    return len(text)


def ensure_daily_heading(body: str, date: dt.date) -> str:
    stripped = body.lstrip()
    heading = f'# {date.isoformat()}'
    if not stripped:
        return heading + '\n'
    if re.match(rf'^#\s+{re.escape(date.isoformat())}\s*$', stripped.splitlines()[0]):
        return stripped.rstrip() + '\n'
    return heading + '\n\n' + stripped.rstrip() + '\n'


def collect_note_tags(text: str, extra_tags: List[str] | None = None) -> List[str]:
    tags = parse_tags_text(text)
    if extra_tags:
        tags.extend(extra_tags)
    return uniq_keep_order(tags)


def normalize_daily_note(text: str, date: dt.date) -> str:
    meta, body = split_frontmatter(text)
    body = ensure_daily_heading(strip_known_cli_noise(body), date)
    tags = collect_note_tags(body, meta.get('tags') if isinstance(meta.get('tags'), list) else None)
    meta['date'] = date.isoformat()
    meta['type'] = meta.get('type') or 'daily'
    meta['word_count'] = count_body_chars(body)
    if tags:
        meta['tags'] = tags
    elif 'tags' in meta:
        del meta['tags']
    return render_note(meta, body)


def strip_frontmatter(text: str) -> str:
    return re.sub(r'^---\n.*?\n---\n', '', strip_known_cli_noise(text), flags=re.S).strip()


def remove_generated_block(text: str) -> str:
    return re.sub(re.escape(START) + r'.*?' + re.escape(END), '', text, flags=re.S).strip()


def find_generated_region(text: str) -> tuple[int, int] | None:
    first_start = text.find(START)
    if first_start == -1:
        return None
    last_end = text.rfind(END)
    if last_end == -1 or last_end < first_start:
        return first_start, len(text)
    return first_start, last_end + len(END)


def replace_summary_block(original: str, new_block: str) -> str:
    wrapped = f"{START}\n{new_block.strip()}\n{END}"
    region = find_generated_region(original)
    if region:
        start_idx, end_idx = region
        before = original[:start_idx].rstrip()
        after = original[end_idx:].strip()
        parts = [part for part in (before, wrapped, after) if part]
        return "\n\n".join(parts).rstrip() + "\n"
    sep = "\n\n" if original.rstrip() else ""
    return original.rstrip() + sep + wrapped + "\n"


def parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def sunday_for(date: dt.date) -> dt.date:
    return date + dt.timedelta(days=(6 - date.weekday()))


def monday_for(date: dt.date) -> dt.date:
    return date - dt.timedelta(days=date.weekday())


def daily_path(config: Dict[str, Any], date: dt.date) -> str:
    base = config['daily_dir'].rstrip('/')
    return f"{base}/{date.year}/{date.month:02d}/{date.isoformat()}.md"


def weekly_path(config: Dict[str, Any], sunday: dt.date) -> str:
    base = config['weekly_dir'].rstrip('/')
    return f"{base}/{sunday.year}/{sunday.month:02d}/{sunday.isoformat()}.md"


def base_index_path(config: Dict[str, Any], kind: str) -> str:
    base = config['index_dir'].rstrip('/')
    if kind == 'daily':
        return f"{base}/Daily Notes.base"
    if kind == 'weekly':
        return f"{base}/Weekly Reports.base"
    raise ValueError(f"unknown base kind: {kind}")


def extract_wikilinks(text: str) -> List[str]:
    return sorted(set(re.findall(r'\[\[([^\]]+)\]\]', text)))


def parse_tags_text(text: str) -> List[str]:
    tags = re.findall(r'#([A-Za-z0-9_/\-\u4e00-\u9fff]+)', text)
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
    labels = {
        '问题': 'problem',
        '方案': 'solution',
        '结论': 'conclusion',
        '关键点': 'key_points',
        '关联': 'links',
        '结果': 'conclusion',
        '处理': 'solution',
        '要点': 'key_points',
        '文档': 'links',
        '标签': 'tags',
    }
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


def render_link_line(links: List[str], limit: int = 8) -> str:
    links = sorted(set(links))[:limit]
    if not links:
        return '无'
    rendered = []
    for x in links:
        rendered.append(f'[[{x}]]' if not x.startswith('[[') else x)
    return ' · '.join(rendered)


def render_tags_line(tags: List[str], limit: int = 6) -> str:
    tags = uniq_keep_order(tags)
    if not tags:
        return '无'
    return ' '.join(f'#{t}' for t in tags[:limit])


def squash_spaces(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()


def shorten_text(text: str, limit: int = 76) -> str:
    """Shorten text to limit, always marking that something was dropped.

    Truncation must never be silent. Cutting at a clause separator used to
    return the head with no marker, so a line that lost its second half still
    read as a complete sentence: "根因是 X 只对 Path B 生效" looked final while the
    part describing the actual fix was gone. A reviewer cannot recover what they
    cannot see was removed, so every truncated result now ends with an ellipsis.
    """
    text = squash_spaces(text)
    if len(text) <= limit:
        return text
    threshold = max(24, limit // 2)
    punct_cut = max(text.rfind(sep, 0, limit) for sep in ('；', '。', '，', '、', ',', ';', '.'))
    if punct_cut >= threshold:
        return text[:punct_cut].rstrip('；。，,;. ') + '…'
    space_cut = text.rfind(' ', 0, limit)
    if space_cut >= threshold:
        return text[:space_cut].rstrip('；。，,;. ') + '…'
    return text[:limit].rstrip('；。，,;. ') + '…'


def split_atomic_points(text: str) -> List[str]:
    text = squash_spaces(text)
    if not text:
        return []
    parts = re.split(r'[；;。]\s*', text)
    return [p.strip(' ，,') for p in parts if p.strip(' ，,')]


def is_low_value_decision(point: str) -> bool:
    low_signal_terms = (
        'dirty tree',
        '测试',
        '验证',
        '提交',
        '推送',
        'git ',
        'GitLab',
        'GitHub',
        'Playwright',
        'npm ',
        'node ',
        'build',
    )
    return any(term in point for term in low_signal_terms)


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
    work_items, key_points, links, tags = [], [], [], []
    for s in sections:
        f = parse_structured_fields(s['body'])
        work_items.append({
            'title': s['title'],
            'problem': f['problem'],
            'solution': f['solution'],
            'conclusion': f['conclusion'],
        })
        if f['key_points']:
            key_points.extend(split_atomic_points(f['key_points']))
        links.extend(extract_wikilinks(f.get('links', '')))
        links.extend(extract_inline_paths(f.get('links', '')))
        tags.extend(parse_tags_text(f.get('tags', '')))
        tags.extend(parse_tags_text(s['body']))

    if not work_items:
        loose = extract_loose_bullets(note_text)
        major = loose[:3] if loose else ['当天暂无可提炼的结构化工作记录。']
        return (
            '## 今日总结\n\n'
            '### 今日重点\n'
            + '\n'.join(f'- {shorten_text(item, 88)}' for item in major)
            + '\n\n### 关键判断\n'
            '- 无\n\n'
            '### 文档与标签\n'
            '- 文档：无\n'
            '- 标签：无'
        )

    tags.extend(derive_tags_from_title(' '.join(item['title'] for item in work_items)))
    # Every work item keeps its outcome. The old shape kept 5 items and collapsed the
    # rest into a titles-only "其余事项" blob, so on a busy day a genuinely major item
    # (e.g. a P0 that bricked sessions) was reduced to a bare noun phrase with no
    # result — the one thing a daily review needs. Overflow items now get their own
    # line with a shorter outcome, which keeps the section scannable without hiding
    # what any of them actually achieved.
    summary_items = work_items[:DAILY_SUMMARY_HIGHLIGHT_LIMIT]
    lines = ['## 今日总结', '', '### 今日重点']
    for idx, item in enumerate(summary_items, 1):
        result = item['conclusion'] or item['solution'] or item['problem'] or '已记录该事项。'
        lines.append(f'{idx}. **{item["title"]}**：{shorten_text(result, 84)}')
    for item in work_items[len(summary_items):]:
        result = item['conclusion'] or item['solution'] or item['problem'] or '已记录该事项。'
        lines.append(f'- **{item["title"]}**：{shorten_text(result, 56)}')

    strong_decisions = [point for point in uniq_keep_order(key_points) if not is_low_value_decision(point)]
    decisions = (strong_decisions or uniq_keep_order(key_points))[:3]
    lines.extend(['', '### 关键判断'])
    if decisions:
        lines.extend(f'- {shorten_text(point, 88)}' for point in decisions)
    else:
        lines.append('- 无')

    lines.extend([
        '',
        '### 文档与标签',
        f'- 文档：{render_link_line(links, limit=5)}',
        f'- 标签：{render_tags_line(tags, limit=8)}',
    ])
    return '\n'.join(lines)


def normalize_bucket(title: str) -> str:
    t = title.lower()
    if 'team sharing' in t or 'team-sharing' in t:
        return 'Team Sharing'
    if 'share pages' in t or 'share-pages' in t:
        return 'Share Pages'
    if 'knowledge space' in t:
        return 'Knowledge Space'
    if 'summary' in t or '日报' in t or '周报' in t or '复盘' in t:
        return '总结与复盘能力'
    if '导入' in title or '迁移' in title or '数据库' in title:
        return '导入链路与数据库修复'
    if 'obsidian' in t and 'skill' in t:
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
            problems = '；'.join(uniq_keep_order(item['problems'])[:3])
            key_points = '；'.join(uniq_keep_order(item['key_points'])[:4]) or '无'
            conclusions = '；'.join(uniq_keep_order(item['conclusions'])[:3]) or '无'
            body_lines.append(f'### {idx}. {item["title"]}')
            body_lines.append(f'- 涉及日期：{dates}')
            # Omit rather than assert 无. Only a 问题 line can populate this, so a daily
            # entry written without --problem used to render "核心解决的问题：无", which
            # reads as "this solved nothing" instead of "nobody recorded it". Never
            # backfill it from 处理/结果 either — the fix is not the problem.
            if problems:
                body_lines.append(f'- 核心解决的问题：{problems}')
            body_lines += [
                f'- 关键点：{key_points}',
                f'- 结论/产出：{conclusions}',
                f'- 相关文档：{render_link_line(uniq_keep_order(item["links"]))}',
                f'- 标签：{render_tags_line(item["tags"])}',
                ''
            ]
        overall = '；'.join([f'本周主要推进了 {x["title"]}' for x in ranked[:3]])
        body_lines += ['## 本周总体结论', f'- {overall}', END, '']
    body = '\n'.join(body_lines)
    meta = {
        'type': 'weekly-summary',
        'week_start': week_start.isoformat(),
        'week_end': week_end.isoformat(),
        'word_count': count_body_chars(body),
    }
    tags = uniq_keep_order([tag for item in ranked for tag in item.get('tags', [])])
    if tags:
        meta['tags'] = tags[:12]
    return render_note(meta, body)


def iter_weekly_modules(note_text: str) -> List[Dict[str, str]]:
    body = strip_frontmatter(note_text)
    region = find_generated_region(body)
    if region:
        start_idx, end_idx = region
        body = body[start_idx + len(START):end_idx - len(END)]
    modules: List[Dict[str, str]] = []
    current: Dict[str, str] | None = None
    for line in body.splitlines():
        heading = re.match(r'^###\s+(?:\d+\.\s*)?(.+?)\s*$', line.strip())
        if heading:
            if current:
                modules.append(current)
            current = {'title': heading.group(1).strip(), 'body': ''}
            continue
        if current is not None:
            current['body'] += line + '\n'
    if current:
        modules.append(current)
    return modules


def parse_weekly_module_fields(body: str) -> Dict[str, str]:
    labels = {
        '涉及日期': 'dates',
        '核心解决的问题': 'problem',
        '关键点': 'key_points',
        '结论/产出': 'conclusion',
        '相关文档': 'links',
        '标签': 'tags',
    }
    out = {v: '' for v in labels.values()}
    for line in body.splitlines():
        m = re.match(r'^-\s+(.+?)：\s*(.*)$', line.strip())
        if m and m.group(1) in labels:
            out[labels[m.group(1)]] = m.group(2).strip()
    return out


def build_weekly_brief_from_note(note_text: str, limit: int = 7) -> str:
    modules = iter_weekly_modules(note_text)
    lines = ['上周']
    if not modules:
        return '上周\n1. 工作复盘：暂无可提炼的结构化周报内容。'

    for idx, module in enumerate(modules[:limit], 1):
        fields = parse_weekly_module_fields(module['body'])
        sentence = fields['conclusion'] or fields['problem'] or fields['key_points'] or '完成了相关事项整理与沉淀。'
        sentence = shorten_text(sentence, 72)
        lines.append(f'{idx}. {module["title"]}：{sentence}。')
    return '\n'.join(lines)


def build_base_index_content(kind: str) -> str:
    if kind == 'daily':
        return """filters:
  and:
    - 'type == "daily"'
    - 'file.ext == "md"'

properties:
  date:
    displayName: Date
  word_count:
    displayName: "Body Characters"
  tags:
    displayName: Tags

views:
  - type: table
    name: "Recent Daily Notes"
    limit: 60
    order:
      - file.name
      - date
      - word_count
      - tags
      - file.mtime
    groupBy:
      property: date
      direction: DESC
"""
    if kind == 'weekly':
        return """filters:
  and:
    - 'type == "weekly-summary"'
    - 'file.ext == "md"'

properties:
  week_start:
    displayName: "Week Start"
  week_end:
    displayName: "Week End"
  word_count:
    displayName: "Body Characters"
  tags:
    displayName: Tags

views:
  - type: table
    name: "Weekly Reports"
    limit: 52
    order:
      - file.name
      - week_start
      - week_end
      - word_count
      - tags
      - file.mtime
    groupBy:
      property: week_end
      direction: DESC
"""
    raise ValueError(f"unknown base kind: {kind}")


def build_entry_block(args) -> str:
    """Render one raw session entry.

    The 问题 line must be written even though it makes the entry one line longer.
    parse_structured_fields already maps 问题 back to `problem`, and both the daily
    refresh and the weekly builder read that field — but nothing ever wrote it, so
    `--problem` was a required argument whose value went nowhere, and every weekly
    module built from daily notes reported 核心解决的问题：无. Persisting it makes the
    append → refresh → weekly round trip lossless, which is the whole point of the
    entry layer being source material rather than a one-off summary.
    """
    now = args.time or dt.datetime.now().strftime('%H:%M')
    title = clean_title(args.title)
    tags = [x.strip().lstrip('#') for x in (args.tags or '').split(',') if x.strip()]
    tags.extend(derive_tags_from_title(title))
    links = [x.strip() for x in (args.links or '').split(',') if x.strip()]
    key_points = '；'.join(split_atomic_points(args.key_points)[:2]) or args.key_points
    lines = [f'#### {title} — {now}', '', f'- **结果**: {shorten_text(args.conclusion, 120)}']
    problem = squash_spaces(args.problem or '')
    if problem:
        lines.append(f'- **问题**: {shorten_text(problem, 132)}')
    lines.extend([
        f'- **处理**: {shorten_text(args.solution, 140)}',
        f'- **要点**: {shorten_text(key_points, 140)}',
        f'- **文档**: {render_link_line(links, limit=3)}',
        f'- **标签**: {render_tags_line(tags, limit=3)}',
    ])
    return '\n'.join(lines) + '\n'


def cmd_append_entry(args):
    config = load_config(args)
    date = parse_date(args.date) if args.date else dt.date.today()
    path = daily_path(config, date)
    with note_lock(config, path):
        try:
            existing = read_note(config, path)
        except Exception:
            existing = ''
        normalized = normalize_daily_note(existing, date)
        updated = insert_before_summary_block(normalized, build_entry_block(args))
        create_or_overwrite_note(config, path, normalize_daily_note(updated, date))
    print(path)


def cmd_refresh_daily_auto(args):
    config = load_config(args)
    date = parse_date(args.date) if args.date else dt.date.today()
    path = daily_path(config, date)
    with note_lock(config, path):
        try:
            existing = read_note(config, path)
        except Exception:
            existing = ''
        normalized = normalize_daily_note(existing, date)
        summary = build_daily_summary_from_note(normalized)
        updated = replace_summary_block(normalized, summary)
        create_or_overwrite_note(config, path, normalize_daily_note(updated, date))
    print(path)


def cmd_generate_weekly_auto(args):
    config = load_config(args)
    anchor = dt.date.today() - dt.timedelta(days=7) if args.mode == 'last-week' else (parse_date(args.date) if args.date else dt.date.today())
    week_start, week_end = monday_for(anchor), sunday_for(anchor)
    items = gather_week_items(config, week_start, week_end)
    content = build_weekly_report(week_start, week_end, items)
    path = weekly_path(config, week_end)
    with note_lock(config, path):
        create_or_overwrite_note(config, path, content)
    print(path)


def cmd_print_weekly_brief(args):
    config = load_config(args)
    if args.path:
        path = args.path
    else:
        anchor = dt.date.today() - dt.timedelta(days=7) if args.mode == 'last-week' else (parse_date(args.date) if args.date else dt.date.today())
        path = weekly_path(config, sunday_for(anchor))
    note = read_note(config, path)
    print(build_weekly_brief_from_note(note, limit=args.limit))


def cmd_verify_note(args):
    config = load_config(args)
    text = read_note(config, args.path)
    meta, body = split_frontmatter(text)
    actual = count_body_chars(body)
    stored_raw = meta.get('word_count')
    try:
        stored = int(stored_raw)
    except Exception:
        stored = None
    marker_ok = text.count(START) == text.count(END)
    fixed = False
    if args.fix and (stored != actual or not stored_raw):
        meta['word_count'] = actual
        create_or_overwrite_note(config, args.path, render_note(meta, body))
        stored = actual
        fixed = True
    result = {
        'path': args.path,
        'word_count': stored,
        'actual_word_count': actual,
        'word_count_ok': stored == actual,
        'summary_markers_ok': marker_ok,
        'fixed': fixed,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def cmd_ensure_index_base(args):
    config = load_config(args)
    kinds = ['daily', 'weekly'] if args.kind == 'all' else [args.kind]
    written = []
    for kind in kinds:
        path = base_index_path(config, kind)
        content = build_base_index_content(kind)
        create_or_overwrite_note(config, path, content)
        written.append(path)
    print('\n'.join(written))


def add_common_args(p):
    p.add_argument('--config')
    p.add_argument('--obsidian-bin')
    p.add_argument('--vault')
    p.add_argument('--vault-path')
    p.add_argument('--daily-dir')
    p.add_argument('--weekly-dir')
    p.add_argument('--index-dir')


def main():
    ap = argparse.ArgumentParser(description='Generate Obsidian daily and weekly review notes from existing markdown content.')
    sub = ap.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('append-entry', help='Append a structured session entry into a daily note.')
    add_common_args(a)
    a.add_argument('--date')
    a.add_argument('--time')
    a.add_argument('--title', required=True)
    a.add_argument(
        '--problem',
        default='',
        help='What was broken. Rendered as a 问题 line and is the only source for the '
             'weekly 核心解决的问题 field. Omit for a terser five-line entry.',
    )
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

    b = sub.add_parser('print-weekly-brief', help='Read a weekly report and print a concise group-sendable version.')
    add_common_args(b)
    b.add_argument('--mode', choices=['current', 'last-week'], default='current')
    b.add_argument('--date')
    b.add_argument('--path')
    b.add_argument('--limit', type=int, default=7)
    b.set_defaults(func=cmd_print_weekly_brief)

    v = sub.add_parser('verify-note', help='Verify a note word_count and generated summary markers.')
    add_common_args(v)
    v.add_argument('--path', required=True)
    v.add_argument('--fix', action='store_true')
    v.set_defaults(func=cmd_verify_note)

    ib = sub.add_parser('ensure-index-base', help='Create or refresh Obsidian Bases index files for daily and weekly notes.')
    add_common_args(ib)
    ib.add_argument('--kind', choices=['daily', 'weekly', 'all'], default='all')
    ib.set_defaults(func=cmd_ensure_index_base)

    args = ap.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
