"""Download sounds from the supported sites by ID or search.

    python download_voice.py 401945                  โหลดเสียง Dota ID นั้นลง sounds/
    python download_voice.py 401944 401945           โหลดหลายอันพร้อมกัน
    python download_voice.py --find Ams              ค้นหาในเว็บ Dota
    python download_voice.py --site myinstants --find rampage
    python download_voice.py --site myinstants --find "https://www.myinstants.com/en/categories/sound%20effects/us/"
    python download_voice.py --site myinstants --get 32701
"""
import argparse
import os
import sys

import sources

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SOUNDS_DIR = os.path.join(APP_DIR, 'sounds')


def fetch_catalog():
    """Kept for callers that only care about the Dota catalog."""
    return sources.BY_KEY['dota'].catalog()


def download(entry, out_dir, source=None):
    return sources.download(entry, out_dir, source)


def resolve(src, token):
    """A Dota ID, or — for Myinstants — the instant page URL that --find prints."""
    if src.key == 'dota':
        return src.catalog().get(token)
    if token.lower().startswith('http'):
        hits = src.search(token)
        return hits[0] if hits else None
    return None


def main():
    ap = argparse.ArgumentParser(description='โหลดเสียงจาก dota2voicelines.com / myinstants.com')
    ap.add_argument('ids', nargs='*', help='ID ของเสียง (เว็บ Dota) เช่น 401945')
    ap.add_argument('--site', default='dota', choices=sorted(sources.BY_KEY),
                    help='เว็บที่จะใช้ (ค่าเริ่มต้น: dota)')
    ap.add_argument('--find', metavar='คำค้น', help='ค้นหา แล้วแสดงรายการ')
    ap.add_argument('--get', nargs='+', metavar='ID', help='โหลดตาม ID ที่ได้จาก --find')
    ap.add_argument('--out', default=SOUNDS_DIR, help='โฟลเดอร์ปลายทาง (ค่าเริ่มต้น: sounds/)')
    args = ap.parse_args()

    src = sources.BY_KEY[args.site]
    wanted = list(args.ids) + list(args.get or [])
    if not wanted and not args.find:
        ap.print_help()
        return 1

    if args.find:
        print(f'กำลังค้นหาใน {src.label} ...')
        hits = src.search(args.find)
        if not hits:
            print(f'ไม่พบอะไรที่ตรงกับ {args.find!r}')
            return 1
        for e in hits[:200]:
            tail = f"  {e['page']}" if e.get('page') else ''
            print(f"  {e['id']:>8}  {e['creator']:<14} {e['text']}{tail}")
        print(f"\nพบ {len(hits)} รายการ")
        token = hits[0].get('page') or hits[0]['id']
        print(f'โหลดด้วย: python download_voice.py --site {args.site} --get "{token}"')
        return 0

    missing = []
    for vid in wanted:
        entry = resolve(src, vid)
        if entry is None:
            missing.append(vid)
            print(f'{vid}: ไม่พบ — ใช้ --find ก่อน แล้วคัดลอกค่าที่มันแนะนำมา')
            continue
        print(f"{vid}: {entry['text']}  — {entry['creator']}")
        path = download(entry, args.out, src)
        size = os.path.getsize(path) / 1024
        print(f'  บันทึก: {os.path.basename(path)}  ({size:.0f} KB)')

    print(f'\nเสร็จแล้ว → {args.out}')
    print('เปิดโปรแกรมแล้วกด "เพิ่มทั้งโฟลเดอร์..." เลือกโฟลเดอร์นี้ได้เลย')
    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
