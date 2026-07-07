#!/usr/bin/env python3
"""Estimate Badini-Kurdish token volume in the crawled PDF corpus by sampling."""
import os, random, re, subprocess, sys, json

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "crawls")
SAMPLE = {"spirez": 45, "zcks": 45, "uod": 60}
random.seed(42)

AR = re.compile(r'[؀-ۿݐ-ݿ]')
# chars used in Kurdish (Badini/Sorani) orthography but not standard Arabic
KU = re.compile(r'[ێۆڤڕڵەپچژگکی]')
KU_STRONG = re.compile(r'[ێۆڤڕڵ]')  # ێ ۆ ڤ ڕ ڵ
LAT = re.compile(r'[A-Za-z]')

def classify(text):
    ar = len(AR.findall(text)); lat = len(LAT.findall(text))
    if ar + lat < 200:
        return "image_only_or_empty", ar, lat
    if ar > lat:
        strong = len(KU_STRONG.findall(text))
        # Kurdish text has ~1-4% strong-marker chars; Arabic ~0%
        if strong / max(ar, 1) > 0.005:
            return "kurdish", ar, lat
        return "arabic", ar, lat
    return "latin", ar, lat

results = {}
for site, n in SAMPLE.items():
    d = os.path.join(ROOT, site, "documents")
    files = [f for f in os.listdir(d) if f.lower().endswith(".pdf")]
    total = len(files)
    sample = random.sample(files, min(n, total))
    stats = {"total_docs": total, "sampled": len(sample), "by_lang": {},
             "kurdish_chars": 0, "arabic_chars": 0, "latin_chars": 0,
             "kurdish_words": 0, "image_only": 0}
    for f in sample:
        try:
            out = subprocess.run(["pdftotext", "-q", os.path.join(d, f), "-"],
                                 capture_output=True, timeout=60)
            text = out.stdout.decode("utf-8", "ignore")
        except Exception:
            text = ""
        lang, ar, lat = classify(text)
        stats["by_lang"][lang] = stats["by_lang"].get(lang, 0) + 1
        if lang == "kurdish":
            stats["kurdish_chars"] += ar
            stats["kurdish_words"] += len(re.findall(r'[؀-ݿ]+', text))
        elif lang == "arabic":
            stats["arabic_chars"] += ar
        elif lang == "latin":
            stats["latin_chars"] += len(text)
        else:
            stats["image_only"] += 1
    s = stats
    scale = s["total_docs"] / max(s["sampled"], 1)
    s["est_kurdish_words_corpus"] = int(s["kurdish_words"] * scale)
    s["est_kurdish_chars_corpus"] = int(s["kurdish_chars"] * scale)
    s["est_image_only_docs"] = int(s["image_only"] * scale)
    results[site] = s
    print(json.dumps({site: s}, indent=2, ensure_ascii=False), flush=True)

tw = sum(r["est_kurdish_words_corpus"] for r in results.values())
tc = sum(r["est_kurdish_chars_corpus"] for r in results.values())
print(f"\nTOTAL est. Kurdish (Badini) words: {tw:,}")
print(f"TOTAL est. Kurdish (Badini) arabic-script chars: {tc:,}")
print(f"Token estimates: conservative(w*1.3)={int(tw*1.3):,}  "
      f"typical(w*1.6)={int(tw*1.6):,}  high(w*2.0)={int(tw*2.0):,}")
