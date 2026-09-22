"""Colour themes: a colour scheme and a dark/light mode, chosen independently.
Widgets read these module globals when they are built, so switching = apply() + rebuild.

Role names are historical: PURPLE is the primary accent, PINK the secondary and BLUE
the third (hotkey badges) — in the green theme "PURPLE" is green.
"""

FONT = 'Segoe UI'

LIGHT_STATUS = dict(OK='#047857', WARN='#92400E', DANGER='#B91C1C')

# สีธีม -> {'dark': ..., 'light': ...}   เลือกสีกับโหมดแยกกันได้ทุกคู่
COLORS = {
    'ม่วงนีออน': dict(
        dark=dict(
            BG='#0E0B18', SURFACE='#17122A', SURFACE_2='#1F1838', SURFACE_3='#2A2150',
            INPUT='#221B3D', BORDER='#342B59',
            PURPLE='#9333EA', PURPLE_DARK='#7E22CE', PINK='#DB2777', PINK_DARK='#BE185D',
            BLUE='#4F46E5', BLUE_DARK='#4338CA',
            TEXT='#EDE9FE', TEXT_DIM='#B3A6DD', TEXT_FAINT='#9585C4',
            OK='#34D399', WARN='#FBBF24', DANGER='#F87171'),
        light=dict(
            BG='#F3F1F9', SURFACE='#FFFFFF', SURFACE_2='#F4F1FB', SURFACE_3='#E8E2F7',
            INPUT='#EEEAF8', BORDER='#D6CDEE',
            PURPLE='#7C3AED', PURPLE_DARK='#6D28D9', PINK='#DB2777', PINK_DARK='#BE185D',
            BLUE='#4F46E5', BLUE_DARK='#4338CA',
            TEXT='#1C1230', TEXT_DIM='#463A66', TEXT_FAINT='#62577F', **LIGHT_STATUS)),
    'ไซเบอร์ฟ้า': dict(
        dark=dict(
            BG='#060E18', SURFACE='#0C1A29', SURFACE_2='#112336', SURFACE_3='#183047',
            INPUT='#10263A', BORDER='#1F3B57',
            PURPLE='#22D3EE', PURPLE_DARK='#06B6D4', PINK='#60A5FA', PINK_DARK='#3B82F6',
            BLUE='#A5B4FC', BLUE_DARK='#818CF8',
            TEXT='#E0F2FE', TEXT_DIM='#A5CBE3', TEXT_FAINT='#7FA6C2',
            OK='#34D399', WARN='#FBBF24', DANGER='#F87171'),
        light=dict(
            BG='#EEF5FB', SURFACE='#FFFFFF', SURFACE_2='#EEF6FC', SURFACE_3='#DBEAF6',
            INPUT='#E5F0F9', BORDER='#C4DCEE',
            PURPLE='#0E7490', PURPLE_DARK='#155E75', PINK='#1D4ED8', PINK_DARK='#1E40AF',
            BLUE='#4F46E5', BLUE_DARK='#4338CA',
            TEXT='#0B1B2B', TEXT_DIM='#2F4B63', TEXT_FAINT='#4B6880', **LIGHT_STATUS)),
    'เขียวนีออน': dict(
        dark=dict(
            BG='#060C08', SURFACE='#0D1711', SURFACE_2='#122119', SURFACE_3='#192D22',
            INPUT='#111F18', BORDER='#20382A',
            PURPLE='#4ADE80', PURPLE_DARK='#22C55E', PINK='#A3E635', PINK_DARK='#84CC16',
            BLUE='#2DD4BF', BLUE_DARK='#14B8A6',
            TEXT='#E6FBEE', TEXT_DIM='#A2D4B6', TEXT_FAINT='#7BAA8E',
            OK='#4ADE80', WARN='#FACC15', DANGER='#F87171'),
        light=dict(
            BG='#EFF7F2', SURFACE='#FFFFFF', SURFACE_2='#EFF8F2', SURFACE_3='#DBEEE2',
            INPUT='#E5F3EA', BORDER='#C2E0CC',
            PURPLE='#15803D', PURPLE_DARK='#166534', PINK='#4D7C0F', PINK_DARK='#3F6212',
            BLUE='#0F766E', BLUE_DARK='#115E59',
            TEXT='#0B1F12', TEXT_DIM='#2D4937', TEXT_FAINT='#4A6755', **LIGHT_STATUS)),
    'แดงเลือด': dict(
        dark=dict(
            BG='#0F0606', SURFACE='#1A0C0C', SURFACE_2='#241111', SURFACE_3='#321818',
            INPUT='#221010', BORDER='#3F1D1D',
            PURPLE='#FB5A75', PURPLE_DARK='#F43F5E', PINK='#FB923C', PINK_DARK='#F97316',
            BLUE='#F87171', BLUE_DARK='#EF4444',
            TEXT='#FDECEC', TEXT_DIM='#E0AFAF', TEXT_FAINT='#B98484',
            OK='#34D399', WARN='#FBBF24', DANGER='#FCA5A5'),
        light=dict(
            BG='#FAF0F0', SURFACE='#FFFFFF', SURFACE_2='#FCF0F0', SURFACE_3='#F5DDDD',
            INPUT='#F7E7E7', BORDER='#EAC8C8',
            PURPLE='#BE123C', PURPLE_DARK='#9F1239', PINK='#C2410C', PINK_DARK='#9A3412',
            BLUE='#B91C1C', BLUE_DARK='#991B1B',
            TEXT='#2A0E0E', TEXT_DIM='#582F2F', TEXT_FAINT='#774B4B', **LIGHT_STATUS)),
    'ส้มลาวา': dict(
        dark=dict(
            BG='#0F0904', SURFACE='#1B1109', SURFACE_2='#25170C', SURFACE_3='#332011',
            INPUT='#23160B', BORDER='#402A15',
            PURPLE='#FB923C', PURPLE_DARK='#F97316', PINK='#FACC15', PINK_DARK='#EAB308',
            BLUE='#F87171', BLUE_DARK='#EF4444',
            TEXT='#FFF4E6', TEXT_DIM='#E3C09B', TEXT_FAINT='#B8966F',
            OK='#4ADE80', WARN='#FDE047', DANGER='#F87171'),
        light=dict(
            BG='#FAF4EC', SURFACE='#FFFFFF', SURFACE_2='#FCF4EB', SURFACE_3='#F4E3D0',
            INPUT='#F7EADB', BORDER='#E8D0B4',
            PURPLE='#C2410C', PURPLE_DARK='#9A3412', PINK='#A16207', PINK_DARK='#854D0E',
            BLUE='#B91C1C', BLUE_DARK='#991B1B',
            TEXT='#2A1706', TEXT_DIM='#583C20', TEXT_FAINT='#76573A', **LIGHT_STATUS)),
    'ซินธ์เวฟ': dict(
        dark=dict(
            BG='#0C0220', SURFACE='#170A30', SURFACE_2='#200F40', SURFACE_3='#2C1553',
            INPUT='#1D0C3A', BORDER='#3A1F66',
            PURPLE='#FF3EA5', PURPLE_DARK='#F0177F', PINK='#22E3FF', PINK_DARK='#00C8E6',
            BLUE='#FFB627', BLUE_DARK='#F59E0B',
            TEXT='#FCE7FF', TEXT_DIM='#D2ADE6', TEXT_FAINT='#A887C2',
            OK='#34D399', WARN='#FFB627', DANGER='#FF6B8B'),
        light=dict(
            BG='#F6EFFA', SURFACE='#FFFFFF', SURFACE_2='#F8F0FC', SURFACE_3='#EDDCF6',
            INPUT='#F1E5F8', BORDER='#DFC6ED',
            PURPLE='#BE185D', PURPLE_DARK='#9D174D', PINK='#0E7490', PINK_DARK='#155E75',
            BLUE='#B45309', BLUE_DARK='#92400E',
            TEXT='#240A30', TEXT_DIM='#4C2C5C', TEXT_FAINT='#6B4B7B', **LIGHT_STATUS)),
}
DEFAULT = 'ม่วงนีออน'
NAMES = list(COLORS)
MODES = ('dark', 'light')

