#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from typing import List, Dict, Any

DEFAULTS = {
    "obsidian_bin": "obsidian",
    "vault": "default",
    "vault_path": "",
    "daily_dir": "Memory/daily",
    "weekly_dir": "Memory/weekly",
    "monthly_dir": "Memory/monthly",
    "monthly_note_suffix": "个人目标月度Review",
    "index_dir": "Memory/index",
    "queue_db": "",
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
    for key in ('obsidian_bin', 'vault', 'vault_path', 'daily_dir', 'weekly_dir', 'monthly_dir', 'index_dir', 'queue_db'):
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


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def queue_db_path(config: Dict[str, Any]) -> Path:
    configured = str(config.get('queue_db') or '').strip()
    path = (
        Path(configured).expanduser()
        if configured
        else Path.home() / '.local' / 'state' / 'conversation-recap' / 'queue.db'
    ).resolve()

    forbidden_roots = [Path(__file__).resolve().parent.parent]
    vault_root = resolve_vault_root(config)
    if vault_root is not None:
        forbidden_roots.append(vault_root.resolve())
    if any(path_is_within(path, root) for root in forbidden_roots):
        raise ValueError('queue_db must stay outside the vault and the skill repository')
    return path


def queue_connect(config: Dict[str, Any]) -> sqlite3.Connection:
    path = queue_db_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    init_lock_path = path.parent / 'queue-init.lock'
    conn: sqlite3.Connection | None = None
    with open(init_lock_path, 'w') as init_lock:
        fcntl.flock(init_lock.fileno(), fcntl.LOCK_EX)
        try:
            conn = sqlite3.connect(path, timeout=30)
            conn.execute('PRAGMA busy_timeout=30000')
            journal_mode = str(conn.execute('PRAGMA journal_mode').fetchone()[0]).lower()
            if journal_mode != 'wal':
                conn.execute('PRAGMA journal_mode=WAL')
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entry_queue (
                  id         INTEGER PRIMARY KEY AUTOINCREMENT,
                  status     TEXT NOT NULL DEFAULT 'pending',
                  date       TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  payload    TEXT NOT NULL,
                  error      TEXT
                )
                """
            )
            conn.commit()
        except Exception:
            if conn is not None:
                conn.close()
            raise
        finally:
            fcntl.flock(init_lock.fileno(), fcntl.LOCK_UN)
    assert conn is not None
    return conn


def lock_file_path(config: Dict[str, Any], path: str) -> Path:
    lock_dir = queue_db_path(config).parent / 'locks'
    lock_dir.mkdir(parents=True, exist_ok=True)
    vault_identity = str(resolve_vault_root(config) or config.get('vault') or 'default')
    digest = hashlib.sha256(f'{vault_identity}\0{path}'.encode()).hexdigest()[:20]
    safe_name = re.sub(r'[^A-Za-z0-9._-]+', '-', Path(path).name).strip('-') or 'note'
    return lock_dir / f'{safe_name}-{digest}.lock'


@contextmanager
def note_lock(config: Dict[str, Any], path: str):
    lock_path = lock_file_path(config, path)
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
    # Exact bytes first. Round-tripping long CJK notes through the Obsidian CLI was seen
    # (2026-09-02) to turn single characters into U+FFFD runs that grew on every pass; the
    # CLI stays as the fallback for vaults whose path cannot be resolved.
    if fs_path is not None and fs_path.exists():
        return strip_known_cli_noise(fs_path.read_text(encoding='utf-8'))
    if has_obsidian_cli(config):
        try:
            return strip_known_cli_noise(run_obsidian(config, [f"vault={config['vault']}", 'read', f'path={path}']))
        except Exception as exc:
            if fs_path is not None and fs_path.exists():
                return strip_known_cli_noise(fs_path.read_text(encoding='utf-8'))
            if is_missing_note_error(exc):
                raise FileNotFoundError(path)
            if fs_path is not None:
                raise RuntimeError(f"obsidian read failed for {path}: {exc}") from exc
            raise
    if fs_path is not None:
        if fs_path.exists():
            return strip_known_cli_noise(fs_path.read_text(encoding='utf-8'))
        raise FileNotFoundError(path)
    raise RuntimeError(f"obsidian CLI unavailable and vault path is unresolved for {path}")


def create_or_overwrite_note(config: Dict[str, Any], path: str, content: str) -> None:
    fs_path = note_fs_path(config, path)
    if fs_path is not None:
        fs_path.parent.mkdir(parents=True, exist_ok=True)
        fs_path.write_text(content, encoding='utf-8')
        return
    if has_obsidian_cli(config):
        try:
            run_obsidian(config, [f"vault={config['vault']}", 'create', f'path={path}', f'content={content}', 'overwrite'])
            return
        except Exception:
            if fs_path is None:
                raise
    if fs_path is not None:
        fs_path.parent.mkdir(parents=True, exist_ok=True)
        fs_path.write_text(content, encoding='utf-8')
        return
    raise RuntimeError(f"obsidian CLI unavailable and vault path is unresolved for {path}")


def read_note_direct(config: Dict[str, Any], path: str) -> str:
    fs_path = note_fs_path(config, path)
    if fs_path is None:
        raise RuntimeError(f"vault path is unresolved for direct read: {path}")
    if not fs_path.exists():
        raise FileNotFoundError(path)
    return strip_known_cli_noise(fs_path.read_text(encoding='utf-8'))


def write_note_direct(config: Dict[str, Any], path: str, content: str) -> None:
    fs_path = note_fs_path(config, path)
    if fs_path is None:
        raise RuntimeError(f"vault path is unresolved for direct write: {path}")
    fs_path.parent.mkdir(parents=True, exist_ok=True)
    fs_path.write_text(content, encoding='utf-8')


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
    last_key = None
    for line in m.group(1).splitlines():
        # YAML block list continuation (`tags:` followed by `  - item` lines). Losing these
        # on a rewrite silently strips a reviewed note's tags, so fold them into the list.
        item = re.match(r'^\s+-\s+(.*)$', line)
        if item and last_key is not None and meta.get(last_key) in ('', None) or (item and isinstance(meta.get(last_key), list)):
            if not isinstance(meta.get(last_key), list):
                meta[last_key] = []
            meta[last_key].append(item.group(1).strip().strip('"').strip("'"))
            continue
        if ':' not in line:
            continue
        key, raw = line.split(':', 1)
        key = key.strip()
        last_key = key
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
    ordered_keys = ['title', 'date', 'updated', 'created', 'type', 'week_start', 'week_end', 'period_start', 'period_end', 'project', 'status', 'source_daily_notes', 'author', 'word_count', 'tags']
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


def parse_month(s: str) -> tuple[int, int]:
    m = re.fullmatch(r'(\d{4})-(\d{1,2})', (s or '').strip())
    if not m:
        raise ValueError(f'month must look like YYYY-MM, got {s!r}')
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f'month out of range: {s!r}')
    return year, month


def month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    start = dt.date(year, month, 1)
    end = dt.date(year + (month == 12), (month % 12) + 1, 1) - dt.timedelta(days=1)
    return start, end


def monthly_path(config: Dict[str, Any], year: int, month: int, project: str = '') -> str:
    base = config['monthly_dir'].rstrip('/')
    suffix = config.get('monthly_note_suffix') or DEFAULTS['monthly_note_suffix']
    stem = f"{year}-{month:02d}_{project}{suffix}" if project else f"{year}-{month:02d}_{suffix}"
    return f"{base}/{year}/{month:02d}/{stem}.md"


def base_index_path(config: Dict[str, Any], kind: str) -> str:
    base = config['index_dir'].rstrip('/')
    if kind == 'daily':
        return f"{base}/Daily Notes.base"
    if kind == 'weekly':
        return f"{base}/Weekly Reports.base"
    if kind == 'monthly':
        return f"{base}/Monthly Reviews.base"
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
        '工作项': 'work_items',
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


def parse_work_items_arg(text: str) -> List[str]:
    return [item.strip() for item in (text or '').split(',') if item.strip()]


def parse_rendered_work_items(text: str) -> List[str]:
    items = []
    for item in re.split(r'\s+·\s+', text or ''):
        item = item.strip()
        if item.startswith('`') and item.endswith('`'):
            item = item[1:-1].strip()
        if item:
            items.append(item)
    return items


def render_work_items_line(items: List[str], limit: int = 8) -> str:
    rendered = []
    for item in uniq_keep_order(items)[:limit]:
        item = item.strip()
        if item.startswith('[[') and item.endswith(']]'):
            rendered.append(item)
        else:
            rendered.append(f'`{item.strip("`")}`')
    return ' · '.join(rendered) if rendered else '无'


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
    work_items, key_points, links, tags, referenced_work_items = [], [], [], [], []
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
        referenced_work_items.extend(parse_rendered_work_items(f.get('work_items', '')))
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

    lines.extend(['', '### 文档与标签'])
    if referenced_work_items:
        lines.append(f'- 工作项：{render_work_items_line(referenced_work_items, limit=8)}')
    lines.extend([
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
                item = buckets.setdefault(bucket, {'title': bucket, 'dates': [], 'problems': [], 'key_points': [], 'conclusions': [], 'links': [], 'work_items': [], 'tags': []})
                item['dates'].append(d.isoformat())
                if fields['problem']:
                    item['problems'].append(fields['problem'])
                if fields['key_points']:
                    item['key_points'].append(fields['key_points'])
                if fields['conclusion']:
                    item['conclusions'].append(fields['conclusion'])
                item['links'].extend(extract_wikilinks(fields.get('links', '')))
                item['links'].extend(extract_inline_paths(fields.get('links', '')))
                item['work_items'].extend(parse_rendered_work_items(fields.get('work_items', '')))
                item['tags'].extend(parse_tags_text(fields.get('tags', '')))
                item['tags'].extend(parse_tags_text(sec['body']))
                item['tags'].extend(derive_tags_from_title(sec['title']))
        else:
            loose = extract_loose_bullets(text)
            if loose:
                bucket = '零散工作记录'
                item = buckets.setdefault(bucket, {'title': bucket, 'dates': [], 'problems': [], 'key_points': [], 'conclusions': [], 'links': [], 'work_items': [], 'tags': []})
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
            ]
            referenced_work_items = item.get('work_items', [])
            if referenced_work_items:
                body_lines.append(f'- 相关工作项：{render_work_items_line(referenced_work_items)}')
            body_lines += [f'- 标签：{render_tags_line(item["tags"])}', '']
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


MONTHLY_ALIGNMENT_HEADING = '## 与上月计划的对齐'
MONTHLY_REQUIRED_HEADINGS = [
    '## 本月目标',
    '## 本月目标完成情况',
    '## 解决效果',
    '## 下月重点动作',
    '## 需要协同支持的事项',
]
ISO_DATE_RE = re.compile(r'(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)')


def section_time(raw_title: str) -> str:
    m = re.search(r'\s+[—-]\s+(\d{1,2}:\d{2})$', raw_title.strip())
    return m.group(1) if m else ''


def gather_month_entries(config: Dict[str, Any], year: int, month: int) -> Dict[str, Any]:
    """Read every Daily Note of one month once and flatten the structured entries.

    The generated 今日总结 block is dropped by extract_sections, so the ledger only
    carries what the sessions themselves recorded. Nothing here judges relevance;
    scope filtering happens in scope_month_entries so the excluded side stays visible.
    """
    start, end = month_bounds(year, month)
    notes: List[Dict[str, Any]] = []
    entries: List[Dict[str, Any]] = []
    day = start
    while day <= end:
        path = daily_path(config, day)
        try:
            text = read_note(config, path)
        except Exception:
            day += dt.timedelta(days=1)
            continue
        body = remove_generated_block(strip_frontmatter(text))
        raw_titles = [re.sub(r'^####\s+', '', line.strip()) for line in body.splitlines() if re.match(r'^####\s+', line.strip())]
        sections = extract_sections(text)
        for idx, sec in enumerate(sections):
            fields = parse_structured_fields(sec['body'])
            raw = raw_titles[idx] if idx < len(raw_titles) else sec['title']
            tags = uniq_keep_order(parse_tags_text(fields.get('tags', '')) + parse_tags_text(sec['body']))
            entries.append({
                'date': day.isoformat(),
                'time': section_time(raw),
                'title': sec['title'],
                'problem': fields['problem'],
                'solution': fields['solution'],
                'conclusion': fields['conclusion'],
                'key_points': fields['key_points'],
                'links': uniq_keep_order(extract_wikilinks(fields.get('links', '')) + extract_inline_paths(fields.get('links', ''))),
                'work_items': uniq_keep_order(parse_rendered_work_items(fields.get('work_items', ''))),
                'tags': tags,
            })
        notes.append({'date': day.isoformat(), 'path': path, 'entries': len(sections), 'has_generated_summary': START in text})
        day += dt.timedelta(days=1)
    return {'year': year, 'month': month, 'period_start': start.isoformat(), 'period_end': end.isoformat(), 'notes': notes, 'entries': entries}


def work_item_display(item: str) -> str:
    """`[[doc#^anchor|CC-BKLT-26810-1]]` and `` `CC-BKLT-26810-1` `` both mean the bare ID."""
    text = (item or '').strip().strip('`').strip()
    m = re.fullmatch(r'\[\[(?:[^\]|]*)\|([^\]]+)\]\]', text)
    if m:
        return m.group(1).strip()
    m = re.fullmatch(r'\[\[([^\]|#]*)(?:#[^\]|]*)?\]\]', text)
    if m:
        return m.group(1).strip().split('/')[-1]
    return text


def work_item_prefix(item: str) -> str:
    m = re.match(r'^([A-Za-z]{2,6})-', work_item_display(item))
    return m.group(1).upper() if m else ''


def scope_month_entries(entries: List[Dict[str, Any]], tags: List[str], prefixes: List[str], keywords: List[str]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split entries into in-scope and excluded. No rule at all means everything is in scope."""
    want_tags = {t.strip().lstrip('#').lower() for t in tags if t.strip()}
    want_prefixes = {p.strip().upper() for p in prefixes if p.strip()}
    want_keywords = [k.strip().lower() for k in keywords if k.strip()]
    if not (want_tags or want_prefixes or want_keywords):
        return list(entries), []
    included, excluded = [], []
    for e in entries:
        entry_tags = {t.lstrip('#').lower() for t in e['tags']}
        # `创角` should also catch `创角agent` and `创角/性能`: a wanted tag matches any entry tag it prefixes.
        hit = (
            any(et == wt or et.startswith(wt) for et in entry_tags for wt in want_tags)
            or any(work_item_prefix(w) in want_prefixes for w in e['work_items'])
            or any(k in e['title'].lower() for k in want_keywords)
        )
        (included if hit else excluded).append(e)
    return included, excluded


def build_month_ledger(inv: Dict[str, Any], included: List[Dict[str, Any]], excluded: List[Dict[str, Any]], scope_desc: str, brief: bool = False) -> str:
    """Render the private evidence ledger. This is working material, never a vault note."""
    notes = inv['notes']
    lines = [f"# {inv['year']}-{inv['month']:02d} 月度证据台账", '']
    lines.append(f"- 结算期：{inv['period_start']} 至 {inv['period_end']}")
    lines.append(f"- 日报覆盖：{len(notes)} 篇 · {'、'.join(n['date'][-2:] for n in notes) or '无'}")
    empty = [n['date'] for n in notes if n['entries'] == 0]
    if empty:
        lines.append(f"- 无结构化条目的日报：{'、'.join(empty)}")
    lines.append(f"- 条目：{len(inv['entries'])} 条，纳入 {len(included)} 条，排除 {len(excluded)} 条")
    lines.append(f"- 范围规则：{scope_desc or '无（全部纳入）'}")
    lines.append('- 说明：AI 生成的今日总结块不计入；每条只带日报里的结构化字段，判断与归并留给复盘者')
    if brief:
        lines.append('- 精简版：每条只保留结果与工作项，归并工作线用；核对交付边界时回到完整版或原日报')
    lines.append('')
    lines.append('## 纳入条目（按日期）')
    current = None
    for e in included:
        if e['date'] != current:
            current = e['date']
            lines += ['', f"### {current}"]
        head = f"#### {e['title']}" + (f" — {e['time']}" if e['time'] else '')
        lines += ['', head]
        field_order = (('结果', 'conclusion'),) if brief else (('结果', 'conclusion'), ('问题', 'problem'), ('处理', 'solution'), ('要点', 'key_points'))
        for label, key in field_order:
            if e[key]:
                lines.append(f"- {label}: {e[key]}")
        if e['work_items']:
            lines.append(f"- 工作项: {' · '.join(work_item_display(w) for w in e['work_items'])}")
        if not brief and e['links']:
            lines.append(f"- 文档: {' · '.join(e['links'])}")
        if not brief and e['tags']:
            lines.append(f"- 标签: {' '.join('#' + t.lstrip('#') for t in e['tags'])}")
    lines += ['', '## 排除条目（只列标题，review 里要点名被排除的工作线）']
    if excluded:
        for e in excluded:
            tags = ' '.join('#' + t.lstrip('#') for t in e['tags'][:3])
            lines.append(f"- {e['date']} · {e['title']}" + (f" · {tags}" if tags else ''))
    else:
        lines.append('- 无')
    prefix_dates: Dict[str, List[str]] = {}
    for e in included:
        for w in e['work_items']:
            pre = work_item_prefix(w) or '其他'
            prefix_dates.setdefault(pre, []).append(e['date'])
    lines += ['', '## 工作项前缀索引（纳入部分）']
    if prefix_dates:
        for pre, dates in sorted(prefix_dates.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"- {pre}: {len(dates)} 处 · {'、'.join(uniq_keep_order(d[-2:] for d in dates))}")
    else:
        lines.append('- 无')
    lines += ['', '## 日报来源链接']
    lines.append(' · '.join(f"[[{n['path'][:-3]}]]" for n in notes) or '- 无')
    return '\n'.join(lines) + '\n'


def count_daily_notes_in_period(config: Dict[str, Any], start: dt.date, end: dt.date) -> int:
    count = 0
    day = start
    while day <= end:
        try:
            read_note(config, daily_path(config, day))
            count += 1
        except Exception:
            pass
        day += dt.timedelta(days=1)
    return count


def monthly_note_checks(config: Dict[str, Any], meta: Dict[str, Any], body: str, text: str) -> Dict[str, Any]:
    """Extra verification for type: monthly-goal-review notes. Reports; only the count is fixable."""
    missing = [h for h in MONTHLY_REQUIRED_HEADINGS if not re.search(rf'^{re.escape(h)}\s*$', body, flags=re.M)]
    result: Dict[str, Any] = {
        'required_headings_ok': not missing,
        'missing_headings': missing,
        # Private plan-versus-reality note; expected whenever a previous month's review exists.
        'has_alignment_section': bool(re.search(rf'^{re.escape(MONTHLY_ALIGNMENT_HEADING)}', body, flags=re.M)),
        'no_summary_markers_ok': START not in text and END not in text,
    }
    try:
        start = parse_date(str(meta.get('period_start')))
        end = parse_date(str(meta.get('period_end')))
    except Exception:
        result['period_ok'] = False
        return result
    result['period_ok'] = True
    actual = count_daily_notes_in_period(config, start, end)
    result['actual_source_daily_notes'] = actual
    result['source_daily_notes_ok'] = meta.get('source_daily_notes') == actual
    late = sorted({m.group(0) for m in ISO_DATE_RE.finditer(body) if parse_date(m.group(0)) > end})
    result['post_period_dates'] = late
    return result


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
        '相关工作项': 'work_items',
        '标签': 'tags',
    }
    out = {v: '' for v in labels.values()}
    for line in body.splitlines():
        m = re.match(r'^-\s+(.+?)：\s*(.*)$', line.strip())
        if m and m.group(1) in labels:
            out[labels[m.group(1)]] = m.group(2).strip()
    return out


