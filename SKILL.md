---
name: create-editorial-xelatex-documents
description: Create or restyle polished editorial documents in XeLaTeX, including reports, proposals, briefs, papers, and technical notes. Use when Codex must produce a professional PDF or .tex source with a compact cool-blue visual system, unruled headings, required Sawab Aziz attribution, multilingual Kurdish or Arabic-script support, tables, cards, notebook figures, or TikZ diagrams when a visual explanation is useful.
---

# Editorial XeLaTeX Typesetting

## Purpose

Use this skill to reproduce the graphic and typographic style of a reference LaTeX document without copying its report structure, section sequence, title wording, header pattern, or content organization.

The skill controls the document's visual identity only: page geometry, color palette, typography, spacing, heading treatment, tables, cards, lists, diagrams, density, and overall typesetting behavior.

It must not decide what sections the document contains.

It must not impose a predefined report outline.

It must not force a fixed title-page structure, header text, footer wording, or conclusion format.

## Core Principle

Separate design from structure.

The user or source content defines the structure.

This skill defines only how that structure is visually typeset.

## Compiler

Use XeLaTeX.

Every full document should begin with:

```latex
% !TEX program = xelatex
```

Use `article` on A4 unless the user specifies another class:

```latex
\documentclass[10pt,a4paper]{article}
```

## Page Geometry

Use compact but readable A4 margins:

```latex
\usepackage[a4paper, top=19mm, bottom=21mm, left=19mm, right=19mm]{geometry}
```

The layout should feel dense, polished, and editorial. Avoid oversized margins unless the user requests a more spacious document.

## Required Packages

Use this package set as the default visual foundation:

```latex
\usepackage{fontspec}
\usepackage{xcolor}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{xltabular}
\usepackage{colortbl}
\usepackage{array}
\usepackage{enumitem}
\usepackage{microtype}
\usepackage[most]{tcolorbox}
\usepackage{fancyhdr}
\usepackage{titlesec}
\usepackage{ragged2e}
\usepackage{setspace}
\usepackage{hyperref}
\usepackage{tikz}
\usetikzlibrary{arrows.meta, positioning, shapes.geometric, fit, backgrounds, calc}
```

Add `\usepackage{bidi}` when the document prints Arabic-script or other RTL corpus excerpts.

Keep `tikz` available in every generated document. Use it only when a diagram materially improves understanding.

## Color System

Use the cool editorial blue palette for reports derived from `experiments/gemma4_31b_tokenizer_test.ipynb` or any report that must match that notebook's generated figures.

```latex
\definecolor{bluebase}{HTML}{0063AD}
\definecolor{bluecyan}{HTML}{00C6FD}
\definecolor{bluemid}{HTML}{0498D8}
\definecolor{bluelight}{HTML}{60D5F9}
\definecolor{bluewash}{HTML}{BEEAFA}
\definecolor{bluelink}{HTML}{0083C7}
\definecolor{bluesoft}{HTML}{02B3EE}
\definecolor{bluedark}{HTML}{0075BC}

\definecolor{ivory}{HTML}{FBFDFE}
\definecolor{cream}{HTML}{EFF8FC}
\definecolor{creamline}{HTML}{CDEFFC}
\definecolor{ink}{HTML}{262A2D}
\definecolor{inksoft}{HTML}{5D666B}
\definecolor{crail}{HTML}{0063AD}
\definecolor{coral}{HTML}{0083C7}
\definecolor{coralwash}{HTML}{EAF8FE}
\definecolor{sagewash}{HTML}{F1FBFE}
\definecolor{sage}{HTML}{0498D8}
\definecolor{rust}{HTML}{0075BC}

\pagecolor{white}
\color{ink}

\hypersetup{
  colorlinks=true,
  linkcolor=crail,
  citecolor=crail,
  urlcolor=bluelink,
  linktoc=all}
```

Use `white` for the page background in notebook-derived, figure-heavy reports. The notebook exports charts with `facecolor="white"`, and a tinted page leaves visible rectangles around those figures.