NEAR_BLACK = '#0B0B12'
WHITE = '#FFFFFF'


# ---------------------------------------------------------------- contrast helpers
def mix(a, b, amount):
    """Blend colour a towards b: amount 0 -> a, 1 -> b. For tinted chip backgrounds."""
    ca = [int(a.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4)]
    return '#' + ''.join(f'{round(x + (y - x) * amount):02X}' for x, y in zip(ca, cb))


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
    accents = [palette[k] for k in ('PURPLE', 'PURPLE_DARK', 'PINK', 'PINK_DARK', 'BLUE', 'BLUE_DARK')]
    worst = lambda ink: min(contrast(ink, a) for a in accents)
    return WHITE if worst(WHITE) >= worst(NEAR_BLACK) else NEAR_BLACK


# ---------------------------------------------------------------- active theme
NAME = DEFAULT
MODE = 'dark'
BG = SURFACE = SURFACE_2 = SURFACE_3 = INPUT = BORDER = ''
PURPLE = PURPLE_DARK = PINK = PINK_DARK = BLUE = BLUE_DARK = ''
TEXT = TEXT_DIM = TEXT_FAINT = OK = WARN = DANGER = ON_ACCENT = ''


def palette(color, mode):
    return COLORS.get(color, COLORS[DEFAULT])['light' if mode == 'light' else 'dark']


def apply(color, mode='dark'):
    """Make colour + mode the active palette. Unknown values fall back to the defaults."""
    global NAME, MODE, ON_ACCENT
    if color not in COLORS:
        color = DEFAULT
    mode = 'light' if mode == 'light' else 'dark'
    pal = palette(color, mode)
    globals().update(pal)
    NAME, MODE = color, mode
    ON_ACCENT = pick_on_accent(pal)
    return color, mode


def from_legacy(theme_name):
    """v1.1-v1.3 stored a single 'theme' name, where 'สว่าง' meant light purple."""
    if theme_name == 'สว่าง':
        return DEFAULT, 'light'
    return (theme_name if theme_name in COLORS else DEFAULT), 'dark'


apply(DEFAULT, 'dark')


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
