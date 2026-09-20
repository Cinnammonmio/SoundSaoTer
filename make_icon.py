"""Generate icon.ico — a rounded square with a purple→pink wash and an equaliser mark."""
import os

from PIL import Image, ImageDraw

SIZE = 512
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')

TOP_LEFT = (139, 63, 224)      # purple
BOTTOM_RIGHT = (236, 72, 153)  # pink


def diagonal_wash(size, c1, c2):
    img = Image.new('RGB', (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2.0 * (size - 1))
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))
    return img


def main():
    base = diagonal_wash(SIZE, TOP_LEFT, BOTTOM_RIGHT).convert('RGBA')

    mask = Image.new('L', (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=int(SIZE * 0.23), fill=255)
    base.putalpha(mask)

    draw = ImageDraw.Draw(base)
    bar_w = int(SIZE * 0.075)
    gap = int(SIZE * 0.055)
    heights = (0.30, 0.52, 0.40, 0.62, 0.34)   # equaliser bars
    total = len(heights) * bar_w + (len(heights) - 1) * gap
    x = (SIZE - total) / 2
    mid = SIZE / 2
    for h in heights:
        half = SIZE * h / 2
        draw.rounded_rectangle((x, mid - half, x + bar_w, mid + half),
                               radius=bar_w // 2, fill=(255, 255, 255, 235))
        x += bar_w + gap

    base.save(OUT, format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print('wrote', OUT, os.path.getsize(OUT), 'bytes')


if __name__ == '__main__':
    main()
