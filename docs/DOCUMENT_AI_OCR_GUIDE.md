# Google Document AI OCR Guide

This guide takes a new contributor from no Google Cloud setup to a verified,
repeatable batch OCR workflow for the scanned PDFs in this repository. It is
also intended to be given to an implementation agent as the operational
contract for the Document AI integration.

The native extraction stage is deliberately separate from OCR:

```text
raw PDFs -> scripts/extract_pipeline.py -> extractions/needs_ocr.csv
                                           -> Document AI batch OCR
                                           -> normalized text + manifest results
```

Do not OCR every PDF automatically. The native extraction pipeline is much
faster and avoids cloud cost for files that already contain a usable text
layer. The OCR input queue is [extractions/needs_ocr.csv](../extractions/needs_ocr.csv).

## 1. Decide the Cloud Design First

Write down these values before creating resources. Keep them consistent
throughout the workflow.

| Setting | Recommended value | Why it matters |
|---|---|---|
| Google Cloud project | your dedicated data/OCR project | Billing, API enablement, IAM, processor ownership |
| Processor type | Enterprise Document OCR | General OCR for scanned PDFs; preserves structural output in Document AI JSON |
| Processor location | `us`, unless EU-only residency is required | A processor cannot be moved later; endpoint and storage location must match |
| Input bucket location | same region/multi-region as processor | Avoids location incompatibilities and unnecessary transfer |
| Output bucket location | same as input and processor | Document AI writes JSON results here |
| Authentication for development | Application Default Credentials (ADC) | The Python client discovers these automatically |
| Processing mode | Cloud Storage batch processing | Appropriate for thousands of PDFs and resumable output |

For a project named `bahdini-data` in the US, the identifiers used below are:

```bash
export PROJECT_ID="bahdini-data"
export LOCATION="us"
export RAW_BUCKET="bahdini-documentai-raw"
export OUTPUT_BUCKET="bahdini-documentai-output"
```

Use new, globally unique bucket names if these are already taken. Do not put
service-account keys, OAuth tokens, credentials files, or processor secrets in
the repository. User ADC is stored outside the repository at
`~/.config/gcloud/application_default_credentials.json`.

## 2. Prerequisites and Permissions

You need a Google account that can use the target project, and billing must be
enabled for that project. A project owner can perform the whole setup. In a
shared project, ask an administrator to grant the least privilege needed.

| Who | Minimum capability / common role | Purpose |
|---|---|---|
| Person enabling APIs | `serviceusage.services.enable` / `roles/serviceusage.serviceUsageAdmin` | Enable the Document AI API |
| Person creating or managing a processor | Document AI processor administration permission, commonly `roles/documentai.editor` | Create and inspect processors |
| Person creating buckets and uploading inputs | Storage bucket administration, commonly `roles/storage.admin` | Create buckets and copy local PDFs |
| Runtime principal reading input objects | `roles/storage.objectViewer` on the input bucket | Let Document AI read batch inputs |
| Runtime principal writing result objects | `roles/storage.objectCreator` on the output bucket | Let Document AI write batch JSON |
| Local developer running Python | Access to use the processor and read/write the relevant buckets | Submit and retrieve OCR work |

Roles can be granted at a narrower bucket or processor scope where the
organization permits it. Avoid broad project-wide admin access just to run a
batch.

### Cross-project bucket access

If the processor and a storage bucket are in different projects, grant the
Document AI service agent from the **processor project** access to the bucket
project. Obtain the processor project's number:

```bash
gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)'
```

The Document AI service agent address is:

```text
service-PROJECT_NUMBER@gcp-sa-documentai.iam.gserviceaccount.com
```

Grant it `roles/storage.objectViewer` on the input bucket and
`roles/storage.objectCreator` on the output bucket. For example:

```bash
export PROJECT_NUMBER="YOUR_PROJECT_NUMBER"
export DOCUMENT_AI_AGENT="service-${PROJECT_NUMBER}@gcp-sa-documentai.iam.gserviceaccount.com"

gcloud storage buckets add-iam-policy-binding "gs://${RAW_BUCKET}" \
  --member="serviceAccount:${DOCUMENT_AI_AGENT}" \
  --role="roles/storage.objectViewer"

gcloud storage buckets add-iam-policy-binding "gs://${OUTPUT_BUCKET}" \
  --member="serviceAccount:${DOCUMENT_AI_AGENT}" \
  --role="roles/storage.objectCreator"
```

