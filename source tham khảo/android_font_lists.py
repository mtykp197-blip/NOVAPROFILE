"""The font lists the genuine Hidemium app writes.

Measured against the 78 profiles the app wrote: every profile carries a
list of about 180 fonts, and the list differs in every profile (48 distinct
lists). 156 fonts are in every list and 44 differ from profile to profile.

The list is what the page reads. Measured against the core: a font that is
in the list is reported by `document.fonts.check` and has its own measured
width, a font that is not in the list is reported as missing. The font
metrics are therefore a website-observable axis of their own, and the
`hidemium.fonts_value` key changes nothing on its own.
"""
from __future__ import annotations

ANDROID_FONT_BASE = (
     'Material Icons Extended', 'Noto Serif', 'Dancing Script', 'Noto Sans Bamum',
     'Noto Sans Gujarati', 'Noto Sans Saurashtra', 'Noto Sans Glagolitic',
     'Material Icons Outlined', 'Noto Sans Hanunoo', 'Noto Sans Tai Le', 'Noto Sans Coptic',
     'inherit', 'Noto Sans Thai', 'Noto Sans Mongolian', 'Noto Sans New Tai Lue',
     'ui-icons', 'Noto Sans Tagbanwa', 'controlIcons', 'Droid Sans Mono',
     'FluentSystemIcons', 'Noto Sans Vai', 'Material Icons', 'Noto Sans Rejang', 'Roboto',
     'Noto Sans Syloti Nagri', 'Noto Sans Limbu', 'Noto Sans Ol Chiki', 'Noto Sans Buhid',
     'Noto Sans Batak', 'Noto Sans Kayah Li', 'Noto Color Emoji', 'Noto Sans Balinese',
     'Noto Sans Buginese', 'Noto Sans Cham', 'Noto Sans Gujarati UI', 'Noto Sans Khmer',
     'Noto Sans Lepcha', 'Noto Sans Meetei Mayek', 'Noto Sans Oriya', 'Noto Sans Oriya UI',
     'Noto Sans Sundanese', 'Noto Sans Symbols', 'Noto Sans Syriac Estrangela',
     'Noto Sans Tai Tham', 'Shifter', 'Coming Soon', 'Cutive Mono', 'Font Awesome',
     'Noto Sans Brahmi', 'Noto Sans Syriac Eastern', 'Noto Sans Phoenician',
     'Carrois Gothic SC', 'Noto Sans Runic', 'Noto Sans Avestan', 'Noto Sans Cypriot',
     'Noto Sans Linear B', 'Noto Sans Osmanya', 'Noto Sans Egyptian Hieroglyphs',
     'Noto Sans Kharoshthi', 'Noto Sans Cuneiform', 'Font Awesome 5 Free',
     'Noto Sans Old Turkic', 'Noto Sans Ugaritic', 'Noto Sans Old Persian',
     'Noto Sans Old Italic', 'Noto Sans Samaritan', 'Noto Sans Tagalog', 'Noto Sans Ogham',
     'Noto Sans Inscriptional Pahlavi', 'Noto Sans Inscriptional Parthian',
     'Noto Sans Imperial Aramaic', 'Noto Sans Old South Arabian', 'Noto Sans Shavian',
     'Noto Sans Gothic', 'Noto Sans Lydian', 'Noto Sans Lycian', 'Noto Sans Phags Pa',
     'MWF-MDL2', 'Google Symbols', 'Noto Sans Kaithi', 'Noto Sans Syriac Western', 'Arial',
     'Courier', 'Courier New', 'Georgia', 'Helvetica', 'Monaco', 'Palatino', 'Tahoma',
     'Times', 'Times New Roman', 'Verdana', 'Baskerville', 'Casual', 'Monospace', 'Serif',
     'Noto Sans Lao UI', 'Noto Naskh Arabic UI', 'Noto Serif CJK JP', 'Noto Serif CJK KR',
     'Noto Serif CJK SC', 'Noto Serif CJK TC', 'Noto Sans Lao', 'Noto Sans CJK SC',
     'Noto Sans CJK TC', 'Noto Sans CJK KR', 'Noto Sans Mono CJK SC', 'Noto Sans CJK JP',
     'Noto Sans Mono CJK JP', 'Noto Sans Mono CJK TC', 'Noto Sans Mono CJK KR',
     'Noto Sans Canadian Aboriginal', 'Noto Sans Cherokee', 'Noto Sans CJK HK',
     'Noto Sans Khmer UI', 'Noto Sans Mono CJK HK', 'Noto Serif Thai', 'Noto Serif Lao',
     'Noto Serif Hebrew', 'Source Sans Pro Semibold Italic', 'Source Sans Pro Bold',
     'Source Sans Pro Bold Italic', 'Source Sans Pro Semibold', 'Source Sans Pro Italic',
     'Noto Sans Telugu', 'Roboto Thin', 'Roboto Light Italic', 'Roboto Bold',
     'Noto Sans Gurmukhi', 'Roboto Condensed Italic', 'Roboto Italic', 'Roboto Condensed',
     'Roboto Black', 'Roboto Medium', 'Roboto Condensed Light', 'Roboto Thin Italic',
     'Roboto Light', 'Roboto Condensed Bold Italic', 'Roboto Black Italic',
     'Roboto Medium Italic', 'Noto Sans Ethiopic', 'Roboto Bold Italic',
     'Roboto Condensed Light Italic', 'Roboto Condensed Bold', 'Noto Sans Gurmukhi UI',
     'PPUI-Icons', 'Noto Sans Kannada UI', 'Noto Sans Telugu UI', 'Google Material Icons',
     'Segoe UI Web (West European)', 'sans-serif', 'Bahnschrift', 'Consolas',
     'MWF-FLUENT-ICONS', 'Work Sans', 'Nunito Sans',
)

ANDROID_FONT_VARYING = (
     'Noto Serif Devanagari', 'Noto Serif Kannada', 'Noto Serif Malayalam',
     'Noto Sans Tifinagh', 'Noto Sans Javanese', 'Noto Sans Myanmar UI',
     'Noto Sans Malayalam UI', 'Noto Sans Sinhala', 'Noto Sans Tamil UI',
     'Noto Sans Bengali UI', 'Noto Sans CJK SC Regular', 'Noto Serif Armenian',
     'Noto Serif Georgian', 'Noto Serif Tamil', 'Lato Regular', 'Lato Medium', 'Droid Sans',
     'Noto Sans CJK JP Medium', 'Noto Sans CJK KR Medium', 'Noto Sans CJK SC Medium',
     'Noto Sans CJK TC Medium', 'Source Sans Pro', 'Noto Serif Bengali',
     'Noto Serif Gujarati', 'Noto Serif Telugu', 'Noto Sans Adlam', 'Noto Sans Devanagari',
     'Noto Sans Devanagari UI', 'Noto Sans Malayalam', 'Droid Sans Fallback', 'Lato Italic',
     'Lato Bold', 'Lato Bold Italic', 'Lato Medium Italic', 'Karla', 'Lustria',
     'Noto Sans Myanmar', 'Noto Sans Tamil', 'Noto Sans Armenian', 'Noto Sans Bengali',
     'Lohit Punjabi', 'Jomolhari', 'Open Sans', 'Noto Sans Georgian',
)
