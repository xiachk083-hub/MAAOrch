"""Upload MAAOrch.exe to GitHub Release — primary: gh CLI, fallback: API."""
import json, urllib.request, os, sys, subprocess

token = os.environ.get('GITHUB_TOKEN', '')
repo = os.environ.get('GITHUB_REPOSITORY', '')
ref = os.environ.get('GITHUB_REF', '')
tag = (ref.replace('refs/tags/', '') if ref.startswith('refs/tags/')
       else os.environ.get('GITHUB_REF_NAME', ''))

print(f'Ref: {ref}, Tag: {tag}, Repo: {repo}, Token set: {bool(token)}')
if not tag:
    print('Not a tag push, skipping release upload')
    sys.exit(0)

EXE_PATH = 'dist/MAAOrch.exe'
if not os.path.isfile(EXE_PATH):
    print(f'ERROR: {EXE_PATH} not found')
    sys.exit(1)

file_size = os.path.getsize(EXE_PATH)
print(f'File: {EXE_PATH} ({file_size} bytes)')

def run_gh(args, timeout=180):
    print(f'Running: gh {" ".join(args)}')
    r = subprocess.run(['gh'] + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode == 0:
        for line in r.stdout.strip().splitlines():
            print(f'  gh> {line}')
    else:
        print(f'  gh FAILED (exit={r.returncode}):')
        for line in r.stderr.strip().splitlines():
            print(f'  gh! {line}')
    return r

# ---- Primary: gh CLI ----
# gh is pre-installed on GitHub Actions Windows runners and handles auth via GITHUB_TOKEN automatically
r = run_gh(['release', 'view', tag])
if r.returncode != 0:
    print('Release does not exist, creating...')
    r = run_gh(['release', 'create', tag, '--title', tag,
                '--notes', f'MAAOrch {tag} release'])
    if r.returncode != 0:
        print('gh release create failed, trying API fallback...')
    else:
        print('Release created via gh')

print(f'Uploading {EXE_PATH} to release {tag}...')
r = run_gh(['release', 'upload', tag, EXE_PATH, '--clobber'])
if r.returncode == 0:
    print(f'SUCCESS: uploaded to https://github.com/{repo}/releases/tag/{tag}')
    sys.exit(0)

# ---- Fallback: API ----
print('gh upload failed. Trying API upload...')
base = f'https://api.github.com/repos/{repo}'
auth_hdr = {'Authorization': f'Bearer {token}', 'User-Agent': 'MAAOrch', 'Content-Type': 'application/json'}

def _api(method, url, data=None, extra_headers=None, timeout=30):
    h = dict(auth_hdr)
    if extra_headers:
        h.update(extra_headers)
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())

# Get or create release via API
try:
    release = _api('GET', f'{base}/releases/tags/{tag}')
    print(f'Found existing release via API')
except Exception as e:
    print(f'API get release failed: {e}, creating...')
    try:
        release = _api('POST', f'{base}/releases',
                       data={'tag_name': tag, 'name': tag, 'body': f'MAAOrch {tag} release'})
        print(f'Release created via API')
    except Exception as e2:
        print(f'API create failed: {e2}')
        sys.exit(1)

upload_url = release['upload_url'].split('{?')[0] + '?name=MAAOrch.exe'
with open(EXE_PATH, 'rb') as f:
    payload = f.read()

try:
    _api('POST', upload_url, data=payload,
         extra_headers={'Content-Type': 'application/octet-stream'}, timeout=300)
    print(f'API upload OK ({len(payload)} bytes)')
    print(f'SUCCESS: https://github.com/{repo}/releases/tag/{tag}')
    sys.exit(0)
except Exception as e:
    print(f'API upload failed: {e}')
    sys.exit(1)
