#!/usr/bin/env python3
"""Sanitize a skill bundle for public publishing.

Copies a source skill directory to an output directory while removing known
secret files and replacing personal/internal identifiers with placeholders.
Emits a JSON report. Fails non-zero if hard blockers remain.
"""
from __future__ import annotations
import argparse, json, os, re, shutil, stat
from pathlib import Path

TEXT_SUFFIXES={'.md','.txt','.json','.yaml','.yml','.toml','.ini','.cfg','.conf','.sh','.bash','.py','.js','.mjs','.ts','.tsx','.jsx','.html','.css'}
DROP_NAMES={'.env','.env.local','.env.production','.env.development'}
DROP_SUFFIXES={'.pem','.key','.p12','.pfx','.secret','.credentials'}
DROP_DIRS={'secrets','.secrets','node_modules','.git','__pycache__','.pytest_cache','.mypy_cache','dist','build','.next'}
REPLACEMENTS=[
    (re.compile(r'/Users/[A-Za-z0-9._-]+'), '<HOME>'),
    (re.compile(r'/home/[A-Za-z0-9._-]+'), '/home/<user>'),
    (re.compile(r'\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '<REDACTED_IP>'),
    (re.compile(r'\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '<REDACTED_IP>'),
    (re.compile(r'\b192\.168\.\d{1,3}\.\d{1,3}\b'), '<REDACTED_IP>'),
    (re.compile(r'\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b'), '<REDACTED_IP>'),
    (re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'), 'user@example.com'),
    (re.compile(r'gh[pousr]_[A-Za-z0-9_]{20,}'), '<REDACTED_GITHUB_TOKEN>'),
    (re.compile(r'sk-[A-Za-z0-9_-]{20,}'), '<REDACTED_API_KEY>'),
    (re.compile(r'xox[baprs]-[A-Za-z0-9-]{20,}'), '<REDACTED_SLACK_TOKEN>'),
    (re.compile(r'Bearer\s+[A-Za-z0-9._~+/-]{20,}'), 'Bearer <REDACTED_TOKEN>'),
    (re.compile(r'(?i)(password|api[_-]?key|token|secret)(\s*[:=]\s*)(["\']?)[^"\'\s]+'), r'\1\2\3<REDACTED>'),
]
BLOCK_PATTERNS={
    'api_key': re.compile(r'(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|Bearer\s+[A-Za-z0-9._~+/-]{20,})'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----'),
    'env_assignment_secret': re.compile(r'(?i)\b(?:PASSWORD|TOKEN|SECRET|API_KEY|AUTH_TOKEN|COOKIE)=[^\s<][^\s]*'),
}

def is_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES or path.name in {'SKILL.md','README.md','LICENSE'}:
        return True
    try:
        data=path.read_bytes()[:4096]
    except Exception:
        return False
    return b'\0' not in data

def copy_tree(src: Path, out: Path, report: dict):
    if out.exists(): shutil.rmtree(out)
    out.mkdir(parents=True)
    for root, dirs, files in os.walk(src):
        rootp=Path(root)
        dirs[:] = [d for d in dirs if d not in DROP_DIRS]
        relroot=rootp.relative_to(src)
        for d in dirs:
            (out/relroot/d).mkdir(parents=True, exist_ok=True)
        for f in files:
            sp=rootp/f
            rel=relroot/f
            if f in DROP_NAMES or sp.suffix.lower() in DROP_SUFFIXES:
                report['removed'].append(str(rel)); continue
            dp=out/rel
            dp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sp, dp)
            if is_text(dp):
                text=dp.read_text(errors='ignore')
                original=text
                for pat, repl in REPLACEMENTS:
                    text=pat.sub(repl, text)
                if text != original:
                    report['modified'].append(str(rel))
                    dp.write_text(text)
            mode=dp.stat().st_mode
            if mode & stat.S_IXUSR:
                dp.chmod(mode | stat.S_IXUSR)

def scan(out: Path, report: dict):
    for p in out.rglob('*'):
        if not p.is_file() or not is_text(p): continue
        text=p.read_text(errors='ignore')
        for label, pat in BLOCK_PATTERNS.items():
            if pat.search(text):
                report['blocked'].append({'file': str(p.relative_to(out)), 'reason': label})

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--report', required=True)
    args=ap.parse_args()
    src=Path(args.input).expanduser().resolve(); out=Path(args.output).expanduser().resolve(); rep=Path(args.report).expanduser().resolve()
    if not (src/'SKILL.md').exists(): raise SystemExit('SKILL.md missing')
    report={'source': str(src), 'output': str(out), 'removed': [], 'modified': [], 'blocked': []}
    copy_tree(src, out, report)
    scan(out, report)
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'output': str(out), 'report': str(rep), 'blocked': len(report['blocked']), 'modified': len(report['modified']), 'removed': len(report['removed'])}, indent=2))
    if report['blocked']:
        raise SystemExit(2)
if __name__=='__main__': main()
