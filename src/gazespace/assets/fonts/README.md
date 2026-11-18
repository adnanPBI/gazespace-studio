# Bundled DejaVu UI fonts

`DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` are unmodified TrueType fonts from
the DejaVu distribution, copied from Matplotlib's installed font assets.
Upstream: https://dejavu-fonts.github.io/.

`LICENSE-DejaVu.txt` accompanies both fonts and contains the Bitstream Vera
and Arev copyright and permission notices. The font files keep their original
family names and glyphs. They are included with this application to provide
readable native Qt text even when a platform font database lacks system fonts.

The frozen Studio smoke gate verifies printable ASCII, UI Unicode symbols,
resolved family names, and distinct actual Qt glyph rasterization. It saves a
font proof image as well as the complete native application screenshot.
