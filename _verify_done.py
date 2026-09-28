import json, re, os, datetime, sys

d = json.load(open('done_tmp.json'))
rows = sorted(d, key=lambda t: t.get('completed_at') or 0, reverse=True)
cutoff = datetime.datetime(2026, 9, 28, 9, 0).timestamp()
recent = [t for t in rows if (t.get('completed_at') or 0) >= cutoff]
print("recent done:", len(recent))

pat = re.compile(r'`([^`\s]*?(?:docs/|runtime/|tools/|tests/)[^`\s]*\.(?:py|md|json|txt|jsonl))`')
prefixes = ('docs/', 'runtime/', 'tools/', 'tests/')

bad = 0
for t in recent:
    body = t.get('body') or ''
    paths = set(pat.findall(body))
    missing = []
    for p in paths:
        p2 = p.replace('\\', '/').split(':')[-1] if False else p.replace('\\', '/')
        p2 = p2.lstrip('./')
        # strip leading drive/path noise
        for pre in prefixes:
            i = p2.find(pre)
            if i >= 0:
                p2 = p2[i:]
                break
        else:
            continue
        if not os.path.exists(p2):
            missing.append(p2)
    if missing:
        bad += 1
        print("\n" + t['id'] + " " + t['title'][:60])
        for m in sorted(set(missing))[:10]:
            print("   MISSING:", m)
print("\ncards with missing refs:", bad, "/", len(recent))
