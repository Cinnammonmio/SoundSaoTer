"""Build SoundSaoTer.exe — one file, no Python needed on the friend's PC.

    python build.py

ผลลัพธ์อยู่ที่  dist/SoundSaoTer.exe
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = 'SoundSaoTer'
# the version, stamped into the bundle: lets the exe notice it was started on another
# version's unpacked files (see rthook_fresh.py)
STAMP = os.path.join(HERE, 'build', 'build_stamp.txt')

ARGS = [
    sys.executable, '-m', 'PyInstaller',
    '--noconfirm', '--clean',
    '--onefile',
    '--windowed',                       # no console window
    '--name', NAME,
    '--icon', os.path.join(HERE, 'icon.ico'),
    '--add-data', f"{os.path.join(HERE, 'icon.ico')}{os.pathsep}.",
    '--add-data', f"{STAMP}{os.pathsep}.",
    '--runtime-hook', os.path.join(HERE, 'rthook_fresh.py'),   # runs first: see the file
    '--collect-all', 'customtkinter',   # ships its theme json + assets
    '--collect-all', 'sounddevice',     # portaudio dll
    '--collect-all', 'soundfile',       # libsndfile dll (mp3/ogg/flac support)
    '--exclude-module', 'matplotlib',
    '--exclude-module', 'pytest',
    '--exclude-module', 'setuptools',   # dragged in via cffi's build helpers; at startup its
    '--exclude-module', 'pkg_resources',  # runtime hook alone costs ~15 MB of RAM for nothing
    '--collect-all', 'pystray',         # system tray icon
    '--collect-submodules', 'PIL',      # pystray renders the tray icon with Pillow
    '--exclude-module', 'PIL._avif',    # AVIF codec (7.5 MB) — the app never reads AVIF
    '--exclude-module', 'PIL.AvifImagePlugin',
    '--collect-submodules', 'yt_dlp',   # ✂ YouTube: extractors are loaded lazily by name
    '--collect-all', 'av',              # ✂ YouTube: PyAV + its ffmpeg dlls
    '--hidden-import', 'ytclip',
    os.path.join(HERE, 'soundboard.py'),
]


VENV_PY = os.path.join(HERE, '.venv', 'Scripts', 'python.exe')


def main():
    # always build with the project's Python 3.14 venv, whichever `python` ran this
    if os.path.isfile(VENV_PY) and os.path.normcase(sys.prefix) != os.path.normcase(os.path.join(HERE, '.venv')):
        print(f'ใช้ Python ของโปรเจกต์: {VENV_PY}')
        return subprocess.call([VENV_PY, os.path.abspath(__file__)] + sys.argv[1:])
    if sys.version_info < (3, 14):
        print(f'!! กำลังใช้ Python {sys.version.split()[0]} — ควร build ด้วย 3.14 (ดู README หัวข้อ "ติดตั้ง")')

    icon = os.path.join(HERE, 'icon.ico')
    if not os.path.isfile(icon):
        print('ยังไม่มี icon.ico — สร้างก่อน')
        subprocess.check_call([sys.executable, os.path.join(HERE, 'make_icon.py')])

    print('กำลังสร้าง exe (ใช้เวลาสัก 1–3 นาที) ...\n')
    sys.path.insert(0, HERE)
    import version
    os.makedirs(os.path.dirname(STAMP), exist_ok=True)
    with open(STAMP, 'w', encoding='utf-8') as fh:
        fh.write(version.VERSION)
    subprocess.check_call(ARGS, cwd=HERE)

    exe = os.path.join(HERE, 'dist', NAME + '.exe')
    size = os.path.getsize(exe) / (1024 * 1024)
    print(f'\nเสร็จแล้ว: {exe}  ({size:.0f} MB)')

    # a ready-to-send folder next to the exe
    share = os.path.join(HERE, 'dist', 'ส่งให้เพื่อน')
    os.makedirs(share, exist_ok=True)
    shutil.copy2(exe, share)
    readme = os.path.join(HERE, 'อ่านก่อนใช้.txt')
    if os.path.isfile(readme):
        shutil.copy2(readme, share)
    os.makedirs(os.path.join(share, 'sounds'), exist_ok=True)
    # VB-CABLE แจกต่อได้แบบไฟล์ต้นฉบับเท่านั้น — ก๊อปทั้ง zip ไม่แตกไม่แก้
    for name in os.listdir(os.path.join(HERE, 'vbcable')) if os.path.isdir(os.path.join(HERE, 'vbcable')) else []:
        if name.lower().endswith('.zip'):
            shutil.copy2(os.path.join(HERE, 'vbcable', name), share)
    print(f'โฟลเดอร์พร้อมส่ง: {share}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