Keep this step even when access appears to work initially: batch processing
fails later with a storage permission error if the service agent cannot read
the inputs or write the results.

## 3. Install and Verify the Google Cloud CLI

Install the Google Cloud CLI using the official installation instructions for
your operating system. Then authenticate the CLI and select the intended
project:

```bash
gcloud auth login
gcloud config set project "$PROJECT_ID"
gcloud config list --format='text(core.project,core.account)'
gcloud --version
```

The last two commands should show the expected project, user account, and a
recent Google Cloud SDK version.

### Browser authentication problems on Linux

`gcloud auth application-default login` launches a browser by default. GTK,
Wayland, Vulkan, theme, or NSS certificate warnings can be noisy. They are not
the success criterion. Authentication is complete only when the command ends
with:

```text
Credentials saved to file: [.../application_default_credentials.json]
```

If the browser opens but consent fails, retry and explicitly approve the
Google consent page with the `cloud-platform` scope. If a local browser cannot
complete the login, use the remote bootstrap flow correctly:

1. Run `gcloud auth application-default login --no-browser` on the development
   machine.
2. Copy the **entire command** that it prints, including `--remote-bootstrap`.
3. Run that printed command on a different machine that has a working browser
   and a compatible `gcloud` version.
4. Complete consent there.
5. Copy the authorization response printed by that second command back into
   the original terminal prompt.

Do not paste the original authorization URL into the response prompt. It is
not an authorization response and produces the error that `state` and `code`
query parameters are missing.

## 4. Enable the API and Create the Processor

Enable the API from either the console or CLI:

```bash
gcloud services enable documentai.googleapis.com --project="$PROJECT_ID"
gcloud services list --enabled --project="$PROJECT_ID" \
  --filter='config.name=documentai.googleapis.com' \
  --format='value(config.name)'
```

The last command must print `documentai.googleapis.com`.

Create the processor in the Google Cloud Console:

1. Open **Google Cloud Console** and select the intended project.
2. Open **Document AI** from the navigation menu or search box.
3. Select **Processors** and choose **Create Processor**.
4. Choose **Enterprise Document OCR**.
5. Give it an unambiguous name such as `bahdini-enterprise-ocr`.
6. Select `us` or `eu`, matching the location decision from step 1.
7. Create it and copy the numeric processor ID shown on its details page.

Set that ID for the remaining commands:

```bash
export PROCESSOR_ID="YOUR_NUMERIC_PROCESSOR_ID"
export PROCESSOR_NAME="projects/${PROJECT_ID}/locations/${LOCATION}/processors/${PROCESSOR_ID}"
```

There is not always a `gcloud documentai` command group installed, so a missing
`gcloud documentai` command does **not** indicate an API failure. Use the
console or the Python check below to inspect processors.

## 5. Set Up Application Default Credentials and Python

The repository uses the Conda environment named `ai`. Do not create a
project-local virtual environment for this workflow.

```bash
conda activate ai
gcloud auth application-default login
gcloud auth application-default print-access-token >/dev/null && echo "ADC is valid"
python -m pip install --upgrade google-cloud-documentai
python -c 'from google.cloud import documentai; print(documentai.__file__)'
```

ADC is for local development. A deployed job should normally use the runtime
environment's attached service account or Workload Identity, not a copied
user credential file.

### Verify the processor with the Python client

This check verifies four common failure points at once: ADC, API enablement,
the processor ID, and the required location-specific endpoint.

```bash
conda run --no-capture-output -n ai python - <<'PY'
import os
from google.cloud import documentai

project_id = os.environ["PROJECT_ID"]
location = os.environ["LOCATION"]
processor_id = os.environ["PROCESSOR_ID"]

client = documentai.DocumentProcessorServiceClient(
    client_options={"api_endpoint": f"{location}-documentai.googleapis.com"}
)
processor = client.get_processor(
    name=f"projects/{project_id}/locations/{location}/processors/{processor_id}"
)
print({
    "name": processor.name,
    "display_name": processor.display_name,
    "type": processor.type_,
    "state": processor.state.name,
})
PY
```

