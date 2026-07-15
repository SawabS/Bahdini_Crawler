import json
import os

def main():
    extractions_dir = "/home/sawab/AI - Project/KI_finetuning/data/Bahdini_Crawler/extractions"
    sample_path = os.path.join(extractions_dir, "audit_sample.json")
    
    with open(sample_path, 'r', encoding='utf-8') as f:
        samples = json.load(f)
        
    # Get 1 sample per source
    seen_sources = set()
    selected = []
    for s in samples:
        source = s.get('source')
        if source not in seen_sources:
            seen_sources.add(source)
            selected.append(s)
            
    prompts = []
    
    for i, s in enumerate(selected):
        source = s.get('source')
        pdf_file = s.get('input')
        txt_file = s.get('out')
        
        pdf_path = f"/home/sawab/AI - Project/KI_finetuning/data/Bahdini_Crawler/crawls/{source}/documents/{pdf_file}"
        txt_path = f"/home/sawab/AI - Project/KI_finetuning/data/Bahdini_Crawler/{txt_file}"
        out_path = f"/home/sawab/AI - Project/KI_finetuning/data/Bahdini_Crawler/extractions/audit_results/source_eval_{source}.json"
        
        prompt = f"""You are a Kurdish Bahdini (Kurmanji, Arabic-script) corpus-quality auditor.
Your job is to determine whether documents currently labeled `safe/` are genuinely suitable for AI training without OCR.

Document Source: {source}
PDF path: {pdf_path}
Extracted Text Path: {txt_path}
Output JSON Path: {out_path}

1. Use view_file to inspect the Extracted Text.
2. Use view_file to inspect the PDF.
3. Compare the visible text in the PDF pages to the extracted text. Check specifically for:
   - Arabic presentation-form characters or legacy-font glyph substitutions.
   - Reversed words, reversed lines, or visual-order rather than logical-order text.
   - Incorrect Kurdish letters, especially `ە`, `ێ`, `ڕ`, `ڵ`, `ڤ`, `پ`, `چ`, `ژ`, `گ`, and `ۆ`.
   - Garbled punctuation, isolated letters, missing paragraphs, large mismatches.
   - Junk content mislabeled as Bahdini.
4. Classify the document as EXACTLY ONE of: `safe_no_document_ai`, `needs_document_ai`, `manual_review`.
5. Output a JSON string matching the required schema to the Output JSON Path using write_to_file. Do not wrap in markdown blocks.
6. End your turn.
"""
        prompts.append({
            "TypeName": "self",
            "Role": f"Auditor {source}",
            "Prompt": prompt
        })

    with open(os.path.join(extractions_dir, "batch1_prompts.json"), 'w', encoding='utf-8') as f:
        json.dump(prompts, f, indent=2)

if __name__ == "__main__":
    main()
