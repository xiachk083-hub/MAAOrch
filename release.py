"""Upload MAAOrch.exe to GitHub Release via API. Called from CI workflow."""
import json, urllib.request, os, sys

token = os.environ['GITHUB_TOKEN']
repo = os.environ['GITHUB_REPOSITORY']
tag = os.environ['GITHUB_REF_NAME']
base = f'https://api.github.com/repos/{repo}'
auth_hdr = {'Authorization': f'Bearer {token}', 'User-Agent': 'MAAOrch', 'Content-Type': 'application/json'}

# Try to get existing release, or create new one
release = None
try:
    req = urllib.request.Request(f'{base}/releases/tags/{tag}', headers=auth_hdr)
    release = json.loads(urllib.request.urlopen(req, timeout=10).read())
    print(f'Found existing release: {release["html_url"]}')
except Exception:
    body = json.dumps({'tag_name': tag, 'name': tag, 'body': f'MAAOrch {tag} release'}).encode()
    req = urllib.request.Request(f'{base}/releases', data=body, headers=auth_hdr)
    try:
        release = json.loads(urllib.request.urlopen(req, timeout=10).read())
        print(f'Created release: {release["html_url"]}')
    except Exception as e:
        print(f'Failed to create release: {e}')
        sys.exit(1)

# Upload asset
url = release['upload_url'].split('{?')[0] + '?name=MAAOrch.exe'
with open('dist/MAAOrch.exe', 'rb') as f:
    data = f.read()
req = urllib.request.Request(url, data=data,
    headers={'Authorization': f'Bearer {token}', 'User-Agent': 'MAAOrch', 'Content-Type': 'application/octet-stream'})
urllib.request.urlopen(req, timeout=60)
print(f'Uploaded MAAOrch.exe ({len(data)} bytes) to {release["html_url"]}')
