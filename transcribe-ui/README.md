# 2en Apps - V2T

A standalone Vue frontend for the existing ASR API, MinIO and KPN report worker.

## Run locally

Use Node.js 20.19+ or 22.12+.

```sh
cp .env.example .env.local
npm ci
npm run dev
```

Open `http://localhost:5173/`. Set `VITE_ASR_API_URL`, `VITE_MINIO_URL`,
`VITE_MINIO_BUCKET`, and `VITE_MINIO_REGION` to the browser-accessible service
settings. Use the MinIO **S3 API** URL, whose default port is 9000.
For a deployed frontend, use HTTPS and allow its origin on both services.

Set `VITE_MINIO_ACCESS_KEY`, `VITE_MINIO_SECRET_KEY`, and the optional
`VITE_MINIO_SESSION_TOKEN` in `.env.local`, then restart Vite. Vite embeds these
values in the browser bundle. Use scoped short-lived credentials and keep the
resulting bundle within the intended audience. The browser signs S3 requests
and sends recordings directly to MinIO.

MinIO needs to allow the frontend origin to issue GET and PUT requests, allow
the AWS signing headers, `Content-Type`, `If-Match`, and `If-None-Match`, and
expose the response header `ETag`. The browser credentials need permission to
read and write `uploads/`, read and update `results/<jobId>/transcript.vtt`,
read and update `results/<jobId>/transcript.json`, create
`results/<jobId>/transcript.original.json`, and read the completed KPN DOCX.
The ASR API's local CORS settings already allow the default Vite origin.

## Workflow

1. Choose **Tambah** to upload an audio or video recording. When the upload
   succeeds, the frontend calls `POST /jobs/external` with its MinIO path.
2. Select a recording in the table to play it. Caption rows follow playback and
   seek the recording when selected. Use the separate speaker and text fields
   to edit the report transcript. **Tukar semua nama** renames every matching
   speaker label. Use **Tambah teks dan masa** to add a caption. Drag each
   time slider, use the one-second adjustment buttons, or copy the current
   player time. **Ikut masa pemain** tracks playback to set the start, then
   continues tracking to set the end. Saving adds the entry
   to both the report transcript and `transcript.vtt`. Save the edits before
   generating a report. The first save makes a backup of
   the original. Edits update the top-level report text using a conditional
   ETag write. The editor sorts timed rows chronologically and saves their
   timestamps across speaker and text edits. Timed additions are also saved
   as segments and WebVTT cues. Use **Ubah masa** in the **Masa** column to
   change an existing cue's start or end with the same sliders.
   Speaker and text edits on rows with known timestamps also update their
   VTT cues. If writing the VTT fails, save again to retry the pending cues.
3. Choose **Jana laporan**. The frontend calls `POST /jobs/<jobId>/kpn-report`
   and polls the returned report workflow. The existing API reads the saved
   transcript object by key. Avoid editing that object in other clients while
   the report runs.
4. Choose **Muat turun Word** after the report completes. The frontend fetches
   the report directly from MinIO using the bucket and object key returned by
   the API.

The queue is restored within the same browser. The backend workers execute
queued jobs. Run `transcribe` for new audio and `kpn-output` for Word reports.

Run `npm run build` to generate static files in `dist/` for separate hosting.

Use **Padam** in the table to delete a finished recording and its current MinIO
objects. The browser credentials need bucket listing permission scoped to the
job prefixes and delete permission on those objects. Add DELETE to the allowed
MinIO CORS methods. The API still retains the job record and Temporal history.

Drop a file on the **Templat Word** card or use **Pilih fail Word** to upload a compatible `.docx`
(up to 5 MiB) when you click **Jana laporan**. The worker keeps the session
details from the chosen file, fills its eight numbered answer tables and
exports under that filename. Use the updated API and report worker together.
