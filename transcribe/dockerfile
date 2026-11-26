# ─── Stage 1: Build Java ───────────────────────────────────────
FROM maven:3.9.6-eclipse-temurin-21 AS java-builder
WORKDIR /app

COPY pom.xml .
COPY src ./src
RUN mvn clean package -DskipTests -q

# ─── Stage 2: Final Image ──────────────────────────────────────
FROM eclipse-temurin:21-jre-jammy

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    ffmpeg \
    && ln -s /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=java-builder /app/target/*.jar app.jar

COPY python ./python
COPY install ./install
COPY diagnose_transcription.py .

# Install explicit CPU wheels in both isolated runtimes; no CUDA payload by default.
RUN SETUP_PYTHON=python3 bash install/setup_cpu.sh
ENV DIARIZATION_PYTHON=/app/python/diarization-venv/bin/python
ENV DIARIZATION_DEVICE=cpu
ENV PYANNOTE_METRICS_ENABLED=0

RUN mkdir -p /tmp/temporal-asr-poc

EXPOSE 8082

ENTRYPOINT ["java", "-jar", "app.jar"]
