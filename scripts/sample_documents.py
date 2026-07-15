import os
import random
import shutil
from pathlib import Path

base_dir = Path("/home/sawab/AI - Project/KI_finetuning/data/Bahdini_Crawler")
sample_dir = base_dir / "document_ai_sample"

# Directories to search for PDFs
target_dirs = [
    base_dir / "crawls",
    base_dir / "facebook",
    base_dir / "telegram"
]

sample_ratio = 0.015  # 1.5%

def gather_pdfs():
    pdfs_by_source = {}
    for d in target_dirs:
        if not d.exists():
            continue
        for root, _, files in os.walk(d):
            # Skip if it's our output directory
            if sample_dir.name in root:
                continue
            
            pdf_files = [Path(root) / f for f in files if f.lower().endswith('.pdf')]
            if pdf_files:
                # Group by the directory they are in to ensure stratified sampling
                rel_path = Path(root).relative_to(base_dir)
                # Keep top level source as the key for stratification
                # e.g., 'crawls/spirez/documents', 'facebook/pdfs', 'telegram/downloads/channel'
                source_key = str(rel_path)
                pdfs_by_source[source_key] = pdf_files
    return pdfs_by_source

def main():
    if sample_dir.exists():
        shutil.rmtree(sample_dir)
    sample_dir.mkdir(parents=True, exist_ok=True)
    
    pdfs_by_source = gather_pdfs()
    total_pdfs = sum(len(files) for files in pdfs_by_source.values())
    print(f"Total PDFs found: {total_pdfs}")
    
    sampled_files = []
    
    # Stratified sampling
    for source, files in pdfs_by_source.items():
        k = max(1, int(len(files) * sample_ratio))
        # Use a fixed seed for reproducibility if needed, but random is fine
        sampled = random.sample(files, k)
        sampled_files.extend([(source, f) for f in sampled])
        
    print(f"Total sampled PDFs: {len(sampled_files)} (~{sample_ratio*100:.1f}%)")
    
    # Copy files to sample directory, preserving directory structure
    for source, fpath in sampled_files:
        rel_path = fpath.relative_to(base_dir)
        dest_path = sample_dir / rel_path
        
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(fpath), str(dest_path))
        
    print(f"Successfully copied {len(sampled_files)} documents to {sample_dir.relative_to(base_dir)}/")

if __name__ == '__main__':
    random.seed(42) # Ensure reproducible sampling
    main()
