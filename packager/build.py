#!/usr/bin/env python3
"""Build a deterministic WB package from exact local release assets (Python 3.10+)."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

SHA = re.compile(r'^[0-9a-f]{40}$')
NAMES = ('backend/wb.srv.jar', 'ui/dist.zip', 'firmware/v4/app.bin',
         'firmware/v4/bootloader.bin', 'firmware/v4/partitions.bin')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_zip(data):
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        files = [n for n in z.namelist() if not n.endswith('/')]
        if not files:
            raise ValueError('Empty UI ZIP')
        for n in z.namelist():
            p = __import__('pathlib').PurePosixPath(n)
            if p.is_absolute() or '..' in p.parts or '\\' in n:
                raise ValueError('Unsafe UI ZIP path: ' + n)


def build(args):
    shas = dict(zip(('backend', 'ui', 'firmware'), (args.backend_sha, args.ui_sha, args.firmware_sha)))
    if not all(SHA.fullmatch(s) for s in shas.values()):
        raise ValueError('Full 40-character Git SHA required for each component')
    inputs = (args.backend, args.ui, args.firmware, args.bootloader, args.partitions)
    contents = dict(zip(NAMES, (Path(p).read_bytes() for p in inputs)))
    for name, data in contents.items():
        if not data:
            raise ValueError('Empty asset: ' + name)
    if not contents[NAMES[0]].startswith(b'PK'):
        raise ValueError('Backend is not a JAR/ZIP')
    validate_zip(contents[NAMES[1]])
    manifest = {
        'schema': 1,
        'version': args.version,
        'target': {'board': 'wb-v4', 'mcu': 'esp32s3'},
        'components': {key: {'repository': f'an2an1970/{repo}', 'git_sha': shas[key]}
                       for key, repo in [('backend','wb.srv'),('ui','wb.ui'),('firmware','wb.arduino')]},
        'files': {name: {'sha256': digest(data), 'size': len(data)} for name, data in sorted(contents.items())},
        'firmware': {'variant': 'v4', 'images': {'app': NAMES[2], 'bootloader': NAMES[3], 'partitions': NAMES[4]},
                     'flash_offsets': None},
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode()
    contents['manifest.json'] = manifest_bytes
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output


def verify(path):
    with zipfile.ZipFile(path) as z:
        if len(z.namelist()) != len(set(z.namelist())):
            raise ValueError('Duplicate ZIP entries')
        manifest = json.loads(z.read('manifest.json'))
        if set(z.namelist()) != set(manifest['files']) | {'manifest.json'}:
            raise ValueError('Unexpected/missing entries')
        for name, spec in manifest['files'].items():
            data = z.read(name)
            if digest(data) != spec['sha256'] or len(data) != spec['size']:
                raise ValueError('Corrupt: ' + name)
        return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    for arg in ('version','backend','ui','firmware','bootloader','partitions','backend-sha','ui-sha','firmware-sha','output'):
        b.add_argument('--' + arg, required=True)
    v = sub.add_parser('verify'); v.add_argument('package')
    a = p.parse_args()
    if a.cmd == 'build':
        output = build(a)
        verify(output)
        print(f'Created and verified {output} ({output.stat().st_size} bytes)')
    else:
        manifest = verify(a.package)
        print(json.dumps({'valid_checksums': True, 'version': manifest['version'], 'target': manifest['target']}, indent=2))


if __name__ == '__main__':
    main()
