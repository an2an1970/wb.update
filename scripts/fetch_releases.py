#!/usr/bin/env python3
"""Download exact GitHub Release assets, checking release target commit against pinned SHA."""
import argparse
import json
import os
from pathlib import Path
import re
import urllib.request

API = "https://api.github.com/repos/an2an1970/"
def get(url, token, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "wb.update-packager",
    })
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.read()

def latest_release(repo, patterns, token):
    releases = json.loads(get(API + repo + "/releases?per_page=100", token))
    for release in releases:
        if release.get("draft"):
            continue
        assets = release.get("assets", [])
        if not all(sum(bool(re.fullmatch(pattern, asset["name"])) for asset in assets) == 1
                   for pattern in patterns.values()):
            continue
        tag = release["tag_name"]
        ref = json.loads(get(API + repo + "/git/ref/tags/" + tag, token))
        obj = ref["object"]
        if obj["type"] == "tag":
            obj = json.loads(get(obj["url"], token))["object"]
        if obj["type"] == "commit":
            return tag, obj["sha"]
    raise RuntimeError(f"{repo}: no suitable release in last 100 releases")

def download(repo, tag, sha, patterns, out, token):
    base = API + repo
    release = json.loads(get(base + "/releases/tags/" + tag, token))
    # Release target_commitish can be a branch, so verify actual tag ref too.
    ref = json.loads(get(base + "/git/ref/tags/" + tag, token))
    obj = ref["object"]
    if obj["type"] == "tag":
        obj = json.loads(get(obj["url"], token))["object"]
    if obj["type"] != "commit" or obj["sha"] != sha:
        raise RuntimeError(f"{repo}:{tag} points to {obj['sha']}, expected {sha}")
    assets = release["assets"]
    for label, pattern in patterns.items():
        matches = [a for a in assets if re.fullmatch(pattern, a["name"])]
        if len(matches) != 1:
            raise RuntimeError(f"{repo}:{tag}: expected exactly one {label} asset, found {len(matches)}")
        asset = matches[0]
        data = get(asset["url"], token, "application/octet-stream")
        # GitHub may return JSON metadata instead of binary depending on API gateway.
        if data.startswith(b"{") and b'"browser_download_url"' in data[:2000]:
            raise RuntimeError("GitHub returned asset metadata, not binary")
        if len(data) != asset["size"]:
            raise RuntimeError(f"{label}: download size mismatch")
        (out / label).write_bytes(data)
        print(f"{repo}: {label} <- {asset['name']} ({len(data)} bytes)")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--backend-tag")
    p.add_argument("--backend-sha")
    p.add_argument("--ui-tag")
    p.add_argument("--ui-sha")
    p.add_argument("--firmware-tag")
    p.add_argument("--firmware-sha")
    p.add_argument("--output", default="downloaded")
    a = p.parse_args()
    token = os.environ.get("WB_RELEASE_TOKEN")
    if not token:
        p.error("WB_RELEASE_TOKEN is required (read access to all three source repositories)")
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    specs = [
        ("wb.srv", a.backend_tag, a.backend_sha, {"backend.jar": r"wb\\.srv-.*\\.jar"}),
        ("wb.ui", a.ui_tag, a.ui_sha, {"ui.zip": r"ui-.*\\.zip"}),
        ("wb.arduino", a.firmware_tag, a.firmware_sha,
         {"app.bin": r"wb-v4-esp32s3-(?!bootloader|partitions).*\\.bin",
          "bootloader.bin": r"wb-v4-esp32s3\\.bootloader-.*\\.bin",
          "partitions.bin": r"wb-v4-esp32s3\\.partitions-.*\\.bin"})
    ]
    selected = {}
    for repo, tag, sha, patterns in specs:
        if (tag is None) != (sha is None):
            p.error(f"{repo}: tag and SHA must be supplied together")
        if tag is None:
            tag, sha = latest_release(repo, patterns, token)
        print(f"Selected {repo}: {tag} ({sha})")
        download(repo, tag, sha, patterns, out, token)
        selected[repo] = {"tag": tag, "sha": sha}
    (out / "selected-releases.json").write_text(json.dumps(selected, indent=2) + "\\n")

if __name__ == "__main__":
    main()
