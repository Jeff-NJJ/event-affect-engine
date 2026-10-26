"""Verify the published tree against the committed MANIFEST.sha256."""
import argparse, hashlib, os, sys
from pathlib import Path

EXCLUDE_DIRS = {'.git', '__pycache__', 'node_modules', '.idea', '.vscode'}
# The manifest records published files. Reports written by run_checks.py (locally
# or in CI) are generated output and are deliberately not recorded.


def is_excluded(rel: str, name: str) -> bool:
    if name == 'MANIFEST.sha256':
        return True
    if rel.startswith('reports/') and name.endswith('.json') and not name.startswith('sanitized_'):
        return True
    return False


def tree(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            if is_excluded(rel, fn):
                continue
            yield rel, p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument('--write', action='store_true', help='regenerate MANIFEST.sha256 instead of checking')
    args = ap.parse_args()
    root = Path(args.root).resolve()
    rows = [(rel, hashlib.sha256(p.read_bytes()).hexdigest()) for rel, p in tree(root)]

    if args.write:
        out = root / 'MANIFEST.sha256'
        out.write_text(''.join('%s  %s\n' % (h, rel) for rel, h in rows), encoding='utf-8')
        print('wrote %s (%d files)' % (out, len(rows)))
        return 0

    man = root / 'MANIFEST.sha256'
    if not man.exists():
        print('MANIFEST.sha256 missing; run: python tools/verify_manifest.py --write')
        return 2
    recorded = {}
    for line in man.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line:
            h, rel = line.split('  ', 1)
            recorded[rel] = h
    current = dict(rows)
    missing = sorted(set(recorded) - set(current))
    extra = sorted(set(current) - set(recorded))
    bad = sorted(r for r in set(recorded) & set(current) if recorded[r] != current[r])
    for r in missing:
        print('MISSING   ', r)
    for r in bad:
        print('MISMATCH  ', r)
    for r in extra:
        print('UNRECORDED', r)
    ok = not (missing or bad or extra)
    print('verified %d files: %s' % (len(current), 'OK' if ok else 'PROBLEMS'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
