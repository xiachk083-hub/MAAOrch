import json, os, re

F = '2026/09/17/rollout-2026-09-17T02-11-12-01a0ab6a-6615-7862-a93a-6555c7bf3c0b.jsonl'
SKIP = ('<in-app-browser-context', '<environment_context>', '<permissions instructions>',
        'Referenced chats with Codex', 'The following is the Codex agent history',
        '<user_instructions>', '<INSTRUCTIONS>')
users, asst = [], []
with open(F, encoding='utf-8') as fh:
    for line in fh:
        try:
            d = json.loads(line)
        except Exception:
            continue
        p = d.get('payload') or {}
        if d.get('type') != 'response_item' or p.get('type') != 'message':
            continue
        role = p.get('role')
        if role in ('developer', 'system'):
            continue
        txt = ''.join(c.get('text', '') for c in (p.get('content') or []) if isinstance(c, dict)).strip()
        if not txt or any(k in txt[:260] for k in SKIP):
            continue
        (users if role == 'user' else asst).append(txt)

print('#### 用户真指令（%d 条）####' % len(users))
for i, u in enumerate(users):
    print('%2d. %s' % (i + 1, u.replace('\n', ' ')[:300]))

print()
print('#### 助手最后 8 条（当前状态）####')
for a in asst[-8:]:
    print('- ' + a.replace('\n', ' ')[:700])
    print()
