"""ตรวจหาเวอร์ชันใหม่จากไฟล์ประกาศบนเน็ต แล้วเปลี่ยนตัวเองเป็นเวอร์ชันใหม่

Windows ห้ามเขียนทับไฟล์ exe ที่กำลังรันอยู่ แต่ "เปลี่ยนชื่อ" ได้
เลยใช้วิธี: เปลี่ยนชื่อตัวเก่าเป็น .old -> ย้ายตัวใหม่มาแทน -> เปิดตัวใหม่ -> ออก
แล้วรอบหน้าค่อยลบ .old ทิ้ง
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.request

import version

UA = {'User-Agent': f'SoundSaoTer/{version.VERSION}'}
TIMEOUT = 20


def running_exe():
    """path ของ exe ตัวเอง หรือ None ถ้ารันจาก source"""
    return os.path.abspath(sys.argv[0]) if getattr(sys, 'frozen', False) else None


def cleanup_old():
    """ลบซากเวอร์ชันก่อนหน้าที่ค้างจากการอัปเดตรอบที่แล้ว"""
    exe = running_exe()
    if not exe:
        return False
    old = exe + '.old'
    if os.path.exists(old):
        try:
            os.remove(old)
            return True
        except OSError:
            pass          # ยังถูกจับอยู่ ไว้ลบรอบหน้า
    return False


def check(manifest_url=None, current=None):
    """คืน dict ของเวอร์ชันใหม่ถ้ามี ไม่มีก็คืน None

    ไฟล์ประกาศหน้าตาแบบนี้:
        {"version": "1.1.0", "url": "https://.../SoundSaoTer.exe",
         "sha256": "....", "notes": "แก้โน่นนี่"}
    """
    url = manifest_url or version.UPDATE_MANIFEST_URL
    if not url:
        return None
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode('utf-8', 'replace'))
    if not isinstance(data, dict) or not data.get('version') or not data.get('url'):
        raise ValueError('ไฟล์ประกาศอัปเดตผิดรูปแบบ')
    if not version.is_newer(data['version'], current or version.VERSION):
        return None
    return data


def download(info, dest_dir=None, on_progress=None):
    """โหลด exe ใหม่มาไว้ข้าง ๆ ตัวเก่า แล้วคืน path"""
    exe = running_exe()
    dest_dir = dest_dir or (os.path.dirname(exe) if exe else os.getcwd())
    dest = os.path.join(dest_dir, f"SoundSaoTer-{info['version']}.new")
    part = dest + '.part'

    req = urllib.request.Request(info['url'], headers=UA)
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get('Content-Length') or 0)
        done = 0
        if on_progress:
            on_progress(0, total)
        with open(part, 'wb') as fh:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)

    want = (info.get('sha256') or '').strip().lower()
    if want and digest.hexdigest() != want:
        os.remove(part)
        raise ValueError('ไฟล์ที่โหลดมาไม่ตรงกับลายนิ้วมือที่ประกาศไว้ — ยกเลิกเพื่อความปลอดภัย')
    if done < 1024 * 1024:
        os.remove(part)
        raise ValueError(f'ไฟล์เล็กผิดปกติ ({done} ไบต์) น่าจะโหลดมาไม่ครบ')

    os.replace(part, dest)
    return dest


def apply(new_path, relaunch=True):
    """สลับตัวเองเป็นเวอร์ชันใหม่ แล้วเปิดตัวใหม่ขึ้นมา — เรียกตอนกำลังจะปิดโปรแกรม"""
    exe = running_exe()
    if not exe:
        raise RuntimeError('อัปเดตอัตโนมัติใช้ได้เฉพาะตอนรันจากไฟล์ exe')
    old = exe + '.old'
    if os.path.exists(old):
        try:
            os.remove(old)
        except OSError:
            pass
    os.replace(exe, old)            # Windows ยอมให้เปลี่ยนชื่อ exe ที่กำลังรัน
    try:
        os.replace(new_path, exe)
    except OSError:
        os.replace(old, exe)        # ย้ายตัวใหม่ไม่สำเร็จ เอาตัวเก่ากลับมา
        raise
    if relaunch:
        # a fresh environment: otherwise PyInstaller treats the new exe as our child
        # and runs it on this (old) version's unpacked files
        subprocess.Popen([exe], cwd=os.path.dirname(exe), close_fds=True,
                         env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'))
    return exe