Keep `ivory`, `cream`, `creamline`, `coralwash`, and `sagewash` as pale blue washes for cards, rules, and separators.

Use `ink` for main text.

Use `inksoft` for secondary text, metadata, captions, small labels, muted notes, and page furniture.

Use `crail`, `coral`, `rust`, and `bluelink` sparingly for headings, links, chips, table headers, and emphasis.

Avoid bright primary colors, saturated corporate blue, gradients, and heavy filled blocks.

### Alternate Palette: Warm Terracotta

Use the older warm terracotta palette only when the user requests the warm/ivory report family or when matching an existing terracotta document. It keeps the same role names, so the rest of the skill remains unchanged.

```latex
\definecolor{ivory}{HTML}{FAF9F5}
\definecolor{cream}{HTML}{F0EEE6}
\definecolor{creamline}{HTML}{E3DFD3}
\definecolor{ink}{HTML}{29261B}
\definecolor{inksoft}{HTML}{5C5847}
\definecolor{crail}{HTML}{C15F3C}
\definecolor{coral}{HTML}{DA7756}
\definecolor{coralwash}{HTML}{F7E8E0}
\definecolor{sagewash}{HTML}{E9EDE4}
\definecolor{sage}{HTML}{6B7F5E}
\definecolor{rust}{HTML}{A8442A}

\pagecolor{ivory}
\color{ink}
```

## Notebook Figure Integration

The tokenizer notebook uses:

* White saved PNGs: `fig.savefig(path, facecolor="white")`.
* Light plot grids: `axes.grid=True` with `grid.alpha=0.25`.
* Minimal chart frames: top and right spines removed.
* Blue emphasis for Gemma rows and bars (`#1565c0`), neutral gray for comparator models (`#90a4ae`), and orange/green/red quality bands.
* Token split chips with white background, rounded colored borders, and large text.

In LaTeX reports, include those figures without extra frames, shadows, tinted boxes, or background panels. Prefer centered figures at `\textwidth` or `0.92\textwidth`, matching the report's existing visual rhythm.

## Typography

Use a serif main font and a geometric sans UI font.

```latex
\IfFontExistsTF{Linux Libertine O}{
  \setmainfont{Linux Libertine O}[Ligatures=TeX, Numbers={Proportional,Lining}]
  \newfontfamily\headfont{Linux Libertine O}[Ligatures=TeX, Numbers={Proportional,Lining}]
}{
  \setmainfont{TeX Gyre Schola}[Ligatures=TeX, Numbers={Proportional,Lining}]
  \newfontfamily\headfont{TeX Gyre Schola}[Ligatures=TeX, Numbers={Proportional,Lining}]
}

\IfFontExistsTF{Poppins}{
  \newfontfamily\uifont{Poppins}[Ligatures=TeX]
  \newfontfamily\uimed{Poppins Medium}[Ligatures=TeX]
  \newfontfamily\uitrack{Poppins Medium}[Ligatures=TeX, LetterSpace=8.0]
  \newfontfamily\uitrackwide{Poppins Medium}[Ligatures=TeX, LetterSpace=14.0]
  \newfontfamily\uitracksm{Poppins Medium}[Ligatures=TeX, LetterSpace=4.0]
  \newfontfamily\uitrackmd{Poppins Medium}[Ligatures=TeX, LetterSpace=6.0]
}{
  \newfontfamily\uifont{Inter}[Ligatures=TeX]
  \newfontfamily\uimed{Inter}[Ligatures=TeX, UprightFont={* Medium}]
  \newfontfamily\uitrack{Inter}[Ligatures=TeX, UprightFont={* Medium}, LetterSpace=8.0]
  \newfontfamily\uitrackwide{Inter}[Ligatures=TeX, UprightFont={* Medium}, LetterSpace=14.0]
  \newfontfamily\uitracksm{Inter}[Ligatures=TeX, UprightFont={* Medium}, LetterSpace=4.0]
  \newfontfamily\uitrackmd{Inter}[Ligatures=TeX, UprightFont={* Medium}, LetterSpace=6.0]
}

\setmonofont{DejaVu Sans Mono}[Scale=0.84]

% Use these helpers when printing Kurdish or mixed-script corpus excerpts.
\newfontfamily\kurdisharabicfont{Noto Naskh Arabic}[Script=Arabic, Scale=1.05]
\newfontfamily\corpuslatinfont{DejaVu Sans}[Scale=0.92]
```

