#!/usr/bin/env python3
"""One-shot: apply interpolation pass to all existing sync files."""
import json, sys
from pathlib import Path
sys.path.insert(0, '.')
from sync_audiobook import interpolate_stuck_timestamps

sync_dir = Path('sync')
fixed = 0
for jf in sorted(sync_dir.glob('[0-9]*.json')):
    data = json.loads(jf.read_text())
    sm = data.get('sync_map', [])
    if not sm:
        continue
    duration = data.get('duration', 0)
    low = sum(1 for p in sm if p['match_score'] < 0.5)
    if low == 0:
        continue
    new_sm = interpolate_stuck_timestamps(sm, duration)
    changed = sum(1 for a, b in zip(sm, new_sm) if a['start'] != b['start'])
    if changed:
        data['sync_map'] = new_sm
        jf.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f'{jf.name[:50]}: low={low} interpolated={changed}')
        fixed += 1
print(f'\nDone: {fixed} files updated')
