import os
import json
import glob
import random
from collections import defaultdict

def main():
    extractions_dir = "/home/sawab/AI - Project/KI_finetuning/data/Bahdini_Crawler/extractions"
    manifests = glob.glob(os.path.join(extractions_dir, "*/safe/_manifest.jsonl"))
    
    records = []
    for m in manifests:
        with open(m, 'r', encoding='utf-8') as f:
            for line in f:
                records.append(json.loads(line))
                
    forced_records = []
    remaining_records = []
    
    for r in records:
        if r.get('presentation_form_ratio', 0) >= 0.05 or r.get('chars', 0) < 2000:
            forced_records.append(r)
        else:
            remaining_records.append(r)
            
    # Stratify by source, and chars band (<10k, 10k-50k, 50k+)
    strata = defaultdict(list)
    for r in remaining_records:
        source = r.get('source', 'unknown')
        chars = r.get('chars', 0)
        if chars < 10000:
            band = 'small'
        elif chars < 50000:
            band = 'medium'
        else:
            band = 'large'
        
        strata[(source, band)].append(r)
        
    sampled_records = []
    random.seed(42)
    for key, group in strata.items():
        n_sample = min(2, len(group))
        sampled_records.extend(random.sample(group, n_sample))
        
    final_sample = forced_records + sampled_records
    
    # Remove duplicates if any (based on source and input)
    seen = set()
    unique_sample = []
    for r in final_sample:
        identifier = (r.get('source'), r.get('input'))
        if identifier not in seen:
            seen.add(identifier)
            unique_sample.append(r)
            
    output_path = os.path.join(extractions_dir, "audit_sample.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(unique_sample, f, indent=2)
        
    print(f"Total documents: {len(records)}")
    print(f"Forced records (>=0.05 presentation or <2000 chars): {len(forced_records)}")
    print(f"Stratified sample size: {len(sampled_records)}")
    print(f"Final distinct documents to audit: {len(unique_sample)}")

if __name__ == "__main__":
    main()