For `us`, the host must be `us-documentai.googleapis.com`; for `eu`, it must
be `eu-documentai.googleapis.com`. Using the default endpoint with an EU
processor is a common source of confusing `NOT_FOUND` errors.

## 6. Create Storage for Batch OCR

Document AI batch processing reads from Cloud Storage and writes structured
Document AI JSON files to Cloud Storage. Use distinct input and output
buckets, keep uniform bucket-level access enabled, and keep both in the same
location as the processor.

```bash
gcloud storage buckets create "gs://${RAW_BUCKET}" \
  --project="$PROJECT_ID" \
  --location="$LOCATION" \
  --uniform-bucket-level-access

gcloud storage buckets create "gs://${OUTPUT_BUCKET}" \
  --project="$PROJECT_ID" \
  --location="$LOCATION" \
  --uniform-bucket-level-access

gcloud storage buckets list --project="$PROJECT_ID" --format='value(name,location)'
```

For a cost-controlled trial, upload one small, representative PDF first. Do
not start with the whole corpus.

```bash
mkdir -p /tmp/document-ai-trial
cp "PATH/TO/ONE/SCANNED.pdf" /tmp/document-ai-trial/input.pdf
gcloud storage cp /tmp/document-ai-trial/input.pdf "gs://${RAW_BUCKET}/trial/input.pdf"
gcloud storage ls "gs://${RAW_BUCKET}/trial/"
```

Use stable prefixes for later batches:

```text
gs://RAW_BUCKET/batches/2026-07-15/batch-0001/
gs://OUTPUT_BUCKET/batches/2026-07-15/batch-0001/
```

Never reuse an output prefix for a different submission. A unique prefix makes
it possible to identify outputs, retry a failed batch safely, and retain an
audit trail.

## 7. Run Batch OCR Safely

The correct unit of work is a tracked batch, not an unbounded upload. Build a
batch manifest from [extractions/needs_ocr.csv](../extractions/needs_ocr.csv)
that preserves at least:

```text
source, original relative path, original file name, input GCS URI,
output GCS prefix, processor name, submission time, operation name,
completion time, status, error, result JSON URI, normalized text path
```

Before implementing the full 2,869-PDF run, complete this progression:

1. Submit one known scanned PDF and inspect the output JSON and extracted text.
2. Submit a small mixed-source pilot batch, such as 5 to 20 documents.
3. Review Badini character quality, reading order, page counts, and cost.
4. Choose a bounded production batch size based on the service limits and
   observed document sizes.
5. Run production batches sequentially or with deliberately limited
   concurrency, recording every operation and retry.

### Required batch runner behavior

An implementation agent should add a script such as
`scripts/document_ai_batch_ocr.py`. The script must:

- accept `--project`, `--location`, `--processor-id`, `--input-prefix`, and
  `--output-prefix`, with environment-variable defaults;
- read its candidate inputs from `extractions/needs_ocr.csv` or a generated
  batch manifest, never recursively process arbitrary local files by default;
- upload only the explicitly selected batch and preserve the original source
  path in its manifest;
- call `DocumentProcessorServiceClient.batch_process_documents` using the
  `LOCATION-documentai.googleapis.com` endpoint;
- poll the long-running operation until success or failure and record the
  operation name before waiting;
- download the Document AI JSON outputs, reconstruct text through each page's
  text anchors, and write UTF-8 text files;
- run the same NFKC and KLPT normalization rules as
  [scripts/extract_pipeline.py](../scripts/extract_pipeline.py);
- write one JSONL manifest record per original document, including failures;
- be resumable and idempotent: an already successful document is skipped,
  while failed documents can be selected for a controlled retry;
- default to `--dry-run` unless an explicit submit flag is supplied;
- never delete input PDFs, source manifests, or Cloud Storage results.

Document AI batch output is structured JSON, not a plain text file. Preserve
the raw JSON even after extracting text: it contains page geometry, confidence
information, and text anchors needed for future diagnosis.

### Text-anchor reconstruction rule

