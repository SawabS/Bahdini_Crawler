import re
from pathlib import Path

readme_path = Path("/home/sawab/AI - Project/KI_finetuning/data/Bahdini_Crawler/README.md")
with open(readme_path, 'r', encoding='utf-8') as f:
    content = f.read()

mermaid_diagram = """
```mermaid
graph TD
    %% Main Repository Node
    Root([Bahdini_Crawler])
    
    %% Top Level Directories
    Root --> Web[web/]
    Root --> Crawls[crawls/]
    Root --> Extractions[extractions/]
    Root --> Scripts[scripts/]
    Root --> Docs[docs/]
    Root --> Sources[sources/]
    Root --> Sample[document_ai_sample/]
    
    %% Web Crawler
    Web --> CrawlerPy[crawler.py]
    
    %% Crawls structure
    Crawls --> FB[facebook/]
    Crawls --> TG[telegram/]
    Crawls --> Sites["govarabadinan/ , spirez/ , etc."]
    
    %% Extractions structure
    Extractions --> ExtText["Extracted Text (*.txt)"]
    Extractions --> OCR[needs_ocr.csv]
    
    %% Sample structure
    Sample --> SampleDocs["1.5% Sampled Documents"]
    
    %% Scripts
    Scripts --> ExtractPy[extract_pipeline.py]
    Scripts --> TokenPy[token_estimate.py]
    
    %% Styles
    classDef dir fill:#f9f,stroke:#333,stroke-width:2px;
    class Root,Web,Crawls,Extractions,Scripts,Docs,Sources,Sample dir;
```

"""

# Insert mermaid diagram right after '## Repository layout\n\n[some text]\n\n'
content = re.sub(r'(## Repository layout\n\n.*?extractions/`\.\n\n)', r'\1' + mermaid_diagram, content, flags=re.DOTALL)

with open(readme_path, 'w', encoding='utf-8') as f:
    f.write(content)