MIN_CJK_BRIEF_PREFIX = 2
# Subitems listed under one project group in the group-sendable brief. Anything past
# this is reported as a count rather than dropped.
MAX_BRIEF_SUBITEMS = 4


def weekly_brief_prefix(title: str) -> str:
    """Return the project token a module title starts with, or '' if there is none.

    Only a leading latin token counts here (Kizuna, MagClaw). CJK project names have
    no whitespace to split on, so they are handled by
    weekly_brief_shared_cjk_prefixes across the whole module set instead.
    """
    parts = title.split(maxsplit=1)
    if not parts:
        return ''
    first = parts[0].strip('：:、/|-')
    if re.fullmatch(r'[A-Za-z][A-Za-z0-9._-]{1,31}', first):
        return first
    return ''


def weekly_brief_shared_cjk_prefixes(titles: List[str]) -> List[str]:
    """Find CJK project prefixes shared by at least two module titles.

    Without this the grouping only fires on latin project names, so a week of
    「创角问题修复」「创角性能优化」「创角测试验证」 would still print three top-level
    items all starting with the same words — exactly the repetition the group-sendable
    brief exists to avoid. Requires at least MIN_CJK_BRIEF_PREFIX characters and a
    non-empty remainder for every member, so a one-character coincidence cannot merge
    unrelated work. Longer prefixes win, keeping 「创角立绘」 tighter than 「创角」 when
    both apply.
    """
    candidates: Dict[str, int] = {}
    for i, left in enumerate(titles):
        for right in titles[i + 1:]:
            shared = 0
            for a, b in zip(left, right):
                if a != b:
                    break
                shared += 1
            prefix = left[:shared].strip()
            if len(prefix) < MIN_CJK_BRIEF_PREFIX or not re.fullmatch(r'[一-鿿]+', prefix):
                continue
            if left[len(prefix):].strip(' ：:、/|-') and right[len(prefix):].strip(' ：:、/|-'):
                candidates[prefix] = candidates.get(prefix, 0) + 1
    return sorted(candidates, key=len, reverse=True)