Document AI stores full document text in `document.text`. Layout elements
reference character intervals in that text. To reconstruct anchored text, join
every `segment.start_index:segment.end_index` slice from the relevant
`text_anchor.text_segments`; treat a missing `start_index` as zero. Do not
assume there is only one segment.

Keep page separators in the final text, for example `\n\f\n`, to match the
existing native extraction pipeline and preserve page boundaries for review.

## 8. Integrate and Validate Outputs

OCR output is a candidate corpus, not automatically training-ready text. After
each batch:

1. Retain raw result JSON in an auditable location.
2. Normalize the reconstructed text with the existing pipeline rules.
3. Put results under a source-specific OCR output directory rather than
   overwriting native extraction outputs.
4. Record the processor, processor version if available, location, input hash,
   and operation name in the manifest.
5. Run language and quality classification before moving text into the training
   candidate set.
6. Sample pages visually against the original PDF, especially for ligatures,
   punctuation, tables, multi-column documents, and Arabic-script reading
   order.

Useful acceptance checks for the pilot batch:

| Check | Expected outcome |
|---|---|
| Request succeeds | Batch operation reaches success and result JSON exists |
| Page count | OCR page count agrees with PDF page count, allowing documented exceptions |
| Text quality | Kurdish characters such as `ێ`, `ۆ`, `ڕ`, `ڵ`, `ڤ`, `پ`, `چ`, `ژ`, `گ`, `ە` survive correctly |
| Reading order | Sampled paragraphs follow the visible page order |
| Provenance | Every output maps to one original local path and one GCS input URI |
| Re-run safety | Re-running the same manifest does not submit completed documents again |
| Failure visibility | Errors are stored as manifest records, not silently omitted |

## 9. Troubleshooting Map

| Symptom | Likely cause | Resolution |
|---|---|---|
| `DefaultCredentialsError` | ADC absent or wrong user | Run `gcloud auth application-default login`, then test with `print-access-token` |
| API disabled error | Document AI API disabled in the resource project | Enable `documentai.googleapis.com` in the correct project |
| `NOT_FOUND` for a known processor | Wrong project, processor ID, or endpoint location | Verify the processor name and use `us-` or `eu-documentai.googleapis.com` |
| Browser login says `cloud-platform` was not consented | Consent was cancelled or incomplete | Re-run login and approve the requested scope |
| `--no-browser` says authorization response is invalid | The original URL was pasted instead of the remote-bootstrap output | Run the printed remote-bootstrap command on another browser-capable machine and paste its final response |
| Batch fails reading `gs://` input | Document AI service agent lacks input access | Grant it `roles/storage.objectViewer` on the input bucket |
| Batch fails writing output | Service agent lacks output access or prefix is invalid | Grant `roles/storage.objectCreator`; use a new valid `gs://.../prefix/` |
| Bucket/processor location error | US/EU mismatch | Create/use buckets in the processor's location; do not mix `us` and `eu` |
| `gcloud documentai` is unavailable | SDK does not include that command group | Use the console or Python client; this does not mean the API is unavailable |
| OCR text is garbled | Legacy font encoding, poor scan, complex layout, or language mismatch | Preserve JSON, inspect pages, sample another OCR model/configuration, and flag for review |

## 10. Operational Checklist

Use this checklist before a production run:

- [ ] Billing is enabled for the selected project and expected cost has been approved.
- [ ] `documentai.googleapis.com` is enabled in that project.
- [ ] A Document OCR processor exists and its location is recorded.
- [ ] The processor endpoint matches its location.
- [ ] ADC is valid for local development, or the runtime service account is configured.
- [ ] Input and output buckets exist in the same location as the processor.
- [ ] The Document AI service agent has required bucket permissions.
- [ ] A one-document test and small pilot batch passed quality review.
- [ ] The batch manifest, output prefix, operation name, and raw JSON retention location are recorded.
- [ ] The runner defaults to dry-run and is resumable before a large submission.
- [ ] OCR text remains separated from unreviewed native extraction and has passed corpus-quality checks.

## Project Status: 2026-07-15

For the current `bahdini-data` setup, the local developer environment has
valid ADC and the Document AI API is enabled. At the time of this guide, no
processor and no Cloud Storage buckets have been created yet. The next manual
action is therefore to create the processor, choose its location, and create
the corresponding buckets before an OCR runner can submit work.