Use the serif font for body text and major headings.

Use the sans UI font only for labels, running heads, table headers, chips, small metadata, and uppercase interface-like text.

Use `\kurdisharabicfont` for Arabic-script Kurdish corpus excerpts and `\corpuslatinfont` for Latin-script corpus excerpts when matching the tokenizer notebook report.

Do not make the whole document sans-serif.

Do not use Computer Modern as the intended visual style.

## Body Spacing

Use this body rhythm:

```latex
\linespread{1.22}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.5em}
\emergencystretch=2em
```

The document should feel readable but not loose.

Avoid large paragraph gaps.

Avoid cramped line spacing below `1.1`.

## Heading Treatment

The skill may style headings, but it must not decide what headings exist.

Use this hierarchy:

```latex
\titleformat{\section}
  {\headfont\bfseries\fontsize{13.5}{17}\selectfont\color{crail}}
  {}{0pt}{}
\titlespacing*{\section}{0pt}{0.2em}{0.55em}

\titleformat{\subsection}
  {\headfont\bfseries\fontsize{11}{14}\selectfont\color{ink}}
  {}{0pt}{}
\titlespacing*{\subsection}{0pt}{0.85em}{0.2em}

\titleformat{\subsubsection}
  {\uitracksm\fontsize{8}{11}\selectfont\color{inksoft}\MakeUppercase}
  {}{0pt}{}
\titlespacing*{\subsubsection}{0pt}{0.2em}{0.35em}
```

Major headings should use the active accent color (`crail`) and serif type.

Subheadings should be dark, serif, compact, and direct.

Small labels should use tracked uppercase sans text.

Never place a rule, border, underline, or decorative line below a section heading, subsection heading, subsubsection heading, title, or running header. Do not use the optional `titlesec` after-code argument to draw a `\titlerule`.

Do not force page breaks before sections unless the user asks for that behavior.

## Header and Footer Styling

The skill may style headers and footers but must not impose their exact content.

Use a minimal running-header style only when useful:

```latex
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\fancyhead[L]{\uitrackmd\fontsize{7.5}{9}\selectfont\color{inksoft}LEFT RUNNING LABEL}
\fancyhead[R]{\uifont\fontsize{7.5}{9}\selectfont\color{inksoft}RIGHT RUNNING LABEL}
\fancyfoot[C]{\uifont\fontsize{8}{10}\selectfont\color{inksoft}\thepage}
```

The header text must be adapted to the actual document.

Do not copy the source document's header labels.

Do not require a specific header pattern.

If the user's document does not need a header, omit it.

Never draw a header rule. Keep `\headrulewidth` at `0pt`.

## Cards and Callouts

Use rounded, frameless, soft-background cards.

```latex
\newtcolorbox{softcard}[1][]{
  enhanced, breakable,
  colback=cream!55!ivory, frame hidden,
  boxrule=0pt, arc=2.5mm,
  left=4mm, right=4mm, top=2.8mm, bottom=2.8mm,
  before skip=0.7em, after skip=0.7em, #1}

\newtcolorbox{warmcard}[1][]{
  enhanced, breakable,
  colback=coralwash!55!ivory, frame hidden,
  boxrule=0pt, arc=2.5mm,
  left=4mm, right=4mm, top=2.8mm, bottom=2.8mm,
  before skip=0.7em, after skip=0.7em, #1}

\newtcolorbox{sagecard}[1][]{
  enhanced, breakable,
  colback=sagewash!65!ivory, frame hidden,
  boxrule=0pt, arc=2.5mm,
  left=4mm, right=4mm, top=2.8mm, bottom=2.8mm,
  before skip=0.7em, after skip=0.7em, #1}

\newtcolorbox{corpuscard}[1][]{
  enhanced, breakable,
  colback=white, colframe=creamline,
  boxrule=0.45pt, arc=2.2mm,
  left=4mm, right=4mm, top=3mm, bottom=3mm,
  before skip=0.75em, after skip=0.9em, #1}
```