def weekly_brief_sentence(module: Dict[str, str]) -> str:
    fields = parse_weekly_module_fields(module['body'])
    sentence = fields['conclusion'] or fields['problem'] or fields['key_points'] or '完成了相关事项整理与沉淀。'
    return shorten_text(sentence, 72).rstrip('。；; ')


def build_weekly_brief_from_note(note_text: str, limit: int = 4) -> str:
    modules = iter_weekly_modules(note_text)
    lines = ['上周工作：']
    if not modules:
        return '上周工作：\n1. 工作复盘：暂无可提炼的结构化周报内容。'

    titles = [module['title'] for module in modules]
    prefixes = [weekly_brief_prefix(title) for title in titles]
    repeated_prefixes = {prefix for prefix in prefixes if prefix and prefixes.count(prefix) > 1}
    cjk_prefixes = weekly_brief_shared_cjk_prefixes(titles)

    def group_key(title: str, latin_prefix: str) -> str:
        if latin_prefix in repeated_prefixes:
            return latin_prefix
        for prefix in cjk_prefixes:
            if title.startswith(prefix) and title[len(prefix):].strip(' ：:、/|-'):
                return prefix
        return title

    groups: Dict[str, List[Dict[str, str]]] = {}
    for module, title, prefix in zip(modules, titles, prefixes):
        groups.setdefault(group_key(title, prefix), []).append(module)

    ordered = list(groups.items())
    for idx, (group_title, group_modules) in enumerate(ordered[:limit], 1):
        if len(group_modules) == 1:
            lines.append(f'{idx}. {group_title}：{weekly_brief_sentence(group_modules[0])}。')
            continue

        lines.append(f'{idx}. {group_title}')
        for module in group_modules[:MAX_BRIEF_SUBITEMS]:
            subtitle = module['title'][len(group_title):].strip(' ：:、/|-') or '主要进展'
            lines.append(f'   - {subtitle}：{weekly_brief_sentence(module)}。')
        # Never let work vanish from a brief that gets sent to a group chat.
        dropped = len(group_modules) - MAX_BRIEF_SUBITEMS
        if dropped > 0:
            lines.append(f'   - 另有 {dropped} 项同项目工作，详见周报。')
    if len(ordered) > limit:
        lines.append(f'（另有 {len(ordered) - limit} 项工作未列入，详见周报。）')
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
    if kind == 'monthly':
        return """filters:
  and:
    - 'type == "monthly-goal-review"'
    - 'file.ext == "md"'

properties:
  period_start:
    displayName: "Period Start"
  period_end:
    displayName: "Period End"
  project:
    displayName: Project
  status:
    displayName: Status
  source_daily_notes:
    displayName: "Daily Notes Read"
  word_count:
    displayName: "Body Characters"

views:
  - type: table
    name: "Monthly Reviews"
    limit: 36
    order:
      - file.name
      - project
      - period_start
      - period_end
      - status
      - source_daily_notes
      - word_count
      - file.mtime
    groupBy:
      property: period_start
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
    work_items = parse_work_items_arg(getattr(args, 'work_items', '') or '')
    key_points = '；'.join(split_atomic_points(args.key_points)[:2]) or args.key_points
    lines = [f'#### {title} — {now}', '', f'- **结果**: {shorten_text(args.conclusion, 120)}']
    problem = squash_spaces(args.problem or '')
    if problem:
        lines.append(f'- **问题**: {shorten_text(problem, 132)}')
    lines.extend([
        f'- **处理**: {shorten_text(args.solution, 140)}',
        f'- **要点**: {shorten_text(key_points, 140)}',
    ])
    if work_items:
        lines.append(f'- **工作项**: {render_work_items_line(work_items, limit=3)}')
    lines.extend([
        f'- **文档**: {render_link_line(links, limit=3)}',
        f'- **标签**: {render_tags_line(tags, limit=3)}',
    ])
    return '\n'.join(lines) + '\n'


# Where each CLI keeps its per-session transcript. An entry's date must come from
# when the work happened, not from when someone got around to summarizing it.
CLAUDE_SESSION_ROOT = Path.home() / '.claude' / 'projects'
CODEX_SESSION_ROOT = Path.home() / '.codex' / 'sessions'
# Synthetic user turns both CLIs inject (system reminders, slash-command echoes,
# plugin banners). They carry the summarize request's clock, never the work's.
SYNTHETIC_TURN_PREFIXES = ('<', '/', '#', 'Caveat:', '<recommended_plugins>')


def find_session_transcript(session_id: str) -> Path | None:
    """Locate the transcript for a Claude session id or a Codex thread id."""
    session_id = (session_id or '').strip()
    if not session_id:
        return None
    claude = sorted(CLAUDE_SESSION_ROOT.glob(f'*/{session_id}.jsonl'))
    if claude:
        return claude[0]
    codex = sorted(CODEX_SESSION_ROOT.glob(f'**/rollout-*-{session_id}.jsonl'))
    if codex:
        return codex[0]
    return None


def looks_like_real_user_turn(text: str) -> bool:
    text = (text or '').strip()
    if not text:
        return False
    return not text.startswith(SYNTHETIC_TURN_PREFIXES)


def transcript_user_turn_timestamps(path: Path) -> List[str]:
    """Every real user-turn timestamp in a transcript, oldest first."""
    stamps: List[str] = []
    try:
        handle = path.open('r', encoding='utf-8', errors='replace')
    except OSError:
        return stamps
    with handle:
        for line in handle:
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                continue
            stamp = record.get('timestamp')
            if not stamp:
                continue
            text = ''
            if record.get('type') == 'user':
                message = record.get('message')
                if isinstance(message, dict) and isinstance(message.get('content'), str):
                    text = message['content']
            elif record.get('type') == 'response_item':
                payload = record.get('payload') or {}
                if payload.get('type') == 'message' and payload.get('role') == 'user':
                    parts = payload.get('content')
                    if isinstance(parts, list):
                        text = ' '.join(
                            str(part.get('text', ''))
                            for part in parts
                            if isinstance(part, dict)
                        )
            if looks_like_real_user_turn(text):
                stamps.append(str(stamp))
    return stamps


def session_work_started_at(session_id: str) -> dt.datetime | None:
    """Local datetime of a session's first real user turn.

    That turn is when the work being summarized actually began. The last turn is
    typically the "summarize this session" request itself, which is exactly the
    clock that misfiles a recap into the wrong day.
    """
    path = find_session_transcript(session_id)
    if path is None:
        return None
    stamps = transcript_user_turn_timestamps(path)
    if not stamps:
        return None
    raw = stamps[0].replace('Z', '+00:00')
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone()


def resolve_entry_datetime(args) -> tuple:
    """Decide which day and time an entry belongs to.

    Precedence is explicit > inferred > today, because an agent that knows the
    real date must always win, and a batch of recaps written days later must not
    silently land on the day someone typed "总结会话".
    """
    session_id = (getattr(args, 'cli_session', '') or '').strip()
    started = session_work_started_at(session_id) if session_id else None
    if args.date:
        return parse_date(args.date), (args.time or dt.datetime.now().strftime('%H:%M')), 'explicit'
    if started is not None:
        return started.date(), (args.time or started.strftime('%H:%M')), 'session'
    return dt.date.today(), (args.time or dt.datetime.now().strftime('%H:%M')), 'today'


def warn_entry_date_source(source: str, date: dt.date, session_id: str) -> None:
    if source == 'explicit':
        return
    if source == 'session':
        print(
            f'[recap] entry dated {date.isoformat()} from session {session_id} first user turn',
            file=sys.stderr,
        )
        return
    detail = f'session {session_id} has no readable transcript' if session_id else 'no --cli-session given'
    print(
        f'[recap] WARNING: no --date and {detail}; defaulting to today ({date.isoformat()}). '
        'If this recap covers work from another day, rerun with --date/--time — '
        'there is no command to move an entry afterwards.',
        file=sys.stderr,
    )


def entry_payload_from_args(args) -> Dict[str, str]:
    return {
        'title': args.title,
        'problem': args.problem,
        'solution': args.solution,
        'conclusion': args.conclusion,
        'key_points': args.key_points,
        'links': args.links,
        'tags': args.tags,
        'time': args.time or '',
        'work_items': getattr(args, 'work_items', '') or '',
    }


def enqueue_entry(config: Dict[str, Any], date: dt.date, payload: Dict[str, str]) -> int:
    with closing(queue_connect(config)) as conn:
        cursor = conn.execute(
            'INSERT INTO entry_queue (date, created_at, payload) VALUES (?, ?, ?)',
            (
                date.isoformat(),
                dt.datetime.now().astimezone().isoformat(timespec='seconds'),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def queue_entry_result(config: Dict[str, Any], entry_id: int) -> tuple[str, str | None]:
    with closing(queue_connect(config)) as conn:
        row = conn.execute(
            'SELECT status, error FROM entry_queue WHERE id = ?',
            (entry_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f'queued entry disappeared: {entry_id}')
    return str(row[0]), row[1]


def flush_pending(config: Dict[str, Any]) -> Dict[str, int]:
    flushed = 0
    failed = 0
    conn = queue_connect(config)
    try:
        while True:
            conn.execute('BEGIN IMMEDIATE')
            rows = conn.execute(
                "SELECT id, date, payload FROM entry_queue "
                "WHERE status = 'pending' ORDER BY id LIMIT 50"
            ).fetchall()
            if not rows:
                conn.commit()
                break

            grouped: Dict[str, List[tuple[int, str]]] = {}
            for entry_id, date_text, payload_text in rows:
                grouped.setdefault(str(date_text), []).append((int(entry_id), str(payload_text)))

            for date_text, queued_entries in grouped.items():
                ids = [entry_id for entry_id, _ in queued_entries]
                placeholders = ','.join('?' for _ in ids)
                try:
                    date = parse_date(date_text)
                    path = daily_path(config, date)
                    with note_lock(config, path):
                        try:
                            existing = read_note_direct(config, path)
                        except FileNotFoundError:
                            existing = ''
                        updated = normalize_daily_note(existing, date)
                        for _, payload_text in queued_entries:
                            payload = json.loads(payload_text)
                            updated = insert_before_summary_block(
                                updated,
                                build_entry_block(argparse.Namespace(**payload)),
                            )
                        summary = build_daily_summary_from_note(updated)
                        updated = replace_summary_block(updated, summary)
                        write_note_direct(config, path, normalize_daily_note(updated, date))
                    conn.execute(
                        f"UPDATE entry_queue SET status = 'done', error = NULL WHERE id IN ({placeholders})",
                        ids,
                    )
                    flushed += len(ids)
                except Exception as exc:
                    conn.execute(
                        f"UPDATE entry_queue SET status = 'failed', error = ? WHERE id IN ({placeholders})",
                        [str(exc), *ids],
                    )
                    failed += len(ids)
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    with closing(queue_connect(config)) as count_conn:
        remaining = int(
            count_conn.execute(
                "SELECT COUNT(*) FROM entry_queue WHERE status = 'pending'"
            ).fetchone()[0]
        )
    return {'flushed': flushed, 'failed': failed, 'remaining': remaining}


def cmd_append_entry(args):
    config = load_config(args)
    date, entry_time, source = resolve_entry_datetime(args)
    args.time = entry_time
    warn_entry_date_source(source, date, (getattr(args, 'cli_session', '') or '').strip())
    path = daily_path(config, date)
    entry_id = enqueue_entry(config, date, entry_payload_from_args(args))
    flush_pending(config)
    status, error = queue_entry_result(config, entry_id)
    if status != 'done':
        raise RuntimeError(error or f'queued entry {entry_id} did not reach done status')
    print(path)


def cmd_flush_pending(args):
    config = load_config(args)
    print(json.dumps(flush_pending(config), ensure_ascii=False, sort_keys=True))


def cmd_refresh_daily_auto(args):
    config = load_config(args)
    date = parse_date(args.date) if args.date else dt.date.today()
    path = daily_path(config, date)
    with note_lock(config, path):
        try:
            existing = read_note_direct(config, path)
        except FileNotFoundError:
            existing = ''
        normalized = normalize_daily_note(existing, date)
        summary = build_daily_summary_from_note(normalized)
        updated = replace_summary_block(normalized, summary)
        write_note_direct(config, path, normalize_daily_note(updated, date))
    print(path)


def cmd_replace_summary(args):
    config = load_config(args)
    date = parse_date(args.date)
    path = daily_path(config, date)
    block = Path(args.file).expanduser().read_text()
    if START in block or END in block:
        raise ValueError('replacement block must omit AI_SUMMARY markers')
    with note_lock(config, path):
        existing = read_note_direct(config, path)
        updated = replace_summary_block(existing, block)
        meta, body = split_frontmatter(updated)
        meta['word_count'] = count_body_chars(body)
        write_note_direct(config, path, render_note(meta, body))
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
    # render_note trims trailing whitespace to one newline; count what will actually be
    # written, or a note ending in two blank lines needs two --fix passes to settle.
    body = body.rstrip() + '\n'
    actual = count_body_chars(body)
    stored_raw = meta.get('word_count')
    try:
        stored = int(stored_raw)
    except Exception:
        stored = None
    marker_ok = text.count(START) == text.count(END)
    is_monthly = meta.get('type') == 'monthly-goal-review'
    monthly = monthly_note_checks(config, meta, body, text) if is_monthly else {}
    dirty = False
    if args.fix and (stored != actual or not stored_raw):
        meta['word_count'] = actual
        stored = actual
        dirty = True
    if args.fix and is_monthly and monthly.get('period_ok') and not monthly.get('source_daily_notes_ok'):
        meta['source_daily_notes'] = monthly['actual_source_daily_notes']
        monthly['source_daily_notes_ok'] = True
        dirty = True
    if dirty:
        if is_monthly:
            # A reviewed monthly note keeps its authoring date; refreshes stamp `updated`.
            meta['updated'] = dt.date.today().isoformat()
        create_or_overwrite_note(config, args.path, render_note(meta, body))
    result = {
        'path': args.path,
        'word_count': stored,
        'actual_word_count': actual,
        'word_count_ok': stored == actual,
        'summary_markers_ok': marker_ok,
        'fixed': dirty,
    }
    result.update(monthly)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def cmd_inventory_month(args):
    config = load_config(args)
    year, month = parse_month(args.month)
    inv = gather_month_entries(config, year, month)
    tags = parse_work_items_arg(args.tags)
    prefixes = parse_work_items_arg(args.work_item_prefixes)
    keywords = parse_work_items_arg(args.keywords)
    included, excluded = scope_month_entries(inv['entries'], tags, prefixes, keywords)
    scope_bits = []
    if tags:
        scope_bits.append('标签 ' + '/'.join('#' + t.lstrip('#') for t in tags))
    if prefixes:
        scope_bits.append('工作项前缀 ' + '/'.join(p.upper() for p in prefixes))
    if keywords:
        scope_bits.append('标题关键词 ' + '/'.join(keywords))
    scope_desc = '；'.join(scope_bits)
    if args.format == 'json':
        out = json.dumps({**inv, 'included': included, 'excluded': excluded, 'scope': scope_desc}, ensure_ascii=False, indent=2)
    else:
        out = build_month_ledger(inv, included, excluded, scope_desc, brief=args.brief)
    if args.out:
        target = Path(args.out).expanduser()
        vault_root = resolve_vault_root(config)
        if vault_root is not None and path_is_within(target.resolve(), vault_root.resolve()):
            raise ValueError('the ledger is working material; write it outside the vault')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(out)
        print(json.dumps({'out': str(target), 'notes': len(inv['notes']), 'entries': len(inv['entries']), 'included': len(included), 'excluded': len(excluded), 'suggested_note_path': monthly_path(config, year, month, args.project)}, ensure_ascii=False))
    else:
        sys.stdout.write(out)


def cmd_ensure_index_base(args):
    config = load_config(args)
    kinds = ['daily', 'weekly', 'monthly'] if args.kind == 'all' else [args.kind]
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
    p.add_argument('--monthly-dir')
    p.add_argument('--index-dir')
    p.add_argument('--queue-db')


def main():
    ap = argparse.ArgumentParser(description='Generate Obsidian daily, weekly and monthly review notes from existing markdown content.')
    sub = ap.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('append-entry', help='Append a structured session entry into a daily note.')
    add_common_args(a)
    a.add_argument('--date')
    a.add_argument('--time')
    a.add_argument(
        '--cli-session',
        default='',
        help='Claude session id or Codex thread id. Without --date, the entry is '
             'dated from this session first real user turn instead of today, so a '
             'recap written days later still lands on the day the work happened.',
    )
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
    a.add_argument('--work-items', default='')
    a.add_argument('--tags', default='')
    a.set_defaults(func=cmd_append_entry)

    f = sub.add_parser('flush-pending', help='Flush queued session entries into their daily notes.')
    add_common_args(f)
    f.set_defaults(func=cmd_flush_pending)

    d = sub.add_parser('refresh-daily-auto', help='Read a daily note and regenerate its summary block.')
    add_common_args(d)
    d.add_argument('--date')
    d.set_defaults(func=cmd_refresh_daily_auto)

    r = sub.add_parser('replace-summary', help='Replace one daily generated summary through the shared note lock.')
    add_common_args(r)
    r.add_argument('--date', required=True)
    r.add_argument('--file', required=True)
    r.set_defaults(func=cmd_replace_summary)

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
    b.add_argument('--limit', type=int, default=4, help='Maximum number of top-level project groups to print.')
    b.set_defaults(func=cmd_print_weekly_brief)

    im = sub.add_parser('inventory-month', help='Read every daily note of one month once and print a private evidence ledger for a monthly review.')
    add_common_args(im)
    im.add_argument('--month', required=True, help='Reporting month as YYYY-MM.')
    im.add_argument('--tags', default='', help='Comma-separated tags that put an entry in scope, e.g. kizuna,omnivibe.')
    im.add_argument('--work-item-prefixes', default='', help='Comma-separated work-item domain prefixes that put an entry in scope, e.g. CC,PLT.')
    im.add_argument('--keywords', default='', help='Comma-separated title keywords that put an entry in scope.')
    im.add_argument('--project', default='', help='Project name used only to suggest the monthly note path.')
    im.add_argument('--format', choices=['markdown', 'json'], default='markdown')
    im.add_argument('--brief', action='store_true', help='Markdown only: keep title, 结果 and 工作项 per entry for a first grouping pass.')
    im.add_argument('--out', default='', help='Write the ledger to this file outside the vault instead of stdout.')
    im.set_defaults(func=cmd_inventory_month)

    v = sub.add_parser('verify-note', help='Verify a note word_count and generated summary markers; monthly-goal-review notes also get heading, source-count and marker checks.')
    add_common_args(v)
    v.add_argument('--path', required=True)
    v.add_argument('--fix', action='store_true')
    v.set_defaults(func=cmd_verify_note)

    ib = sub.add_parser('ensure-index-base', help='Create or refresh Obsidian Bases index files for daily and weekly notes.')
    add_common_args(ib)
    ib.add_argument('--kind', choices=['daily', 'weekly', 'monthly', 'all'], default='all')
    ib.set_defaults(func=cmd_ensure_index_base)

    args = ap.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
