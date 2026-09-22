"""เวอร์ชันของโปรแกรม และที่อยู่ของไฟล์ประกาศอัปเดต

เวลาจะออกเวอร์ชันใหม่:
  1. แก้ VERSION ที่นี่
  2. เขียนสิ่งที่เปลี่ยนไว้ใน CHANGES
  3. python build.py        -> ได้ exe ใหม่
  4. python publish.py      -> อัปขึ้นที่ฝากไฟล์ พร้อมไฟล์ประกาศ
"""

VERSION = '1.2.0'
BUILD_DATE = '2026-09-22'

# ไฟล์ JSON ที่บอกว่าเวอร์ชันล่าสุดคืออะไรและโหลดได้จากไหน
# ว่างไว้ = ปิดระบบตรวจอัปเดต
UPDATE_MANIFEST_URL = 'https://github.com/Cinnammonmio/SoundSaoTer/releases/latest/download/latest.json'

CHANGES = [
    ('1.2.0', 'ปรับดังเบาแยกแต่ละเสียง, ปุ่ม/คีย์ลัดสุ่มเสียง, กันสแปมคีย์ลัด, '
              'ตัดช่วงเงียบและปรับทุกเสียงให้ดังเท่ากันอัตโนมัติ, เปิดพร้อม Windows, '
              'จำกัดแรมที่ใช้เก็บเสียง (ค่าเริ่มต้น 40 MB)'),
    ('1.1.0', 'ธีมใหม่ 7 แบบ — ม่วงนีออน, ไซเบอร์ฟ้า, เขียวนีออน, แดงเลือด, ส้มลาวา, ซินธ์เวฟ และธีมสว่าง '
              'กดปุ่ม 🎨 ธีม มุมขวาบนเพื่อเปลี่ยน เปลี่ยนกลางเกมได้ เสียงไม่สะดุด'),
    ('1.0.0', 'เวอร์ชันแรก — soundboard, ฮอตคีย์, ผสมไมค์, โหลดเสียงจากเว็บ, tray, ฟังเสียงตัวเอง'),
]


def as_tuple(text):
    """'1.2.3' -> (1, 2, 3) เอาไว้เทียบว่าเวอร์ชันไหนใหม่กว่า"""
    parts = []
    for chunk in str(text or '0').strip().lstrip('vV').split('.'):
        digits = ''.join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(candidate, current=VERSION):
    return as_tuple(candidate) > as_tuple(current)
