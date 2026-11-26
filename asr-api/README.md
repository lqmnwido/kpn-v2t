# ASR API

A Spring Boot API for submitting automatic speech recognition jobs and checking their status. PostgreSQL stores job records, MinIO stores media and output files, and Temporal coordinates processing.

## Prerequisites

- JDK 21 and Maven.
- Docker for the local PostgreSQL and MinIO examples. You can also use existing instances of either service.
- The [Temporal CLI](https://docs.temporal.io/cli) to run a local Temporal server, or access to an existing Temporal server.
- Compatible Temporal workers for end-to-end transcription and analysis. This repository contains the API and workflow interfaces. Run the worker implementation separately and point it at the same Temporal server, namespace, and task queues.

## Local setup

Clone the repository and run the commands below in Bash. The PostgreSQL and MinIO containers use named volumes to retain data across restarts.

```bash
git clone https://github.com/lqmnwido/asr-api.git
cd asr-api
```

### 1. Start PostgreSQL

The API uses PostgreSQL. This example creates the `asr_db` database and an `asr_user` login. Hibernate creates or updates the application tables at startup, so the login needs permission to create and modify tables in its schema.

```bash
export SPRING_DATASOURCE_USERNAME=asr_user
read -rsp 'Choose a local PostgreSQL password: ' SPRING_DATASOURCE_PASSWORD
echo
export SPRING_DATASOURCE_PASSWORD

docker run -d --name asr-postgres \
  -p 127.0.0.1:5432:5432 \
  -e POSTGRES_DB=asr_db \
  -e POSTGRES_USER="$SPRING_DATASOURCE_USERNAME" \
  -e POSTGRES_PASSWORD="$SPRING_DATASOURCE_PASSWORD" \
  -v asr-postgres-data:/var/lib/postgresql/data \
  postgres:16
```

The [PostgreSQL container](https://hub.docker.com/_/postgres) uses these settings when initializing an empty volume. If you already have a database, set the JDBC URL and credentials to match it.

### 2. Start MinIO

```bash
export MINIO_ACCESS_KEY=asr-local
read -rsp 'Choose a local MinIO password (at least 8 characters): ' MINIO_SECRET_KEY
echo
export MINIO_SECRET_KEY

docker run -d --name asr-minio \
  -p 127.0.0.1:9000:9000 \
  -p 127.0.0.1:9001:9001 \
  -e MINIO_ROOT_USER="$MINIO_ACCESS_KEY" \
  -e MINIO_ROOT_PASSWORD="$MINIO_SECRET_KEY" \
  -v asr-minio-data:/data \
  quay.io/minio/minio server /data --console-address ":9001"
```

The [MinIO server](https://www.min.io/blog/distributed-tracing-using-opentelemetry-jaeger) exposes the S3 API on port `9000` and the browser console on port `9001`. The API checks for the `asr-poc` bucket at startup and creates it if missing. With an existing MinIO service, use credentials that can check the bucket and upload objects. If the credentials lack bucket creation permission, create the bucket before starting the API.

### 3. Start Temporal

Install the [Temporal CLI](https://docs.temporal.io/cli) for your platform, then run this in a separate terminal and leave it running.

```bash
temporal server start-dev
```

The development server uses `127.0.0.1:7233`, the `default` namespace, and a Web UI at `http://127.0.0.1:8233`. The development server runs independently of the API's PostgreSQL database. With an existing Temporal server, set `TEMPORAL_TARGET` and `TEMPORAL_NAMESPACE` to the endpoint and namespace your workers use.

### 4. Run and check the API

From the shell where you exported the PostgreSQL and MinIO credentials, run

```bash
mvn spring-boot:run
```

In another terminal, check that the API is running.

```bash
curl -fsS http://127.0.0.1:8081/actuator/health
```

With compatible workers running, submit a local media file and use the returned `jobId` to check its status.

```bash
curl -fsS -F 'file=@/path/to/audio.wav' http://127.0.0.1:8081/jobs
curl -fsS http://127.0.0.1:8081/jobs/JOB_ID
```

## Environment variables

Export these before starting Maven. The defaults below apply to the GitHub checkout.

| Variable | Default | When to set it |
| --- | --- | --- |
| `SPRING_DATASOURCE_URL` | `jdbc:postgresql://127.0.0.1:5432/asr_db` | PostgreSQL host, port, or database name differs. |
| `SPRING_DATASOURCE_USERNAME` | Required | Set to the PostgreSQL login. |
| `SPRING_DATASOURCE_PASSWORD` | Required | Set to the PostgreSQL password. |
| `MINIO_URL` | `http://127.0.0.1:9000` | MinIO's S3 API is on another endpoint. |
| `MINIO_ACCESS_KEY` | Required | Set to the MinIO access key. |
| `MINIO_SECRET_KEY` | Required | Set to the MinIO secret key. |
| `MINIO_BUCKET` | `asr-poc` | Use a different default bucket. Configure the workers to access it. |
| `TEMPORAL_TARGET` | `127.0.0.1:7233` | Temporal is on another host or port. Use `host:port`. |
| `TEMPORAL_NAMESPACE` | `default` | Use an existing namespace with a different name. |
| `TEMPORAL_WORKFLOW_TASK_QUEUE` | `ASR_WORKFLOW_QUEUE` | Set to the queue polled by the workflow worker if it differs. |
| `TEMPORAL_MEDIA_TASK_QUEUE` | `MEDIA_TASK_QUEUE` | Set to the queue used for media activities if it differs. |
| `TEMPORAL_ASR_TASK_QUEUE` | `ASR_TASK_QUEUE` | Set to the queue used for ASR activities if it differs. |
| `TEMPORAL_ANALYSIS_TASK_QUEUE` | `ANALYSIS_TASK_QUEUE` | Reserved by the configuration. The current API starts analysis regeneration on the workflow queue. |
| `SERVER_PORT` | `8081` | Run the API on a different HTTP port. |

## Generate a KPN rapporteur report

Once `results/<jobId>/transcript.json` exists in the job's MinIO bucket, send
`POST /jobs/<jobId>/kpn-report` without a body. For a job without a recorded
bucket, the API checks the default MinIO bucket. The response contains the
report's `workflowId`. Poll
`GET /jobs/<jobId>/kpn-report/<workflowId>` for its status. On completion,
the response includes the MinIO `bucket` and `outputKey`. During processing,
`currentStep` and `progressPercent` show the report's stage and the proportion
of completed stages when available.
The numbered JSON is stored alongside the document under `kpn-report/output.json`.
Run the sibling `kpn-output` worker in the same Temporal namespace.

A report start can return HTTP 409 with one of these codes.

- `TRANSCRIPT_NOT_FOUND` includes the bucket and key the API checked.
- `REPORT_ALREADY_RUNNING` includes the existing report workflow ID to poll.

For an uploaded report template, include both `templateId` and `templateName`
as query parameters on the report POST. Upload the Word file to
`results/<jobId>/kpn/templates/<templateId>/<templateName>` in the job bucket
before submitting. The download gets the template's filename.
