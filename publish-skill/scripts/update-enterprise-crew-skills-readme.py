#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
ap=argparse.ArgumentParser()
ap.add_argument('--repo', required=True)
ap.add_argument('--slug', required=True)
ap.add_argument('--description', required=True)
args=ap.parse_args()
readme=Path(args.repo)/'README.md'
text=readme.read_text()
line=f'| [{args.slug}](./{args.slug}/) | {args.description} |'
lines=[l for l in text.splitlines() if not l.startswith(f'| [{args.slug}](')]
idx=max((i for i,l in enumerate(lines) if l.startswith('|')), default=len(lines)-1)
lines.insert(idx+1, line)
readme.write_text('\n'.join(lines)+'\n')
print(f'updated {readme}')
