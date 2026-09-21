"""Colour themes. Widgets read these module globals when they are built, so switching
theme = apply() + rebuild the window.

Role names are historical: PURPLE is the primary accent, PINK the secondary and BLUE
the third (hotkey badges) — in the green theme "PURPLE" is green.
"""

FONT = 'Segoe UI'

# ชื่อธีม -> สี  (MODE คือโหมดของ customtkinter เอง: 'dark' หรือ 'light')
THEMES = {
    'ม่วงนีออน': dict(
        MODE='dark',
        BG='#0E0B18', SURFACE='#17122A', SURFACE_2='#1F1838', SURFACE_3='#2A2150',
        INPUT='#221B3D', BORDER='#342B59',
        PURPLE='#9333EA', PURPLE_DARK='#7E22CE', PINK='#DB2777', PINK_DARK='#BE185D',
        BLUE='#4F46E5', BLUE_DARK='#4338CA',
        TEXT='#EDE9FE', TEXT_DIM='#B3A6DD', TEXT_FAINT='#9585C4',
        OK='#34D399', WARN='#FBBF24', DANGER='#F87171'),
    'ไซเบอร์ฟ้า': dict(
        MODE='dark',
        BG='#060E18', SURFACE='#0C1A29', SURFACE_2='#112336', SURFACE_3='#183047',
        INPUT='#10263A', BORDER='#1F3B57',
        PURPLE='#22D3EE', PURPLE_DARK='#06B6D4', PINK='#60A5FA', PINK_DARK='#3B82F6',
        BLUE='#818CF8', BLUE_DARK='#6366F1',
        TEXT='#E0F2FE', TEXT_DIM='#A5CBE3', TEXT_FAINT='#7FA6C2',
        OK='#34D399', WARN='#FBBF24', DANGER='#F87171'),
    'เขียวนีออน': dict(
        MODE='dark',
        BG='#060C08', SURFACE='#0D1711', SURFACE_2='#122119', SURFACE_3='#192D22',
        INPUT='#111F18', BORDER='#20382A',
        PURPLE='#4ADE80', PURPLE_DARK='#22C55E', PINK='#A3E635', PINK_DARK='#84CC16',
        BLUE='#2DD4BF', BLUE_DARK='#14B8A6',
        TEXT='#E6FBEE', TEXT_DIM='#A2D4B6', TEXT_FAINT='#7BAA8E',
        OK='#4ADE80', WARN='#FACC15', DANGER='#F87171'),
    'แดงเลือด': dict(
        MODE='dark',
        BG='#0F0606', SURFACE='#1A0C0C', SURFACE_2='#241111', SURFACE_3='#321818',
        INPUT='#221010', BORDER='#3F1D1D',
        PURPLE='#FB5A75', PURPLE_DARK='#F43F5E', PINK='#FB923C', PINK_DARK='#F97316',
        BLUE='#F87171', BLUE_DARK='#EF4444',
        TEXT='#FDECEC', TEXT_DIM='#E0AFAF', TEXT_FAINT='#B98484',
        OK='#34D399', WARN='#FBBF24', DANGER='#FCA5A5'),
    'ส้มลาวา': dict(
        MODE='dark',
        BG='#0F0904', SURFACE='#1B1109', SURFACE_2='#25170C', SURFACE_3='#332011',
        INPUT='#23160B', BORDER='#402A15',
        PURPLE='#FB923C', PURPLE_DARK='#F97316', PINK='#FACC15', PINK_DARK='#EAB308',
        BLUE='#F87171', BLUE_DARK='#EF4444',
        TEXT='#FFF4E6', TEXT_DIM='#E3C09B', TEXT_FAINT='#B8966F',
        OK='#4ADE80', WARN='#FDE047', DANGER='#F87171'),
    'ซินธ์เวฟ': dict(
        MODE='dark',
        BG='#0C0220', SURFACE='#170A30', SURFACE_2='#200F40', SURFACE_3='#2C1553',
        INPUT='#1D0C3A', BORDER='#3A1F66',
        PURPLE='#FF3EA5', PURPLE_DARK='#F0177F', PINK='#22E3FF', PINK_DARK='#00C8E6',
        BLUE='#FFB627', BLUE_DARK='#F59E0B',
        TEXT='#FCE7FF', TEXT_DIM='#D2ADE6', TEXT_FAINT='#A887C2',
        OK='#34D399', WARN='#FFB627', DANGER='#FF6B8B'),
    'สว่าง': dict(
        MODE='light',
        BG='#F3F1F9', SURFACE='#FFFFFF', SURFACE_2='#F4F1FB', SURFACE_3='#E8E2F7',
        INPUT='#EEEAF8', BORDER='#D6CDEE',
        PURPLE='#7C3AED', PURPLE_DARK='#6D28D9', PINK='#DB2777', PINK_DARK='#BE185D',
        BLUE='#4F46E5', BLUE_DARK='#4338CA',
        TEXT='#1C1230', TEXT_DIM='#463A66', TEXT_FAINT='#62577F',
        OK='#047857', WARN='#92400E', DANGER='#B91C1C'),
}
DEFAULT = 'ม่วงนีออน'
NAMES = list(THEMES)

NEAR_BLACK = '#0B0B12'
WHITE = '#FFFFFF'


# ---------------------------------------------------------------- contrast helpers
def _luminance(hex_color):
    h = hex_color.lstrip('#')
    rgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    """WCAG contrast ratio, 1.0 (none) .. 21.0 (black on white)."""
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def pick_on_accent(palette):
    """White or near-black text for accent buttons — whichever reads better on all of them.

    Neon green/cyan/yellow fills are too bright for white labels, so those themes
    get dark text on their buttons instead.
    """
    accents = [palette[k] for k in ('PURPLE', 'PURPLE_DARK', 'PINK', 'PINK_DARK', 'BLUE')]
    worst = lambda ink: min(contrast(ink, a) for a in accents)
    return WHITE if worst(WHITE) >= worst(NEAR_BLACK) else NEAR_BLACK


# ---------------------------------------------------------------- active theme
NAME = DEFAULT
MODE = 'dark'
BG = SURFACE = SURFACE_2 = SURFACE_3 = INPUT = BORDER = ''
PURPLE = PURPLE_DARK = PINK = PINK_DARK = BLUE = BLUE_DARK = ''
TEXT = TEXT_DIM = TEXT_FAINT = OK = WARN = DANGER = ON_ACCENT = ''


def apply(name):
    """Make `name` the active palette. Unknown names fall back to the default."""
    global NAME, ON_ACCENT
    if name not in THEMES:
        name = DEFAULT
    palette = THEMES[name]
    globals().update(palette)
    NAME = name
    ON_ACCENT = pick_on_accent(palette)
    return name


apply(DEFAULT)


def font(size=13, weight='normal'):
    import customtkinter as ctk
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


def short_device(label):
    """'Headphones (AirPods Pro)  [Windows WASAPI]' -> 'Headphones (AirPods Pro) · WASAPI'"""
    if '  [' not in label:
        return label
    name, _, api = label.partition('  [')
    api = api.rstrip(']').replace('Windows ', '').strip()
    return f'{name.strip()} · {api}'
