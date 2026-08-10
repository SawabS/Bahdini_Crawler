#!/bin/bash
# Build the QA-generation report PDF.
#
# Requires a XeLaTeX engine. This machine has TeX Live 2026 installed
# user-locally at ~/texlive/2026 -- TeX Live's own installer takes a --texdir
# prefix, so a full-scheme install needs no sudo and no MacTeX .pkg. Both
# locations are probed below, so this also works on a machine with MacTeX in
# the system location instead.
#
# To reinstall from scratch:
#   curl -fsSL https://mirror.ctan.org/systems/texlive/tlnet/install-tl-unx.tar.gz | tar xz
#   cd install-tl-* && ./install-tl -no-interaction -scheme scheme-full -texdir "$HOME/texlive/2026"
#
# Two passes are required, not one: the document has a table of contents, and
# the first pass only writes the .toc file that the second pass reads back.
#
#   bash qa_generation/report/build.sh

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# User-local TeX Live first, then the MacTeX system location. Written as an
# explicit `if` rather than `[ -x ... ] && PATH=...`: under `set -e` a failing
# test at the head of an && chain takes the whole script down, so the first
# candidate that did not exist would abort the build instead of falling
# through to the next one.
for d in "$HOME"/texlive/*/bin/*/ /Library/TeX/texbin; do
  if [ -x "${d%/}/xelatex" ]; then
    PATH="${d%/}:$PATH"
    break
  fi
done
export PATH

if ! command -v xelatex >/dev/null; then
  echo "xelatex not found. Install TeX Live (see the header of this script),"
  echo "then re-run. If it is installed, add its bin directory to PATH:"
  echo '  export PATH="$HOME/texlive/2026/bin/universal-darwin:$PATH"'
  exit 1
fi
echo "engine: $(command -v xelatex)"

# Figures live in ../output/ which is gitignored; regenerate if absent.
if [ ! -f ../output/figures/fig_record_tokens.png ]; then
  echo "Figures missing; regenerating ..."
  /opt/miniconda3/envs/ai/bin/python ../export/make_report_figures.py
fi

for pass in 1 2; do
  echo "=== xelatex pass $pass ==="
  xelatex -interaction=nonstopmode -halt-on-error qa_generation_report.tex \
    | tail -5
done

rm -f qa_generation_report.{aux,log,out,toc}
echo
echo "Built: $(pwd)/qa_generation_report.pdf"
