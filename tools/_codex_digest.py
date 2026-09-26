import json, glob, os, re, collections

files = sorted(glob.glob('2026/09/17/*.jsonl') + glob.glob('2026/09/16/*.jsonl'),
               key=os.path.getmtime, reverse=True)[:6]
PATCH = re.compile(r'\*\*\* (?:Update|Add|Delete) File: (.+)')
PATHRE = re.compile(r'(?:tools|docs|services|network|infrastructure|ui|models)[/\\][\w./\\-]+\.(?:py|md|js|json)')

for f in files:
    users, asst, touched = [], [], collections.Counter()
    try:
        with open(f, encoding='utf-8') as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                p = d.get('payload') or {}
                if d.get('type') != 'response_item':
                    continue
                t = p.get('type')
                if t == 'message':
                    role = p.get('role')
                    txt = ''.join(c.get('text', '') for c in (p.get('content') or []) if isinstance(c, dict)).strip()
                    if not txt or role in ('developer', 'system'):
                        continue
                    if any(k in txt[:220] for k in ('<environment_context>', '<permissions instructions>',
                                                    '<user_instructions>', '<INSTRUCTIONS>')):
                        continue
                    (users if role == 'user' else asst).append(txt)
                elif t in ('function_call', 'custom_tool_call', 'local_shell_call'):
                    s = json.dumps(p, ensure_ascii=False)
                    for m in PATCH.finditer(s):
                        touched[m.group(1)] += 1
                    for m in PATHRE.finditer(s):
                        touched[m.group(0).replace(chr(92), '/')] += 1
    except Exception as e:
        print(f, '读取失败', e)
        continue
    print('=' * 96)
    print('会话', os.path.basename(f)[:46], '| 用户 %d 条 | 助手 %d 条' % (len(users), len(asst)))
    for u in users[:4]:
        print('  [用户]', u.replace('\n', ' ')[:260])
    for a in asst[-3:]:
        print('  [助手]', a.replace('\n', ' ')[:300])
    if touched:
        print('  [改过的文件]', ', '.join('%s x%d' % (k, v) for k, v in touched.most_common(8)))
