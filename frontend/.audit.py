# -*- coding: utf-8 -*-
"""Flags rules that set a border or background but no padding — the shape that
produces content touching an edge."""
import io, re

s = io.open('src/styles.css', encoding='utf-8').read()
s = re.sub(r'/\*.*?\*/', '', s, flags=re.S)

blocks = re.findall(r'([^{}]+)\{([^{}]*)\}', s)
suspect = []
for sel, body in blocks:
    sel = ' '.join(sel.split())
    if sel.startswith('@') or ':root' in sel or 'from' in sel or 'to' in sel:
        continue
    has_edge = ('border:' in body or 'background:' in body) and 'transparent' not in body
    has_pad = 'padding' in body
    has_margin = 'margin' in body
    # Containers that lay out children are the ones that need internal spacing.
    lays_out = 'display: grid' in body or 'display: flex' in body
    if has_edge and lays_out and not has_pad and not has_margin:
        suspect.append((sel, ' '.join(body.split())[:110]))

print('--- border/background + layout, but no padding ---')
for sel, body in suspect:
    print('  %-52s %s' % (sel[:52], body))
print('total:', len(suspect))