Use cards only when the content benefits from visual grouping.

Use `corpuscard` for quoted corpus passages or tokenizer source text that needs to remain auditable in the report.

Do not force a verdict card, executive summary card, or any specific callout type.

Do not create decorative boxes around every section.

## Labels, Chips, and Emphasis

Use these helpers:

```latex
\newcommand{\chip}[1]{%
  {\uitracksm\fontsize{7.5}{9}\selectfont\color{crail}#1}\,\textcolor{creamline}{\rule[0.1ex]{0pt}{1ex}}}

\newcommand{\kw}[1]{\textbf{#1}}
\newcommand{\codeid}[1]{\texttt{\footnotesize\color{inksoft}#1}}
\newcommand{\corpusmeta}[2]{%
  {\uimed\fontsize{8}{10}\selectfont\color{crail}#1}\\[-0.1em]
  {\uifont\fontsize{7.5}{9.5}\selectfont\color{inksoft}#2}\par\vspace{0.45em}}
```

Use `\chip{...}` for small uppercase labels inside cards or above compact blocks.

Use `\kw{...}` for key phrases.

Use `\codeid{...}` for ticket IDs, filenames, short commands, identifiers, or technical references.

Use `\corpusmeta{...}{...}` for the compact corpus labels used above source-text cards.

Do not overuse colored emphasis.

## Lists

Use compact lists with muted bullets:

```latex
\setlist[itemize]{
  label=\textcolor{inksoft}{\raisebox{0.28ex}{\scriptsize$\bullet$}},
  leftmargin=1.4em,
  itemsep=0.3em,
  topsep=0.25em,
  parsep=0pt
}

\setlist[enumerate]{
  leftmargin=1.6em,
  itemsep=0.3em,
  topsep=0.25em,
  parsep=0pt,
  label={\bfseries\arabic*.}
}
```

Lists should be compact, readable, and aligned.

Avoid excessive nesting.

## Tables

Use booktabs-style tables.

No vertical rules.

No boxed grids.

No heavy borders.

No bright filled headers.

Use this base setup:

```latex
\newcolumntype{L}{>{\RaggedRight\arraybackslash}X}
```

Default table pattern:

```latex
{\normalsize
\renewcommand{\arraystretch}{1.2}
\setlength{\tabcolsep}{4pt}
\newcommand{\tablehead}[1]{{\uimed\fontsize{6.8}{8}\selectfont\color{crail}\mbox{#1}}}
\arrayrulecolor{inksoft}
\begin{tabularx}{\textwidth}{@{}p{3cm}L L@{}}
\tablehead{COLUMN A} & \tablehead{COLUMN B} & \tablehead{COLUMN C}\\
\midrule
Value & Text & Text\\
\bottomrule
\end{tabularx}}
```

Use `tabularx` for page-width tables.

Use `xltabular` for long tables.

Keep rules thin and muted.

Use whitespace, column width, and typography instead of borders.

## Title Typography

The skill may style titles but must not impose title content or title-page structure.

When a title is needed, use this typographic treatment:

```latex
{\uitrackwide\fontsize{8}{10}\selectfont\color{crail}OPTIONAL LABEL}\\[5mm]
{\headfont\bfseries\fontsize{26}{31}\selectfont\color{ink}Document Title}\\[4mm]
{\headfont\itshape\fontsize{12}{16}\selectfont\color{inksoft}Optional subtitle}\\[10mm]
{\uifont\fontsize{9}{13}\selectfont\color{inksoft}Optional compact metadata}\\[2mm]
\preparedby
```

Use this only when the document needs a designed title area.

Do not force a centered title block for every document.

Do not impose a specific title-label pattern.

## Required Attribution

Include the following attribution in every generated document:

