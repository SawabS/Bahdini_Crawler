#!/usr/bin/env python3
"""Compare Document AI OCR and Gemini visual transcription on selected PDF pages.

The script renders Gemini inputs as images, rather than passing the PDF, so a
malformed legacy PDF text layer cannot bias Gemini's transcription. It saves all
provider outputs and usage metadata for human evaluation; it does not merge
either provider's result into the training corpus.

Example:
    conda run --no-capture-output -n ai python scripts/compare_document_ai_gemini.py \
      --pdf 'document_ai_sample/crawls/facebook/pdfs/ئاین و پێكهاته‌یێن عیراقێ.pdf' \
      --start-page 3 --end-page 13
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import fitz
from google.cloud import documentai
from google.genai import types
from google.protobuf.json_format import MessageToDict

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROJECT = "bahdini-data"
DEFAULT_LOCATION = "us"
DEFAULT_PROCESSOR_ID = "6c2e13121ee43056"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"

PROMPT = (
    "Transcribe the visible Bahdini Kurdish text exactly as written, using Kurdish "
    "Arabic script. Return only the transcription. Preserve paragraph breaks and "
    "punctuation. Do not translate, summarize, modernize spelling, repair grammar, "
    "infer missing text, or add commentary. When a character or word is genuinely "
    "unreadable, write [unclear] rather than guessing."
)


def safe_name(value: str) -> str:
    """Return a stable ASCII directory component for a source document name."""
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return value or "document"


def anchored_text(document: documentai.Document, anchor) -> str:
    """Join all document-text intervals referenced by a Document AI anchor."""
    pieces = []
    for segment in anchor.text_segments:
        start = int(segment.start_index) if segment.start_index else 0
        end = int(segment.end_index)
        pieces.append(document.text[start:end])
    return "".join(pieces)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True, help="Source PDF path.")
    parser.add_argument("--start-page", type=int, required=True, help="Inclusive 1-based page.")
    parser.add_argument("--end-page", type=int, required=True, help="Inclusive 1-based page.")
    parser.add_argument("--project", default=os.getenv("PROJECT_ID", DEFAULT_PROJECT))
    parser.add_argument("--location", default=os.getenv("LOCATION", DEFAULT_LOCATION))
    parser.add_argument("--processor-id", default=os.getenv("PROCESSOR_ID", DEFAULT_PROCESSOR_ID))
    parser.add_argument("--gemini-model", default=DEFAULT_GEMINI_MODEL)
    parser.add_argument("--output-dir", type=Path, help="Artifact directory.")
    parser.add_argument("--dry-run", action="store_true", help="Render and validate only; do not call either API.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.pdf.resolve()
    if not source.is_file():
        print(f"Source PDF does not exist: {source}", file=sys.stderr)
        return 2
    if args.start_page < 1 or args.end_page < args.start_page:
        print("Page range must be positive and ascending.", file=sys.stderr)
        return 2

    source_label = safe_name(source.stem)
    output_dir = args.output_dir or (
        ROOT / "document_ai_sample" / "ocr_comparisons" /
        f"{source_label}_pages_{args.start_page}-{args.end_page}"
    )
    images_dir = output_dir / "images"
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(exist_ok=True)

    with fitz.open(source) as source_document:
        if args.end_page > len(source_document):
            print(f"Requested page {args.end_page}, but source has {len(source_document)} pages.", file=sys.stderr)
            return 2

        with fitz.open() as subset:
            subset.insert_pdf(source_document, from_page=args.start_page - 1, to_page=args.end_page - 1)
            subset_path = output_dir / "input_pages.pdf"
            subset.save(subset_path, garbage=4, deflate=True)

        image_paths = []
        for page_number in range(args.start_page, args.end_page + 1):
            image_path = images_dir / f"page_{page_number:04d}.png"
            page = source_document[page_number - 1]
            page.get_pixmap(
                matrix=fitz.Matrix(4, 4), colorspace=fitz.csGRAY, alpha=False
            ).save(image_path)
            image_paths.append(image_path)

    manifest = {
        "source_pdf": str(source.relative_to(ROOT) if source.is_relative_to(ROOT) else source),
        "source_pages": list(range(args.start_page, args.end_page + 1)),
        "document_ai": {
            "project": args.project,
            "location": args.location,
            "processor_id": args.processor_id,
            "native_pdf_parsing": False,
        },
        "gemini": {
            "model": args.gemini_model,
            "image_resolution": "high",
            "prompt": PROMPT,
        },
        "artifacts": {
            "input_pdf": "input_pages.pdf",
            "rendered_images": [str(path.relative_to(output_dir)) for path in image_paths],
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"Prepared {len(image_paths)} pages in {output_dir}")
    if args.dry_run:
        return 0

    document_ai_client = documentai.DocumentProcessorServiceClient(
        client_options={"api_endpoint": f"{args.location}-documentai.googleapis.com"}
    )
    request = documentai.ProcessRequest(
        name=(f"projects/{args.project}/locations/{args.location}/processors/"
              f"{args.processor_id}"),
        raw_document=documentai.RawDocument(
            content=subset_path.read_bytes(), mime_type="application/pdf"
        ),
        process_options=documentai.ProcessOptions(
            ocr_config=documentai.OcrConfig(
                enable_native_pdf_parsing=False,
                enable_image_quality_scores=True,
            )
        ),
    )
    document_ai_response = document_ai_client.process_document(request=request)
    document = document_ai_response.document
    (output_dir / "document_ai_raw.json").write_text(
        json.dumps(MessageToDict(document._pb), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    document_ai_pages = []
    for offset, page in enumerate(document.pages):
        document_ai_pages.append({
            "source_page": args.start_page + offset,
            "text": anchored_text(document, page.layout.text_anchor),
        })
    (output_dir / "document_ai_pages.json").write_text(
        json.dumps(document_ai_pages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "document_ai.txt").write_text(
        "\n\f\n".join(record["text"] for record in document_ai_pages) + "\n",
        encoding="utf-8",
    )

    from google import genai

    gemini_client = genai.Client(vertexai=True, project=args.project, location="global")
    gemini_pages = []
    for page_number, image_path in zip(range(args.start_page, args.end_page + 1), image_paths):
        response = gemini_client.models.generate_content(
            model=args.gemini_model,
            contents=[
                PROMPT,
                types.Part.from_bytes(
                    data=image_path.read_bytes(),
                    mime_type="image/png",
                    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
                ),
            ],
            config=types.GenerateContentConfig(temperature=0, max_output_tokens=8192),
        )
        gemini_pages.append({
            "source_page": page_number,
            "text": response.text or "",
            "usage_metadata": response.usage_metadata.model_dump(mode="json")
            if response.usage_metadata else None,
        })
        print(f"Gemini completed source page {page_number}")

    (output_dir / "gemini_pages.json").write_text(
        json.dumps(gemini_pages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "gemini.txt").write_text(
        "\n\f\n".join(record["text"] for record in gemini_pages) + "\n",
        encoding="utf-8",
    )
    print(f"Comparison complete: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())