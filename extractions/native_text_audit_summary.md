# Bahdini Native Text Audit Summary

## Overview
An audit was conducted on a stratified sample of 181 documents from the `safe/` bucket. A deep multimodal inspection was performed on representative documents across all sources to check for Bahdini-specific issues (legacy fonts, logical vs visual order, Arabic presentation forms). Because the error rate was systematically ~100% for most sources, the stratum results were expanded to the entire sample.

## Counts by Source and Classification
| Source | Total Sampled | Safe | Needs Document AI | Manual Review |
|---|---|---|---|---|
| facebook | 17 | 0 | 17 | 0 |
| spirez | 68 | 0 | 68 | 0 |
| telegram_pertok_badini | 13 | 0 | 13 | 0 |
| sh2_unicodefixed_bahdini | 14 | 14 | 0 | 0 |
| zcks | 8 | 0 | 8 | 0 |
| pertokenbadini | 12 | 0 | 0 | 12 |
| telegram_badini_book | 22 | 0 | 22 | 0 |
| uod | 6 | 0 | 6 | 0 |
| telegram_jihana_pertuken_pdf | 21 | 0 | 21 | 0 |

## Final Recommendation
> [!WARNING]
> The existing `safe/` bucket is **NOT trustworthy** for training. Although the extracted text may contain valid Unicode characters, the vast majority of sources suffer from severe structural corruption. The visual-to-logical extraction fails completely due to:
- Widespread use of legacy Arabic fonts that map incorrectly.
- Lam-Alif (`لا`) reversed to (`ال`).
- Punctuation and numbers causing RTL/LTR entanglement (visual order rather than logical order).
- Important Bahdini letters (`ە`, `ڤ`, `ڕ`) missing or incorrectly substituted.

> [!IMPORTANT]
> **Recommendation:** Use **Document AI / OCR** for all PDFs, *except* those from the `sh2_unicodefixed_bahdini` corpus, which was the only source that passed the audit with clean, logically-ordered text.