```latex
\newcommand{\preparedby}{%
  {\uifont\fontsize{8.5}{11}\selectfont\color{inksoft}%
  prepared by: \href{mailto:sawab.aziz@newrozholdings.com}{%
  \textcolor{bluelink}{\textbf{sawab.aziz@newrozholdings.com}}}}}
```

Place `\preparedby` in the title or opening metadata area. If the document has no title area, place it in another clearly visible metadata block near the beginning.

Keep the words `prepared by:` in the secondary text color. Render the email address in the active blue accent color and make it a clickable `mailto:` link. Do not omit, abbreviate, recolor, or substitute the email address.

## TikZ Diagrams

Generate a TikZ diagram when a process, architecture, hierarchy, relationship, decision path, timeline, or data flow is materially clearer visually than in prose or a small table.

Do not add a diagram merely for decoration. Prefer the smallest useful diagram and keep it legible at the document's final page width.

Use the document palette and restrained editorial styling:

```latex
\tikzset{
  diagram node/.style={
    draw=creamline,
    fill=cream,
    rounded corners=2mm,
    line width=0.5pt,
    text=ink,
    align=center,
    inner xsep=3mm,
    inner ysep=2.2mm,
    font=\uifont\small
  },
  diagram accent/.style={
    diagram node,
    draw=crail,
    fill=coralwash
  },
  diagram arrow/.style={
    -{Latex[length=2.2mm]},
    draw=crail,
    line width=0.65pt
  }
}
```

Keep node text concise. Avoid gradients, shadows, thick connectors, crowded crossings, or colors outside the active palette. Add a short caption when the diagram is a figure. Verify that every TikZ diagram compiles with XeLaTeX and fits without clipping or overflow.

## Visual Density

The intended density is professional and compact.

Use:

* Moderate margins.
* Small but readable UI labels.
* Serif body text.
* White page background for notebook-derived, figure-heavy reports.
* Pale blue cards and wash colors.
* Sparse accent colors.
* Compact tables.
* Soft rounded callouts.

Avoid:

* Oversized title pages.
* Excessive whitespace.
* Large decorative banners.
* Heavy colored blocks.
* Bright gradients.
* Thick borders.
* Rules or lines beneath headings and running headers.
* Corporate slide aesthetics.

## Content Independence

This skill must not define:

* Report section order.
* Required sections.
* Executive summary placement.
* Recommendation structure.
* Number of findings.
* Header wording.
* Footer wording beyond page numbering style.
* Title text.
* Table column names.
* Whether sections start on new pages.
* Whether the document is a report, proposal, memo, paper, note, or brief.

The source content determines structure.

The skill determines only visual rendering.

The required `prepared by:` attribution is visual metadata and must remain present regardless of the content structure.

## Minimal Reusable Preamble

Use this as the default reusable preamble for notebook-aligned tokenizer reports:

