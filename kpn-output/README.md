# KPN rapporteur worker

This Java 21 worker fills the eight report sections in the supplied
`Rapporteur_Kertas_01.docx` for **Kertas Pembentangan 1**. It uses the finalized
transcript published by the sibling `transcribe` project. It uses verified excerpts from the published speech to write detailed Malay
summaries and flags sections that lack supporting material. The template retains its event details, rapporteur name line,
A4 page setup, one-inch margins, and Arial 12 formatting.

## Run alongside transcribe

Build and run the updated `transcribe` worker with its usual configuration.
[application.yml](src/main/resources/application.yml) loads the sibling
`transcribe/.env` when you start from `kpn-output` or the workspace root.
Set `KPN_TRANSCRIBE_ENV` to the absolute path of a shared settings file for
other layouts. Use plain `KEY=value` lines for imported settings. Environment
variables supplied directly to the worker take precedence over the file.
Restart the worker to pick up file changes.

The local `kpn-output/.env` sets `KPN_OLLAMA_MODEL=qwen3:4b-instruct` and uses the
shared `OLLAMA_HOST`. Spring reads the local file directly at startup.

Pull the model on the Ollama server used by the worker before starting it.

```sh
ollama pull qwen3:4b-instruct
```

| Variable | Default |
| --- | --- |
| `TEMPORAL_TARGET` | `127.0.0.1:7233` |
| `TEMPORAL_NAMESPACE` | `default` |
| `MINIO_URL` | `http://127.0.0.1:9000` |
| `MINIO_ACCESS_KEY` | Required |
| `MINIO_SECRET_KEY` | Required |
| `MINIO_BUCKET` | `asr-poc` (fallback when the workflow supplies no bucket) |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` |
| `KPN_OLLAMA_MODEL` | `qwen3:4b-instruct` in the local file |

Use the same Temporal namespace and MinIO endpoint as `transcribe`. When a job
supplies a bucket, it takes precedence over `MINIO_BUCKET`. The storage
identity needs to read the finalized transcript and check and write the report
object in the input job's bucket. With standard S3 authorization, it also needs
bucket listing permission to distinguish a missing report from denied access.

`application.yml` also lists the PostgreSQL environment variables shared with
`asr-api`: `SPRING_DATASOURCE_URL`, `SPRING_DATASOURCE_USERNAME`, and
`SPRING_DATASOURCE_PASSWORD`. The API owns the job database connection.
Report generation uses Temporal and MinIO.

From `kpn-output`, install the rendering dependency into the Python environment
used by the worker. Set `KPN_PYTHON_EXECUTABLE` in the launch environment if it
differs from `python3`. Maven tests also use this variable when running the
renderer.

```sh
"${KPN_PYTHON_EXECUTABLE:-python3}" -m pip install -r requirements.txt
mvn package
java -jar target/kpn-output-0.0.1-SNAPSHOT.jar
```

## Generate a report through asr-api

Once `results/<jobId>/transcript.json` exists in the job's MinIO bucket, send
`POST /jobs/<jobId>/kpn-report` with no body. The API responds with HTTP 202 and a report `workflowId`.
The original recording can keep its existing MinIO path and filename.

Poll `GET /jobs/<jobId>/kpn-report/<workflowId>`. When the status is
`COMPLETED`, use the returned `bucket` and `outputKey` to download the Word
document from MinIO. Each job has one current report. A new POST after the
previous report closes regenerates both files in the job's bucket.

```text
kpn/<jobId>/chunk_transcript/chunk_1.json
kpn/<jobId>/chunk_transcript/chunk_2.json
results/<jobId>/kpn/kpn-report/output.json
results/<jobId>/kpn/Rapporteur_Kertas_01.docx
```

The worker reads the top-level `text` from `results/<jobId>/transcript.json`,
removes speaker labels at the start of each line, and chunks the spoken text into
windows of up to 3,200 characters with about 600 characters of overlap. It
writes each numbered window to MinIO before running Ollama. Each chunk includes
its text, index, UTF-16 start and end offsets in the label-free text, and overlap
with the previous chunk.
Ollama selects excerpts for the eight sections, and the worker checks them against the
corresponding input window before saving them. Sections with fewer than two verified excerpts get a second pass
where Ollama selects numbered source passages by ID. The worker resolves those IDs
against the source and ignores unknown IDs. The prompt follows the topic discussed
in the transcript while the Word template retains its original event metadata.
Evidence extraction, fallback passage selection and the eight section activities
use the same section-specific guidance. It distinguishes a presentation's focus,
problem, content, challenges, future direction, explicit recommendations, substantive
Q&A and overall synthesis. Each section activity writes formal third-person Malay
paragraphs using verified evidence in bounded batches. If Ollama returns an empty or truncated response,
the worker splits the batch and retries the smaller batches. Distinct points are separate paragraphs and each paragraph
must reference at least one valid source excerpt. The JSON includes the section
number, heading, display text, source excerpts used, and numbered paragraphs.
Paragraph numbering resets at 1 in every section. It uploads the JSON first, then uses `python-docx` to
write each numbered entry as a separate paragraph in the section's answer cell.
The worker identity needs permission to read the transcript, write report and
chunk objects, and list the chunk prefix. When a rerun yields fewer chunks, it
removes stale numbered chunk objects in that prefix.

In the Temporal workflow history, new runs show `ChunkTranscript`,
`ExtractEvidence`, `Section_1` through `Section_8`, `WriteReportJson`, and
`RenderDocx`. Ollama reads each chunk in the first extraction pass and revisits it only
if any sections still have fewer than two verified excerpts.
The section activities use run-scoped intermediate JSON under
`results/<jobId>/kpn/kpn-report/runs/<runId>/`. Grant the worker read and write
access to this prefix. Existing workflow histories retain their original
activity names.

The API can also queue a template uploaded to
`results/<jobId>/kpn/templates/<templateId>/<filename>.docx`. The worker validates
the eight numbered answer tables, keeps the original session information and
renders the report from the selected file. The report is saved under the selected
filename directly inside `results/<jobId>/kpn/`.
