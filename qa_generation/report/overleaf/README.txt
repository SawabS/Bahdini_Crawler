Bahdini QA Generation — build report
====================================

1. Upload this whole folder (or the .zip) to Overleaf: New Project ->
   Upload Project.

2. IMPORTANT: set the compiler to XeLaTeX.
   Menu (top left) -> Compiler -> XeLaTeX.
   Overleaf does not honour the "% !TEX program = xelatex" line in the
   source, so this must be set by hand or the build fails on fontspec.

3. Recompile. The table of contents needs two passes; Overleaf normally
   does this automatically, but if the TOC comes out empty just hit
   Recompile once more.

Files
-----
  main.tex        the report
  figures/*.png   8 generated figures, referenced via \graphicspath

Fonts
-----
Every font has a fallback chain ending in a face that ships with TeX Live,
so it builds as-is. For the intended typography, Overleaf will pick up
Linux Libertine O and Poppins automatically if they are present; otherwise
it falls back to TeX Gyre Schola and TeX Gyre Heros, which is still a
correct and readable setting.