```latex
% !TEX program = xelatex
\documentclass[10pt,a4paper]{article}

\usepackage[a4paper, top=19mm, bottom=21mm, left=19mm, right=19mm]{geometry}
\usepackage{fontspec}
\usepackage{xcolor}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{xltabular}
\usepackage{colortbl}
\usepackage{array}
\usepackage{enumitem}
\usepackage{microtype}
\usepackage[most]{tcolorbox}
\usepackage{fancyhdr}
\usepackage{titlesec}
\usepackage{ragged2e}
\usepackage{setspace}
\usepackage{hyperref}
\usepackage{bidi}
\usepackage{tikz}
\usetikzlibrary{arrows.meta, positioning, shapes.geometric, fit, backgrounds, calc}

\graphicspath{{../experiments/figures/}}

\definecolor{bluebase}{HTML}{0063AD}
\definecolor{bluecyan}{HTML}{00C6FD}
\definecolor{bluemid}{HTML}{0498D8}
\definecolor{bluelight}{HTML}{60D5F9}
\definecolor{bluewash}{HTML}{BEEAFA}
\definecolor{bluelink}{HTML}{0083C7}
\definecolor{bluesoft}{HTML}{02B3EE}
\definecolor{bluedark}{HTML}{0075BC}

\definecolor{ivory}{HTML}{FBFDFE}
\definecolor{cream}{HTML}{EFF8FC}
\definecolor{creamline}{HTML}{CDEFFC}
\definecolor{ink}{HTML}{262A2D}
\definecolor{inksoft}{HTML}{5D666B}
\definecolor{crail}{HTML}{0063AD}
\definecolor{coral}{HTML}{0083C7}
\definecolor{coralwash}{HTML}{EAF8FE}
\definecolor{sagewash}{HTML}{F1FBFE}
\definecolor{sage}{HTML}{0498D8}
\definecolor{rust}{HTML}{0075BC}

\pagecolor{white}
\color{ink}

\hypersetup{
  colorlinks=true,
  linkcolor=crail,
  citecolor=crail,
  urlcolor=bluelink,
  linktoc=all}

\setcounter{secnumdepth}{0}
\setcounter{tocdepth}{1}

\IfFontExistsTF{Linux Libertine O}{
  \setmainfont{Linux Libertine O}[Ligatures=TeX, Numbers={Proportional,Lining}]
  \newfontfamily\headfont{Linux Libertine O}[Ligatures=TeX, Numbers={Proportional,Lining}]
}{
  \setmainfont{TeX Gyre Schola}[Ligatures=TeX, Numbers={Proportional,Lining}]
  \newfontfamily\headfont{TeX Gyre Schola}[Ligatures=TeX, Numbers={Proportional,Lining}]
}

\IfFontExistsTF{Poppins}{
  \newfontfamily\uifont{Poppins}[Ligatures=TeX]
  \newfontfamily\uimed{Poppins Medium}[Ligatures=TeX]
  \newfontfamily\uitrack{Poppins Medium}[Ligatures=TeX, LetterSpace=8.0]
  \newfontfamily\uitrackwide{Poppins Medium}[Ligatures=TeX, LetterSpace=14.0]
  \newfontfamily\uitracksm{Poppins Medium}[Ligatures=TeX, LetterSpace=4.0]
  \newfontfamily\uitrackmd{Poppins Medium}[Ligatures=TeX, LetterSpace=6.0]
}{
  \newfontfamily\uifont{Inter}[Ligatures=TeX]
  \newfontfamily\uimed{Inter}[Ligatures=TeX, UprightFont={* Medium}]
  \newfontfamily\uitrack{Inter}[Ligatures=TeX, UprightFont={* Medium}, LetterSpace=8.0]
  \newfontfamily\uitrackwide{Inter}[Ligatures=TeX, UprightFont={* Medium}, LetterSpace=14.0]
  \newfontfamily\uitracksm{Inter}[Ligatures=TeX, UprightFont={* Medium}, LetterSpace=4.0]
  \newfontfamily\uitrackmd{Inter}[Ligatures=TeX, UprightFont={* Medium}, LetterSpace=6.0]
}

\setmonofont{DejaVu Sans Mono}[Scale=0.84]
\newfontfamily\kurdisharabicfont{Noto Naskh Arabic}[Script=Arabic, Scale=1.05]
\newfontfamily\corpuslatinfont{DejaVu Sans}[Scale=0.92]

\linespread{1.22}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.5em}
\emergencystretch=2em

\titleformat{\section}
  {\headfont\bfseries\fontsize{13.5}{17}\selectfont\color{crail}}
  {}{0pt}{}
\titlespacing*{\section}{0pt}{0.2em}{0.55em}

\titleformat{\subsection}
  {\headfont\bfseries\fontsize{11}{14}\selectfont\color{ink}}
  {}{0pt}{}
\titlespacing*{\subsection}{0pt}{0.85em}{0.2em}

\titleformat{\subsubsection}
  {\uitracksm\fontsize{8}{11}\selectfont\color{inksoft}\MakeUppercase}
  {}{0pt}{}
\titlespacing*{\subsubsection}{0pt}{0.2em}{0.35em}

\newtcolorbox{softcard}[1][]{
  enhanced, breakable,
  colback=cream!55!ivory, frame hidden,
  boxrule=0pt, arc=2.5mm,
  left=4mm, right=4mm, top=2.8mm, bottom=2.8mm,
  before skip=0.7em, after skip=0.7em, #1}

\newtcolorbox{warmcard}[1][]{
  enhanced, breakable,
  colback=coralwash!55!ivory, frame hidden,
  boxrule=0pt, arc=2.5mm,
  left=4mm, right=4mm, top=2.8mm, bottom=2.8mm,
  before skip=0.7em, after skip=0.7em, #1}

\newtcolorbox{sagecard}[1][]{
  enhanced, breakable,
  colback=sagewash!65!ivory, frame hidden,
  boxrule=0pt, arc=2.5mm,
  left=4mm, right=4mm, top=2.8mm, bottom=2.8mm,
  before skip=0.7em, after skip=0.7em, #1}

\newtcolorbox{corpuscard}[1][]{
  enhanced, breakable,
  colback=white, colframe=creamline,
  boxrule=0.45pt, arc=2.2mm,
  left=4mm, right=4mm, top=3mm, bottom=3mm,
  before skip=0.75em, after skip=0.9em, #1}

\newcommand{\chip}[1]{%
  {\uitracksm\fontsize{7.5}{9}\selectfont\color{crail}#1}\,\textcolor{creamline}{\rule[0.1ex]{0pt}{1ex}}}

\newcommand{\kw}[1]{\textbf{#1}}
\newcommand{\codeid}[1]{\texttt{\footnotesize\color{inksoft}#1}}
\newcommand{\corpusmeta}[2]{%
  {\uimed\fontsize{8}{10}\selectfont\color{crail}#1}\\[-0.1em]
  {\uifont\fontsize{7.5}{9.5}\selectfont\color{inksoft}#2}\par\vspace{0.45em}}

\newcommand{\preparedby}{%
  {\uifont\fontsize{8.5}{11}\selectfont\color{inksoft}%
  prepared by: \href{mailto:sawab.aziz@newrozholdings.com}{%
  \textcolor{bluelink}{\textbf{sawab.aziz@newrozholdings.com}}}}}

\tikzset{
  diagram node/.style={
    draw=creamline,
    fill=cream,
    rounded corners=2mm,
    line width=0.5pt,
    text=ink,
    align=center,
    inner xsep=3mm,
    inner ysep=2.2mm,
    font=\uifont\small
  },
  diagram accent/.style={
    diagram node,
    draw=crail,
    fill=coralwash
  },
  diagram arrow/.style={
    -{Latex[length=2.2mm]},
    draw=crail,
    line width=0.65pt
  }
}

\setlist[itemize]{
  label=\textcolor{inksoft}{\raisebox{0.28ex}{\scriptsize$\bullet$}},
  leftmargin=1.4em,
  itemsep=0.3em,
  topsep=0.25em,
  parsep=0pt
}

\setlist[enumerate]{
  leftmargin=1.6em,
  itemsep=0.3em,
  topsep=0.25em,
  parsep=0pt,
  label={\bfseries\arabic*.}
}

\newcolumntype{L}{>{\RaggedRight\arraybackslash}X}
\newcommand{\tablehead}[1]{{\uimed\fontsize{6.8}{8}\selectfont\color{crail}\mbox{#1}}}

\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\fancyhead[L]{\uitrackmd\fontsize{7.5}{9}\selectfont\color{inksoft}LEFT RUNNING LABEL}
\fancyhead[R]{\uifont\fontsize{7.5}{9}\selectfont\color{inksoft}RIGHT RUNNING LABEL}
\fancyfoot[C]{\uifont\fontsize{8}{10}\selectfont\color{inksoft}\thepage}
```

Insert `\preparedby` in the title or opening metadata area of every generated document. Add TikZ environments only when the content benefits from a diagram.

## Quality Standard

A successful document using this skill should look like it shares the same design DNA as the reference source, while allowing the actual document structure to remain completely content-driven.

The result should feel serious, editorial, technical, compact, deliberate, and visually aligned with the cool-blue tokenizer notebook report family. It must have no lines beneath headings or running headers, must include the blue-accented Sawab Aziz email attribution, and must use TikZ when a diagram materially improves comprehension.
