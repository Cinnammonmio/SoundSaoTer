"""Dark purple / blue / pink palette shared by every window."""

BG = '#0E0B18'          # window background
SURFACE = '#17122A'     # cards
SURFACE_2 = '#1F1838'   # list rows
SURFACE_3 = '#2A2150'   # row hover
INPUT = '#221B3D'       # dropdowns, entries
BORDER = '#342B59'

PURPLE = '#A855F7'
PURPLE_DARK = '#8B3FE0'
BLUE = '#6366F1'
BLUE_DARK = '#4F46E5'
PINK = '#EC4899'
PINK_DARK = '#DB2777'

TEXT = '#EDE9FE'
TEXT_DIM = '#A99BD4'
TEXT_FAINT = '#7A6CA8'

OK = '#34D399'
WARN = '#FBBF24'
DANGER = '#F87171'

FONT = 'Segoe UI'


def font(size=13, weight='normal'):
    import customtkinter as ctk
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


def short_device(label):
    """'Headphones (AirPods Pro)  [Windows WASAPI]' -> 'Headphones (AirPods Pro) · WASAPI'"""
    if '  [' not in label:
        return label
    name, _, api = label.partition('  [')
    api = api.rstrip(']')
    api = api.replace('Windows ', '').replace('MME', 'MME').strip()
    return f'{name.strip()} · {api}'
