"""Upload MAAOrch.exe to GitHub Release via API."""
import json, urllib.request, os, sys

token = os.environ.get('GITHUB_TOKEN', '')
repo = os.environ.get('GITHUB_REPOSITORY', '')
ref = os.environ.get('GITHUB_REF', '')
tag = ref.replace('refs/tags/', '') if ref.startswith('refs/tags/') else ''

print(f'Ref: {ref}, Tag: {tag}, Repo: {repo}')
if not tag:
    print('Not a tag push, skipping release upload')
    sys.exit(0)  # Don't fail on branch pushes

base = f'https://api.github.com/repos/{repo}'
auth_hdr = {'Authorization': f'Bearer {token}', 'User-Agent': 'MAAOrch', 'Content-Type': 'application/json'}

# Get or create release
release = None
try:
    req = urllib.request.Request(f'{base}/releases/tags/{tag}', headers=auth_hdr)
    release = json.loads(urllib.request.urlopen(req, timeout=10).read())
    print(f'Found existing release: {release["html_url"]}')
except Exception as e1:
    print(f'Get release failed: {e1}, creating new...')
    body = json.dumps({'tag_name': tag, 'name': tag, 'body': f'MAAOrch {tag} release'}).encode()
    req = urllib.request.Request(f'{base}/releases', data=body, headers=auth_hdr)
    try:
        release = json.loads(urllib.request.urlopen(req, timeout=10).read())
        print(f'Created release: {release["html_url"]}')
    except Exception as e2:
        print(f'Create release failed: {e2}')
        sys.exit(1)

# Upload
url = release['upload_url'].split('{?')[0] + '?name=MAAOrch.exe'
with open('dist/MAAOrch.exe', 'rb') as f:
    data = f.read()
req = urllib.request.Request(url, data=data,
    headers={'Authorization': f'Bearer {token}', 'User-Agent': 'MAAOrch', 'Content-Type': 'application/octet-stream'})
try:
    urllib.request.urlopen(req, timeout=60)
    print(f'Uploaded {len(data)} bytes to {release["html_url"]}')
except Exception as e:
    print(f'Upload failed: {e}')
    sys.exit(1)
