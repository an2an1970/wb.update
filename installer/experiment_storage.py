#!/usr/bin/env python3
"""Idempotent migration of WB experiment storage. Run as root while wb-srv is stopped."""
import hashlib
import os
from pathlib import Path
import pwd
import grp
import shutil
import stat
import subprocess
import tempfile

DEST = Path("/var/lib/wb/exp")
BASE = Path("/var/lib/wb")
PROP = "wb-srv.files-save-path"
VALUE = "/var/lib/wb"

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.digest()

def entries(root):
    result = {}
    if not root.exists():
        return result
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"Not a real directory: {root}")
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            p = Path(parent) / name
            if p.is_symlink():
                raise RuntimeError(f"Symlinks in experiment storage are not supported: {p}")
            mode = p.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise RuntimeError(f"Unsupported experiment entry: {p}")
            result[p.relative_to(root)] = p
    return result

def service_identity():
    def field(name):
        value = subprocess.check_output(
            ["systemctl", "show", "wb-srv.service", "-p", name, "--value"], text=True
        ).strip()
        return value
    user = field("User")
    if not user:
        raise RuntimeError("wb-srv.service has no User=; refusing to use root")
    group = field("Group") or pwd.getpwnam(user).pw_gid
    uid = pwd.getpwnam(user).pw_uid
    gid = grp.getgrnam(group).gr_gid if isinstance(group, str) else group
    return user, uid, gid

def check_compatible(src, dest):
    source = entries(src)
    target = entries(dest)
    for rel, p in source.items():
        other = target.get(rel)
        if other is None:
            continue
        if p.is_dir() != other.is_dir():
            raise RuntimeError(f"Experiment path conflict: {rel}")
        if p.is_file() and (p.stat().st_size != other.stat().st_size or digest(p) != digest(other)):
            raise RuntimeError(f"Experiment content conflict: {rel}")
    return source

def update_properties(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError(f"Unsafe properties file: {path}")
    original = path.read_bytes() if path.exists() else b""
    text = original.decode("utf-8")
    lines = text.splitlines(keepends=True)
    indices = [i for i, line in enumerate(lines)
               if line.lstrip().startswith(PROP + "=") or line.lstrip().startswith(PROP + " =")]
    if len(indices) > 1:
        raise RuntimeError(f"Duplicate {PROP} settings")
    if indices:
        i = indices[0]
        if lines[i].strip().split("=", 1)[1].strip() == VALUE:
            return
        newline = "\\r\\n" if lines[i].endswith("\\r\\n") else "\\n"
        lines[i] = PROP + "=" + VALUE + newline
    else:
        if lines and not lines[-1].endswith(("\\n", "\\r")):
            lines[-1] += "\\n"
        lines.append(PROP + "=" + VALUE + "\\n")
    new = "".join(lines).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".application-local-", dir=path.parent)
    try:
        if path.exists():
            shutil.copystat(path, tmp)
            os.fchown(fd, path.stat().st_uid, path.stat().st_gid)
        with os.fdopen(fd, "wb") as stream:
            stream.write(new)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def configure(root=Path("/opt/wb.srv"), dry_run=False):
    user, uid, gid = service_identity()
    old = root / "exp"
    prop = root / "application-local.properties"
    if old.is_symlink():
        if old.resolve(strict=False) != DEST:
            raise RuntimeError(f"Unexpected experiment symlink: {old}")
        source = None
    elif old.exists():
        source = old
    else:
        source = None
    if BASE.is_symlink() or DEST.is_symlink():
        raise RuntimeError("Storage destination cannot be a symlink")
    check_compatible(source, DEST) if source else entries(DEST)
    if dry_run:
        print(f"Experiment storage preflight OK; service user: {user}")
        return
    BASE.mkdir(parents=True, exist_ok=True)
    DEST.mkdir(parents=True, exist_ok=True)
    if source:
        for rel, p in entries(source).items():
            target = DEST / rel
            if p.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                # Stage and verify before publishing each file.
                fd, tmp = tempfile.mkstemp(prefix=".wb-exp-", dir=target.parent)
                os.close(fd)
                try:
                    shutil.copy2(p, tmp)
                    if digest(p) != digest(Path(tmp)):
                        raise RuntimeError(f"Experiment copy verification failed: {rel}")
                    os.replace(tmp, target)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
        check_compatible(source, DEST)
    # Apply ownership only to experiment storage; keep the parent traversable.
    os.chown(BASE, uid, gid)
    for parent, dirs, files in os.walk(DEST):
        os.chown(parent, uid, gid)
        for name in dirs + files:
            os.chown(os.path.join(parent, name), uid, gid)
    # Verify access as the service identity, not root.
    subprocess.run(["runuser", "-u", user, "--", "test", "-w", str(DEST)], check=True)
    subprocess.run(["runuser", "-u", user, "--", "test", "-x", str(DEST)], check=True)
    if source:
        # Keep original data in a separate backup until explicitly cleaned up.
        backup = root / "backup" / "exp-before-migration"
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists() or backup.is_symlink():
            raise RuntimeError(f"Experiment backup already exists: {backup}")
        source.rename(backup)
        try:
            old.symlink_to(DEST, target_is_directory=True)
        except BaseException:
            backup.rename(old)
            raise
    elif not old.is_symlink():
        old.symlink_to(DEST, target_is_directory=True)
    update_properties(prop)
    print(f"Experiment storage ready: {old} -> {DEST} ({user})")

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    configure(dry_run=a.dry_run)
