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
    p.add_argument("--backend-tag", required=True)
    p.add_argument("--backend-sha", required=True)
    p.add_argument("--ui-tag", required=True)
    p.add_argument("--ui-sha", required=True)
    p.add_argument("--firmware-tag", required=True)
    p.add_argument("--firmware-sha", required=True)
    p.add_argument("--output", default="downloaded")
    a = p.parse_args()
    token = os.environ.get("WB_RELEASE_TOKEN")
    if not token:
        p.error("WB_RELEASE_TOKEN is required (read access to all three source repositories)")
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    download("wb.srv", a.backend_tag, a.backend_sha,
             {"backend.jar": r"wb\.srv-.*\.jar"}, out, token)
    download("wb.ui", a.ui_tag, a.ui_sha,
             {"ui.zip": r"ui-.*\.zip"}, out, token)
    download("wb.arduino", a.firmware_tag, a.firmware_sha,
             {"app.bin": r"wb-v4-esp32s3-(?!bootloader|partitions).*\.bin",
              "bootloader.bin": r"wb-v4-esp32s3\.bootloader-.*\.bin",
              "partitions.bin": r"wb-v4-esp32s3\.partitions-.*\.bin"}, out, token)

if __name__ == "__main__":
    main()
