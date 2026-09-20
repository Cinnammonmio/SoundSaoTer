"""ปล่อยเวอร์ชันใหม่ขึ้น GitHub Releases

ขั้นตอนออกเวอร์ชันใหม่:
    1. แก้ VERSION กับ CHANGES ใน version.py
    2. python build.py
    3. python publish.py

สคริปต์จะสร้าง release ตามเลขเวอร์ชัน อัป exe ขึ้นไป แล้วเขียน latest.json
ที่ตัวโปรแกรมของทุกคนใช้ตรวจว่ามีของใหม่หรือยัง
"""
import hashlib
import json
import os
import subprocess
import sys

import version

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, 'dist', 'SoundSaoTer.exe')
MANIFEST = os.path.join(HERE, 'dist', 'latest.json')
ASSET = 'SoundSaoTer.exe'


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, encoding='utf-8', **kw)


def repo_slug():
    out = run(['gh', 'repo', 'view', '--json', 'nameWithOwner', '-q', '.nameWithOwner'], cwd=HERE)
    if out.returncode != 0:
        raise SystemExit('ยังไม่ได้ผูกกับ repo บน GitHub — รัน gh repo create ก่อน\n' + out.stderr)
    return out.stdout.strip()


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def notes_for(v):
    for num, text in version.CHANGES:
        if num == v:
            return text
    return f'เวอร์ชัน {v}'


def main():
    if not os.path.isfile(EXE):
        raise SystemExit('ยังไม่มี dist/SoundSaoTer.exe — รัน python build.py ก่อน')
    if run(['gh', 'auth', 'status']).returncode != 0:
        raise SystemExit('ยังไม่ได้ล็อกอิน GitHub — รัน  gh auth login  ในเทอร์มินัลของคุณก่อน')

    v = version.VERSION
    slug = repo_slug()
    tag = f'v{v}'
    base = f'https://github.com/{slug}/releases/latest/download'
    digest = sha256(EXE)

    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    with open(MANIFEST, 'w', encoding='utf-8') as fh:
        json.dump({'version': v,
                   'url': f'{base}/{ASSET}',
                   'sha256': digest,
                   'notes': notes_for(v),
                   'date': version.BUILD_DATE}, fh, ensure_ascii=False, indent=2)

    print(f'repo    : {slug}')
    print(f'เวอร์ชัน : {v}   ({os.path.getsize(EXE) / 1024 / 1024:.1f} MB)')
    print(f'sha256  : {digest[:16]}...')

    exists = run(['gh', 'release', 'view', tag], cwd=HERE).returncode == 0
    if exists:
        print(f'มี {tag} อยู่แล้ว — อัปทับไฟล์เดิม')
        out = run(['gh', 'release', 'upload', tag, EXE, MANIFEST, '--clobber'], cwd=HERE)
    else:
        out = run(['gh', 'release', 'create', tag, EXE, MANIFEST,
                   '--title', f'SoundSaoTer {tag}', '--notes', notes_for(v)], cwd=HERE)
    if out.returncode != 0:
        raise SystemExit('ปล่อยเวอร์ชันไม่สำเร็จ:\n' + (out.stderr or out.stdout))

    print(f'\nเสร็จแล้ว: https://github.com/{slug}/releases/tag/{tag}')
    print(f'ไฟล์ประกาศ: {base}/latest.json')
    if version.UPDATE_MANIFEST_URL != f'{base}/latest.json':
        print('\n!! ยังไม่ได้ตั้ง UPDATE_MANIFEST_URL ใน version.py ให้เป็นค่านี้')
        print('   ตั้งแล้วต้อง build + publish ใหม่อีกรอบ ตัวโปรแกรมถึงจะรู้จักที่อยู่นี้')
    return 0


if __name__ == '__main__':
    sys.exit(main())
