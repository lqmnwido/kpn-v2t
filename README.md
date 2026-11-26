# KPN V2T

KPN V2T brings together the ASR API, transcription worker, Vue interface, and KPN report worker. Upload an audio or video recording, review and edit the synced transcript, then queue a KPN report and download the completed Word document.

## Quick start

Clone the repository and create the local settings files. Apply your service endpoints and credentials before building the frontend.

```bash
git clone https://github.com/lqmnwido/kpn-v2t.git
cd kpn-v2t
cp -n transcribe/.env.example transcribe/.env
cp -n kpn-output/.env.example kpn-output/.env
cp -n transcribe-ui/.env.example transcribe-ui/.env.local
```

With the supporting services running, install and serve the app.

```bash
./install.sh
./serve.sh
```

Open http://localhost:5173. Use Ctrl+C to stop the four processes. Service logs are in `.logs/`. Stop any standalone copies before serving to avoid port conflicts and competing Temporal workers.

## Requirements

Use Java 21 or newer, Maven, Node.js 22.12+ or 24+, Python with venv support, and FFmpeg/FFprobe. Start the existing PostgreSQL, Temporal, MinIO, and Ollama services before running `serve.sh`. Use the existing database, Temporal namespace, and bucket. Install the report model on the configured Ollama server with `ollama pull qwen3:4b-instruct`.

`./install.sh` installs the CPU transcription runtime by default. Use `./install.sh --gpu` for the existing NVIDIA CUDA setup flow, or `./install.sh --reuse-runtime /absolute/path/to/transcribe` to reuse working Python environments on the same host. Reuse mode also expects python-docx in `KPN_PYTHON_EXECUTABLE` (default `python3`). Transcription and diarization models download on demand as configured by the workers.

## Configuration

- `transcribe/.env` holds the shared Temporal, PostgreSQL, MinIO, Ollama and transcription settings. The API port is `ASR_API_PORT` (default 8081). `SERVER_PORT` controls the transcription worker.
- `kpn-output/.env` holds KPN-specific settings, including `KPN_OLLAMA_MODEL`.
- `transcribe-ui/.env.local` holds the browser-facing API and MinIO endpoints and browser credentials. MinIO must permit requests from the UI origin. Use credentials scoped to this application because Vite includes these values in its browser bundle. Run the installer again after changing the UI configuration.

The local settings files are excluded from Git. The interface is served on loopback. Set `KPN_UI_PORT` before running `serve.sh` to change the port and configure the corresponding MinIO CORS origin.
