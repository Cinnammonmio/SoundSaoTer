# rnnoise.dll

ตัวตัดเสียงรบกวนของไมค์ (ใช้ใน [micfx.py](../../micfx.py))

- **RNNoise** — Xiph.Org / Jean-Marc Valin · https://github.com/xiph/rnnoise · ลิขสิทธิ์แบบ BSD 3-Clause ([COPYING-rnnoise.txt](COPYING-rnnoise.txt))
- ไฟล์ `rnnoise.dll` (Windows x64) นำมาจากแพ็กเกจ **pyrnnoise 0.4.3** บน PyPI
  (https://github.com/pengzhendong/pyrnnoise · Apache-2.0, [LICENSE-pyrnnoise.txt](LICENSE-pyrnnoise.txt)) ไม่ได้แก้ไข
- ใช้เฉพาะตัว DLL เรียกผ่าน ctypes เอง ไม่ได้ติดตั้ง pyrnnoise (มันพ่วง matplotlib มาด้วย)
