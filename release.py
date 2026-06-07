"""Upload MAAOrch.exe to GitHub Release via API, with gh CLI fallback."""
import json, urllib.request, os, sys, subprocess

token = os.environ.get('GITHUB_TOKEN', '')
repo = os.environ.get('GITHUB_REPOSITORY', '')
ref = os.environ.get('GITHUB_REF', '')
tag = ref.replace('refs/tags/', '') if ref.startswith('refs/tags/') else ''
# Fallback to GITHUB_REF_NAME if GITHUB_REF isn't set
if not tag:
    tag = os.environ.get('GITHUB_REF_NAME', '')

print(f'Ref: {ref}, Tag: {tag}, Repo: {repo}, Token set: {bool(token)}')
if not tag:
    print('Not a tag push, skipping release upload')
    sys.exit(0)  # Don't fail on branch pushes

EXE_PATH = 'dist/MAAOrch.exe'
if not os.path.isfile(EXE_PATH):
    print(f'ERROR: {EXE_PATH} not found')
    sys.exit(1)

file_size = os.path.getsize(EXE_PATH)
print(f'File: {EXE_PATH} ({file_size} bytes)')

# ---- gh CLI fallback ----
if not token:
    print('No GITHUB_TOKEN, trying gh CLI...')
    r = subprocess.run(['gh', 'release', 'upload', tag, EXE_PATH, '--clobber'],
                       capture_output=True, text=True, timeout=120)
    if r.returncode == 0:
        print(f'gh upload OK: stdout={r.stdout.strip()}')
        sys.exit(0)
    else:
        print(f'gh failed: {r.stderr.strip()}')
        sys.exit(1)

base = f'https://api.github.com/repos/{repo}'
auth_hdr = {'Authorization': f'Bearer {token}', 'User-Agent': 'MAAOrch', 'Content-Type': 'application/json'}

def _req(method, url, data=None, headers=None, timeout=15):
    h = dict(auth_hdr)
    if headers:
        h.update(headers)
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    resp = urllib.request.urlopen(req, timeout=timeout)
    body = resp.read()
    try:
        return json.loads(body)
    except Exception:
        return body

# Get or create release
release = None
try:
    release = _req('GET', f'{base}/releases/tags/{tag}')
    print(f'Found existing release: {release.get("html_url", release.get("url", "?"))}')
except Exception as e1:
    print(f'Get release failed: {e1}, creating new...')
    try:
        payload = {'tag_name': tag, 'name': tag, 'body': f'MAAOrch {tag} release'}
        release = _req('POST', f'{base}/releases', data=payload)
        print(f'Created release: {release.get("html_url", "?")}')
    except Exception as e2:
        print(f'Create release failed: {e2}')
        # Try gh CLI as fallback
        print('Trying gh CLI fallback...')
        r = subprocess.run(['gh', 'release', 'create', tag, '--title', tag,
                            '--notes', f'MAAOrch {tag} release'], capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            print(f'gh release create OK')
        else:
            print(f'gh failed: {r.stderr.strip()}')
            sys.exit(1)

# Upload
upload_url = release['upload_url'].split('{?')[0] + '?name=MAAOrch.exe'
with open(EXE_PATH, 'rb') as f:
    data = f.read()
try:
    _req('POST', upload_url, data=data,
         headers={'Content-Type': 'application/octet-stream'}, timeout=120)
    print(f'Uploaded {len(data)} bytes OK')
except Exception as e:
    print(f'Upload via API failed: {e}')
    # gh CLI fallback
    print('Trying gh CLI fallback...')
    r = subprocess.run(['gh', 'release', 'upload', tag, EXE_PATH, '--clobber'],
                       capture_output=True, text=True, timeout=120)
    if r.returncode == 0:
        print(f'gh upload fallback OK')
        sys.exit(0)
    else:
        print(f'gh fallback failed: {r.stderr.strip()}')
        sys.exit(1)
